import asyncio
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agent.runtime import process_run
from app.agent.worker import claim_next
from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext, get_actor
from app.main import app
from app.models.foundation import IdempotencyRecord, ProviderSetting
from app.models.workflow import AgentRun, AuditEvent
from app.services.provider_settings import config_for


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_storage_dir", tmp_path)
    monkeypatch.setattr(settings, "provider_encryption_key", None)
    monkeypatch.setattr(settings, "llm_base_url", None)
    monkeypatch.setattr(settings, "llm_model", None)
    monkeypatch.setattr(settings, "llm_api_key", None)


def save(client, version=0, **values):
    return client.patch(
        "/api/v1/provider-settings",
        json={
            "base_url": "http://127.0.0.1:9999/v1",
            "model": "fixture-model",
            "expected_version": version,
            **values,
        },
    )


def test_encrypted_write_only_secret_and_metadata_readiness(jobs_client):
    client, db = jobs_client
    secret = "sk-private-fixture-key"
    response = save(client, api_key=secret)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "base_url": "http://127.0.0.1:9999/v1",
        "model": "fixture-model",
        "api_key_set": True,
        "configured": True,
        "source": "saved",
        "version": 1,
    }
    assert secret not in response.text
    assert secret not in client.get("/api/v1/provider-settings").text
    row = db.get(ProviderSetting, LOCAL_OWNER_ID)
    assert secret not in row.encrypted_api_key and row.encrypted_api_key.startswith("gAAAA")
    config = config_for(db, LOCAL_OWNER_ID)
    assert config.api_key.get_secret_value() == secret and secret not in repr(config)
    assert client.get("/api/v1/agent-settings").json()["provider_configured"]
    assert client.get("/api/v1/agent-capabilities").json()["provider_configured"]
    assert not client.get("/api/v1/agent-settings").json()["enabled"]
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "provider_settings_updated")
    ).all()
    assert len(events) == 1 and secret not in str([event.after_data for event in events])


def test_preserve_replace_secret_endpoint_boundary_and_conflicts(jobs_client):
    client, db = jobs_client
    assert save(client).status_code == 422
    assert save(client, api_key="original-key").status_code == 200
    assert save(client, 1, model="next-model").json()["version"] == 2
    assert config_for(db, LOCAL_OWNER_ID).api_key.get_secret_value() == "original-key"
    assert save(client, 1, api_key="different-key").status_code == 409
    assert save(client, 2, base_url="https://other.example/v1").status_code == 422
    assert (
        save(client, 2, base_url="https://other.example/v1", api_key="replacement-key").status_code
        == 200
    )
    assert config_for(db, LOCAL_OWNER_ID).api_key.get_secret_value() == "replacement-key"


def test_owner_and_agent_boundary_ciphertext_cannot_move_between_owners(jobs_client):
    client, db = jobs_client
    assert save(client, api_key="first-owner-key").status_code == 200
    other = ActorContext(uuid.uuid4())
    first = db.get(ProviderSetting, LOCAL_OWNER_ID).encrypted_api_key
    app.dependency_overrides[get_actor] = lambda: other
    try:
        assert client.get("/api/v1/provider-settings").json()["version"] == 0
        assert save(client, api_key="second-owner-key").status_code == 200
        assert config_for(db, other.owner_id).api_key.get_secret_value() == "second-owner-key"
        row = db.get(ProviderSetting, other.owner_id)
        row.encrypted_api_key = first
        db.commit()
        assert not client.get("/api/v1/provider-settings").json()["configured"]
    finally:
        app.dependency_overrides.pop(get_actor)
    app.dependency_overrides[get_actor] = lambda: ActorContext(LOCAL_OWNER_ID, "agent")
    try:
        assert client.get("/api/v1/provider-settings").status_code == 403
        assert save(client, 1, api_key="cannot-write").status_code == 403
    finally:
        app.dependency_overrides.pop(get_actor)


