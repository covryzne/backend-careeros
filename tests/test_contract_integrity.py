from sqlalchemy import event


def test_preparing_application_can_be_confirmed_without_duplicate(jobs_client):
    client, _ = jobs_client
    job = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"}).json()
    application = client.post("/api/v1/applications", json={"job_id": job["id"]}).json()
    response = client.patch(
        "/api/v1/applications/" + application["id"],
        json={
            "status": "applied",
            "application_date": "2026-10-27",
            "application_channel": "manual",
            "expected_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "applied"
    assert response.json()["version"] == 2
    assert (
        len(
            [
                a
                for a in response.json()["activities"]
                if a["activity_type"] == "application_submitted"
            ]
        )
        == 1
    )
    assert client.get("/api/v1/applications").json()["total"] == 1


def test_batched_job_and_application_queries_do_not_grow_per_card(jobs_client):
    client, db = jobs_client
    for index in range(20):
        job = client.post(
            "/api/v1/jobs", json={"title": f"Engineer {index}", "company": "Example"}
        ).json()
        client.post(
            "/api/v1/applications",
            json={
                "job_id": job["id"],
                "status": "applied",
                "application_date": "2026-10-27",
                "application_channel": "manual",
            },
        )
    counts = []

    def record(_conn, _cursor, statement, _params, _ctx, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            counts.append(statement)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        for resource in ("jobs", "applications"):
            counts.clear()
            assert client.get(f"/api/v1/{resource}?page_size=1").status_code == 200
            one = len(counts)
            counts.clear()
            response = client.get(f"/api/v1/{resource}?page_size=20")
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) == 20
            assert len(counts) <= one + 1
    finally:
        event.remove(db.bind, "before_cursor_execute", record)


def test_decimal_noop_and_required_null_validation(jobs_client):
    client, _ = jobs_client
    first = client.patch(
        "/api/v1/profile",
        json={"full_name": "Candidate", "preferred_salary_min": 0.1, "preferred_salary_max": 0.2},
    ).json()
    assert first["profile_version"] == 1
    assert (
        client.patch("/api/v1/profile", json={"preferred_salary_min": 0.1}).json()[
            "profile_version"
        ]
        == 1
    )
    assert client.patch("/api/v1/profile", json={"preferred_salary_min": 10}).status_code == 422
    assert (
        client.post(
            "/api/v1/jobs",
            json={"title": "Engineer", "company": "Example", "salary_min": 2, "salary_max": 1},
        ).status_code
        == 422
    )
    job = client.post(
        "/api/v1/jobs",
        json={
            "title": "Engineer",
            "company": "Example",
            "original_url": "https://example.com/jobs/1#section",
        },
    ).json()
    assert (
        client.post(
            "/api/v1/jobs",
            json={
                "title": "Engineer",
                "company": "Example",
                "original_url": "https://example.com/jobs/1",
            },
        ).status_code
        == 409
    )
    assert client.patch("/api/v1/jobs/" + job["id"], json={"status": None}).status_code == 422


def test_unknown_job_score_is_not_a_fabricated_zero(jobs_client):
    client, _ = jobs_client
    job = client.post(
        "/api/v1/jobs",
        json={
            "title": "Engineer",
            "company": "Example",
            "description": "Actual stored description",
        },
    ).json()
    detail = client.get("/api/v1/jobs/" + job["id"]).json()
    assert detail["description"] == "Actual stored description"
    assert detail["match_score"] is None
    assert detail["is_match_stale"] is False


def test_reminder_pagination_updates_and_document_metadata(jobs_client, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "document_storage_dir", tmp_path)
    client, _ = jobs_client
    client.patch("/api/v1/profile", json={"full_name": "Candidate"})
    uploaded = client.post(
        "/api/v1/profile/documents",
        files={"file": ("cv.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    job = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"}).json()
    app = client.post(
        "/api/v1/applications", json={"job_id": job["id"], "selected_document_id": document["id"]}
    ).json()
    assert app["selected_document"]["id"] == document["id"]
    assert "storage_key" not in app["selected_document"]
    reminder = client.post(
        "/api/v1/reminders",
        json={"application_id": app["id"], "title": "Follow up", "due_at": "2026-11-01T02:00:00Z"},
    ).json()
    page = client.get("/api/v1/reminders?page_size=1").json()
    assert page["total"] == 1 and page["has_next"] is False
    assert client.patch("/api/v1/reminders/" + reminder["id"], json={"status": "completed"}).json()[
        "completed_at"
    ]
    assert client.get("/api/v1/reminders?status=pending").json()["total"] == 0
    detail = client.get("/api/v1/applications/" + app["id"]).json()
    listed = client.get("/api/v1/applications").json()["items"][0]
    assert detail["selected_document"] == listed["selected_document"]
    assert detail["reminders"][0]["id"] == reminder["id"]
    assert (
        client.patch(
            "/api/v1/reminders/" + reminder["id"], json={"due_at": "2026-11-01T02:00:00"}
        ).status_code
        == 422
    )


def test_request_body_limit_rejects_before_mutation(jobs_client):
    client, _ = jobs_client
    response = client.post(
        "/api/v1/jobs",
        content="x" * (1024 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert client.get("/api/v1/jobs").json()["total"] == 0


def test_agent_scope_filters_before_pagination(jobs_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_enabled", True)
    import uuid

    from app.agent.context import ToolContext
    from app.core.security import LOCAL_OWNER_ID, ActorContext
    from app.schemas.agent import SearchArguments
    from app.services.agent_facade import AgentServices

    client, db = jobs_client
    target = client.post("/api/v1/jobs", json={"title": "Allowed", "company": "Example"}).json()
    client.post("/api/v1/jobs", json={"title": "Outside", "company": "Example"})
    context = ToolContext(
        ActorContext(LOCAL_OWNER_ID, "agent"),
        uuid.uuid4(),
        frozenset({"search_jobs"}),
        {"job_ids": [target["id"]]},
    )
    result = AgentServices(db).invoke(context, "search_jobs", SearchArguments(page_size=1))
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == target["id"]
    assert result["has_next"] is False


def test_database_numeric_and_string_limits_are_validated_before_write(jobs_client):
    client, _ = jobs_client
    assert (
        client.post(
            "/api/v1/jobs", json={"title": "Engineer", "company": "Example", "salary_min": 1e30}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/jobs",
            json={"title": "Engineer", "company": "Example", "source_name": "x" * 121},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/jobs",
            json={"title": "Engineer", "company": "Example", "original_url": "https://["},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/v1/profile", json={"full_name": "Candidate", "preferred_salary_min": 1e30}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/jobs").json()["total"] == 0
