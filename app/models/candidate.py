import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CandidateProfile(TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    place_of_birth: Mapped[str | None] = mapped_column(String(120))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    headline: Mapped[str | None] = mapped_column(String(240))
    professional_summary: Mapped[str | None] = mapped_column(Text)
    preferred_work_arrangement: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    preferred_salary_min: Mapped[float | None] = mapped_column(Numeric(14, 2))
    preferred_salary_max: Mapped[float | None] = mapped_column(Numeric(14, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    skills: Mapped[list["CandidateSkill"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    experiences: Mapped[list["CandidateExperience"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    projects: Mapped[list["CandidateProject"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    facts: Mapped[list["CandidateFact"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class CandidateSkill(Base):
    __tablename__ = "candidate_skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    proficiency: Mapped[str | None] = mapped_column(String(40))
    years_experience: Mapped[float | None] = mapped_column(Numeric(4, 1))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile: Mapped[CandidateProfile] = relationship(back_populates="skills")


class CandidateExperience(Base):
    __tablename__ = "candidate_experiences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[CandidateProfile] = relationship(back_populates="experiences")


class CandidateProject(Base):
    __tablename__ = "candidate_projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile: Mapped[CandidateProfile] = relationship(back_populates="projects")


class CandidateFact(Base):
    __tablename__ = "candidate_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[CandidateProfile] = relationship(back_populates="facts")