@pytest.mark.parametrize("value", [None, "", "has space", "newline\r\nsecret"])
def test_invalid_keys_are_rejected_without_echo(jobs_client, value):
    client, _ = jobs_client
    response = save(client, api_key=value)
    assert response.status_code == 422
    if value:
        assert value not in response.text


def test_secret_idempotency_and_bad_master_fail_closed(jobs_client):
    client, db = jobs_client
    payload = {
        "base_url": "https://provider.example/v1",
        "model": "fixture",
        "expected_version": 0,
        "api_key": "secret-idempotency-value",
    }
    headers = {"Idempotency-Key": "provider-fixture"}
    first = client.patch("/api/v1/provider-settings", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert (
        client.patch("/api/v1/provider-settings", json=payload, headers=headers).json()
        == first.json()
    )
    assert (
        client.patch(
            "/api/v1/provider-settings",
            json={**payload, "api_key": "different-secret"},
            headers=headers,
        ).status_code
        == 409
    )
    record = db.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.action == "provider-settings:update")
    )
    assert payload["api_key"] not in str(record.result)
    (settings.document_storage_dir / ".provider-key").write_bytes(b"corrupt-key")
    assert not client.get("/api/v1/provider-settings").json()["configured"]
    assert save(client, 1, api_key="new-secret").status_code == 503
    assert (settings.document_storage_dir / ".provider-key").read_bytes() == b"corrupt-key"


def test_invalid_urls_no_embedded_credentials_and_environment_compatibility(
    jobs_client, monkeypatch
):
    client, _ = jobs_client
    for url in [
        "https://user:password@example.com/v1",
        "https://example.com/v1?key=do-not-echo",
        "file:///tmp/a",
        "https://example.com/v1#fragment",
    ]:
        result = save(client, base_url=url, api_key="fixture")
        assert result.status_code == 422
        assert "do-not-echo" not in result.text and "password@" not in result.text
    monkeypatch.setattr(settings, "llm_base_url", "https://env.example/v1")
    monkeypatch.setattr(settings, "llm_model", "env-model")
    monkeypatch.setattr(settings, "llm_api_key", "env-secret")
    result = client.get("/api/v1/provider-settings")
    assert result.json()["source"] == "environment" and result.json()["configured"]
    assert "env-secret" not in result.text
    monkeypatch.setattr(settings, "app_env", "production")
    # Validate schema directly; production local identity intentionally rejects API access.
    from app.schemas.provider_settings import ProviderSettingsPatch

    with pytest.raises(ValueError):
        ProviderSettingsPatch(
            expected_version=0, base_url="http://example.com", model="a", api_key="b"
        )


def test_worker_uses_saved_credentials_over_real_http_without_exposing_key(
    jobs_client, monkeypatch
):
    client, db = jobs_client
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("Authorization"), body["model"]))
            report = {
                "kind": "general",
                "title": "Saved provider works",
                "summary": "Empty context reviewed.",
                "recommendations": [],
                "pipeline_reviews": [],
                "next_steps": [],
                "limitations": [],
            }
            data = json.dumps({"choices": [{"message": {"content": json.dumps(report)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert (
            save(
                client,
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="saved-model",
                api_key="saved-fixture-key",
            ).status_code
            == 200
        )
        monkeypatch.setattr(settings, "agent_enabled", True)
        run = client.post(
            "/api/v1/agent-runs",
            json={"task_type": "career_assistant", "instruction": "Review my context"},
        ).json()
        factory = sessionmaker(db.bind, expire_on_commit=False)
        assert claim_next(factory) == uuid.UUID(run["id"])
        asyncio.run(process_run(uuid.UUID(run["id"]), session_factory=factory))
        db.expire_all()
        stored = db.get(AgentRun, uuid.UUID(run["id"]))
        assert stored.status == "completed", stored.error_code
        assert received == [("/v1/chat/completions", "Bearer saved-fixture-key", "saved-model")]
        result = client.get("/api/v1/agent-runs/" + run["id"]).text
        assert "saved-fixture-key" not in result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
