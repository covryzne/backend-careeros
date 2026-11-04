import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class CrawlerOutcomeCounts(BaseModel):
    jobs_found: int = 0
    jobs_new: int = 0
    jobs_duplicate: int = 0
    jobs_rejected: int = 0
    jobs_filtered: int = 0
    jobs_date_unknown: int = 0
    detail_attempts: int = 0
    pages_completed: int = 0
    jobs_crawled_successfully: int = 0


class CrawlerRunCounts(BaseModel):
    runs_started: int = 0
    runs_running: int = 0
    runs_completed: int = 0
    runs_partial: int = 0
    runs_failed: int = 0
    runs_cancelled: int = 0


class CrawlerStatsDay(BaseModel):
    date: date
    outcomes: CrawlerOutcomeCounts | None
    runs: CrawlerRunCounts | None


class CrawlerPlatformStats(BaseModel):
    platform: str
    outcomes: CrawlerOutcomeCounts
    runs: CrawlerRunCounts


class CrawlerStatsResponse(BaseModel):
    definition_version: int
    timezone: Literal["Asia/Jakarta"]
    history_basis: Literal["retained_runs"] = "retained_runs"
    from_date: date
    to_date: date
    platform: str | None
    batch_id: uuid.UUID
    source_snapshot_at: datetime
    published_at: datetime
    coverage_start: date
    coverage_end: date
    coverage: Literal["complete", "partial", "none"]
    stale: bool
    totals: CrawlerOutcomeCounts | None
    runs: CrawlerRunCounts | None
    series: list[CrawlerStatsDay]
    platforms: list[CrawlerPlatformStats]
