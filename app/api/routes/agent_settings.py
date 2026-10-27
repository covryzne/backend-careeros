# ruff: noqa: B008
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.agent_settings import AgentSettingsPatch, AgentSettingsResponse
from app.services.agent_settings import read_settings, update_settings
from app.services.idempotency import command

router = APIRouter()


@router.get("/agent-settings", response_model=AgentSettingsResponse)
def read(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    return read_settings(db, actor)


@router.patch("/agent-settings", response_model=AgentSettingsResponse)
def update(
    payload: AgentSettingsPatch,
    request: Request,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    result = command(
        db,
        actor,
        "agent-settings:update",
        request.headers.get("Idempotency-Key"),
        payload.model_dump(mode="json"),
        lambda: update_settings(db, actor, payload),
    )
    db.commit()
    return result
