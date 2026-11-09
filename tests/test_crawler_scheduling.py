import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.security import LOCAL_OWNER_ID, ActorContext
from app.models.crawler import (
    CrawlRun,
    CrawlRunAttempt,
    CrawlRunItem,
    CrawlSchedule,
    CrawlScheduleOccurrence,
    CrawlWorkerSlot,
)
from app.schemas.crawler import CrawlCreate, ScheduleSave
from app.services.crawler import runs, schedules
from app.services.crawler.ingestion import apply_event

NOW = datetime(2026, 11, 8, 22, 0, tzinfo=UTC)  # 05:00 WIB on November 9.


@pytest.fixture
def queue(jobs_client):
    client, db = jobs_client
    db.add(
        CrawlWorkerSlot(
            id=1,
            capabilities=[
                {
                    "platform": name,
                    "status": "experimental",
                    "date_filter": "portal_search_filter",
                    "default_headless": True,
                    "location_scope": "Indonesia",
                }
                for name in ("linkedin", "jobstreet")
            ],
        )
    )
    db.commit()
    return client, db


def payload(**kw):
    return CrawlCreate(keywords=["AI Engineer"], platforms=["linkedin"], **kw)


def start(db, owner, key="start", config=None):
    run = runs.enqueue(db, ActorContext(owner), config or payload(), key)
    db.commit()
    return run


def test_two_slots_run_different_owners_and_third_waits_fifo(queue):
    _client, db = queue
    runs.configure_slots(db, 2)
    owners = [uuid.uuid4() for _ in range(3)]
    a, b, c = [start(db, owner) for owner in owners]
    assert runs.claim(db, "w1", 30)[0] == a.id
    db.commit()
    assert runs.claim(db, "w2", 30)[0] == b.id
    db.commit()
    assert runs.claim(db, "w3", 30) is None
    assert a.status == b.status == "running" and c.status == "queued"
    assert a.execution_slot != b.execution_slot
    assert runs.enqueue(db, ActorContext(owners[0]), payload(days=3), "again").id == a.id
    runs.finish(db, a.id, "w1", a.fencing_token)
    db.commit()
    assert runs.claim(db, "w3", 30)[0] == c.id
    db.commit()
    assert b.status == "running"
    with pytest.raises(RuntimeError, match="ownership"):
        runs.guarded(db, b.id, "w1", a.fencing_token)


def test_active_run_reuse_has_durable_request_receipt(queue):
    _client, db = queue
    actor = ActorContext(uuid.uuid4())
    a = start(db, actor.owner_id)
    assert runs.enqueue(db, actor, payload(days=3), "second-click").id == a.id
    db.commit()
    runs.claim(db, "w", 30)
    runs.finish(db, a.id, "w", a.fencing_token)
    db.commit()
    assert runs.enqueue(db, actor, payload(days=3), "second-click").id == a.id
    assert db.scalar(select(func.count()).select_from(CrawlRun)) == 1


def test_retry_on_another_slot_rejects_previous_attempt_fencing_token(queue):
    _client, db = queue
    runs.configure_slots(db, 2)
    owner = ActorContext(uuid.uuid4())
    a = start(db, owner.owner_id)
    _, old_token = runs.claim(db, "same-worker", 30)
    runs.finish(db, a.id, "same-worker", old_token, "fixture failure")
    db.commit()
    b = start(db, uuid.uuid4())
    assert runs.claim(db, "other-worker", 30)[0] == b.id
    db.commit()
    runs.retry(db, owner, a.id, "retry-slot")
    db.commit()
    _, new_token = runs.claim(db, "same-worker", 30)
    assert a.execution_slot == 2 and new_token > old_token
    with pytest.raises(RuntimeError, match="ownership"):
        runs.guarded(db, a.id, "same-worker", old_token)


@pytest.mark.parametrize(
    "hours,expected",
    [
        ([5], "2026-11-09T22:00:00+00:00"),
        ([17], "2026-11-09T10:00:00+00:00"),
        ([5, 17], "2026-11-09T10:00:00+00:00"),
    ],
)
def test_next_fixed_wib_time(hours, expected):
    assert schedules.next_occurrence(hours, NOW).isoformat() == expected


