import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.workflow import Application, Job, JobMatch, Reminder
from app.schemas.dashboard import DashboardApplication, DashboardReminder, DashboardResponse
from app.schemas.workflow import ApplicationStatus
from app.services.jobs import summarize_job

JAKARTA = ZoneInfo("Asia/Jakarta")
ACTIVE_STAGES = ("applied", "assessment", "interview", "offer")
PREVIEW_LIMIT = 5


def utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=JAKARTA).astimezone(UTC)


def dashboard_summary(
    db: Session, owner_id: uuid.UUID, from_date: date | None = None, to_date: date | None = None,
) -> DashboardResponse:
    now = datetime.now(UTC)
    today = now.astimezone(JAKARTA).date()
    horizon = today + timedelta(days=7)
    jobs_scope = [Job.owner_id == owner_id, Job.deleted_at.is_(None)]
    applications_scope = [Application.owner_id == owner_id, *jobs_scope]
    if from_date:
        jobs_scope.append(Job.discovered_at >= utc_midnight(from_date))
        applications_scope.append(or_(
            Application.application_date >= from_date,
            (Application.application_date.is_(None)) & (Application.created_at >= utc_midnight(from_date)),
        ))
    if to_date:
        end = utc_midnight(to_date + timedelta(days=1))
        jobs_scope.append(Job.discovered_at < end)
        applications_scope.append(or_(
            Application.application_date <= to_date,
            (Application.application_date.is_(None)) & (Application.created_at < end),
        ))

    job_counts = dict(db.execute(select(Job.status, func.count()).where(*jobs_scope)
                                .group_by(Job.status)).all())
    application_counts = dict(db.execute(select(Application.status, func.count())
                                         .join(Job, Job.id == Application.job_id)
                                         .where(*applications_scope)
                                         .group_by(Application.status)).all())
    distribution = {status: application_counts.get(status.value, 0) for status in ApplicationStatus}
    recent_jobs = db.scalars(select(Job).where(*jobs_scope)
                            .order_by(Job.discovered_at.desc(), Job.id.desc()).limit(PREVIEW_LIMIT)).all()
    recent_applications = db.execute(select(Application, Job).join(Job, Job.id == Application.job_id)
                                    .where(*applications_scope, Application.status != "preparing")
                                    .order_by(Application.updated_at.desc(), Application.id.desc())
                                    .limit(PREVIEW_LIMIT)).all()
    latest_score = (select(JobMatch.score).where(
        JobMatch.job_id == Job.id, JobMatch.owner_id == owner_id,
    ).order_by(JobMatch.calculated_at.desc(), JobMatch.id.desc()).limit(1).scalar_subquery())
    already_applied = select(Application.id).where(
        Application.job_id == Job.id, Application.owner_id == owner_id,
        Application.application_date.is_not(None),
    ).exists()
    priority_jobs = db.scalars(select(Job).where(
        *jobs_scope, Job.status == "saved", latest_score >= 80, ~already_applied,
    ).order_by(latest_score.desc(), Job.discovered_at.desc(), Job.id.desc()).limit(PREVIEW_LIMIT)).all()

    follow_up_end = min(horizon, to_date) if to_date else horizon
    follow_up_query = select(Application, Job).join(Job, Job.id == Application.job_id).where(
        Application.owner_id == owner_id, Job.owner_id == owner_id, Job.deleted_at.is_(None),
        Application.status.in_(ACTIVE_STAGES), Application.follow_up_date <= follow_up_end,
    )
    if from_date:
        follow_up_query = follow_up_query.where(Application.follow_up_date >= from_date)
    follow_ups = db.execute(follow_up_query.order_by(Application.follow_up_date, Application.id)
                           .limit(PREVIEW_LIMIT)).all()
    reminder_query = (select(Reminder, Application, Job)
                      .outerjoin(Application, Application.id == Reminder.application_id)
                      .outerjoin(Job, Job.id == func.coalesce(Reminder.job_id, Application.job_id))
                      .where(Reminder.owner_id == owner_id, Reminder.status == "pending",
                             Reminder.due_at < utc_midnight(follow_up_end + timedelta(days=1)),
                             or_(Reminder.application_id.is_(None),
                                 (Application.owner_id == owner_id) & Application.status.in_(ACTIVE_STAGES)),
                             or_((Reminder.job_id.is_(None)) & (Reminder.application_id.is_(None)),
                                 (Job.owner_id == owner_id) & Job.deleted_at.is_(None))))
    if from_date:
        reminder_query = reminder_query.where(Reminder.due_at >= utc_midnight(from_date))
    reminders = db.execute(reminder_query.order_by(Reminder.due_at, Reminder.id)
                           .limit(PREVIEW_LIMIT)).all()
    upcoming = []
    reminder_keys = set()
    for reminder, _application, job in reminders:
        due_at = reminder.due_at.replace(tzinfo=UTC) if reminder.due_at.tzinfo is None else reminder.due_at
        due_date = due_at.astimezone(JAKARTA).date()
        reminder_keys.add((reminder.application_id, due_date))
        upcoming.append(DashboardReminder(
            id=reminder.id, kind="reminder", title=reminder.title,
            company=job.company if job else None, application_id=reminder.application_id,
            job_id=job.id if job else None, due_date=due_date, due_at=due_at, overdue=due_at < now,
        ))
    for application, job in follow_ups:
        if (application.id, application.follow_up_date) in reminder_keys:
            continue
        upcoming.append(DashboardReminder(
            id=application.id, kind="follow_up", title=job.title, company=job.company,
            application_id=application.id, job_id=job.id,
            due_date=application.follow_up_date, overdue=application.follow_up_date < today,
        ))
    upcoming.sort(key=lambda item: (item.due_date, str(item.id)))

    return DashboardResponse(
        jobs_total=sum(job_counts.values()), saved_jobs_total=job_counts.get("saved", 0),
        archived_jobs_total=job_counts.get("archived", 0),
        active_applications_total=sum(application_counts.get(status, 0) for status in ACTIVE_STAGES),
        application_status_distribution=distribution,
        recent_applications=[DashboardApplication(
            id=application.id, job=summarize_job(db, job), status=application.status,
            application_date=application.application_date, follow_up_date=application.follow_up_date,
            updated_at=application.updated_at,
        ) for application, job in recent_applications],
        recent_jobs=[summarize_job(db, job) for job in recent_jobs],
        upcoming_reminders=upcoming[:PREVIEW_LIMIT],
        high_priority_jobs=[summarize_job(db, job) for job in priority_jobs], generated_at=now,
    )
