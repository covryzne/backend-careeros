import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import ActorContext
from app.models.workflow import Job, JobSkill, JobSource
from app.schemas.workflow import JobCreate, JobListResponse, JobSummary, JobUpdate
from app.services.jobs import summarize_job as _summary


def create_job(db: Session, actor: ActorContext, *, payload: JobCreate) -> JobSummary:
    actor.require("jobs:write")
    from urllib.parse import urlsplit, urlunsplit

    from app.services.audit import audit

    if not payload.title.strip() or not payload.company.strip():
        raise HTTPException(422, "Title and company cannot be blank")
    if (
        payload.salary_min is not None
        and payload.salary_min < 0
        or payload.salary_max is not None
        and payload.salary_max < 0
    ):
        raise HTTPException(422, "Salary cannot be negative")
    if (
        payload.salary_min is not None
        and payload.salary_max is not None
        and payload.salary_min > payload.salary_max
    ):
        raise HTTPException(422, "Invalid salary range")
    canonical = None
    if payload.original_url:
        try:
            parsed = urlsplit(payload.original_url.strip())
        except ValueError:
            raise HTTPException(422, "Invalid job URL") from None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(422, "Job URL must use HTTP or HTTPS without credentials")
        canonical = urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
        )
        existing = db.scalar(
            select(Job).where(
                Job.owner_id == actor.owner_id,
                Job.deleted_at.is_(None),
                or_(Job.canonical_url == canonical, Job.original_url == payload.original_url),
            )
        )
        if existing:
            raise HTTPException(
                409,
                {
                    "code": "DUPLICATE_JOB",
                    "message": "Lowongan dengan URL ini sudah tersedia.",
                    "existing_job_id": str(existing.id),
                },
            )
    source = None
    if payload.source_name:
        source = db.scalar(
            select(JobSource).where(
                JobSource.owner_id == actor.owner_id, JobSource.name == payload.source_name
            )
        )
        if source is None:
            source = JobSource(
                owner_id=actor.owner_id, name=payload.source_name, source_type="manual"
            )
            db.add(source)
            db.flush()
    values = payload.model_dump(exclude={"source_name", "skills"})
    values["title"] = values["title"].strip()
    values["company"] = values["company"].strip()
    values["original_url"] = values.get("original_url") or None
    from decimal import Decimal

    for field in ("salary_min", "salary_max"):
        if values.get(field) is not None:
            values[field] = Decimal(str(values[field])).quantize(Decimal("0.01"))
    job = Job(
        canonical_url=canonical,
        owner_id=actor.owner_id,
        source_id=source.id if source else None,
        discovered_at=datetime.now(UTC),
        **values,
    )
    db.add(job)
    db.flush()
    for name in dict.fromkeys(payload.skills):
        db.add(JobSkill(job_id=job.id, name=name))
    db.flush()
    audit(db, actor, "job_created", "job", job.id)
    return _summary(db, job)


def update_job(
    db: Session, actor: ActorContext, *, job_id: uuid.UUID, payload: JobUpdate
) -> JobSummary:
    actor.require("jobs:write")
    job = db.scalar(
        select(Job).where(
            Job.id == job_id, Job.owner_id == actor.owner_id, Job.deleted_at.is_(None)
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field in {"title", "company"}:
            value = value.strip()
            if not value:
                raise HTTPException(422, "Title and company cannot be blank")
        setattr(job, field, value)
    from app.services.audit import audit

    audit(db, actor, "job_updated", "job", job.id)
    db.flush()
    return _summary(db, job)


def delete_job(db: Session, actor: ActorContext, *, job_id: uuid.UUID) -> None:
    actor.require("jobs:write")
    job = db.scalar(
        select(Job).where(
            Job.id == job_id, Job.owner_id == actor.owner_id, Job.deleted_at.is_(None)
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    from app.services.audit import audit

    audit(db, actor, "job_deleted", "job", job.id)
    job.deleted_at = datetime.now(UTC)
    db.flush()


def list_jobs(
    db,
    actor,
    *,
    q=None,
    status=None,
    source_id=None,
    work_arrangement=None,
    min_match_score=None,
    max_match_score=None,
    discovered_from=None,
    discovered_to=None,
    sort="discovered_at",
    page=1,
    page_size=20,
    job_ids=None,
):
    from app.services import matching
    from app.services.jobs import summarize_jobs

    actor.require("jobs:read")
    matching.refresh(db, actor.owner_id)
    score = matching.score_for(actor.owner_id)
    query = select(Job).where(Job.owner_id == actor.owner_id, Job.deleted_at.is_(None))
    if job_ids:
        query = query.where(Job.id.in_(job_ids))
    if q and q.strip():
        query = query.where(
            or_(
                Job.title.ilike("%" + q.strip() + "%"),
                Job.company.ilike("%" + q.strip() + "%"),
                Job.location.ilike("%" + q.strip() + "%"),
            )
        )
    if status:
        query = query.where(Job.status == str(status))
    if source_id:
        query = query.where(Job.source_id == source_id)
    if work_arrangement:
        query = query.where(Job.work_arrangement == work_arrangement)
    if min_match_score is not None:
        query = query.where(score >= min_match_score)
    if max_match_score is not None:
        query = query.where(score <= max_match_score)
    if discovered_from or discovered_to:
        from datetime import timedelta

        from app.services.dashboard import utc_midnight

        if discovered_from:
            query = query.where(Job.discovered_at >= utc_midnight(discovered_from))
        if discovered_to:
            query = query.where(Job.discovered_at < utc_midnight(discovered_to + timedelta(days=1)))
    if discovered_from and discovered_to and discovered_from > discovered_to:
        raise HTTPException(422, "Invalid date range")
    if (
        min_match_score is not None
        and max_match_score is not None
        and min_match_score > max_match_score
    ):
        raise HTTPException(422, "Invalid score range")
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    sorts = {
        "discovered_at": Job.discovered_at.desc(),
        "title": Job.title.asc(),
        "match_score": score.desc().nullslast(),
        "application_deadline": Job.application_deadline.asc().nullslast(),
    }
    if sort not in sorts:
        raise HTTPException(422, "Invalid sort")
    jobs = db.scalars(
        query.order_by(sorts[sort], Job.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return JobListResponse(
        items=summarize_jobs(db, jobs),
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
        has_next=page * page_size < total,
    )


def get_job(db, actor, job_id):
    actor.require("jobs:read")
    job = db.scalar(
        select(Job).where(
            Job.id == job_id, Job.owner_id == actor.owner_id, Job.deleted_at.is_(None)
        )
    )
    if job is None:
        raise HTTPException(404, "Job not found")
    from app.services.job_content import details

    return {
        **_summary(db, job).model_dump(mode="json"),
        **details(job),
        "notes": job.notes,
    }


def career_job_details(db, actor, jobs):
    """Bounded detail batch for the same owned summaries returned by list_jobs."""
    actor.require("jobs:read")
    ids = [job.id for job in jobs]
    rows = (
        db.scalars(
            select(Job).where(
                Job.owner_id == actor.owner_id, Job.deleted_at.is_(None), Job.id.in_(ids)
            )
        ).all()
        if ids
        else []
    )
    from app.services.job_content import details

    return {
        str(job.id): "\n".join(
            f"{key}: {value}"
            for key, value in details(job).items()
            if value and not key.startswith("hr_contact_")
        )
        for job in rows
    }