def save_schedule(
    db, owner=LOCAL_OWNER_ID, version=0, enabled=True, now=NOW - timedelta(minutes=1)
):
    result = schedules.save(
        db,
        ActorContext(owner),
        ScheduleSave(config=payload(), hours=[5, 17], enabled=enabled, expected_version=version),
        now=now,
    )
    db.commit()
    return result


def test_manual_does_not_change_schedule_dispatch_skips_overlap_then_next_slot(queue):
    _client, db = queue
    saved = save_schedule(db)
    run = start(db, LOCAL_OWNER_ID)
    schedule = db.get(CrawlSchedule, saved["id"])
    assert schedules.runs.utc(schedule.next_run_at) == NOW
    assert schedules.dispatch_one(db, schedule.id, LOCAL_OWNER_ID, now=NOW) == "skipped_overlap"
    db.commit()
    assert schedule.enabled and schedules.runs.utc(schedule.next_run_at) == NOW + timedelta(
        hours=12
    )
    assert schedules.dispatch_one(db, schedule.id, LOCAL_OWNER_ID, now=NOW) is None
    assert db.scalar(select(func.count()).select_from(CrawlScheduleOccurrence)) == 1
    assert runs.active_for_owner(db, LOCAL_OWNER_ID).id == run.id


def test_dispatch_enqueues_only_once_while_another_owner_runs(queue):
    _client, db = queue
    saved = save_schedule(db)
    other = start(db, uuid.uuid4())
    runs.claim(db, "other", 30)
    db.commit()
    assert schedules.dispatch_one(db, saved["id"], LOCAL_OWNER_ID, now=NOW) == "enqueued"
    db.commit()
    scheduled = runs.active_for_owner(db, LOCAL_OWNER_ID)
    assert scheduled.trigger_type == "scheduled" and scheduled.status == "queued"
    assert other.status == "running"
    assert schedules.dispatch_one(db, saved["id"], LOCAL_OWNER_ID, now=NOW) is None
    assert db.scalar(select(func.count()).select_from(CrawlScheduleOccurrence)) == 1


def test_schedule_versions_pause_resume_and_misfire(queue):
    _client, db = queue
    first = save_schedule(db)
    with pytest.raises(HTTPException) as exc:
        save_schedule(db, version=0)
    assert exc.value.status_code == 409
    db.rollback()
    paused = save_schedule(db, version=first["version"], enabled=False)
    assert paused["next_run_at"] is None
    resumed = save_schedule(db, version=paused["version"])
    assert (
        schedules.dispatch_one(db, resumed["id"], LOCAL_OWNER_ID, now=NOW + timedelta(hours=1))
        == "missed"
    )
    db.commit()
    assert runs.active_for_owner(db, LOCAL_OWNER_ID) is None
    assert schedules.read(db, ActorContext(uuid.uuid4())) is None


