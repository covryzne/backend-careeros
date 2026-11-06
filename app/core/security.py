import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import PyJWKClientConnectionError, PyJWKSetError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

LOCAL_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_APPROVAL_CAPABILITY = object()
bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=8)
def jwks_client(url: str, timeout: float, lifespan: int) -> jwt.PyJWKClient:
    # Cache the set with a TTL, not individual keys indefinitely (key rotation).
    return jwt.PyJWKClient(url, timeout=timeout, lifespan=lifespan, cache_keys=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        401,
        "A valid Supabase user access token is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verified_owner(token: str) -> uuid.UUID:
    try:
        issuer, url = settings.auth_endpoints()
    except ValueError:
        raise HTTPException(503, "Authentication is not configured") from None
    try:
        if len(token) > 8192:
            raise unauthorized()
        header = jwt.get_unverified_header(token)
        # Never select algorithms or network endpoints from untrusted token claims.
        if header.get("alg") not in {"ES256", "RS256"} or not header.get("kid"):
            raise unauthorized()
        key = jwks_client(
            url, settings.auth_jwks_timeout_seconds, settings.auth_jwks_cache_seconds
        ).get_signing_key_from_jwt(token)
        if key.algorithm_name != header["alg"]:
            raise unauthorized()
        claims = jwt.decode(
            token,
            key.key,
            algorithms=[key.algorithm_name],
            issuer=issuer,
            audience=settings.supabase_jwt_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "role"]},
        )
        if claims["role"] != "authenticated":
            raise unauthorized()
        owner = uuid.UUID(claims["sub"])
        if owner.int == 0 or owner == LOCAL_OWNER_ID:
            raise unauthorized()
        return owner
    except (PyJWKClientConnectionError, PyJWKSetError, OSError):
        raise HTTPException(503, "Authentication key service is unavailable") from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise unauthorized() from None


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


def get_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> ActorContext:
    if settings.app_env == "development" and settings.local_auth_enabled:
        from app.services.local_auth import account_for_token
        if credentials is None:
            raise unauthorized()
        account = account_for_token(db, credentials.credentials)
        return ActorContext(account.user_id)
    if credentials is None:
        if settings.app_env in {"development", "test"} and settings.allow_local_identity:
            return ActorContext(LOCAL_OWNER_ID)
        raise unauthorized()
    return ActorContext(verified_owner(credentials.credentials))
