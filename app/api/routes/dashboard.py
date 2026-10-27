# ruff: noqa: B008

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.routes.profile import LOCAL_OWNER_ID
from app.db.session import get_db
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import dashboard_summary

router = APIRouter()


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> DashboardResponse:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="Tanggal awal tidak boleh melewati tanggal akhir.")
    return dashboard_summary(db, LOCAL_OWNER_ID, from_date, to_date)
