import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.candidate import (
    CandidateExperience,
    CandidateFact,
    CandidatePreference,
    CandidateProfile,
    CandidateProject,
    CandidateSkill,
    User,
)
from app.schemas.profile import (
    CandidateExperienceCreate,
    CandidateExperienceResponse,
    CandidateExperienceUpdate,
    CandidateFactCreate,
    CandidateFactResponse,
    CandidateFactUpdate,
    CandidateProfileResponse,
    CandidateProjectCreate,
    CandidateProjectResponse,
    CandidateProjectUpdate,
    CandidateSkillCreate,
    CandidateSkillResponse,
    CandidateSkillUpdate,
    ProfileUpdate,
)

router = APIRouter()

# Temporary single-user fixture until authentication is finalized.
LOCAL_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@router.get("", response_model=CandidateProfileResponse)
def get_profile(db: Session = Depends(get_db)) -> CandidateProfile:  # noqa: B008
    profile = _load_profile(db)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate profile not found")
    return profile


@router.patch("", response_model=CandidateProfileResponse)
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db)) -> CandidateProfile:  # noqa: B008
    profile = _load_profile(db)
    if profile is None:
        if not payload.full_name:
            raise HTTPException(status_code=422, detail="full_name is required to create a profile")
        if db.get(User, LOCAL_OWNER_ID) is None:
            db.add(User(id=LOCAL_OWNER_ID))
            db.flush()
        profile = CandidateProfile(owner_id=LOCAL_OWNER_ID, full_name=payload.full_name)
        db.add(profile)
        db.flush()
    values = payload.model_dump(exclude_unset=True)
    preferred_titles = values.pop("preferred_titles", None)
    preferred_locations = values.pop("preferred_locations", None)
    for field, value in values.items():
        setattr(profile, field, value)
    if preferred_titles is not None or preferred_locations is not None:
        db.query(CandidatePreference).filter(
            CandidatePreference.profile_id == profile.id
        ).delete(synchronize_session=False)
        for preference_type, values in (
            ("title", preferred_titles or []),
            ("location", preferred_locations or []),
        ):
            db.add_all(
                CandidatePreference(
                    profile_id=profile.id, preference_type=preference_type, value=value
                )
                for value in dict.fromkeys(values)
            )
    profile.profile_version += 1
    db.commit()
    return _load_profile(db)


def _load_profile(db: Session) -> CandidateProfile | None:
    return db.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.owner_id == LOCAL_OWNER_ID)
        .options(
            selectinload(CandidateProfile.skills),
            selectinload(CandidateProfile.experiences),
            selectinload(CandidateProfile.projects),
            selectinload(CandidateProfile.facts),
            selectinload(CandidateProfile.preferences),
        )
    )


def _get_profile_or_404(db: Session) -> CandidateProfile:
    profile = _load_profile(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="Candidate profile not found")
    return profile


def _crud_routes(model, response_model, create_model, update_model, collection_name: str):
    def create(payload, db):
        profile = _get_profile_or_404(db)
        item = model(profile_id=profile.id, **payload.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def update(item_id, payload, db):
        profile = _get_profile_or_404(db)
        item = db.scalar(select(model).where(model.id == item_id, model.profile_id == profile.id))
        if item is None:
            raise HTTPException(status_code=404, detail=f"{collection_name} item not found")
        for field, value in payload.model_dump().items():
            setattr(item, field, value)
        db.commit()
        db.refresh(item)
        return item

    def delete(item_id, db):
        profile = _get_profile_or_404(db)
        item = db.scalar(select(model).where(model.id == item_id, model.profile_id == profile.id))
        if item is None:
            raise HTTPException(status_code=404, detail=f"{collection_name} item not found")
        db.delete(item)
        db.commit()

    return create, update, delete


def _register_crud(model, response_model, create_model, update_model, path: str, label: str):
    create, update, delete = _crud_routes(model, response_model, create_model, update_model, label)

    @router.post(path, response_model=response_model, status_code=201)
    def create_item(payload: create_model, db: Session = Depends(get_db)):  # noqa: B008
        return create(payload, db)

    @router.patch(f"{path}/{{item_id}}", response_model=response_model)
    def update_item(item_id: uuid.UUID, payload: update_model, db: Session = Depends(get_db)):  # noqa: B008
        return update(item_id, payload, db)

    @router.delete(f"{path}/{{item_id}}", status_code=204)
    def delete_item(item_id: uuid.UUID, db: Session = Depends(get_db)):  # noqa: B008
        delete(item_id, db)


_register_crud(CandidateSkill, CandidateSkillResponse, CandidateSkillCreate, CandidateSkillUpdate, "/skills", "skill")
_register_crud(CandidateExperience, CandidateExperienceResponse, CandidateExperienceCreate, CandidateExperienceUpdate, "/experiences", "experience")
_register_crud(CandidateProject, CandidateProjectResponse, CandidateProjectCreate, CandidateProjectUpdate, "/projects", "project")
_register_crud(CandidateFact, CandidateFactResponse, CandidateFactCreate, CandidateFactUpdate, "/facts", "fact")
