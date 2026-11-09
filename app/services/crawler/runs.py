import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import settings
from app.models.crawler import (
    CrawlRequestReceipt,
    CrawlRun,
    CrawlRunAttempt,
    CrawlRunItem,
    CrawlRunSource,
    CrawlScheduleOccurrence,
    CrawlWorkerSlot,
)
from app.services.crawler.events import append as append_event
from app.services.shared.idempotency import fingerprint, lock_owner

ACTIVE = {"queued", "running"}


def queue_time(db):
    # All replicas use the database clock for FIFO ordering, not browser/host clocks.
    return (
        db.scalar(select(func.clock_timestamp()))
        if db.get_bind().dialect.name == "postgresql"
        else datetime.now(UTC)
    )


def utc(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def owned(db, actor, run_id):
    actor.require("jobs:read")
    run = db.scalar(
        select(CrawlRun).where(CrawlRun.id == run_id, CrawlRun.owner_id == actor.owner_id)
    )
    if run is None:
        raise HTTPException(404, "Crawl run not found")
    return run


def slot(db, slot_id=1):
    item = db.scalar(select(CrawlWorkerSlot).where(CrawlWorkerSlot.id == slot_id).with_for_update())
    if item is None:
        # Migration seeds this row. Never silently weaken coordination if it is missing.
        raise HTTPException(503, "Crawler migration/worker slot is not initialized")
    return item


def source_rows(db, run_id):
    return db.scalars(
        select(CrawlRunSource)
        .where(CrawlRunSource.run_id == run_id)
        .order_by(CrawlRunSource.platform)
    ).all()


def run_data(db, run):
    sources = source_rows(db, run.id)
    return {
        "run_id": run.id,
        "trigger_type": run.trigger_type,
        "attempt_no": run.attempt_no,
        "retryable_platforms": [s.platform for s in sources if s.status in {"failed", "partial"}],
        "schedule_id": run.schedule_id,
        "scheduled_for": db.scalar(
            select(CrawlScheduleOccurrence.scheduled_for).where(
                CrawlScheduleOccurrence.run_id == run.id
            )
        )
        if run.schedule_id
        else None,
        "status": run.status,
        "config": run.config,
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "heartbeat_at": run.heartbeat_at,
        "error": run.error,
        "jobs_found": sum(s.jobs_found for s in sources),
        "jobs_new": sum(s.jobs_new for s in sources),
        "jobs_duplicate": sum(s.jobs_duplicate for s in sources),
        "jobs_rejected": sum(s.jobs_rejected for s in sources),
        "jobs_filtered": sum(s.jobs_filtered for s in sources),
        "jobs_date_unknown": sum(s.jobs_date_unknown for s in sources),
        "sources": [
            {
                key: getattr(s, key)
                for key in (
                    "platform",
                    "status",
                    "jobs_found",
                    "jobs_new",
                    "jobs_duplicate",
                    "jobs_rejected",
                    "jobs_filtered",
                    "jobs_date_unknown",
                    "detail_attempts",
                    "pages_completed",
                    "error",
                )
            }
            for s in sources
        ],
    }


def active_for_owner(db, owner_id):
    return db.scalar(
        select(CrawlRun).where(CrawlRun.owner_id == owner_id, CrawlRun.status.in_(ACTIVE))
    )


def attempt(db, run):
    return db.scalar(
        select(CrawlRunAttempt).where(
            CrawlRunAttempt.run_id == run.id, CrawlRunAttempt.number == run.attempt_no
        )
    )


def source_snapshot(db, run):
    return {
        s.platform: {
            k: getattr(s, k)
            for k in (
                "status",
                "jobs_found",
                "jobs_new",
                "jobs_duplicate",
                "jobs_rejected",
                "jobs_filtered",
                "jobs_date_unknown",
                "detail_attempts",
                "pages_completed",
                "error",
            )
        }
        for s in source_rows(db, run.id)
    }


def configure_slots(db, capacity):
    state = slot(db)
    occupied = db.scalar(
        select(CrawlWorkerSlot.id)
        .where(CrawlWorkerSlot.id > capacity, CrawlWorkerSlot.active_run_id.is_not(None))
        .limit(1)
    )
    if occupied:
        raise RuntimeError("Drain higher execution slots before reducing capacity")
    state.capacity = capacity
    for number in range(2, capacity + 1):
        if db.get(CrawlWorkerSlot, number) is None:
            db.add(CrawlWorkerSlot(id=number))
    db.flush()


def capabilities(db, actor):
    actor.require("jobs:read")
    state = db.get(CrawlWorkerSlot, 1)
    current = active_for_owner(db, actor.owner_id)
    slots = list(db.scalars(select(CrawlWorkerSlot)))
    online = any(
        utc(s.heartbeat_at) and utc(s.heartbeat_at) > datetime.now(UTC) - timedelta(seconds=15)
        for s in slots
    )
    return {
        "engine": "selenium",
        "platforms": [
            c for c in state.capabilities if c["platform"] in settings.enabled_crawler_portals()
        ]
        if state
        else [],
        "worker_online": online,
        "scheduler_online": bool(
            state
            and utc(state.dispatcher_heartbeat_at)
            and utc(state.dispatcher_heartbeat_at) > datetime.now(UTC) - timedelta(seconds=150)
        ),
        "active": current is not None,
        "active_run_id": current.id if current else None,
        "running_count": sum(s.active_run_id is not None for s in slots),
        "queued_count": db.scalar(
            select(func.count()).select_from(CrawlRun).where(CrawlRun.status == "queued")
        )
        or 0,
        "execution_capacity": state.capacity if state else 1,
        "limits": {"max_pages": 20, "days": 30, "max_jobs_per_platform": 200, "keywords": 5},
    }


def receipt(db, actor, action, key, digest):
    if not key or not key.strip() or len(key) > 200:
        raise HTTPException(422, "Idempotency-Key is required (1..200 characters)")
    previous = db.scalar(
        select(CrawlRequestReceipt).where(
            CrawlRequestReceipt.owner_id == actor.owner_id,
            CrawlRequestReceipt.action == action,
            CrawlRequestReceipt.key == key,
        )
    )
    if previous:
        if previous.payload_hash != digest:
            raise HTTPException(409, "Idempotency key reused with different payload")
        return db.get(CrawlRun, previous.run_id)
    return None


def save_receipt(db, actor, action, key, digest, run):
    db.add(
        CrawlRequestReceipt(
            owner_id=actor.owner_id, action=action, key=key, payload_hash=digest, run_id=run.id
        )
    )
    db.flush()


def validated_config(db, payload):
    state = slot(db)
    installed = {c["platform"]: c for c in state.capabilities}
    for platform in payload.platforms:
        if platform not in settings.enabled_crawler_portals():
            raise HTTPException(422, f"Portal {platform} is disabled for new crawls")
        capability = installed.get(platform)
        if not capability or capability.get("status") not in {"available", "experimental"}:
            raise HTTPException(
                422, f"Adapter {platform} is unavailable; start/check the crawler worker"
            )
        if (
            capability.get("location_scope") == "Indonesia"
            and payload.location.casefold() != "indonesia"
        ):
            raise HTTPException(422, f"{platform} currently supports Indonesia only")
    if (
        "linkedin" in payload.platforms
        and payload.location.casefold() != "indonesia"
        and payload.linkedin_geo_id == "102478259"
    ):
        raise HTTPException(422, "Custom LinkedIn location needs the corresponding linkedin_geo_id")
    return {
        **payload.model_dump(mode="json"),
        "adapter_capabilities": [installed[p] for p in payload.platforms],
        "post_filter_days": True,
    }


def new_run(db, actor, config, key, digest, trigger="manual", schedule_id=None):
    now = queue_time(db)
    run = CrawlRun(
        id=uuid.uuid4(),
        owner_id=actor.owner_id,
        idempotency_key=key,
        payload_hash=digest,
        config=config,
        trigger_type=trigger,
        status="queued",
        schedule_id=schedule_id,
        attempt_no=1,
        enqueued_at=now,
    )
    db.add(run)
    db.flush()
    for platform in config["platforms"]:
        db.add(CrawlRunSource(run_id=run.id, platform=platform))
    db.add(
        CrawlRunAttempt(
            run_id=run.id,
            number=1,
            kind="initial",
            platforms=config["platforms"],
            status="queued",
            sources_before={},
            sources_after={},
        )
    )
    db.flush()
    append_event(db, run, f"Run {trigger} masuk antrean. Menunggu slot worker tersedia.")
    return run


def enqueue(db, actor, payload, key):
    actor.require("jobs:write")
    if actor.actor_type != "user":
        raise HTTPException(403, "Crawler manual start requires a user")
    lock_owner(db, actor)
    digest = fingerprint(payload.model_dump(mode="json"))
    previous = receipt(db, actor, "manual", key, digest)
    if previous:
        return previous
    # Preserve idempotent receipts created before the queue migration.
    previous = db.scalar(
        select(CrawlRun).where(CrawlRun.owner_id == actor.owner_id, CrawlRun.idempotency_key == key)
    )
    if previous:
        if previous.payload_hash != digest:
            raise HTTPException(409, "Idempotency key reused with different payload")
        return previous
    config = validated_config(db, payload)
    current = active_for_owner(db, actor.owner_id)
    run = current or new_run(db, actor, config, key, digest)
    save_receipt(db, actor, "manual", key, digest, run)
    return run


def retry(db, actor, run_id, key):
    actor.require("jobs:write")
    if actor.actor_type != "user":
        raise HTTPException(403, "Retry requires a user")
    lock_owner(db, actor)
    run = owned(db, actor, run_id)
    digest = fingerprint(str(run.id))
    previous = receipt(db, actor, "retry", key, digest)
    if previous:
        return previous
    slot(db)
    current = active_for_owner(db, actor.owner_id)
    if current:
        if current.id != run.id:
            raise HTTPException(409, "Finish the current crawl before retrying an older run")
        save_receipt(db, actor, "retry", key, digest, run)
        return run
    targets = [s for s in source_rows(db, run.id) if s.status in {"failed", "partial"}]
    if not targets:
        raise HTTPException(409, "No failed or partial portals to retry")
    from app.schemas.crawler import CrawlCreate

    values = {name: run.config[name] for name in CrawlCreate.model_fields if name in run.config}
    values["platforms"] = [s.platform for s in targets]
    validated_config(db, CrawlCreate(**values))  # Current portal policy still applies.
    before = source_snapshot(db, run)
    old = attempt(db, run)
    if old is None:
        old = CrawlRunAttempt(
            run_id=run.id,
            number=run.attempt_no,
            kind="initial",
            platforms=run.config["platforms"],
            status=run.status,
            queued_at=run.queued_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
            sources_before={},
            sources_after=before,
        )
        db.add(old)
    elif not old.sources_after:
        old.sources_after = before
    for source in targets:
        for item in db.scalars(
            select(CrawlRunItem).where(
                CrawlRunItem.run_id == run.id,
                CrawlRunItem.platform == source.platform,
                CrawlRunItem.status.in_(["failed", "rejected", "discovered"]),
            )
        ):
            if item.status == "rejected":
                source.jobs_rejected -= 1
            item.status, item.error = "discovered", None
        source.status, source.error, source.finished_at = "queued", None, None
    run.attempt_no += 1
    run.status, run.error, run.finished_at = "queued", None, None
    run.enqueued_at = queue_time(db)
    db.add(
        CrawlRunAttempt(
            run_id=run.id,
            number=run.attempt_no,
            kind="retry",
            platforms=[s.platform for s in targets],
            status="queued",
            sources_before=before,
            sources_after={},
        )
    )
    save_receipt(db, actor, "retry", key, digest, run)
    append_event(
        db,
        run,
        f"Percobaan {run.attempt_no}: retry portal bermasalah ({', '.join(s.platform for s in targets)}).",
    )
    return run


def claim(db, worker_id, lease_seconds):
    coordinator = slot(db)
    # Never reuse a worker/slot with an uncertain running browser, even after expiry.
    if db.scalar(
        select(CrawlWorkerSlot.id).where(
            CrawlWorkerSlot.worker_id == worker_id, CrawlWorkerSlot.active_run_id.is_not(None)
        )
    ):
        return None
    available = db.scalar(
        select(CrawlWorkerSlot)
        .where(CrawlWorkerSlot.id <= coordinator.capacity, CrawlWorkerSlot.active_run_id.is_(None))
        .order_by(CrawlWorkerSlot.id)
        .with_for_update()
    )
    if available is None:
        return None
    run = db.scalar(
        select(CrawlRun)
        .where(CrawlRun.status == "queued")
        .order_by(CrawlRun.enqueued_at, CrawlRun.id)
        .limit(1)
        .with_for_update()
    )
    if run is None:
        return None
    now = datetime.now(UTC)
    # Tokens remain monotonic for this run even when a retry uses another slot.
    available.fencing_token = max(available.fencing_token, run.fencing_token) + 1
    available.worker_id, available.heartbeat_at, available.active_run_id = worker_id, now, run.id
    run.execution_slot = available.id
    run.status, run.worker_id, run.fencing_token = "running", worker_id, available.fencing_token
    run.started_at = run.started_at or now
    run.heartbeat_at, run.lease_until = now, now + timedelta(seconds=lease_seconds)
    current = attempt(db, run)
    if current:
        current.status, current.started_at = "running", now
    db.flush()
    append_event(
        db,
        run,
        f"Worker mengambil percobaan {run.attempt_no} pada slot {available.id}. Memulai pencarian lowongan.",
    )
    return run.id, run.fencing_token


def guarded(db, run_id, worker_id, token, *, lease=True):
    slot(db)
    run = db.scalar(select(CrawlRun).where(CrawlRun.id == run_id).with_for_update())
    state = slot(db, run.execution_slot) if run else None
    if (
        not run
        or state.active_run_id != run_id
        or state.worker_id != worker_id
        or state.fencing_token != token
        or run.worker_id != worker_id
        or run.fencing_token != token
        or run.status != "running"
        or (lease and (not run.lease_until or utc(run.lease_until) <= datetime.now(UTC)))
    ):
        raise RuntimeError("Crawler execution ownership/lease lost")
    return run, state


def heartbeat(db, run_id, worker_id, token, lease_seconds):
    run, state = guarded(db, run_id, worker_id, token)
    run.heartbeat_at = state.heartbeat_at = datetime.now(UTC)
    run.lease_until = run.heartbeat_at + timedelta(seconds=lease_seconds)


def finish(db, run_id, worker_id, token, error=None):
    run, state = guarded(db, run_id, worker_id, token, lease=False)
    sources = source_rows(db, run_id)
    current_attempt = attempt(db, run)
    for source in sources:
        if source.status in ACTIVE:
            source.status = "partial" if source.jobs_new + source.jobs_duplicate else "failed"
            source.error = source.error or error or "Execution interrupted"
            source.finished_at = datetime.now(UTC)
    good = any(s.status == "completed" or s.jobs_new + s.jobs_duplicate for s in sources)
    bad = error is not None or any(s.status != "completed" for s in sources)
    run.status = ("partial" if good else "failed") if bad else "completed"
    run.error = error or (
        "Some sources could not finish; inspect per-platform outcomes" if bad else None
    )
    run.finished_at = datetime.now(UTC)
    run.lease_until = None
    if current_attempt:
        current_attempt.status, current_attempt.finished_at, current_attempt.error = (
            run.status,
            run.finished_at,
            run.error,
        )
        current_attempt.sources_after = source_snapshot(db, run)
    state.active_run_id = None
    db.flush()
    append_event(
        db,
        run,
        f"Run selesai: {run.status}. Baru: {sum(s.jobs_new for s in sources)}, "
        f"duplikat: {sum(s.jobs_duplicate for s in sources)}, "
        f"dilewati filter tanggal: {sum(s.jobs_filtered for s in sources)}."
        + (f" Keterangan: {run.error}." if run.error else ""),
        level="INFO" if run.status == "completed" else "WARN",
    )


def page(db, actor, *, page=1, page_size=20):
    actor.require("jobs:read")
    query = select(CrawlRun).where(CrawlRun.owner_id == actor.owner_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(CrawlRun.queued_at.desc(), CrawlRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [run_data(db, r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": page * page_size < total,
    }
