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


def previous_months(count: int, now: datetime | None = None) -> list[str]:
    """Completed calendar months, newest first, using the Korean month boundary."""
    day = today_kst(now)
    year, month = day.year, day.month
    result = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        result.append(f"{year}{month:02d}")
    return result
