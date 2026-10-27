# ruff: noqa: B008
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.workflow import (
    JobCreate,
    JobDetail,
    JobListResponse,
    JobStatus,
    JobSummary,
    JobUpdate,
)
from app.services import job_workflows as service
from app.services.idempotency import command

router = APIRouter()


@router.post("", response_model=JobSummary, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    *,
    request: Request,
) -> JobSummary:
    result = command(
        db,
        actor,
        request.method + ":" + request.url.path,
        request.headers.get("Idempotency-Key"),
        {"payload": payload.model_dump(mode="json", exclude_unset=True)},
        lambda: jsonable_encoder(service.create_job(payload=payload, db=db, actor=actor)),
    )
    db.commit()
    return result


@router.patch("/{job_id}", response_model=JobSummary)
def update_job(
    job_id: uuid.UUID,
    payload: JobUpdate,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    *,
    request: Request,
) -> JobSummary:
    result = command(
        db,
        actor,
        request.method + ":" + request.url.path,
        request.headers.get("Idempotency-Key"),
        {"job_id": str(job_id), "payload": payload.model_dump(mode="json", exclude_unset=True)},
        lambda: jsonable_encoder(
            service.update_job(job_id=job_id, payload=payload, db=db, actor=actor)
        ),
    )
    db.commit()
    return result


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
    *,
    request: Request,
) -> None:
    result = command(
        db,
        actor,
        request.method + ":" + request.url.path,
        request.headers.get("Idempotency-Key"),
        {"job_id": str(job_id)},
        lambda: jsonable_encoder(service.delete_job(job_id=job_id, db=db, actor=actor)),
    )
    db.commit()
    return result


@router.get("", response_model=JobListResponse)
def list_jobs(
    q: str | None = Query(default=None),
    status: JobStatus | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
    work_arrangement: str | None = Query(default=None),
    min_match_score: float | None = Query(default=None, ge=0, le=100),
    max_match_score: float | None = Query(default=None, ge=0, le=100),
    discovered_from: date | None = Query(default=None),
    discovered_to: date | None = Query(default=None),
    sort: str = Query(default="discovered_at"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
) -> JobListResponse:
    return service.list_jobs(
        q=q,
        status=status,
        source_id=source_id,
        work_arrangement=work_arrangement,
        min_match_score=min_match_score,
        max_match_score=max_match_score,
        discovered_from=discovered_from,
        discovered_to=discovered_to,
        sort=sort,
        page=page,
        page_size=page_size,
        db=db,
        actor=actor,
    )


@router.get("/{job_id}", response_model=JobDetail)
def get_job(
    job_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    return service.get_job(db, actor, job_id)
