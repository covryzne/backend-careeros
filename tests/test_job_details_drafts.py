import uuid

import pytest
from sqlalchemy import select

from app.core.security import ActorContext, get_actor
from app.main import app
from app.models.foundation import Draft, DraftVersion
from app.services.job_content import details


def job(client, **values):
    result = client.post(
        "/api/v1/jobs", json={"title": "Backend Engineer", "company": "Example", **values}
    )
    assert result.status_code == 201, result.text
    return result.json()


def professional(client):
    client.patch(
        "/api/v1/profile",
        json={
            "full_name": "Private Candidate",
            "email": "owner@example.com",
            "phone": "081111222333",
            "date_of_birth": "1991-02-03",
            "location": "Jl. Private No. 4",
            "professional_summary": "Build reliable Python services.",
        },
    )
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    client.post(
        "/api/v1/profile/projects",
        json={
            "name": "API Platform",
            "role": "Backend Developer",
            "description": "Built FastAPI services with Python and PostgreSQL.",
        },
    )
    return client.get("/api/v1/profile").json()


def create(client, target, owner, draft_type="cover_letter", **overrides):
    return client.post(
        "/api/v1/drafts",
        json={
            "job_id": target["id"],
            "source_profile_version": owner["profile_version"],
            "draft_type": draft_type,
            **overrides,
        },
    )


def test_job_details_structured_fields_and_partial_updates(jobs_client):
    client, _ = jobs_client
    item = job(
        client,
        description="About the backend team",
        requirements="Python and SQL",
        responsibilities="Build APIs",
        experience_requirements="Two years",
        preferred_qualifications="Docker",
        hr_contact_name="Recruitment Team",
        hr_contact_email="hr@example.com",
        hr_contact_phone="+62 812-3456-7890",
    )
    detail = client.get("/api/v1/jobs/" + item["id"]).json()
    assert detail["requirements"] == "Python and SQL" and detail["responsibilities"] == "Build APIs"
    assert (
        detail["experience_requirements"] == "Two years"
        and detail["preferred_qualifications"] == "Docker"
    )
    assert (
        detail["hr_contact_email"] == "hr@example.com"
        and detail["hr_contact_phone"] == "+62 812-3456-7890"
    )
    assert (
        client.patch("/api/v1/jobs/" + item["id"], json={"status": "archived"}).status_code == 200
    )
    assert client.get("/api/v1/jobs/" + item["id"]).json()["responsibilities"] == "Build APIs"
    assert (
        client.patch(
            "/api/v1/jobs/" + item["id"], json={"hr_contact_email": "bad\r\nBcc:other@example.com"}
        ).status_code
        == 422
    )
    assert (
        client.patch("/api/v1/jobs/" + item["id"], json={"requirements": None}).status_code == 200
    )
    assert client.get("/api/v1/jobs/" + item["id"]).json()["requirements"] is None


@pytest.mark.parametrize(
    "description",
    [
        "About the role\nBuild internal tools.\nResponsibilities:\nBuild Python APIs\nRequirements:\nPython and PostgreSQL\nPreferred qualifications:\nDocker\nContact HR:\nhr@example.com\n081234567890",
        "<p>About the role</p><p>Build internal tools.</p><h3>Responsibilities:</h3><ul><li>Build Python APIs</li></ul><h3>Requirements:</h3><p>Python and PostgreSQL</p><h3>Preferred qualifications:</h3><p>Docker</p><h3>Contact HR:</h3><p>hr@example.com</p><p>081234567890</p><script>steal()</script>",
    ],
)
def test_legacy_sections_contacts_and_safe_text(description):
    result = details({"description": description})
    assert result["responsibilities"] == "Build Python APIs"
    assert result["requirements"] == "Python and PostgreSQL"
    assert result["preferred_qualifications"] == "Docker"
    assert (
        result["hr_contact_email"] == "hr@example.com"
        and result["hr_contact_phone"] == "081234567890"
    )
    assert "steal()" not in result["description"] and "<script>" not in result["description"]
    assert (
        details({"description": "Customer demo: customer@example.com\nPosted: 2026-10-28"})[
            "hr_contact_email"
        ]
        is None
    )
    assert details({"description": "Contact HR: 2026-10-28"})["hr_contact_phone"] is None
    empty = details({})
    assert all(value is None for value in empty.values())


