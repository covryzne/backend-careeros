import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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

    @model_validator(mode="after")
    def validate_manual_application(self):
        if self.status not in {ApplicationStatus.PREPARING, ApplicationStatus.APPLIED}:
            raise ValueError("New applications must be preparing or applied")
        if self.status == ApplicationStatus.APPLIED and (
            self.application_date is None or self.application_channel is None
        ):
            raise ValueError("Application date and channel are required for manual apply")
        return self


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    follow_up_date: date | None = None
    notes: str | None = None
    expected_version: int = Field(ge=1)
    change_kind: Literal["move", "undo"] = "move"

    @model_validator(mode="after")
    def validate_undo_payload(self):
        if self.change_kind == "undo" and (
            self.status is None or self.model_fields_set & {"notes", "follow_up_date"}
        ):
            raise ValueError("Undo requires a target status and cannot edit other fields")
        return self


class ApplicationActivitySummary(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    activity_type: str
    title: str
    description: str | None
    occurred_at: datetime
    source: str
    metadata_json: dict


class ApplicationDetail(BaseModel):
    id: uuid.UUID
    job: JobSummary
    status: ApplicationStatus
    application_date: date | None
    application_channel: ApplicationChannel | None
    selected_document: dict | None = None
    notes: str | None
    follow_up_date: date | None
    activities: list[ApplicationActivitySummary]
    reminders: list[dict] = []
    version: int
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(BaseModel):
    items: list[ApplicationDetail]
    page: int
    page_size: int
    total: int
    total_pages: int


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
