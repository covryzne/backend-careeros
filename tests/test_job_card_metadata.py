import uuid

from app.core.security import LOCAL_OWNER_ID
from app.models.crawler import CrawlRun, CrawlRunItem


def test_job_cards_use_owned_crawl_keywords_and_description_preview(jobs_client):
    client, db = jobs_client
    description = ("Build APIs.\n" + "Keep services reliable. " * 40).strip()
    created = client.post(
        "/api/v1/jobs",
        json={
            "title": "Senior Software Engineer",
            "company": "Example",
            "source_name": "LinkedIn",
            "description": description,
            "salary_text": "IDR 15–20 juta",
            "skills": ["Python"],
        },
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    for owner, keyword, status in [
        (LOCAL_OWNER_ID, "Backend Developer", "new"),
        (LOCAL_OWNER_ID, "Backend Developer", "duplicate"),
        (LOCAL_OWNER_ID, "Python Engineer", "duplicate"),
        (LOCAL_OWNER_ID, "  ", "duplicate"),
        (LOCAL_OWNER_ID, "Filtered keyword", "filtered_date"),
        (uuid.uuid4(), "Private keyword", "new"),
    ]:
        run = CrawlRun(
            owner_id=owner, config={}, idempotency_key=str(uuid.uuid4()), payload_hash="test",
            status="completed", execution_slot=None,
        )
        db.add(run)
        db.flush()
        db.add(CrawlRunItem(
            run_id=run.id, platform="linkedin", identity_key=str(uuid.uuid4()),
            external_id="1", keyword=keyword, title="Senior Software Engineer",
            url="https://www.linkedin.com/jobs/view/1", status=status,
            job_id=uuid.UUID(job_id),
        ))
        db.flush()
    db.commit()

    listing = client.get("/api/v1/jobs").json()["items"][0]
    detail_response = client.get(f"/api/v1/jobs/{job_id}")
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    for record in [listing, detail]:
        assert set(record["crawler_keywords"]) == {"Backend Developer", "Python Engineer"}
        assert len(record["crawler_keywords"]) == 2
        assert record["source_name"] == "LinkedIn"
        assert record["description_preview"] == " ".join(description.split())[:500]
        assert record["salary_text"] == "IDR 15–20 juta"
        assert record["skills"] == ["Python"]
    assert detail["description"] == description
    assert len(detail["description"]) > len(listing["description_preview"])


def test_manual_job_has_no_invented_keyword_or_description(jobs_client):
    client, _ = jobs_client
    response = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"})
    assert response.status_code == 201
    result = client.get("/api/v1/jobs").json()["items"][0]
    assert result["crawler_keywords"] == []
    assert result["description_preview"] is None
    assert result["skills"] == []
    assert result["salary_text"] is None
