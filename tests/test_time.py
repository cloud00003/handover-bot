from datetime import date, datetime, time, timezone

import pytest

from app.time import OPERATIONAL_TIMEZONE, format_local_datetime, local_datetime, scheduled_utc


def test_bishkek_schedule_and_display_across_utc_date_boundary():
    instant = scheduled_utc(date(2026, 9, 8), time(0, 15))
    assert OPERATIONAL_TIMEZONE.key == "Asia/Bishkek"
    assert instant == datetime(2026, 9, 7, 18, 15, tzinfo=timezone.utc)
    assert format_local_datetime(instant) == "08.09.2026 00:15"
    assert local_datetime(instant).tzinfo == OPERATIONAL_TIMEZONE


def test_naive_display_timestamp_is_rejected():
    with pytest.raises(ValueError):
        format_local_datetime(datetime(2026, 9, 8, 12))


def test_schedule_requires_local_calendar_time():
    with pytest.raises(ValueError):
        scheduled_utc(date(2026, 9, 8), time(12, tzinfo=timezone.utc))
