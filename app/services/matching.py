"""Refresh a transparent skill-alignment cache from the current owned profile."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Numeric, exists, false, literal, or_, select

from app.core.config import settings
from app.core.security import ActorContext
from app.models.candidate import CandidateProfile
from app.models.workflow import Job, JobMatch, JobSkill
from app.services.idempotency import lock_owner
from app.services.professional_sources import draft_sources
from app.services.profile import load_profile
from app.services.skill_extraction import VERSION, canonical, effective_skills, pattern_for

ALGORITHM = "professional-overlap-v2"


def refresh(db, owner_id, job_ids=None):
    if not settings.job_matching_enabled:
        return 0
    if db.scalar(select(CandidateProfile.id).where(CandidateProfile.owner_id == owner_id)) is None:
        return 0
    actor = ActorContext(owner_id)
    lock_owner(db, actor)
    owner = load_profile(db, actor, lock=True)
    if owner is None:
        return 0
    sources = draft_sources(owner, max_chars=None)
    if not sources:
        return 0
    query = select(Job).where(Job.owner_id == owner_id, Job.deleted_at.is_(None))
    if job_ids is not None:
        if not job_ids:
            return 0
        query = query.where(Job.id.in_(job_ids))
    jobs = db.scalars(query.execution_options(populate_existing=True)).all()
    if not jobs:
        return 0
    ids = [item.id for item in jobs]
    skills = {}
    for identity, name in db.execute(
        select(JobSkill.job_id, JobSkill.name)
        .where(JobSkill.job_id.in_(ids))
        .order_by(JobSkill.name)
    ):
        if name.strip():
            skills.setdefault(identity, []).append(name.strip())
    cached = {
        item.job_id: item
        for item in db.scalars(
            select(JobMatch).where(
                JobMatch.owner_id == owner_id,
                JobMatch.profile_id == owner.id,
                JobMatch.profile_version == owner.profile_version,
                JobMatch.algorithm_version == ALGORITHM,
                JobMatch.job_id.in_(ids),
            )
        )
    }
    changed = 0
    for job in jobs:
        extracted = effective_skills(job, skills.get(job.id, []))
        required = [item["skill"] for item in extracted]
        if not required:
            if job.id in cached:
                db.delete(cached[job.id])  # Invalidate only the current derived cache.
            continue
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "extractor": VERSION,
                    "title": job.title,
                    "description": job.description,
                    "requirements": job.requirements,
                    "responsibilities": job.responsibilities,
                    "experience_requirements": job.experience_requirements,
                    "preferred_qualifications": job.preferred_qualifications,
                    "skills": required,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        row = cached.get(job.id)
        if row and any(
            item.get("job_fingerprint") == fingerprint
            for item in (row.evidence or [])
            if isinstance(item, dict)
        ):
            continue
        matched, missing, evidence = [], [], [{"job_fingerprint": fingerprint}, *extracted]
        for skill in required:
            pattern = pattern_for(skill)
            source = next((item for item in sources if (
                item["kind"] == "skill" and canonical(item["text"]).casefold() == skill.casefold()
            ) or pattern.search(item["text"])), None)
            (matched if source else missing).append(skill)
            if source:
                evidence.append(
                    {"source_id": source["id"], "kind": source["kind"], "matched_skill": skill}
                )
        if row is None:
            row = JobMatch(
                owner_id=owner_id,
                job_id=job.id,
                profile_id=owner.id,
                profile_version=owner.profile_version,
                algorithm_version=ALGORITHM,
            )
            db.add(row)
        row.score = (Decimal(len(matched)) * 100 / len(required)).quantize(Decimal("0.01"))
        row.matching_skills = matched
        row.missing_requirements = missing
        row.evidence = evidence
        row.blockers = []
        row.calculated_at = datetime.now(UTC)
        changed += 1
    db.flush()
    return changed


def eligible_match():
    if not settings.job_matching_enabled:
        return false()
    # Current rows are refreshed/invalidated before reads. Preserve nonautomatic legacy
    # scores only for jobs without structured skills; v1 must not mask unknown v2 data.
    has_skills = exists(
        select(JobSkill.id).where(JobSkill.job_id == JobMatch.job_id, JobSkill.name != "")
    )
    return or_(
        JobMatch.algorithm_version == ALGORITHM,
        JobMatch.algorithm_version.notin_([ALGORITHM, "professional-overlap-v1"]) & ~has_skills,
    )


def score_for(owner_id):
    if not settings.job_matching_enabled:
        return literal(None, type_=Numeric(5, 2))
    return (
        select(JobMatch.score)
        .join(CandidateProfile, CandidateProfile.id == JobMatch.profile_id)
        .where(
            JobMatch.job_id == Job.id,
            JobMatch.owner_id == owner_id,
            CandidateProfile.owner_id == owner_id,
            JobMatch.profile_version == CandidateProfile.profile_version,
            eligible_match(),
        )
        .order_by(
            (JobMatch.algorithm_version == ALGORITHM).desc(),
            JobMatch.calculated_at.desc(),
            JobMatch.id.desc(),
        )
        .limit(1)
        .scalar_subquery()
    )
