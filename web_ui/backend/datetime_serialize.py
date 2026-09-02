"""Serialize datetimes for JSON so clients parse a single unambiguous instant (UTC, Z)."""

from datetime import datetime, timezone


def datetime_to_api_iso(dt: datetime) -> str:
    """Naive datetimes from PostgreSQL TIMESTAMP are treated as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    s = dt.isoformat()
    if s.endswith("+00:00"):
        return s[:-6] + "Z"
    return s
