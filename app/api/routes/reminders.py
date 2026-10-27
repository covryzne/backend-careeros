# ruff: noqa: B008
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.workflow import ReminderCreate, ReminderPatch
from app.services import reminders
from app.services.idempotency import command

router = APIRouter()


@router.get("")
def list_reminders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Literal["pending", "completed"] | None = None,
    due_before: datetime | None = None,
    application_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    return jsonable_encoder(
        reminders.list_reminders(
            db,
            actor,
            page=page,
            page_size=page_size,
            status=status,
            due_before=due_before,
            application_id=application_id,
        )
    )


@router.post("", status_code=201)
def create_reminder(
    payload: ReminderCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    result = command(
        db,
        actor,
        "reminder:create",
        request.headers.get("Idempotency-Key"),
        payload.model_dump(mode="json"),
        lambda: jsonable_encoder(reminders.create_reminder(db, actor, payload)),
    )
    db.commit()
    return result


@router.post("/{reminder_id}/complete")
def complete(
    reminder_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    result = jsonable_encoder(reminders.complete_reminder(db, actor, reminder_id))
    db.commit()
    return result


@router.patch("/{reminder_id}")
def update(
    reminder_id: uuid.UUID,
    payload: ReminderPatch,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    item = reminders.update_reminder(db, actor, reminder_id, payload)
    result = jsonable_encoder(item)
    db.commit()
    return result
