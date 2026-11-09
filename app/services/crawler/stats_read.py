"""Owner-scoped reads of one published batch; never recompute raw crawler data."""

from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, literal, select, union_all
from sqlalchemy.exc import ProgrammingError

from app.models.crawler_stats import (
    RUN_COUNTERS,
    SOURCE_COUNTERS,
    CrawlerDailyRunStats,
    CrawlerDailyStats,
    CrawlerStatsPublication,
)
from app.schemas.crawler_stats import (
    CrawlerOutcomeCounts,
    CrawlerPlatformStats,
    CrawlerRunCounts,
    CrawlerStatsDay,
    CrawlerStatsResponse,
)
from app.services.crawler.stats import DEFINITION_VERSION, JAKARTA


def not_ready():
    return HTTPException(
        503,
        {
            "code": "CRAWLER_STATS_NOT_READY",
            "message": "Statistik crawl belum tersedia. Rekap histori perlu diperbarui.",
        },
    )


def read_stats(db, actor, from_date: date, to_date: date, platform: str | None = None):
    actor.require("jobs:read")
    days = (to_date - from_date).days + 1
    if not 1 <= days <= 366:
        raise HTTPException(422, "Rentang statistik harus antara 1 dan 366 tanggal kalender.")

    pub = CrawlerStatsPublication.__table__
    daily, runs = CrawlerDailyStats.__table__, CrawlerDailyRunStats.__table__
    # One statement keeps metadata, portal rows and distinct-run rows on the same
    # MVCC snapshot even with the default READ COMMITTED request transaction.
    portal_rows = select(
        daily.c.owner_id,
        daily.c.batch_id,
        daily.c.activity_date,
        daily.c.platform,
        literal("portal").label("kind"),
        *(daily.c[name] for name in (*SOURCE_COUNTERS, *RUN_COUNTERS)),
    )
    if platform:
        portal_rows = portal_rows.where(daily.c.platform == platform)
    run_rows = select(
        runs.c.owner_id,
        runs.c.batch_id,
        runs.c.activity_date,
        literal(None).label("platform"),
        literal("runs").label("kind"),
        *(literal(0).label(name) for name in SOURCE_COUNTERS),
        *(runs.c[name] for name in RUN_COUNTERS),
    )
    if platform:
        run_rows = run_rows.where(literal(False))
    combined = union_all(portal_rows, run_rows).subquery()
    query = (
        select(pub, *combined.c)
        .select_from(
            pub.outerjoin(
                combined,
                and_(
                    combined.c.owner_id == pub.c.owner_id,
                    combined.c.batch_id == pub.c.batch_id,
                    combined.c.activity_date >= from_date,
                    combined.c.activity_date <= to_date,
                ),
            )
        )
        .where(pub.c.owner_id == actor.owner_id)
    )
    try:
        rows = list(db.execute(query).mappings())
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42P01":
            db.rollback()
            raise not_ready() from None
        raise
    if not rows or rows[0]["definition_version"] != DEFINITION_VERSION:
        raise not_ready()
    metadata = rows[0]
    snapshot_at = metadata["source_snapshot_at"]
    if snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=UTC)
    coverage_start = metadata["coverage_start"]
    coverage_end = snapshot_at.astimezone(JAKARTA).date()
    covered_start, covered_end = max(from_date, coverage_start), min(to_date, coverage_end)
    has_coverage = covered_start <= covered_end
    coverage = (
        "none"
        if not has_coverage
        else "complete"
        if covered_start == from_date and covered_end == to_date
        else "partial"
    )
    totals, run_totals = CrawlerOutcomeCounts(), CrawlerRunCounts()
    by_day, by_platform = {}, {}
    for row in rows:
        day = row["activity_date"]
        if day is None or not covered_start <= day <= covered_end:
            continue
        if day not in by_day:
            by_day[day] = (CrawlerOutcomeCounts(), CrawlerRunCounts())
        day_counts, day_runs = by_day[day]
        if row["kind"] == "portal":
            portal = row["platform"]
            if portal not in by_platform:
                by_platform[portal] = CrawlerPlatformStats(
                    platform=portal, outcomes=CrawlerOutcomeCounts(), runs=CrawlerRunCounts()
                )
            for target in (totals, day_counts, by_platform[portal].outcomes):
                for name in SOURCE_COUNTERS:
                    setattr(target, name, getattr(target, name) + row[name])
                target.jobs_crawled_successfully = target.jobs_new + target.jobs_duplicate
            for name in RUN_COUNTERS:
                target = by_platform[portal].runs
                setattr(target, name, getattr(target, name) + row[name])
        if row["kind"] == "runs" or platform:
            for target in (run_totals, day_runs):
                for name in RUN_COUNTERS:
                    setattr(target, name, getattr(target, name) + row[name])
    series = []
    for index in range(days):
        day = from_date + timedelta(days=index)
        known = coverage_start <= day <= coverage_end
        counts, day_runs = by_day.get(day, (CrawlerOutcomeCounts(), CrawlerRunCounts()))
        series.append(
            CrawlerStatsDay(
                date=day, outcomes=counts if known else None, runs=day_runs if known else None
            )
        )
    return CrawlerStatsResponse(
        definition_version=metadata["definition_version"],
        timezone=metadata["timezone"],
        from_date=from_date,
        to_date=to_date,
        platform=platform,
        batch_id=metadata["batch_id"],
        source_snapshot_at=snapshot_at,
        published_at=metadata["published_at"],
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        coverage=coverage,
        stale=datetime.now(UTC) - snapshot_at > timedelta(minutes=10),
        totals=totals if has_coverage else None,
        runs=run_totals if has_coverage else None,
        series=series,
        platforms=sorted(by_platform.values(), key=lambda item: item.platform),
    )
