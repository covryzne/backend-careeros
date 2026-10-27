import hashlib
import os
import re
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import settings
from app.models.candidate import CandidateFact
from app.models.foundation import CandidateDocument, FactEvidence
from app.services.idempotency import lock_owner
from app.services.profile import check_version, load_profile, touch


def metadata(item):
    return {
        key: getattr(item, key)
        for key in (
            "id",
            "name",
            "original_file_name",
            "mime_type",
            "file_size_bytes",
            "checksum_sha256",
            "uploaded_at",
            "deleted_at",
            "target_role",
            "version_number",
            "snapshot_revision",
        )
    } | {"matching_ready": item.matching_snapshot is not None}


def owned_document(db, actor, document_id):
    actor.require("profile:read")
    item = db.scalar(
        select(CandidateDocument).where(
            CandidateDocument.id == document_id,
            CandidateDocument.owner_id == actor.owner_id,
            CandidateDocument.deleted_at.is_(None),
        )
    )
    if item is None:
        raise HTTPException(404, "Document not found")
    return item


def document_path(item):
    if not item.storage_key.endswith(".pdf") or len(item.storage_key) != 36:
        raise HTTPException(404, "Document unavailable")
    try:
        uuid.UUID(hex=item.storage_key[:-4])
    except ValueError:
        raise HTTPException(404, "Document unavailable") from None
    root = settings.document_storage_dir.resolve()
    raw_path = root / item.storage_key
    if raw_path.is_symlink():
        raise HTTPException(404, "Document unavailable")
    path = raw_path.resolve()
    if path.parent != root or path.is_symlink():
        raise HTTPException(404, "Document unavailable")
    return path


def next_version(db, actor, target_role):
    # One owner lock serializes numbering with uploads and legacy import/apply.
    lock_owner(db, actor)
    role = " ".join((target_role or "").split())
    key = role.casefold()
    if len(key) > 160:
        raise HTTPException(422, "Target role terlalu panjang setelah normalisasi.")
    number = (
        db.scalar(
            select(func.max(CandidateDocument.version_number)).where(
                CandidateDocument.owner_id == actor.owner_id, CandidateDocument.role_key == key
            )
        )
        or 0
    )
    return {
        "owner_id": actor.owner_id,
        "target_role": role or None,
        "role_key": key,
        "version_number": number + 1,
    }


def upload_document(
    db, actor, name, content_type, content, expected=None, *, target_role=None, label=None
):
    actor.require("profile:write", approval=True)
    version_fields = next_version(db, actor, target_role)
    profile = load_profile(db, actor, lock=True)
    if profile:
        check_version(profile, expected, actor)
    elif expected is not None:
        raise HTTPException(409, "Profile does not exist")
    if (
        content_type != "application/pdf"
        or not name.lower().endswith(".pdf")
        or not content.startswith(b"%PDF-")
    ):
        raise HTTPException(422, "A valid PDF file is required")
    if len(content) > settings.document_max_bytes:
        raise HTTPException(413, "Document is too large")
    root = settings.document_storage_dir.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    item = CandidateDocument(
        **version_fields,
        profile_id=profile.id if profile else None,
        name=(label.strip() if label and label.strip() else re.split(r"[/\\]", name)[-1])[:200],
        original_file_name=re.split(r"[/\\]", name)[-1][:255],
        storage_key=uuid.uuid4().hex + ".pdf",
        file_size_bytes=len(content),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
    )
    path = document_path(item)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
    try:
        db.add(item)
        db.flush()
        if profile:
            touch(db, actor, profile, "document_uploaded", item.id)
        else:
            from app.services.audit import audit

            audit(db, actor, "document_uploaded", "document", item.id)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return item, profile.profile_version if profile else None


def delete_document(db, actor, document_id, expected=None):
    actor.require("profile:write", approval=True)
    lock_owner(db, actor)
    profile = load_profile(db, actor, lock=True)
    if profile:
        check_version(profile, expected, actor)
    elif expected is not None:
        raise HTTPException(409, "Profile does not exist")
    item = owned_document(db, actor, document_id)
    item.deleted_at = datetime.now(UTC)
    links = db.scalars(select(FactEvidence).where(FactEvidence.document_id == item.id)).all()
    for link in links:
        fact = db.get(CandidateFact, link.fact_id)
        if fact and profile and fact.profile_id == profile.id:
            fact.evidence_status = "unassessed"
    if profile:
        touch(db, actor, profile, "document_deleted", item.id)
    else:
        from app.services.audit import audit

        audit(db, actor, "document_deleted", "document", item.id)
    db.flush()
    return profile.profile_version if profile else None


def attach_evidence(db, actor, fact_id, document_id, excerpt, assessment, expected):
    actor.require("profile:confirm", approval=True)
    profile = load_profile(db, actor, lock=True)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    check_version(profile, expected, actor)
    fact = db.scalar(
        select(CandidateFact).where(
            CandidateFact.id == fact_id, CandidateFact.profile_id == profile.id
        )
    )
    if fact is None:
        raise HTTPException(404, "Fact not found")
    document = owned_document(db, actor, document_id)
    link = db.scalar(
        select(FactEvidence).where(
            FactEvidence.fact_id == fact.id, FactEvidence.document_id == document.id
        )
    )
    if not link:
        link = FactEvidence(fact_id=fact.id, document_id=document.id, excerpt=excerpt)
        db.add(link)
    changed = link.excerpt != excerpt or fact.evidence_status != assessment or link.id is None
    link.excerpt = excerpt
    fact.evidence_status = assessment
    if changed:
        touch(db, actor, profile, "fact_evidence_reviewed", fact.id)
    db.flush()
    return fact, profile.profile_version
