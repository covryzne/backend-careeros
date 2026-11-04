"""Real PostgreSQL locks/indexes plus the real API and worker with fixture engine events."""

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.security import ActorContext, get_actor
from app.crawler.worker import execute
from app.db.session import get_db
from app.main import app
from app.models.candidate import User
from app.models.crawler import CrawlRun, CrawlWorkerSlot
from app.schemas.crawler import CrawlCreate
from app.services.crawler import runs as service

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Dedicated local PostgreSQL required"
)


@pytest.fixture
def pg():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    assert url.host in {"localhost", "127.0.0.1", "::1"} and url.database.startswith(
        "careeros_test_"
    )
    engine = create_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        state = db.get(CrawlWorkerSlot, 1)
        assert state.active_run_id is None
        state.capabilities = [
            {
                "platform": "linkedin",
                "status": "experimental",
                "date_filter": "portal_search_filter",
                "default_headless": True,
                "location_scope": "custom",
            }
        ]
        db.commit()
    yield factory
    with factory() as db:
        state = db.get(CrawlWorkerSlot, 1)
        if state.active_run_id:
            run = db.get(CrawlRun, state.active_run_id)
            run.status, run.finished_at = "failed", datetime.now(UTC)
            state.active_run_id = None
            db.commit()
    engine.dispose()


def actor(factory):
    result = ActorContext(uuid.uuid4())
    with factory() as db:
        db.add(User(id=result.owner_id))
        db.commit()
    return result


def payload(**values):
    return CrawlCreate(keywords=["AI Engineer"], platforms=["linkedin"], **values)


@pytest.mark.parametrize("same_key", [False, True])
def test_simultaneous_manual_enqueues_are_global_and_idempotent(pg, same_key):
    owners = [actor(pg), actor(pg)]
    if same_key:
        owners[1] = owners[0]
    barrier = Barrier(2)

    def start(index):
        with pg() as db:
            barrier.wait(timeout=10)
            try:
                run = service.enqueue(
                    db, owners[index], payload(), "same" if same_key else str(index)
                )
                db.commit()
                return str(run.id)
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(start, [0, 1]))
    if same_key:
        assert results[0] == results[1]
    else:
        assert sum(value == 409 for value in results) == 1
    with pg() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(CrawlRun)
                .where(CrawlRun.status.in_(["queued", "running"]))
            )
            == 1
        )


def test_two_workers_claim_once_and_do_not_steal_expired_execution(pg):
    owner = actor(pg)
    with pg() as db:
        run = service.enqueue(db, owner, payload(), "claim")
        run_id = run.id
        db.commit()
    barrier = Barrier(2)

    def claim(index):
        with pg() as db:
            barrier.wait(timeout=10)
            value = service.claim(db, f"worker-{index}", 30)
            db.commit()
            return value

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, [0, 1]))
    assert sum(value is not None for value in claims) == 1
    with pg() as db:
        run = db.get(CrawlRun, run_id)
        run.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert service.claim(db, "replacement", 30) is None
        with pytest.raises(RuntimeError):
            service.guarded(db, run_id, run.worker_id, run.fencing_token + 1)


class FixtureEngine:
    launches = 0

    def __init__(self):
        self.config = None

    async def start(self):
        type(self).launches += 1

    async def send(self, config):
        self.config = config
        assert config["request"]["max_pages"] == 5
        assert config["request"]["days"] == 7

    async def events(self):
        ref = {
            "external_id": "987654321",
            "title": "AI Engineer",
            "url": "https://www.linkedin.com/jobs/view/987654321/",
            "search_page": 1,
            "discovered_from": "https://id.linkedin.com/jobs/search",
        }
        yield {"type": "source_started", "platform": "linkedin"}
        yield {"type": "page", "platform": "linkedin", "references": [ref]}
        yield {"type": "detail_started", "platform": "linkedin", "external_id": ref["external_id"]}
        yield {
            "type": "job",
            "platform": "linkedin",
            "job": {
                **ref,
                "portal": "linkedin",
                "job_url": ref["url"],
                "company": "Fixture Company",
                "description": "Full public job description.",
                "requirements": None,
            },
        }
        # A later listing challenge must preserve the already committed job.
        yield {
            "type": "source_finished",
            "platform": "linkedin",
            "state": "partial",
            "stop_reason": "challenge_blocked",
        }

    async def stop(self):
        pass


