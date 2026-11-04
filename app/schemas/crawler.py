from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CrawlCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=5
    )
    platforms: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        min_length=1, max_length=7
    )
    location: str = Field(default="Indonesia", min_length=1, max_length=240)
    linkedin_geo_id: str = Field(default="102478259", pattern=r"^\d{1,20}$")
    days: int = Field(default=7, ge=1, le=30)
    max_pages: int = Field(default=2, ge=1, le=20)
    max_jobs_per_platform: int = Field(default=20, ge=1, le=200)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    delay_seconds: float = Field(default=3, ge=1, le=60, allow_inf_nan=False)

    @field_validator("keywords", "platforms")
    @classmethod
    def normalized_list(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("Empty values are not allowed")
            if value.casefold() not in {item.casefold() for item in result}:
                result.append(value)
        return result

    @field_validator("location")
    @classmethod
    def normalized_location(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Location cannot be blank")
        return value


class SourceRead(BaseModel):
    platform: str
    status: str
    jobs_found: int
    jobs_new: int
    jobs_duplicate: int
    jobs_rejected: int
    jobs_filtered: int
    jobs_date_unknown: int
    detail_attempts: int
    pages_completed: int
    error: str | None


class RunRead(BaseModel):
    run_id: UUID
    trigger_type: str
    status: str
    config: dict
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    error: str | None
    jobs_found: int
    jobs_new: int
    jobs_duplicate: int
    jobs_rejected: int
    jobs_filtered: int
    jobs_date_unknown: int
    sources: list[SourceRead]


class RunPage(BaseModel):
    items: list[RunRead]
    page: int
    page_size: int
    total: int
    has_next: bool


class AdapterRead(BaseModel):
    platform: str
    status: str
    date_filter: str
    default_headless: bool
    location_scope: str


class CapabilitiesRead(BaseModel):
    engine: str
    platforms: list[AdapterRead]
    worker_online: bool
    active: bool
    active_run_id: UUID | None
    limits: dict[str, int]


class ItemRead(BaseModel):
    id: UUID
    platform: str
    keyword: str
    title: str
    url: str
    status: str
    job_id: UUID | None
    error: str | None
    date_check: str | None = None
    posting_date: str | None = None


class ItemPage(BaseModel):
    items: list[ItemRead]
    page: int
    page_size: int
    total: int
    has_next: bool


class CrawlEventRead(BaseModel):
    sequence_number: int
    created_at: datetime
    level: str
    platform: str | None
    message: str


class CrawlEventPage(BaseModel):
    items: list[CrawlEventRead]
    next_after: int
    has_more: bool
