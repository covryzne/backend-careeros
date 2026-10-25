import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    place_of_birth: str | None = Field(default=None, max_length=120)
    date_of_birth: date | None = None
    headline: str | None = Field(default=None, max_length=240)
    location: str | None = Field(default=None, max_length=240)
    professional_summary: str | None = None
    preferred_work_arrangement: str | None = Field(default=None, max_length=30)
    preferred_salary_min: float | None = Field(default=None, ge=0)
    preferred_salary_max: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    preferred_titles: list[str] | None = None
    preferred_locations: list[str] | None = None


class CandidateSkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    proficiency: str | None
    years_experience: float | None
    is_verified: bool


class CandidateExperienceResponse(BaseModel):
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


class CandidateProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    role: str | None
    description: str | None
    url: str | None
    is_verified: bool


class CandidateFactResponse(BaseModel):
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


class CandidateSkillUpdate(CandidateSkillCreate):
    pass


class CandidateExperienceCreate(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None
    is_verified: bool = False
    evidence_reference: str | None = None


class CandidateExperienceUpdate(CandidateExperienceCreate):
    pass


class CandidateProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=160)
    description: str | None = None
    url: str | None = None
    is_verified: bool = False


class CandidateProjectUpdate(CandidateProjectCreate):
    pass


class CandidateFactCreate(BaseModel):
    category: str = Field(min_length=1, max_length=60)
    claim: str = Field(min_length=1)
    evidence_reference: str | None = None
    is_verified: bool = False
    verified_at: datetime | None = None


class CandidateFactUpdate(CandidateFactCreate):
    pass


class CandidateProfileResponse(BaseModel):
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
