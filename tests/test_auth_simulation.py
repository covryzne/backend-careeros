"""Opt-in real JWKS HTTP roundtrip through a local Postman simulation."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import Settings, settings
from app.core.security import jwks_client

pytestmark = pytest.mark.skipif(
    not os.getenv("AUTH_SIMULATION_URL"), reason="Start local Postman simulation first"
)


def test_api_verifies_http_jwks_and_fails_closed_on_outage(jobs_client, monkeypatch):
    client, _ = jobs_client
    base = os.environ["AUTH_SIMULATION_URL"].rstrip("/")
    issuer = "https://auth-fixture.supabase.co/auth/v1"
    # Test-only endpoint wiring. Application configuration still requires HTTPS.
    monkeypatch.setattr(
        Settings,
        "auth_endpoints",
        lambda self: (issuer, f"{base}/supabase/auth/v1/.well-known/jwks.json"),
    )
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "allow_local_identity", False)
    key = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    public.update(kid="synthetic-http-key", alg="ES256", use="sig")

    def configure(status):
        request = Request(
            f"{base}/supabase/__test/control",
            data=json.dumps({"keys": [public], "status": status}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200

    configure(200)
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "iss": issuer,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    token = jwt.encode(claims, key, algorithm="ES256", headers={"kid": public["kid"]})
    headers = {"Authorization": f"Bearer {token}"}
    jwks_client.cache_clear()
    try:
        result = client.patch(
            "/api/v1/profile", headers=headers, json={"full_name": "HTTP fixture"}
        )
        assert result.status_code == 200, result.text
        configure(503)
        # A previously fetched valid key remains usable until the configured TTL.
        assert client.get("/api/v1/profile", headers=headers).status_code == 200
        jwks_client.cache_clear()  # A cold/expired cache cannot rely on previously fetched keys.
        failed = client.get("/api/v1/profile", headers=headers)
        assert failed.status_code == 503
        assert token not in failed.text
        configure(200)
        jwks_client.cache_clear()
        assert client.get("/api/v1/profile", headers=headers).status_code == 200
    finally:
        configure(200)
        jwks_client.cache_clear()
