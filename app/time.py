"""Operational local time conversion; persisted instants remain UTC."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

OPERATIONAL_TIMEZONE = ZoneInfo("Asia/Bishkek")


def scheduled_utc(local_date: date, local_time: time) -> datetime:
    """Interpret entered calendar fields in the operational timezone."""
    if local_time.tzinfo is not None:
        raise ValueError("Provide local time without a timezone")
    return datetime.combine(local_date, local_time, OPERATIONAL_TIMEZONE).astimezone(timezone.utc)


def local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    return value.astimezone(OPERATIONAL_TIMEZONE)


def format_local_datetime(value: datetime) -> str:
    return local_datetime(value).strftime("%d.%m.%Y %H:%M")
