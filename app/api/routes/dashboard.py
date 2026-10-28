# ruff: noqa: B008

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import ActorContext, get_actor
from app.db.session import get_db
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import dashboard_summary

router = APIRouter()


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    actor: ActorContext = Depends(get_actor),
) -> DashboardResponse:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=422, detail="Tanggal awal tidak boleh melewati tanggal akhir."
        )
    result = dashboard_summary(db, actor.owner_id, from_date, to_date)
    db.commit()
    return result
