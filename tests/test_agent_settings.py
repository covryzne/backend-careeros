import asyncio
import contextlib
import copy
import json
import uuid

import pytest
from fastapi import HTTPException

from app.agent.context import ToolContext
from app.agent.runtime import process_run
from app.agent.worker import claim_next
from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext, get_actor
from app.main import app
from app.models.foundation import AgentPreference
from app.schemas.agent import SearchArguments
from app.services.agent_facade import AgentServices


def patch(client, version=0, **values):
    return client.patch("/api/v1/agent-settings", json={"expected_version": version, **values})


def test_preferences_persist_version_and_control_capabilities(jobs_client):
    client, db = jobs_client
    initial = client.get("/api/v1/agent-settings").json()
    assert initial["version"] == 0 and not initial["enabled"]
    assert db.get(AgentPreference, LOCAL_OWNER_ID) is None
    response = patch(
        client,
        enabled=True,
        internal_writes_enabled=True,
        approvals_enabled=True,
        max_tool_calls=6,
        max_turns=4,
        timeout_seconds=30,
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    caps = client.get("/api/v1/agent-capabilities").json()
    assert caps["enabled"] and caps["internal_writes_enabled"] and caps["approvals_enabled"]
    assert "save_job" in caps["tools"] and caps["timeout_seconds"] == 30
    assert patch(client, enabled=False).status_code == 409
    assert patch(client, 1, enabled=True).json()["version"] == 1
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "career_assistant", "instruction": "Read my data"}
    ).json()
    assert run["max_tool_calls"] == 6 and run["timeout_seconds"] == 30
    assert db.get(AgentPreference, LOCAL_OWNER_ID).max_turns == 4
    assert not any(key in initial for key in ("llm_api_key", "llm_base_url", "database_url"))


def test_preferences_validate_owner_server_ceiling_and_agent_boundary(jobs_client, monkeypatch):
    client, _ = jobs_client
    assert patch(client, enabled=None).status_code == 422
    assert patch(client, external_actions_enabled=True).status_code == 422
    assert patch(client, max_tool_calls=13).status_code == 422
    monkeypatch.setattr(settings, "agent_max_turns", 3)
    assert patch(client, max_turns=4).status_code == 422
    monkeypatch.setattr(settings, "agent_runtime_allowed", False)
    assert patch(client, enabled=True).status_code == 403
    monkeypatch.setattr(settings, "agent_runtime_allowed", True)
    assert patch(client, enabled=True).status_code == 200
    app.dependency_overrides[get_actor] = lambda: ActorContext(uuid.uuid4())
    try:
        assert not client.get("/api/v1/agent-settings").json()["enabled"]
    finally:
        app.dependency_overrides.pop(get_actor, None)
    app.dependency_overrides[get_actor] = lambda: ActorContext(LOCAL_OWNER_ID, "agent")
    try:
        assert client.get("/api/v1/agent-settings").status_code == 403
        assert patch(client, enabled=False).status_code == 403
    finally:
        app.dependency_overrides.pop(get_actor, None)


def test_disable_prevents_queued_execution_and_existing_tool_grants(jobs_client):
    client, db = jobs_client
    patch(client, enabled=True)
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "career_assistant", "instruction": "Review"}
    ).json()
    assert patch(client, 1, enabled=False).status_code == 200
    assert claim_next(lambda: contextlib.nullcontext(db)) is None
    assert client.get("/api/v1/agent-runs/" + run["id"]).json()["status"] == "cancelled"
    assert (
        client.post(
            "/api/v1/agent-runs", json={"task_type": "career_assistant", "instruction": "Review"}
        ).status_code
        == 503
    )
    context = ToolContext(
        ActorContext(LOCAL_OWNER_ID, "agent"), uuid.uuid4(), frozenset({"search_jobs"}), {}
    )
    with pytest.raises(HTTPException):
        AgentServices(db).invoke(context, "search_jobs", SearchArguments())


def test_settings_retry_does_not_duplicate_version(jobs_client):
    client, _ = jobs_client
    payload = {"expected_version": 0, "enabled": True}
    headers = {"Idempotency-Key": "owner-setting-retry"}
    first = client.patch("/api/v1/agent-settings", json=payload, headers=headers)
    retry = client.patch("/api/v1/agent-settings", json=payload, headers=headers)
    assert first.status_code == 200 and retry.json() == first.json()
    assert client.get("/api/v1/agent-settings").json()["version"] == 1


