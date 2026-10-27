import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.security import ActorContext
from app.models.candidate import CandidatePreference, CandidateProfile, User
from app.services.audit import audit


def load_profile(db: Session, actor: ActorContext, *, lock=False):
    actor.require("profile:read")
    query = select(CandidateProfile).where(CandidateProfile.owner_id == actor.owner_id)
    if lock:
        query = query.with_for_update()
    return db.scalar(
        query.options(
            *[
                selectinload(getattr(CandidateProfile, name))
                for name in (
                    "skills",
                    "experiences",
                    "projects",
                    "facts",
                    "preferences",
                    "documents",
                )
            ]
        ).execution_options(populate_existing=True)
    )


def check_version(profile, expected, actor):
    if expected is None and (settings.require_profile_version or actor.actor_type == "agent"):
        raise HTTPException(428, "Expected profile version required")
    if expected is not None and expected != profile.profile_version:
        raise HTTPException(
            409,
            {
                "code": "STALE_VERSION",
                "message": "Profile changed; reload before saving",
                "current_version": profile.profile_version,
            },
        )


def touch(db, actor, profile, action, entity_id=None):
    profile.profile_version += 1
    profile.updated_at = datetime.now(UTC)
    audit(db, actor, action, "profile", entity_id or profile.id, version=profile.profile_version)


def normalize_numbers(values):
    for key in ("preferred_salary_min", "preferred_salary_max", "years_experience"):
        if key in values and values[key] is not None:
            values[key] = Decimal(str(values[key])).quantize(
                Decimal("0.1") if key == "years_experience" else Decimal("0.01")
            )
    return values


