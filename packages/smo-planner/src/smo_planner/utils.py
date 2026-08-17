"""SMO Planner — Shared utilities.

All planner times are absolute ISO-8601 UTC instants on **SIM TIME** (the
simulator's clock — see ``docs/plan_2026-08c/TIME_ARCHITECTURE.md``). They are
never on-board time and never wall clock.
"""
from __future__ import annotations

from datetime import datetime, timezone

#: Sort key for activities whose time string is missing or unparseable. They
#: sort before every real instant, which is what the old lexical sort on ""
#: did, so ordering of well-formed entries is the only thing that changes.
_UNPARSEABLE_SORT_KEY = datetime.min.replace(tzinfo=timezone.utc)


def parse_iso(s: str) -> datetime | None:
    """Parse an ISO 8601 datetime string to a timezone-aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def iso_sort_key(s: str | None) -> datetime:
    """Chronological sort key for an ISO-8601 time string.

    Sorting ISO strings lexically only works when every string uses the same
    offset spelling. It does not: ``2026-03-11T10:00:00Z``,
    ``2026-03-11T10:00:00+00:00`` and ``2026-03-11T11:00:00+01:00`` are the
    same instant but three different strings, and
    ``2026-03-11T09:00:00-01:00`` (= 10:00 UTC) sorts *after* all of them
    because ``'9' > '1'``. Parse to an aware datetime and compare instants.
    """
    return parse_iso(s or "") or _UNPARSEABLE_SORT_KEY
