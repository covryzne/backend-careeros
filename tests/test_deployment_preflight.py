"""Offline regression checks for the production rollout failures."""

import base64
import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "deployment_preflight", Path(__file__).resolve().parents[1] / "scripts/verify_deployment.py"
)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture
def configs():
    env = {
        "APP_ENV": "production",
        "ALLOW_LOCAL_IDENTITY": "false",
        "LOCAL_AUTH_ENABLED": "false",
        "DATABASE_URL": "postgresql+psycopg://fixture@db.example.test:5432/postgres",
        "SUPABASE_URL": "https://fixture.supabase.co",
        "PROVIDER_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"x" * 32).decode(),
        "ALLOWED_ORIGINS": '["https://workspace.example.test"]',
        "DOCUMENT_STORAGE_DIR": "/data/careeros/documents",
        "CRAWLER_BROWSER_MODE": "auto",
        "CRAWLER_MAX_CONCURRENCY": "2",
        "AGENT_WORKER_EMBEDDED": "false",
        "CRAWLER_STATS_COVERAGE_START": "2020-01-01",
    }
    backend = {
        "name": "careeros-backend-production",
        "services": {
            "backend": {
                "environment": deepcopy(env),
                "volumes": [{"target": "/data/careeros/documents"}],
            },
            "crawler-dispatcher": {
                "environment": deepcopy(env),
                "profiles": ["scheduler"],
                "depends_on": {"backend": {"condition": "service_healthy"}},
            },
            "crawler-stats": {
                "environment": deepcopy(env),
                "profiles": ["analytics"],
                "depends_on": {"backend": {"condition": "service_healthy"}},
            },
        },
    }
    frontend = {
        "services": {
            "frontend": {
                "build": {"args": {"NEXT_PUBLIC_AUTH_ENABLED": "true"}},
                "environment": {
                    "APP_ORIGIN": "https://workspace.example.test",
                    "SUPABASE_URL": env["SUPABASE_URL"],
                    "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture",
                    "NEXT_PUBLIC_AUTH_ENABLED": "true",
                    "BACKEND_INTERNAL_URL": "http://backend:8001",
                },
            }
        },
        "networks": {"backend": {"name": "careeros-backend-production_default"}},
    }
    crawler = {"services": {"crawler-worker": {"scale": 2, "environment": deepcopy(env)}}}
    return [backend, frontend, crawler]


def test_complete_production_config_and_deliberately_fewer_workers(configs):
    preflight.validate(configs, "production")
    configs[2]["services"]["crawler-worker"]["scale"] = 1
    preflight.validate(configs, "production")  # Capacity is a limit, not a replica requirement.


def test_old_production_compose_fails_before_service_startup(configs):
    del configs[0]["services"]["crawler-dispatcher"]
    with pytest.raises(preflight.PreflightError, match="crawler-dispatcher"):
        preflight.validate(configs, "production")


@pytest.mark.parametrize(
    "problem", ["network", "internal_url", "auth_project", "auth_mode", "storage", "database"]
)
def test_cross_repo_mismatch_is_reported(configs, problem):
    if problem == "network":
        configs[1]["networks"]["backend"]["name"] = "careeros-backend-staging_default"
    elif problem == "internal_url":
        configs[1]["services"]["frontend"]["environment"]["BACKEND_INTERNAL_URL"] = (
            "http://127.0.0.1:8001"
        )
    elif problem == "auth_project":
        configs[1]["services"]["frontend"]["environment"]["SUPABASE_URL"] = (
            "https://other.supabase.co"
        )
    elif problem == "auth_mode":
        configs[1]["services"]["frontend"]["build"]["args"]["NEXT_PUBLIC_AUTH_ENABLED"] = "false"
    elif problem == "storage":
        configs[0]["services"]["backend"]["environment"]["DOCUMENT_STORAGE_DIR"] = "/unmounted"
    else:
        configs[2]["services"]["crawler-worker"]["environment"]["DATABASE_URL"] = (
            "postgresql://another-database"
        )
    with pytest.raises(preflight.PreflightError):
        preflight.validate(configs, "production")


@pytest.mark.parametrize("coverage", ["", "invalid-date", "9999-01-01"])
def test_missing_or_invalid_coverage_is_caught_before_publisher_start(configs, coverage):
    configs[0]["services"]["crawler-stats"]["environment"]["CRAWLER_STATS_COVERAGE_START"] = (
        coverage
    )
    with pytest.raises(preflight.PreflightError, match="coverage"):
        preflight.validate(configs, "production")


def test_transaction_pooler_is_rejected_for_session_lock_publisher(configs):
    for service in [*configs[0]["services"].values(), configs[2]["services"]["crawler-worker"]]:
        service["environment"]["DATABASE_URL"] = (
            "postgresql://fixture@pooler.example.test:6543/postgres"
        )
    with pytest.raises(preflight.PreflightError, match="transaction pooler"):
        preflight.validate(configs, "production")


def test_compose_error_does_not_expose_rendered_env_or_stderr(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.production"
    env_file.write_text("DATABASE_URL=fixture\n")
    secret = "sensitive-test-credential"
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=secret, stderr=secret),
    )
    with pytest.raises(preflight.PreflightError) as error:
        preflight.compose_config(tmp_path, "production", env_file)
    assert secret not in str(error.value)


def test_legacy_service_role_key_is_rejected(configs):
    encoded = base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=")
    configs[1]["services"]["frontend"]["environment"]["SUPABASE_PUBLISHABLE_KEY"] = (
        f"eyJ.{encoded}.signature"
    )
    with pytest.raises(preflight.PreflightError, match="anon role"):
        preflight.validate(configs, "production")
