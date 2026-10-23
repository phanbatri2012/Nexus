"""Unit tests for Circadian Dayparting and Persona-based Trust Builder scheduling."""

import datetime
import unittest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

from nexus_engine.services import database as db
from nexus_engine.services import trust_builder_scheduler as scheduler
from nexus_engine.services import trust_builder_service as service


class TestTrustCircadianScheduling(unittest.TestCase):
    def test_persona_active_windows(self):
        # Circadian Standard: 07:30 to 23:00
        self.assertFalse(scheduler.is_persona_active_at("circadian_standard", 3, 0))
        self.assertFalse(scheduler.is_persona_active_at("circadian_standard", 7, 0))
        self.assertTrue(scheduler.is_persona_active_at("circadian_standard", 7, 45))
        self.assertTrue(scheduler.is_persona_active_at("circadian_standard", 12, 15))
        self.assertTrue(scheduler.is_persona_active_at("circadian_standard", 20, 30))
        self.assertFalse(scheduler.is_persona_active_at("circadian_standard", 23, 15))

        # Office Worker: 08:00 to 23:00
        self.assertFalse(scheduler.is_persona_active_at("office_worker", 7, 45))
        self.assertTrue(scheduler.is_persona_active_at("office_worker", 8, 15))
        self.assertTrue(scheduler.is_persona_active_at("office_worker", 12, 0))
        self.assertFalse(scheduler.is_persona_active_at("office_worker", 23, 5))

        # Night Owl: 14:00 to 02:00
        self.assertFalse(scheduler.is_persona_active_at("night_owl", 8, 0))
        self.assertFalse(scheduler.is_persona_active_at("night_owl", 13, 30))
        self.assertTrue(scheduler.is_persona_active_at("night_owl", 14, 15))
        self.assertTrue(scheduler.is_persona_active_at("night_owl", 23, 45))
        self.assertTrue(scheduler.is_persona_active_at("night_owl", 1, 30))
        self.assertFalse(scheduler.is_persona_active_at("night_owl", 2, 15))

    def test_circadian_interval_pacing(self):
        # Lunch break interval should be shorter than morning work interval
        morning_intervals = [
            scheduler.calculate_circadian_interval("circadian_standard", 9, 0)
            for _ in range(20)
        ]
        lunch_intervals = [
            scheduler.calculate_circadian_interval("circadian_standard", 12, 0)
            for _ in range(20)
        ]
        prime_intervals = [
            scheduler.calculate_circadian_interval("circadian_standard", 20, 0)
            for _ in range(20)
        ]

        avg_morning = sum(morning_intervals) / len(morning_intervals)
        avg_lunch = sum(lunch_intervals) / len(lunch_intervals)
        avg_prime = sum(prime_intervals) / len(prime_intervals)

        self.assertGreater(avg_morning, avg_lunch)
        self.assertGreater(avg_morning, avg_prime)
        self.assertLess(avg_lunch, 100)
        self.assertLess(avg_prime, 90)

    def test_social_engagement_favorable_times(self):
        # Test VN timezone (UTC+7)
        tz_vn = "Asia/Ho_Chi_Minh"

        # 09:30 AM local -> Office working hour: Comments NOT favorable
        morning_utc = datetime.datetime(2026, 10, 23, 2, 30, tzinfo=datetime.timezone.utc)
        self.assertFalse(
            service.is_engagement_action_favorable("comment", "circadian_standard", tz_vn, morning_utc)
        )
        self.assertFalse(
            service.is_engagement_action_favorable("subscribe", "circadian_standard", tz_vn, morning_utc)
        )
        # Likes are always allowed
        self.assertTrue(
            service.is_engagement_action_favorable("like", "circadian_standard", tz_vn, morning_utc)
        )

        # 12:30 PM local -> Lunch break: Comments ARE favorable
        lunch_utc = datetime.datetime(2026, 10, 23, 5, 30, tzinfo=datetime.timezone.utc)
        self.assertTrue(
            service.is_engagement_action_favorable("comment", "circadian_standard", tz_vn, lunch_utc)
        )

        # 20:30 PM local -> Prime evening: Comments and Subs ARE favorable
        evening_utc = datetime.datetime(2026, 10, 23, 13, 30, tzinfo=datetime.timezone.utc)
        self.assertTrue(
            service.is_engagement_action_favorable("comment", "circadian_standard", tz_vn, evening_utc)
        )
        self.assertTrue(
            service.is_engagement_action_favorable("subscribe", "circadian_standard", tz_vn, evening_utc)
        )

    def test_defer_plan_hops_sleep_window(self):
        # When deferring into sleep hours (e.g. 22:45 + 60 min -> 23:45),
        # _defer_plan should jump directly to the next morning start.
        plan = {
            "id": 99,
            "publication_timezone": "UTC",
            "schedule_persona": "circadian_standard",
        }
        late_night_utc = datetime.datetime(2026, 10, 23, 22, 45, tzinfo=datetime.timezone.utc)

        with patch.object(scheduler, "_utc_now", return_value=late_night_utc), patch.object(
            db, "update_channel_trust_plan"
        ) as mock_update:
            scheduler._defer_plan(99, 60, plan)

            mock_update.assert_called_once()
            called_next_run = mock_update.call_args[1]["next_run_at"]
            called_dt = datetime.datetime.fromisoformat(called_next_run)
            # The next run should be on the next day (Oct 24) between 07:30 and 08:30 UTC
            self.assertEqual(called_dt.date(), datetime.date(2026, 10, 24))
            self.assertGreaterEqual(called_dt.hour, 7)
            self.assertLessEqual(called_dt.hour, 8)


if __name__ == "__main__":
    unittest.main()
