from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.agent.registry import available_tools
from app.models.candidate import User
from app.models.workflow import AgentRun, AgentToolEvent
from app.services import application_workflows, job_workflows
from app.services.agent_settings import policy_for


def create_run(db, actor, payload):
    if payload.scope.document_id and payload.task_type != "career_assistant":
        raise HTTPException(422, "CV selection is supported by career_assistant tasks only")
    policy = policy_for(db, actor.owner_id)
    if not policy.enabled:
        raise HTTPException(503, "Agent is disabled; enable only after readiness checks")
    owner = db.scalar(select(User).where(User.id == actor.owner_id).with_for_update())
    if owner is None:
        db.add(User(id=actor.owner_id))
        db.flush()
    recent = db.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.owner_id == actor.owner_id,
            AgentRun.created_at >= datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    if recent >= 5:
        raise HTTPException(429, "Agent rate limit exceeded")
    if db.scalar(
        select(AgentRun.id).where(
            AgentRun.owner_id == actor.owner_id, AgentRun.status.in_(["pending", "running"])
        )
    ):
        raise HTTPException(409, "Another run is already active")
    registry = available_tools(policy)
    names = payload.allowed_tools or [
        name for name, tool in registry.items() if tool.effect == "read"
    ]
    selected_document = None
    if payload.task_type == "career_assistant":
        if payload.scope.document_id:
            from app.services.cv_matching import ready_document

            selected_document = ready_document(db, actor, payload.scope.document_id)
        if any(name != "get_career_context" for name in payload.allowed_tools):
            raise HTTPException(403, "Career assistant uses a server-managed read-only toolset")
        if len(payload.scope.job_ids) > 10:
            raise HTTPException(422, "Select at most 10 jobs")
        collection = payload.scope.job_collection or "saved"
        if collection == "selected" and not payload.scope.job_ids:
            raise HTTPException(422, "Select at least one job")
        if payload.scope.job_ids and collection != "selected":
            raise HTTPException(422, "Explicit jobs require selected collection")
        names = ["get_career_context"]
        for permission in ("profile:read", "jobs:read", "applications:read", "reminders:read"):
            actor.require(permission)
    for name in names:
        if name not in registry:
            raise HTTPException(403, "TOOL_NOT_ALLOWED")
        actor.require(registry[name].permission)
        if payload.task_type == "daily_briefing" and registry[name].effect != "read":
            raise HTTPException(403, "Daily briefing is read-only")
    for job_id in payload.scope.job_ids:
        job_workflows.get_job(db, actor, job_id)
    related_job_ids = []
    for application_id in payload.scope.application_ids:
        application = application_workflows.get_application(
            db, actor, application_id=application_id
        )
        if payload.scope.job_ids and application.job.id not in payload.scope.job_ids:
            raise HTTPException(422, "Application and job scope disagree")
        related_job_ids.append(str(application.job.id))
    permissions = (
        {tool.permission for name, tool in registry.items() if name in names}
        | {"profile:read", "jobs:read", "applications:read", "drafts:read", "reminders:read"}
    ) & actor.permissions
    scope = payload.scope.model_dump(mode="json")
    if selected_document:
        scope["_document_revision"] = selected_document.snapshot_revision
    scope["_permissions"] = sorted(permissions)
    scope["_max_turns"] = policy.max_turns
    if related_job_ids and not scope["job_ids"]:
        scope["job_ids"] = list(dict.fromkeys(related_job_ids))
    if payload.task_type == "career_assistant":
        scope["job_collection"] = payload.scope.job_collection or "saved"
    run = AgentRun(
        owner_id=actor.owner_id,
        task_type=payload.task_type,
        instruction=payload.instruction,
        scope=scope,
        allowed_tools=list(dict.fromkeys(names)),
        max_tool_calls=policy.max_tool_calls,
        timeout_seconds=policy.timeout_seconds,
    )
    db.add(run)
    db.flush()
    return run


def owned_run(db, actor, run_id, lock=False):
    query = select(AgentRun).where(AgentRun.id == run_id, AgentRun.owner_id == actor.owner_id)
    run = db.scalar(query.with_for_update() if lock else query)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


_UNSET = object()


def run_detail(db, actor, run, *, events=None, profile_version=_UNSET):
    if events is None:
        events = db.scalars(
            select(AgentToolEvent)
            .where(AgentToolEvent.agent_run_id == run.id)
            .order_by(AgentToolEvent.sequence_number)
        ).all()
    if run.result and run.result.get("version") == 1 and profile_version is _UNSET:
        from app.models.candidate import CandidateProfile

        profile_version = db.scalar(
            select(CandidateProfile.profile_version).where(
                CandidateProfile.owner_id == actor.owner_id
            )
        )
    return {
        "instruction": run.instruction,
        "scope": {key: value for key, value in run.scope.items() if not key.startswith("_")},
        "model_turn_count": run.model_turn_count,
        "is_profile_stale": bool(
            run.result
            and run.result.get("source_profile_version") is not None
            and run.result.get("source_profile_version") != profile_version
        ),
        "id": str(run.id),
        "task_type": run.task_type,
        "status": run.status,
        "tool_call_count": run.tool_call_count,
        "max_tool_calls": run.max_tool_calls,
        "timeout_seconds": run.timeout_seconds,
        "cancel_requested": run.cancel_requested,
        "result": run.result,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "events": [
            {
                "sequence": e.sequence_number,
                "tool_name": e.tool_name,
                "status": e.status,
                "started_at": e.started_at,
                "completed_at": e.completed_at,
                "error_code": e.error_code,
            }
            for e in events
        ],
    }


def cancel_run(db, actor, run_id):
    run = owned_run(db, actor, run_id, lock=True)
    if run.status in {"pending", "running"}:
        run.cancel_requested = True
        if run.status == "pending":
            run.status = "cancelled"
            run.completed_at = datetime.now(UTC)
    db.flush()
    return run


def list_runs(db, actor, *, q=None, status=None, page=1, page_size=20):
    query = select(AgentRun).where(AgentRun.owner_id == actor.owner_id)
    if q and q.strip():
        query = query.where(AgentRun.instruction.ilike("%" + q.strip() + "%"))
    if status:
        query = query.where(AgentRun.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    runs = db.scalars(
        query.order_by(AgentRun.created_at.desc(), AgentRun.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    grouped = {}
    if runs:
        for event in db.scalars(
            select(AgentToolEvent)
            .where(AgentToolEvent.agent_run_id.in_([run.id for run in runs]))
            .order_by(AgentToolEvent.sequence_number)
        ):
            grouped.setdefault(event.agent_run_id, []).append(event)
    from app.models.candidate import CandidateProfile

    version = db.scalar(
        select(CandidateProfile.profile_version).where(CandidateProfile.owner_id == actor.owner_id)
    )
    return {
        "items": [
            run_detail(db, actor, run, events=grouped.get(run.id, []), profile_version=version)
            for run in runs
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
        "has_next": page * page_size < total,
    }
