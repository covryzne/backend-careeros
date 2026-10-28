import re
import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

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


class JobContentInput(BaseModel):
    responsibilities: str | None = Field(default=None, max_length=50000)
    requirements: str | None = Field(default=None, max_length=50000)
    experience_requirements: str | None = Field(default=None, max_length=30000)
    preferred_qualifications: str | None = Field(default=None, max_length=30000)
    hr_contact_name: str | None = Field(default=None, max_length=200)
    hr_contact_email: str | None = Field(
        default=None, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
    )
    hr_contact_phone: str | None = Field(
        default=None, max_length=50, pattern=r"^\+?[\d ().-]{8,50}$"
    )

    @model_validator(mode="after")
    def validate_contact_phone(self):
        if (
            self.hr_contact_phone is not None
            and not 8 <= len(re.sub(r"\D", "", self.hr_contact_phone)) <= 16
        ):
            raise ValueError("Contact phone must contain 8–16 digits")
        return self


class JobCreate(JobContentInput):
    title: str = Field(min_length=1, max_length=240)
    company: str = Field(min_length=1, max_length=240)
    original_url: str | None = None
    description: str | None = None
    location: str | None = Field(default=None, max_length=240)
    work_arrangement: Literal["unknown", "remote", "hybrid", "on_site", "flexible"] = "unknown"
    employment_type: Literal[
        "unknown", "full_time", "part_time", "contract", "internship", "temporary"
    ] = "unknown"
    salary_text: str | None = Field(default=None, max_length=200)
    salary_min: float | None = Field(default=None, ge=0, le=999999999999.99)
    salary_max: float | None = Field(default=None, ge=0, le=999999999999.99)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    source_name: str | None = Field(default=None, max_length=120)
    skills: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=100
    )
    status: JobStatus = JobStatus.SAVED


class JobUpdate(JobContentInput):
    @model_validator(mode="after")
    def validate_null(self):
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set & {"title", "company", "status"}
        ):
            raise ValueError("Required job fields cannot be null")
        return self

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
    match_profile_version: int | None = None
    is_match_stale: bool = False
    match_summary: str | None = None
    matching_skills: list[str] = []
    missing_requirements: list[str] = []


class JobDetail(JobSummary):
    description: str | None = None
    notes: str | None = None
    requirements: str | None = None
    responsibilities: str | None = None
    experience_requirements: str | None = None
    preferred_qualifications: str | None = None
    hr_contact_name: str | None = None
    hr_contact_email: str | None = None
    hr_contact_phone: str | None = None


class JobListResponse(BaseModel):
    items: list[JobSummary]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool = False


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
    selected_document_id: uuid.UUID | None = None
    status: ApplicationStatus | None = None
    follow_up_date: date | None = None
    notes: str | None = None
    application_date: date | None = None
    application_channel: ApplicationChannel | None = None
    expected_version: int = Field(ge=1)
    change_kind: Literal["move", "undo"] = "move"

    @model_validator(mode="after")
    def validate_undo_payload(self):
        if self.change_kind == "undo" and (
            self.status is None
            or self.model_fields_set
            & {
                "notes",
                "follow_up_date",
                "application_date",
                "application_channel",
                "selected_document_id",
            }
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
    has_next: bool = False


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


class ReminderPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = None
    due_at: datetime | None = None
    status: Literal["pending", "completed"] | None = None

    @model_validator(mode="after")
    def not_null(self):
        if any(
            getattr(self, key) is None
            for key in self.model_fields_set & {"title", "due_at", "status"}
        ):
            raise ValueError("Required fields cannot be null")
        return self
