import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.candidate import CandidateProfile
from app.schemas.profile import CandidateProfileResponse, ProfileUpdate

router = APIRouter()

# Temporary single-user fixture until authentication is finalized.
LOCAL_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@router.get("", response_model=CandidateProfileResponse)
def get_profile(db: Session = Depends(get_db)) -> CandidateProfile:  # noqa: B008
    profile = db.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.owner_id == LOCAL_OWNER_ID)
        .options(
            selectinload(CandidateProfile.skills),
            selectinload(CandidateProfile.experiences),
            selectinload(CandidateProfile.projects),
            selectinload(CandidateProfile.facts),
        )
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate profile not found")
    return profile


@router.patch("", response_model=CandidateProfileResponse)
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db)) -> CandidateProfile:  # noqa: B008
    profile = db.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.owner_id == LOCAL_OWNER_ID)
        .options(
            selectinload(CandidateProfile.skills),
            selectinload(CandidateProfile.experiences),
            selectinload(CandidateProfile.projects),
            selectinload(CandidateProfile.facts),
        )
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate profile not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    profile.profile_version += 1
    db.commit()
    db.refresh(profile)
    return profile
