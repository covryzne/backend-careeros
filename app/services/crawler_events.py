"""Bounded, structured progress messages, never raw process stdout or credentials."""

from sqlalchemy import func, select

from app.models.crawler import CrawlRunEvent

MAX_EVENTS = 5000


def append(db, run, message, *, level="INFO", platform=None):
    # Callers already own the run row lock (or are creating an uncommitted run).
    previous = (
        db.scalar(
            select(func.max(CrawlRunEvent.sequence_number)).where(CrawlRunEvent.run_id == run.id)
        )
        or 0
    )
    if previous >= MAX_EVENTS:
        return
    if previous == MAX_EVENTS - 1:
        message, level = "Batas log tercapai; progres dan hasil tetap disimpan.", "WARN"
    db.add(
        CrawlRunEvent(
            run_id=run.id,
            sequence_number=previous + 1,
            level=level,
            platform=platform,
            message=message[:600],
        )
    )
    db.flush()
