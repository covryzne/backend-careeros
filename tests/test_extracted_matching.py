import uuid

import pytest
from sqlalchemy import select

from app.models.workflow import Job, JobMatch, JobSkill
from app.services.matching import ALGORITHM

pytestmark = pytest.mark.usefixtures("enabled_job_matching")


def setup(client):
    assert client.patch("/api/v1/profile", json={"full_name": "Candidate"}).status_code == 200
    for skill in ["Golang", "Postgres", "Python"]:
        assert client.post("/api/v1/profile/skills", json={"name": skill}).status_code == 201


def test_existing_job_extracts_on_read_matches_aliases_and_preserves_source(jobs_client):
    client, db = jobs_client
    setup(client)
    description = "Requirements: Go, PostgreSQL, Docker and Python."
    created = client.post("/api/v1/jobs", json={
        "title": "Software Engineer", "company": "Example", "description": description
    })
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["match_score"] == 75
    assert set(result["matching_skills"]) == {"Go", "PostgreSQL", "Python"}
    assert result["missing_requirements"] == ["Docker"]
    assert result["match_summary"] == "3 dari 4 skill lowongan ditemukan pada profil"
    assert set(result["skills"]) == {"Go", "PostgreSQL", "Python", "Docker"}
    assert all(item["excerpt"] in description for item in result["skill_evidence"])
    assert db.get(Job, uuid.UUID(job_id)).description == description
    assert db.scalar(select(JobSkill).where(JobSkill.job_id == uuid.UUID(job_id))) is None
    cached = db.scalar(select(JobMatch).where(JobMatch.job_id == uuid.UUID(job_id)))
    assert cached.algorithm_version == ALGORITHM
    assert any(item.get("skill") == "Docker" for item in cached.evidence)
    assert client.get("/api/v1/jobs?min_match_score=70").json()["items"][0]["id"] == job_id
    assert client.get("/api/v1/jobs?min_match_score=80").json()["total"] == 0


def test_content_changes_invalidate_extraction_and_no_skills_stays_unknown(jobs_client):
    client, db = jobs_client
    setup(client)
    target = client.post("/api/v1/jobs", json={
        "title": "Engineer", "company": "Example", "requirements": "Python"
    }).json()
    job_id = target["id"]
    assert target["match_score"] == 100
    row = db.get(Job, uuid.UUID(job_id))
    row.requirements = "Docker"
    db.commit()
    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["match_score"] == 0 and result["missing_requirements"] == ["Docker"]
    row.requirements = "No technical skills listed."
    db.commit()
    result = client.get(f"/api/v1/jobs/{job_id}").json()
    assert result["match_score"] is None and result["skills"] == []
    assert result["skill_evidence"] == []


def test_skills_are_visible_before_profile_is_available(jobs_client):
    client, _ = jobs_client
    result = client.post("/api/v1/jobs", json={
        "title": "Backend Engineer", "company": "Example", "description": "Python and FastAPI"
    }).json()
    assert result["match_score"] is None
    assert result["skills"] == ["FastAPI", "Python"]
