# ruff: noqa: B008

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.profile import LOCAL_OWNER_ID
from app.db.session import get_db
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

router = APIRouter()


def detail(db: Session, application: Application, job: Job) -> ApplicationDetail:
    activities = db.scalars(select(ApplicationActivity).where(
        ApplicationActivity.application_id == application.id,
        ApplicationActivity.owner_id == LOCAL_OWNER_ID,
    ).order_by(ApplicationActivity.occurred_at, ApplicationActivity.id)).all()
    return ApplicationDetail(
        **{column.name: getattr(application, column.name) for column in Application.__table__.columns
           if column.name not in {"job_id", "owner_id", "selected_document_id"}},
        job=_summary(db, job), activities=activities,
    )


@router.post("", response_model=ApplicationDetail, status_code=201)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(
        Job.id == payload.job_id, Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None),
    ).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Lowongan tidak ditemukan.")
    if db.scalar(select(Application.id).where(
        Application.owner_id == LOCAL_OWNER_ID, Application.job_id == job.id,
    )):
        raise HTTPException(status_code=409, detail="Lowongan ini sudah memiliki lamaran di pipeline.")
    if payload.selected_document_id is not None:
        raise HTTPException(status_code=422, detail="Pemilihan dokumen belum tersedia.")
    application = Application(owner_id=LOCAL_OWNER_ID, **payload.model_dump())
    db.add(application)
    try:
        db.flush()
        record_activity(db, application, None)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Lamaran sudah tercatat.") from None
    return detail(db, application, job)


@router.get("", response_model=ApplicationListResponse)
def list_applications(
    status: ApplicationStatus | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = select(Application, Job).join(Job, Job.id == Application.job_id).where(
        Application.owner_id == LOCAL_OWNER_ID, Job.owner_id == LOCAL_OWNER_ID,
        Job.deleted_at.is_(None),
    )
    if status:
        query = query.where(Application.status == status.value)
    if q and q.strip():
        search = f"%{q.strip()}%"
        query = query.where(or_(Job.title.ilike(search), Job.company.ilike(search)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(Application.updated_at.desc(), Application.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return ApplicationListResponse(
        items=[detail(db, application, job) for application, job in rows],
        page=page, page_size=page_size, total=total,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(application_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.execute(select(Application, Job).join(Job, Job.id == Application.job_id).where(
        Application.id == application_id, Application.owner_id == LOCAL_OWNER_ID,
        Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None),
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Lamaran tidak ditemukan.")
    return detail(db, *row)


@router.patch("/{application_id}", response_model=ApplicationDetail)
def update_application(
    application_id: uuid.UUID, payload: ApplicationUpdate, db: Session = Depends(get_db),
):
    application = db.scalar(select(Application).where(
        Application.id == application_id, Application.owner_id == LOCAL_OWNER_ID,
    ).with_for_update())
    if application is None:
        raise HTTPException(status_code=404, detail="Lamaran tidak ditemukan.")
    job = db.scalar(select(Job).where(
        Job.id == application.job_id, Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None),
    ))
    if job is None:
        raise HTTPException(status_code=404, detail="Lowongan tidak ditemukan.")
    if payload.expected_version != application.version:
        raise HTTPException(status_code=409, detail="Lamaran sudah berubah. Muat ulang pipeline.")
    before = application.status
    if "status" in payload.model_fields_set:
        if payload.status is None:
            raise HTTPException(status_code=422, detail="Status tidak boleh kosong.")
        validate_transition(application, payload.status.value)
        if payload.change_kind == "undo":
            validate_undo(db, application, payload.status.value)
    values = payload.model_dump(exclude_unset=True, exclude={"expected_version", "change_kind"})
    if all(getattr(application, field) == value for field, value in values.items()):
        return detail(db, application, job)
    for field, value in values.items():
        setattr(application, field, value)
    application.version += 1
    application.updated_at = datetime.now(UTC)
    if application.status != before:
        record_activity(db, application, before, payload.change_kind)
    db.commit()
    return detail(db, application, job)
