from __future__ import annotations

from datetime import UTC, datetime

from fuck_inside_traders.time_utils import ensure_utc


def age_minutes(timestamp: datetime | None, now: datetime | None = None) -> float | None:
    if timestamp is None:
        return None
    now = now or datetime.now(UTC)
    return max(0.0, (ensure_utc(now) - ensure_utc(timestamp)).total_seconds() / 60.0)


def is_stale(
    timestamp: datetime | None,
    fresh_minutes: int,
    now: datetime | None = None,
) -> bool:
    age = age_minutes(timestamp, now)
    return age is None or age > fresh_minutes
