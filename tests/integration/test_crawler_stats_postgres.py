"""Real publisher serialization, MVCC snapshots and rollback on disposable PostgreSQL."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event

import pytest
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.models.candidate import User
from app.models.crawler import CrawlRun, CrawlRunSource
from app.models.crawler_stats import CrawlerDailyStats, CrawlerStatsPublication
from app.services.crawler_stats import PUBLISH_LOCK, refresh

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Dedicated local PostgreSQL required"
)
COVERAGE = date(2026, 10, 1)


@pytest.fixture
def pg_stats():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database.startswith("careeros_test_")
    schema = "stats_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        owner_id, run_id = uuid.uuid4(), uuid.uuid4()
        with Session(engine) as db:
            db.add(User(id=owner_id))
            db.flush()
            db.add(
                CrawlRun(
                    id=run_id,
                    owner_id=owner_id,
                    idempotency_key="stats",
                    payload_hash="stats",
                    config={},
                    status="partial",
                    started_at=datetime(2026, 10, 3, 18, tzinfo=UTC),
                    finished_at=datetime(2026, 10, 5, tzinfo=UTC),
                )
            )
            db.flush()
            db.add_all(
                [
                    CrawlRunSource(
                        run_id=run_id, platform="linkedin", jobs_new=7, jobs_duplicate=2
                    ),
                    CrawlRunSource(run_id=run_id, platform="jobstreet", jobs_new=3),
                ]
            )
            db.commit()
        yield engine, owner_id, run_id
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_postgres_publication_and_database_constraints(pg_stats):
    engine, owner_id, _ = pg_stats
    first = refresh(engine, coverage_start=COVERAGE)
    second = refresh(engine, coverage_start=COVERAGE)
    assert first.batch_id != second.batch_id
    with Session(engine) as db:
        publication = db.get(CrawlerStatsPublication, owner_id)
        rows = db.scalars(select(CrawlerDailyStats)).all()
        assert publication.batch_id == second.batch_id
        assert len(rows) == 2
        assert sum(row.jobs_crawled_successfully for row in rows) == 12
        assert all(row.activity_date == date(2026, 10, 4) for row in rows)
        assert all(row.runs_partial == 1 for row in rows)
        with pytest.raises(IntegrityError):
            db.execute(update(CrawlerDailyStats).values(batch_id=uuid.uuid4()))
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(update(CrawlerDailyStats).values(jobs_new=-1))


def test_second_publisher_skips_and_cannot_overwrite_snapshot(pg_stats):
    engine, _, _ = pg_stats
    entered, release = Event(), Event()

    def hold(conn, cursor, statement, parameters, context, executemany):
        if "transaction_timestamp()" in statement:
            entered.set()
            assert release.wait(timeout=15)

    event.listen(engine, "before_cursor_execute", hold)
    try:
        with ThreadPoolExecutor(1) as pool:
            first = pool.submit(refresh, engine, coverage_start=COVERAGE)
            try:
                assert entered.wait(timeout=10)
                assert refresh(engine, coverage_start=COVERAGE) is None
            finally:
                release.set()
            result = first.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", hold)
    with Session(engine) as db:
        assert set(db.scalars(select(CrawlerDailyStats.batch_id))) == {result.batch_id}


def test_repeatable_snapshot_excludes_mid_refresh_source_update(pg_stats):
    engine, _, run_id = pg_stats
    updated = False

    def update_after_snapshot(conn, cursor, statement, parameters, context, executemany):
        nonlocal updated
        if statement.startswith("SELECT crawl_runs.owner_id") and not updated:
            updated = True
            with engine.begin() as writer:
                writer.execute(
                    update(CrawlRunSource)
                    .where(CrawlRunSource.run_id == run_id, CrawlRunSource.platform == "linkedin")
                    .values(jobs_new=100)
                )

    event.listen(engine, "before_cursor_execute", update_after_snapshot)
    try:
        refresh(engine, coverage_start=COVERAGE)
    finally:
        event.remove(engine, "before_cursor_execute", update_after_snapshot)
    assert updated
    with Session(engine) as db:
        assert db.scalar(select(func.sum(CrawlerDailyStats.jobs_new))) == 10
        assert db.scalar(select(func.sum(CrawlRunSource.jobs_new))) == 103
    refresh(engine, coverage_start=COVERAGE)
    with Session(engine) as db:
        assert db.scalar(select(func.sum(CrawlerDailyStats.jobs_new))) == 103


def test_failed_publish_preserves_batch_and_releases_session_lock(pg_stats):
    engine, owner_id, _ = pg_stats
    old = refresh(engine, coverage_start=COVERAGE)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO crawler_daily_stats"):
            raise RuntimeError("interrupted")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            refresh(engine, coverage_start=COVERAGE)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with Session(engine) as db:
        assert db.get(CrawlerStatsPublication, owner_id).batch_id == old.batch_id
        assert set(db.scalars(select(CrawlerDailyStats.batch_id))) == {old.batch_id}
    # A different physical connection must be able to acquire the lock.
    with engine.connect() as a, engine.connect() as b:
        assert a.scalar(select(func.pg_try_advisory_lock(PUBLISH_LOCK)))
        assert not b.scalar(select(func.pg_try_advisory_lock(PUBLISH_LOCK)))
        assert a.scalar(select(func.pg_advisory_unlock(PUBLISH_LOCK)))
    assert refresh(engine, coverage_start=COVERAGE) is not None


def test_readers_see_old_complete_batch_until_publication_commits(pg_stats):
    engine, owner_id, _ = pg_stats
    old = refresh(engine, coverage_start=COVERAGE)
    entered, release = Event(), Event()

    def hold_after_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE crawler_stats_publications"):
            entered.set()
            assert release.wait(timeout=15)

    event.listen(engine, "before_cursor_execute", hold_after_delete)
    try:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(refresh, engine, coverage_start=COVERAGE)
            try:
                assert entered.wait(timeout=10)
                with Session(engine) as db:
                    assert db.get(CrawlerStatsPublication, owner_id).batch_id == old.batch_id
                    assert list(db.scalars(select(CrawlerDailyStats.batch_id))) == [
                        old.batch_id,
                        old.batch_id,
                    ]
            finally:
                release.set()
            new = pending.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", hold_after_delete)
    with Session(engine) as db:
        assert db.get(CrawlerStatsPublication, owner_id).batch_id == new.batch_id
        assert set(db.scalars(select(CrawlerDailyStats.batch_id))) == {new.batch_id}
