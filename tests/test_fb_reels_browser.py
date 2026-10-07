"""Unit tests for Facebook Reels browser automation and dispatcher."""

import datetime
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auto_yt.services.fb_reels_browser_service import (
    THUMBNAIL_DEFAULT_PATTERN,
    THUMBNAIL_SECTION_PATTERN,
    THUMBNAIL_UPLOAD_PATTERN,
    _calendar_week_position,
    _find_thumbnail_upload_trigger,
    _parse_meta_date_value,
    _schedule_values_match,
    _set_scoped_thumbnail_input,
    _thumbnail_upload_verified,
    _upload_optional_thumbnail,
    _format_schedule_time,
    FbBrowserAutomationError,
)
import auto_yt.services.fb_reels_browser_service as fb_browser
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

    def test_parse_meta_localized_date_value(self):
        self.assertEqual(
            _parse_meta_date_value("7 Tháng 10, 2026"),
            datetime.date(2026, 10, 7),
        )
        self.assertEqual(
            _parse_meta_date_value("07/10/2026"),
            datetime.date(2026, 10, 7),
        )

    def test_schedule_values_match_separate_hour_and_minute_controls(self):
        self.assertTrue(
            _schedule_values_match(
                "7 Tháng 10, 2026",
                "19",
                "00",
                datetime.datetime(2026, 10, 7, 19, 0),
            )
        )
        self.assertFalse(
            _schedule_values_match(
                "7 Tháng 10, 2026",
                "12",
                "24",
                datetime.datetime(2026, 10, 7, 19, 0),
            )
        )

    def test_calendar_week_position_uses_sunday_columns(self):
        self.assertEqual(
            _calendar_week_position(
                datetime.date(2026, 10, 7),
                datetime.date(2026, 10, 7),
            ),
            (0, 3),
        )
        self.assertEqual(
            _calendar_week_position(
                datetime.date(2026, 10, 11),
                datetime.date(2026, 10, 7),
            ),
            (1, 0),
        )

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

    def test_post_submission_popup_selectors(self):
        # Verify that common Meta confirmation buttons and dismiss labels are covered
        expected_keywords = ["Lúc khác", "Maybe later", "Not now", "Để sau", "Bỏ qua", "Dismiss"]
        selector_string = (
            'div[role="dialog"] button:has-text("Lúc khác"), div[role="dialog"] div[role="button"]:has-text("Lúc khác"), '
            'button:has-text("Lúc khác"), div[role="button"]:has-text("Lúc khác"), '
            'button:has-text("Maybe later"), div[role="button"]:has-text("Maybe later"), '
            'button:has-text("Not now"), div[role="button"]:has-text("Not now"), '
            'button:has-text("Để sau"), div[role="button"]:has-text("Để sau"), '
            'button:has-text("Bỏ qua"), div[role="button"]:has-text("Bỏ qua"), '
            'button:has-text("Dismiss"), div[role="button"]:has-text("Dismiss")'
        )
        for kw in expected_keywords:
            self.assertIn(f'has-text("{kw}")', selector_string)

    def test_verify_video_attachment_present_none_or_closed_page(self):
        from auto_yt.services.fb_reels_browser_service import _verify_video_attachment_present
        import asyncio

        class ClosedPageMock:
            def is_closed(self):
                return True

        async def _test():
            self.assertFalse(await _verify_video_attachment_present(None))
            self.assertFalse(await _verify_video_attachment_present(ClosedPageMock()))

        asyncio.run(_test())

    def test_dismiss_unwanted_modals_none_or_closed_page(self):
        from auto_yt.services.fb_reels_browser_service import _dismiss_unwanted_modals
        import asyncio

        class ClosedPageMock:
            def is_closed(self):
                return True

        async def _test():
            self.assertFalse(await _dismiss_unwanted_modals(None))
            self.assertFalse(await _dismiss_unwanted_modals(ClosedPageMock()))

        asyncio.run(_test())

    def test_inspect_composer_stage_none_or_closed_page(self):
        from auto_yt.services.fb_reels_browser_service import (
            _inspect_composer_stage,
            STAGE_INVALID_OR_STALE,
            STAGE_SHARE_READY,
            STAGE_EDIT_READY,
            STAGE_CREATE_READY,
        )
        import asyncio

        self.assertEqual(STAGE_SHARE_READY, "STAGE_SHARE_READY")
        self.assertEqual(STAGE_EDIT_READY, "STAGE_EDIT_READY")
        self.assertEqual(STAGE_CREATE_READY, "STAGE_CREATE_READY")
        self.assertEqual(STAGE_INVALID_OR_STALE, "STAGE_INVALID")

        class ClosedPageMock:
            def is_closed(self):
                return True

            @property
            def url(self):
                return "https://business.facebook.com/latest/reels_composer"

        class NonComposerPageMock:
            def is_closed(self):
                return False

            @property
            def url(self):
                return "https://www.facebook.com/login"

        async def _test():
            self.assertEqual(await _inspect_composer_stage(None), STAGE_INVALID_OR_STALE)
            self.assertEqual(await _inspect_composer_stage(ClosedPageMock()), STAGE_INVALID_OR_STALE)
            self.assertEqual(await _inspect_composer_stage(NonComposerPageMock()), STAGE_INVALID_OR_STALE)

        asyncio.run(_test())

    def test_thumbnail_tab_regex_patterns(self):
        self.assertTrue(THUMBNAIL_SECTION_PATTERN.match("Hình thu nhỏ"))
        self.assertTrue(THUMBNAIL_SECTION_PATTERN.match("Thumbnail"))
        self.assertTrue(THUMBNAIL_UPLOAD_PATTERN.match("Tải hình ảnh lên"))
        self.assertTrue(THUMBNAIL_UPLOAD_PATTERN.match("Upload image"))
        self.assertTrue(THUMBNAIL_DEFAULT_PATTERN.match("Chọn hình thu nhỏ gợi ý"))
        self.assertTrue(THUMBNAIL_DEFAULT_PATTERN.match("Choose suggested thumbnail"))
        self.assertFalse(THUMBNAIL_UPLOAD_PATTERN.match("Tải video lên"))
        self.assertFalse(THUMBNAIL_UPLOAD_PATTERN.match("Chọn khung hình từ video"))

    def test_thumbnail_upload_verification_accepts_new_preview_or_file_name(self):
        before = {
            "images": ["https://example.test/default.jpg"],
            "backgrounds": [],
            "fileNames": [],
            "visibleCanvases": 0,
        }
        self.assertTrue(
            _thumbnail_upload_verified(
                before,
                {**before, "fileNames": ["custom.jpg"]},
                "custom.jpg",
                crop_confirmed=False,
            )
        )
        self.assertTrue(
            _thumbnail_upload_verified(
                before,
                {**before, "images": [*before["images"], "blob:custom-preview"]},
                "custom.jpg",
                crop_confirmed=False,
            )
        )
        self.assertFalse(
            _thumbnail_upload_verified(
                before,
                dict(before),
                "custom.jpg",
                crop_confirmed=False,
            )
        )

    def test_thumbnail_trigger_skips_duplicate_tab_and_uses_cta(self):
        class Candidate:
            def __init__(self, text, role="", inside_selector=False):
                self.text = text
                self.role = role
                self.inside_selector = inside_selector

            async def is_visible(self):
                return True

            async def inner_text(self):
                return self.text

            async def get_attribute(self, name):
                if name == "role":
                    return self.role
                if name == "aria-selected":
                    return "true" if self.role == "tab" else None
                return None

            async def evaluate(self, _expression):
                return self.inside_selector

        class Collection:
            def __init__(self, candidates):
                self.candidates = candidates

            def filter(self, **_kwargs):
                return self

            async def count(self):
                return len(self.candidates)

            def nth(self, index):
                return self.candidates[index]

        class Section:
            def __init__(self, candidates):
                self.candidates = candidates

            def locator(self, _selector):
                return Collection(self.candidates)

        tab = Candidate("Tải hình ảnh lên", role="tab", inside_selector=True)
        cta = Candidate("Tải hình ảnh lên")

        async def _test():
            selected = await _find_thumbnail_upload_trigger(Section([tab, cta]))
            self.assertIs(selected, cta)

        import asyncio
        asyncio.run(_test())

    def test_scoped_thumbnail_input_ignores_video_and_accepts_generic(self):
        class FileInput:
            def __init__(self, accept):
                self.accept = accept
                self.files = []

            async def get_attribute(self, name):
                return self.accept if name == "accept" else None

            async def set_input_files(self, path):
                self.files.append(path)

        class Collection:
            def __init__(self, inputs):
                self.inputs = inputs

            async def count(self):
                return len(self.inputs)

            def nth(self, index):
                return self.inputs[index]

        class Section:
            def __init__(self, inputs):
                self.inputs = inputs

            def locator(self, _selector):
                return Collection(self.inputs)

        video_input = FileInput("video/mp4")
        generic_thumbnail_input = FileInput("")

        async def _test():
            selected = await _set_scoped_thumbnail_input(
                Section([video_input, generic_thumbnail_input]),
                Path("custom.jpg"),
            )
            self.assertTrue(selected)
            self.assertEqual(video_input.files, [])
            self.assertEqual(len(generic_thumbnail_input.files), 1)

        import asyncio
        asyncio.run(_test())

    def test_optional_thumbnail_failure_restores_default_without_raising(self):
        section = object()
        upload_tab = AsyncMock()
        upload_tab.click = AsyncMock()
        primary_error = RuntimeError("image input not found")

        async def _test():
            with (
                patch.object(fb_browser, "_find_thumbnail_section", AsyncMock(return_value=section)),
                patch.object(fb_browser, "_thumbnail_preview_signature", AsyncMock(return_value={})),
                patch.object(fb_browser, "_visible_exact_text_candidates", AsyncMock(return_value=[upload_tab])),
                patch.object(fb_browser, "_find_thumbnail_upload_trigger", AsyncMock(return_value=None)),
                patch.object(
                    fb_browser,
                    "_set_scoped_thumbnail_input",
                    AsyncMock(side_effect=primary_error),
                ),
                patch.object(fb_browser, "_restore_default_thumbnail", AsyncMock(return_value=True)),
                patch.object(fb_browser.asyncio, "sleep", AsyncMock()),
            ):
                result = await _upload_optional_thumbnail(None, Path("custom.jpg"))
            self.assertEqual(result["status"], "fallback_default")
            self.assertTrue(result["fallback_restored"])
            self.assertIn("image input not found", result["warning"])

        import asyncio
        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
