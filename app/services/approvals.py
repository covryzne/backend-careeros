from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import _APPROVAL_CAPABILITY, ActorContext
from app.models.foundation import AgentApproval, ProfileProposal
from app.models.workflow import AgentRun
from app.schemas.agent import ConfirmFactArguments, ManualApplyArguments, MoveArguments
from app.schemas.profile import ProfileUpdate
from app.schemas.workflow import ApplicationCreate, ApplicationUpdate
from app.services import application_workflows, job_workflows, profile
from app.services.agent_settings import policy_for
from app.services.audit import audit
from app.services.idempotency import fingerprint


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def prepare_action(db, context, name, args):
    policy = policy_for(db, context.actor.owner_id)
    if not policy.enabled or not policy.approvals_enabled:
        raise HTTPException(403, "Approval mutations are disabled")
    actor = context.actor
    resource = {"tool_version": "1.0"}
    if name == "update_application_status":
        record = application_workflows.get_application(
            db, actor, application_id=args.application_id
        )
        context.check_scope(application_id=args.application_id, job_id=record.job.id)
        if args.expected_version != record.version:
            raise HTTPException(409, "STALE_VERSION")
        version = record.version
    elif name == "confirm_profile_fact":
        owner = profile.load_profile(db, actor)
        if not owner or not any(f.id == args.fact_id for f in owner.facts):
            raise HTTPException(404, "Fact not found")
        profile.check_version(owner, args.expected_profile_version, actor)
        version = owner.profile_version
    elif name == "confirm_manual_application":
        record = job_workflows.get_job(db, actor, args.job_id)
        context.check_scope(job_id=args.job_id)
        resource["job_hash"] = fingerprint(record)
        version = 0
    elif name == "apply_profile_proposal":
        proposal = db.scalar(
            select(ProfileProposal).where(
                ProfileProposal.id == args["proposal_id"],
                ProfileProposal.owner_id == actor.owner_id,
            )
        )
        if proposal is None:
            raise HTTPException(404, "Proposal not found")
        version = proposal.source_profile_version
        resource["proposal_hash"] = fingerprint(proposal.changes)
    else:
        raise HTTPException(403, "TOOL_NOT_ALLOWED")
    payload = (
        args.model_dump(mode="json")
        if hasattr(args, "model_dump")
        else {k: str(v) for k, v in args.items()}
    )
    frozen = {"payload": payload, "resource": resource}
    digest = fingerprint(
        {
            "owner": str(actor.owner_id),
            "run": str(context.run_id),
            "tool": name,
            "arguments": frozen,
            "version": version,
        }
    )
    existing = db.scalar(
        select(AgentApproval).where(
            AgentApproval.run_id == context.run_id, AgentApproval.content_hash == digest
        )
    )
    if existing:
        return existing
    action = AgentApproval(
        owner_id=actor.owner_id,
        run_id=context.run_id,
        tool_name=name,
        arguments=frozen,
        content_hash=digest,
        resource_version=version,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(action)
    db.flush()
    audit(db, actor, "approval_proposed", "approval", action.id)
    return action


def decide(db, owner, approval_id, content_hash, approve):
    policy = policy_for(db, owner.owner_id)
    if approve and (not policy.enabled or not policy.approvals_enabled):
        raise HTTPException(403, "Approval mutations are disabled")
    item = db.scalar(
        select(AgentApproval)
        .where(AgentApproval.id == approval_id, AgentApproval.owner_id == owner.owner_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "Approval not found")
    from app.agent.registry import TOOLS

    permission = (
        "profile:write"
        if item.tool_name == "apply_profile_proposal"
        else TOOLS[item.tool_name].permission
    )
    owner.require(permission)
    if item.arguments.get("resource", {}).get("tool_version") != "1.0":
        raise HTTPException(409, "Tool version changed")
    if item.content_hash != content_hash:
        raise HTTPException(409, "Approval preview changed")
    digest = fingerprint(
        {
            "owner": str(item.owner_id),
            "run": str(item.run_id),
            "tool": item.tool_name,
            "arguments": item.arguments,
            "version": item.resource_version,
        }
    )
    if digest != item.content_hash:
        raise HTTPException(409, "Approval payload changed")
    if item.status == "executed":
        return item.result
    if item.status != "pending" or aware(item.expires_at) <= datetime.now(UTC):
        raise HTTPException(409, "APPROVAL_EXPIRED or approval already rejected")
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == item.run_id, AgentRun.owner_id == owner.owner_id)
        .with_for_update()
    )
    permitted = (
        "propose_profile_update" if item.tool_name == "apply_profile_proposal" else item.tool_name
    )
    if (
        not run
        or run.cancel_requested
        or run.status in {"failed", "cancelled"}
        or permitted not in run.allowed_tools
    ):
        raise HTTPException(403, "Run no longer authorizes this action")
    if not approve:
        item.status = "rejected"
        audit(db, owner, "approval_rejected", "approval", item.id)
        db.flush()
        return {"status": "rejected"}
    from app.agent.registry import TOOLS

    permission = (
        "profile:write"
        if item.tool_name == "apply_profile_proposal"
        else TOOLS[item.tool_name].permission
    )
    owner.require(permission)
    permissions = frozenset(run.scope.get("_permissions", [])) | {permission}
    actor = ActorContext(owner.owner_id, "agent", permissions, item.id, _APPROVAL_CAPABILITY)
    values = item.arguments["payload"]
    from app.agent.context import ToolContext

    context = ToolContext(actor, run.id, frozenset(run.allowed_tools), run.scope)
    if item.tool_name == "update_application_status":
        args = MoveArguments.model_validate(values)
        record = application_workflows.get_application(
            db, actor, application_id=args.application_id
        )
        context.check_scope(application_id=args.application_id, job_id=record.job.id)
        updated = application_workflows.update_application(
            db,
            actor,
            application_id=args.application_id,
            payload=ApplicationUpdate(status=args.status, expected_version=args.expected_version),
        )
        result = {
            "status": "executed",
            "application_id": str(updated.id),
            "application_status": updated.status,
            "version": updated.version,
        }
    elif item.tool_name == "confirm_profile_fact":
        args = ConfirmFactArguments.model_validate(values)
        fact, version = profile.confirm_fact(
            db, actor, args.fact_id, "user_confirmed", args.expected_profile_version
        )
        result = {"status": "executed", "fact_id": str(fact.id), "profile_version": version}
    elif item.tool_name == "confirm_manual_application":
        args = ManualApplyArguments.model_validate(values)
        context.check_scope(job_id=args.job_id)
        record = job_workflows.get_job(db, actor, args.job_id)
        if fingerprint(record) != item.arguments["resource"]["job_hash"]:
            raise HTTPException(409, "Job changed since approval")
        from app.models.workflow import Application

        existing = db.scalar(
            select(Application).where(
                Application.owner_id == owner.owner_id, Application.job_id == args.job_id
            )
        )
        if existing:
            raise HTTPException(
                409, "Application already exists; confirm the existing application manually"
            )
        updated = application_workflows.create_application(
            db, actor, payload=ApplicationCreate(**args.model_dump(), status="applied")
        )
        result = {"status": "executed", "application_id": str(updated.id)}
    else:
        import uuid

        proposal = db.scalar(
            select(ProfileProposal)
            .where(
                ProfileProposal.id == uuid.UUID(values["proposal_id"]),
                ProfileProposal.owner_id == owner.owner_id,
            )
            .with_for_update()
        )
        if (
            not proposal
            or proposal.status != "pending"
            or fingerprint(proposal.changes) != item.arguments["resource"]["proposal_hash"]
        ):
            raise HTTPException(409, "Proposal changed")
        updated = profile.update_profile(
            db, actor, ProfileUpdate.model_validate(proposal.changes), item.resource_version
        )
        proposal.status = "applied"
        result = {"status": "executed", "profile_version": updated.profile_version}
    item.status = "executed"
    item.result = result
    audit(db, owner, "approval_executed", "approval", item.id)
    db.flush()
    return result
