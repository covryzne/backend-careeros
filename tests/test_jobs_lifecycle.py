import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.profile import LOCAL_OWNER_ID
from app.db.session import get_db
from app.main import app
from app.models.workflow import Job, JobMatch, JobSkill, JobSource


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(UUID, "sqlite")
def compile_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(32)"


@pytest.fixture
def jobs_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for model in (JobSource, Job, JobSkill, JobMatch):
        model.__table__.create(engine)
    with Session(engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield client, db
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def test_save_archive_restore_preserves_crawling_record(jobs_client):
    client, db = jobs_client
    source = JobSource(owner_id=LOCAL_OWNER_ID, name="Crawler", source_type="job_board")
    db.add(source)
    db.flush()
    job = Job(
        owner_id=LOCAL_OWNER_ID, source_id=source.id, title="Engineer", company="Example",
        original_url="https://example.com/jobs/1", discovered_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()
    job_id = str(job.id)
    original = client.get("/api/v1/jobs").json()["items"][0]
    assert original["status"] == "new"
    assert client.get("/api/v1/jobs?status=saved").json()["total"] == 0

    for status in ("saved", "saved", "archived", "archived", "saved"):
        response = client.patch(f"/api/v1/jobs/{job_id}", json={"status": status})
        assert response.status_code == 200
        all_jobs = client.get("/api/v1/jobs").json()
        assert all_jobs["total"] == 1
        current = all_jobs["items"][0]
        assert current["status"] == status
        for field in ("id", "original_url", "source_id", "source_name", "discovered_at"):
            assert current[field] == original[field]
        saved = client.get("/api/v1/jobs?status=saved").json()
        archived = client.get("/api/v1/jobs?status=archived").json()
        assert saved["total"] == int(status == "saved")
        assert archived["total"] == int(status == "archived")

    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert client.get("/api/v1/jobs").json()["total"] == 0
    assert db.get(Job, job.id).deleted_at is not None


def test_unfiltered_pagination_includes_all_statuses_and_excludes_other_owners(jobs_client):
    client, db = jobs_client
    discovered_at = datetime.now(UTC)
    for index in range(105):
        db.add(Job(
            owner_id=LOCAL_OWNER_ID, title=f"Engineer {index}", company="Example",
            discovered_at=discovered_at, status=("new", "saved", "archived")[index % 3],
        ))
    db.add(Job(
        owner_id=uuid.uuid4(), title="Private job", company="Other",
        discovered_at=discovered_at,
    ))
    db.commit()
    first = client.get("/api/v1/jobs?page_size=100").json()
    second = client.get("/api/v1/jobs?page_size=100&page=2").json()
    assert first["total"] == second["total"] == 105
    assert first["total_pages"] == 2
    assert len(first["items"]) == 100
    assert len(second["items"]) == 5
    assert len({job["id"] for job in first["items"] + second["items"]}) == 105


def test_manual_creation_remains_explicitly_saved(jobs_client):
    client, _db = jobs_client
    response = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"})
    assert response.status_code == 201
    assert response.json()["status"] == "saved"
