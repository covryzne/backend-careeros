"""Owner-managed provider metadata; secrets never leave this service boundary."""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import settings
from app.models.foundation import ProviderSetting
from app.schemas.provider_settings import ProviderSettingsPatch
from app.services.audit import audit


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api_key: SecretStr


def cipher(*, create=False):
    try:
        if settings.provider_encryption_key:
            return Fernet(settings.provider_encryption_key.get_secret_value().encode())
        if settings.app_env not in {"development", "test"}:
            raise ValueError()
        root = settings.document_storage_dir.resolve()
        path = root / ".provider-key"
        if path.is_symlink():
            raise ValueError()
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
        with path.open("rb") as stream:
            key = stream.read(128)
        return Fernet(key)
    except (OSError, ValueError, TypeError):
        raise HTTPException(
            503, "Penyimpanan kredensial provider tidak tersedia. Periksa kunci enkripsi backend."
        ) from None


def row_for(db, owner_id):
    return db.scalar(
        select(ProviderSetting)
        .where(ProviderSetting.owner_id == owner_id)
        .execution_options(populate_existing=True)
    )


def config_for(db, owner_id):
    item = row_for(db, owner_id)
    if item:
        try:
            data = json.loads(cipher().decrypt(item.encrypted_api_key.encode()))
            if (
                data["owner_id"] != str(owner_id)
                or data["base_url"] != item.base_url
                or data["model"] != item.model
                or not isinstance(data["api_key"], str)
                or not data["api_key"]
            ):
                raise ValueError()
            return ProviderConfig(item.base_url, item.model, SecretStr(data["api_key"]))
        except (InvalidToken, ValueError, TypeError, KeyError):
            raise HTTPException(
                503,
                "Kredensial provider tidak dapat dibaca. Simpan ulang API key atau pulihkan kunci enkripsi backend.",
            ) from None
    if all((settings.llm_base_url, settings.llm_model, settings.llm_api_key)):
        try:
            base = ProviderSettingsPatch.valid_url(settings.llm_base_url)
            model = ProviderSettingsPatch.valid_model(settings.llm_model)
        except ValueError:
            raise HTTPException(503, "Konfigurasi provider backend tidak valid.") from None
        return ProviderConfig(base, model, SecretStr(settings.llm_api_key))
    return None


def configured(db, owner_id):
    try:
        return config_for(db, owner_id) is not None
    except HTTPException:
        return False


def owner_only(actor, permission):
    if actor.actor_type != "user":
        raise HTTPException(403, "Only the owner may manage provider settings")
    actor.require(permission)


def read_settings(db, actor):
    owner_only(actor, "profile:read")
    item = row_for(db, actor.owner_id)
    base = item.base_url if item else settings.llm_base_url or ""
    # Invalid legacy URLs must not echo credentials/query strings into the UI.
    try:
        base = ProviderSettingsPatch.valid_url(base) if base else ""
    except ValueError:
        base = ""
    return {
        "base_url": base,
        "model": item.model if item else settings.llm_model or "",
        "api_key_set": bool(item.encrypted_api_key if item else settings.llm_api_key),
        "configured": configured(db, actor.owner_id),
        "source": "saved"
        if item
        else "environment"
        if any((settings.llm_base_url, settings.llm_model, settings.llm_api_key))
        else "none",
        "version": item.version if item else 0,
    }


def update_settings(db, actor, payload):
    owner_only(actor, "profile:write")
    current = read_settings(db, actor)
    if payload.expected_version != current["version"]:
        raise HTTPException(
            409, "Konfigurasi provider telah berubah. Muat ulang sebelum menyimpan."
        )
    item = row_for(db, actor.owner_id)
    if payload.api_key is None:
        if payload.base_url != current["base_url"]:
            raise HTTPException(422, "Masukkan API key saat mengganti endpoint provider.")
        config = config_for(db, actor.owner_id)
        if config is None:
            raise HTTPException(422, "Masukkan API key provider terlebih dahulu.")
        key = config.api_key
    else:
        key = payload.api_key
    if (
        item
        and payload.api_key is None
        and item.base_url == payload.base_url
        and item.model == payload.model
    ):
        return current
    encrypted = (
        cipher(create=True)
        .encrypt(
            json.dumps(
                {
                    "owner_id": str(actor.owner_id),
                    "base_url": payload.base_url,
                    "model": payload.model,
                    "api_key": key.get_secret_value(),
                }
            ).encode()
        )
        .decode()
    )
    if item is None:
        item = ProviderSetting(
            owner_id=actor.owner_id,
            base_url=payload.base_url,
            model=payload.model,
            encrypted_api_key=encrypted,
            version=0,
        )
        db.add(item)
    item.base_url = payload.base_url
    item.model = payload.model
    item.encrypted_api_key = encrypted
    item.version += 1
    item.updated_at = datetime.now(UTC)
    audit(
        db,
        actor,
        "provider_settings_updated",
        "provider_settings",
        actor.owner_id,
        version=item.version,
    )
    db.flush()
    return read_settings(db, actor)
