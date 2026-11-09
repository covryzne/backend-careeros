"""Cryptographically signed synthetic tokens; never uses a real Supabase project."""

import json
import uuid
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jwt.exceptions import PyJWKClientConnectionError
from sqlalchemy import select

from app.core import security
from app.core.config import Settings, settings
from app.main import app
from app.models.candidate import User
from scripts.seed_options import check_staging_user, seed_actor


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "allow_local_identity", False)
    monkeypatch.setattr(settings, "supabase_url", "https://auth-fixture.supabase.co")
    monkeypatch.setattr(settings, "supabase_jwks_url", None)
    monkeypatch.setattr(settings, "provider_encryption_key", None)
    # Pydantic assignment accepts the already constructed SecretStr from isolated settings.
    configured = Settings(
        _env_file=None,
        database_url="sqlite://",
        provider_encryption_key=Fernet.generate_key().decode(),
    )
    monkeypatch.setattr(settings, "provider_encryption_key", configured.provider_encryption_key)


@pytest.fixture(params=["ES256", "RS256"])
def signer(request, monkeypatch):
    algorithm = request.param
    key = (
        ec.generate_private_key(ec.SECP256R1())
        if algorithm == "ES256"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    public = key.public_key()
    monkeypatch.setattr(
        security,
        "jwks_client",
        lambda *args: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(
                key=public, algorithm_name=algorithm
            )
        ),
    )

    def sign(owner=None, **overrides):
        now = datetime.now(UTC)
        claims = {
            "sub": str(owner or uuid.uuid4()),
            "iss": settings.auth_endpoints()[0],
            "aud": "authenticated",
            "role": "authenticated",
            "session_id": str(uuid.uuid4()),
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        claims.update(overrides)
        for field in tuple(claims):
            if claims[field] is None:
                del claims[field]
        return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "synthetic-key"})

    return sign


