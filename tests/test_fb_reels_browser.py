"""Unit tests for Facebook Reels browser automation and dispatcher."""

import datetime
from pathlib import Path
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auto_yt.services.fb_reels_browser_service import (
    _format_schedule_time,
    FbBrowserAutomationError,
)
from auto_yt.services.fb_crossposter_service import (
    reset_fb_crossposter_checkpoint,
    TEMP_DOWNLOAD_DIR,
)
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

    def test_fb_checkpoint_state_machine(self):
        test_page_id = "test_page_checkpoint_999"
        upsert_res = db.upsert_fb_crossposter_queue_items([
            {
                "youtube_id": "test_yt_cp_123",
                "youtube_url": "https://www.youtube.com/watch?v=test_yt_cp_123",
                "original_title": "Test Checkpoint Video",
            }
        ], target_page_id=test_page_id)
        self.assertGreaterEqual(upsert_res["inserted"] + upsert_res["existing"], 1)

        queue_data = db.get_fb_crossposter_queue(target_page_id=test_page_id)
        self.assertTrue(len(queue_data["items"]) > 0)
        item = queue_data["items"][0]
        item_id = item["id"]

        # 1. Update checkpoint to CP3_CDP_READY with paused status
        db.update_fb_checkpoint(
            item_id,
            "CP3_CDP_READY",
            status="checkpoint_paused",
            screenshot_path="data/logs/crossposter/test_ss.png",
            can_resume=True,
            error_message="Cổng debug CDP chưa mở",
        )

        item_after = db.get_fb_crossposter_queue_item(item_id)
        self.assertEqual(item_after["checkpoint_phase"], "CP3_CDP_READY")
        self.assertEqual(item_after["status"], "checkpoint_paused")
        self.assertEqual(item_after["can_resume"], 1)
        self.assertEqual(item_after["checkpoint_screenshot"], "data/logs/crossposter/test_ss.png")
        self.assertIn("CDP", item_after["error_message"])

        # 2. Reset checkpoint
        db.reset_fb_checkpoint(item_id)
        item_reset = db.get_fb_crossposter_queue_item(item_id)
        self.assertEqual(item_reset["checkpoint_phase"], "")
        self.assertEqual(item_reset["status"], "pending")
        self.assertEqual(item_reset["can_resume"], 0)

        # Cleanup
        db.delete_fb_crossposter_queue_item(item_id)
        db.delete_fb_crossposter_campaign(test_page_id)

    def test_browser_automation_error_properties(self):
        err = FbBrowserAutomationError(
            "Session Facebook bị hết hạn",
            phase="CP4_COMPOSER_READY",
            screenshot_path="data/logs/crossposter/fb_login_err.png",
            can_resume=True,
        )
        self.assertEqual(err.phase, "CP4_COMPOSER_READY")
        self.assertEqual(err.screenshot_path, "data/logs/crossposter/fb_login_err.png")
        self.assertTrue(err.can_resume)
        self.assertIn("Session Facebook bị hết hạn", str(err))

    def test_upload_file_via_cdp_missing_file_raises(self):
        from auto_yt.services.fb_reels_browser_service import upload_file_via_cdp
        import asyncio

        async def _test():
            with self.assertRaises(FileNotFoundError):
                await upload_file_via_cdp(None, "non_existent_file_path_xyz_123.mp4")

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
