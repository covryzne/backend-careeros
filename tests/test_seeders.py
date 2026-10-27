import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app.core.security import LOCAL_OWNER_ID, ActorContext
from app.models.candidate import CandidateFact, CandidateProfile, CandidateSkill
from app.models.foundation import CandidateDocument
from app.models.workflow import Application, Job, JobMatch, JobSkill
from app.schemas.profile import ProfileUpdate
from app.services import profile
from app.services.career import career_context
from scripts.seed_jobs import ALGORITHM, JOBS
from scripts.seed_jobs import seed as seed_jobs
from scripts.seed_real_profile import EXPERIENCES, PROFILE, PROJECTS, SKILLS
from scripts.seed_real_profile import seed as seed_profile


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_fifteen_complete_jobs_without_profile_and_safe_replay(jobs_client):
    _, db = jobs_client
    result = seed_jobs(db)
    db.commit()
    assert result["created"] == result["seed_jobs"] == 15 and result["profile_version"] is None
    assert count(db, Job) == 15 and count(db, CandidateProfile) == count(db, JobMatch) == 0
    ids = set(db.scalars(select(Job.id)))
    for item in db.scalars(select(Job)):
        for key in (
            "title",
            "company",
            "external_job_id",
            "source_id",
            "original_url",
            "canonical_url",
            "description",
            "requirements",
            "responsibilities",
            "experience_requirements",
            "preferred_qualifications",
            "hr_contact_name",
            "hr_contact_email",
            "hr_contact_phone",
            "location",
            "work_arrangement",
            "employment_type",
            "salary_text",
            "salary_min",
            "salary_max",
            "salary_currency",
            "posting_date",
            "application_deadline",
            "discovered_at",
            "notes",
        ):
            assert getattr(item, key), key
        assert "example.com" in item.original_url and item.hr_contact_email.endswith("@example.com")
        assert "DEMO" in item.description
        assert item.salary_min <= item.salary_max and item.posting_date < item.application_deadline
    assert [
        db.scalar(select(func.count()).select_from(Job).where(Job.status == status))
        for status in ("new", "saved", "archived")
    ] == [6, 6, 3]
    before_skills = count(db, JobSkill)
    assert seed_jobs(db)["created"] == 0
    db.commit()
    assert set(db.scalars(select(Job.id))) == ids and count(db, JobSkill) == before_skills


def test_profile_replay_has_no_duplicates_facts_or_fake_cv(jobs_client):
    _, db = jobs_client
    first = seed_profile(db)
    db.commit()
    assert first["totals"] == {
        "skills": len(SKILLS),
        "experiences": len(EXPERIENCES),
        "projects": len(PROJECTS),
    }
    second = seed_profile(db)
    db.commit()
    assert second["profile_version"] == first["profile_version"]
    assert all(value == 0 for value in second["created"].values())
    assert count(db, CandidateFact) == count(db, CandidateDocument) == 0
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    assert "AI-Based Digital-Space Monitoring System" in str(context)
    assert all(
        private not in str(context) for private in ("shendyteuku2@gmail.com", "+62 895-1485-1100")
    )


def test_existing_profile_fields_and_unrelated_records_preserved(jobs_client):
    client, db = jobs_client
    owner = profile.update_profile(
        db,
        ActorContext(LOCAL_OWNER_ID),
        ProfileUpdate(
            full_name="Edited name", headline="My own headline", preferred_titles=["Custom Role"]
        ),
    )
    db.add(CandidateSkill(profile_id=owner.id, name="Custom Skill"))
    db.commit()
    seed_profile(db)
    db.commit()
    result = client.get("/api/v1/profile").json()
    assert result["full_name"] == "Edited name" and result["headline"] == "My own headline"
    assert any(item["value"] == "Custom Role" for item in result["preferences"])
    assert any(item["name"] == "Custom Skill" for item in result["skills"])
    assert result["professional_summary"]


def test_restore_seeded_jobs_preserves_id_application_and_active_status_changes(jobs_client):
    client, db = jobs_client
    seed_profile(db)
    seed_jobs(db)
    db.commit()
    deleted = db.scalar(select(Job).where(Job.external_job_id == JOBS[0]["external_job_id"]))
    edited = db.scalar(select(Job).where(Job.external_job_id == JOBS[1]["external_job_id"]))
    application = client.post(
        "/api/v1/applications",
        json={
            "job_id": str(deleted.id),
            "status": "applied",
            "application_date": "2026-10-28",
            "application_channel": "manual",
        },
    ).json()
    deleted.deleted_at = datetime.now(UTC)
    deleted.requirements = None
    edited.status = "new"
    edited.description = "User edited this description"
    db.commit()
    result = seed_jobs(db)
    db.commit()
    assert result["created"] == 0 and result["restored"] == 1
    assert deleted.deleted_at is None and deleted.requirements
    assert edited.status == "new" and edited.description == "User edited this description"
    assert count(db, Job) == 15
    stored = db.get(Application, uuid.UUID(application["id"]))
    assert stored.job_id == deleted.id and stored.status == "applied"


def test_demo_matches_use_current_profile_version_and_professional_sources(jobs_client):
    _, db = jobs_client
    seed_profile(db)
    first = seed_jobs(db)
    db.commit()
    assert first["matches_created"] == 15
    assert seed_jobs(db)["matches_created"] == 0
    old = profile.load_profile(db, ActorContext(LOCAL_OWNER_ID))
    for match in db.scalars(select(JobMatch)):
        assert match.profile_version == old.profile_version and match.algorithm_version == ALGORITHM
        assert 0 <= match.score <= 100
        assert all("source_id" in source for source in match.evidence)
    new_version = profile.update_profile(
        db,
        ActorContext(LOCAL_OWNER_ID),
        ProfileUpdate(headline="Updated headline"),
        expected=old.profile_version,
    ).profile_version
    assert seed_jobs(db)["matches_created"] == 15
    db.commit()
    assert count(db, JobMatch) == 30
    assert (
        db.scalar(
            select(func.count())
            .select_from(JobMatch)
            .where(JobMatch.profile_version == new_version)
        )
        == 15
    )


def test_explicit_profile_overwrite_restores_nullable_child_fields(jobs_client):
    _, db = jobs_client
    seed_profile(db)
    owner = profile.load_profile(db, ActorContext(LOCAL_OWNER_ID))
    owner.headline = "Edited headline"
    reviewer = next(item for item in owner.experiences if item.company == "Dicoding Indonesia")
    reviewer.is_current = False
    reviewer.end_date = date(2026, 1, 1)
    db.commit()
    changed = seed_profile(db, overwrite=True)
    db.commit()
    owner = profile.load_profile(db, ActorContext(LOCAL_OWNER_ID))
    reviewer = next(item for item in owner.experiences if item.company == "Dicoding Indonesia")
    assert owner.headline == PROFILE["headline"]
    assert reviewer.is_current and reviewer.end_date is None
    assert seed_profile(db, overwrite=True)["profile_version"] == changed["profile_version"]
