import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import ActorContext
from app.models.workflow import Application, ApplicationActivity, Job
from app.schemas.workflow import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationListResponse,
    ApplicationStatus,
    ApplicationUpdate,
)
from app.services.applications import record_activity, validate_transition, validate_undo
from app.services.jobs import summarize_job as _summary


def related_metadata(db, actor, applications):
    from app.models.foundation import CandidateDocument
    from app.models.workflow import Reminder
    from app.services.documents import metadata

    document_ids = [item.selected_document_id for item in applications if item.selected_document_id]
    docs = (
        {
            item.id: metadata(item)
            for item in db.scalars(
                select(CandidateDocument).where(
                    CandidateDocument.owner_id == actor.owner_id,
                    CandidateDocument.id.in_(document_ids),
                )
            )
        }
        if document_ids
        else {}
    )
    ids = [item.id for item in applications]
    reminders = {}
    if ids:
        for item in db.scalars(
            select(Reminder)
            .where(Reminder.owner_id == actor.owner_id, Reminder.application_id.in_(ids))
            .order_by(Reminder.due_at, Reminder.id)
        ):
            reminders.setdefault(item.application_id, []).append(
                {
                    column.name: getattr(item, column.name)
                    for column in Reminder.__table__.columns
                    if column.name != "owner_id"
                }
            )
    return docs, reminders


def detail(
    db: Session, actor: ActorContext, *, application: Application, job: Job
) -> ApplicationDetail:
    activities = db.scalars(
        select(ApplicationActivity)
        .where(
            ApplicationActivity.application_id == application.id,
            ApplicationActivity.owner_id == actor.owner_id,
        )
        .order_by(
            ApplicationActivity.metadata_json["version"].as_integer(),
            ApplicationActivity.occurred_at,
            ApplicationActivity.id,
        )
    ).all()
    docs, reminders = related_metadata(db, actor, [application])
    return ApplicationDetail(
        selected_document=docs.get(application.selected_document_id),
        reminders=reminders.get(application.id, []),
        **{
            column.name: getattr(application, column.name)
            for column in Application.__table__.columns
            if column.name not in {"job_id", "owner_id", "selected_document_id"}
        },
        job=_summary(db, job),
        activities=activities,
    )


