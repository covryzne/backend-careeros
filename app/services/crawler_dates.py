"""Conservative date-only/ISO timestamp handling; never confuse discovery with posting."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

JAKARTA = ZoneInfo("Asia/Jakarta")


def posting_date(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})", value):
            return datetime.fromisoformat(value).astimezone(JAKARTA).date()
    except ValueError:
        pass
    # Relative dates, recruiter activity, timezone-less timestamps and malformed dates
    # cannot establish an exact posting date at this boundary.
    return None


def evaluate(run, platform, raw):
    posted = posting_date(raw)
    capability = next(
        (c for c in run.config.get("adapter_capabilities", []) if c["platform"] == platform), {}
    )
    enabled = run.config.get("post_filter_days") and capability.get("date_filter") in {
        "latest_sort_only_no_age_filter",
        "portal_default_order_no_age_filter",
    }
    if not enabled:
        return "portal_filter", posted
    if posted is None:
        return "unknown", None
    instant = (
        run.queued_at if run.queued_at.tzinfo is not None else run.queued_at.replace(tzinfo=UTC)
    )
    latest = instant.astimezone(JAKARTA).date()
    earliest = latest - timedelta(days=run.config["days"] - 1)
    return ("within_range" if earliest <= posted <= latest else "outside_range"), posted
