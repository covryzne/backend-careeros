"""Shared DBAPI settings for application workers and Alembic."""

from sqlalchemy.engine import URL, make_url


def connection_options(database_url: str | URL) -> dict:
    # Transaction poolers may hand out a different server session per transaction.
    # Psycopg's automatic named statements then fail with 26000 or 42P05.
    # None disables preparation; 0 would prepare immediately and is NOT a fix.
    if make_url(database_url).drivername == "postgresql+psycopg":
        return {"prepare_threshold": None}
    return {}
