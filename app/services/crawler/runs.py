import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.crawler import CrawlRun, CrawlRunSource, CrawlWorkerSlot
from app.services.crawler.events import append as append_event
from app.services.shared.idempotency import fingerprint, lock_owner

ACTIVE = {"queued", "running"}


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


def slot(db):
    item = db.scalar(select(CrawlWorkerSlot).where(CrawlWorkerSlot.id == 1).with_for_update())
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


def capabilities(db, actor):
    actor.require("jobs:read")
    state = db.get(CrawlWorkerSlot, 1)
    current = db.get(CrawlRun, state.active_run_id) if state and state.active_run_id else None
    online = bool(
        state
        and utc(state.heartbeat_at)
        and utc(state.heartbeat_at) > datetime.now(UTC) - timedelta(seconds=15)
    )
    return {
        "engine": "selenium",
        "platforms": state.capabilities if state else [],
        "worker_online": online,
        "active": bool(current or (state and state.active_run_id)),
        "active_run_id": current.id if current and current.owner_id == actor.owner_id else None,
        "limits": {"max_pages": 20, "days": 30, "max_jobs_per_platform": 200, "keywords": 5},
    }


def enqueue(db, actor, payload, key):
    actor.require("jobs:write")
    if actor.actor_type != "user":
        raise HTTPException(403, "Crawler manual start requires a user")
    if not key or not key.strip() or len(key) > 200:
        raise HTTPException(422, "Idempotency-Key is required (1..200 characters)")
    lock_owner(db, actor)
    config = payload.model_dump(mode="json")
    digest = fingerprint(config)
    previous = db.scalar(
        select(CrawlRun).where(CrawlRun.owner_id == actor.owner_id, CrawlRun.idempotency_key == key)
    )
    if previous:
        if previous.payload_hash != digest:
            raise HTTPException(409, "Idempotency key reused with different payload")
        return previous
    state = slot(db)
    if state.active_run_id or db.scalar(
        select(CrawlRun.id).where(CrawlRun.status.in_(ACTIVE)).limit(1)
    ):
        current = db.get(CrawlRun, state.active_run_id) if state.active_run_id else None
        raise HTTPException(
            409,
            {
                "code": "CRAWLER_BUSY",
                "message": "Another crawl is queued, running, or awaiting safe recovery.",
                "active_run_id": str(current.id)
                if current and current.owner_id == actor.owner_id
                else None,
            },
        )
    installed = {c["platform"]: c for c in state.capabilities}
    for platform in payload.platforms:
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
    config["adapter_capabilities"] = [installed[p] for p in payload.platforms]
    config["post_filter_days"] = True  # Versioned snapshot: do not reinterpret historical runs.
    run = CrawlRun(
        id=uuid.uuid4(),
        owner_id=actor.owner_id,
        idempotency_key=key,
        payload_hash=digest,
        config=config,
        trigger_type="manual",
        status="queued",
    )
    db.add(run)
    db.flush()
    for platform in payload.platforms:
        db.add(CrawlRunSource(run_id=run.id, platform=platform))
    state.active_run_id = run.id
    db.flush()
    append_event(db, run, "Run manual masuk antrean. Menunggu worker crawler.")
    return run


def claim(db, worker_id, lease_seconds):
    state = slot(db)
    if state.active_run_id is None:
        return None
    run = db.scalar(select(CrawlRun).where(CrawlRun.id == state.active_run_id).with_for_update())
    # Stale RUNNING is not reclaimed. A browser may still be alive.
    if run is None or run.status != "queued":
        return None
    now = datetime.now(UTC)
    state.fencing_token += 1
    state.worker_id = worker_id
    state.heartbeat_at = now
    run.status, run.worker_id, run.fencing_token = "running", worker_id, state.fencing_token
    run.started_at = run.heartbeat_at = now
    run.lease_until = now + timedelta(seconds=lease_seconds)
    db.flush()
    append_event(db, run, "Worker mengambil run. Memulai pencarian lowongan.")
    return run.id, run.fencing_token


def guarded(db, run_id, worker_id, token, *, lease=True):
    state = slot(db)
    run = db.scalar(select(CrawlRun).where(CrawlRun.id == run_id).with_for_update())
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
