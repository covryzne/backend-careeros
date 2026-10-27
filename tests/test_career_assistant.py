import asyncio
import contextlib
import copy
import json
import uuid

import pytest

from app.agent.runtime import process_run
from app.agent.worker import claim_next
from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext
from app.models.workflow import AgentRun
from app.services.career import career_context, validate_report


@pytest.fixture
def assistant(monkeypatch):
    monkeypatch.setattr(settings, "agent_enabled", True)


def seed(client):
    client.patch(
        "/api/v1/profile", json={"full_name": "Career Candidate", "email": "private@example.com"}
    )
    fact = client.post(
        "/api/v1/profile/facts", json={"category": "skills", "claim": "I build Python APIs."}
    ).json()
    client.post(
        "/api/v1/profile/facts/" + fact["id"] + "/confirm",
        json={"confirmation_status": "user_confirmed", "expected_profile_version": 2},
    )
    client.post("/api/v1/profile/skills", json={"name": "Python"})
    job = client.post(
        "/api/v1/jobs",
        json={
            "title": "Backend Engineer",
            "company": "Example",
            "description": "Build Python APIs with FastAPI.",
        },
    ).json()
    return fact, job


def report_for(context):
    jobs = context["jobs"]
    report = {
        "kind": "job_analysis",
        "title": "Peluang karier",
        "summary": "Tinjau sumber sebelum melamar.",
        "recommendations": [],
        "pipeline_reviews": [],
        "next_steps": [],
        "limitations": [],
    }
    if jobs:
        job = jobs[0]
        refs = [{"source_id": "job:" + job["id"], "quote": "Backend Engineer"}]
        confirmed = next(
            (item for item in context["sources"].values() if item["kind"] == "skill"),
            None,
        )
        if confirmed:
            refs.append({"source_id": confirmed["id"], "quote": confirmed["text"]})
        report["recommendations"] = [
            {
                "job_id": job["id"],
                "category": "prioritize" if confirmed else "insufficient",
                "reasons": [
                    {
                        "text": "Periksa kecocokan pengalaman dengan kebutuhan lowongan.",
                        "citations": refs,
                    }
                ],
                "gaps": [],
                "unknowns": [],
            }
        ]
    return report


class ReportProvider:
    def __init__(self, invalid=0):
        self.calls = 0
        self.invalid = invalid

    async def complete(self, messages, *_):
        self.calls += 1
        context = json.loads(
            next(message["content"] for message in messages if message["role"] == "tool")
        )["data"]
        result = report_for(context)
        if self.calls <= self.invalid:
            result["recommendations"][0]["reasons"][0]["citations"][0]["quote"] = "Invented quote"
        return {"content": json.dumps(result)}


def run_task(client, db, provider, scope=None):
    response = client.post(
        "/api/v1/agent-runs",
        json={
            "task_type": "career_assistant",
            "instruction": "Prioritaskan peluang saya",
            "scope": scope or {},
        },
    )
    assert response.status_code == 202, response.text
    record = response.json()
    factory = lambda: contextlib.nullcontext(db)
    assert claim_next(factory) == uuid.UUID(record["id"])
    asyncio.run(process_run(uuid.UUID(record["id"]), provider, factory))
    return client.get("/api/v1/agent-runs/" + record["id"]).json()


def test_assistant_structured_report_context_and_stale(jobs_client, assistant):
    client, db = jobs_client
    _fact, job = seed(client)
    record = run_task(client, db, ReportProvider())
    assert record["status"] == "completed", record
    assert record["tool_call_count"] == 1
    assert record["model_turn_count"] == 1
    assert record["scope"]["job_collection"] == "saved"
    assert not any(key.startswith("_") for key in record["scope"])
    assert record["result"]["coverage"]["jobs_reviewed"] == 1
    item = record["result"]["report"]["recommendations"][0]
    assert item["job"]["id"] == job["id"] and item["category"] == "prioritize"
    assert "private@example.com" not in json.dumps(record)
    assert record["result"]["review_flags"] == []
    client.patch("/api/v1/profile", json={"headline": "Changed"})
    assert client.get("/api/v1/agent-runs/" + record["id"]).json()["is_profile_stale"]
    assert db.get(AgentRun, uuid.UUID(record["id"])).allowed_tools == ["get_career_context"]


