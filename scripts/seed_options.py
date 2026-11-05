"""Explicit, non-production seed ownership; no implicit local owner in staging."""

import uuid

from sqlalchemy import text

from app.core.config import settings
from app.core.security import LOCAL_OWNER_ID, ActorContext


def add_owner_options(parser):
    parser.add_argument("--owner-id", type=uuid.UUID, help="Existing Supabase Auth user's UUID")
    parser.add_argument(
        "--confirm-staging", action="store_true", help="Confirm staging seed writes"
    )


def seed_actor(args) -> ActorContext:
    if settings.app_env in {"development", "test"}:
        if args.confirm_staging:
            raise SystemExit("--confirm-staging requires APP_ENV=staging")
        return ActorContext(args.owner_id or LOCAL_OWNER_ID)
    if settings.app_env != "staging":
        raise SystemExit("Seeding production is not supported")
    settings.validate_deployment()
    if not args.confirm_staging or not args.owner_id or args.owner_id.int == 0:
        raise SystemExit("Staging requires --confirm-staging and --owner-id <Supabase user UUID>")
    if args.owner_id == LOCAL_OWNER_ID:
        raise SystemExit("Staging cannot seed the development owner")
    return ActorContext(args.owner_id)


def check_staging_user(db, actor):
    if settings.app_env == "staging":
        exists = db.scalar(
            text("SELECT EXISTS (SELECT 1 FROM auth.users WHERE id = :owner)"),
            {"owner": actor.owner_id},
        )
        if not exists:
            raise SystemExit("Seed owner does not exist in this staging project's Supabase Auth")
