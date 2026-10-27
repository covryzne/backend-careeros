# ruff: noqa: B008
import uuid

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.cv_import import CVApply, CVImportResponse, CVReview
from app.schemas.profile import CandidateProfileResponse
from app.services import cv_imports, documents
from app.services.idempotency import command

router = APIRouter()


@router.post(
    "/profile/documents/{document_id}/parse", status_code=201, response_model=CVImportResponse
)
async def parse_saved(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    staged_path = None

    def execute():
        nonlocal staged_path
        item = cv_imports.parse_document(db, actor, document_id)
        staged_path = documents.document_path(item)
        return cv_imports.detail(item)

    def perform():
        try:
            result = command(
                db,
                actor,
                "document:parse:" + str(document_id),
                request.headers.get("Idempotency-Key"),
                {},
                execute,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            if staged_path:
                staged_path.unlink(missing_ok=True)
            raise

    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(perform)


@router.post("/profile/cv-imports/{import_id}/review")
def review(
    import_id: uuid.UUID,
    payload: CVReview,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    from fastapi.encoders import jsonable_encoder

    try:
        result = command(
            db,
            actor,
            "cv-import:review:" + str(import_id),
            request.headers.get("Idempotency-Key"),
            payload.model_dump(mode="json"),
            lambda: jsonable_encoder(
                documents.metadata(cv_imports.review(db, actor, import_id, payload))
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/profile/cv-imports", status_code=201, response_model=CVImportResponse)
async def upload(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    data = await file.read(min(settings.document_max_bytes, 10 * 1024 * 1024) + 1)
    staged_path = None

    def execute():
        nonlocal staged_path
        staged = cv_imports.stage(db, actor, file.filename or "cv.pdf", file.content_type, data)
        staged_path = documents.document_path(staged)
        return cv_imports.detail(staged)

    from hashlib import sha256

    def perform():
        try:
            result = command(
                db,
                actor,
                "cv-import:upload",
                request.headers.get("Idempotency-Key"),
                {
                    "checksum": sha256(data).hexdigest(),
                    "file_name": file.filename,
                    "mime": file.content_type,
                },
                execute,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            if staged_path:
                staged_path.unlink(missing_ok=True)
            raise

    result = await run_in_threadpool(perform)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/profile/cv-imports/{import_id}", response_model=CVImportResponse)
def read(
    import_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    response.headers["Cache-Control"] = "no-store"
    return cv_imports.detail(cv_imports.owned_import(db, actor, import_id))


@router.post("/profile/cv-imports/{import_id}/apply", response_model=CandidateProfileResponse)
def apply(
    import_id: uuid.UUID,
    payload: CVApply,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    try:
        result = command(
            db,
            actor,
            "cv-import:apply:" + str(import_id),
            request.headers.get("Idempotency-Key"),
            payload.model_dump(mode="json"),
            lambda: CandidateProfileResponse.model_validate(
                cv_imports.apply(db, actor, import_id, payload)
            ).model_dump(mode="json"),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    cv_imports.cleanup_redundant_file(db, actor, import_id)
    response.headers["X-Profile-Version"] = str(result["profile_version"])
    response.headers["Cache-Control"] = "no-store"
    return result


@router.delete("/profile/cv-imports/{import_id}", status_code=204)
def cancel(
    import_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    item = cv_imports.cancel(db, actor, import_id)
    path = documents.document_path(item)
    db.commit()
    path.unlink(missing_ok=True)


@router.get("/profile/cv-imports")
def pending(
    response: Response, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    response.headers["Cache-Control"] = "no-store"
    from sqlalchemy import select

    from app.models.foundation import CVImport

    actor.require("profile:read")
    items = db.scalars(
        select(CVImport)
        .where(CVImport.owner_id == actor.owner_id, CVImport.status == "pending")
        .order_by(CVImport.created_at.desc(), CVImport.id)
        .limit(20)
    ).all()
    return {"items": [{"id": str(item.id), "file_name": item.file_name} for item in items]}