def validate_values(values):
    if values.get("preferred_work_arrangement") not in {
        None,
        "remote",
        "hybrid",
        "on_site",
        "flexible",
        "unknown",
    }:
        raise HTTPException(422, "Invalid work arrangement")
    minimum, maximum = values.get("preferred_salary_min"), values.get("preferred_salary_max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HTTPException(422, "Minimum salary cannot exceed maximum")
    start, end = values.get("start_date"), values.get("end_date")
    if start and end and end < start:
        raise HTTPException(422, "Experience end date precedes start date")
    if values.get("is_current") and end:
        raise HTTPException(422, "Current experience cannot have an end date")


def update_profile(db: Session, actor: ActorContext, payload, expected=None):
    actor.require("profile:write", approval=True)
    profile = load_profile(db, actor, lock=True)
    created = profile is None
    if created:
        if not payload.full_name:
            raise HTTPException(422, "full_name is required to create a profile")
        if expected is not None:
            raise HTTPException(409, "Profile does not exist")
        if db.get(User, actor.owner_id) is None:
            db.add(User(id=actor.owner_id))
            db.flush()
        profile = CandidateProfile(owner_id=actor.owner_id, full_name=payload.full_name)
        db.add(profile)
        db.flush()
        from sqlalchemy import update

        from app.models.foundation import CandidateDocument

        db.execute(
            update(CandidateDocument)
            .where(
                CandidateDocument.owner_id == actor.owner_id,
                CandidateDocument.profile_id.is_(None),
            )
            .values(profile_id=profile.id)
        )
    else:
        check_version(profile, expected, actor)
    values = normalize_numbers(payload.model_dump(exclude_unset=True))
    validate_values(
        {
            **{
                key: getattr(profile, key)
                for key in ("preferred_salary_min", "preferred_salary_max")
            },
            **values,
        }
    )
    if "full_name" in values:
        values["full_name"] = values["full_name"].strip()
        if not values["full_name"]:
            raise HTTPException(422, "Full name cannot be blank")
    changed = created
    for name, kind in (("preferred_titles", "title"), ("preferred_locations", "location")):
        if name not in values:
            continue
        incoming = values.pop(name)
        if incoming is None:
            raise HTTPException(422, "Use [] to clear preferences; null is not supported")
        incoming = list(dict.fromkeys(value.strip() for value in incoming if value.strip()))
        if any(len(value) > 200 for value in incoming):
            raise HTTPException(422, "Preference exceeds maximum length")
        previous = [item for item in profile.preferences if item.preference_type == kind]
        if set(incoming) != {item.value for item in previous}:
            for item in previous:
                db.delete(item)
            db.flush()
            db.add_all(
                CandidatePreference(profile_id=profile.id, preference_type=kind, value=value)
                for value in incoming
            )
            changed = True
    for key, value in values.items():
        if getattr(profile, key) != value:
            setattr(profile, key, value)
            changed = True
    if changed:
        if created:
            audit(db, actor, "profile_created", "profile", profile.id, version=1)
        else:
            touch(db, actor, profile, "profile_updated")
    db.flush()
    return load_profile(db, actor)


def mutate_child(db, actor, model, action, *, item_id=None, payload=None, expected=None):
    actor.require("profile:write", approval=True)
    profile = load_profile(db, actor, lock=True)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    item = None
    if action != "create":
        item = db.scalar(select(model).where(model.id == item_id, model.profile_id == profile.id))
        if item is None:
            raise HTTPException(404, "Profile item not found")
    check_version(profile, expected, actor)
    if action == "delete":
        db.delete(item)
        touch(db, actor, profile, "profile_child_deleted", item.id)
        db.flush()
        return None, profile.profile_version
    values = normalize_numbers(payload.model_dump(exclude_unset=action == "update"))
    # Legacy booleans remain compatibility data, never confirmation/evidence grants.
    values.pop("verified_at", None)
    if "name" in values:
        values["name"] = values["name"].strip()
        if not values["name"]:
            raise HTTPException(422, "Name cannot be blank")
    for key, value in values.items():
        column = model.__table__.columns.get(key)
        limit = getattr(column.type, "length", None) if column is not None else None
        if limit and isinstance(value, str) and len(value) > limit:
            raise HTTPException(422, "Field exceeds maximum length")
    current = {col.name: getattr(item, col.name) for col in model.__table__.columns} if item else {}
    validate_values({**current, **values})
    changed = action == "create" or any(
        getattr(item, key) != value for key, value in values.items()
    )
    if action == "create":
        item = model(profile_id=profile.id, source_type="user_input", **values)
        db.add(item)
        db.flush()
    else:
        for key, value in values.items():
            setattr(item, key, value)
        if changed:
            item.confirmation_status = "unconfirmed"
            item.evidence_status = "unassessed"
            item.confirmed_at = None
    if changed:
        touch(db, actor, profile, "profile_child_" + action, item.id)
    db.flush()
    return item, profile.profile_version


def confirm_fact(db, actor, fact_id: uuid.UUID, status: str, expected: int):
    from app.models.candidate import CandidateFact

    actor.require("profile:confirm", approval=True)
    profile = load_profile(db, actor, lock=True)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    fact = db.scalar(
        select(CandidateFact).where(
            CandidateFact.id == fact_id, CandidateFact.profile_id == profile.id
        )
    )
    if fact is None:
        raise HTTPException(404, "Fact not found")
    check_version(profile, expected, actor)
    if fact.confirmation_status != status:
        fact.confirmation_status = status
        fact.confirmed_at = datetime.now(UTC) if status == "user_confirmed" else None
        touch(db, actor, profile, "fact_confirmation_changed", fact.id)
    db.flush()
    return fact, profile.profile_version


def merge_cv_fields(db, actor, payload, *, document_added=False):
    """Atomic non-destructive CV merge; one profile version per import."""
    from pydantic import ValidationError

    from app.models.candidate import CandidateExperience, CandidateProject, CandidateSkill
    from app.schemas.profile import CandidateExperienceCreate, CandidateProjectCreate

    try:
        for value in payload.experiences:
            data = CandidateExperienceCreate.model_validate(value.model_dump())
            if not data.role.strip() or not data.company.strip():
                raise ValueError()
            validate_values(data.model_dump())
        for value in payload.projects:
            data = CandidateProjectCreate.model_validate(value.model_dump())
            if not data.name.strip():
                raise ValueError()
    except (ValidationError, ValueError):
        raise HTTPException(
            422,
            "Lengkapi role, perusahaan, nama project, dan tanggal yang valid sebelum menyimpan.",
        ) from None
    current = load_profile(db, actor, lock=True)
    before = current.profile_version if current else None
    if current:
        if payload.expected_profile_version is None:
            raise HTTPException(428, "Expected profile version required")
        check_version(current, payload.expected_profile_version, actor)
    elif payload.expected_profile_version is not None:
        raise HTTPException(409, "Profile does not exist")
    owner = update_profile(db, actor, payload.profile, expected=payload.expected_profile_version)
    changed = False
    existing = {item.name.strip().casefold() for item in owner.skills}
    for name in payload.skills:
        name = name.strip()
        if not name:
            raise HTTPException(422, "Skill cannot be blank")
        if name.casefold() not in existing:
            db.add(CandidateSkill(profile_id=owner.id, name=name, source_type="resume"))
            existing.add(name.casefold())
            changed = True
    seen_experiences = list(owner.experiences)
    for value in payload.experiences:
        item = CandidateExperienceCreate.model_validate(value.model_dump())
        if not item.role.strip() or not item.company.strip():
            raise HTTPException(422, "Lengkapi role dan perusahaan pada pengalaman.")
        values = item.model_dump()
        values["role"] = values["role"].strip()
        values["company"] = values["company"].strip()
        validate_values(values)
        duplicate = any(
            old.role.strip().casefold() == values["role"].casefold()
            and old.company.strip().casefold() == values["company"].casefold()
            and (not values["start_date"] or old.start_date == values["start_date"])
            for old in seen_experiences
        )
        if not duplicate:
            row = CandidateExperience(profile_id=owner.id, source_type="resume", **values)
            db.add(row)
            seen_experiences.append(row)
            changed = True
    existing_projects = {item.name.strip().casefold() for item in owner.projects}
    for value in payload.projects:
        item = CandidateProjectCreate.model_validate(value.model_dump())
        if not item.name.strip():
            raise HTTPException(422, "Project name cannot be blank")
        if item.name.strip().casefold() not in existing_projects:
            db.add(
                CandidateProject(
                    profile_id=owner.id,
                    source_type="resume",
                    **{**item.model_dump(), "name": item.name.strip()},
                )
            )
            existing_projects.add(item.name.strip().casefold())
            changed = True
    if before is not None and (changed or document_added) and owner.profile_version == before:
        touch(db, actor, owner, "profile_cv_imported")
    db.flush()
    return load_profile(db, actor)
