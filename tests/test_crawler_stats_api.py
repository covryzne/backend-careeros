import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import event, select

from app.core.security import LOCAL_OWNER_ID, ActorContext, get_actor
from app.main import app
from app.models.candidate import User
from app.models.crawler import CrawlRun, CrawlRunSource
from app.models.crawler_stats import CrawlerStatsPublication
from app.services.crawler.stats import _publish


def seed(db, owner_id):
    db.add(User(id=owner_id))
    db.flush()
    run_id = uuid.uuid4()
    db.add(
        CrawlRun(
            id=run_id,
            owner_id=owner_id,
            idempotency_key=str(run_id),
            payload_hash="test",
            config={},
            status="completed",
            started_at=datetime(2026, 10, 3, 18, tzinfo=UTC),
        )
    )
    db.flush()
    db.add_all(
        [
            CrawlRunSource(run_id=run_id, platform="linkedin", jobs_new=7, jobs_duplicate=2),
            CrawlRunSource(run_id=run_id, platform="jobstreet", jobs_new=3),
        ]
    )
    db.commit()


def publish(db):
    _publish(db.connection(), date(2026, 10, 1))
    db.commit()


def test_read_stored_outcomes_and_distinct_runs_in_one_statement(jobs_client):
    client, db = jobs_client
    seed(db, LOCAL_OWNER_ID)
    seed(db, uuid.uuid4())
    publish(db)
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        response = client.get("/api/v1/crawler/stats?from=2026-10-03&to=2026-10-05")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["totals"]["jobs_crawled_successfully"] == 12
    assert result["runs"]["runs_started"] == 1
    assert sum(item["runs"]["runs_started"] for item in result["platforms"]) == 2
    assert result["series"][0]["outcomes"]["jobs_new"] == 0
    assert result["series"][1]["outcomes"]["jobs_new"] == 10
    assert result["series"][1]["runs"]["runs_started"] == 1
    assert result["coverage"] == "complete" and result["history_basis"] == "retained_runs"
    assert len(queries) == 1
    assert "crawl_runs" not in queries[0] and "crawl_run_sources" not in queries[0]
    filtered = client.get(
        "/api/v1/crawler/stats?from=2026-10-03&to=2026-10-05&platform=linkedin"
    ).json()
    assert filtered["totals"]["jobs_crawled_successfully"] == 9
    assert filtered["runs"]["runs_started"] == 1
    assert [item["platform"] for item in filtered["platforms"]] == ["linkedin"]


def test_coverage_unknown_dates_empty_periods_and_stale(jobs_client):
    client, db = jobs_client
    seed(db, LOCAL_OWNER_ID)
    publish(db)
    response = client.get("/api/v1/crawler/stats?from=2026-09-30&to=2026-10-02").json()
    assert response["coverage"] == "partial"
    assert response["series"][0]["outcomes"] is None
    assert response["series"][1]["outcomes"]["jobs_new"] == 0
    assert response["totals"]["jobs_new"] == 0
    unknown = client.get("/api/v1/crawler/stats?from=2026-09-01&to=2026-09-02").json()
    assert unknown["coverage"] == "none" and unknown["totals"] is None
    assert unknown["runs"] is None
    future = client.get("/api/v1/crawler/stats?from=9999-01-01&to=9999-01-02").json()
    assert future["totals"] is None
    metadata = db.get(CrawlerStatsPublication, LOCAL_OWNER_ID)
    metadata.source_snapshot_at = datetime.now(UTC) - timedelta(minutes=11)
    db.commit()
    assert client.get("/api/v1/crawler/stats?from=2026-10-03&to=2026-10-05").json()["stale"]


def test_not_ready_permissions_validation_and_no_publication_on_get(jobs_client):
    client, db = jobs_client
    response = client.get("/api/v1/crawler/stats?from=2026-10-01&to=2026-10-03")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CRAWLER_STATS_NOT_READY"
    assert db.scalar(select(CrawlerStatsPublication)) is None
    for query in (
        "from=2026-10-03&to=2026-10-01",
        "from=2024-01-01&to=2026-10-03",
        "from=invalid&to=2026-10-03",
        "from=2026-10-01",
        "from=2026-10-01&to=2026-10-03&platform=",
    ):
        assert client.get("/api/v1/crawler/stats?" + query).status_code == 422
    app.dependency_overrides[get_actor] = lambda: ActorContext(
        LOCAL_OWNER_ID, "agent", permissions=frozenset()
    )
    try:
        assert client.get("/api/v1/crawler/stats?from=2026-10-01&to=2026-10-03").status_code == 403
    finally:
        app.dependency_overrides.pop(get_actor, None)


def test_old_definition_requires_refresh(jobs_client):
    client, db = jobs_client
    seed(db, LOCAL_OWNER_ID)
    publish(db)
    db.get(CrawlerStatsPublication, LOCAL_OWNER_ID).definition_version = 1
    db.commit()
    assert client.get("/api/v1/crawler/stats?from=2026-10-01&to=2026-10-03").status_code == 503
