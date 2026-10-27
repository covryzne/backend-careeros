from sqlalchemy.orm import Session

from app.core.security import ActorContext
from app.models.workflow import AuditEvent


def audit(db: Session, actor: ActorContext, action: str, entity: str, entity_id, *, version=None):
    # Log structural changes only, never raw candidate text or provider credentials.
    db.add(
        AuditEvent(
            owner_id=actor.owner_id,
            actor_type=actor.actor_type,
            actor_id=actor.owner_id,
            action=action,
            entity_type=entity,
            entity_id=entity_id,
            after_data={
                "version": version,
                "approval_id": str(actor.approved_action_id) if actor.approved_action_id else None,
            },
        )
    )
