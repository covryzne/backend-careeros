import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.api.routes.profile import LOCAL_OWNER_ID
from app.models.workflow import Application, ApplicationActivity, AuditEvent, Job


def create_manual_application(client):
    job = client.post("/api/v1/jobs", json={
        "title": "Engineer", "company": "Example", "original_url": "https://example.com/jobs/1",
    }).json()
    response = client.post("/api/v1/applications", json={
        "job_id": job["id"], "status": "applied", "application_date": "2026-10-26",
        "application_channel": "career_page", "notes": "Applied on website",
        "follow_up_date": "2026-11-02",
    })
    assert response.status_code == 201, response.text
    return job, response.json()


def test_manual_apply_persists_pipeline_and_preserves_saved_job(jobs_client):
    client, db = jobs_client
    job, application = create_manual_application(client)
    assert application["job"]["id"] == job["id"]
    assert application["status"] == "applied"
    assert application["application_channel"] == "career_page"
    assert application["follow_up_date"] == "2026-11-02"
    assert application["activities"][0]["activity_type"] == "application_submitted"
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "application")) == 1
    db.expire_all()
    records = client.get("/api/v1/applications").json()
    assert records["total"] == 1
    assert records["items"][0]["id"] == application["id"]
    assert client.get("/api/v1/jobs?status=saved").json()["items"][0]["id"] == job["id"]
    assert client.get(f'/api/v1/applications/{application["id"]}').status_code == 200
    duplicate = client.post("/api/v1/applications", json={
        "job_id": job["id"], "status": "applied", "application_date": "2026-10-26",
        "application_channel": "manual",
    })
    assert duplicate.status_code == 409
    assert db.scalar(select(func.count()).select_from(Application)) == 1
    assert db.scalar(select(func.count()).select_from(ApplicationActivity)) == 1


@pytest.mark.parametrize("missing", ["application_date", "application_channel"])
def test_apply_requires_confirmation_fields(jobs_client, missing):
    client, db = jobs_client
    payload = {"job_id": str(uuid.uuid4()), "status": "applied",
               "application_date": "2026-10-26", "application_channel": "manual"}
    payload.pop(missing)
    assert client.post("/api/v1/applications", json=payload).status_code == 422
    assert db.scalar(select(func.count()).select_from(Application)) == 0


def test_pipeline_stage_update_is_versioned_and_audited(jobs_client):
    client, db = jobs_client
    _, application = create_manual_application(client)
    path = f'/api/v1/applications/{application["id"]}'
    updated = client.patch(path, json={"status": "assessment", "expected_version": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["activities"][-1]["metadata_json"] == {
        "from": "applied", "to": "assessment", "change_kind": "move", "version": 2,
    }
    assert client.get(path).json()["status"] == "assessment"
    assert client.patch(path, json={"status": "interview", "expected_version": 1}).status_code == 409
    assert client.patch(path, json={"status": "preparing", "expected_version": 2}).status_code == 409
    assert client.patch(path, json={"status": "offer"}).status_code == 422
    assert client.patch(path, json={"status": None, "expected_version": 2}).status_code == 422
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "application")) == 2
    for version, stage in [(2, "interview"), (3, "offer"), (4, "withdrawn")]:
        assert client.patch(path, json={"status": stage, "expected_version": version}).status_code == 200
    assert client.get("/api/v1/applications?status=withdrawn").json()["total"] == 1
    assert client.get("/api/v1/applications?status=applied").json()["total"] == 0


STAGES = ["applied", "assessment", "interview", "offer", "rejected", "withdrawn"]


@pytest.mark.parametrize("source,target", [
    (source, target) for source in STAGES for target in STAGES if source != target
])
def test_all_board_stages_can_move_forward_backward_or_skip(jobs_client, source, target):
    client, _db = jobs_client
    _, application = create_manual_application(client)
    path = f'/api/v1/applications/{application["id"]}'
    version = 1
    if source != "applied":
        response = client.patch(path, json={"status": source, "expected_version": version})
        assert response.status_code == 200
        version = response.json()["version"]
    response = client.patch(path, json={"status": target, "expected_version": version})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == target
    activities = response.json()["activities"]
    assert sum(item["activity_type"] == "application_submitted" for item in activities) == 1
    assert activities[-1]["metadata_json"]["from"] == source
    assert activities[-1]["metadata_json"]["to"] == target
    assert client.get(path).json()["status"] == target


