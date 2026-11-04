import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.candidate import User
from app.models.crawler import CrawlRun, CrawlRunSource
from app.models.crawler_stats import CrawlerDailyStats, CrawlerStatsPublication
from app.services.crawler_stats import _publish, refresh

COVERAGE = date(2026, 10, 1)


@pytest.fixture
def stats_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def owner(engine):
    owner_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(User(id=owner_id))
        db.commit()
    return owner_id


def run(engine, owner_id, *, started_at=None, status="completed", platforms=None):
    run_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(
            CrawlRun(
                id=run_id,
                owner_id=owner_id,
                idempotency_key=str(run_id),
                payload_hash="test",
                config={},
                status=status,
                started_at=started_at,
                finished_at=datetime(2026, 11, 5, tzinfo=UTC) if status != "running" else None,
            )
        )
        db.flush()
        for platform, counts in (platforms or {"linkedin": {}}).items():
            db.add(CrawlRunSource(run_id=run_id, platform=platform, **counts))
        db.commit()
    return run_id


def publish(engine, coverage_start=COVERAGE):
    with engine.begin() as conn:
        return _publish(conn, coverage_start)


def test_backfill_wib_owner_isolation_and_multi_platform_counts(stats_engine):
    a, b = owner(stats_engine), owner(stats_engine)
    run(
        stats_engine,
        a,
        started_at=datetime(2026, 11, 3, 18, tzinfo=UTC),
        platforms={
            "linkedin": {"jobs_found": 20, "jobs_new": 10, "jobs_duplicate": 5},
            "jobstreet": {"jobs_found": 8, "jobs_new": 6, "jobs_duplicate": 2},
        },
    )
    run(
        stats_engine,
        a,
        started_at=datetime(2026, 11, 3, 16, 59, tzinfo=UTC),
        status="partial",
        platforms={"linkedin": {"jobs_new": 3, "jobs_rejected": 1}},
    )
    run(
        stats_engine,
        b,
        started_at=datetime(2026, 11, 3, 18, tzinfo=UTC),
        platforms={"linkedin": {"jobs_new": 99}},
    )
    run(stats_engine, a, status="queued")
    run(stats_engine, a, started_at=datetime(2026, 9, 30, 12, tzinfo=UTC))

    result = publish(stats_engine)
    assert result.owners == 2 and result.buckets == 4
    with Session(stats_engine) as db:
        rows = db.scalars(select(CrawlerDailyStats).where(CrawlerDailyStats.owner_id == a)).all()
        values = {(row.activity_date, row.platform): row for row in rows}
        linkedin = values[date(2026, 11, 4), "linkedin"]
        assert linkedin.jobs_crawled_successfully == 15
        assert linkedin.runs_started == linkedin.runs_completed == 1
        assert values[date(2026, 11, 4), "jobstreet"].runs_started == 1
        assert values[date(2026, 11, 3), "linkedin"].runs_partial == 1
        assert all(row.batch_id == result.batch_id for row in rows)
        # Cross-midnight completion stays attributed to November 4, not November 5.
        assert len(rows) == 3


def test_repeated_refresh_replaces_totals_and_reconciles_old_running_bucket(stats_engine):
    a = owner(stats_engine)
    run_id = run(
        stats_engine,
        a,
        started_at=datetime(2026, 10, 3, tzinfo=UTC),
        status="running",
        platforms={"linkedin": {"jobs_new": 2}},
    )
    first = publish(stats_engine)
    second = publish(stats_engine)
    assert first.batch_id != second.batch_id
    with Session(stats_engine) as db:
        row = db.scalar(select(CrawlerDailyStats))
        assert row.jobs_new == 2 and row.runs_running == 1
        source = db.scalar(select(CrawlRunSource).where(CrawlRunSource.run_id == run_id))
        source.jobs_new, source.jobs_filtered, source.jobs_date_unknown = 7, 3, 2
        db.get(CrawlRun, run_id).status = "partial"
        db.commit()
    publish(stats_engine)
    with Session(stats_engine) as db:
        row = db.scalar(select(CrawlerDailyStats))
        assert (row.jobs_new, row.jobs_filtered, row.jobs_date_unknown) == (7, 3, 2)
        assert (row.runs_started, row.runs_running, row.runs_partial) == (1, 0, 1)
        assert db.get(CrawlRun, run_id).status == "partial"


