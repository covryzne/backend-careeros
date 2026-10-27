"""Reviewed, private-data-minimized context for a specific immutable CV PDF."""

import uuid
from types import SimpleNamespace

from fastapi import HTTPException

from app.services.agent_privacy import private_values, sanitize_payload, sanitize_text
from app.services.profile import validate_values

PROFESSIONAL_FIELDS = (
    "headline",
    "professional_summary",
    "preferred_work_arrangement",
    "preferred_salary_min",
    "preferred_salary_max",
    "salary_currency",
    "preferred_titles",
    "preferred_locations",
)


def snapshot(fields, *, require_professional=True):
    values = fields.profile.model_dump()
    validate_values(values)
    private = private_values(SimpleNamespace(**values))
    for item in fields.experiences:
        if not item.role.strip() or not item.company.strip():
            raise HTTPException(422, "Lengkapi posisi dan perusahaan pada experience CV.")
        validate_values(item.model_dump())
    if any(not item.name.strip() for item in fields.projects):
        raise HTTPException(422, "Lengkapi nama project CV.")
    data = fields.model_dump(mode="json", include={"profile", "skills", "experiences", "projects"})
    data["profile"] = {
        key: value
        for key, value in data["profile"].items()
        if key in PROFESSIONAL_FIELDS and value is not None
    }
    data = sanitize_payload(data, private)
    if not any(data["profile"].values()) and not any(
        data[key] for key in ("skills", "experiences", "projects")
    ):
        if not require_professional:
            return None
        raise HTTPException(422, "Isi data profesional CV untuk rekomendasi.")
    return data


def ready_document(db, actor, document_id):
    from app.services.documents import owned_document

    item = owned_document(db, actor, document_id)
    if item.matching_snapshot is None:
        raise HTTPException(
            422, "Tinjau dan simpan data profesional CV terlebih dahulu untuk analisis khusus CV."
        )
    return item


def context(item, owner, source):
    data = item.matching_snapshot
    refs = []

    def add(kind, path, text):
        refs.append(source(kind, uuid.uuid5(item.id, path), text))

    for index, value in enumerate(data["experiences"][:8]):
        add(
            "experience",
            f"experience/{index}",
            f"{value['role']} at {value['company']}; Period: {value.get('start_date') or 'not recorded'} - {'Present' if value.get('is_current') else value.get('end_date') or 'not recorded'}; {value.get('description') or ''}",
        )
    for index, value in enumerate(data["projects"][:8]):
        add(
            "project",
            f"project/{index}",
            f"{value['name']}; Role: {value.get('role') or ''}; {value.get('description') or ''}",
        )
    for index, value in enumerate(data["skills"][:30]):
        add("skill", f"skill/{index}", value)
    professional = "; ".join(f"{key}: {value}" for key, value in data["profile"].items())
    if professional:
        add("profile", "profile", professional)
    if owner:
        for value in owner.preferences[:8]:
            add("preference", str(value.id), value.preference_type + ": " + value.value)
    private = private_values(owner)
    return {
        "available": True,
        "profile_version": owner.profile_version if owner else None,
        "sources": refs,
        "source_count": sum(len(data[key]) for key in ("skills", "experiences", "projects")),
        "selected_cv": {
            "id": str(item.id),
            "name": "CV v" + str(item.version_number),
            "target_role": sanitize_text(item.target_role or "", private),
            "version_number": item.version_number,
            "snapshot_revision": item.snapshot_revision,
        },
    }
