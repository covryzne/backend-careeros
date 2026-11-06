"""Log actionable worker failure categories without SQL, parameters or secrets."""

import re


def log_failure(logger, phase, error, run_id=None):
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
        sqlstate = "-"
    code = (
        "WORKER_LEASE_LOST"
        if isinstance(error, RuntimeError)
        and error.args == ("Crawler execution ownership/lease lost",)
        else "-"
    )
    logger.error(
        "Crawler failure phase=%s run_id=%s error_type=%s db_error_type=%s "
        "sqlstate=%s connection_invalidated=%s code=%s",
        phase,
        run_id or "-",
        type(error).__name__,
        type(original).__name__ if original is not None else "-",
        sqlstate,
        bool(getattr(error, "connection_invalidated", False)),
        code,
    )