def test_correction_removes_obsolete_bucket(stats_engine):
    a = owner(stats_engine)
    run_id = run(stats_engine, a, started_at=datetime(2026, 10, 3, tzinfo=UTC))
    publish(stats_engine)
    with Session(stats_engine) as db:
        db.get(CrawlRun, run_id).started_at = datetime(2026, 10, 4, tzinfo=UTC)
        db.commit()
    publish(stats_engine)
    with Session(stats_engine) as db:
        assert list(db.scalars(select(CrawlerDailyStats.activity_date))) == [date(2026, 10, 4)]


@pytest.mark.parametrize("status", ["completed", "partial", "failed", "cancelled", "running"])
def test_status_buckets_include_zero_and_failed_results(stats_engine, status):
    a = owner(stats_engine)
    run(stats_engine, a, started_at=datetime(2026, 10, 3, tzinfo=UTC), status=status)
    publish(stats_engine)
    with Session(stats_engine) as db:
        row = db.scalar(select(CrawlerDailyStats))
        assert row.runs_started == 1 and getattr(row, f"runs_{status}") == 1
        assert row.jobs_found == 0


def test_empty_publication_and_explicit_immutable_coverage(stats_engine):
    a = owner(stats_engine)
    publish(stats_engine)
    with Session(stats_engine) as db:
        metadata = db.get(CrawlerStatsPublication, a)
        batch_id = metadata.batch_id
        assert metadata.coverage_start == COVERAGE
        assert metadata.timezone == "Asia/Jakarta" and metadata.definition_version == 2
        assert db.scalar(select(CrawlerDailyStats)) is None
    with pytest.raises(ValueError, match="immutable"):
        publish(stats_engine, date(2026, 10, 2))
    with pytest.raises(ValueError, match="future"):
        publish(stats_engine, date(9999, 1, 1))
    with Session(stats_engine) as db:
        assert db.get(CrawlerStatsPublication, a).batch_id == batch_id


def test_interrupted_publication_preserves_complete_previous_batch(stats_engine):
    a = owner(stats_engine)
    run(
        stats_engine,
        a,
        started_at=datetime(2026, 10, 3, tzinfo=UTC),
        platforms={"linkedin": {"jobs_new": 4}},
    )
    old = publish(stats_engine)

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO crawler_daily_stats"):
            raise RuntimeError("Simulated interrupted publication")

    event.listen(stats_engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            publish(stats_engine)
    finally:
        event.remove(stats_engine, "before_cursor_execute", fail_insert)
    with Session(stats_engine) as db:
        assert db.get(CrawlerStatsPublication, a).batch_id == old.batch_id
        row = db.scalar(select(CrawlerDailyStats))
        assert row.jobs_new == 4 and row.batch_id == old.batch_id


def test_public_runner_rejects_non_postgres(stats_engine):
    with pytest.raises(ValueError, match="PostgreSQL"):
        refresh(stats_engine, coverage_start=COVERAGE)


def test_cli_retries_database_failure_at_configured_interval(monkeypatch, caplog):
    from sqlalchemy.exc import SQLAlchemyError

    from app.crawler import stats

    calls, intervals = [], []

    def refresh_once_then_interrupt(engine, *, coverage_start):
        calls.append(coverage_start)
        if len(calls) == 1:
            raise SQLAlchemyError("sensitive connection details")
        raise KeyboardInterrupt

    class Timer:
        def wait(self, seconds):
            intervals.append(seconds)

    monkeypatch.setattr(stats, "refresh", refresh_once_then_interrupt)
    monkeypatch.setattr(stats.threading, "Event", Timer)
    assert stats.main(["--coverage-start", "2026-10-01"]) == 0
    assert calls == [COVERAGE, COVERAGE] and intervals == [300]
    assert "sensitive connection details" not in caplog.text


def test_cli_once_failure_and_invalid_configuration_exit_codes(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app.crawler import stats

    def fail(engine, *, coverage_start):
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(stats, "refresh", fail)
    assert stats.main(["--coverage-start", "2026-10-01", "--once"]) == 1

    def invalid(engine, *, coverage_start):
        raise ValueError("coverage_start is immutable")

    monkeypatch.setattr(stats, "refresh", invalid)
    assert stats.main(["--coverage-start", "2026-10-01", "--once"]) == 2
    with pytest.raises(SystemExit) as exc:
        stats.main(["--coverage-start", "2026-10-01", "--interval-seconds", "0"])
    assert exc.value.code == 2
