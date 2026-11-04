from sqlalchemy import select

from app.core.config import settings
from app.models.workflow import JobMatch


def test_matching_disabled_preserves_cache_and_suppresses_scores(jobs_client, monkeypatch):
    client, db = jobs_client
    monkeypatch.setattr(settings, "job_matching_enabled", True)
    client.patch("/api/v1/profile", json={"full_name": "Candidate"})
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    created = client.post("/api/v1/jobs", json={
        "title": "Engineer", "company": "Example", "skills": ["Python"],
        "description": "Python and Docker",
    }).json()
    assert created["match_score"] == 100
    before = [(row.id, row.score, row.calculated_at) for row in db.scalars(select(JobMatch))]
    monkeypatch.setattr(settings, "job_matching_enabled", False)

    def unexpected(*args, **kwargs):
        raise AssertionError("Automatic matching and skill extraction must remain inactive")

    monkeypatch.setattr("app.services.matching.load_profile", unexpected)
    monkeypatch.setattr("app.services.jobs.effective_skills", unexpected)
    for result in [
        client.get("/api/v1/jobs").json()["items"][0],
        client.get("/api/v1/jobs/" + created["id"]).json(),
    ]:
        assert result["matching_status"] == "coming_soon"
        assert result["match_score"] is None
        assert result["match_summary"] is None
        assert result["matching_skills"] == result["missing_requirements"] == []
        assert result["skills"] == ["Python"]
    assert client.get("/api/v1/jobs?sort=match_score").json()["total"] == 1
    assert client.get("/api/v1/jobs?min_match_score=1").json()["total"] == 0
    assert client.get("/api/v1/dashboard").json()["high_priority_jobs"] == []
    assert [(row.id, row.score, row.calculated_at) for row in db.scalars(select(JobMatch))] == before


def test_disabled_is_default_and_new_jobs_do_not_create_matches(jobs_client):
    client, db = jobs_client
    assert settings.job_matching_enabled is False
    client.patch("/api/v1/profile", json={"full_name": "Candidate"})
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    result = client.post("/api/v1/jobs", json={
        "title": "Engineer", "company": "Example", "description": "Python and Docker"
    }).json()
    assert result["matching_status"] == "coming_soon" and result["match_score"] is None
    assert result["skills"] == []
    assert db.scalar(select(JobMatch)) is None