def test_undo_restores_previous_stage_once_without_resubmitting(jobs_client):
    client, db = jobs_client
    _, application = create_manual_application(client)
    path = f'/api/v1/applications/{application["id"]}'
    assert client.patch(path, json={"status": "assessment", "expected_version": 1}).status_code == 200
    undo = {"status": "applied", "expected_version": 2, "change_kind": "undo"}
    response = client.patch(path, json=undo)
    assert response.status_code == 200, response.text
    restored = response.json()
    assert restored["status"] == "applied"
    assert restored["version"] == 3
    assert restored["application_date"] == application["application_date"]
    assert restored["activities"][-1]["metadata_json"] == {
        "from": "assessment", "to": "applied", "change_kind": "undo", "version": 3,
    }
    assert sum(item["activity_type"] == "application_submitted" for item in restored["activities"]) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "application")) == 3
    assert client.patch(path, json=undo).status_code == 409
    assert client.patch(path, json={"status": "assessment", "expected_version": 3,
                                    "change_kind": "undo"}).status_code == 409
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "application")) == 3


def test_undo_checks_target_latest_move_and_concurrent_edits(jobs_client):
    client, _db = jobs_client
    _, application = create_manual_application(client)
    path = f'/api/v1/applications/{application["id"]}'
    assert client.patch(path, json={"status": "assessment", "expected_version": 1}).status_code == 200
    assert client.patch(path, json={"status": "offer", "expected_version": 2,
                                    "change_kind": "undo"}).status_code == 409
    assert client.patch(path, json={"status": "interview", "expected_version": 2}).status_code == 200
    assert client.patch(path, json={"status": "applied", "expected_version": 2,
                                    "change_kind": "undo"}).status_code == 409
    assert client.patch(path, json={"status": "applied", "expected_version": 3,
                                    "change_kind": "undo"}).status_code == 409
    assert client.patch(path, json={"notes": "Changed in another tab", "expected_version": 3}).status_code == 200
    assert client.patch(path, json={"status": "assessment", "expected_version": 3,
                                    "change_kind": "undo"}).status_code == 409
    assert client.patch(path, json={"status": "assessment", "expected_version": 4,
                                    "change_kind": "undo"}).status_code == 409
    assert client.get(path).json()["status"] == "interview"


def test_undo_cannot_cancel_creation_or_combine_other_edits(jobs_client):
    client, _db = jobs_client
    _, application = create_manual_application(client)
    path = f'/api/v1/applications/{application["id"]}'
    assert client.patch(path, json={"status": "assessment", "expected_version": 1,
                                    "change_kind": "undo"}).status_code == 409
    assert client.patch(path, json={"status": "assessment", "expected_version": 1}).status_code == 200
    assert client.patch(path, json={"status": "applied", "expected_version": 2,
                                    "change_kind": "undo", "notes": "Extra edit"}).status_code == 422
    assert client.patch(path, json={"expected_version": 2, "change_kind": "undo"}).status_code == 422
    assert client.patch(path, json={"status": "applied", "expected_version": 2,
                                    "change_kind": "invalid"}).status_code == 422
    assert client.get(path).json()["status"] == "assessment"


def test_same_stage_does_not_consume_version_or_create_undo_event(jobs_client):
    client, db = jobs_client
    _, application = create_manual_application(client)
    response = client.patch(f'/api/v1/applications/{application["id"]}', json={
        "status": "applied", "expected_version": 1,
    })
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert len(response.json()["activities"]) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "application")) == 1


def test_applications_respect_owner_and_deleted_job(jobs_client):
    client, db = jobs_client
    foreign_job = Job(owner_id=uuid.uuid4(), title="Private", company="Other",
                      discovered_at=datetime.now(UTC))
    db.add(foreign_job)
    db.flush()
    foreign_application = Application(owner_id=foreign_job.owner_id, job_id=foreign_job.id)
    db.add(foreign_application)
    db.commit()
    foreign_id = str(foreign_application.id)
    assert client.get("/api/v1/applications").json()["total"] == 0
    assert client.get(f"/api/v1/applications/{foreign_id}").status_code == 404
    assert client.patch(f"/api/v1/applications/{foreign_id}", json={
        "status": "withdrawn", "expected_version": 1,
    }).status_code == 404
    assert client.post("/api/v1/applications", json={"job_id": str(foreign_job.id)}).status_code == 404
    job, _ = create_manual_application(client)
    assert client.delete(f'/api/v1/jobs/{job["id"]}').status_code == 204
    assert client.get("/api/v1/applications").json()["total"] == 0


def test_applications_pagination_and_search(jobs_client):
    client, db = jobs_client
    for index in range(105):
        job = Job(owner_id=LOCAL_OWNER_ID, title=f"Engineer {index}", company="Example",
                  discovered_at=datetime.now(UTC))
        db.add(job)
        db.flush()
        db.add(Application(owner_id=LOCAL_OWNER_ID, job_id=job.id, status="applied"))
    db.commit()
    first = client.get("/api/v1/applications?page_size=100").json()
    second = client.get("/api/v1/applications?page_size=100&page=2").json()
    assert first["total"] == 105
    assert first["total_pages"] == 2
    assert len(first["items"]) == 100
    assert len(second["items"]) == 5
    assert len({item["id"] for item in first["items"] + second["items"]}) == 105
    assert client.get("/api/v1/applications?q=Engineer%20104").json()["total"] == 1
