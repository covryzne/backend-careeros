"""Crawler persistence owned exclusively by the CareerOS backend."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_crawl_request"),
        CheckConstraint("execution_slot BETWEEN 1 AND 8", name="ck_crawl_execution_slot"),
        CheckConstraint(
            "status IN ('queued','running','completed','partial','failed','cancelled')",
            name="ck_crawl_status",
        ),
        CheckConstraint("trigger_type IN ('manual','scheduled')", name="ck_crawl_trigger"),
        Index(
            "uq_crawl_running_slot",
            "execution_slot",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
        Index(
            "uq_crawl_owner_active",
            "owner_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
            sqlite_where=text("status IN ('queued','running')"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    trigger_type: Mapped[str] = mapped_column(String(20), default="manual")
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawl_schedules.id"))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    execution_slot: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    process_id: Mapped[int | None] = mapped_column(Integer)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class CrawlRunSource(Base):
    __tablename__ = "crawl_run_sources"
    __table_args__ = (UniqueConstraint("run_id", "platform", name="uq_crawl_source"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    jobs_rejected: Mapped[int] = mapped_column(Integer, default=0)
    jobs_filtered: Mapped[int] = mapped_column(Integer, default=0)
    jobs_date_unknown: Mapped[int] = mapped_column(Integer, default=0)
    detail_attempts: Mapped[int] = mapped_column(Integer, default=0)
    pages_completed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlRunItem(Base):
    __tablename__ = "crawl_run_items"
    __table_args__ = (UniqueConstraint("run_id", "platform", "identity_key", name="uq_crawl_item"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(40))
    identity_key: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(Text)
    keyword: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="discovered")
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlWorkerSlot(Base):
    __tablename__ = "crawl_worker_slots"
    __table_args__ = (
        CheckConstraint("id BETWEEN 1 AND 8", name="ck_crawl_slot_range"),
        CheckConstraint("capacity BETWEEN 1 AND 8", name="ck_crawl_capacity"),
        Index("uq_crawl_slot_run", "active_run_id", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawl_runs.id"))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    dispatcher_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlSchedule(Base):
    __tablename__ = "crawl_schedules"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    config: Mapped[dict] = mapped_column(JSONB)
    hours: Mapped[list] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlScheduleOccurrence(Base):
    __tablename__ = "crawl_schedule_occurrences"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_for", name="uq_crawl_occurrence"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawl_schedules.id"))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schedule_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(String(200))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawl_runs.id"))


class CrawlRunAttempt(Base):
    __tablename__ = "crawl_run_attempts"
    __table_args__ = (UniqueConstraint("run_id", "number", name="uq_crawl_attempt"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawl_runs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    platforms: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    sources_before: Mapped[dict] = mapped_column(JSONB, default=dict)
    sources_after: Mapped[dict] = mapped_column(JSONB, default=dict)


class CrawlRequestReceipt(Base):
    __tablename__ = "crawl_request_receipts"
    __table_args__ = (UniqueConstraint("owner_id", "action", "key", name="uq_crawl_receipt"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawl_runs.id"))


class CrawlRunEvent(Base):
    __tablename__ = "crawl_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_number", name="uq_crawl_event_sequence"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(10))
    platform: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(600))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
