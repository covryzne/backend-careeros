"""Enqueue due fixed-WIB schedules. Run continuously, or invoke --once from cron."""

import argparse
import logging
import threading

from app.db.session import SessionLocal
from app.services.crawler.schedules import dispatch

log = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        while True:
            try:
                results = dispatch(SessionLocal)
                log.info("Dispatch checked: %d occurrences processed", len(results))
            except Exception:  # noqa: BLE001 - keep dispatcher alive without logging credentials
                log.error("Dispatch failed; pending occurrences retained for next check")
                if args.once:
                    return 1
            if args.once:
                return 0
            threading.Event().wait(60)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