def test_distinct_job_specific_formats_and_no_unsupported_qualifications(jobs_client):
    client, _ = jobs_client
    owner = professional(client)
    target = job(
        client,
        requirements="Python, Kubernetes certification",
        responsibilities="Develop payment APIs",
        description="Backend team",
    )
    cover = create(client, target, owner).json()
    email = create(client, target, owner, "email").json()
    assert cover["content"] != email["content"]
    assert email["content"].startswith("Subjek: Lamaran untuk Backend Engineer")
    assert cover["content"].startswith("Lamaran untuk posisi Backend Engineer")
    for draft in (cover, email):
        assert (
            "Develop payment APIs" in draft["content"]
            and "Python, Kubernetes certification" in draft["content"]
        )
        assert "API Platform" in draft["content"] and "Python" in draft["content"]
        assert draft["job"]["id"] == target["id"]
        assert draft["job_snapshot"]["requirements"] == "Python, Kubernetes certification"
        assert (
            not draft["is_job_stale"] and draft["composition_method"] == "local_grounded_template"
        )
        assert not any(
            value in draft["content"]
            for value in (
                "Private Candidate",
                "owner@example.com",
                "081111222333",
                "1991-02-03",
                "Jl. Private",
            )
        )
        assert (
            "certified in Kubernetes" not in draft["content"]
            and "bersertifikasi Kubernetes" not in draft["content"]
        )
    other = job(
        client,
        title="Data Analyst",
        requirements="SQL and analytics",
        responsibilities="Build reporting dashboards",
    )
    analyst = create(client, other, owner).json()
    assert "reporting dashboards" in analyst["content"] and "payment APIs" not in analyst["content"]
    english = create(client, target, owner, "email", language="en").json()
    assert (
        english["content"].startswith("Subject: Application for")
        and "Dear Hiring Team" in english["content"]
    )


def test_draft_automatically_prioritizes_relevant_experiences_over_old_unrelated_sources(
    jobs_client,
):
    client, _ = jobs_client
    professional(client)
    for index in range(5):
        client.post(
            "/api/v1/profile/experiences",
            json={
                "role": "Designer",
                "company": "Design " + str(index),
                "description": "Illustration and print layouts",
            },
        )
    client.post(
        "/api/v1/profile/experiences",
        json={
            "role": "Backend Engineer",
            "company": "API Team",
            "description": "Built Python payment APIs with FastAPI",
        },
    )
    owner = client.get("/api/v1/profile").json()
    target = job(client, requirements="Python and FastAPI", responsibilities="Build payment APIs")
    response = create(client, target, owner)
    assert response.status_code == 201, response.text
    assert "API Team" in response.json()["content"]


def test_draft_edit_delete_ownership_conflicts_and_retained_versions(jobs_client):
    client, db = jobs_client
    owner = professional(client)
    target = job(client)
    draft = create(client, target, owner).json()
    identity = draft["id"]
    other = ActorContext(uuid.uuid4())
    app.dependency_overrides[get_actor] = lambda: other
    try:
        assert client.get("/api/v1/jobs/" + target["id"]).status_code == 404
        assert client.get("/api/v1/drafts/" + identity).status_code == 404
        assert (
            client.delete("/api/v1/drafts/" + identity + "?expected_version=1").status_code == 404
        )
        assert create(client, target, owner).status_code == 404
    finally:
        app.dependency_overrides.pop(get_actor)
    assert (
        client.patch(
            "/api/v1/drafts/" + identity, json={"content": "  ", "expected_version": 1}
        ).status_code
        == 422
    )
    edited = client.patch(
        "/api/v1/drafts/" + identity,
        json={"content": "Owner adjusted application body", "expected_version": 1},
    )
    assert edited.status_code == 200 and edited.json()["version"] == 2
    assert client.delete("/api/v1/drafts/" + identity + "?expected_version=1").status_code == 409
    assert client.delete("/api/v1/drafts/" + identity + "?expected_version=2").status_code == 204
    assert client.delete("/api/v1/drafts/" + identity + "?expected_version=2").status_code == 204
    assert client.get("/api/v1/drafts/" + identity).status_code == 404
    assert not client.get("/api/v1/drafts").json()["items"]
    assert (
        client.patch(
            "/api/v1/drafts/" + identity, json={"content": "restore", "expected_version": 3}
        ).status_code
        == 404
    )
    stored = db.get(Draft, uuid.UUID(identity))
    assert stored.deleted_at and stored.version == 3
    assert (
        len(db.scalars(select(DraftVersion).where(DraftVersion.draft_id == stored.id)).all()) == 2
    )


def test_job_and_profile_changes_mark_existing_drafts_stale(jobs_client):
    client, _ = jobs_client
    owner = professional(client)
    target = job(client, requirements="Python")
    draft = create(client, target, owner).json()
    client.patch("/api/v1/jobs/" + target["id"], json={"requirements": "Python and AWS"})
    saved = client.get("/api/v1/drafts/" + draft["id"]).json()
    assert saved["is_job_stale"] and saved["job_snapshot"]["requirements"] == "Python"
    assert saved["content"] == draft["content"]
    client.patch("/api/v1/profile", json={"headline": "Backend specialist"})
    assert client.get("/api/v1/drafts/" + draft["id"]).json()["is_stale"]
    assert create(client, target, owner).status_code == 409
