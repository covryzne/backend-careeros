"""Rebuild stored activity summaries without touching operational crawler state."""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import Date, case, cast, delete, func, insert, select, text, update
from sqlalchemy.engine import Connection, Engine

from app.models import foundation  # noqa: F401 - register profile document relationships for CLI
from app.models.candidate import User
from app.models.crawler import CrawlRun, CrawlRunSource
from app.models.crawler_stats import (
    RUN_COUNTERS,
    SOURCE_COUNTERS,
    CrawlerDailyRunStats,
    CrawlerDailyStats,
    CrawlerStatsPublication,
)

DEFINITION_VERSION = 2
TIMEZONE = "Asia/Jakarta"
JAKARTA = ZoneInfo(TIMEZONE)
# Session-level lock survives the short lock-acquisition transaction. The source
# snapshot starts only AFTER this lock is acquired, avoiding stale publishers.
PUBLISH_LOCK = 0x4341524553544154


@dataclass(frozen=True)
class RefreshResult:
    batch_id: uuid.UUID
    owners: int
    buckets: int
    source_snapshot_at: datetime


def _publish(conn: Connection, coverage_start: date) -> RefreshResult:
    """Caller owns the transaction and publisher lock; no commits happen here."""
    if conn.dialect.name == "postgresql":
        snapshot_at = conn.scalar(text("SELECT transaction_timestamp()"))
        day = cast(func.timezone(TIMEZONE, CrawlRun.started_at), Date)
    else:
        # SQLite is only used by isolated unit tests, never by the public runner.
        snapshot_at = datetime.now(UTC)
        day = func.date(CrawlRun.started_at, "+7 hours")
    if coverage_start > snapshot_at.astimezone(JAKARTA).date():
        raise ValueError("coverage_start cannot be in the future")

    publications = CrawlerStatsPublication.__table__
    daily = CrawlerDailyStats.__table__
    owners = list(conn.scalars(select(User.id)))
    existing = list(conn.execute(select(publications)).mappings())
    if any(row["coverage_start"] != coverage_start for row in existing):
        raise ValueError("coverage_start is immutable; use the original declared coverage date")

    # Only started runs participate. This includes persisted progress and all
    # terminal statuses, attributed to start day even if completion crosses midnight.
    start_utc = datetime.combine(coverage_start, time.min, tzinfo=JAKARTA).astimezone(UTC)
    query = (
        select(
            CrawlRun.owner_id,
            day.label("activity_date"),
            CrawlRunSource.platform,
            func.count().label("runs_started"),
            *(
                func.sum(case((CrawlRun.status == name.removeprefix("runs_"), 1), else_=0)).label(
                    name
                )
                for name in RUN_COUNTERS
                if name != "runs_started"
            ),
            *(func.sum(getattr(CrawlRunSource, name)).label(name) for name in SOURCE_COUNTERS),
        )
        .join(CrawlRunSource, CrawlRunSource.run_id == CrawlRun.id)
        .where(CrawlRun.started_at >= start_utc, CrawlRun.started_at <= snapshot_at)
        .group_by(CrawlRun.owner_id, day, CrawlRunSource.platform)
    )
    buckets = [dict(row) for row in conn.execute(query).mappings()]
    run_query = (
        select(
            CrawlRun.owner_id,
            day.label("activity_date"),
            func.count().label("runs_started"),
            *(
                func.sum(case((CrawlRun.status == name.removeprefix("runs_"), 1), else_=0)).label(
                    name
                )
                for name in RUN_COUNTERS
                if name != "runs_started"
            ),
        )
        .where(CrawlRun.started_at >= start_utc, CrawlRun.started_at <= snapshot_at)
        .group_by(CrawlRun.owner_id, day)
    )
    run_buckets = [dict(row) for row in conn.execute(run_query).mappings()]
    batch_id = uuid.uuid4()
    published_at = datetime.now(UTC)

    # All rows and all owners' metadata switch in one commit. A reader requiring
    # multiple statements must use a consistent transaction / match batch IDs.
    conn.execute(delete(daily))
    conn.execute(delete(CrawlerDailyRunStats.__table__))
    known = {row["owner_id"] for row in existing}
    for owner_id in owners:
        metadata = {
            "batch_id": batch_id,
            "definition_version": DEFINITION_VERSION,
            "timezone": TIMEZONE,
            "coverage_start": coverage_start,
            "source_snapshot_at": snapshot_at,
            "published_at": published_at,
        }
        if owner_id in known:
            conn.execute(
                update(publications).where(publications.c.owner_id == owner_id).values(**metadata)
            )
        else:
            conn.execute(insert(publications).values(owner_id=owner_id, **metadata))

    for row in [*buckets, *run_buckets]:
        if isinstance(row["activity_date"], str):
            row["activity_date"] = date.fromisoformat(row["activity_date"])
        row.update(
            batch_id=batch_id, definition_version=DEFINITION_VERSION, updated_at=published_at
        )
    # Bound DBAPI parameter batches while retaining a single atomic publication.
    for offset in range(0, len(buckets), 1000):
        conn.execute(insert(daily), buckets[offset : offset + 1000])
    for offset in range(0, len(run_buckets), 1000):
        conn.execute(insert(CrawlerDailyRunStats.__table__), run_buckets[offset : offset + 1000])
    return RefreshResult(batch_id, len(owners), len(buckets), snapshot_at)


def refresh(engine: Engine, *, coverage_start: date) -> RefreshResult | None:
    """Backfill/refresh every bucket; None means another publisher owns the lock.

    coverage_start is an operator assertion of retained, complete source history.
    It is deliberately not inferred from the earliest observed run.
    """
    if engine.dialect.name != "postgresql":
        raise ValueError("Crawler statistics publication requires PostgreSQL")
    with engine.connect() as conn:
        locked = conn.scalar(select(func.pg_try_advisory_lock(PUBLISH_LOCK)))
        try:
            conn.commit()
            if not locked:
                return None
            conn = conn.execution_options(isolation_level="REPEATABLE READ")
            with conn.begin():
                result = _publish(conn, coverage_start)
            return result
        finally:
            if locked:
                try:
                    conn.rollback()
                    conn.execute(select(func.pg_advisory_unlock(PUBLISH_LOCK)))
                    conn.commit()
                except BaseException:
                    # Never return a session-level advisory lock to the pool.
                    conn.invalidate()
                    raise