def test_matching_uses_professional_profile_without_verified_facts_and_filters_pii(jobs_client):
    from app.services.agent_privacy import sanitize_messages
    from app.services.career import career_context, validate_report

    client, db = jobs_client
    personal = {
        "full_name": "Private Candidate",
        "email": "secret@example.com",
        "phone": "081234567890",
        "date_of_birth": "1990-01-17",
        "place_of_birth": "Bekasi",
        "location": "Jl. Rahasia No. 11, Bekasi",
    }
    summary = "Build reliable APIs. Email secret@example.com; Phone 081234567890; born 1990-01-17; Alamat: Jl. Rahasia No. 11, Bekasi"
    client.patch(
        "/api/v1/profile",
        json={
            **personal,
            "headline": "Backend Engineer",
            "professional_summary": summary,
            "preferred_titles": ["Backend Engineer"],
            "preferred_locations": ["Jakarta"],
            "preferred_salary_min": 10000000,
        },
    )
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    client.post(
        "/api/v1/profile/experiences",
        json={"role": "Engineer", "company": "Example", "description": summary},
    )
    client.post("/api/v1/profile/projects", json={"name": "API Platform", "description": summary})
    job = client.post(
        "/api/v1/jobs",
        json={
            "title": "Backend Engineer",
            "company": "Example",
            "location": "Jakarta",
            "description": "Build Python APIs.",
        },
    ).json()
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    serialized = json.dumps(context)
    for value in personal.values():
        assert value not in serialized, value
    assert all(
        kind in [source["kind"] for source in context["sources"].values()]
        for kind in ["profile", "skill", "experience", "project", "preference"]
    )
    assert "Jakarta" in serialized and "10000000" in serialized
    assert not any(source["kind"] == "fact" for source in context["sources"].values())
    skill = next(source for source in context["sources"].values() if source["kind"] == "skill")
    report = {
        "kind": "job_analysis",
        "title": "Prioritas",
        "summary": "Berdasarkan profil profesional.",
        "recommendations": [
            {
                "job_id": job["id"],
                "category": "prioritize",
                "reasons": [
                    {
                        "text": "Pengalaman dan kebutuhan posisi dapat dibandingkan.",
                        "citations": [
                            {"source_id": "job:" + job["id"], "quote": "Backend Engineer"},
                            {"source_id": skill["id"], "quote": "Python"},
                        ],
                    }
                ],
            }
        ],
    }
    validated = validate_report(json.dumps(report), context)
    assert validated["report"]["recommendations"][0]["category"] == "prioritize"
    assert validated["review_flags"] == []
    patch(client, enabled=True)
    run = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "career_assistant",
            "instruction": "Review. secret@example.com 081234567890 1990-01-17",
        },
    ).json()
    factory = lambda: contextlib.nullcontext(db)
    claim_next(factory)

    class Provider:
        captured = None

        async def complete(self, messages, *_):
            self.captured = copy.deepcopy(messages)
            return {"content": json.dumps(report)}

    provider = Provider()
    asyncio.run(process_run(uuid.UUID(run["id"]), provider, factory))
    assert client.get("/api/v1/agent-runs/" + run["id"]).json()["status"] == "completed"
    for value in personal.values():
        assert value not in json.dumps(provider.captured)
    assert "Python" in json.dumps(provider.captured)
    # Resource IDs and tool protocol must survive privacy filtering intact.
    assert job["id"] in json.dumps(sanitize_messages(provider.captured))


def test_embedded_worker_lifecycle_does_not_need_agent_enable_env(jobs_client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.agent import worker

    started = []

    async def local_worker():
        started.append(True)
        await asyncio.Future()

    monkeypatch.setattr(worker, "main", local_worker)
    monkeypatch.setattr(settings, "agent_worker_embedded", True)
    monkeypatch.setattr(settings, "app_env", "development")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert app.state.agent_worker is not None
        assert not app.state.agent_worker.done()
    assert started and app.state.agent_worker.cancelled()
