import datetime as dt
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from zoneinfo import ZoneInfo

from auto_yt.services.browser_youtube_uploader import (
    _format_date_for_picker,
    _format_time_for_picker,
    _parse_schedule_at,
    _schedule_date_matches,
    _schedule_time_matches,
    _classify_monetization_snapshot,
    MonetizationCapability,
    MonetizationDetection,
    BrowserUploadError,
    BrowserUploadNeedsReview,
    _fill_single_element,
    _read_control_value,
    _text_value_matches,
    UPLOAD_TITLE_EDITOR_SELECTORS,
    UPLOAD_DESCRIPTION_EDITOR_SELECTORS,
)


class TestBrowserYouTubeUploader(unittest.IsolatedAsyncioTestCase):
    def test_format_date_for_picker_vietnamese(self):
        d = dt.date(2026, 10, 3)
        formatted = _format_date_for_picker(d, is_vietnamese=True)
        self.assertEqual(formatted, "3 thg 10, 2026")

    def test_format_date_for_picker_english(self):
        d = dt.date(2026, 10, 3)
        formatted = _format_date_for_picker(d, is_vietnamese=False)
        self.assertEqual(formatted, "Oct 03, 2026")

    def test_format_time_for_picker(self):
        t = dt.time(11, 0)
        formatted = _format_time_for_picker(t)
        self.assertEqual(formatted, "11:00")

    def test_parse_schedule_at_utc(self):
        iso = "2026-10-03T04:00:00+00:00"
        tz = "Asia/Ho_Chi_Minh"
        parsed = _parse_schedule_at(iso, tz)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 10)
        self.assertEqual(parsed.day, 3)
        self.assertEqual(parsed.hour, 11)
        self.assertEqual(parsed.minute, 0)

    def test_schedule_date_matches(self):
        d = dt.date(2026, 10, 3)
        self.assertTrue(_schedule_date_matches("3 thg 10, 2026", d))
        self.assertTrue(_schedule_date_matches("03/10/2026", d))
        self.assertTrue(_schedule_date_matches("Oct 3, 2026", d))
        self.assertFalse(_schedule_date_matches("4 thg 10, 2026", d))

    def test_schedule_time_matches(self):
        t = dt.time(11, 0)
        self.assertTrue(_schedule_time_matches("11:00", t))
        self.assertTrue(_schedule_time_matches("11:00 AM", t))
        self.assertFalse(_schedule_time_matches("12:00", t))

    def test_classify_monetization_available(self):
        snap = {
            "hasMonetization": True,
            "stepText": ["Chi tiết", "Kiếm tiền", "Các thành phần", "Kiểm tra", "Chế độ hiển thị"],
        }
        res = _classify_monetization_snapshot(snap)
        self.assertEqual(res.capability, MonetizationCapability.AVAILABLE)

    def test_classify_monetization_unavailable(self):
        snap = {
            "explicitUnavailable": True,
            "stepText": ["Chi tiết", "Các thành phần", "Kiểm tra", "Chế độ hiển thị"],
        }
        res = _classify_monetization_snapshot(snap)
        self.assertEqual(res.capability, MonetizationCapability.UNAVAILABLE)

    def test_classify_monetization_topology_complete(self):
        snap = {
            "hasElements": True,
            "hasChecks": True,
            "hasVisibility": True,
            "stepText": ["Chi tiết", "Các thành phần", "Kiểm tra", "Chế độ hiển thị"],
        }
        res = _classify_monetization_snapshot(snap)
        self.assertEqual(res.capability, MonetizationCapability.UNAVAILABLE)

    async def test_read_control_value_input(self):
        mock_el = AsyncMock()
        mock_el.input_value.return_value = "Hello World"
        val = await _read_control_value(mock_el)
        self.assertEqual(val, "Hello World")

    async def test_read_control_value_div_contenteditable(self):
        mock_el = AsyncMock()
        mock_el.input_value.side_effect = Exception("Not input")
        mock_el.get_attribute.return_value = None
        mock_el.inner_text.return_value = "My Video Title"
        val = await _read_control_value(mock_el)
        self.assertEqual(val, "My Video Title")

    async def test_fill_single_element_fallback(self):
        page = AsyncMock()
        element = AsyncMock()
        element.fill.side_effect = Exception("Fill error on div")
        page.keyboard.insert_text = AsyncMock()

        res = await _fill_single_element(page, element, "Sample Text")
        self.assertTrue(res)
        page.keyboard.insert_text.assert_awaited_once_with("Sample Text")

    async def test_set_datepicker_value_already_matches(self):
        from auto_yt.services.browser_youtube_uploader import _set_datepicker_value
        page = AsyncMock()
        mock_trigger = AsyncMock()
        mock_trigger.input_value.return_value = "3 thg 10, 2026"
        page.query_selector.return_value = mock_trigger

        ok, val = await _set_datepicker_value(page, dt.date(2026, 10, 3))
        self.assertTrue(ok)
        self.assertEqual(val, "3 thg 10, 2026")

    async def test_set_timepicker_value_already_matches(self):
        from auto_yt.services.browser_youtube_uploader import _set_timepicker_value
        page = AsyncMock()
        mock_input = AsyncMock()
        mock_input.is_visible.return_value = True
        mock_input.input_value.return_value = "11:00"
        page.query_selector.return_value = mock_input

        ok, val = await _set_timepicker_value(page, dt.time(11, 0))
        self.assertTrue(ok)
        self.assertEqual(val, "11:00")

    async def test_wait_for_file_upload_complete_immediate(self):
        from auto_yt.services.browser_youtube_uploader import _wait_for_file_upload_complete
        page = AsyncMock()
        page.evaluate.return_value = "Đã hoàn tất quá trình tải lên. Đang xử lý..."
        progress_called = []
        def on_prog(msg, st, pct):
            progress_called.append((msg, st, pct))

        await _wait_for_file_upload_complete(page, timeout_seconds=5.0, progress=on_prog)
        self.assertTrue(len(progress_called) > 0)
        self.assertEqual(progress_called[-1][1], "upload_complete")

    def test_completion_dialog_selectors(self):
        from auto_yt.services.browser_youtube_uploader import UPLOAD_COMPLETION_DIALOG_SELECTOR
        self.assertIn("ytcp-video-share-dialog", UPLOAD_COMPLETION_DIALOG_SELECTOR)
        self.assertIn("Scheduled", UPLOAD_COMPLETION_DIALOG_SELECTOR)
        self.assertIn("Đã lên lịch", UPLOAD_COMPLETION_DIALOG_SELECTOR)


if __name__ == "__main__":
    unittest.main()


