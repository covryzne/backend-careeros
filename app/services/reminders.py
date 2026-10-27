from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.workflow import Application, Job, Reminder
from app.services.audit import audit


def owned_reminder(db, actor, reminder_id, *, lock=False):
    query = select(Reminder).where(Reminder.id == reminder_id, Reminder.owner_id == actor.owner_id)
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise HTTPException(404, "Reminder not found")
    return item


def create_reminder(db, actor, payload):
    actor.require("reminders:write")
    values = payload.model_dump()
    if payload.due_at.tzinfo is None:
        raise HTTPException(422, "Reminder date must include timezone")
    application = None
    if payload.application_id:
        application = db.scalar(
            select(Application).where(
                Application.id == payload.application_id, Application.owner_id == actor.owner_id
            )
        )
        if application is None:
            raise HTTPException(404, "Application not found")
    job_id = payload.job_id or (application.job_id if application else None)
    if application and payload.job_id and application.job_id != payload.job_id:
        raise HTTPException(422, "Reminder targets disagree")
    if (
        job_id
        and db.scalar(
            select(Job.id).where(
                Job.id == job_id, Job.owner_id == actor.owner_id, Job.deleted_at.is_(None)
            )
        )
        is None
    ):
        raise HTTPException(404, "Job not found")
    item = Reminder(owner_id=actor.owner_id, **values)
    db.add(item)
    db.flush()
    audit(db, actor, "reminder_created", "reminder", item.id)
    return item


def list_reminders(
    db, actor, *, page=1, page_size=20, status=None, due_before=None, application_id=None
):
    actor.require("reminders:read")
    query = select(Reminder).where(Reminder.owner_id == actor.owner_id)
    if status:
        query = query.where(Reminder.status == status)
    if due_before:
        if due_before.tzinfo is None:
            raise HTTPException(422, "Timezone is required")
        query = query.where(Reminder.due_at <= due_before)
    if application_id:
        query = query.where(Reminder.application_id == application_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(
        query.order_by(Reminder.due_at, Reminder.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
        "has_next": page * page_size < total,
    }


def complete_reminder(db, actor, reminder_id):
    actor.require("reminders:write")
    item = owned_reminder(db, actor, reminder_id, lock=True)
    if item.status != "completed":
        item.status = "completed"
        item.completed_at = datetime.now(UTC)
        audit(db, actor, "reminder_completed", "reminder", item.id)
    db.flush()
    return item


def update_reminder(db, actor, reminder_id, payload):
    actor.require("reminders:write")
    item = owned_reminder(db, actor, reminder_id, lock=True)
    values = payload.model_dump(exclude_unset=True)
    if values.get("due_at") and values["due_at"].tzinfo is None:
        raise HTTPException(422, "Timezone is required")
    if all(getattr(item, key) == value for key, value in values.items()):
        return item
    for key, value in values.items():
        setattr(item, key, value)
    if "status" in values:
        item.completed_at = datetime.now(UTC) if item.status == "completed" else None
    audit(db, actor, "reminder_updated", "reminder", item.id)
    db.flush()
    return item
