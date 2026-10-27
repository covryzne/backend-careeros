# ruff: noqa: B008
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.workflow import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationListResponse,
    ApplicationStatus,
    ApplicationUpdate,
)
from app.services import application_workflows as service
from app.services.idempotency import command

router = APIRouter()


@router.post("", response_model=ApplicationDetail, status_code=201)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    *,
    request: Request,
):
    result = command(
        db,
        actor,
        request.method + ":" + request.url.path,
        request.headers.get("Idempotency-Key"),
        {"payload": payload.model_dump(mode="json", exclude_unset=True)},
        lambda: jsonable_encoder(service.create_application(payload=payload, db=db, actor=actor)),
    )
    db.commit()
    return result


@router.get("", response_model=ApplicationListResponse)
def list_applications(
    status: ApplicationStatus | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    return service.list_applications(
        status=status, q=q, page=page, page_size=page_size, db=db, actor=actor
    )


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    return service.get_application(application_id=application_id, db=db, actor=actor)


@router.patch("/{application_id}", response_model=ApplicationDetail)
def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    *,
    request: Request,
):
    result = command(
        db,
        actor,
        request.method + ":" + request.url.path,
        request.headers.get("Idempotency-Key"),
        {
            "application_id": str(application_id),
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        lambda: jsonable_encoder(
            service.update_application(
                application_id=application_id, payload=payload, db=db, actor=actor
            )
        ),
    )
    db.commit()
    return result
