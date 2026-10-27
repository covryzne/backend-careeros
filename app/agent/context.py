import uuid
from dataclasses import dataclass

from app.core.security import ActorContext


@dataclass(frozen=True)
class ToolContext:
    actor: ActorContext
    run_id: uuid.UUID
    allowed_tools: frozenset[str]
    scope: dict

    def check_scope(self, *, job_id=None, application_id=None):
        from fastapi import HTTPException

        for field, value in (("job_ids", job_id), ("application_ids", application_id)):
            if value is not None and self.scope.get(field) and str(value) not in self.scope[field]:
                raise HTTPException(403, "Resource outside run scope")
