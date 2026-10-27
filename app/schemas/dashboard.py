import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.workflow import ApplicationStatus, JobSummary


class DashboardApplication(BaseModel):
    id: uuid.UUID
    job: JobSummary
    status: ApplicationStatus
    application_date: date | None
    follow_up_date: date | None
    updated_at: datetime


class DashboardReminder(BaseModel):
    id: uuid.UUID
    kind: Literal["reminder", "follow_up"]
    title: str
    company: str | None = None
    application_id: uuid.UUID | None
    job_id: uuid.UUID | None
    due_date: date
    due_at: datetime | None = None
    overdue: bool


class DashboardResponse(BaseModel):
    jobs_total: int
    saved_jobs_total: int
    archived_jobs_total: int
    active_applications_total: int
    application_status_distribution: dict[ApplicationStatus, int]
    recent_applications: list[DashboardApplication]
    recent_jobs: list[JobSummary]
    upcoming_reminders: list[DashboardReminder]
    high_priority_jobs: list[JobSummary]
    generated_at: datetime
