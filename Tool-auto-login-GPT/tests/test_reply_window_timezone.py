"""Unit tests for timezone-aware channel reply window calculation."""

import datetime
from zoneinfo import ZoneInfo
from auto_yt.main import _move_into_channel_reply_window


def test_reply_window_respects_vietnam_timezone():
    # 02:00 UTC is 09:00 in Vietnam (within 08:00 - 22:00)
    utc_time = datetime.datetime(2026, 10, 11, 2, 0, tzinfo=datetime.timezone.utc)
    channel = {
        "publication_timezone": "Asia/Ho_Chi_Minh",
        "reply_window_start": "08:00",
        "reply_window_end": "22:00",
    }
    result = _move_into_channel_reply_window(utc_time, channel)
    assert result == utc_time


def test_reply_window_respects_us_new_york_timezone():
    # 02:00 UTC is 22:00 EDT (Oct 10) in New York, which is at the end of 08:00 - 22:00 window.
    # So it should be shifted to next day's 08:00 EDT (12:00 UTC).
    utc_time = datetime.datetime(2026, 10, 11, 2, 0, tzinfo=datetime.timezone.utc)
    channel = {
        "publication_timezone": "America/New_York",
        "reply_window_start": "08:00",
        "reply_window_end": "22:00",
    }
    result = _move_into_channel_reply_window(utc_time, channel)
    # Expected: Oct 11, 08:00 EDT = Oct 11, 12:00 UTC
    expected = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=datetime.timezone.utc)
    assert result == expected


def test_reply_window_falls_back_to_vietnam_on_empty_timezone():
    # When publication_timezone is empty, fallback to Asia/Ho_Chi_Minh
    utc_time = datetime.datetime(2026, 10, 11, 2, 0, tzinfo=datetime.timezone.utc)
    channel = {
        "publication_timezone": "",
        "reply_window_start": "08:00",
        "reply_window_end": "22:00",
    }
    result = _move_into_channel_reply_window(utc_time, channel)
    assert result == utc_time