def create_application(db: Session, actor: ActorContext, *, payload: ApplicationCreate):
    actor.require("applications:write", approval=True)
    job = db.scalar(
        select(Job)
        .where(Job.id == payload.job_id, Job.owner_id == actor.owner_id, Job.deleted_at.is_(None))
        .with_for_update()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Lowongan tidak ditemukan.")
    if db.scalar(
        select(Application.id).where(
            Application.owner_id == actor.owner_id, Application.job_id == job.id
        )
    ):
        raise HTTPException(
            status_code=409, detail="Lowongan ini sudah memiliki lamaran di pipeline."
        )
    if payload.selected_document_id is not None:
        from app.services.documents import owned_document

        owned_document(db, actor, payload.selected_document_id)
    application = Application(owner_id=actor.owner_id, **payload.model_dump())
    db.add(application)
    try:
        db.flush()
        record_activity(db, application, None, actor=actor)
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Lamaran sudah tercatat.") from None
    return detail(db, actor, application=application, job=job)


def list_applications(
    db: Session,
    actor: ActorContext,
    *,
    status: ApplicationStatus | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    job_ids=None,
    application_ids=None,
    board_only=False,
):
    actor.require("applications:read")
    query = (
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(
            Application.owner_id == actor.owner_id,
            Job.owner_id == actor.owner_id,
            Job.deleted_at.is_(None),
        )
    )
    if board_only:
        query = query.where(Application.status != "preparing")
    if job_ids:
        query = query.where(Job.id.in_(job_ids))
    if application_ids:
        query = query.where(Application.id.in_(application_ids))
    if status:
        query = query.where(Application.status == status.value)
    if q and q.strip():
        search = f"%{q.strip()}%"
        query = query.where(or_(Job.title.ilike(search), Job.company.ilike(search)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(Application.updated_at.desc(), Application.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    from app.services.jobs import summarize_jobs

    summaries = {job.id: job for job in summarize_jobs(db, [job for _, job in rows])}
    ids = [application.id for application, _ in rows]
    activities = {}
    if ids:
        for event in db.scalars(
            select(ApplicationActivity)
            .where(
                ApplicationActivity.application_id.in_(ids),
                ApplicationActivity.owner_id == actor.owner_id,
            )
            .order_by(
                ApplicationActivity.metadata_json["version"].as_integer(),
                ApplicationActivity.occurred_at,
                ApplicationActivity.id,
            )
        ):
            activities.setdefault(event.application_id, []).append(event)
    docs, reminders = related_metadata(db, actor, [application for application, _ in rows])
    items = [
        ApplicationDetail(
            selected_document=docs.get(application.selected_document_id),
            reminders=reminders.get(application.id, []),
            **{
                column.name: getattr(application, column.name)
                for column in Application.__table__.columns
                if column.name not in {"job_id", "owner_id", "selected_document_id"}
            },
            job=summaries[application.job_id],
            activities=activities.get(application.id, []),
        )
        for application, _ in rows
    ]
    return ApplicationListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
        has_next=page * page_size < total,
    )


def get_application(db: Session, actor: ActorContext, *, application_id: uuid.UUID):
    actor.require("applications:read")
    row = db.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(
            Application.id == application_id,
            Application.owner_id == actor.owner_id,
            Job.owner_id == actor.owner_id,
            Job.deleted_at.is_(None),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Lamaran tidak ditemukan.")
    return detail(db, actor, application=row[0], job=row[1])


def update_application(
    db: Session, actor: ActorContext, *, application_id: uuid.UUID, payload: ApplicationUpdate
):
    actor.require("applications:write", approval=True)
    application = db.scalar(
        select(Application)
        .where(Application.id == application_id, Application.owner_id == actor.owner_id)
        .with_for_update()
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Lamaran tidak ditemukan.")
    job = db.scalar(
        select(Job).where(
            Job.id == application.job_id, Job.owner_id == actor.owner_id, Job.deleted_at.is_(None)
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Lowongan tidak ditemukan.")
    if payload.expected_version != application.version:
        raise HTTPException(status_code=409, detail="Lamaran sudah berubah. Muat ulang pipeline.")
    before = application.status
    if "status" in payload.model_fields_set:
        if payload.status is None:
            raise HTTPException(status_code=422, detail="Status tidak boleh kosong.")
        validate_transition(
            application, payload.status.value, payload.model_dump(exclude_unset=True)
        )
        if payload.change_kind == "undo":
            validate_undo(db, application, payload.status.value)
    values = payload.model_dump(exclude_unset=True, exclude={"expected_version", "change_kind"})
    if values.get("selected_document_id"):
        from app.services.documents import owned_document

        owned_document(db, actor, values["selected_document_id"])
    validate_transition(application, str(values.get("status", application.status)), values)
    if all((getattr(application, field) == value for field, value in values.items())):
        return detail(db, actor, application=application, job=job)
    for field, value in values.items():
        setattr(application, field, value)
    application.version += 1
    application.updated_at = datetime.now(UTC)
    if application.status != before:
        record_activity(db, application, before, payload.change_kind, actor=actor)
    else:
        from app.services.audit import audit

        audit(
            db,
            actor,
            "application_updated",
            "application",
            application.id,
            version=application.version,
        )
    db.flush()
    return detail(db, actor, application=application, job=job)


def states_for_jobs(db, actor, job_ids):
    actor.require("applications:read")
    if not job_ids:
        return {}
    return {
        str(job_id): status
        for job_id, status in db.execute(
            select(Application.job_id, Application.status)
            .join(Job, Job.id == Application.job_id)
            .where(
                Application.owner_id == actor.owner_id,
                Job.owner_id == actor.owner_id,
                Job.deleted_at.is_(None),
                Application.job_id.in_(job_ids),
            )
        )
    }
