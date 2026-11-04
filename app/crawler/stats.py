"""Run separately: python -m app.crawler.stats --coverage-start YYYY-MM-DD."""

import argparse
import logging
import threading
from datetime import date

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import engine
from app.services.crawler.stats import refresh

log = logging.getLogger(__name__)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coverage-start",
        type=date.fromisoformat,
        required=True,
        help="First WIB day with verified complete, retained source history",
    )
    parser.add_argument("--once", action="store_true", help="Backfill/refresh once and exit")
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be positive")
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    try:
        while True:
            try:
                result = refresh(engine, coverage_start=args.coverage_start)
            except ValueError as exc:
                log.error("Invalid statistics configuration: %s", exc)
                return 2
            except SQLAlchemyError:
                # Connection exceptions can contain credentials; do not log them.
                log.error("Statistics refresh failed; previous publication retained")
                if args.once:
                    return 1
            else:
                if result is None:
                    log.info("Refresh skipped: another publisher is active")
                else:
                    log.info(
                        "Published batch %s: %d owners, %d daily portal buckets",
                        result.batch_id,
                        result.owners,
                        result.buckets,
                    )
                if args.once:
                    return 0
            stop.wait(args.interval_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
