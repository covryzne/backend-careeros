import uuid

from fastapi import HTTPException

from app.agent.registry import available_tools
from app.schemas.workflow import JobCreate, ReminderCreate
from app.services import application_workflows, approvals, drafts, job_workflows, profile, reminders
from app.services.agent_privacy import private_values, sanitize_text
from app.services.agent_settings import policy_for
from app.services.idempotency import command, fingerprint


class AgentServices:
    def __init__(self, db):
        self._db = db

    def invoke(self, context, name, args):
        db = self._db
        actor = context.actor
        policy = policy_for(db, actor.owner_id)
        if not policy.enabled:
            raise HTTPException(403, "Agent is disabled")
        tool = available_tools(policy).get(name)
        if tool is None or name not in context.allowed_tools:
            raise HTTPException(403, "TOOL_NOT_ALLOWED")
        actor.require(tool.permission)
        context.check_scope(
            job_id=getattr(args, "job_id", None),
            application_id=getattr(args, "application_id", None),
        )
        if name == "get_career_context":
            from app.services.career import career_context

            return career_context(db, actor, context.scope)
        if tool.effect == "approval":
            action = approvals.prepare_action(db, context, name, args)
            return {
                "status": "blocked",
                "data": {"approval_id": str(action.id)},
                "error": {
                    "code": "APPROVAL_REQUIRED",
                    "message": "Owner review required",
                    "retryable": False,
                },
            }
        if name == "get_candidate_profile":
            owner = profile.load_profile(db, actor)
            if owner is None:
                raise HTTPException(404, "Profile not found")
            return {
                "profile_id": str(owner.id),
                "profile_version": owner.profile_version,
                "skills": [{"id": str(x.id), "name": x.name} for x in owner.skills]
                if args.include_skills
                else [],
                "experiences": [
                    {
                        "id": str(x.id),
                        "role": x.role,
                        "company": x.company,
                    }
                    for x in owner.experiences
                ]
                if args.include_experiences
                else [],
            }
        if name == "search_jobs":
            result = job_workflows.list_jobs(
                db,
                actor,
                job_ids=[uuid.UUID(x) for x in context.scope.get("job_ids", [])],
                **args.model_dump(),
            )
            items = [
                x.model_dump(mode="json")
                for x in result.items
                if not context.scope.get("job_ids") or str(x.id) in context.scope["job_ids"]
            ]
            return {
                "items": items[: context.scope.get("max_results", 20)],
                "page": args.page,
                "has_next": result.has_next,
            }
        if name == "get_job_details":
            result = job_workflows.get_job(db, actor, args.job_id)
            result.pop("notes", None)
            private = private_values(profile.load_profile(db, actor))
            if result.get("description"):
                result["description"] = sanitize_text(result["description"], private)[:12000]
            return result
        if name == "list_applications":
            values = args.model_dump()
            result = application_workflows.list_applications(
                db,
                actor,
                job_ids=[uuid.UUID(x) for x in context.scope.get("job_ids", [])],
                application_ids=[uuid.UUID(x) for x in context.scope.get("application_ids", [])],
                **values,
            )
            items = [
                {
                    "id": str(x.id),
                    "job_id": str(x.job.id),
                    "title": x.job.title,
                    "company": x.job.company,
                    "status": x.status,
                    "version": x.version,
                    "follow_up_date": str(x.follow_up_date) if x.follow_up_date else None,
                }
                for x in result.items
                if (not context.scope.get("job_ids") or str(x.job.id) in context.scope["job_ids"])
                and (
                    not context.scope.get("application_ids")
                    or str(x.id) in context.scope["application_ids"]
                )
            ]
            return {
                "items": items[: context.scope.get("max_results", 20)],
                "page": args.page,
                "has_next": result.has_next,
            }
        if name == "get_application_details":
            x = application_workflows.get_application(db, actor, application_id=args.application_id)
            context.check_scope(job_id=x.job.id)
            return {
                "id": str(x.id),
                "job_id": str(x.job.id),
                "title": x.job.title,
                "status": x.status,
                "version": x.version,
                "follow_up_date": str(x.follow_up_date) if x.follow_up_date else None,
            }
        key = fingerprint(
            {"run": str(context.run_id), "tool": name, "args": args.model_dump(mode="json")}
        )

        def write():
            if name in {"create_email_draft", "create_cover_letter_draft"}:
                item = drafts.create_draft(
                    db, actor, args, "email" if name == "create_email_draft" else "cover_letter"
                )
                return {
                    "draft_id": str(item.id),
                    "status": "draft",
                    "source_profile_version": item.source_profile_version,
                    "review_flags": item.review_flags,
                }
            if name == "schedule_reminder":
                if args.application_id:
                    record = application_workflows.get_application(
                        db, actor, application_id=args.application_id
                    )
                    context.check_scope(job_id=record.job.id)
                item = reminders.create_reminder(
                    db, actor, ReminderCreate.model_validate(args.model_dump())
                )
                return {"reminder_id": str(item.id)}
            if name == "save_job":
                if context.scope.get("job_ids"):
                    raise HTTPException(403, "Cannot create new jobs in a fixed-resource scope")
                item = job_workflows.create_job(db, actor, payload=JobCreate(**args.model_dump()))
                return {"job_id": str(item.id), "status": "saved"}
            if name == "propose_profile_update":
                result = drafts.propose_profile(db, actor, args)
                if policy.approvals_enabled:
                    action = approvals.prepare_action(
                        db,
                        context,
                        "apply_profile_proposal",
                        {"proposal_id": uuid.UUID(result["id"])},
                    )
                    result["approval_id"] = str(action.id)
                return result
            raise HTTPException(403, "TOOL_NOT_ALLOWED")

        return command(db, actor, "agent:" + name, key, args.model_dump(mode="json"), write)
