"""Consistent local-time formatting for the read-only UI."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


MUNICH_TIMEZONE = ZoneInfo("Europe/Berlin")


def parse_datetime(value: object | None) -> datetime | None:
    """Parse an ISO timestamp and return an aware datetime in Munich time."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(MUNICH_TIMEZONE)


def format_local_datetime(
    value: object | None,
    *,
    include_seconds: bool = True,
    fallback: str = "—",
) -> str:
    """Format a stored timestamp as ``DD.MM.YYYY HH:MM[:SS]`` in Munich time."""

    parsed = parse_datetime(value)
    if parsed is None:
        return fallback if value in (None, "") else str(value)
    pattern = "%d.%m.%Y %H:%M:%S" if include_seconds else "%d.%m.%Y %H:%M"
    return parsed.strftime(pattern)


def current_munich_time() -> str:
    """Return the current local clock for the sidebar."""

    return format_local_datetime(datetime.now(timezone.utc))
