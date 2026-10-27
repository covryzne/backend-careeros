from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import Job, JobMatch, JobSkill, JobSource
from app.schemas.workflow import JobSummary


def summarize_job(db: Session, job: Job) -> JobSummary:
    source_name = db.scalar(select(JobSource.name).where(
        JobSource.id == job.source_id, JobSource.owner_id == job.owner_id,
    ))
    skills = db.scalars(select(JobSkill.name).where(
        JobSkill.job_id == job.id,
    ).order_by(JobSkill.name)).all()
    score = db.scalar(select(JobMatch.score).where(
        JobMatch.job_id == job.id, JobMatch.owner_id == job.owner_id,
    ).order_by(JobMatch.calculated_at.desc(), JobMatch.id.desc()).limit(1))
    return JobSummary.model_validate({
        **{column.name: getattr(job, column.name) for column in Job.__table__.columns},
        "source_name": source_name, "skills": skills,
        "match_score": float(score) if score is not None else None,
    })
