import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.security import ActorContext, get_actor
from app.main import app
from app.models.crawler import CrawlRun, CrawlWorkerSlot
from app.models.workflow import Job
from app.services import crawler_runs as service
from app.services.crawler_ingestion import apply_event

CAPS = [
    {
        "platform": "linkedin",
        "status": "experimental",
        "date_filter": "portal_search_filter",
        "default_headless": True,
        "location_scope": "custom",
    }
]


@pytest.fixture
def crawler(jobs_client):
    client, db = jobs_client
    db.add(CrawlWorkerSlot(id=1, capabilities=CAPS))
    db.commit()
    return client, db


def create(client, key="one", **params):
    return client.post(
        "/api/v1/crawler/runs",
        headers={"Idempotency-Key": key},
        json={"keywords": ["AI Engineer"], "platforms": ["linkedin"], **params},
    )


def test_manual_202_replay_conflict_and_snapshot(crawler):
    client, db = crawler
    first = create(client, max_pages=5, days=7, max_jobs_per_platform=50)
    assert first.status_code == 202, first.text
    run = first.json()
    assert run["status"] == "queued" and run["trigger_type"] == "manual"
    assert run["config"]["max_pages"] == 5
    assert run["config"]["days"] == 7
    assert (
        create(client, max_pages=5, days=7, max_jobs_per_platform=50).json()["run_id"]
        == run["run_id"]
    )
    assert create(client, key="two").status_code == 409
    assert create(client, days=14).status_code == 409
    assert db.scalar(select(func.count()).select_from(CrawlRun)) == 1
    assert client.get("/api/v1/crawler/runs").json()["total"] == 1
    assert (
        client.get(f"/api/v1/crawler/runs/{run['run_id']}/sources").json()[0]["status"] == "queued"
    )
    assert client.get(f"/api/v1/crawler/runs/{run['run_id']}/results").json()["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"days": 31},
        {"max_pages": 21},
        {"max_jobs_per_platform": 201},
        {"keywords": [" "]},
        {"platforms": ["unknown"]},
        {"engine": "browser-use"},
        {"owner_id": str(uuid.uuid4())},
    ],
)
def test_invalid_parameters_never_enqueue(crawler, params):
    client, db = crawler
    assert create(client, **params).status_code == 422
    assert db.scalar(select(func.count()).select_from(CrawlRun)) == 0


def test_required_key_and_owner_isolation(crawler):
    client, _db = crawler
    assert (
        client.post(
            "/api/v1/crawler/runs", json={"keywords": ["AI"], "platforms": ["linkedin"]}
        ).status_code
        == 422
    )
    run_id = create(client).json()["run_id"]
    app.dependency_overrides[get_actor] = lambda: ActorContext(
        uuid.UUID("00000000-0000-0000-0000-000000000002")
    )
    try:
        assert client.get(f"/api/v1/crawler/runs/{run_id}").status_code == 404
        assert client.get(f"/api/v1/crawler/runs/{run_id}/results").status_code == 404
        assert client.get("/api/v1/crawler/capabilities").json()["active_run_id"] is None
        assert client.get("/api/v1/crawler/runs").json()["total"] == 0
    finally:
        app.dependency_overrides.pop(get_actor, None)


def events():
    ref = {
        "external_id": "123456",
        "url": "https://www.linkedin.com/jobs/view/123456/",
        "title": "AI Engineer",
        "search_page": 1,
        "discovered_from": "https://id.linkedin.com/jobs/search?keywords=AI",
    }
    yield {"type": "source_started", "platform": "linkedin"}
    yield {"type": "page", "platform": "linkedin", "references": [ref]}
    yield {"type": "detail_started", "platform": "linkedin", "external_id": ref["external_id"]}
    yield {
        "type": "job",
        "platform": "linkedin",
        "job": dict(
            portal="linkedin",
            **ref,
            job_url=ref["url"],
            company="Example",
            description="Build AI services.",
            requirements=None,
        ),
    }
    yield {
        "type": "source_finished",
        "platform": "linkedin",
        "state": "completed",
        "stop_reason": "max_pages",
    }


