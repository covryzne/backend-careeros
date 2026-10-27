# ruff: noqa: B008
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core import security
from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.models.candidate import (
    CandidateExperience,
    CandidateFact,
    CandidateProject,
    CandidateSkill,
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
    FactConfirmation,
    ProfileUpdate,
)
from app.services import profile as service
from app.services.idempotency import command

LOCAL_OWNER_ID = security.LOCAL_OWNER_ID

router = APIRouter()


@router.get("", response_model=CandidateProfileResponse)
def get_profile(
    response: Response, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    profile = service.load_profile(db, actor)
    if profile is None:
        raise HTTPException(404, "Candidate profile not found")
    response.headers["X-Profile-Version"] = str(profile.profile_version)
    return profile


@router.patch("", response_model=CandidateProfileResponse)
def update_profile(
    payload: ProfileUpdate,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    expected: int | None = Header(None, alias="X-Expected-Profile-Version", ge=1),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    def execute():
        profile = service.update_profile(db, actor, payload, expected)
        return {
            "data": jsonable_encoder(CandidateProfileResponse.model_validate(profile)),
            "version": profile.profile_version,
        }

    result = command(
        db,
        actor,
        "profile:update",
        key,
        {"payload": payload.model_dump(exclude_unset=True), "expected": expected},
        execute,
    )
    db.commit()
    response.headers["X-Profile-Version"] = str(result["version"])
    return result["data"]


def _register_crud(model, response_model, create_model, update_model, path: str, label: str):
    def mutate(action, payload, item_id, response, db, actor, expected, key):
        def execute():
            item, version = service.mutate_child(
                db, actor, model, action, item_id=item_id, payload=payload, expected=expected
            )
            return {
                "data": jsonable_encoder(response_model.model_validate(item)) if item else None,
                "version": version,
            }

        result = command(
            db,
            actor,
            f"profile:{label}:{action}:{item_id or ''}",
            key,
            {
                "payload": payload.model_dump(exclude_unset=True) if payload else None,
                "expected": expected,
            },
            execute,
        )
        db.commit()
        response.headers["X-Profile-Version"] = str(result["version"])
        return result["data"]

    @router.post(path, response_model=response_model, status_code=201)
    def create_item(
        payload: create_model,
        response: Response,
        db: Session = Depends(get_db),
        actor: ActorContext = Depends(get_actor),
        expected: int | None = Header(None, alias="X-Expected-Profile-Version", ge=1),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return mutate("create", payload, None, response, db, actor, expected, key)

    @router.patch(f"{path}/{{item_id}}", response_model=response_model)
    def update_item(
        item_id: uuid.UUID,
        payload: update_model,
        response: Response,
        db: Session = Depends(get_db),
        actor: ActorContext = Depends(get_actor),
        expected: int | None = Header(None, alias="X-Expected-Profile-Version", ge=1),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return mutate("update", payload, item_id, response, db, actor, expected, key)

    @router.delete(f"{path}/{{item_id}}", status_code=204)
    def delete_item(
        item_id: uuid.UUID,
        response: Response,
        db: Session = Depends(get_db),
        actor: ActorContext = Depends(get_actor),
        expected: int | None = Header(None, alias="X-Expected-Profile-Version", ge=1),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        mutate("delete", None, item_id, response, db, actor, expected, key)


@router.post("/facts/{fact_id}/confirm", response_model=CandidateFactResponse)
def confirm_fact(
    fact_id: uuid.UUID,
    payload: FactConfirmation,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    fact, version = service.confirm_fact(
        db, actor, fact_id, payload.confirmation_status, payload.expected_profile_version
    )
    db.commit()
    response.headers["X-Profile-Version"] = str(version)
    return fact


_register_crud(
    CandidateSkill,
    CandidateSkillResponse,
    CandidateSkillCreate,
    CandidateSkillUpdate,
    "/skills",
    "skill",
)
_register_crud(
    CandidateExperience,
    CandidateExperienceResponse,
    CandidateExperienceCreate,
    CandidateExperienceUpdate,
    "/experiences",
    "experience",
)
_register_crud(
    CandidateProject,
    CandidateProjectResponse,
    CandidateProjectCreate,
    CandidateProjectUpdate,
    "/projects",
    "project",
)
_register_crud(
    CandidateFact, CandidateFactResponse, CandidateFactCreate, CandidateFactUpdate, "/facts", "fact"
)
