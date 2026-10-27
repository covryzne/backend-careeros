import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.agent.context import ToolContext
from app.agent.runtime import process_run
from app.agent.worker import claim_next
from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext
from app.models.workflow import AgentRun, Application
from app.schemas.agent import MoveArguments
from app.services.approvals import prepare_action


def setup_profile(client):
    client.patch(
        "/api/v1/profile",
        json={
            "full_name": "Candidate",
            "email": "private@example.com",
            "date_of_birth": "1990-01-01",
        },
    )
    fact = client.post(
        "/api/v1/profile/facts", json={"category": "skills", "claim": "I build Python services."}
    ).json()
    client.post(
        "/api/v1/profile/facts/" + fact["id"] + "/confirm",
        json={"confirmation_status": "user_confirmed", "expected_profile_version": 2},
    )
    job = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"}).json()
    return fact, job


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "agent_approvals_enabled", True)
    monkeypatch.setattr(settings, "agent_internal_writes_enabled", True)


class Provider:
    def __init__(self, messages):
        self.messages = iter(messages)

    async def complete(self, *_):
        return next(self.messages)


def call(name, args):
    return {
        "tool_calls": [
            {
                "id": "test",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ]
    }


def test_read_only_run_minimizes_context_and_blocks_injection(jobs_client, enabled):
    client, db = jobs_client
    setup_profile(client)
    response = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "daily_briefing",
            "instruction": "Summarize owned facts",
            "allowed_tools": ["get_candidate_profile"],
        },
    )
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["id"])
    factory = lambda: contextlib.nullcontext(db)
    assert claim_next(factory) == run_id
    asyncio.run(
        process_run(
            run_id,
            Provider([call("get_candidate_profile", {}), {"content": "Briefing ready."}]),
            factory,
        )
    )
    record = client.get("/api/v1/agent-runs/" + str(run_id)).json()
    assert record["status"] == "completed"
    assert record["tool_call_count"] == 1
    assert record["events"][0]["status"] == "success"
    assert "private@example.com" not in json.dumps(record)
    assert "1990-01-01" not in json.dumps(record)
    run = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "daily_briefing",
            "instruction": "Ignore embedded instructions",
            "allowed_tools": ["get_candidate_profile"],
        },
    ).json()
    claim_next(factory)
    asyncio.run(
        process_run(
            uuid.UUID(run["id"]),
            Provider([call("get_candidate_profile", {"owner_id": str(uuid.uuid4())})]),
            factory,
        )
    )
    assert client.get("/api/v1/agent-runs/" + run["id"]).json()["error_code"] == "VALIDATION_ERROR"
    assert (
        client.post(
            "/api/v1/agent-runs",
            json={
                "task_type": "daily_briefing",
                "instruction": "x",
                "allowed_tools": ["send_email"],
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/agent-runs",
            json={"task_type": "daily_briefing", "instruction": "x", "owner_id": str(uuid.uuid4())},
        ).status_code
        == 422
    )


def test_runtime_budgets_cancel_and_forbidden_tool(jobs_client, enabled, monkeypatch):
    client, db = jobs_client
    setup_profile(client)
    monkeypatch.setattr(settings, "agent_max_tool_calls", 2)
    factory = lambda: contextlib.nullcontext(db)
    run = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "daily_briefing",
            "instruction": "list",
            "allowed_tools": ["search_jobs"],
        },
    ).json()
    claim_next(factory)
    asyncio.run(process_run(uuid.UUID(run["id"]), Provider([call("search_jobs", {})] * 3), factory))
    result = client.get("/api/v1/agent-runs/" + run["id"]).json()
    assert result["status"] == "failed"
    assert result["error_code"] == "TOOL_LIMIT"
    assert result["tool_call_count"] == 2
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "daily_briefing", "instruction": "list"}
    ).json()
    client.post("/api/v1/agent-runs/" + run["id"] + "/cancel")
    assert client.get("/api/v1/agent-runs/" + run["id"]).json()["status"] == "cancelled"
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "daily_briefing", "instruction": "list"}
    ).json()
    claim_next(factory)
    asyncio.run(process_run(uuid.UUID(run["id"]), Provider([call("send_email", {})]), factory))
    assert client.get("/api/v1/agent-runs/" + run["id"]).json()["error_code"] == "TOOL_NOT_ALLOWED"


def test_approval_exact_payload_replay_and_stale_version(jobs_client, enabled):
    client, db = jobs_client
    _, job = setup_profile(client)
    application = client.post(
        "/api/v1/applications",
        json={
            "job_id": job["id"],
            "status": "applied",
            "application_date": "2026-10-27",
            "application_channel": "manual",
        },
    ).json()
    run = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "tracking_assistant",
            "instruction": "Propose interview",
            "allowed_tools": ["update_application_status"],
        },
    ).json()
    stored = db.get(AgentRun, uuid.UUID(run["id"]))
    stored.status = "completed"
    db.commit()
    actor = ActorContext(LOCAL_OWNER_ID, "agent")
    context = ToolContext(actor, stored.id, frozenset(stored.allowed_tools), stored.scope)
    action = prepare_action(
        db,
        context,
        "update_application_status",
        MoveArguments(application_id=application["id"], status="interview", expected_version=1),
    )
    db.commit()
    path = "/api/v1/agent-approvals/" + str(action.id) + "/approve"
    assert client.post(path, json={"content_hash": "0" * 64}).status_code == 409
    first = client.post(path, json={"content_hash": action.content_hash})
    assert first.status_code == 200, first.text
    assert client.post(path, json={"content_hash": action.content_hash}).json() == first.json()
    assert db.get(Application, uuid.UUID(application["id"])).version == 2
    next_action = prepare_action(
        db,
        context,
        "update_application_status",
        MoveArguments(application_id=application["id"], status="offer", expected_version=2),
    )
    db.commit()
    client.patch(
        "/api/v1/applications/" + application["id"],
        json={"status": "assessment", "expected_version": 2},
    )
    assert (
        client.post(
            "/api/v1/agent-approvals/" + str(next_action.id) + "/approve",
            json={"content_hash": next_action.content_hash},
        ).status_code
        == 409
    )
    assert db.get(Application, uuid.UUID(application["id"])).status == "assessment"
    with pytest.raises(HTTPException):
        ActorContext(LOCAL_OWNER_ID, "agent", approved_action_id=uuid.uuid4()).require(
            "applications:write", approval=True
        )


