import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.api.routes.profile import LOCAL_OWNER_ID
from app.models.candidate import CandidateProfile
from app.models.workflow import Application, Job, JobMatch, Reminder
from app.services.dashboard import JAKARTA, utc_midnight


def add_job(db, *, status="saved", owner_id=LOCAL_OWNER_ID, deleted=False, discovered_at=None):
    job = Job(owner_id=owner_id, title="Engineer", company="Example", status=status,
              discovered_at=discovered_at or datetime.now(UTC),
              deleted_at=datetime.now(UTC) if deleted else None)
    db.add(job)
    db.flush()
    return job


def add_application(db, job, *, status="applied", follow_up_date=None, application_date=None):
    application = Application(
        owner_id=job.owner_id, job_id=job.id, status=status,
        application_date=application_date or datetime.now(JAKARTA).date(),
        application_channel="manual", follow_up_date=follow_up_date,
    )
    db.add(application)
    db.flush()
    return application


def add_match(db, job, score, *, calculated_at=None, owner_id=None, match_id=None):
    actual_owner = owner_id or job.owner_id
    profile = db.scalar(select(CandidateProfile).where(CandidateProfile.owner_id == actual_owner))
    if profile is None:
        profile = CandidateProfile(owner_id=actual_owner, full_name="Test Candidate")
        db.add(profile)
        db.flush()
    match = JobMatch(
        owner_id=owner_id or job.owner_id, job_id=job.id, profile_id=profile.id,
        profile_version=1, score=score, algorithm_version="test-" + str(uuid.uuid4()),
        calculated_at=calculated_at or datetime.now(UTC),
    )
    if match_id:
        match.id = match_id
    db.add(match)


def test_dashboard_empty_returns_real_zero_counts(jobs_client):
    client, _db = jobs_client
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()
    for key in ("jobs_total", "saved_jobs_total", "archived_jobs_total", "active_applications_total"):
        assert data[key] == 0
    assert all(count == 0 for count in data["application_status_distribution"].values())
    for key in ("recent_jobs", "recent_applications", "upcoming_reminders", "high_priority_jobs"):
        assert data[key] == []
    assert datetime.fromisoformat(data["generated_at"]).tzinfo is not None


def test_dashboard_counts_all_rows_and_respects_owner_and_deleted_jobs(jobs_client):
    client, db = jobs_client
    for index in range(160):
        add_job(db, status="saved" if index < 120 else "archived" if index < 150 else "new")
    statuses = ["preparing", "applied", "assessment", "interview", "offer", "rejected", "withdrawn"]
    for stage in statuses:
        add_application(db, add_job(db), status=stage)
    foreign = add_job(db, owner_id=uuid.uuid4())
    add_application(db, foreign, status="interview")
    deleted = add_job(db, deleted=True)
    add_application(db, deleted, status="offer")
    db.commit()
    data = client.get("/api/v1/dashboard").json()
    assert data["jobs_total"] == 167
    assert data["saved_jobs_total"] == 127
    assert data["archived_jobs_total"] == 30
    assert data["active_applications_total"] == 4
    assert data["application_status_distribution"] == {stage: 1 for stage in statuses}
    assert len(data["recent_jobs"]) == len(data["recent_applications"]) == 5
    assert all(item["status"] != "preparing" for item in data["recent_applications"])
    hidden = {str(foreign.id), str(deleted.id)}
    assert not hidden.intersection(item["id"] for item in data["recent_jobs"])
    assert not hidden.intersection(item["job"]["id"] for item in data["recent_applications"])
    assert data["saved_jobs_total"] == client.get("/api/v1/jobs?status=saved").json()["total"]


def test_priority_uses_latest_match_and_only_saved_unapplied_jobs(jobs_client):
    client, db = jobs_client
    eligible = add_job(db)
    add_match(db, eligible, 80)
    dropped = add_job(db)
    add_match(db, dropped, 99, calculated_at=datetime.now(UTC) - timedelta(days=1))
    add_match(db, dropped, 40)
    applied = add_job(db)
    add_match(db, applied, 99)
    add_application(db, applied)
    archived = add_job(db, status="archived")
    add_match(db, archived, 99)
    foreign_match = add_job(db)
    add_match(db, foreign_match, 99, owner_id=uuid.uuid4())
    tied = add_job(db)
    when = datetime.now(UTC)
    add_match(db, tied, 95, calculated_at=when, match_id=uuid.UUID(int=1))
    add_match(db, tied, 85, calculated_at=when, match_id=uuid.UUID(int=2))
    db.commit()
    data = client.get("/api/v1/dashboard").json()
    assert [(item["id"], item["match_score"]) for item in data["high_priority_jobs"]] == [
        (str(tied.id), 85), (str(eligible.id), 80),
    ]


