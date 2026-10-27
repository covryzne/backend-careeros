import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatchInput(BaseModel):
    @model_validator(mode="after")
    def not_null_required(self):
        required = {
            "full_name",
            "preferred_work_arrangement",
            "name",
            "role",
            "company",
            "category",
            "claim",
            "is_current",
            "is_verified",
        }
        if any(
            key in required and getattr(self, key, None) is None for key in self.model_fields_set
        ):
            raise ValueError("Required fields cannot be null")
        return self


class GroundingResponse(BaseModel):
    source_type: str | None = None
    confirmation_status: str = "unconfirmed"
    evidence_status: str = "unassessed"
    confirmed_at: datetime | None = None


class ProfileUpdate(PatchInput):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    place_of_birth: str | None = Field(default=None, max_length=120)
    date_of_birth: date | None = None
    headline: str | None = Field(default=None, max_length=240)
    location: str | None = Field(default=None, max_length=240)
    professional_summary: str | None = None
    preferred_work_arrangement: str | None = Field(default=None, max_length=30)
    preferred_salary_min: float | None = Field(default=None, ge=0, le=999999999999.99)
    preferred_salary_max: float | None = Field(default=None, ge=0, le=999999999999.99)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    preferred_titles: list[str] | None = None
    preferred_locations: list[str] | None = None


class CandidateSkillResponse(GroundingResponse):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    proficiency: str | None
    years_experience: float | None
    is_verified: bool


class CandidateExperienceResponse(GroundingResponse):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role: str
    company: str
    start_date: date | None
    end_date: date | None
    is_current: bool
    description: str | None
    is_verified: bool
    evidence_reference: str | None


class CandidateProjectResponse(GroundingResponse):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    role: str | None
    description: str | None
    url: str | None
    is_verified: bool


class CandidateFactResponse(GroundingResponse):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    category: str
    claim: str
    evidence_reference: str | None
    is_verified: bool
    verified_at: datetime | None


class CandidatePreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    preference_type: str
    value: str


class CandidateSkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    proficiency: str | None = Field(default=None, max_length=40)
    years_experience: float | None = Field(default=None, ge=0, le=99)
    is_verified: bool = False


class CandidateSkillUpdate(PatchInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    proficiency: str | None = None
    years_experience: float | None = Field(default=None, ge=0, le=99)
    is_verified: bool | None = None


class CandidateExperienceCreate(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None
    is_verified: bool = False
    evidence_reference: str | None = None


class CandidateExperienceUpdate(PatchInput):
    role: str | None = Field(default=None, min_length=1, max_length=200)
    company: str | None = Field(default=None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    description: str | None = None
    is_verified: bool | None = None
    evidence_reference: str | None = None


class CandidateProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=160)
    description: str | None = None
    url: str | None = None
    is_verified: bool = False


class CandidateProjectUpdate(PatchInput):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=160)
    description: str | None = None
    url: str | None = None
    is_verified: bool | None = None


class CandidateFactCreate(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    claim: str = Field(min_length=1)
    evidence_reference: str | None = None
    is_verified: bool = False
    verified_at: datetime | None = None


class CandidateFactUpdate(PatchInput):
    category: str | None = Field(default=None, min_length=1, max_length=60)
    claim: str | None = Field(default=None, min_length=1)
    evidence_reference: str | None = None
    is_verified: bool | None = None
    verified_at: datetime | None = None


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    mime_type: str
    file_size_bytes: int
    checksum_sha256: str
    target_role: str | None = None
    version_number: int = 1
    snapshot_revision: int = 0


class CandidateProfileResponse(BaseModel):
    documents: list[DocumentMetadata] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    owner_id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    place_of_birth: str | None
    date_of_birth: date | None
    headline: str | None
    location: str | None
    professional_summary: str | None
    preferred_work_arrangement: str
    preferred_salary_min: float | None
    preferred_salary_max: float | None
    salary_currency: str | None
    profile_version: int
    created_at: datetime
    updated_at: datetime
    skills: list[CandidateSkillResponse]
    experiences: list[CandidateExperienceResponse]
    projects: list[CandidateProjectResponse]
    facts: list[CandidateFactResponse]
    preferences: list[CandidatePreferenceResponse]


class FactConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_status: Literal["user_confirmed", "rejected", "unconfirmed"]
    expected_profile_version: int = Field(ge=1)
