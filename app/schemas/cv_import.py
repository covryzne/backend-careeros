import uuid
from datetime import date
from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.agent import StrictInput
from app.schemas.profile import ProfileUpdate


class CVExperience(StrictInput):
    role: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = Field(default=None, max_length=15000)


class CVProject(StrictInput):
    name: str = Field(default="", max_length=200)
    role: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=15000)
    url: str | None = Field(default=None, max_length=2000)


class CVFields(StrictInput):
    profile: ProfileUpdate = Field(default_factory=ProfileUpdate)
    skills: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=100
    )
    experiences: list[CVExperience] = Field(default_factory=list, max_length=50)
    projects: list[CVProject] = Field(default_factory=list, max_length=50)


class CVApply(CVFields):
    expected_profile_version: int | None = Field(default=None, ge=1)


class CVReview(CVFields):
    expected_revision: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def omit_empty_profile_values(cls, values):
        # Parse previews include nullable placeholders; absent professional values
        # have no meaning in a standalone recommendation snapshot.
        if isinstance(values, dict) and isinstance(values.get("profile"), dict):
            values = {
                **values,
                "profile": {
                    key: value for key, value in values["profile"].items() if value is not None
                },
            }
        return values


class CVImportResponse(StrictInput):
    id: uuid.UUID
    file_name: str
    status: str
    source_profile_version: int | None
    fields: CVFields
    warnings: list[str]
    parser_version: str
    document_id: uuid.UUID | None = None
    document_revision: int = 0
