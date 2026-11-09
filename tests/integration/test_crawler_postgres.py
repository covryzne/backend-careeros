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
        assert (
            db.scalar(
                select(func.count())
                .select_from(CrawlRun)
                .where(CrawlRun.status.in_(["queued", "running"]))
            )
            == 0
        )
        service.configure_slots(db, 1)
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
        for run in db.scalars(select(CrawlRun).where(CrawlRun.status.in_(["queued", "running"]))):
            run.status, run.finished_at = "failed", datetime.now(UTC)
        for state in db.scalars(select(CrawlWorkerSlot)):
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
        assert len(set(results)) == 2 and all(isinstance(value, str) for value in results)
    with pg() as db:
        assert db.scalar(
            select(func.count())
            .select_from(CrawlRun)
            .where(CrawlRun.status.in_(["queued", "running"]))
        ) == (1 if same_key else 2)


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


def test_capacity_two_executes_two_owners_concurrently_and_third_queues(pg):
    from app.models.workflow import Job

    owners = [actor(pg) for _ in range(3)]
    with pg() as db:
        service.configure_slots(db, 2)
        ids = [service.enqueue(db, owner, payload(max_pages=5), "parallel").id for owner in owners]
        db.commit()
    barrier = Barrier(2)

    def claim(index):
        with pg() as db:
            barrier.wait(timeout=10)
            result = service.claim(db, f"parallel-{index}", 30)
            db.commit()
            return result

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, [0, 1]))
    assert {value[0] for value in claims} == set(ids[:2])
    with pg() as db:
        assert service.claim(db, "parallel-third", 30) is None
        assert db.get(CrawlRun, ids[2]).status == "queued"
    executing = Barrier(2)

    class ParallelEngine(FixtureEngine):
        async def events(self):
            # Neither execution can finish until both are running their engines.
            await asyncio.to_thread(executing.wait, 10)
            async for event in super().events():
                if event["type"] == "source_finished":
                    event = {**event, "state": "completed", "stop_reason": "max_pages"}
                yield event

    def execute_one(index):
        asyncio.run(
            execute(*claims[index], f"parallel-{index}", factory=pg, engine_factory=ParallelEngine)
        )

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(execute_one, [0, 1]))
    with pg() as db:
        assert all(db.get(CrawlRun, run_id).status == "completed" for run_id in ids[:2])
        jobs = list(
            db.scalars(select(Job).where(Job.owner_id.in_([owner.owner_id for owner in owners])))
        )
        assert {job.owner_id for job in jobs} == {owners[0].owner_id, owners[1].owner_id}
        assert len(jobs) == 2
        assert service.claim(db, "parallel-third", 30)[0] == ids[2]
        db.commit()


def test_two_dispatchers_publish_one_occurrence_and_manual_reuses_it(pg):
    from app.models.crawler import CrawlScheduleOccurrence
    from app.schemas.crawler import ScheduleSave
    from app.services.crawler import schedules

    owner = actor(pg)
    now = datetime(2026, 11, 8, 22, 0, tzinfo=UTC)
    with pg() as db:
        schedule = schedules.save(
            db,
            owner,
            ScheduleSave(
                config=payload(max_pages=5), hours=[5, 17], enabled=True, expected_version=0
            ),
            now=now - timedelta(minutes=1),
        )
        schedule_id = schedule["id"]
        db.commit()
    barrier = Barrier(2)

    def dispatch(index):
        with pg() as db:
            barrier.wait(timeout=10)
            value = schedules.dispatch_one(db, schedule_id, owner.owner_id, now=now)
            db.commit()
            return value

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(dispatch, [0, 1]))
    assert outcomes.count("enqueued") == 1 and outcomes.count(None) == 1
    with pg() as db:
        scheduled = service.active_for_owner(db, owner.owner_id)
        assert service.enqueue(db, owner, payload(), "manual-after-schedule").id == scheduled.id
        assert (
            db.scalar(
                select(func.count())
                .select_from(CrawlScheduleOccurrence)
                .where(CrawlScheduleOccurrence.schedule_id == schedule_id)
            )
            == 1
        )
        db.commit()
        claimed = service.claim(db, "scheduled-worker", 30)
        db.commit()
    asyncio.run(execute(*claimed, "scheduled-worker", factory=pg, engine_factory=FixtureEngine))
    with pg() as db:
        result = db.get(CrawlRun, claimed[0])
        assert result.trigger_type == "scheduled" and result.status == "partial"
        assert service.run_data(db, result)["jobs_new"] == 1


def test_concurrent_retry_requests_make_one_attempt(pg):
    from app.models.crawler import CrawlRunAttempt

    owner = actor(pg)
    with pg() as db:
        run = service.enqueue(db, owner, payload(), "failed-run")
        db.commit()
        service.claim(db, "failed-worker", 30)
        service.finish(db, run.id, "failed-worker", run.fencing_token, "fixture failure")
        db.commit()
        run_id = run.id
    barrier = Barrier(2)

    def retry(index):
        with pg() as db:
            barrier.wait(timeout=10)
            result = service.retry(db, owner, run_id, "retry-once")
            db.commit()
            return result.id

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(retry, [0, 1])) == [run_id, run_id]
    with pg() as db:
        assert db.get(CrawlRun, run_id).attempt_no == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(CrawlRunAttempt)
                .where(CrawlRunAttempt.run_id == run_id)
            )
            == 2
        )


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
        assert service.enqueue(db, owner, payload(), "new").id == run_id


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
