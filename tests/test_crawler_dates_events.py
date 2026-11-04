import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.security import ActorContext, get_actor
from app.main import app
from app.models.crawler import CrawlRun, CrawlRunItem, CrawlRunSource, CrawlWorkerSlot
from app.models.workflow import Job
from app.services import crawler_runs as service
from app.services.crawler_dates import evaluate, posting_date
from app.services.crawler_ingestion import apply_event


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-10-29", "2026-10-29"),
        ("2026-10-28T18:00:00Z", "2026-10-29"),
        ("2026-11-04T01:30:00+07:00", "2026-11-04"),
        (None, None),
        ("7 days ago", None),
        ("2026-11-04T01:30:00", None),
        ("2026-02-30", None),
        ("2026-11-04Tnot-a-timestampZ", None),
        ("Actively hiring 2 hours ago", None),
    ],
)
def test_posting_date_does_not_guess(raw, expected):
    result = posting_date(raw)
    assert (result.isoformat() if result else None) == expected


@pytest.fixture
def kalibrr(jobs_client):
    client, db = jobs_client
    db.add(
        CrawlWorkerSlot(
            id=1,
            capabilities=[
                {
                    "platform": "kalibrr",
                    "status": "experimental",
                    "date_filter": "latest_sort_only_no_age_filter",
                    "default_headless": True,
                    "location_scope": "Indonesia",
                }
            ],
        )
    )
    db.commit()
    response = client.post(
        "/api/v1/crawler/runs",
        headers={"Idempotency-Key": "date-run"},
        json={"keywords": ["Data Engineer"], "platforms": ["kalibrr"], "days": 7},
    )
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["run_id"])
    run = db.get(CrawlRun, run_id)
    run.queued_at = datetime(2026, 11, 4, 0, 0, tzinfo=UTC)
    db.commit()
    service.claim(db, "date-worker", 30)
    db.commit()
    return client, db, run


def ingest(db, run, raw, job_id="123"):
    ref = {
        "external_id": job_id,
        "url": f"https://www.kalibrr.com/id-ID/c/example/jobs/{job_id}/data-engineer",
        "title": "Data Engineer",
        "search_page": 1,
        "discovered_from": "https://www.kalibrr.com/id-ID/home/te/data-engineer",
    }
    events = [
        {"type": "source_started"},
        {"type": "page", "references": [ref], "page": 1},
        {"type": "detail_started", "external_id": job_id},
        {
            "type": "job",
            "job": {
                **ref,
                "portal": "kalibrr",
                "job_url": ref["url"],
                "company": "Example",
                "description": "Build pipelines.",
                "posted_at_raw": raw,
            },
        },
        {"type": "source_finished", "state": "completed", "stop_reason": "max_jobs"},
    ]
    for event in events:
        apply_event(db, run, "kalibrr", "Data Engineer", event)
        db.commit()


@pytest.mark.parametrize(
    "raw,expected_status,new,filtered,unknown",
    [
        ("2026-11-04", "new", 1, 0, 0),
        ("2026-10-29", "new", 1, 0, 0),
        ("2026-10-28T18:00:00Z", "new", 1, 0, 0),
        ("2026-10-28", "filtered_date", 0, 1, 0),
        ("2026-11-05", "filtered_date", 0, 1, 0),
        (None, "date_unknown", 0, 1, 1),
        ("2026-11-04T08:00:00", "date_unknown", 0, 1, 1),
    ],
)
def test_fallback_filters_before_inserting_jobs_and_logs_reason(
    kalibrr, raw, expected_status, new, filtered, unknown
):
    client, db, run = kalibrr
    ingest(db, run, raw)
    source = db.scalar(select(CrawlRunSource).where(CrawlRunSource.run_id == run.id))
    assert (
        source.jobs_new == new
        and source.jobs_filtered == filtered
        and source.jobs_date_unknown == unknown
    )
    assert source.jobs_rejected == 0 and source.status == "completed"
    assert db.scalar(select(func.count()).select_from(Job)) == new
    item = db.scalar(select(CrawlRunItem).where(CrawlRunItem.run_id == run.id))
    assert item.status == expected_status
    result = client.get(f"/api/v1/crawler/runs/{run.id}/results").json()["items"][0]
    assert result["date_check"] == item.provenance["date_check"]
    messages = client.get(f"/api/v1/crawler/runs/{run.id}/events").json()["items"]
    assert any("Mengambil detail" in message["message"] for message in messages)
    if filtered:
        assert item.error and any("Dilewati" in message["message"] for message in messages)
    service.finish(db, run.id, "date-worker", run.fencing_token)
    db.commit()
    assert run.status == "completed"  # Validly filtered results are not execution failures.


def test_date_reference_is_snapshot_time_not_later_ingestion_time(kalibrr):
    _client, _db, run = kalibrr
    assert evaluate(run, "kalibrr", "2026-10-29")[0] == "within_range"
    # Another run beginning a day later has a different window; no wall-clock dependence.
    run.queued_at = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    assert evaluate(run, "kalibrr", "2026-10-29")[0] == "outside_range"


def test_historical_config_is_not_reinterpreted(kalibrr):
    _client, db, run = kalibrr
    run.config = {key: value for key, value in run.config.items() if key != "post_filter_days"}
    db.commit()
    ingest(db, run, "2020-01-01")
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_events_cursor_and_owner_isolation(kalibrr):
    client, db, run = kalibrr
    ingest(db, run, "2026-11-04")
    first = client.get(f"/api/v1/crawler/runs/{run.id}/events?limit=2").json()
    assert first["next_after"] == 2 and first["has_more"]
    later = client.get(f"/api/v1/crawler/runs/{run.id}/events?after=2").json()
    assert all(event["sequence_number"] > 2 for event in later["items"])
    assert (
        client.get(f"/api/v1/crawler/runs/{run.id}/events?after={later['next_after']}").json()[
            "items"
        ]
        == []
    )
    assert "Build pipelines" not in str(first) + str(later)  # No raw job descriptions in logs.
    app.dependency_overrides[get_actor] = lambda: ActorContext(uuid.uuid4())
    try:
        assert client.get(f"/api/v1/crawler/runs/{run.id}/events").status_code == 404
    finally:
        app.dependency_overrides.pop(get_actor, None)
