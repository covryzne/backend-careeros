import copy
import uuid

import pytest
from test_cv_imports import CV_TEXT, make_pdf

from app.core.security import LOCAL_OWNER_ID, ActorContext, get_actor
from app.main import app
from app.models.foundation import CandidateDocument
from app.services.career import career_context, validate_report
from app.services.cv_matching import ready_document


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "document_storage_dir", tmp_path)
    return tmp_path


def upload(client, role="AI Engineer", content=None, **kwargs):
    result = client.post(
        "/api/v1/profile/documents",
        data={"target_role": role, "label": "Ayu Candidate CV"},
        files={"file": ("cv.pdf", content or make_pdf(), "application/pdf")},
        **kwargs,
    )
    assert result.status_code == 201, result.text
    return result.json()


def parse(client, document):
    result = client.post(f"/api/v1/profile/documents/{document['id']}/parse")
    assert result.status_code == 201, result.text
    return result.json()


def review(client, record, **overrides):
    values = copy.deepcopy(record["fields"])
    values.update(overrides)
    return client.post(
        f"/api/v1/profile/cv-imports/{record['id']}/review",
        json={**values, "expected_revision": record["document_revision"]},
    )


def test_plain_upload_precedes_optional_parsing_and_preserves_pdf(
    jobs_client, storage, monkeypatch
):
    client, db = jobs_client

    def forbidden(*_args):
        raise AssertionError("Ordinary upload must not run extraction")

    with monkeypatch.context() as local:
        local.setattr("app.services.cv_imports.parse_pdf", forbidden)
        doc = upload(client)
    assert client.get("/api/v1/profile").status_code == 404
    assert client.get("/api/v1/profile/documents").json()["items"][0]["id"] == doc["id"]
    assert not doc["matching_ready"] and doc["version_number"] == 1
    assert client.get(f"/api/v1/profile/documents/{doc['id']}/preview").content == make_pdf()
    result = parse(client, doc)
    assert result["document_id"] == doc["id"]
    assert client.delete(f"/api/v1/profile/cv-imports/{result['id']}").status_code == 204
    assert client.get(f"/api/v1/profile/documents/{doc['id']}/preview").status_code == 200
    assert len(list(storage.glob("*.pdf"))) == 1
    assert not db.get(CandidateDocument, uuid.UUID(doc["id"])).matching_snapshot


def test_versions_number_by_normalized_role_include_archived_and_retry(jobs_client, storage):
    client, _ = jobs_client
    first = upload(client, " AI  Engineer ", headers={"Idempotency-Key": "cv-upload"})
    assert upload(client, " AI  Engineer ", headers={"Idempotency-Key": "cv-upload"}) == first
    assert upload(client, "Data Analyst")["version_number"] == 1
    assert upload(client, "ai engineer")["version_number"] == 2
    assert client.delete(f"/api/v1/profile/documents/{first['id']}").status_code == 204
    assert upload(client, "AI Engineer")["version_number"] == 3
    assert len(client.get("/api/v1/profile/documents").json()["items"]) == 3


