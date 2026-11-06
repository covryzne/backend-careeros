import logging
import uuid

from sqlalchemy.exc import OperationalError

from app.crawler.diagnostics import log_failure


def test_database_diagnostic_excludes_exception_sql_parameters_and_credentials(caplog):
    class DriverError(Exception):
        sqlstate = "42P05"

    secret = "sensitive-database-password"
    error = OperationalError(
        f"SQL containing {secret}",
        {"password": secret},
        DriverError(secret),
        connection_invalidated=True,
    )
    run = uuid.uuid4()
    with caplog.at_level(logging.ERROR):
        log_failure(logging.getLogger("crawler-test"), "polling", error, run)
    assert "sqlstate=42P05" in caplog.text
    assert "error_type=OperationalError" in caplog.text
    assert "db_error_type=DriverError" in caplog.text
    assert "connection_invalidated=True" in caplog.text
    assert str(run) in caplog.text
    assert secret not in caplog.text
    assert "SQL containing" not in caplog.text


def test_lease_loss_has_explicit_code_without_generic_exception_text(caplog):
    with caplog.at_level(logging.ERROR):
        log_failure(
            logging.getLogger("crawler-test"),
            "heartbeat",
            RuntimeError("Crawler execution ownership/lease lost"),
        )
    assert "code=WORKER_LEASE_LOST" in caplog.text
    assert "sqlstate=-" in caplog.text


def test_non_database_errors_and_unexpected_sqlstate_do_not_leak_text(caplog):
    error = OperationalError("secret SQL", {}, RuntimeError("secret payload"))
    error.orig.sqlstate = "sensitive arbitrary content"
    with caplog.at_level(logging.ERROR):
        log_failure(logging.getLogger("crawler-test"), "execute", error)
        log_failure(logging.getLogger("crawler-test"), "execute", ValueError("private page body"))
    assert "sqlstate=-" in caplog.text
    assert "sensitive" not in caplog.text
    assert "private page" not in caplog.text
    assert "secret" not in caplog.text
