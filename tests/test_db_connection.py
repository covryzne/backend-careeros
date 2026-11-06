"""Verify real SQLAlchemy connection wiring without opening a DB connection."""

import pytest
from sqlalchemy import create_engine, engine_from_config

from app.db.connection import connection_options


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://fixture:fixture@localhost:5434/fixture",
        "postgresql+psycopg://fixture:fixture@pooler.example:6543/postgres?sslmode=require",
        "postgresql+psycopg://fixture:fixture@pooler.example:5432/postgres?sslmode=require",
        "postgresql://fixture:fixture@localhost:5434/fixture",
        "postgresql://fixture:fixture@pooler.example:6543/postgres?sslmode=require",
        "postgresql://fixture:fixture@pooler.example:5432/postgres?sslmode=require",
    ],
)
@pytest.mark.parametrize("migration", [False, True])
def test_psycopg_receives_none_not_zero_for_all_connection_modes(url, migration, monkeypatch):
    if migration:
        engine = engine_from_config(
            {"sqlalchemy.url": url}, prefix="sqlalchemy.", connect_args=connection_options(url)
        )
    else:
        engine = create_engine(url, pool_pre_ping=True, connect_args=connection_options(url))
    captured = {}

    class NoNetwork(Exception):
        pass

    def connect(*args, **kwargs):
        captured.update(kwargs)
        raise NoNetwork()

    monkeypatch.setattr(engine.dialect, "connect", connect)
    try:
        with pytest.raises(NoNetwork):
            engine.connect()
        assert "prepare_threshold" in captured
        assert captured["prepare_threshold"] is None
        assert captured["port"] in {5432, 5434, 6543}
        if "sslmode=require" in url:
            assert captured["sslmode"] == "require"
    finally:
        engine.dispose()


def test_other_explicit_postgres_drivers_keep_their_own_options():
    assert connection_options("postgresql+psycopg2://fixture:fixture@localhost/fixture") == {}
    assert connection_options("postgresql+asyncpg://fixture:fixture@localhost/fixture") == {}


def test_sqlite_does_not_receive_postgres_only_parameters():
    assert connection_options("sqlite://") == {}
    engine = create_engine("sqlite://", connect_args=connection_options("sqlite://"))
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        engine.dispose()
