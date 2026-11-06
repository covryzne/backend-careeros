from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.local_auth import LocalAccount, LocalSession


@pytest.fixture(autouse=True)
def local_mode(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "local_auth_enabled", True)


def register(client, email="first@example.test"):
    return client.post("/api/v1/local-auth/register", json={
        "name": "Local Candidate", "email": email,
        "password": "Password123", "confirmPassword": "Password123",
    })


def test_register_login_identity_logout_and_revoked_access(jobs_client):
    client, db = jobs_client
    assert client.get("/api/v1/jobs").status_code == 401
    created = register(client)
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/local-auth/session", headers=headers).json()["email"] == "first@example.test"
    assert client.get("/api/v1/jobs", headers=headers).status_code == 200
    account = db.scalar(select(LocalAccount))
    assert account.password_hash.startswith("scrypt-v1$") and "Password123" not in account.password_hash
    assert db.scalar(select(LocalSession)).token_hash != token
    logged_in = client.post("/api/v1/local-auth/login", json={"email": "FIRST@example.test", "password": "Password123"})
    assert logged_in.status_code == 200
    assert client.post("/api/v1/local-auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/jobs", headers=headers).status_code == 401
    assert client.get("/api/v1/jobs", headers={"Authorization": "Bearer " + logged_in.json()["token"]}).status_code == 200


def test_account_isolation_expiration_and_duplicate_email(jobs_client):
    client, db = jobs_client
    first = register(client).json()
    second = register(client, "second@example.test").json()
    headers = {"Authorization": "Bearer " + first["token"]}
    job = client.post("/api/v1/jobs", json={"title": "Engineer", "company": "Example"}, headers=headers)
    assert job.status_code == 201
    other = {"Authorization": "Bearer " + second["token"]}
    assert client.get("/api/v1/jobs", headers=other).json()["total"] == 0
    assert client.get("/api/v1/jobs/" + job.json()["id"], headers=other).status_code == 404
    assert register(client).status_code == 409
    row = db.scalar(select(LocalSession).where(LocalSession.user_id == db.scalar(select(LocalAccount.user_id).where(LocalAccount.email == "first@example.test"))))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/v1/local-auth/session", headers=headers).status_code == 401


def test_invalid_credentials_confirmation_and_rate_limit(jobs_client):
    client, _ = jobs_client
    invalid = {"name": "Name", "email": "name@example.test", "password": "short", "confirmPassword": "short"}
    assert client.post("/api/v1/local-auth/register", json=invalid).status_code == 400
    invalid.update(password="Password123", confirmPassword="different")
    assert client.post("/api/v1/local-auth/register", json=invalid).status_code == 400
    register(client)
    for _ in range(5):
        assert client.post("/api/v1/local-auth/login", json={"email": "first@example.test", "password": "incorrect"}).status_code == 401
    assert client.post("/api/v1/local-auth/login", json={"email": "first@example.test", "password": "Password123"}).status_code == 429


def test_local_auth_cannot_be_used_in_staging(jobs_client, monkeypatch):
    client, _ = jobs_client
    monkeypatch.setattr(settings, "app_env", "staging")
    assert register(client).status_code == 404
    with pytest.raises(ValueError, match="LOCAL_AUTH_ENABLED"):
        settings.validate_deployment()
