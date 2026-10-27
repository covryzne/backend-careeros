# ruff: noqa: B008

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.routes.profile import LOCAL_OWNER_ID
from app.db.session import get_db
from app.models.workflow import Job, JobMatch, JobSkill, JobSource
from app.schemas.workflow import JobCreate, JobListResponse, JobStatus, JobSummary, JobUpdate
from app.services.jobs import summarize_job as _summary

router = APIRouter()


@router.post("", response_model=JobSummary, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> JobSummary:
    source = None
    if payload.source_name:
        source = db.scalar(select(JobSource).where(
            JobSource.owner_id == LOCAL_OWNER_ID, JobSource.name == payload.source_name
        ))
        if source is None:
            source = JobSource(owner_id=LOCAL_OWNER_ID, name=payload.source_name, source_type="manual")
            db.add(source)
            db.flush()
    values = payload.model_dump(exclude={"source_name", "skills"})
    job = Job(owner_id=LOCAL_OWNER_ID, source_id=source.id if source else None, discovered_at=datetime.now(UTC), **values)
    db.add(job)
    db.flush()
    for name in dict.fromkeys(payload.skills):
        db.add(JobSkill(job_id=job.id, name=name))
    db.commit()
    return _summary(db, job)


@router.patch("/{job_id}", response_model=JobSummary)
def update_job(job_id: uuid.UUID, payload: JobUpdate, db: Session = Depends(get_db)) -> JobSummary:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None)))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(job, field, value)
    db.commit()
    return _summary(db, job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None)))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.deleted_at = datetime.now(UTC)
    db.commit()


@router.get("", response_model=JobListResponse)
def list_jobs(
    q: str | None = Query(default=None),
    status: JobStatus | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
    work_arrangement: str | None = Query(default=None),
    min_match_score: float | None = Query(default=None, ge=0, le=100),
    max_match_score: float | None = Query(default=None, ge=0, le=100),
    discovered_from: date | None = Query(default=None),
    discovered_to: date | None = Query(default=None),
    sort: str = Query(default="discovered_at"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JobListResponse:
    latest_match_date = (
        select(func.max(JobMatch.calculated_at))
        .where(JobMatch.job_id == Job.id, JobMatch.owner_id == LOCAL_OWNER_ID)
        .correlate(Job)
        .scalar_subquery()
    )
    latest_match = JobMatch.__table__.alias("latest_match")
    query = (
        select(Job, JobSource.name, latest_match.c.score)
        .outerjoin(JobSource, Job.source_id == JobSource.id)
        .outerjoin(
            latest_match,
            (latest_match.c.job_id == Job.id)
            & (latest_match.c.owner_id == LOCAL_OWNER_ID)
            & (latest_match.c.calculated_at == latest_match_date),
        )
        .where(Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None))
    )
    count_query = select(func.count()).select_from(Job).where(
        Job.owner_id == LOCAL_OWNER_ID, Job.deleted_at.is_(None)
    )
    filters = []
    if q:
        search = f"%{q.strip()}%"
        filters.append(or_(Job.title.ilike(search), Job.company.ilike(search), Job.location.ilike(search)))
    if status:
        filters.append(Job.status == status.value)
    if source_id:
        filters.append(Job.source_id == source_id)
    if work_arrangement:
        filters.append(Job.work_arrangement == work_arrangement)
    if discovered_from:
        filters.append(Job.discovered_at >= discovered_from)
    if discovered_to:
        filters.append(Job.discovered_at < date.fromordinal(discovered_to.toordinal() + 1))
    if min_match_score is not None:
        filters.append(latest_match.c.score >= min_match_score)
    if max_match_score is not None:
        filters.append(latest_match.c.score <= max_match_score)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    sort_columns = {
        "discovered_at": Job.discovered_at.desc(),
        "title": Job.title.asc(),
        "application_deadline": Job.application_deadline.asc().nullslast(),
        "match_score": latest_match.c.score.desc().nullslast(),
    }
    query = query.order_by(sort_columns.get(sort, sort_columns["discovered_at"]), Job.id)
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = db.scalar(count_query) or 0
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for job, source_name, score in rows:
        skills = db.scalars(select(JobSkill.name).where(JobSkill.job_id == job.id).order_by(JobSkill.name)).all()
        items.append(
            JobSummary.model_validate(
                {
                    **{column.name: getattr(job, column.name) for column in Job.__table__.columns},
                    "source_name": source_name,
                    "skills": skills,
                    "match_score": float(score) if score is not None else None,
                }
            )
        )
    return JobListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
    )
