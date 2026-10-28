# ruff: noqa: B008
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.registry import available_tools
from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.models.foundation import AgentApproval
from app.schemas.agent import AgentRunPage, AgentRunResponse, RunCreate, StrictInput
from app.services import agent_runs, approvals
from app.services.agent_settings import policy_for
from app.services.idempotency import command
from app.services.provider_settings import configured

router = APIRouter()


class ApprovalDecision(StrictInput):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("/agent-capabilities")
def capabilities(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    policy = policy_for(db, actor.owner_id)
    return {
        "enabled": policy.enabled,
        "internal_writes_enabled": policy.enabled and policy.internal_writes_enabled,
        "approvals_enabled": policy.enabled and policy.approvals_enabled,
        "provider_configured": configured(db, actor.owner_id),
        "max_tool_calls": policy.max_tool_calls,
        "timeout_seconds": policy.timeout_seconds,
        "tools": list(available_tools(policy)),
        "max_turns": policy.max_turns,
        "assistant": {
            "task_type": "career_assistant",
            "max_jobs": 10,
            "max_recommendations": 5,
            "on_demand": True,
        },
        "external_actions_enabled": False,
    }


@router.post("/agent-runs", status_code=202, response_model=AgentRunResponse)
def create(
    payload: RunCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    result = command(
        db,
        actor,
        "agent-run:create",
        request.headers.get("Idempotency-Key"),
        payload.model_dump(mode="json"),
        lambda: jsonable_encoder(
            agent_runs.run_detail(db, actor, agent_runs.create_run(db, actor, payload))
        ),
    )
    db.commit()
    return result


@router.get("/agent-runs", response_model=AgentRunPage)
def list_runs(
    q: str | None = Query(None, max_length=240),
    status: Literal["pending", "running", "completed", "failed", "cancelled"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    return agent_runs.list_runs(db, actor, q=q, status=status, page=page, page_size=page_size)


@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
def detail(
    run_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    return agent_runs.run_detail(db, actor, agent_runs.owned_run(db, actor, run_id))


@router.post("/agent-runs/{run_id}/cancel", response_model=AgentRunResponse)
def cancel(
    run_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    item = agent_runs.cancel_run(db, actor, run_id)
    db.commit()
    return agent_runs.run_detail(db, actor, item)


@router.get("/agent-approvals")
def list_approvals(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    items = db.scalars(
        select(AgentApproval)
        .where(AgentApproval.owner_id == actor.owner_id)
        .order_by(AgentApproval.created_at.desc())
        .limit(20)
    ).all()
    return {
        "items": [
            {
                key: getattr(item, key)
                for key in (
                    "id",
                    "run_id",
                    "tool_name",
                    "arguments",
                    "content_hash",
                    "resource_version",
                    "status",
                    "expires_at",
                    "result",
                )
            }
            for item in items
        ]
    }


@router.post("/agent-approvals/{approval_id}/approve")
def approve(
    approval_id: uuid.UUID,
    payload: ApprovalDecision,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    result = approvals.decide(db, actor, approval_id, payload.content_hash, True)
    db.commit()
    return result


@router.post("/agent-approvals/{approval_id}/reject")
def reject(
    approval_id: uuid.UUID,
    payload: ApprovalDecision,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    result = approvals.decide(db, actor, approval_id, payload.content_hash, False)
    db.commit()
    return result
