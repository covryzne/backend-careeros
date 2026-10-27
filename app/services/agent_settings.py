from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models.foundation import AgentPreference
from app.services.audit import audit


@dataclass(frozen=True)
class AgentPolicy:
    enabled: bool
    internal_writes_enabled: bool
    approvals_enabled: bool
    max_tool_calls: int
    max_turns: int
    timeout_seconds: int


def policy_for(db, owner_id):
    item = db.scalar(
        select(AgentPreference)
        .where(AgentPreference.owner_id == owner_id)
        .execution_options(populate_existing=True)
    )
    enabled = (item.enabled if item else settings.agent_enabled) and settings.agent_runtime_allowed
    return AgentPolicy(
        enabled,
        (item.internal_writes_enabled if item else settings.agent_internal_writes_enabled),
        (item.approvals_enabled if item else settings.agent_approvals_enabled),
        min(
            item.max_tool_calls if item else settings.agent_max_tool_calls,
            settings.agent_max_tool_calls,
        ),
        min(item.max_turns if item else settings.agent_max_turns, settings.agent_max_turns),
        min(
            item.timeout_seconds if item else settings.agent_timeout_seconds,
            settings.agent_timeout_seconds,
        ),
    )


def read_settings(db, actor):
    if actor.actor_type != "user":
        raise HTTPException(403, "Only the owner may manage agent settings")
    policy = policy_for(db, actor.owner_id)
    item = db.get(AgentPreference, actor.owner_id)
    return {
        "enabled": item.enabled if item else settings.agent_enabled,
        "internal_writes_enabled": policy.internal_writes_enabled,
        "approvals_enabled": policy.approvals_enabled,
        "max_tool_calls": policy.max_tool_calls,
        "max_turns": policy.max_turns,
        "timeout_seconds": policy.timeout_seconds,
        "effective_enabled": policy.enabled,
        "version": item.version if item else 0,
        "runtime_allowed": settings.agent_runtime_allowed,
        "provider_configured": bool(
            settings.llm_base_url and settings.llm_api_key and settings.llm_model
        ),
        "worker_mode": "embedded"
        if settings.agent_worker_embedded and settings.app_env == "development"
        else "external",
        "limits": {
            "max_tool_calls": settings.agent_max_tool_calls,
            "max_turns": settings.agent_max_turns,
            "timeout_seconds": settings.agent_timeout_seconds,
        },
        "external_actions_enabled": False,
        "approval_required": True,
    }


def update_settings(db, actor, payload):
    if actor.actor_type != "user":
        raise HTTPException(403, "Only the owner may manage agent settings")
    current = read_settings(db, actor)
    if payload.expected_version != current["version"]:
        raise HTTPException(
            409,
            {
                "code": "STALE_VERSION",
                "message": "Pengaturan telah berubah. Muat ulang sebelum menyimpan.",
                "current_version": current["version"],
            },
        )
    values = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if values.get("enabled") and not settings.agent_runtime_allowed:
        raise HTTPException(403, "Agent runtime is disabled by the server")
    for field in ("max_tool_calls", "max_turns", "timeout_seconds"):
        if field in values and values[field] > current["limits"][field]:
            raise HTTPException(422, "Setting exceeds the server limit")
    changed = {key: value for key, value in values.items() if current[key] != value}
    if not changed:
        return current
    item = db.get(AgentPreference, actor.owner_id)
    if item is None:
        item = AgentPreference(
            owner_id=actor.owner_id,
            **{
                key: current[key]
                for key in (
                    "enabled",
                    "internal_writes_enabled",
                    "approvals_enabled",
                    "max_tool_calls",
                    "max_turns",
                    "timeout_seconds",
                )
            },
            version=0,
        )
        db.add(item)
    for key, value in changed.items():
        setattr(item, key, value)
    item.version += 1
    item.updated_at = datetime.now(UTC)
    audit(
        db, actor, "agent_settings_updated", "agent_settings", actor.owner_id, version=item.version
    )
    db.flush()
    return read_settings(db, actor)
