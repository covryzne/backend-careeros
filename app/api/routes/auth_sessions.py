# ruff: noqa: B008
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, StrictBool
from sqlalchemy.orm import Session

from app.core.security import bearer, verified_claims
from app.db.session import get_db
from app.services import auth_sessions as service

router = APIRouter()


class ClaimInput(BaseModel):
    takeover: StrictBool = False


def identity(credentials=Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Silakan login terlebih dahulu.")
    claims = verified_claims(credentials.credentials)
    return uuid.UUID(claims["sub"]), service.supabase_key(claims)


def no_store(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


@router.post("", dependencies=[Depends(no_store)])
def claim(payload: ClaimInput, who=Depends(identity), db: Session = Depends(get_db)):
    owner, key = who
    return service.claim(db, owner, key, takeover=payload.takeover)


@router.get("/current", dependencies=[Depends(no_store)])
def current(who=Depends(identity), db: Session = Depends(get_db)):
    owner, key = who
    return service.describe(service.validate(db, key, owner))


@router.delete("/current", status_code=204, dependencies=[Depends(no_store)])
def logout(who=Depends(identity), db: Session = Depends(get_db)):
    _owner, key = who
    service.revoke(db, key)
