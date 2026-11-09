"""Shared DBAPI settings for application workers and Alembic."""

from sqlalchemy.engine import URL, make_url


def connection_options(database_url: str | URL) -> dict:
    # Transaction poolers may hand out a different server session per transaction.
    # Psycopg's automatic named statements then fail with 26000 or 42P05.
    # None disables preparation; 0 would prepare immediately and is NOT a fix.
    url = make_url(database_url)
    # SQLAlchemy 2.1 also selects psycopg for the plain postgresql:// URL.
    # Match the effective driver, not just one explicit URL spelling.
    if url.get_backend_name() == "postgresql" and url.get_driver_name() == "psycopg":
        return {"prepare_threshold": None}
    return {}
