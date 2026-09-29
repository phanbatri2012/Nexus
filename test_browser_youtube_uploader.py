import datetime as dt
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from auto_yt.services import browser_youtube_uploader as uploader


class BrowserYouTubeUploaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_value_prefers_contenteditable_text_over_aria_label(self):
        element = MagicMock()
        element.input_value = AsyncMock(side_effect=RuntimeError("not an input"))
        element.get_attribute = AsyncMock(
            side_effect=lambda name: {
                "value": None,
                "aria-valuetext": None,
                "aria-label": "Tiêu đề (bắt buộc)",
            }.get(name)
        )
        element.inner_text = AsyncMock(return_value="Tiêu đề video đã điền")

        value = await uploader._read_control_value(element)

        self.assertEqual(value, "Tiêu đề video đã điền")

    async def test_blank_new_upload_dialog_is_not_treated_as_saved_draft(self):
        dialog = MagicMock()
        dialog.query_selector = AsyncMock(return_value=None)
        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[dialog])

        result = await uploader._find_visible_upload_details_dialog(page)

        self.assertIsNone(result)

    async def test_details_dialog_uses_visible_editor_when_custom_root_has_no_box(self):
        editor = MagicMock()
        editor.is_visible = AsyncMock(return_value=True)
        dialog = MagicMock()
        dialog.is_visible = AsyncMock(return_value=False)
        dialog.query_selector = AsyncMock(return_value=editor)
        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[dialog])

        result = await uploader._find_visible_upload_details_dialog(page)

        self.assertIs(result, dialog)
        dialog.is_visible.assert_not_awaited()

    async def test_existing_draft_uses_edit_draft_button_without_upload_url(self):
        page = MagicMock()
        page.goto = AsyncMock()
        details_dialog = MagicMock()

        with (
            patch.object(
                uploader,
                "_find_visible_upload_details_dialog",
                AsyncMock(side_effect=[None, details_dialog]),
            ),
            patch.object(uploader, "_safe_click", AsyncMock(return_value=True)) as click,
        ):
            await uploader._open_existing_draft_upload_dialog(page)

        page.goto.assert_not_awaited()
        click.assert_awaited_once()
        self.assertIn("Chỉnh sửa bản nháp", " ".join(click.await_args.args[1]))

    async def test_monetized_wizard_requires_stable_reads(self):
        snapshot = {
            "stepText": [
                "Chi tiết",
                "Kiếm tiền",
                "Các thành phần của video",
                "Kiểm tra",
                "Chế độ hiển thị",
            ],
            "hasMonetization": True,
            "hasElements": True,
            "hasChecks": True,
            "hasVisibility": True,
            "explicitUnavailable": False,
        }
        reader = AsyncMock(side_effect=[snapshot, snapshot])
        with patch.object(uploader, "_read_monetization_snapshot", reader):
            result = await uploader.detect_monetization_capability(
                object(), timeout_seconds=1, poll_seconds=0.001
            )
        self.assertEqual(result.capability, uploader.MonetizationCapability.AVAILABLE)
        self.assertEqual(reader.await_count, 2)

    async def test_non_monetized_complete_wizard_is_unavailable(self):
        snapshot = {
            "stepText": [
                "Chi tiết",
                "Các thành phần của video",
                "Kiểm tra",
                "Chế độ hiển thị",
            ],
            "hasMonetization": False,
            "hasElements": True,
            "hasChecks": True,
            "hasVisibility": True,
            "explicitUnavailable": False,
        }
        with patch.object(
            uploader,
            "_read_monetization_snapshot",
            AsyncMock(side_effect=[snapshot, snapshot]),
        ):
            result = await uploader.detect_monetization_capability(
                object(), timeout_seconds=1, poll_seconds=0.001
            )
        self.assertEqual(result.capability, uploader.MonetizationCapability.UNAVAILABLE)
        self.assertIn("complete_non_monetized_topology", result.evidence)

    async def test_explicit_ineligible_marker_is_unavailable(self):
        snapshot = {
            "stepText": ["Chi tiết"],
            "hasMonetization": False,
            "hasElements": False,
            "hasChecks": False,
            "hasVisibility": False,
            "explicitUnavailable": True,
        }
        with patch.object(
            uploader,
            "_read_monetization_snapshot",
            AsyncMock(side_effect=[snapshot, snapshot]),
        ):
            result = await uploader.detect_monetization_capability(
                object(), timeout_seconds=1, poll_seconds=0.001
            )
        self.assertEqual(result.capability, uploader.MonetizationCapability.UNAVAILABLE)
        self.assertIn("explicit_unavailable", result.evidence)

    async def test_incomplete_or_broken_ui_never_becomes_unavailable(self):
        incomplete = {
            "stepText": ["Chi tiết"],
            "hasMonetization": False,
            "hasElements": False,
            "hasChecks": False,
            "hasVisibility": False,
            "explicitUnavailable": False,
        }
        with patch.object(
            uploader,
            "_read_monetization_snapshot",
            AsyncMock(side_effect=[incomplete, RuntimeError("detached")]),
        ):
            result = await uploader.detect_monetization_capability(
                object(), timeout_seconds=0.01, poll_seconds=0.001
            )
        self.assertEqual(result.capability, uploader.MonetizationCapability.UNKNOWN)

    def test_schedule_uses_channel_timezone(self):
        converted = uploader._parse_schedule_at(
            "2026-09-28T12:00:00+00:00", "Asia/Ho_Chi_Minh"
        )
        self.assertEqual(converted.date(), dt.date(2026, 9, 28))
        self.assertEqual(converted.strftime("%H:%M"), "19:00")

    def test_schedule_readback_matchers_accept_localized_values(self):
        target_date = dt.date(2026, 9, 28)
        target_time = dt.time(19, 0)
        self.assertTrue(uploader._schedule_date_matches("28 thg 9, 2026", target_date))
        self.assertTrue(uploader._schedule_date_matches("Sep 28, 2026", target_date))
        self.assertFalse(uploader._schedule_date_matches("29 thg 9, 2026", target_date))
        self.assertTrue(uploader._schedule_time_matches("19:00", target_time))
        self.assertTrue(uploader._schedule_time_matches("7:00 PM", target_time))
        self.assertFalse(uploader._schedule_time_matches("18:00", target_time))


if __name__ == "__main__":
    unittest.main()
