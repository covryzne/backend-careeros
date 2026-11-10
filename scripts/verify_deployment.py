"""Read-only cross-repository Compose preflight; never prints resolved credentials.

Run on the host, not inside a container. No database connection, image build,
migration, container creation or deployment is performed.
"""

import argparse
import base64
import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit


class PreflightError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise PreflightError(message)


def repository(root, service):
    choices = [root / f"{service}-careeros", root / f"careeros-{service}"]
    matches = [path for path in choices if path.is_dir()]
    require(len(matches) == 1, f"Expected one {service} checkout under the workspace root")
    return matches[0]


def compose_config(repo, environment, env_file):
    require(env_file.is_file(), f"Missing selected env file for {repo.name}")
    env = {**os.environ, "APP_ENV_FILE": str(env_file.resolve())}
    # The crawler image must embed THIS backend checkout, not another clone.
    if repo.name in {"crawler-careeros", "careeros-crawler"}:
        expected = repository(repo.parent, "backend").resolve()
        if env.get("BACKEND_CONTEXT"):
            selected = Path(env["BACKEND_CONTEXT"])
            if not selected.is_absolute():
                selected = repo / selected
            require(selected.resolve() == expected, "BACKEND_CONTEXT points to another checkout")
        env["BACKEND_CONTEXT"] = str(expected)
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file.resolve()),
        "-f",
        str(repo / f"compose.{environment}.yml"),
        "--profile",
        "scheduler",
        "--profile",
        "analytics",
        "config",
        "--format",
        "json",
    ]
    try:
        result = subprocess.run(
            command, cwd=repo, env=env, capture_output=True, text=True, check=False, timeout=45
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightError(
            "Docker Compose is unavailable or config validation timed out"
        ) from None
    # Do not echo stdout/stderr: rendered Compose can contain private env values.
    require(result.returncode == 0, f"Compose config failed for {repo.name}; check paths and env")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        raise PreflightError(f"Invalid Compose config output for {repo.name}") from None


def validate(configs, environment, examples=False):
    backend, frontend, crawler = configs
    services = backend.get("services", {})
    required = {"backend", "crawler-dispatcher", "crawler-stats"}
    require(
        required <= services.keys(),
        "Backend Compose needs backend, crawler-dispatcher and crawler-stats",
    )
    require("frontend" in frontend.get("services", {}), "Frontend service is missing")
    require("crawler-worker" in crawler.get("services", {}), "Crawler worker service is missing")
    api = services["backend"]["environment"]
    fe = frontend["services"]["frontend"]
    worker = crawler["services"]["crawler-worker"]
    we = worker["environment"]
    fe_env = fe["environment"]
    for service in [*services.values(), worker]:
        values = service.get("environment", {})
        require(
            values.get("APP_ENV") == environment,
            "Service APP_ENV does not match selected environment",
        )
        require(
            str(values.get("ALLOW_LOCAL_IDENTITY", "false")).lower() == "false",
            "Local identity must be disabled",
        )
        require(
            str(values.get("LOCAL_AUTH_ENABLED", "false")).lower() == "false",
            "Local accounts must be disabled",
        )
        require(not values.get("APP_ENV_FILE"), "Remove host APP_ENV_FILE from container env files")
        require(
            values.get("DATABASE_URL") == api.get("DATABASE_URL"),
            "API, dispatcher, stats and worker database targets differ",
        )
    for name, profile in [("crawler-dispatcher", "scheduler"), ("crawler-stats", "analytics")]:
        service = services[name]
        require(profile in service.get("profiles", []), f"Missing {profile} profile")
        require(
            service.get("depends_on", {}).get("backend", {}).get("condition") == "service_healthy",
            f"{name} must wait for backend health",
        )
    require(
        str(we.get("AGENT_WORKER_EMBEDDED")).lower() == "false",
        "Crawler embedded agent must be disabled",
    )
    try:
        capacity = int(we.get("CRAWLER_MAX_CONCURRENCY", 1))
        replicas = int(worker.get("scale", 1))
    except (TypeError, ValueError):
        raise PreflightError("Worker capacity/replicas must be integers") from None
    require(
        1 <= capacity <= 8 and replicas >= 1,
        "Capacity must be 1..8 with at least one worker replica",
    )
    if replicas != capacity:
        print("NOTE: worker count differs from capacity; simultaneous runs are limited by both.")
    require(
        we.get("CRAWLER_BROWSER_MODE") == api.get("CRAWLER_BROWSER_MODE", "headless"),
        "Crawler browser mode differs from backend env",
    )
    require(
        we.get("CRAWLER_BROWSER_MODE") in {"auto", "headed", "headless"},
        "Invalid crawler browser mode",
    )
    expected_network = backend["name"] + "_default"
    require(
        frontend.get("networks", {}).get("backend", {}).get("name") == expected_network,
        "Frontend points to the wrong backend network",
    )
    require(
        fe_env.get("BACKEND_INTERNAL_URL") == "http://backend:8001",
        "Frontend internal backend URL must use the Compose service",
    )
    require(
        str(fe.get("build", {}).get("args", {}).get("NEXT_PUBLIC_AUTH_ENABLED")).lower() == "true",
        "Frontend image must build in Supabase mode",
    )
    require(
        str(fe_env.get("NEXT_PUBLIC_AUTH_ENABLED")).lower() == "true",
        "Frontend runtime Auth mode must match the image",
    )
    require(
        str(fe_env.get("LOCAL_AUTH_ENABLED", "false")).lower() == "false",
        "Frontend local accounts must be disabled",
    )
    require(
        fe_env.get("SUPABASE_URL") == api.get("SUPABASE_URL"),
        "Frontend and backend Supabase Auth projects differ",
    )
    origin = fe_env.get("APP_ORIGIN", "")
    require(_https_origin(origin), "APP_ORIGIN must be an HTTPS origin without a path")
    require(
        _https_origin(api.get("SUPABASE_URL", "")), "SUPABASE_URL must be an HTTPS project origin"
    )
    try:
        origins = json.loads(api.get("ALLOWED_ORIGINS", "[]"))
    except (TypeError, ValueError):
        raise PreflightError("ALLOWED_ORIGINS must be a JSON list") from None
    require(
        isinstance(origins, list) and origin in origins and "*" not in origins,
        "Backend origins must include the exact frontend origin",
    )
    document_mounts = [
        v
        for v in services["backend"].get("volumes", [])
        if v.get("target") == api.get("DOCUMENT_STORAGE_DIR")
    ]
    require(bool(document_mounts), "Document storage path is not mounted persistently")
    if not examples:
        for key in ["DATABASE_URL", "SUPABASE_URL", "PROVIDER_ENCRYPTION_KEY"]:
            require(
                bool(api.get(key)) and "REPLACE_WITH" not in api[key],
                f"Fill the private backend {key}",
            )
        key = fe_env.get("SUPABASE_PUBLISHABLE_KEY", "")
        require(
            bool(key) and "REPLACE_WITH" not in key and not key.startswith("sb_secret_"),
            "Fill a frontend Supabase publishable key",
        )
        require(
            key.startswith("sb_publishable_") or _anon_key(key),
            "Frontend key must be publishable or a legacy anon role key",
        )
        try:
            valid_key = len(base64.urlsafe_b64decode(api["PROVIDER_ENCRYPTION_KEY"])) == 32
        except (ValueError, TypeError):
            valid_key = False
        require(valid_key, "Invalid Fernet encryption key format")
        try:
            db = urlsplit(api["DATABASE_URL"])
            transaction_pooler = db.port == 6543
        except ValueError:
            raise PreflightError("Invalid database URL format") from None
        require(
            not transaction_pooler,
            "Stats publisher needs direct/session connection, not transaction pooler port 6543",
        )
        try:
            coverage = date.fromisoformat(
                services["crawler-stats"]["environment"].get("CRAWLER_STATS_COVERAGE_START", "")
            )
        except (ValueError, TypeError):
            raise PreflightError(
                "Set the retained statistics coverage date in backend env"
            ) from None
        require(
            coverage <= (datetime.now(UTC) + timedelta(hours=7)).date(),
            "Statistics coverage date cannot be in the future",
        )


def _https_origin(value):
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and (parsed.port is None or parsed.port > 0)
        )
    except (ValueError, TypeError):
        return False