def execute_events(db, run_id, worker="worker"):
    claimed = service.claim(db, worker, 30)
    assert claimed and claimed[0] == run_id
    db.commit()
    for event in events():
        run, _ = service.guarded(db, run_id, worker, claimed[1])
        apply_event(db, run, "linkedin", "AI Engineer", event)
        db.commit()
    service.finish(db, run_id, worker, claimed[1])
    db.commit()


def test_ingestion_repeated_crawl_preserves_personal_state_and_tombstone(crawler):
    client, db = crawler
    run_id = uuid.UUID(create(client).json()["run_id"])
    execute_events(db, run_id)
    job = db.scalar(select(Job))
    assert job.status == "new" and job.external_job_id == "123456"
    assert client.get("/api/v1/jobs").json()["total"] == 1
    job.status, job.notes = "saved", "User notes"
    db.commit()
    again = uuid.UUID(create(client, key="again").json()["run_id"])
    execute_events(db, again)
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert job.status == "saved" and job.notes == "User notes"
    assert service.run_data(db, db.get(CrawlRun, again))["jobs_duplicate"] == 1
    job.deleted_at = datetime.now(UTC)
    db.commit()
    third = uuid.UUID(create(client, key="third").json()["run_id"])
    execute_events(db, third)
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert job.deleted_at is not None
    assert client.get("/api/v1/jobs").json()["total"] == 0


def test_stale_lease_is_not_reclaimed_and_fencing_rejects_writes(crawler):
    client, db = crawler
    run_id = uuid.UUID(create(client).json()["run_id"])
    _, token = service.claim(db, "first", 30)
    db.commit()
    assert service.claim(db, "second", 30) is None
    db.get(CrawlRun, run_id).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert service.claim(db, "second", 30) is None
    with pytest.raises(RuntimeError, match="ownership/lease"):
        service.heartbeat(db, run_id, "first", token, 30)
    db.rollback()
    assert create(client, key="new").status_code == 409


def test_worker_waiting_notice_distinguishes_idle_running_and_expired(crawler):
    from app.crawler.worker import waiting_notice

    client, db = crawler
    assert "Idle:" in waiting_notice(db)
    run_id = uuid.UUID(create(client).json()["run_id"])
    service.claim(db, "first", 30)
    db.commit()
    assert "another execution" in waiting_notice(db)
    db.get(CrawlRun, run_id).lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert "safe operator recovery" in waiting_notice(db)
    assert service.claim(db, "second", 30) is None


def test_empty_card_title_uses_validated_detail_in_results_and_log(crawler):
    client, db = crawler
    for key, expected in (("first", "new"), ("second", "duplicate")):
        run_id = uuid.UUID(create(client, key=key).json()["run_id"])
        _, token = service.claim(db, "first", 30)
        db.commit()
        run = db.get(CrawlRun, run_id)
        for event in [
            {"type": "browser_ready"},
            {"type": "listing_started", "page": 1},
            *events(),
        ]:
            if event["type"] == "page":
                event["references"][0]["title"] = ""
            apply_event(db, run, "linkedin", "AI Engineer", event)
            db.commit()
        service.finish(db, run_id, "first", token)
        db.commit()
        item = client.get(f"/api/v1/crawler/runs/{run_id}/results").json()["items"][0]
        assert item["title"] == "AI Engineer" and item["status"] == expected
        messages = [
            e["message"]
            for e in client.get(f"/api/v1/crawler/runs/{run_id}/events").json()["items"]
        ]
        assert any("Ditemukan: ID 123456" in m for m in messages)
        assert any("AI Engineer" in m for m in messages)