def test_retry_targets_failed_portal_preserves_items_and_does_not_double_count(queue):
    client, db = queue
    actor = ActorContext(LOCAL_OWNER_ID)
    config = CrawlCreate(
        keywords=["AI Engineer"], platforms=["linkedin", "jobstreet"], max_jobs_per_platform=1
    )
    run = start(db, actor.owner_id, config=config)
    runs.claim(db, "w", 30)
    db.commit()
    for platform in config.platforms:
        url = (
            "https://www.linkedin.com/jobs/view/123/"
            if platform == "linkedin"
            else "https://id.jobstreet.com/id/job/123"
        )
        ref = {
            "external_id": "123",
            "url": url,
            "title": "AI Engineer",
            "search_page": 1,
            "discovered_from": url,
        }
        apply_event(db, run, platform, "AI Engineer", {"type": "source_started"})
        apply_event(db, run, platform, "AI Engineer", {"type": "page", "references": [ref]})
        apply_event(
            db, run, platform, "AI Engineer", {"type": "detail_started", "external_id": "123"}
        )
        if platform == "linkedin":
            apply_event(
                db,
                run,
                platform,
                "AI Engineer",
                {
                    "type": "job",
                    "job": {
                        **ref,
                        "job_url": url,
                        "portal": platform,
                        "company": "Example",
                        "description": "Build AI",
                    },
                },
            )
            apply_event(
                db,
                run,
                platform,
                "AI Engineer",
                {"type": "source_finished", "state": "completed", "stop_reason": "max_jobs"},
            )
        else:
            apply_event(
                db,
                run,
                platform,
                "AI Engineer",
                {"type": "detail_error", "external_id": "123", "status": "listing_timeout"},
            )
    runs.finish(db, run.id, "w", run.fencing_token)
    db.commit()
    original_config = dict(run.config)
    response = client.post(
        f"/api/v1/crawler/runs/{run.id}/retry", headers={"Idempotency-Key": "retry-one"}
    )
    assert response.status_code == 202, response.text
    assert run.attempt_no == 2 and run.config == original_config
    assert runs.attempt(db, run).platforms == ["jobstreet"]
    assert (
        client.post(
            f"/api/v1/crawler/runs/{run.id}/retry", headers={"Idempotency-Key": "retry-one"}
        ).status_code
        == 202
    )
    assert run.attempt_no == 2
    runs.claim(db, "w2", 30)
    source = next(s for s in runs.source_rows(db, run.id) if s.platform == "jobstreet")
    item = db.scalar(
        select(CrawlRunItem).where(
            CrawlRunItem.run_id == run.id, CrawlRunItem.platform == "jobstreet"
        )
    )
    apply_event(
        db,
        run,
        "jobstreet",
        "AI Engineer",
        {
            "type": "job",
            "job": {
                "external_id": "123",
                "job_url": item.url,
                "portal": "jobstreet",
                "title": "AI Engineer",
                "company": "Example",
                "description": "Build AI",
            },
        },
    )
    apply_event(
        db,
        run,
        "jobstreet",
        "AI Engineer",
        {"type": "source_finished", "state": "completed", "stop_reason": "max_jobs"},
    )
    runs.finish(db, run.id, "w2", run.fencing_token)
    db.commit()
    assert run.status == "completed" and source.jobs_found == source.jobs_new == 1
    assert runs.run_data(db, run)["jobs_new"] == 2
    assert db.scalar(select(func.count()).select_from(CrawlRunAttempt)) == 2
    assert len(client.get(f"/api/v1/crawler/runs/{run.id}/attempts").json()) == 2


def test_schedule_api_rejects_unsupported_hours(queue):
    client, _db = queue
    for hours in ([6], [], [5, 5], [5, 17, 5]):
        response = client.put(
            "/api/v1/crawler/schedule",
            json={
                "config": payload().model_dump(),
                "hours": hours,
                "enabled": True,
                "expected_version": 0,
            },
        )
        assert response.status_code == 422


def test_saved_schedule_can_be_paused_and_resumed_using_read_configuration(queue):
    client, _db = queue
    response = client.put(
        "/api/v1/crawler/schedule",
        json={
            "config": payload().model_dump(),
            "hours": [5, 17],
            "enabled": True,
            "expected_version": 0,
        },
    )
    assert response.status_code == 200, response.text
    saved = client.get("/api/v1/crawler/schedule").json()
    paused = client.put(
        "/api/v1/crawler/schedule",
        json={
            "config": saved["config"],
            "hours": saved["hours"],
            "enabled": False,
            "expected_version": saved["version"],
        },
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["next_run_at"] is None


def test_dispatcher_processes_more_than_one_batch_and_publishes_heartbeat(queue):
    from sqlalchemy.orm import sessionmaker

    from app.models.candidate import User

    _client, db = queue
    owners = [uuid.uuid4() for _ in range(201)]
    db.add_all([User(id=owner) for owner in owners])
    db.flush()
    db.add_all(
        [
            CrawlSchedule(
                owner_id=owner,
                config=payload().model_dump(),
                hours=[5, 17],
                enabled=True,
                version=1,
                next_run_at=NOW,
            )
            for owner in owners
        ]
    )
    db.commit()
    results = schedules.dispatch(sessionmaker(bind=db.get_bind()), now=NOW)
    assert len(results) == 201 and set(results) == {"enqueued"}
    db.expire_all()
    assert runs.utc(db.get(CrawlWorkerSlot, 1).dispatcher_heartbeat_at) == NOW
    assert db.scalar(select(func.count()).select_from(CrawlRun)) == 201
