# ruff: noqa: B008
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.models.crawler import CrawlRunEvent, CrawlRunItem
from app.schemas.crawler import (
    CapabilitiesRead,
    CrawlCreate,
    CrawlEventPage,
    ItemPage,
    RunPage,
    RunRead,
    SourceRead,
)
from app.schemas.crawler_stats import CrawlerStatsResponse
from app.services import crawler_runs as service
from app.services.crawler_stats_read import read_stats

router = APIRouter()


@router.get("/stats", response_model=CrawlerStatsResponse)
def statistics(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    platform: str | None = Query(
        default=None, min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$"
    ),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    return read_stats(db, actor, from_date, to_date, platform)


@router.get("/capabilities", response_model=CapabilitiesRead)
def capabilities(db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)):
    return service.capabilities(db, actor)


@router.post("/runs", response_model=RunRead, status_code=202)
def create(
    payload: CrawlCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    run = service.enqueue(db, actor, payload, idempotency_key)
    result = service.run_data(db, run)
    db.commit()
    return result


@router.get("/runs", response_model=RunPage)
def history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    return service.page(db, actor, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(
    run_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    return service.run_data(db, service.owned(db, actor, run_id))


@router.get("/runs/{run_id}/sources", response_model=list[SourceRead])
def sources(
    run_id: uuid.UUID, db: Session = Depends(get_db), actor: ActorContext = Depends(get_actor)
):
    return service.run_data(db, service.owned(db, actor, run_id))["sources"]


@router.get("/runs/{run_id}/results", response_model=ItemPage)
def results(
    run_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    service.owned(db, actor, run_id)
    query = select(CrawlRunItem).where(CrawlRunItem.run_id == run_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(CrawlRunItem.created_at, CrawlRunItem.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                k: getattr(row, k)
                for k in ("id", "platform", "keyword", "title", "url", "status", "job_id", "error")
            }
            | {
                "date_check": row.provenance.get("date_check"),
                "posting_date": row.provenance.get("posting_date"),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": page * page_size < total,
    }


@router.get("/runs/{run_id}/events", response_model=CrawlEventPage)
def events(
    run_id: uuid.UUID,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
):
    service.owned(db, actor, run_id)
    rows = db.scalars(
        select(CrawlRunEvent)
        .where(CrawlRunEvent.run_id == run_id, CrawlRunEvent.sequence_number > after)
        .order_by(CrawlRunEvent.sequence_number)
        .limit(limit + 1)
    ).all()
    selected = rows[:limit]
    return {
        "items": [
            {
                k: getattr(row, k)
                for k in ("sequence_number", "created_at", "level", "platform", "message")
            }
            for row in selected
        ],
        "next_after": selected[-1].sequence_number if selected else after,
        "has_more": len(rows) > limit,
    }
