import uuid

import pytest

from app.models.workflow import Job


@pytest.mark.parametrize("collection", ["saved", "archived"])
def test_remove_from_personal_collection_preserves_crawled_job_and_application(
    jobs_client, collection
):
    client, db = jobs_client
    created = client.post(
        "/api/v1/jobs",
        json={
            "title": "Backend Engineer",
            "company": "Example",
            "status": collection,
            "description": "Build services",
            "requirements": "Python",
            "responsibilities": "Build APIs",
        },
    )
    assert created.status_code == 201
    identity = created.json()["id"]
    application = client.post(
        "/api/v1/applications",
        json={
            "job_id": identity,
            "status": "applied",
            "application_date": "2026-10-28",
            "application_channel": "career_page",
            "notes": "Keep application history",
        },
    ).json()
    assert client.get("/api/v1/jobs?status=" + collection).json()["total"] == 1

    response = client.patch(
        "/api/v1/jobs/" + identity,
        json={"status": "new"},
        headers={"Idempotency-Key": "remove-personal-job"},
    )
    assert response.status_code == 200 and response.json()["status"] == "new"
    assert client.get("/api/v1/jobs?status=saved").json()["total"] == 0
    assert client.get("/api/v1/jobs?status=archived").json()["total"] == 0
    assert [item["id"] for item in client.get("/api/v1/jobs").json()["items"]] == [identity]
    detail = client.get("/api/v1/jobs/" + identity).json()
    assert detail["description"] == "Build services" and detail["requirements"] == "Python"
    assert detail["responsibilities"] == "Build APIs"
    assert db.get(Job, uuid.UUID(identity)).deleted_at is None
    stored = client.get("/api/v1/applications/" + application["id"]).json()
    assert stored["status"] == application["status"] and stored["version"] == application["version"]
    assert (
        stored["activities"] == application["activities"]
        and stored["notes"] == application["notes"]
    )
    assert client.patch("/api/v1/jobs/" + identity, json={"status": "saved"}).status_code == 200
    assert client.get("/api/v1/jobs?status=saved").json()["items"][0]["id"] == identity