def test_http_queue_worker_ingestion_and_jobs_read_use_local_postgres(pg):
    FixtureEngine.launches = 0
    owner = actor(pg)

    def database():
        with pg() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_actor] = lambda: owner
    try:
        with TestClient(app) as client:
            body = payload(max_pages=5).model_dump()
            response = client.post(
                "/api/v1/crawler/runs", json=body, headers={"Idempotency-Key": "pg-api"}
            )
            assert response.status_code == 202, response.text
            run_id = uuid.UUID(response.json()["run_id"])
            assert FixtureEngine.launches == 0  # HTTP acceptance never starts a browser.
            with pg() as db:
                claim = service.claim(db, "integration", 30)
                db.commit()
            asyncio.run(execute(*claim, "integration", factory=pg, engine_factory=FixtureEngine))
            result = client.get(f"/api/v1/crawler/runs/{run_id}").json()
            assert result["status"] == "partial" and result["jobs_new"] == 1
            assert result["sources"][0]["error"] == "challenge_blocked"
            items = client.get(f"/api/v1/crawler/runs/{run_id}/results").json()["items"]
            assert items[0]["status"] == "new" and items[0]["job_id"]
            job = client.get(f"/api/v1/jobs/{items[0]['job_id']}").json()
            assert job["status"] == "new" and job["description"] == "Full public job description."
            replay = client.post(
                "/api/v1/crawler/runs", json=body, headers={"Idempotency-Key": "pg-api"}
            )
            assert replay.json()["run_id"] == str(run_id)
            assert FixtureEngine.launches == 1
            with pg() as db:
                assert db.get(CrawlWorkerSlot, 1).active_run_id is None
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_actor, None)


def test_process_failure_and_unconfirmed_cleanup_retain_the_global_slot(pg):
    owner = actor(pg)
    with pg() as db:
        run = service.enqueue(db, owner, payload(), "cleanup")
        run_id = run.id
        db.commit()
        _, token = service.claim(db, "interrupted", 30)
        db.commit()

    class Broken(FixtureEngine):
        async def send(self, config):
            raise RuntimeError("engine failed")

        async def stop(self):
            raise RuntimeError("cannot prove process exit")

    asyncio.run(execute(run_id, token, "interrupted", factory=pg, engine_factory=Broken))
    with pg() as db:
        assert db.get(CrawlRun, run_id).status == "running"
        assert db.get(CrawlWorkerSlot, 1).active_run_id == run_id
        with pytest.raises(HTTPException) as conflict:
            service.enqueue(db, owner, payload(), "new")
        assert conflict.value.status_code == 409


def test_post_extraction_date_filter_and_progress_events_on_postgres(pg):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from app.models.crawler import CrawlRunEvent, CrawlRunItem

    owner = actor(pg)
    with pg() as db:
        db.get(CrawlWorkerSlot, 1).capabilities = [
            {
                "platform": "kalibrr",
                "status": "experimental",
                "date_filter": "latest_sort_only_no_age_filter",
                "default_headless": True,
                "location_scope": "Indonesia",
            }
        ]
        db.commit()
        run = service.enqueue(
            db,
            owner,
            CrawlCreate(keywords=["Data Engineer"], platforms=["kalibrr"], max_jobs_per_platform=3),
            "dates",
        )
        run_id = run.id
        today = run.queued_at.astimezone(ZoneInfo("Asia/Jakarta")).date()
        db.commit()
        _, token = service.claim(db, "dates-worker", 30)
        db.commit()

    class DateEngine(FixtureEngine):
        async def send(self, config):
            assert config["request"]["days"] == 7

        async def events(self):
            refs = [
                {
                    "external_id": str(900 + i),
                    "url": f"https://www.kalibrr.com/id-ID/c/example/jobs/{900 + i}/data-engineer",
                    "title": "Data Engineer",
                    "search_page": 1,
                    "discovered_from": "https://www.kalibrr.com/id-ID/home",
                }
                for i in range(3)
            ]
            yield {"type": "source_started", "platform": "kalibrr"}
            yield {"type": "page", "platform": "kalibrr", "references": refs, "page": 1}
            for ref, posted in zip(
                refs,
                [today.isoformat(), (today - timedelta(days=10)).isoformat(), None],
                strict=True,
            ):
                yield {
                    "type": "detail_started",
                    "platform": "kalibrr",
                    "external_id": ref["external_id"],
                }
                yield {
                    "type": "job",
                    "platform": "kalibrr",
                    "job": {
                        **ref,
                        "portal": "kalibrr",
                        "job_url": ref["url"],
                        "company": "Example",
                        "description": "Data pipelines",
                        "posted_at_raw": posted,
                    },
                }
            yield {
                "type": "source_finished",
                "platform": "kalibrr",
                "state": "completed",
                "stop_reason": "max_jobs",
            }

    asyncio.run(execute(run_id, token, "dates-worker", factory=pg, engine_factory=DateEngine))
    with pg() as db:
        result = service.run_data(db, db.get(CrawlRun, run_id))
        assert result["status"] == "completed" and result["jobs_new"] == 1
        assert result["jobs_filtered"] == 2 and result["jobs_date_unknown"] == 1
        states = db.scalars(select(CrawlRunItem.status).where(CrawlRunItem.run_id == run_id)).all()
        assert set(states) == {"new", "filtered_date", "date_unknown"}
        events = db.scalars(
            select(CrawlRunEvent)
            .where(CrawlRunEvent.run_id == run_id)
            .order_by(CrawlRunEvent.sequence_number)
        ).all()
        assert [e.sequence_number for e in events] == list(range(1, len(events) + 1))
        assert "Run selesai" in events[-1].message
