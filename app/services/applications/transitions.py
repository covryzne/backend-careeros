from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import Application, ApplicationActivity, AuditEvent

PIPELINE_STAGES = {"applied", "assessment", "interview", "offer", "rejected", "withdrawn"}
TRANSITIONS = {
    "preparing": {"applied", "withdrawn"},
    **{stage: PIPELINE_STAGES - {stage} for stage in PIPELINE_STAGES},
}


def validate_transition(application: Application, target: str, values=None) -> None:
    values = values or {}
    if target != application.status and target not in TRANSITIONS[application.status]:
        raise HTTPException(status_code=409, detail="Perpindahan tahap lamaran tidak diizinkan.")
    if target == "applied" and (
        values.get("application_date", application.application_date) is None
        or values.get("application_channel", application.application_channel) is None
    ):
        raise HTTPException(status_code=422, detail="Tanggal dan channel apply wajib diisi.")


def validate_undo(db: Session, application: Application, target: str) -> None:
    activity = db.scalar(
        select(ApplicationActivity).where(
            ApplicationActivity.application_id == application.id,
            ApplicationActivity.owner_id == application.owner_id,
            ApplicationActivity.activity_type == "status_change",
            ApplicationActivity.metadata_json["version"].as_integer() == application.version,
        )
    )
    metadata = activity.metadata_json if activity else {}
    if (
        metadata.get("change_kind") != "move"
        or metadata.get("to") != application.status
        or metadata.get("from") != target
    ):
        raise HTTPException(
            status_code=409, detail="Perpindahan ini tidak dapat diurungkan. Muat ulang pipeline."
        )


def record_activity(
    db: Session,
    application: Application,
    before: str | None,
    change_kind: str = "move",
    *,
    actor=None,
) -> None:
    submitted = application.status == "applied" and before in {None, "preparing"}
    activity_type = "application_submitted" if submitted else "status_change"
    metadata = {
        "from": before,
        "to": application.status,
        "change_kind": change_kind,
        "version": application.version,
    }
    db.add(
        ApplicationActivity(
            owner_id=application.owner_id,
            application_id=application.id,
            activity_type=activity_type,
            title=(
                "Apply dikonfirmasi manual"
                if submitted
                else "Perpindahan tahap diurungkan"
                if change_kind == "undo"
                else "Tahap lamaran diperbarui"
            ),
            description=f"{before or 'new'} → {application.status}",
            occurred_at=datetime.now(UTC),
            source=actor.actor_type if actor else "user",
            metadata_json=metadata,
        )
    )
    db.add(
        AuditEvent(
            owner_id=application.owner_id,
            actor_type=actor.actor_type if actor else "user",
            actor_id=application.owner_id,
            action=activity_type,
            entity_type="application",
            entity_id=application.id,
            before_data={"status": before} if before else None,
            after_data={
                "status": application.status,
                "version": application.version,
                "change_kind": change_kind,
            },
        )
    )
