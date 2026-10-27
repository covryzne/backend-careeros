import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.models.candidate import User
from app.models.foundation import IdempotencyRecord


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def lock_owner(db, actor):
    # Serialize first writes too, when two tabs bootstrap the same owner.
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        db.execute(
            insert(User).values(id=actor.owner_id).on_conflict_do_nothing(index_elements=[User.id])
        )
    owner = db.scalar(select(User).where(User.id == actor.owner_id).with_for_update())
    if owner is None:
        owner = User(id=actor.owner_id)
        db.add(owner)
        db.flush()
    return owner


def command(db, actor, action, key, payload, execute):
    if key and len(key) > 200:
        raise HTTPException(422, "Idempotency key too long")
    # An owner lock serializes independent commands even before a profile exists.
    lock_owner(db, actor)
    if not key:
        return execute()
    record = db.scalar(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.owner_id == actor.owner_id,
            IdempotencyRecord.action == action,
            IdempotencyRecord.key == key,
        )
        .with_for_update()
    )
    digest = fingerprint(payload)
    if record:
        created = (
            record.created_at.replace(tzinfo=UTC)
            if record.created_at.tzinfo is None
            else record.created_at
        )
        if created < datetime.now(UTC) - timedelta(days=1):
            db.delete(record)
            db.flush()
        else:
            if record.payload_hash != digest:
                raise HTTPException(409, "Idempotency key reused with different payload")
            return record.result
    result = execute()
    db.add(
        IdempotencyRecord(
            owner_id=actor.owner_id, action=action, key=key, payload_hash=digest, result=result
        )
    )
    db.flush()
    return result
