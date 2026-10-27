from sqlalchemy import func, select

from app.models.candidate import CandidateProfile
from app.models.workflow import Job, JobMatch, JobSkill, JobSource
from app.schemas.workflow import JobSummary


def summarize_jobs(db, jobs):
    if not jobs:
        return []
    ids = [job.id for job in jobs]
    owners = {job.owner_id for job in jobs}
    source_ids = [job.source_id for job in jobs if job.source_id]
    source_rows = (
        db.execute(
            select(JobSource.id, JobSource.name, JobSource.owner_id).where(
                JobSource.owner_id.in_(owners), JobSource.id.in_(source_ids)
            )
        ).all()
        if source_ids
        else []
    )
    sources = {row.id: row.name for row in source_rows}
    source_owners = {row.id: row.owner_id for row in source_rows}
    skills = {}
    for job_id, name in db.execute(
        select(JobSkill.job_id, JobSkill.name)
        .where(JobSkill.job_id.in_(ids))
        .order_by(JobSkill.name)
    ):
        skills.setdefault(job_id, []).append(name)
    ranked = (
        select(
            JobMatch.job_id,
            JobMatch.owner_id,
            JobMatch.score,
            JobMatch.profile_version,
            CandidateProfile.profile_version.label("current_version"),
            func.row_number()
            .over(
                partition_by=(JobMatch.owner_id, JobMatch.job_id),
                order_by=(JobMatch.calculated_at.desc(), JobMatch.id.desc()),
            )
            .label("position"),
        )
        .join(CandidateProfile, CandidateProfile.id == JobMatch.profile_id)
        .where(
            JobMatch.job_id.in_(ids),
            JobMatch.owner_id.in_(owners),
            CandidateProfile.owner_id == JobMatch.owner_id,
        )
        .subquery()
    )
    matches = {
        (r.owner_id, r.job_id): r for r in db.execute(select(ranked).where(ranked.c.position == 1))
    }
    result = []
    for job in jobs:
        match = matches.get((job.owner_id, job.id))
        result.append(
            JobSummary.model_validate(
                {
                    **{c.name: getattr(job, c.name) for c in Job.__table__.columns},
                    "source_name": sources.get(job.source_id)
                    if source_owners.get(job.source_id) == job.owner_id
                    else None,
                    "skills": skills.get(job.id, []),
                    "match_score": float(match.score) if match else None,
                    "match_profile_version": match.profile_version if match else None,
                    "is_match_stale": match.profile_version != match.current_version
                    if match
                    else False,
                }
            )
        )
    return result


def summarize_job(db, job):
    return summarize_jobs(db, [job])[0]
