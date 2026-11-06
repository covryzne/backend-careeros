"""Native env selection and Docker-style imports, using synthetic configuration."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import config


def fresh_settings():
    # Avoid changing the cached Settings shared by other test modules.
    return config.get_settings.__wrapped__()


def test_explicit_file_resolves_from_repo_and_does_not_merge_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKEND_ROOT", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV_FILE", ".env.development")
    (tmp_path / ".env").write_text("SUPABASE_URL=https://unselected.example.test\n")
    (tmp_path / ".env.development").write_text("DATABASE_URL=sqlite:///selected.db\n")
    settings = fresh_settings()
    assert settings.database_url == "sqlite:///selected.db"
    assert settings.supabase_url is None


def test_process_environment_overrides_selected_file(tmp_path, monkeypatch):
    selected = tmp_path / "private.env"
    selected.write_text("DATABASE_URL=sqlite:///file.db\nAPP_ENV=development\n")
    monkeypatch.setenv("APP_ENV_FILE", str(selected))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///process.db")
    monkeypatch.setenv("APP_ENV", "staging")
    settings = fresh_settings()
    assert settings.database_url == "sqlite:///process.db"
    assert settings.app_env == "staging"


@pytest.mark.parametrize("selected", ["", "missing.env"])
def test_explicit_invalid_file_fails_clearly(tmp_path, monkeypatch, selected):
    monkeypatch.setattr(config, "BACKEND_ROOT", tmp_path)
    monkeypatch.setenv("APP_ENV_FILE", selected)
    with pytest.raises(ValueError, match="APP_ENV_FILE"):
        fresh_settings()


@pytest.mark.parametrize("cwd_name", [".", "app"])
def test_main_selects_file_before_import_without_starting_server(tmp_path, cwd_name):
    root = Path(__file__).resolve().parents[1]
    selected = tmp_path / "development.env"
    selected.write_text("DATABASE_URL=sqlite:///manual.db\nAPP_ENV=development\n")
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("APP_ENV_FILE", None)
    env["APP_ENV"] = "development"
    env["PYTHONPATH"] = str(root)
    code = """
import json, runpy, sys, uvicorn
def inspect(app, **options):
    from app.core.config import settings
    print(json.dumps({'database_url': settings.database_url, **options}))
uvicorn.run = inspect
sys.argv = [sys.argv[1], '--env-file', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(root / "app/main.py"), str(selected)],
        cwd=root / cwd_name, env=env, capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == {
        "database_url": "sqlite:///manual.db", "host": "127.0.0.1", "port": 8001
    }


def test_docker_style_import_uses_injected_environment_without_local_file(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("APP_ENV_FILE", None)
    env.update(DATABASE_URL="sqlite:///injected.db", APP_ENV="staging", PYTHONPATH=str(root))
    # Disabling the default file models the image, which contains no private env files.
    code = """
import sys
sys.argv = ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8001']
from pydantic_settings import BaseSettings
from unittest.mock import patch
with patch('pydantic_settings.main.DotEnvSettingsSource._read_env_files', return_value={}):
    from app.main import app
    from app.core.config import settings
    assert app.title == 'CareerOS API'
    assert settings.app_env == 'staging'
    assert settings.database_url == 'sqlite:///injected.db'
print('Docker import OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "Docker import OK"
