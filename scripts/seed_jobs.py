"""Seed 15 complete demo jobs without requiring a running API server."""

import argparse
import re
import sys
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext
from app.db.session import SessionLocal
from app.models.workflow import Job, JobMatch, JobSkill
from app.schemas.workflow import JobCreate
from app.services import job_workflows, profile
from app.services.audit import audit
from app.services.idempotency import lock_owner
from app.services.professional_sources import draft_sources
from scripts.seed_data import jobs_for

JOBS = jobs_for(datetime.now(UTC).date())
ALGORITHM = "seed-professional-overlap-v2"


def seed(db, actor=None):
    actor = actor or ActorContext(LOCAL_OWNER_ID)
    lock_owner(db, actor)
    owner = profile.load_profile(db, actor, lock=True)
    sources = draft_sources(owner)
    counts = {"created": 0, "restored": 0, "enriched": 0, "matches_created": 0}
    for record in JOBS:
        job = db.scalar(
            select(Job)
            .where(Job.owner_id == actor.owner_id, Job.external_job_id == record["external_job_id"])
            .with_for_update()
        )
        if job is None:
            payload = JobCreate.model_validate(
                {key: value for key, value in record.items() if key in JobCreate.model_fields}
                | {"source_name": "CareerOS Demo Seeds"}
            )
            summary = job_workflows.create_job(db, actor, payload=payload)
            job = db.get(Job, summary.id)
            job.external_job_id = record["external_job_id"]
            job.posting_date = record["posting_date"]
            job.application_deadline = record["application_deadline"]
            job.discovered_at = datetime.combine(record["discovered_at"], time(9), tzinfo=UTC)
            job.notes = record["notes"]
            counts["created"] += 1
        else:
            changed = False
            if job.deleted_at is not None:
                job.deleted_at = None
                job.status = record["status"]
                counts["restored"] += 1
                changed = True
            for key, value in record.items():
                if key in {"skills", "external_job_id", "status", "discovered_at"}:
                    continue
                if getattr(job, key) in (None, ""):
                    setattr(job, key, value)
                    changed = True
            if not job.canonical_url:
                job.canonical_url = job.original_url
                changed = True
            if changed:
                counts["enriched"] += 1
                audit(db, actor, "seed_job_enriched", "job", job.id)
        existing = {
            value.strip().casefold()
            for value in db.scalars(select(JobSkill.name).where(JobSkill.job_id == job.id))
        }
        for name in record["skills"]:
            if name.strip().casefold() not in existing:
                db.add(JobSkill(job_id=job.id, name=name, is_required=True))
        db.flush()
        if owner is None:
            continue
        prior = db.scalar(
            select(JobMatch.id).where(
                JobMatch.owner_id == actor.owner_id,
                JobMatch.job_id == job.id,
                JobMatch.profile_version == owner.profile_version,
                JobMatch.algorithm_version == ALGORITHM,
            )
        )
        if prior:
            continue
        matched, missing, evidence = [], [], []
        for skill in record["skills"]:
            pattern = re.compile(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", re.IGNORECASE)
            source = next((item for item in sources if pattern.search(item["text"])), None)
            (matched if source else missing).append(skill)
            if source:
                evidence.append(
                    {"source_id": source["id"], "kind": source["kind"], "matched_skill": skill}
                )
        score = (Decimal(len(matched)) * 100 / len(record["skills"])).quantize(Decimal("0.01"))
        db.add(
            JobMatch(
                owner_id=actor.owner_id,
                job_id=job.id,
                profile_id=owner.id,
                profile_version=owner.profile_version,
                score=score,
                matching_skills=matched,
                missing_requirements=missing,
                blockers=[],
                evidence=evidence,
                algorithm_version=ALGORITHM,
                calculated_at=datetime.now(UTC),
            )
        )
        counts["matches_created"] += 1
    db.flush()
    return {
        **counts,
        "seed_jobs": len(JOBS),
        "profile_version": owner.profile_version if owner else None,
    }


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if settings.app_env not in {"development", "test"}:
        raise SystemExit("Seed jobs hanya untuk database development/test.")
    try:
        with SessionLocal.begin() as db:
            result = seed(db)
    except SQLAlchemyError:
        raise SystemExit(
            "Seed jobs gagal. Periksa DATABASE_URL dan jalankan python -m alembic upgrade head."
        ) from None
    print("Jobs demo siap:", result)
    if result["profile_version"] is None:
        print(
            "Profil belum tersedia. Jalankan python seed_real_profile.py lalu ulangi seed_jobs.py untuk skor demo."
        )


if __name__ == "__main__":
    main()