def test_interrupted_run_logs_reason_and_preserves_accepted_jobs(crawler):
    client, db = crawler
    run_id = uuid.UUID(create(client).json()["run_id"])
    _, token = service.claim(db, "first", 30)
    db.commit()
    run = db.get(CrawlRun, run_id)
    for event in list(events())[:-1]:
        apply_event(db, run, "linkedin", "AI Engineer", event)
        db.commit()
    service.finish(db, run_id, "first", token, "WORKER_INTERRUPTED")
    db.commit()
    assert run.status == "partial"
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert (
        "WORKER_INTERRUPTED"
        in client.get(f"/api/v1/crawler/runs/{run_id}/events").json()["items"][-1]["message"]
    )


def test_worker_pipeline_consumes_events_and_releases_slot(crawler):
    from app.crawler.worker import execute

    client, db = crawler
    run_id = uuid.UUID(create(client).json()["run_id"])
    _, token = service.claim(db, "first", 30)
    db.commit()

    class Factory:
        def __enter__(self):
            return db

        def __exit__(self, *args):
            if args[0]:
                db.rollback()

    class Engine:
        async def start(self):
            return None

        async def send(self, config):
            assert config["request"]["days"] == 7
            assert config["request"]["max_pages"] == 2

        async def events(self):
            for event in events():
                yield event

        async def stop(self):
            pass

    asyncio.run(execute(run_id, token, "first", factory=Factory, engine_factory=Engine))
    assert db.get(CrawlRun, run_id).status == "completed"
    assert db.get(CrawlWorkerSlot, 1).active_run_id is None
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_manual_job_deduplicates_crawler_url_tracking_variants(crawler):
    client, db = crawler
    run_id = uuid.UUID(create(client).json()["run_id"])
    execute_events(db, run_id)
    response = client.post(
        "/api/v1/jobs",
        json={
            "title": "Manual",
            "company": "Example",
            "original_url": "https://id.linkedin.com/jobs/view/ai-engineer-at-company-123456?utm_source=manual",
        },
    )
    assert response.status_code == 409
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_missing_company_is_rejected_without_inventing_job_data(crawler):
    from app.models.crawler import CrawlRunSource

    client, db = crawler
    run_id = uuid.UUID(create(client).json()["run_id"])
    _, token = service.claim(db, "first", 30)
    db.commit()
    for event in events():
        if event["type"] == "job":
            event["job"]["company"] = None
        run, _ = service.guarded(db, run_id, "first", token)
        apply_event(db, run, "linkedin", "AI Engineer", event)
        db.commit()
    service.finish(db, run_id, "first", token)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Job)) == 0
    source = db.scalar(select(CrawlRunSource).where(CrawlRunSource.run_id == run_id))
    assert source.jobs_rejected == 1 and db.get(CrawlRun, run_id).status == "failed"


def test_multiple_keywords_share_one_per_platform_budget(crawler):
    from app.crawler.worker import execute

    client, db = crawler
    run_id = uuid.UUID(
        create(client, keywords=["AI Engineer", "ML Engineer"], max_jobs_per_platform=1).json()[
            "run_id"
        ]
    )
    _, token = service.claim(db, "first", 30)
    db.commit()
    calls = []

    class Factory:
        def __enter__(self):
            return db

        def __exit__(self, *args):
            if args[0]:
                db.rollback()

    class Engine:
        async def start(self):
            calls.append("launch")

        async def send(self, config):
            assert config["request"]["max_jobs"] == 1

        async def events(self):
            for event in events():
                yield event

        async def stop(self):
            pass

    asyncio.run(execute(run_id, token, "first", factory=Factory, engine_factory=Engine))
    assert calls == ["launch"]
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_generic_manual_url_identity_preserves_non_tracking_query_parameters(crawler):
    client, _db = crawler
    for value in ("one", "two"):
        response = client.post(
            "/api/v1/jobs",
            json={
                "title": "Role",
                "company": "Example",
                "original_url": f"https://example.test/jobs?source={value}",
            },
        )
        assert response.status_code == 201
    duplicate = client.post(
        "/api/v1/jobs",
        json={
            "title": "Role",
            "company": "Example",
            "original_url": "https://example.test/jobs?source=one&utm_source=newsletter",
        },
    )
    assert duplicate.status_code == 409
