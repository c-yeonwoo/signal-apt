"""User-facing calendar dates follow the service's Korea-time day boundary."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst(now: datetime | None = None) -> datetime:
    """Return an aware Korean time, independent of the server's timezone."""
    instant = now if now is not None else datetime.now(KST)
    if instant.tzinfo is None:
        raise ValueError("an aware datetime is required")
    return instant.astimezone(KST)


def today_kst(now: datetime | None = None) -> date:
    return now_kst(now).date()
