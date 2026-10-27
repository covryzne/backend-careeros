import uuid

from app.core.security import ActorContext, get_actor
from app.main import app


def test_profile_partial_preferences_and_noop_version(jobs_client):
    client, _ = jobs_client
    created = client.patch(
        "/api/v1/profile",
        json={
            "full_name": "Candidate",
            "preferred_titles": ["Engineer"],
            "preferred_locations": ["Remote"],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["profile_version"] == 1
    updated = client.patch(
        "/api/v1/profile",
        json={"preferred_titles": ["Architect"]},
        headers={"X-Expected-Profile-Version": "1"},
    )
    assert updated.status_code == 200, updated.text
    assert {(p["preference_type"], p["value"]) for p in updated.json()["preferences"]} == {
        ("title", "Architect"),
        ("location", "Remote"),
    }
    assert updated.json()["profile_version"] == 2
    assert (
        client.patch("/api/v1/profile", json={"preferred_titles": ["Architect"]}).json()[
            "profile_version"
        ]
        == 2
    )
    cleared = client.patch("/api/v1/profile", json={"preferred_titles": []}).json()
    assert [(p["preference_type"], p["value"]) for p in cleared["preferences"]] == [
        ("location", "Remote")
    ]
    assert client.patch("/api/v1/profile", json={"full_name": None}).status_code == 422
    assert client.patch("/api/v1/profile", json={"preferred_titles": None}).status_code == 422


def test_child_versions_patch_idempotency_and_grounding(jobs_client):
    client, _ = jobs_client
    client.patch("/api/v1/profile", json={"full_name": "Candidate"})
    headers = {"X-Expected-Profile-Version": "1", "Idempotency-Key": "create-skill"}
    created = client.post(
        "/api/v1/profile/skills", json={"name": "Python", "proficiency": "basic"}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.headers["x-profile-version"] == "2"
    again = client.post(
        "/api/v1/profile/skills", json={"name": "Python", "proficiency": "basic"}, headers=headers
    )
    assert again.json()["id"] == created.json()["id"]
    assert len(client.get("/api/v1/profile").json()["skills"]) == 1
    path = "/api/v1/profile/skills/" + created.json()["id"]
    assert (
        client.patch(
            path, json={"proficiency": "advanced"}, headers={"X-Expected-Profile-Version": "1"}
        ).status_code
        == 409
    )
    changed = client.patch(
        path, json={"proficiency": "advanced"}, headers={"X-Expected-Profile-Version": "2"}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Python"
    assert changed.headers["x-profile-version"] == "3"
    assert client.patch(path, json={"proficiency": "advanced"}).headers["x-profile-version"] == "3"
    fact = client.post(
        "/api/v1/profile/facts",
        json={"category": "skills", "claim": "Uses Python", "is_verified": True},
    ).json()
    assert fact["confirmation_status"] == "unconfirmed"
    assert fact["evidence_status"] == "unassessed"
    assert (
        client.post(
            "/api/v1/profile/facts/" + fact["id"] + "/confirm",
            json={"confirmation_status": "user_confirmed", "expected_profile_version": 4},
        ).status_code
        == 200
    )
    changed = client.patch(
        "/api/v1/profile/facts/" + fact["id"], json={"claim": "Uses Python daily"}
    )
    assert changed.json()["confirmation_status"] == "unconfirmed"
    assert client.delete(path).status_code == 204
    assert client.get("/api/v1/profile").json()["profile_version"] == 7


def test_profile_isolation_and_error_contract(jobs_client):
    client, _ = jobs_client
    client.patch("/api/v1/profile", json={"full_name": "Owner"})
    fact = client.post(
        "/api/v1/profile/facts", json={"category": "test", "claim": "Owned claim"}
    ).json()
    app.dependency_overrides[get_actor] = lambda: ActorContext(uuid.uuid4())
    try:
        response = client.get("/api/v1/profile")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.headers["x-request-id"]
        assert (
            client.patch(
                "/api/v1/profile/facts/" + fact["id"], json={"claim": "stolen"}
            ).status_code
            == 404
        )
    finally:
        app.dependency_overrides.pop(get_actor, None)
