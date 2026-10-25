import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    NEW = "new"
    SAVED = "saved"
    INTERESTED = "interested"
    DISMISSED = "dismissed"
    ARCHIVED = "archived"


class ApplicationStatus(StrEnum):
    PREPARING = "preparing"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ApplicationChannel(StrEnum):
    CAREER_PAGE = "career_page"
    LINKEDIN = "linkedin"
    JOBSTREET = "jobstreet"
    GLINTS = "glints"
    REFERRAL = "referral"
    EMAIL = "email"
    MANUAL = "manual"
    OTHER = "other"


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    company: str = Field(min_length=1, max_length=240)
    original_url: str | None = None
    description: str | None = None
    location: str | None = None
    work_arrangement: str = "unknown"
    employment_type: str = "unknown"
    salary_text: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    source_name: str | None = None
    skills: list[str] = []
    status: JobStatus = JobStatus.SAVED


class JobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    company: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    notes: str | None = None
    status: JobStatus | None = None


class JobSummary(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    company: str
    original_url: str | None
    location: str | None
    work_arrangement: str
    employment_type: str
    salary_text: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    posting_date: date | None
    application_deadline: date | None
    discovered_at: datetime
    status: JobStatus
    source_id: uuid.UUID | None
    source_name: str | None = None
    skills: list[str] = []
    match_score: float | None = None


class JobListResponse(BaseModel):
    items: list[JobSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ApplicationCreate(BaseModel):
    job_id: uuid.UUID
    status: ApplicationStatus = ApplicationStatus.PREPARING
    application_date: date | None = None
    application_channel: ApplicationChannel | None = None
    selected_document_id: uuid.UUID | None = None
    notes: str | None = None
    follow_up_date: date | None = None


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    follow_up_date: date | None = None
    notes: str | None = None
    expected_version: int | None = Field(default=None, ge=1)


class ReminderCreate(BaseModel):
    application_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    due_at: datetime


class AgentRunCreate(BaseModel):
    task_type: str = Field(min_length=1, max_length=60)
    instruction: str = Field(min_length=1)
    scope: dict = {}
    allowed_tools: list[str] = []