def test_parse_failure_keeps_original_and_can_use_unparsed_pdf_for_apply(jobs_client, storage):
    client, _ = jobs_client
    doc = upload(client, content=b"%PDF-1.4\ninvalid")
    assert client.post(f"/api/v1/profile/documents/{doc['id']}/parse").status_code == 422
    assert client.get(f"/api/v1/profile/documents/{doc['id']}/preview").status_code == 200
    job = client.post("/api/v1/jobs", json={"title": "AI Engineer", "company": "Example"}).json()
    created = client.post(
        "/api/v1/applications",
        json={
            "job_id": job["id"],
            "status": "applied",
            "application_date": "2026-10-28",
            "application_channel": "career_page",
            "selected_document_id": doc["id"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["selected_document"]["id"] == doc["id"]
    assert client.delete(f"/api/v1/profile/documents/{doc['id']}").status_code == 204
    historical = client.get("/api/v1/applications/" + created.json()["id"]).json()
    assert historical["selected_document"]["id"] == doc["id"]
    assert historical["selected_document"]["deleted_at"]
    another = client.post("/api/v1/jobs", json={"title": "Other", "company": "Example"}).json()
    assert (
        client.post(
            "/api/v1/applications",
            json={"job_id": another["id"], "selected_document_id": doc["id"]},
        ).status_code
        == 404
    )


def test_reviewed_role_snapshots_are_isolated_private_and_profile_is_optional(jobs_client, storage):
    client, db = jobs_client
    ai = upload(client)
    actor = ActorContext(LOCAL_OWNER_ID, "agent")
    with pytest.raises(Exception) as error:
        ready_document(db, actor, uuid.UUID(ai["id"]))
    assert error.value.status_code == 422
    record = parse(client, ai)
    result = review(
        client,
        record,
        skills=["PyTorch", "Python"],
        profile={
            **record["fields"]["profile"],
            "headline": "AI Engineer Ayu Candidate ayu@example.com 081234567890 1995-02-03 Jl. Contoh No. 9, Jakarta",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["snapshot_revision"] == 1 and result.json()["matching_ready"]
    assert review(client, record).status_code == 409
    assert client.get("/api/v1/profile").status_code == 404
    client.delete(f"/api/v1/profile/cv-imports/{record['id']}")
    analyst = upload(
        client,
        "Data Analyst",
        content=make_pdf(
            CV_TEXT.replace("Python", "SQL").replace("Career Platform", "Analytics Dashboard")
        ),
    )
    analyst_record = parse(client, analyst)
    assert review(client, analyst_record, skills=["SQL", "Power BI"]).status_code == 200
    ai_context = career_context(db, actor, {"document_id": ai["id"]})
    da_context = career_context(db, actor, {"document_id": analyst["id"]})
    assert "PyTorch" in str(ai_context) and "Power BI" not in str(ai_context)
    assert "Power BI" in str(da_context) and "PyTorch" not in str(da_context)
    assert "Analytics Dashboard" in str(da_context) and "Analytics Dashboard" not in str(ai_context)
    for private in ("Ayu Candidate", "ayu@example.com", "081234567890", "1995-02-03", "Jl. Contoh"):
        assert private not in str(ai_context)
        assert private not in str(db.get(CandidateDocument, uuid.UUID(ai["id"])).matching_snapshot)
    assert ai_context["profile"]["selected_cv"]["id"] == ai["id"]
    assert not ai_context["profile"]["profile_version"]
    report = {
        "kind": "general",
        "title": "Review",
        "summary": "CV",
        "recommendations": [],
        "pipeline_reviews": [],
        "next_steps": [],
        "limitations": [],
    }
    import json

    assert validate_report(json.dumps(report), ai_context)["selected_cv"]["id"] == ai["id"]


def test_review_apply_keeps_exact_cv_and_does_not_duplicate_document(jobs_client, storage):
    client, _ = jobs_client
    doc = upload(client)
    record = parse(client, doc)
    assert review(client, record, skills=["PyTorch"]).status_code == 200
    values = copy.deepcopy(record["fields"])
    values["profile"] = {
        key: value for key, value in values["profile"].items() if value is not None
    }
    result = client.post(
        f"/api/v1/profile/cv-imports/{record['id']}/apply",
        json={**values, "expected_profile_version": None},
    )
    assert result.status_code == 200, result.text
    assert result.json()["profile_version"] == 1
    assert len(result.json()["documents"]) == 1 and result.json()["documents"][0]["id"] == doc["id"]
    assert len(list(storage.glob("*.pdf"))) == 1
    documents = client.get("/api/v1/profile/documents").json()["items"]
    assert documents[0]["target_role"] == "AI Engineer" and documents[0]["snapshot_revision"] == 1


def test_cv_ownership_review_validation_and_preparing_apply(jobs_client, storage):
    client, _ = jobs_client
    doc = upload(client)
    record = parse(client, doc)
    assert review(client, record, experiences=[{"role": "", "company": ""}]).status_code == 422
    other = ActorContext(uuid.uuid4(), "user")
    app.dependency_overrides[get_actor] = lambda: other
    try:
        assert client.get("/api/v1/profile/documents").json()["items"] == []
        assert client.post(f"/api/v1/profile/documents/{doc['id']}/parse").status_code == 404
        assert review(client, record).status_code == 404
    finally:
        app.dependency_overrides.pop(get_actor)
    job = client.post("/api/v1/jobs", json={"title": "AI Engineer", "company": "Example"}).json()
    preparing = client.post("/api/v1/applications", json={"job_id": job["id"]}).json()
    result = client.patch(
        "/api/v1/applications/" + preparing["id"],
        json={
            "status": "applied",
            "expected_version": preparing["version"],
            "application_date": "2026-10-28",
            "application_channel": "career_page",
            "selected_document_id": doc["id"],
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["selected_document"]["id"] == doc["id"]


def test_cv_scope_requires_review_and_detects_revision_changes(jobs_client, storage, monkeypatch):
    from app.core.config import settings
    from app.schemas.agent import RunCreate
    from app.services.agent_runs import create_run

    monkeypatch.setattr(settings, "agent_enabled", True)
    client, db = jobs_client
    doc = upload(client)
    payload = {
        "task_type": "career_assistant",
        "instruction": "Review CV",
        "scope": {"document_id": doc["id"]},
    }
    assert client.post("/api/v1/agent-runs", json=payload).status_code == 422
    record = parse(client, doc)
    assert review(client, record).status_code == 200
    actor = ActorContext(LOCAL_OWNER_ID)
    run = create_run(db, actor, RunCreate.model_validate(payload))
    assert run.scope["_document_revision"] == 1
    newer = parse(client, doc)
    assert newer["document_revision"] == 1
    assert review(client, newer, skills=["New skill"]).status_code == 200
    with pytest.raises(Exception) as error:
        career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), run.scope)
    assert error.value.status_code == 409
    other = ActorContext(uuid.uuid4(), "user")
    app.dependency_overrides[get_actor] = lambda: other
    try:
        assert client.post("/api/v1/agent-runs", json=payload).status_code == 404
    finally:
        app.dependency_overrides.pop(get_actor)


def test_legacy_import_with_contact_only_does_not_require_recommendation_data(jobs_client, storage):
    client, _ = jobs_client
    record = client.post(
        "/api/v1/profile/cv-imports", files={"file": ("cv.pdf", make_pdf(), "application/pdf")}
    ).json()
    result = client.post(
        f"/api/v1/profile/cv-imports/{record['id']}/apply",
        json={
            "profile": {"full_name": "Candidate"},
            "skills": [],
            "experiences": [],
            "projects": [],
            "expected_profile_version": None,
        },
    )
    assert result.status_code == 200, result.text
    assert not client.get("/api/v1/profile/documents").json()["items"][0]["matching_ready"]
