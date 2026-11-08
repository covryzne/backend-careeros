# ruff: noqa: B008
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.candidate import User
from app.models.local_auth import LocalAccount
from app.services import auth_sessions
from app.services import local_auth as service


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(dependencies=[Depends(service.require_local_auth), Depends(no_store)])


def credentials(payload):
    email, password = payload.get("email"), payload.get("password")
    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(
        r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()
    ):
        raise HTTPException(400, "Masukkan email yang valid.")
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise HTTPException(400, "Password harus 8–128 karakter.")
    return email.strip().casefold(), password


@router.post("/register", status_code=201)
def register(payload: dict, request: Request, db: Session = Depends(get_db)):
    browser_login_response(request, db)
    email, password = credentials(payload)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise HTTPException(400, "Isi nama lengkap maksimal 120 karakter.")
    if payload.get("confirmPassword") != password:
        raise HTTPException(400, "Konfirmasi password belum sama.")
    if db.scalar(select(LocalAccount.user_id).where(LocalAccount.email == email)):
        raise HTTPException(409, "Email sudah terdaftar. Silakan masuk.")
    owner_id = service.new_user_id()
    db.add(User(id=owner_id))
    db.flush()
    account = LocalAccount(user_id=owner_id, email=email, name=name.strip(),
                           password_hash=service.password_hash(password))
    db.add(account)
    try:
        db.flush()
        return service.issue_session(db, account)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email sudah terdaftar. Silakan masuk.") from None


@router.post("/login")
def login(payload: dict, request: Request, db: Session = Depends(get_db)):
    email, password = credentials(payload)
    takeover = payload.get("takeover", False)
    if not isinstance(takeover, bool):
        raise HTTPException(400, "Konfirmasi pengambilalihan tidak valid.")
    existing = browser_login_response(request, db, email)
    if existing is not None:
        return existing
    return service.login(db, email, password, takeover=takeover)


def bearer_token(request):
    value = request.headers.get("authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def browser_login_response(request, db, email=None):
    token = bearer_token(request)
    if not token:
        return
    try:
        account = service.account_for_token(db, token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return
        raise
    if email is not None and account.email.casefold() == email.casefold():
        row = auth_sessions.validate(db, service.token_hash(token), account.user_id)
        return {"token": token, **auth_sessions.describe(row),
                "user": {"id": str(account.user_id), "name": account.name, "email": account.email}}
    raise auth_sessions.session_error("BROWSER_SESSION_ACTIVE",
        "Browser ini sudah memiliki akun aktif. Silakan logout terlebih dahulu.", 409)


@router.get("/session")
def session(request: Request, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    account = service.account_for_token(db, bearer_token(request))
    row = auth_sessions.validate(db, service.token_hash(bearer_token(request)))
    return {"id": str(account.user_id), "name": account.name, "email": account.email,
            **auth_sessions.describe(row)}


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)):
    auth_sessions.revoke(db, service.token_hash(bearer_token(request)))
