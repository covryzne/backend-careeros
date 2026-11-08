import hashlib
import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.models.auth_session import AuthSession
from app.models.candidate import User

SESSION_SECONDS = 6 * 60 * 60
TAKEOVER_MESSAGE = "Akun ini sedang aktif di browser lain. Ingin pindah ke browser ini?"


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def session_error(code, message, status=401):
    return HTTPException(
        status, {"code": code, "message": message}, headers={"Cache-Control": "private, no-store"}
    )


def supabase_key(claims):
    import uuid

    try:
        session_id = str(uuid.UUID(claims["session_id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise session_error("SESSION_INVALID", "Sesi tidak valid. Silakan login ulang.") from None
    return hashlib.sha256(f"supabase:{session_id}".encode()).hexdigest()


def validate(db, key, owner=None):
    row = db.get(AuthSession, key)
    if row is None or (owner is not None and row.user_id != owner):
        raise session_error("SESSION_INVALID", "Sesi tidak tersedia. Silakan login ulang.")
    if row.revoked_at is not None:
        if row.revocation_reason == "replaced":
            raise session_error(
                "SESSION_REPLACED", "Sesi Anda berakhir karena akun dipindahkan ke browser lain."
            )
        raise session_error("SESSION_EXPIRED", "Sesi berakhir. Silakan login ulang.")
    if utc(row.expires_at) <= datetime.now(UTC):
        raise session_error("SESSION_EXPIRED", "Sesi berakhir. Silakan login ulang.")
    return row


def claim(db, owner, key, *, takeover=False):
    # The user row serializes claims across API workers. The partial unique index
    # provides a second guarantee that at most one unrevoked lease exists per user.
    dialect = db.get_bind().dialect.name
    if dialect in {"postgresql", "sqlite"}:
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        db.execute(insert(User).values(id=owner).on_conflict_do_nothing(index_elements=["id"]))
    db.scalar(select(User).where(User.id == owner).with_for_update())
    existing = db.get(AuthSession, key)
    if existing is not None:
        try:
            row = validate(db, key, owner)
            result = describe(row)
            db.commit()
            return result
        except HTTPException:
            db.rollback()
            raise
    now = datetime.now(UTC)
    active = db.scalar(
        select(AuthSession).where(AuthSession.user_id == owner, AuthSession.revoked_at.is_(None))
    )
    if active is not None:
        expired = utc(active.expires_at) <= now
        if not expired and not takeover:
            db.rollback()
            raise session_error("SESSION_ACTIVE_ELSEWHERE", TAKEOVER_MESSAGE, 409)
        active.revoked_at = now
        active.revocation_reason = "expired" if expired else "replaced"
        db.flush()
    row = AuthSession(
        session_key=key,
        user_id=owner,
        created_at=now,
        expires_at=now + timedelta(seconds=SESSION_SECONDS),
    )
    db.add(row)
    db.commit()
    return describe(row)


def describe(row):
    return {
        "expires_in": max(0, math.ceil((utc(row.expires_at) - datetime.now(UTC)).total_seconds())),
        "expires_at": utc(row.expires_at).isoformat(),
    }


def revoke(db, key):
    row = db.get(AuthSession, key)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revocation_reason = "logout"
        db.commit()
