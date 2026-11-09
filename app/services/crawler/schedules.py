"""Fixed WIB schedules; dispatch only enqueues the shared owner-scoped queue."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import ActorContext
from app.models.crawler import CrawlSchedule, CrawlScheduleOccurrence
from app.schemas.crawler import CrawlCreate
from app.services.crawler import runs
from app.services.shared.idempotency import fingerprint, lock_owner

WIB = ZoneInfo("Asia/Jakarta")


def next_occurrence(hours, after):
    local = runs.utc(after).astimezone(WIB)
    candidates = [
        datetime.combine(local.date() + timedelta(days=offset), time(hour), tzinfo=WIB)
        for offset in (0, 1)
        for hour in hours
    ]
    return min(value for value in candidates if value > local).astimezone(UTC)


def data(schedule):
    if schedule is None:
        return None
    result = {
        name: getattr(schedule, name)
        for name in (
            "id",
            "config",
            "hours",
            "enabled",
            "version",
            "next_run_at",
            "last_run_at",
            "updated_at",
        )
    }
    result["config"] = {
        name: value for name, value in schedule.config.items() if name in CrawlCreate.model_fields
    }
    return result


def read(db, actor):
    actor.require("jobs:read")
    return data(db.scalar(select(CrawlSchedule).where(CrawlSchedule.owner_id == actor.owner_id)))


def save(db, actor, payload, *, now=None):
    actor.require("jobs:write")
    if actor.actor_type != "user":
        raise HTTPException(403, "Schedule changes require a user")
    now = now or datetime.now(UTC)
    lock_owner(db, actor)
    schedule = db.scalar(
        select(CrawlSchedule).where(CrawlSchedule.owner_id == actor.owner_id).with_for_update()
    )
    if (schedule.version if schedule else 0) != payload.expected_version:
        raise HTTPException(409, "Schedule changed in another tab. Reload before saving.")
    # Pause remains possible even when a previously saved portal is now disabled.
    config = (
        runs.validated_config(db, payload.config)
        if payload.enabled
        else payload.config.model_dump(mode="json")
    )
    if schedule is None:
        schedule = CrawlSchedule(owner_id=actor.owner_id, version=0)
        db.add(schedule)
    schedule.config, schedule.hours, schedule.enabled = config, payload.hours, payload.enabled
    schedule.version += 1
    schedule.updated_at = now
    schedule.next_run_at = next_occurrence(schedule.hours, now) if schedule.enabled else None
    db.flush()
    return data(schedule)


def dispatch_one(db, schedule_id, owner_id, *, now):
    actor = ActorContext(owner_id, "system")
    lock_owner(db, actor)  # Same lock order as schedule save and manual enqueue.
    schedule = db.scalar(
        select(CrawlSchedule)
        .where(CrawlSchedule.id == schedule_id, CrawlSchedule.owner_id == owner_id)
        .with_for_update()
    )
    if (
        not schedule
        or not schedule.enabled
        or not schedule.next_run_at
        or runs.utc(schedule.next_run_at) > now
    ):
        return None
    due = runs.utc(schedule.next_run_at)
    existing = db.scalar(
        select(CrawlScheduleOccurrence).where(
            CrawlScheduleOccurrence.schedule_id == schedule.id,
            CrawlScheduleOccurrence.scheduled_for == due,
        )
    )
    schedule.next_run_at = next_occurrence(schedule.hours, now)
    if existing:
        return existing.status
    occurrence = CrawlScheduleOccurrence(
        schedule_id=schedule.id,
        scheduled_for=due,
        schedule_version=schedule.version,
        status="missed",
    )
    db.add(occurrence)
    if now - due > timedelta(minutes=2):
        occurrence.reason = "Dispatcher missed the occurrence; backlog not replayed"
    else:
        runs.slot(db)
        if runs.active_for_owner(db, owner_id):
            occurrence.status, occurrence.reason = (
                "skipped_overlap",
                "Owner already has queued/running work",
            )
        else:
            try:
                payload = CrawlCreate(
                    **{
                        name: schedule.config[name]
                        for name in CrawlCreate.model_fields
                        if name in schedule.config
                    }
                )
                config = runs.validated_config(db, payload)
            except HTTPException as exc:
                occurrence.status, occurrence.reason = "unavailable", str(exc.detail)[:200]
            else:
                key = f"schedule:{schedule.id}:{due.isoformat()}"
                run = runs.new_run(
                    db, actor, config, key, fingerprint(config), "scheduled", schedule.id
                )
                occurrence.status, occurrence.run_id = "enqueued", run.id
                schedule.last_run_at = now
    db.flush()
    return occurrence.status


def dispatch(factory, *, now=None):
    now = now or datetime.now(UTC)
    with factory() as db:
        state = runs.slot(db)
        state.dispatcher_heartbeat_at = now
        db.commit()
    outcomes = []
    # Drain all schedules due at this invocation, in short batches. A shared
    # 05:00 trigger must not defer the 201st owner for another full cron interval.
    while True:
        with factory() as db:
            due = list(
                db.execute(
                    select(CrawlSchedule.id, CrawlSchedule.owner_id)
                    .where(CrawlSchedule.enabled.is_(True), CrawlSchedule.next_run_at <= now)
                    .order_by(CrawlSchedule.next_run_at, CrawlSchedule.id)
                    .limit(200)
                )
            )
        if not due:
            return outcomes
        for schedule_id, owner_id in due:
            with factory() as db:
                outcome = dispatch_one(db, schedule_id, owner_id, now=now)
                db.commit()
                if outcome:
                    outcomes.append(outcome)
