# ruff: noqa: B008
import uuid
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.models.foundation import CandidateDocument, Draft, ProfileProposal
from app.schemas.agent import DraftArguments, StrictInput
from app.services import documents, drafts, profile
from app.services.idempotency import command

router = APIRouter()


class DraftCreate(DraftArguments):
    draft_type: Literal["email", "cover_letter"] = "cover_letter"


class DraftEdit(StrictInput):
    content: str = Field(min_length=1, max_length=30000)
    expected_version: int = Field(ge=1)


class EvidenceInput(StrictInput):
    document_id: uuid.UUID
    excerpt: str = Field(min_length=1, max_length=2000)
    assessment: Literal["supported", "unsupported"]
    expected_profile_version: int = Field(ge=1)


@router.get("/profile/documents")
def list_documents(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    actor.require("profile:read")
    items = db.scalars(
        select(CandidateDocument)
        .where(CandidateDocument.owner_id == actor.owner_id, CandidateDocument.deleted_at.is_(None))
        .order_by(CandidateDocument.uploaded_at.desc())
        .limit(100)
    ).all()
    return {"items": [documents.metadata(item) for item in items]}


@router.post("/profile/documents", status_code=201)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    target_role: str | None = Form(None, max_length=160),
    label: str | None = Form(None, max_length=200),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    expected: int | None = Header(None, alias="X-Expected-Profile-Version", ge=1),
):
    data = await file.read(settings.document_max_bytes + 1)
    from hashlib import sha256

    stored = None

    def execute():
        nonlocal stored
        stored, version = documents.upload_document(
            db,
            actor,
            file.filename or "document.pdf",
            file.content_type,
            data,
            expected,
            target_role=target_role,
            label=label,
        )
        return {
            "document": jsonable_encoder(documents.metadata(stored)),
            "profile_version": version,
        }

    try:
        result = command(
            db,
            actor,
            "document:upload",
            request.headers.get("Idempotency-Key"),
            {
                "checksum": sha256(data).hexdigest(),
                "file_name": file.filename,
                "mime": file.content_type,
                "target_role": target_role,
                "label": label,
                "expected": expected,
            },
            execute,
        )
        db.commit()
    except Exception:
        db.rollback()
        if stored:
            documents.document_path(stored).unlink(missing_ok=True)
        raise
    if result["profile_version"] is not None:
        response.headers["X-Profile-Version"] = str(result["profile_version"])
    response.headers["Cache-Control"] = "no-store"
    return result["document"]


@router.get("/profile/documents/{document_id}/preview")
def preview(
    document_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    item = documents.owned_document(db, actor, document_id)
    path = documents.document_path(item)
    if not path.is_file():
        raise HTTPException(404, "Document unavailable")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/profile/documents/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    expected: int | None = Header(None, alias="X-Expected-Profile-Version", ge=1),
):
    version = documents.delete_document(db, actor, document_id, expected)
    db.commit()
    if version is not None:
        response.headers["X-Profile-Version"] = str(version)


@router.post("/profile/facts/{fact_id}/evidence")
def attach_evidence(
    fact_id: uuid.UUID,
    payload: EvidenceInput,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    from app.schemas.profile import CandidateFactResponse

    fact, version = documents.attach_evidence(
        db,
        actor,
        fact_id,
        payload.document_id,
        payload.excerpt,
        payload.assessment,
        payload.expected_profile_version,
    )
    db.commit()
    response.headers["X-Profile-Version"] = str(version)
    return CandidateFactResponse.model_validate(fact)


@router.post("/drafts", status_code=201)
def create_draft(
    payload: DraftCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    result = command(
        db,
        actor,
        "draft:create",
        request.headers.get("Idempotency-Key"),
        payload.model_dump(mode="json"),
        lambda: drafts.draft_detail(
            db, actor, drafts.create_draft(db, actor, payload, payload.draft_type)
        ),
    )
    db.commit()
    return result


@router.get("/drafts")
def list_drafts(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    actor.require("drafts:read")
    items = db.scalars(
        select(Draft)
        .where(Draft.owner_id == actor.owner_id, Draft.deleted_at.is_(None))
        .order_by(Draft.created_at.desc())
        .limit(20)
    ).all()
    result = {"items": [drafts.draft_detail(db, actor, item) for item in items]}
    db.commit()
    return result


@router.get("/drafts/{draft_id}")
def get_draft(
    draft_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    result = drafts.draft_detail(db, actor, drafts.owned_draft(db, actor, draft_id))
    db.commit()
    return result


@router.patch("/drafts/{draft_id}")
def edit_draft(
    draft_id: uuid.UUID,
    payload: DraftEdit,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    item = drafts.edit_draft(db, actor, draft_id, payload.content, payload.expected_version)
    db.commit()
    return drafts.draft_detail(db, actor, item)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(
    draft_id: uuid.UUID,
    expected_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    drafts.delete_draft(db, actor, draft_id, expected_version)
    db.commit()


@router.get("/profile/proposals")
def list_proposals(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    actor.require("profile:read")
    return {
        "items": jsonable_encoder(
            db.scalars(
                select(ProfileProposal).where(ProfileProposal.owner_id == actor.owner_id).limit(20)
            ).all()
        )
    }


@router.get("/draft-sources")
def get_draft_sources(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    from app.services.professional_sources import draft_sources

    actor.require("drafts:read")
    owner = profile.load_profile(db, actor)
    return {
        "items": draft_sources(owner),
        "profile_version": owner.profile_version if owner else None,
    }
