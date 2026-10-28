import uuid

from sqlalchemy import func, select

from app.core.security import LOCAL_OWNER_ID, ActorContext, get_actor
from app.main import app
from app.models.workflow import JobMatch, JobSkill
from app.services.matching import ALGORITHM


def profile(client):
    response = client.patch(
        "/api/v1/profile",
        json={"full_name": "Candidate", "email": "private@example.com", "phone": "081111222333"},
    )
    assert response.status_code == 200
    return response.json()


def job(client, skills, **values):
    response = client.post(
        "/api/v1/jobs", json={"title": "Engineer", "company": "Example", "skills": skills, **values}
    )
    assert response.status_code == 201, response.text
    return response.json()


def rows(db):
    return db.scalar(
        select(func.count()).select_from(JobMatch).where(JobMatch.algorithm_version == ALGORITHM)
    )


def test_profile_changes_automatically_refresh_preserving_history_and_idempotency(jobs_client):
    client, db = jobs_client
    profile(client)
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    target = job(client, ["Python", "Docker"])
    initial = client.get("/api/v1/jobs/" + target["id"]).json()
    assert initial["match_score"] == 50 and not initial["is_match_stale"]
    assert initial["match_summary"] == "1 dari 2 skill lowongan ditemukan pada profil"
    assert initial["matching_skills"] == ["Python"] and initial["missing_requirements"] == [
        "Docker"
    ]
    assert rows(db) == 1
    version = client.get("/api/v1/profile").json()["profile_version"]
    client.post("/api/v1/profile/skills", json={"name": "Docker"})
    current = client.get("/api/v1/jobs/" + target["id"]).json()
    assert current["match_score"] == 100 and current["match_profile_version"] == version + 1
    assert not current["is_match_stale"] and rows(db) == 2
    for _ in range(3):
        assert client.get("/api/v1/jobs/" + target["id"]).json()["match_score"] == 100
    assert rows(db) == 2
    assert db.scalar(select(JobMatch.score).where(JobMatch.profile_version == version)) == 50


def test_project_experience_and_summary_are_direct_professional_sources(jobs_client):
    client, _ = jobs_client
    profile(client)
    client.post(
        "/api/v1/profile/projects",
        json={"name": "Job Tracker", "role": "Developer", "description": "Built FastAPI services"},
    )
    client.post(
        "/api/v1/profile/experiences",
        json={"role": "Engineer", "company": "Example", "description": "Built Python pipelines"},
    )
    target = job(client, ["FastAPI", "Python", "Docker"])
    result = client.get("/api/v1/jobs/" + target["id"]).json()
    assert result["match_score"] == 66.67 and result["missing_requirements"] == ["Docker"]
    client.patch("/api/v1/profile", json={"professional_summary": "Deploy using Docker"})
    assert client.get("/api/v1/jobs/" + target["id"]).json()["match_score"] == 100


def test_job_skill_changes_refresh_the_current_cache_without_duplicates(jobs_client):
    client, db = jobs_client
    profile(client)
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    target = job(client, ["Python", "Docker"])
    assert client.get("/api/v1/jobs/" + target["id"]).json()["match_score"] == 50
    db.add(JobSkill(job_id=uuid.UUID(target["id"]), name="PostgreSQL", is_required=True))
    db.commit()
    result = client.get("/api/v1/jobs/" + target["id"]).json()
    assert result["match_score"] == 33.33 and rows(db) == 1
    assert result["match_summary"] == "1 dari 3 skill lowongan ditemukan pada profil"


def test_current_scores_precede_filters_pagination_home_and_application_summaries(jobs_client):
    client, _ = jobs_client
    profile(client)
    skill = client.post("/api/v1/profile/skills", json={"name": "Python"}).json()
    python = job(client, ["Python"], title="Python Engineer")
    docker = job(client, ["Docker"], title="Docker Engineer")
    first = client.get("/api/v1/jobs?sort=match_score&page_size=1").json()
    assert first["items"][0]["id"] == python["id"]
    client.patch("/api/v1/profile/skills/" + skill["id"], json={"name": "Docker"})
    filtered = client.get("/api/v1/jobs?min_match_score=80&page_size=1").json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == docker["id"]
    assert (
        client.get("/api/v1/jobs?sort=match_score&page_size=1").json()["items"][0]["id"]
        == docker["id"]
    )
    assert [
        item["id"] for item in client.get("/api/v1/dashboard").json()["high_priority_jobs"]
    ] == [docker["id"]]
    created = client.post(
        "/api/v1/applications",
        json={
            "job_id": python["id"],
            "status": "applied",
            "application_date": "2026-10-28",
            "application_channel": "manual",
        },
    ).json()
    assert created["job"]["match_score"] == 0
    assert client.get("/api/v1/applications/" + created["id"]).json()["job"]["match_score"] == 0


def test_unknown_profile_or_job_skills_remain_unknown_and_owner_isolation(jobs_client):
    client, db = jobs_client
    no_profile = job(client, ["Python"])
    assert client.get("/api/v1/jobs/" + no_profile["id"]).json()["match_score"] is None
    profile(client)
    assert client.get("/api/v1/jobs/" + no_profile["id"]).json()["match_score"] is None
    client.post("/api/v1/profile/skills", json={"name": "Docker"})
    assert client.get("/api/v1/jobs/" + no_profile["id"]).json()["match_score"] == 0
    assert job(client, [])["match_score"] is None
    other = ActorContext(uuid.uuid4())
    app.dependency_overrides[get_actor] = lambda: other
    try:
        profile(client)
        client.post("/api/v1/profile/skills", json={"name": "Python"})
        foreign = job(client, ["Python"])
        assert foreign["match_score"] == 100
        assert client.get("/api/v1/jobs/" + no_profile["id"]).status_code == 404
    finally:
        app.dependency_overrides.pop(get_actor)
    assert client.get("/api/v1/jobs/" + no_profile["id"]).json()["match_score"] == 0
    assert (
        db.scalar(
            select(func.count()).select_from(JobMatch).where(JobMatch.owner_id == LOCAL_OWNER_ID)
        )
        == 1
    )


def test_matching_uses_full_professional_text_and_deduplicates_skill_case(jobs_client):
    client, _ = jobs_client
    profile(client)
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    client.post(
        "/api/v1/profile/projects",
        json={
            "name": "Infrastructure",
            "description": ("Deployment notes. " * 150) + "Uses Docker.",
        },
    )
    target = job(client, ["Python", "python", "Docker"])
    assert target["match_score"] == 100
    assert target["match_summary"] == "2 dari 2 skill lowongan ditemukan pada profil"
