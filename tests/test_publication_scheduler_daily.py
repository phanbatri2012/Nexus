"""Unit tests for daily ('mỗi ngày') publication schedule slots."""

import datetime as dt
import pytest
from nexus_engine.services import publication_scheduler


def test_validate_publication_slots_allows_daily_slot():
    slots = [{"day": -1, "time": "09:30"}]
    validated = publication_scheduler.validate_publication_slots(slots)
    assert validated == [{"day": -1, "time": "09:30"}]


def test_validate_publication_slots_rejects_duplicate_daily_slots():
    slots = [{"day": -1, "time": "09:30"}, {"day": -1, "time": "09:30"}]
    with pytest.raises(ValueError, match="trùng nhau"):
        publication_scheduler.validate_publication_slots(slots)


def test_validate_publication_slots_rejects_daily_and_specific_day_collision():
    # If daily slot at 09:30 is set, Monday at 09:30 is a duplicate
    slots = [{"day": -1, "time": "09:30"}, {"day": 0, "time": "09:30"}]
    with pytest.raises(ValueError, match="trùng nhau"):
        publication_scheduler.validate_publication_slots(slots)


def test_validate_publication_slots_rejects_specific_day_and_daily_collision():
    # If Friday at 15:00 is set first, adding daily slot at 15:00 is also a duplicate
    slots = [{"day": 4, "time": "15:00"}, {"day": -1, "time": "15:00"}]
    with pytest.raises(ValueError, match="trùng nhau"):
        publication_scheduler.validate_publication_slots(slots)


def test_validate_publication_slots_allows_daily_and_specific_day_different_times():
    slots = [{"day": -1, "time": "09:30"}, {"day": 5, "time": "18:00"}]
    validated = publication_scheduler.validate_publication_slots(slots)
    assert validated == [{"day": -1, "time": "09:30"}, {"day": 5, "time": "18:00"}]


def test_validate_publication_slots_rejects_invalid_days():
    with pytest.raises(ValueError, match="không hợp lệ"):
        publication_scheduler.validate_publication_slots([{"day": -2, "time": "09:30"}])

    with pytest.raises(ValueError, match="không hợp lệ"):
        publication_scheduler.validate_publication_slots([{"day": 7, "time": "09:30"}])


def test_find_next_publication_slot_daily_picks_next_available_day():
    # Monday 08:00 Vietnam time (01:00 UTC)
    now = dt.datetime(2026, 9, 14, 1, 0, tzinfo=dt.timezone.utc)
    slots = [{"day": -1, "time": "09:00"}]

    # With lead_minutes=120, earliest is 10:00 VN, so Monday 09:00 VN is passed.
    # Tuesday 09:00 VN (02:00 UTC) should be chosen.
    result = publication_scheduler.find_next_publication_slot(
        timezone_name="Asia/Ho_Chi_Minh",
        slots=slots,
        daily_limit=1,
        lead_minutes=120,
        occupied_utc=[],
        now_utc=now,
    )
    assert result == "2026-09-15T02:00:00+00:00"


def test_find_next_publication_slot_daily_advances_when_occupied():
    now = dt.datetime(2026, 9, 14, 1, 0, tzinfo=dt.timezone.utc)
    slots = [{"day": -1, "time": "09:00"}]

    # Tuesday 09:00 VN is already occupied
    result = publication_scheduler.find_next_publication_slot(
        timezone_name="Asia/Ho_Chi_Minh",
        slots=slots,
        daily_limit=1,
        lead_minutes=120,
        occupied_utc=["2026-09-15T02:00:00+00:00"],
        now_utc=now,
    )
    # Advances to Wednesday 09:00 VN (02:00 UTC)
    assert result == "2026-09-16T02:00:00+00:00"
