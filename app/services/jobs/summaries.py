from sqlalchemy import func, select

from app.core.config import settings
from app.models.candidate import CandidateProfile
from app.models.crawler import CrawlRun, CrawlRunItem
from app.models.workflow import Job, JobMatch, JobSkill, JobSource
from app.schemas.workflow import JobSummary
from app.services.jobs import matching
from app.services.jobs.skills import effective_skills


def summarize_jobs(db, jobs):
    if not jobs:
        return []
    ids = [job.id for job in jobs]
    owners = {job.owner_id for job in jobs}
    for owner_id in sorted(owners, key=str):
        matching.refresh(db, owner_id, [job.id for job in jobs if job.owner_id == owner_id])
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
    keywords = {}
    for job_id, owner_id, keyword in db.execute(
        select(CrawlRunItem.job_id, CrawlRun.owner_id, CrawlRunItem.keyword)
        .join(CrawlRun, CrawlRun.id == CrawlRunItem.run_id)
        .where(
            CrawlRunItem.job_id.in_(ids),
            CrawlRun.owner_id.in_(owners),
            CrawlRunItem.status.in_(["new", "duplicate"]),
        )
        .order_by(CrawlRunItem.created_at, CrawlRunItem.id)
    ):
        label = keyword.strip()
        labels = keywords.setdefault((owner_id, job_id), [])
        if label and label not in labels:
            labels.append(label)
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
            JobMatch.matching_skills,
            JobMatch.missing_requirements,
            JobMatch.profile_version,
            CandidateProfile.profile_version.label("current_version"),
            func.row_number()
            .over(
                partition_by=(JobMatch.owner_id, JobMatch.job_id),
                order_by=(
                    (JobMatch.algorithm_version == matching.ALGORITHM).desc(),
                    JobMatch.calculated_at.desc(),
                    JobMatch.id.desc(),
                ),
            )
            .label("position"),
        )
        .join(CandidateProfile, CandidateProfile.id == JobMatch.profile_id)
        .where(
            JobMatch.job_id.in_(ids),
            JobMatch.owner_id.in_(owners),
            CandidateProfile.owner_id == JobMatch.owner_id,
            JobMatch.profile_version == CandidateProfile.profile_version,
            matching.eligible_match(),
        )
        .subquery()
    )
    matches = {
        (r.owner_id, r.job_id): r for r in db.execute(select(ranked).where(ranked.c.position == 1))
    }
    result = []
    for job in jobs:
        extracted = (
            effective_skills(job, skills.get(job.id, []))
            if settings.job_matching_enabled
            else [{"skill": name, "field": "skills", "excerpt": name}
                  for name in skills.get(job.id, [])]
        )
        match = matches.get((job.owner_id, job.id))
        matched_count = len(match.matching_skills or []) if match else 0
        required_count = matched_count + len(match.missing_requirements or []) if match else 0
        result.append(
            JobSummary.model_validate(
                {
                    **{c.name: getattr(job, c.name) for c in Job.__table__.columns},
                    "source_name": sources.get(job.source_id)
                    if source_owners.get(job.source_id) == job.owner_id
                    else None,
                    "crawler_keywords": keywords.get((job.owner_id, job.id), []),
                    "description_preview": " ".join(job.description.split())[:500]
                    if job.description and job.description.strip()
                    else None,
                    "skills": [item["skill"] for item in extracted],
                    "skill_evidence": extracted,
                    "matching_status": "available" if settings.job_matching_enabled else "coming_soon",
                    "match_score": float(match.score) if match else None,
                    "match_profile_version": match.profile_version if match else None,
                    "is_match_stale": False,
                    "matching_skills": match.matching_skills or [] if match else [],
                    "missing_requirements": match.missing_requirements or [] if match else [],
                    "match_summary": (
                        f"{matched_count} dari {required_count} skill lowongan ditemukan pada profil"
                        if extracted
                        else "Berdasarkan hasil matching tersimpan"
                    )
                    if match
                    else None,
                }
            )
        )
    return result


def summarize_job(db, job):
    return summarize_jobs(db, [job])[0]