def test_source_collection_and_limits(jobs_client, assistant):
    client, db = jobs_client
    seed(client)
    archived = client.post(
        "/api/v1/jobs", json={"title": "Archived", "company": "Example", "status": "archived"}
    ).json()
    for i in range(12):
        client.post("/api/v1/jobs", json={"title": f"Engineer {i}", "company": "Example"})
    actor = ActorContext(LOCAL_OWNER_ID, "agent")
    saved = career_context(db, actor, {"job_collection": "saved"})
    all_jobs = career_context(db, actor, {"job_collection": "crawled"})
    assert saved["coverage"]["jobs_available"] == 13
    assert all_jobs["coverage"]["jobs_available"] == 14
    assert len(all_jobs["jobs"]) == 10
    selected = career_context(
        db, actor, {"job_collection": "selected", "job_ids": [archived["id"]]}
    )
    assert selected["jobs"][0]["id"] == archived["id"]
    for scope in [
        {"job_collection": "selected"},
        {"job_collection": "selected", "job_ids": [str(uuid.uuid4())]},
        {"job_collection": "saved", "job_ids": [archived["id"]]},
        {"job_collection": "selected", "job_ids": [archived["id"]] * 11},
    ]:
        response = client.post(
            "/api/v1/agent-runs",
            json={"task_type": "career_assistant", "instruction": "Check", "scope": scope},
        )
        assert response.status_code in (404, 422), response.text
    assert (
        client.post(
            "/api/v1/agent-runs",
            json={
                "task_type": "career_assistant",
                "instruction": "Write",
                "allowed_tools": ["save_job"],
            },
        ).status_code
        == 403
    )


def test_report_repair_once_and_failure(jobs_client, assistant):
    client, db = jobs_client
    seed(client)
    provider = ReportProvider(invalid=1)
    record = run_task(client, db, provider)
    assert record["status"] == "completed" and provider.calls == 2
    provider = ReportProvider(invalid=2)
    record = run_task(client, db, provider)
    assert record["status"] == "failed" and record["error_code"] == "OUTPUT_VALIDATION_ERROR"
    assert provider.calls == 2
    assert not record["result"].get("report")


def test_empty_context_and_missing_profile(jobs_client, assistant):
    client, db = jobs_client
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    assert context["profile"] is None and not context["jobs"]
    record = run_task(client, db, ReportProvider())
    assert record["status"] == "completed"
    assert record["result"]["coverage"]["jobs_reviewed"] == 0


def test_report_rejects_outside_ids_unconfirmed_priority_and_bad_quotes(jobs_client):
    client, db = jobs_client
    fact, _ = seed(client)
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    valid = report_for(context)
    for field in ("job", "source", "quote", "priority"):
        report = copy.deepcopy(valid)
        if field == "job":
            report["recommendations"][0]["job_id"] = str(uuid.uuid4())
        elif field == "source":
            report["recommendations"][0]["reasons"][0]["citations"][0]["source_id"] = "job:" + str(
                uuid.uuid4()
            )
        elif field == "quote":
            report["recommendations"][0]["reasons"][0]["citations"][0]["quote"] = "not in source"
        else:
            report["recommendations"][0]["reasons"][0]["citations"] = report["recommendations"][0][
                "reasons"
            ][0]["citations"][:1]
        with pytest.raises(ValueError):
            validate_report(json.dumps(report), context)
    client.post(
        "/api/v1/profile/facts/" + fact["id"] + "/confirm",
        json={
            "confirmation_status": "unconfirmed",
            "expected_profile_version": client.get("/api/v1/profile").json()["profile_version"],
        },
    )
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    assert (
        validate_report(json.dumps(valid), context)["report"]["recommendations"][0]["category"]
        == "prioritize"
    )


