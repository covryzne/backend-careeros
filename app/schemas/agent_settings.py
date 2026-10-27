from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.agent import StrictInput


class AgentSettingsPatch(StrictInput):
    expected_version: int = Field(ge=0)
    enabled: bool | None = None
    internal_writes_enabled: bool | None = None
    approvals_enabled: bool | None = None
    max_tool_calls: int | None = Field(default=None, ge=1, le=12)
    max_turns: int | None = Field(default=None, ge=1, le=8)
    timeout_seconds: int | None = Field(default=None, ge=1, le=90)

    @model_validator(mode="after")
    def reject_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Setting values cannot be null")
        return self


class ExecutionLimits(BaseModel):
    max_tool_calls: int
    max_turns: int
    timeout_seconds: int


class AgentSettingsResponse(ExecutionLimits):
    enabled: bool
    internal_writes_enabled: bool
    approvals_enabled: bool
    effective_enabled: bool
    version: int
    runtime_allowed: bool
    provider_configured: bool
    worker_mode: Literal["embedded", "external"]
    limits: ExecutionLimits
    external_actions_enabled: Literal[False]
    approval_required: Literal[True]
