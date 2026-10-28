# ruff: noqa: B008
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.agent_settings import AgentSettingsPatch, AgentSettingsResponse
from app.schemas.provider_settings import ProviderSettingsPatch, ProviderSettingsResponse
from app.services import provider_settings
from app.services.agent_settings import read_settings, update_settings
from app.services.idempotency import command

router = APIRouter()


@router.get("/provider-settings", response_model=ProviderSettingsResponse)
def read_provider(
    response: Response, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    response.headers["Cache-Control"] = "no-store"
    return provider_settings.read_settings(db, actor)


@router.patch("/provider-settings", response_model=ProviderSettingsResponse)
def update_provider(
    payload: ProviderSettingsPatch,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    from hashlib import sha256

    provider_settings.owner_only(actor, "profile:write")
    values = payload.model_dump(mode="json", exclude={"api_key"})
    values["api_key_digest"] = (
        sha256(payload.api_key.get_secret_value().encode()).hexdigest() if payload.api_key else None
    )
    try:
        result = command(
            db,
            actor,
            "provider-settings:update",
            request.headers.get("Idempotency-Key"),
            values,
            lambda: provider_settings.update_settings(db, actor, payload),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    response.headers["Cache-Control"] = "no-store"
    return result


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