def test_history_pagination_filter_and_owner_isolation(jobs_client, assistant):
    from app.core.security import get_actor
    from app.main import app

    client, _db = jobs_client
    for i in range(3):
        record = client.post(
            "/api/v1/agent-runs",
            json={"task_type": "career_assistant", "instruction": f"Check {i}"},
        ).json()
        client.post("/api/v1/agent-runs/" + record["id"] + "/cancel")
    response = client.get("/api/v1/agent-runs?page_size=2&status=cancelled").json()
    assert response["total"] == 3 and response["has_next"] and len(response["items"]) == 2
    assert client.get("/api/v1/agent-runs?q=Check%202").json()["total"] == 1
    app.dependency_overrides[get_actor] = lambda: ActorContext(uuid.uuid4())
    try:
        assert client.get("/api/v1/agent-runs").json()["total"] == 0
        assert client.get("/api/v1/agent-runs/" + record["id"]).status_code == 404
    finally:
        app.dependency_overrides.pop(get_actor, None)


def test_pipeline_and_next_steps_use_real_state(jobs_client):
    client, db = jobs_client
    _, job = seed(client)
    application = client.post(
        "/api/v1/applications",
        json={
            "job_id": job["id"],
            "status": "applied",
            "application_date": "2026-10-27",
            "application_channel": "manual",
            "follow_up_date": "2026-10-28",
        },
    ).json()
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {})
    assert context["jobs"][0]["application_status"] == "applied"
    report = report_for(context)
    report["kind"] = "pipeline_review"
    report["pipeline_reviews"] = [
        {
            "application_id": application["id"],
            "observation": {
                "text": "Tahap tercatat applied.",
                "citations": [
                    {"source_id": "application:" + application["id"], "quote": "Status: applied"}
                ],
            },
            "next_step": "Tinjau tanggal follow-up sebelum menghubungi recruiter.",
        }
    ]
    report["next_steps"] = [
        {
            "text": "Periksa pipeline.",
            "job_id": job["id"],
            "application_id": application["id"],
            "citations": [],
        }
    ]
    result = validate_report(json.dumps(report), context)
    assert result["report"]["pipeline_reviews"][0]["application"]["follow_up_date"] == "2026-10-28"
    assert "application:" + application["id"] in [source["id"] for source in result["sources"]]
    report["pipeline_reviews"][0]["application_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError):
        validate_report(json.dumps(report), context)


def test_assistant_executes_over_real_provider_http(jobs_client, assistant, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from app.integrations.llm import LLMProvider

    client, db = jobs_client
    seed(client)
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            context = json.loads(
                next(
                    message["content"] for message in body["messages"] if message["role"] == "tool"
                )
            )["data"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"choices": [{"message": {"content": json.dumps(report_for(context))}}]}
                ).encode()
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(settings, "llm_api_key", "local-fixture-only")
    monkeypatch.setattr(settings, "llm_model", "local-fixture")
    try:
        record = run_task(client, db, LLMProvider())
        assert record["status"] == "completed", record
        assert len(received) == 1
        assert "private@example.com" not in json.dumps(received)
        assert record["result"]["report"]["recommendations"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_context_respects_result_budget_and_excludes_preparing(jobs_client):
    client, db = jobs_client
    _, job = seed(client)
    client.post("/api/v1/applications", json={"job_id": job["id"]})
    other = client.post(
        "/api/v1/jobs", json={"title": "Backend Engineer", "company": "Another"}
    ).json()
    client.post(
        "/api/v1/applications",
        json={
            "job_id": other["id"],
            "status": "applied",
            "application_date": "2026-10-27",
            "application_channel": "manual",
        },
    )
    context = career_context(db, ActorContext(LOCAL_OWNER_ID, "agent"), {"max_results": 1})
    assert len(context["jobs"]) == 1 and len(context["applications"]) == 1
    assert context["applications"][0]["job_id"] == other["id"]
    assert context["coverage"]["applications_available"] == 1
    assert context["coverage"]["jobs_limit"] == 1
