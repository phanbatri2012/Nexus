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
    _schedule_timestamp_matches,
    _detect_schedule_verification,
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

        t_evening = dt.time(19, 0)
        self.assertTrue(_schedule_time_matches("19:00", t_evening))
        self.assertTrue(_schedule_time_matches("7:00 PM", t_evening))
        self.assertTrue(_schedule_time_matches("07:00 PM", t_evening))
        self.assertTrue(_schedule_time_matches("7:00pm", t_evening))
        self.assertFalse(_schedule_time_matches("19:15", t_evening))
        self.assertFalse(_schedule_time_matches("7:00 AM", t_evening))

        t_single_digit = dt.time(8, 30)
        self.assertTrue(_schedule_time_matches("8:30", t_single_digit))
        self.assertTrue(_schedule_time_matches("08:30", t_single_digit))
        self.assertTrue(_schedule_time_matches("8:30 AM", t_single_digit))
        self.assertTrue(_schedule_time_matches("08:30 AM", t_single_digit))

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

    async def test_dismiss_dropdown_safely(self):
        from auto_yt.services.browser_youtube_uploader import _dismiss_dropdown_safely
        page = AsyncMock()
        await _dismiss_dropdown_safely(page)
        page.evaluate.assert_awaited_once()
        # Verify page.keyboard.press was NEVER called
        page.keyboard.press.assert_not_called()

    async def test_ensure_upload_dialog_visible_when_already_open(self):
        from auto_yt.services.browser_youtube_uploader import _ensure_upload_dialog_visible
        page = AsyncMock()
        mock_dialog = AsyncMock()
        mock_dialog.is_visible.return_value = True
        page.query_selector.return_value = mock_dialog

        visible = await _ensure_upload_dialog_visible(page)
        self.assertTrue(visible)

    async def test_ensure_upload_dialog_visible_reopens_minimized(self):
        from auto_yt.services.browser_youtube_uploader import _ensure_upload_dialog_visible
        page = AsyncMock()
        mock_dialog = AsyncMock()
        mock_dialog.is_visible.side_effect = [False, True]
        mock_mini = AsyncMock()
        mock_mini.is_visible.return_value = True

        def query_selector_side_effect(sel):
            if "multi-progress-monitor" in sel or "expand-button" in sel:
                return mock_mini
            return mock_dialog

        page.query_selector.side_effect = query_selector_side_effect

        visible = await _ensure_upload_dialog_visible(page)
        self.assertTrue(visible)
        mock_mini.click.assert_awaited_once()

    def test_detect_schedule_verification_json_timestamp_match(self):
        target_dt = dt.datetime(2026, 10, 6, 18, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        ts = int(target_dt.timestamp())
        mock_html = f'''
        <html>
        <script>
        ytcfg.set({{"scheduledPublishingDetails":{{"scheduledPublishings":[{{"scheduledTimeSeconds":"{ts}","action":"SCHEDULED_PUBLISHING_ACTION_SET_PUBLIC","status":"SCHEDULED_PUBLISHING_STATUS_SCHEDULED"}}]}},"draftStatus":"DRAFT_STATUS_NONE"}});
        </script>
        <body>Chi tiết video - Extension injected content</body>
        </html>
        '''
        # Body text does NOT contain "Đã lên lịch" (e.g. obscured by extensions/shadow dom)
        body_text = "Chi tiết video - Extension injected content"
        is_scheduled, is_matching = _detect_schedule_verification(body_text, mock_html, target_dt)
        self.assertTrue(is_scheduled)
        self.assertTrue(is_matching)

    def test_detect_schedule_verification_body_text_match(self):
        target_dt = dt.datetime(2026, 10, 6, 18, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        body_text = "Chi tiết video. Đã lên lịch 6 thg 10, 2026 lúc 18:00"
        mock_html = "<html><body>Chi tiết video. Đã lên lịch 6 thg 10, 2026 lúc 18:00</body></html>"
        is_scheduled, is_matching = _detect_schedule_verification(body_text, mock_html, target_dt)
        self.assertTrue(is_scheduled)
        self.assertTrue(is_matching)

    def test_detect_schedule_verification_scheduled_but_time_mismatch(self):
        target_dt = dt.datetime(2026, 10, 6, 18, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        diff_dt = dt.datetime(2026, 10, 5, 12, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        ts_diff = int(diff_dt.timestamp())
        mock_html = f'''
        <script>
        ytcfg.set({{"scheduledPublishingDetails":{{"scheduledPublishings":[{{"scheduledTimeSeconds":"{ts_diff}"}}]}},"draftStatus":"DRAFT_STATUS_NONE","status":"SCHEDULED_PUBLISHING_STATUS_SCHEDULED"}});
        </script>
        '''
        body_text = "Chi tiết video"
        is_scheduled, is_matching = _detect_schedule_verification(body_text, mock_html, target_dt)
        self.assertTrue(is_scheduled)
        self.assertFalse(is_matching)

    def test_detect_schedule_verification_draft_status_invalidates_schedule(self):
        target_dt = dt.datetime(2026, 10, 6, 18, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        ts = int(target_dt.timestamp())
        mock_html = f'''
        <script>
        ytcfg.set({{"scheduledPublishingDetails":{{"scheduledPublishings":[{{"scheduledTimeSeconds":"{ts}"}}]}},"draftStatus":"DRAFT_STATUS_DRAFT","status":"SCHEDULED_PUBLISHING_STATUS_SCHEDULED"}});
        </script>
        '''
        body_text = "Chi tiết video"
        is_scheduled, is_matching = _detect_schedule_verification(body_text, mock_html, target_dt)
        self.assertFalse(is_scheduled)
        self.assertFalse(is_matching)

    def test_detect_schedule_verification_interrupted_text_invalidates_schedule(self):
        target_dt = dt.datetime(2026, 10, 6, 18, 0, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        mock_html = '<html><body>Quá trình tải lên bị gián đoạn. Nhấn vào Tiếp tục tải lên...</body></html>'
        body_text = "Quá trình tải lên bị gián đoạn. Nhấn vào Tiếp tục tải lên để tải tệp lên"
        is_scheduled, is_matching = _detect_schedule_verification(body_text, mock_html, target_dt)
        self.assertFalse(is_scheduled)
        self.assertFalse(is_matching)

    def test_matches_playlist_or_podcast_label(self):
        from auto_yt.services.browser_youtube_uploader import _matches_playlist_or_podcast_label
        self.assertTrue(_matches_playlist_or_podcast_label("MC Văn Sâm", "MC Văn Sâm"))
        self.assertTrue(_matches_playlist_or_podcast_label("mc văn sâm", "MC Văn Sâm"))
        self.assertTrue(_matches_playlist_or_podcast_label("MC Văn Sâm Podcast", "MC Văn Sâm"))
        self.assertTrue(_matches_playlist_or_podcast_label("MC Văn Sâm (Podcast)", "MC Văn Sâm"))
        self.assertTrue(_matches_playlist_or_podcast_label("Podcast: MC Văn Sâm", "MC Văn Sâm"))
        self.assertTrue(_matches_playlist_or_podcast_label("MC Văn Sâm\nPodcast", "MC Văn Sâm"))
        self.assertTrue(_matches_playlist_or_podcast_label("MC Văn Sâm - Danh sách phát", "MC Văn Sâm"))
        self.assertFalse(_matches_playlist_or_podcast_label("Khác Hoàn Toàn", "MC Văn Sâm"))
        self.assertFalse(_matches_playlist_or_podcast_label("", "MC Văn Sâm"))
        self.assertFalse(_matches_playlist_or_podcast_label("MC Văn Sâm", ""))

    async def test_select_matching_playlists_and_podcasts_selects_all(self):
        from auto_yt.services.browser_youtube_uploader import _select_matching_playlists_and_podcasts

        cb1 = MagicMock()
        cb1.click = AsyncMock()
        cb1.get_attribute = AsyncMock(side_effect=lambda name: "true" if cb1.click.await_count else "false")
        cb1.is_checked = AsyncMock(return_value=False)
        row1 = MagicMock()
        row1.inner_text = AsyncMock(return_value="MC Văn Sâm")
        row1.query_selector = AsyncMock(return_value=cb1)

        cb2 = MagicMock()
        cb2.click = AsyncMock()
        cb2.get_attribute = AsyncMock(side_effect=lambda name: "true" if cb2.click.await_count else "false")
        cb2.is_checked = AsyncMock(return_value=False)
        row2 = MagicMock()
        row2.inner_text = AsyncMock(return_value="MC Văn Sâm Podcast")
        row2.query_selector = AsyncMock(return_value=cb2)

        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[row1, row2])

        count = await _select_matching_playlists_and_podcasts(
            page,
            container_selector="ytcp-playlist-dialog",
            expected_text="MC Văn Sâm",
        )
        self.assertEqual(count, 2)
        cb1.click.assert_awaited_once()
        cb2.click.assert_awaited_once()

    async def test_select_matching_playlists_and_podcasts_skips_when_not_found(self):
        from auto_yt.services.browser_youtube_uploader import _select_matching_playlists_and_podcasts

        row = MagicMock()
        row.inner_text = AsyncMock(return_value="Danh sách phát khác")
        row.query_selector = AsyncMock(return_value=None)

        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[row])

    async def test_require_fill_self_healing(self):
        from auto_yt.services.browser_youtube_uploader import _require_fill

        page = MagicMock()
        mock_safe_fill = AsyncMock(side_effect=[False, True])
        mock_dismiss = AsyncMock(return_value=True)

        with patch("auto_yt.services.browser_youtube_uploader._safe_fill", mock_safe_fill), \
             patch("auto_yt.services.browser_youtube_uploader._dismiss_active_confirmation_dialogs", mock_dismiss):
            await _require_fill(page, ["#textbox"], "Video Title", "Error msg")

            self.assertEqual(mock_safe_fill.await_count, 2)
            mock_dismiss.assert_awaited_once()

    async def test_dismiss_active_confirmation_dialogs_eval_success(self):
        from auto_yt.services.browser_youtube_uploader import _dismiss_active_confirmation_dialogs

        page = MagicMock()
        page.evaluate = AsyncMock(return_value=True)

        res = await _dismiss_active_confirmation_dialogs(page, timeout_seconds=1.0)
        self.assertTrue(res)
        page.evaluate.assert_awaited()


if __name__ == "__main__":
    unittest.main()