def _anon_key(key):
    try:
        part = key.split(".")[1]
        return (
            json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("role")
            == "anon"
        )
    except (ValueError, IndexError, TypeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["staging", "production"], default="production")
    parser.add_argument("--workspace-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--examples",
        action="store_true",
        help="Validate templates/Compose structure only, not private settings",
    )
    args = parser.parse_args(argv)
    try:
        repos = [
            repository(args.workspace_root, name) for name in ["backend", "frontend", "crawler"]
        ]
        suffix = ".example" if args.examples else ""
        backend_env = repos[0] / f".env.{args.environment}{suffix}"
        frontend_env = repos[1] / f".env.{args.environment}{suffix}"
        configs = [
            compose_config(repo, args.environment, backend_env if i != 1 else frontend_env)
            for i, repo in enumerate(repos)
        ]
        validate(configs, args.environment, args.examples)
    except (PreflightError, KeyError, TypeError) as exc:
        message = (
            str(exc)
            if isinstance(exc, PreflightError)
            else "Unexpected Compose structure; check the selected files"
        )
        print(f"FAIL: {message}")
        return 1
    print(
        f"PASS: {args.environment} {'template structure' if args.examples else 'resolved configuration'} across all three repos"
    )
    print("No database, images, containers or remote services were changed.")
    print(
        "Still verify built-image startup, DB migration/coverage, storage permissions and live HTTPS/login/crawl on the VM."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
