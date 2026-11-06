import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models.local_auth import LocalAccount, LocalSession

SESSION_SECONDS = 8 * 60 * 60


def require_local_auth():
    if settings.app_env != "development" or not settings.local_auth_enabled:
        raise HTTPException(404, "Local authentication is not enabled")


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3,
                            dklen=64, maxmem=64 * 1024 * 1024)
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


def password_matches(password, stored):
    try:
        version, salt, _ = stored.split("$")
        if version != "scrypt-v1":
            return False
        return hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), stored)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def account_for_token(db, token):
    require_local_auth()
    if not isinstance(token, str) or not token.startswith("dev_") or len(token) > 128:
        raise HTTPException(401, "Sesi tidak valid. Silakan masuk kembali.")
    row = db.get(LocalSession, token_hash(token))
    if row is None or utc(row.expires_at) <= datetime.now(UTC):
        raise HTTPException(401, "Sesi berakhir. Silakan masuk kembali.")
    account = db.get(LocalAccount, row.user_id)
    if account is None:
        raise HTTPException(401, "Sesi tidak valid.")
    return account


def issue_session(db, account):
    token = "dev_" + secrets.token_urlsafe(32)
    db.add(LocalSession(token_hash=token_hash(token), user_id=account.user_id,
                        expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS)))
    db.commit()
    return {"token": token, "expires_in": SESSION_SECONDS,
            "user": {"id": str(account.user_id), "name": account.name, "email": account.email}}


def login(db, email, password):
    account = db.scalar(select(LocalAccount).where(LocalAccount.email == email))
    if account and account.locked_until and utc(account.locked_until) > datetime.now(UTC):
        raise HTTPException(429, "Terlalu banyak percobaan. Coba lagi dalam 5 menit.")
    # Still perform the expensive hash for unknown emails.
    stored = account.password_hash if account else "scrypt-v1$" + "00" * 16 + "$" + "00" * 64
    if not password_matches(password, stored) or not account:
        if account:
            account.failed_attempts += 1
            if account.failed_attempts >= 5:
                account.locked_until = datetime.now(UTC) + timedelta(minutes=5)
                account.failed_attempts = 0
            db.commit()
        raise HTTPException(401, "Email atau password tidak sesuai.")
    account.failed_attempts = 0
    account.locked_until = None
    return issue_session(db, account)


def new_user_id():
    return uuid.uuid4()
