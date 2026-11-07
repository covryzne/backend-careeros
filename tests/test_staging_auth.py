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
