"""Published crawler activity, separate from operational crawler state."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SOURCE_COUNTERS = (
    "jobs_found",
    "jobs_new",
    "jobs_duplicate",
    "jobs_rejected",
    "jobs_filtered",
    "jobs_date_unknown",
    "detail_attempts",
    "pages_completed",
)
RUN_COUNTERS = (
    "runs_started",
    "runs_running",
    "runs_completed",
    "runs_partial",
    "runs_failed",
    "runs_cancelled",
)
COUNTERS = (*RUN_COUNTERS, *SOURCE_COUNTERS)


class CrawlerStatsPublication(Base):
    __tablename__ = "crawler_stats_publications"
    __table_args__ = (
        UniqueConstraint("owner_id", "batch_id", name="uq_crawler_stats_owner_batch"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False)
    coverage_start: Mapped[date] = mapped_column(Date, nullable=False)
    source_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrawlerDailyStats(Base):
    __tablename__ = "crawler_daily_stats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "batch_id"],
            ["crawler_stats_publications.owner_id", "crawler_stats_publications.batch_id"],
            name="fk_crawler_stats_publication",
        ),
        *(CheckConstraint(f"{name} >= 0", name=f"ck_crawler_stats_{name}") for name in COUNTERS),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    platform: Mapped[str] = mapped_column(String(40), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    runs_started: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_running: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_completed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_partial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_failed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_cancelled: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jobs_found: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jobs_new: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jobs_duplicate: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jobs_rejected: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jobs_filtered: Mapped[int] = mapped_column(BigInteger, nullable=False)
    jobs_date_unknown: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detail_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pages_completed: Mapped[int] = mapped_column(BigInteger, nullable=False)

    @property
    def jobs_crawled_successfully(self) -> int:
        return self.jobs_new + self.jobs_duplicate


class CrawlerDailyRunStats(Base):
    """Distinct runs across platforms; never sum platform-level run counts."""

    __tablename__ = "crawler_daily_run_stats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "batch_id"],
            ["crawler_stats_publications.owner_id", "crawler_stats_publications.batch_id"],
            name="fk_crawler_run_stats_publication",
        ),
        *(
            CheckConstraint(f"{name} >= 0", name=f"ck_crawler_run_stats_{name}")
            for name in RUN_COUNTERS
        ),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    runs_started: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_running: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_completed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_partial: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_failed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    runs_cancelled: Mapped[int] = mapped_column(BigInteger, nullable=False)