def test_follow_ups_and_reminders_use_jakarta_dates_and_skip_inactive_or_completed(jobs_client):
    client, db = jobs_client
    today = datetime.now(JAKARTA).date()
    overdue = add_application(db, add_job(db), follow_up_date=today - timedelta(days=1))
    upcoming = add_application(db, add_job(db), follow_up_date=today + timedelta(days=7))
    add_application(db, add_job(db), follow_up_date=today + timedelta(days=8))
    add_application(db, add_job(db), status="rejected", follow_up_date=today)
    add_application(db, add_job(db, deleted=True), follow_up_date=today)
    add_application(db, add_job(db, owner_id=uuid.uuid4()), follow_up_date=today)
    real_reminder = Reminder(
        owner_id=LOCAL_OWNER_ID, application_id=upcoming.id,
        title="Contact recruiter", due_at=utc_midnight(today + timedelta(days=7)),
    )
    local_boundary = Reminder(
        owner_id=LOCAL_OWNER_ID, title="Yesterday in Jakarta",
        due_at=utc_midnight(today) - timedelta(minutes=30),
    )
    db.add_all([
        real_reminder, local_boundary,
        Reminder(owner_id=LOCAL_OWNER_ID, title="Completed", status="completed", due_at=utc_midnight(today)),
        Reminder(owner_id=LOCAL_OWNER_ID, title="Future", due_at=utc_midnight(today + timedelta(days=8))),
        Reminder(owner_id=uuid.uuid4(), title="Foreign", due_at=utc_midnight(today)),
    ])
    db.commit()
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    items = response.json()["upcoming_reminders"]
    assert len(items) == 3
    by_id = {item["id"]: item for item in items}
    assert by_id[str(overdue.id)]["kind"] == "follow_up"
    assert by_id[str(overdue.id)]["overdue"] is True
    assert by_id[str(real_reminder.id)]["application_id"] == str(upcoming.id)
    assert str(upcoming.id) not in by_id
    assert by_id[str(local_boundary.id)]["due_date"] == str(today - timedelta(days=1))
    assert by_id[str(local_boundary.id)]["overdue"] is True


def test_dashboard_date_range_uses_local_job_day_and_actual_application_date(jobs_client):
    client, db = jobs_client
    day = date(2026, 10, 1)
    inside = add_job(db, discovered_at=utc_midnight(day))
    outside = add_job(db, discovered_at=utc_midnight(day) - timedelta(microseconds=1))
    add_application(db, inside, application_date=day)
    add_application(db, outside, application_date=day - timedelta(days=1))
    db.commit()
    data = client.get("/api/v1/dashboard?from=2026-10-01&to=2026-10-01").json()
    assert data["jobs_total"] == 1
    assert data["active_applications_total"] == 1
    assert data["recent_jobs"][0]["id"] == str(inside.id)
    assert data["recent_applications"][0]["job"]["id"] == str(inside.id)
    assert client.get("/api/v1/dashboard?from=2026-10-02&to=2026-10-01").status_code == 422
    assert client.get("/api/v1/dashboard?from=invalid").status_code == 422


def test_dashboard_hides_reminders_linked_to_foreign_or_deleted_records(jobs_client):
    client, db = jobs_client
    foreign_job = add_job(db, owner_id=uuid.uuid4())
    foreign_application = add_application(db, foreign_job)
    deleted_job = add_job(db, deleted=True)
    when = datetime.now(UTC) - timedelta(hours=1)
    db.add_all([
        Reminder(owner_id=LOCAL_OWNER_ID, job_id=foreign_job.id, title="Foreign job", due_at=when),
        Reminder(owner_id=LOCAL_OWNER_ID, application_id=foreign_application.id,
                 title="Foreign application", due_at=when),
        Reminder(owner_id=LOCAL_OWNER_ID, job_id=deleted_job.id, title="Deleted job", due_at=when),
    ])
    db.commit()
    assert client.get("/api/v1/dashboard").json()["upcoming_reminders"] == []


def test_dashboard_follows_apply_move_undo_archive_restore_and_delete(jobs_client):
    client, _db = jobs_client
    job = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"}).json()
    application = client.post("/api/v1/applications", json={
        "job_id": job["id"], "status": "applied", "application_date": "2026-10-27",
        "application_channel": "manual", "follow_up_date": str(datetime.now(JAKARTA).date()),
    }).json()
    path = f'/api/v1/applications/{application["id"]}'
    assert client.get("/api/v1/dashboard").json()["active_applications_total"] == 1
    assert client.patch(path, json={"status": "interview", "expected_version": 1}).status_code == 200
    assert client.get("/api/v1/dashboard").json()["application_status_distribution"]["interview"] == 1
    assert client.patch(path, json={"status": "applied", "expected_version": 2, "change_kind": "undo"}).status_code == 200
    assert client.get("/api/v1/dashboard").json()["application_status_distribution"]["interview"] == 0
    client.patch(f'/api/v1/jobs/{job["id"]}', json={"status": "archived"})
    archived = client.get("/api/v1/dashboard").json()
    assert archived["saved_jobs_total"] == 0
    assert archived["archived_jobs_total"] == 1
    assert archived["active_applications_total"] == 1
    client.patch(f'/api/v1/jobs/{job["id"]}', json={"status": "saved"})
    assert client.get("/api/v1/dashboard").json()["saved_jobs_total"] == 1
    client.delete(f'/api/v1/jobs/{job["id"]}')
    empty = client.get("/api/v1/dashboard").json()
    assert empty["jobs_total"] == empty["active_applications_total"] == 0
    assert empty["upcoming_reminders"] == []