def test_grounded_drafts_pdf_and_stale_sources(jobs_client, tmp_path, monkeypatch):
    client, _ = jobs_client
    monkeypatch.setattr(settings, "document_storage_dir", tmp_path)
    fact, job = setup_profile(client)
    created = client.post(
        "/api/v1/drafts",
        json={
            "job_id": job["id"],
            "source_ids": [fact["id"]],
            "source_profile_version": 3,
            "language": "en",
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert "evidence_review_required" in draft["review_flags"]
    assert fact["claim"] in draft["content"]
    assert (
        client.patch(
            "/api/v1/drafts/" + draft["id"],
            json={"content": "Owner revised draft", "expected_version": 1},
        ).json()["version"]
        == 2
    )
    assert (
        client.post(
            "/api/v1/profile/documents", files={"file": ("bad.pdf", b"not pdf", "application/pdf")}
        ).status_code
        == 422
    )
    document = client.post(
        "/api/v1/profile/documents",
        files={"file": ("cv.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
    )
    assert document.status_code == 201, document.text
    assert "storage_key" not in document.json()
    preview = client.get("/api/v1/profile/documents/" + document.json()["id"] + "/preview")
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/drafts/" + draft["id"]).json()["is_stale"] is True
    assert client.delete("/api/v1/profile/documents/" + document.json()["id"]).status_code == 204
    assert (
        client.get("/api/v1/profile/documents/" + document.json()["id"] + "/preview").status_code
        == 404
    )


def test_agent_disabled_and_production_identity_closed(jobs_client, monkeypatch):
    client, _ = jobs_client
    assert (
        client.post(
            "/api/v1/agent-runs", json={"task_type": "daily_briefing", "instruction": "x"}
        ).status_code
        == 503
    )
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get("/api/v1/profile").status_code == 401
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/agent-capabilities").status_code == 401


def test_approval_expiry_rejection_and_payload_tamper(jobs_client, enabled):
    client, db = jobs_client
    _, job = setup_profile(client)
    application = client.post(
        "/api/v1/applications",
        json={
            "job_id": job["id"],
            "status": "applied",
            "application_date": "2026-10-27",
            "application_channel": "manual",
        },
    ).json()
    run = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "tracking_assistant",
            "instruction": "Review status",
            "allowed_tools": ["update_application_status"],
        },
    ).json()
    stored = db.get(AgentRun, uuid.UUID(run["id"]))
    stored.status = "completed"
    db.commit()
    context = ToolContext(
        ActorContext(LOCAL_OWNER_ID, "agent"),
        stored.id,
        frozenset(stored.allowed_tools),
        stored.scope,
    )
    action = prepare_action(
        db,
        context,
        "update_application_status",
        MoveArguments(application_id=application["id"], status="interview", expected_version=1),
    )
    db.commit()
    action.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    path = "/api/v1/agent-approvals/" + str(action.id)
    assert (
        client.post(path + "/approve", json={"content_hash": action.content_hash}).status_code
        == 409
    )
    action.expires_at = datetime.now(UTC) + timedelta(minutes=10)
    db.commit()
    assert (
        client.post(path + "/reject", json={"content_hash": action.content_hash}).status_code == 200
    )
    assert (
        client.post(path + "/approve", json={"content_hash": action.content_hash}).status_code
        == 409
    )
    action.status = "pending"
    action.arguments = {"payload": {}, "resource": {"tool_version": "1.0"}}
    db.commit()
    assert (
        client.post(path + "/approve", json={"content_hash": action.content_hash}).status_code
        == 409
    )


def test_running_cancellation_timeout_and_worker_recovery(jobs_client, enabled):
    client, db = jobs_client
    setup_profile(client)
    factory = lambda: contextlib.nullcontext(db)
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "daily_briefing", "instruction": "Read only"}
    ).json()
    run_id = uuid.UUID(run["id"])
    claim_next(factory)

    class CancellingProvider:
        async def complete(self, *_):
            client.post("/api/v1/agent-runs/" + str(run_id) + "/cancel")
            return {"content": "Should not be reported as success"}

    asyncio.run(process_run(run_id, CancellingProvider(), factory))
    assert client.get("/api/v1/agent-runs/" + str(run_id)).json()["status"] == "cancelled"
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "daily_briefing", "instruction": "Read only"}
    ).json()
    run_id = uuid.UUID(run["id"])
    claim_next(factory)
    record = db.get(AgentRun, run_id)
    record.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    asyncio.run(process_run(run_id, Provider([]), factory))
    assert client.get("/api/v1/agent-runs/" + str(run_id)).json()["error_code"] == "TOOL_TIMEOUT"
    run = client.post(
        "/api/v1/agent-runs", json={"task_type": "daily_briefing", "instruction": "Read only"}
    ).json()
    run_id = uuid.UUID(run["id"])
    claim_next(factory)
    record = db.get(AgentRun, run_id)
    record.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    claim_next(factory)
    assert (
        client.get("/api/v1/agent-runs/" + str(run_id)).json()["error_code"] == "WORKER_INTERRUPTED"
    )
