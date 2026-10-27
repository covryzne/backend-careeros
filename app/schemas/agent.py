import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunScope(StrictInput):
    document_id: uuid.UUID | None = None
    job_collection: Literal["saved", "crawled", "selected"] | None = None
    job_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    application_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    max_results: int = Field(default=20, ge=1, le=20)


class RunCreate(StrictInput):
    task_type: Literal[
        "daily_briefing",
        "tracking_assistant",
        "application_preparation",
        "draft_generation",
        "career_assistant",
    ]
    instruction: str = Field(min_length=1, max_length=4000)
    scope: RunScope = Field(default_factory=RunScope)
    allowed_tools: list[str] = Field(default_factory=list, max_length=16)


class ProfileArguments(StrictInput):
    include_skills: bool = True
    include_experiences: bool = True


class SearchArguments(StrictInput):
    q: str | None = Field(default=None, max_length=240)
    page: int = Field(default=1, ge=1, le=100)
    page_size: int = Field(default=20, ge=1, le=20)


class JobArguments(StrictInput):
    job_id: uuid.UUID


class ApplicationArguments(StrictInput):
    application_id: uuid.UUID


class DraftArguments(JobArguments):
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    source_profile_version: int = Field(ge=1)
    language: Literal["id", "en"] = "id"


class MoveArguments(ApplicationArguments):
    status: Literal["applied", "assessment", "interview", "offer", "rejected", "withdrawn"]
    expected_version: int = Field(ge=1)


class ConfirmFactArguments(StrictInput):
    fact_id: uuid.UUID
    expected_profile_version: int = Field(ge=1)


class ManualApplyArguments(JobArguments):
    application_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    application_channel: Literal[
        "career_page", "linkedin", "jobstreet", "glints", "referral", "email", "manual", "other"
    ]


class SaveJobArguments(StrictInput):
    title: str = Field(min_length=1, max_length=240)
    company: str = Field(min_length=1, max_length=240)
    original_url: str | None = Field(default=None, max_length=2000)


class ReminderArguments(StrictInput):
    application_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=240)
    due_at: str = Field(max_length=64)


class ProposalArguments(StrictInput):
    source_profile_version: int = Field(ge=1)
    changes: dict = Field(max_length=20)


class CareerContextArguments(StrictInput):
    pass


class Citation(StrictInput):
    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=600)


class GroundedReason(StrictInput):
    text: str = Field(min_length=1, max_length=1000)
    citations: list[Citation] = Field(min_length=1, max_length=4)


BoundedNote = Annotated[str, Field(min_length=1, max_length=1000)]


class JobRecommendation(StrictInput):
    job_id: uuid.UUID
    category: Literal["prioritize", "review", "insufficient"]
    reasons: list[GroundedReason] = Field(min_length=1, max_length=4)
    gaps: list[GroundedReason] = Field(default_factory=list, max_length=4)
    unknowns: list[BoundedNote] = Field(default_factory=list, max_length=5)


class PipelineReview(StrictInput):
    application_id: uuid.UUID
    observation: GroundedReason
    next_step: str = Field(min_length=1, max_length=1000)


class CareerNextStep(StrictInput):
    text: str = Field(min_length=1, max_length=1000)
    job_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    citations: list[Citation] = Field(default_factory=list, max_length=4)


class CareerReport(StrictInput):
    kind: Literal["job_analysis", "pipeline_review", "career_priorities", "general"]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2500)
    recommendations: list[JobRecommendation] = Field(default_factory=list, max_length=5)
    pipeline_reviews: list[PipelineReview] = Field(default_factory=list, max_length=10)
    next_steps: list[CareerNextStep] = Field(default_factory=list, max_length=5)
    limitations: list[BoundedNote] = Field(default_factory=list, max_length=8)


class JobRecommendationResult(JobRecommendation):
    job: dict


class PipelineReviewResult(PipelineReview):
    application: dict


class CareerReportResult(CareerReport):
    recommendations: list[JobRecommendationResult] = Field(default_factory=list, max_length=5)
    pipeline_reviews: list[PipelineReviewResult] = Field(default_factory=list, max_length=10)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: str | None = None
    report: CareerReportResult | None = None
    version: int | None = None
    pending_approval_ids: list[str] = Field(default_factory=list)
    external_actions_performed: bool = False


class AgentEventResponse(BaseModel):
    sequence: int
    tool_name: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None


class AgentRunResponse(BaseModel):
    id: uuid.UUID
    task_type: str
    instruction: str
    scope: RunScope
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    tool_call_count: int
    max_tool_calls: int
    timeout_seconds: int
    model_turn_count: int
    is_profile_stale: bool
    cancel_requested: bool
    result: AgentResult | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    events: list[AgentEventResponse]


class AgentRunPage(BaseModel):
    items: list[AgentRunResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
