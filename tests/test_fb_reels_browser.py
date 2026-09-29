"""Unit tests for Facebook Reels browser automation and dispatcher."""

import datetime
from pathlib import Path
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auto_yt.services.fb_reels_browser_service import _format_schedule_time
import auto_yt.services.database as db


class TestFbReelsBrowser(unittest.TestCase):
    def test_format_schedule_time_datetime(self):
        dt = datetime.datetime(2026, 9, 29, 19, 0)
        slash, iso, time_str = _format_schedule_time(dt)
        self.assertEqual(slash, "29/9/2026")
        self.assertEqual(iso, "2026-09-29")
        self.assertEqual(time_str, "19:00")

    def test_format_schedule_time_string(self):
        slash, iso, time_str = _format_schedule_time("2026-09-29 19:30")
        self.assertEqual(slash, "29/9/2026")
        self.assertEqual(iso, "2026-09-29")
        self.assertEqual(time_str, "19:30")

    def test_db_fb_crossposter_settings_upload_mode(self):
        test_page_id = "test_page_upload_mode_123"
        saved = db.save_fb_crossposter_settings({
            "target_fb_page_id": test_page_id,
            "target_fb_page_name": "Test Page",
            "upload_mode": "browser",
            "daily_quota": 3,
        }, page_id=test_page_id)

        self.assertEqual(saved["upload_mode"], "browser")

        # Update to api
        updated = db.save_fb_crossposter_settings({
            "target_fb_page_id": test_page_id,
            "upload_mode": "api",
        }, page_id=test_page_id)

        self.assertEqual(updated["upload_mode"], "api")

        # Cleanup
        db.delete_fb_crossposter_campaign(test_page_id)


if __name__ == "__main__":
    unittest.main()
