"""Idempotently seed saved and archived jobs for the local CareerOS database."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.api.routes.profile import LOCAL_OWNER_ID
from app.db.session import SessionLocal
from app.models.candidate import CandidateProfile, User
from app.models.workflow import Job, JobMatch, JobSkill, JobSource

JOBS = [
    {
        "external_job_id": "seed-gojek-frontend",
        "title": "Senior Frontend Engineer",
        "company": "Gojek",
        "original_url": "https://gojek.com/careers",
        "location": "Jakarta",
        "work_arrangement": "hybrid",
        "employment_type": "full_time",
        "salary_text": "IDR 35–50 juta",
        "salary_min": 35000000,
        "salary_max": 50000000,
        "salary_currency": "IDR",
        "status": "saved",
        "skills": ["React", "TypeScript", "Design System"],
        "match_score": 94,
    },
    {
        "external_job_id": "seed-ajaib-product-designer",
        "title": "Product Designer, Growth",
        "company": "Ajaib",
        "original_url": "https://ajaib.co.id/careers",
        "location": "Jakarta",
        "work_arrangement": "remote",
        "employment_type": "full_time",
        "salary_text": "IDR 28–42 juta",
        "salary_min": 28000000,
        "salary_max": 42000000,
        "salary_currency": "IDR",
        "status": "saved",
        "skills": ["Figma", "Research", "Product Strategy"],
        "match_score": 91,
    },
    {
        "external_job_id": "seed-kredivo-ux-researcher",
        "title": "UX Researcher",
        "company": "Kredivo",
        "original_url": "https://kredivo.com/careers",
        "location": "Jakarta",
        "work_arrangement": "hybrid",
        "employment_type": "full_time",
        "salary_text": "IDR 20–30 juta",
        "salary_min": 20000000,
        "salary_max": 30000000,
        "salary_currency": "IDR",
        "status": "archived",
        "skills": ["Interview", "Usability", "Insights"],
        "match_score": 49,
    },
]


def main() -> None:
    with SessionLocal() as db:
        if db.get(User, LOCAL_OWNER_ID) is None:
            db.add(User(id=LOCAL_OWNER_ID))
            db.flush()
        profile = db.scalar(select(CandidateProfile).where(CandidateProfile.owner_id == LOCAL_OWNER_ID))
        if profile is None:
            raise RuntimeError("Candidate profile must be seeded before jobs.")
        sources = {}
        for source_name, source_type, base_url in (
            ("Seeded LinkedIn", "job_board", "https://www.linkedin.com"),
            ("Seeded Glints", "job_board", "https://glints.com"),
            ("Seeded Manual", "manual", None),
        ):
            source = db.scalar(select(JobSource).where(JobSource.owner_id == LOCAL_OWNER_ID, JobSource.name == source_name))
            if source is None:
                source = JobSource(owner_id=LOCAL_OWNER_ID, name=source_name, source_type=source_type, base_url=base_url)
                db.add(source)
                db.flush()
            sources[source_name] = source
        for data in JOBS:
            job = db.scalar(select(Job).where(Job.owner_id == LOCAL_OWNER_ID, Job.external_job_id == data["external_job_id"]))
            source = sources["Seeded Manual"] if data["status"] == "archived" else sources["Seeded LinkedIn"]
            if job is None:
                job = Job(
                    owner_id=LOCAL_OWNER_ID,
                    source_id=source.id,
                    discovered_at=datetime.now(UTC),
                    **{key: value for key, value in data.items() if key not in {"skills", "match_score"}},
                )
                db.add(job)
                db.flush()
            for skill_name in data["skills"]:
                if db.scalar(select(JobSkill).where(JobSkill.job_id == job.id, JobSkill.name == skill_name)) is None:
                    db.add(JobSkill(job_id=job.id, name=skill_name, is_required=True))
            if db.scalar(select(JobMatch).where(JobMatch.job_id == job.id, JobMatch.owner_id == LOCAL_OWNER_ID)) is None:
                db.add(JobMatch(
                    owner_id=LOCAL_OWNER_ID, job_id=job.id, profile_id=profile.id,
                    profile_version=1, score=data["match_score"], matching_skills=data["skills"],
                    missing_requirements=[], blockers=[], evidence=[], algorithm_version="seed-v1",
                    calculated_at=datetime.now(UTC),
                ))
        db.commit()


if __name__ == "__main__":
    main()