def test_staging_startup_and_missing_token(staging):
    with TestClient(app) as client:
        response = client.get("/api/v1/profile")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "field,value",
    [
        ("allow_local_identity", True),
        ("supabase_url", None),
        ("supabase_url", "http://insecure.example"),
        ("supabase_jwks_url", "https://wrong-project.example/keys"),
        ("provider_encryption_key", None),
        ("allowed_origins", ["*"]),
    ],
)
def test_invalid_deployment_fails_startup(staging, monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(ValueError), TestClient(app):
        pass


def test_authenticated_owners_are_isolated(jobs_client, staging, signer):
    client, db = jobs_client
    first, second = uuid.uuid4(), uuid.uuid4()
    first_headers = {"Authorization": f"Bearer {signer(first)}"}
    second_headers = {"Authorization": f"Bearer {signer(second)}"}
    assert client.post("/api/v1/auth-sessions", headers=first_headers, json={}).status_code == 200
    assert client.post("/api/v1/auth-sessions", headers=second_headers, json={}).status_code == 200
    created = client.patch("/api/v1/profile", headers=first_headers, json={"full_name": "First"})
    assert created.status_code == 200, created.text
    assert db.get(User, first) is not None
    assert client.get("/api/v1/profile", headers=second_headers).status_code == 404
    assert (
        client.patch(
            "/api/v1/profile", headers=second_headers, json={"full_name": "Second"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/profile", headers=first_headers).json()["full_name"] == "First"
    assert client.get("/api/v1/profile", headers=second_headers).json()["full_name"] == "Second"
    assert db.get(User, security.LOCAL_OWNER_ID) is None


def test_stale_tab_account_cannot_read_or_write_as_the_new_cookie_owner(
    jobs_client, staging, signer
):
    client, _db = jobs_client
    first, second = uuid.uuid4(), uuid.uuid4()
    a = {"Authorization": f"Bearer {signer(first)}", "X-CareerOS-Account": str(first)}
    b = {"Authorization": f"Bearer {signer(second)}", "X-CareerOS-Account": str(second)}
    assert client.post("/api/v1/auth-sessions", headers=a, json={}).status_code == 200
    assert client.post("/api/v1/auth-sessions", headers=b, json={}).status_code == 200
    assert (
        client.patch("/api/v1/profile", headers=a, json={"full_name": "Account A"}).status_code
        == 200
    )
    stale = {**b, "X-CareerOS-Account": str(first)}
    for method, payload in (
        (client.get, {}),
        (client.patch, {"json": {"full_name": "Stale A form"}}),
    ):
        response = method("/api/v1/profile", headers=stale, **payload)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ACCOUNT_SESSION_CHANGED"
    assert client.get("/api/v1/profile", headers=b).status_code == 404
    assert client.get("/api/v1/profile", headers=a).json()["full_name"] == "Account A"
    # The consistency header never grants access without a valid user token.
    assert (
        client.get("/api/v1/profile", headers={"X-CareerOS-Account": str(first)}).status_code == 401
    )


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"iss": "https://production.example/auth/v1"},
        {"aud": "wrong"},
        {"role": "service_role"},
        {"role": "anon"},
        {"sub": "not-a-uuid"},
        {"exp": None},
        {"iat": None},
        {"sub": None},
        {"sub": str(security.LOCAL_OWNER_ID)},
        {"sub": str(uuid.UUID(int=0))},
        {"iat": datetime.now(UTC) + timedelta(hours=1)},
    ],
)
def test_invalid_claims_are_rejected(staging, signer, claims):
    with pytest.raises(HTTPException) as caught:
        security.verified_owner(signer(**claims))
    assert caught.value.status_code == 401


def test_tampering_and_algorithm_confusion(staging, signer):
    token = signer()
    header, payload, signature = token.split(".")
    decoded = json.loads(jwt.utils.base64url_decode(payload))
    decoded["sub"] = str(uuid.uuid4())
    modified = jwt.utils.base64url_encode(json.dumps(decoded).encode()).decode()
    for bad in [
        f"{header}.{modified}.{signature}",
        "broken",
        jwt.encode(
            decoded,
            "synthetic-shared-secret-for-negative-testing-only",
            algorithm="HS256",
            headers={"kid": "synthetic-key"},
        ),
    ]:
        with pytest.raises(HTTPException) as caught:
            security.verified_owner(bad)
        assert caught.value.status_code == 401


def test_jwks_unavailable_has_sanitized_503(staging, signer, monkeypatch):
    def unavailable(token):
        raise PyJWKClientConnectionError("sensitive upstream diagnostics")

    monkeypatch.setattr(
        security, "jwks_client", lambda *args: SimpleNamespace(get_signing_key_from_jwt=unavailable)
    )
    with pytest.raises(HTTPException) as caught:
        security.verified_owner(signer())
    assert caught.value.status_code == 503
    assert "sensitive" not in caught.value.detail


def test_token_rejection_logs_distinguish_cause_without_leaking_claims(staging, signer, caplog):
    token = signer(iss="https://private-project.example/auth/v1")
    with pytest.raises(HTTPException) as caught:
        security.verified_owner(token)
    assert caught.value.status_code == 401
    assert "reason=InvalidIssuerError" in caplog.text
    assert token not in caplog.text and "private-project.example" not in caplog.text
    caplog.clear()
    token = jwt.encode({"sub": "private-user-marker"}, "synthetic-test-secret-long-enough-for-hs256", algorithm="HS256")
    with pytest.raises(HTTPException):
        security.verified_owner(token)
    assert "reason=legacy_hs256" in caplog.text
    assert token not in caplog.text and "private-user-marker" not in caplog.text


def test_supplied_bad_token_never_falls_back_to_local(jobs_client):
    client, _ = jobs_client
    assert client.get(
        "/api/v1/profile", headers={"Authorization": "Bearer broken"}
    ).status_code in {401, 503}


def test_seed_staging_requires_explicit_owner_and_confirmation(staging):
    for args in [
        Namespace(owner_id=None, confirm_staging=True),
        Namespace(owner_id=uuid.uuid4(), confirm_staging=False),
        Namespace(owner_id=security.LOCAL_OWNER_ID, confirm_staging=True),
    ]:
        with pytest.raises(SystemExit):
            seed_actor(args)
    owner = uuid.uuid4()
    assert seed_actor(Namespace(owner_id=owner, confirm_staging=True)).owner_id == owner
    with pytest.raises(SystemExit):
        check_staging_user(
            SimpleNamespace(scalar=lambda *args: False), security.ActorContext(owner)
        )


def test_seeding_production_is_disabled(staging, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(SystemExit):
        seed_actor(Namespace(owner_id=uuid.uuid4(), confirm_staging=True))


def test_takeover_revokes_old_session_and_refresh_cannot_extend_lease(jobs_client, staging, signer):
    from app.models.auth_session import AuthSession
    from app.services.auth_sessions import utc

    client, db = jobs_client
    owner, session_id = uuid.uuid4(), str(uuid.uuid4())
    first = {"Authorization": f"Bearer {signer(owner, session_id=session_id)}"}
    second = {"Authorization": f"Bearer {signer(owner)}"}
    # A signed JWT alone cannot bypass the server-side login lease.
    assert client.get("/api/v1/jobs", headers=first).status_code == 401
    claimed = client.post("/api/v1/auth-sessions", headers=first, json={})
    assert claimed.status_code == 200
    assert claimed.json()["expires_in"] == 21600
    expires_at = claimed.json()["expires_at"]
    row = db.scalar(select(AuthSession))
    assert (utc(row.expires_at) - utc(row.created_at)).total_seconds() == 21600
    denied = client.post("/api/v1/auth-sessions", headers=second, json={})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "SESSION_ACTIVE_ELSEWHERE"
    assert client.get("/api/v1/jobs", headers=first).status_code == 200
    refreshed = {"Authorization": f"Bearer {signer(owner, session_id=session_id)}"}
    resumed = client.post("/api/v1/auth-sessions", headers=refreshed, json={})
    assert resumed.json()["expires_at"] == expires_at
    assert client.post("/api/v1/auth-sessions", headers=second, json={"takeover": True}).status_code == 200
    for headers in (first, refreshed):
        result = client.get("/api/v1/jobs", headers=headers)
        assert result.status_code == 401
        assert result.json()["error"]["code"] == "SESSION_REPLACED"
        assert client.post("/api/v1/auth-sessions", headers=headers, json={"takeover": True}).status_code == 401
    assert client.delete("/api/v1/auth-sessions/current", headers=first).status_code == 204
    assert client.get("/api/v1/jobs", headers=second).status_code == 200


def test_absolute_expiry_requires_a_new_provider_session(jobs_client, staging, signer):
    from app.models.auth_session import AuthSession

    client, db = jobs_client
    owner, session_id = uuid.uuid4(), str(uuid.uuid4())
    first = {"Authorization": f"Bearer {signer(owner, session_id=session_id)}"}
    assert client.post("/api/v1/auth-sessions", headers=first, json={}).status_code == 200
    row = db.scalar(select(AuthSession))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    refreshed = {"Authorization": f"Bearer {signer(owner, session_id=session_id)}"}
    expired = client.get("/api/v1/auth-sessions/current", headers=refreshed)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "SESSION_EXPIRED"
    assert client.post("/api/v1/auth-sessions", headers=refreshed, json={"takeover": True}).status_code == 401
    fresh = {"Authorization": f"Bearer {signer(owner)}"}
    assert client.post("/api/v1/auth-sessions", headers=fresh, json={}).status_code == 200
    assert client.post("/api/v1/auth-sessions", headers=fresh, json={"takeover": "false"}).status_code == 422


@pytest.mark.parametrize("session_id", [None, "invalid", 123])
def test_missing_or_invalid_provider_session_is_rejected(jobs_client, staging, signer, session_id):
    client, _ = jobs_client
    headers = {"Authorization": f"Bearer {signer(session_id=session_id)}"}
    assert client.post("/api/v1/auth-sessions", headers=headers, json={}).status_code == 401
