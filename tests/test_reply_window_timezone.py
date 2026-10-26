"""Unit tests for timezone-aware channel reply window calculation."""

import datetime
from zoneinfo import ZoneInfo
from nexus_engine.main import _move_into_channel_reply_window, _next_comment_publish_time
import nexus_engine.main as main


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


def _daily_limit_channel(timezone_name: str) -> dict:
    return {
        "id": 91,
        "publication_timezone": timezone_name,
        "reply_window_start": "00:00",
        "reply_window_end": "23:59",
        "reply_interval_minutes": 1,
        "quarter_hour_reply_limit": 99,
        "hourly_reply_limit": 99,
        "video_half_hour_reply_limit": 99,
        "daily_reply_limit": 1,
        "backlog_daily_reply_limit": 99,
    }


def test_daily_comment_limit_uses_vietnam_calendar_day(monkeypatch):
    now = datetime.datetime(2026, 10, 26, 16, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(
        main.db,
        "list_channel_reply_activity",
        lambda *_args: [
            {
                "replied_at": "2026-10-26T15:30:00+00:00",
                "video_id": 1,
                "comment_published_at": "2026-10-26T15:00:00+00:00",
            }
        ],
    )
    publish_at, reason = _next_comment_publish_time(
        {"video_id": 2, "published_at": now.isoformat()},
        _daily_limit_channel("Asia/Ho_Chi_Minh"),
        now=now,
    )
    assert publish_at == datetime.datetime(
        2026, 10, 26, 17, 0, tzinfo=datetime.timezone.utc
    )
    assert "đủ giới hạn trong ngày" in reason


def test_daily_comment_limit_uses_new_york_day_across_dst(monkeypatch):
    # 04:30 UTC is 23:30 EST on Mar 7; the next local midnight is 05:00 UTC.
    now = datetime.datetime(2026, 3, 8, 4, 30, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(
        main.db,
        "list_channel_reply_activity",
        lambda *_args: [
            {
                "replied_at": "2026-03-07T06:00:00+00:00",
                "video_id": 1,
                "comment_published_at": "2026-03-07T06:00:00+00:00",
            }
        ],
    )
    publish_at, reason = _next_comment_publish_time(
        {"video_id": 2, "published_at": now.isoformat()},
        _daily_limit_channel("America/New_York"),
        now=now,
    )
    assert publish_at == datetime.datetime(
        2026, 3, 8, 5, 0, tzinfo=datetime.timezone.utc
    )
    assert "đủ giới hạn trong ngày" in reason
