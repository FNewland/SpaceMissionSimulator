"""SMO Planner — Shared utilities."""
from __future__ import annotations

from datetime import datetime, timezone


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
