import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException

from app.core.config import settings

LOCAL_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_APPROVAL_CAPABILITY = object()


@dataclass(frozen=True)
class ActorContext:
    owner_id: uuid.UUID
    actor_type: str = "user"
    permissions: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "profile:read",
                "profile:write",
                "profile:confirm",
                "profile:propose",
                "jobs:read",
                "jobs:write",
                "applications:read",
                "applications:write",
                "reminders:read",
                "reminders:write",
                "drafts:read",
                "drafts:write",
            }
        )
    )
    approved_action_id: uuid.UUID | None = None
    approval_capability: object | None = None

    def require(self, permission: str, *, approval: bool = False) -> None:
        if permission not in self.permissions:
            raise HTTPException(403, "Permission denied")
        if (
            approval
            and self.actor_type == "agent"
            and (
                self.approved_action_id is None
                or self.approval_capability is not _APPROVAL_CAPABILITY
            )
        ):
            raise HTTPException(403, "APPROVAL_REQUIRED")


def get_actor() -> ActorContext:
    if settings.app_env not in {"development", "test"} or not settings.allow_local_identity:
        raise HTTPException(401, "Authentication required; local identity is development-only")
    return ActorContext(LOCAL_OWNER_ID)
