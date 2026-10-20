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
    _complete_ad_suitability,
    _set_checkbox,
    _wait_for_ad_suitability,
    _wait_for_ad_suitability_saved,
    AD_SUITABILITY_NONE_SELECTORS,
    AD_SUITABILITY_ROOT_SELECTORS,
    AD_SUITABILITY_SUBMIT_SELECTORS,
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

    def test_ad_suitability_selectors_cover_new_and_legacy_dom(self):
        self.assertTrue(
            any("ytpp-self-certification-questionnaire" in selector for selector in AD_SUITABILITY_ROOT_SELECTORS)
        )
        self.assertTrue(
            any("ytcp-self-certification" in selector for selector in AD_SUITABILITY_ROOT_SELECTORS)
        )
        self.assertTrue(
            any("ytcp-checkbox-lit.all-none-checkbox" in selector for selector in AD_SUITABILITY_NONE_SELECTORS)
        )
        self.assertTrue(
            any("ytpp-self-certification-predictor" in selector for selector in AD_SUITABILITY_SUBMIT_SELECTORS)
        )

    async def test_wait_for_ad_suitability_accepts_delayed_new_dom(self):
        page = MagicMock()
        questionnaire = AsyncMock()
        calls = []

        async def find_elements(_page, selector):
            calls.append(selector)
            if len(calls) <= len(AD_SUITABILITY_ROOT_SELECTORS):
                return []
            if "ytpp-self-certification-questionnaire" in selector:
                return [questionnaire]
            return []

        with patch(
            "auto_yt.services.browser_youtube_uploader._visible_upload_dialog_elements",
            side_effect=find_elements,
        ), patch(
            "auto_yt.services.browser_youtube_uploader.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock:
            result = await _wait_for_ad_suitability(page, timeout_seconds=1.0)

        self.assertIs(result, questionnaire)
        sleep_mock.assert_awaited_once_with(0.25)

    async def test_wait_for_ad_suitability_keeps_legacy_dom_fallback(self):
        page = MagicMock()
        questionnaire = AsyncMock()

        async def find_elements(_page, selector):
            if "ytcp-self-certification" in selector:
                return [questionnaire]
            return []

        with patch(
            "auto_yt.services.browser_youtube_uploader._visible_upload_dialog_elements",
            side_effect=find_elements,
        ):
            result = await _wait_for_ad_suitability(page, timeout_seconds=1.0)

        self.assertIs(result, questionnaire)

    async def test_set_checkbox_supports_new_lit_checkbox(self):
        page = MagicMock()
        wrapper = MagicMock()
        wrapper.click = AsyncMock()
        wrapper.get_attribute = AsyncMock(return_value=None)
        nested_checkbox = MagicMock()
        nested_checkbox.get_attribute = AsyncMock(
            side_effect=lambda name: (
                "true" if name == "aria-checked" and wrapper.click.await_count else "false"
            )
        )
        wrapper.query_selector = AsyncMock(return_value=nested_checkbox)

        with patch(
            "auto_yt.services.browser_youtube_uploader._visible_upload_dialog_elements",
            new=AsyncMock(return_value=[wrapper]),
        ), patch(
            "auto_yt.services.browser_youtube_uploader.asyncio.sleep",
            new=AsyncMock(),
        ):
            selected = await _set_checkbox(
                page,
                ["ytpp-self-certification-questionnaire ytcp-checkbox-lit.all-none-checkbox"],
                True,
            )

        self.assertTrue(selected)
        wrapper.click.assert_awaited_once()

    async def test_wait_for_ad_suitability_saved_finds_new_submit_button(self):
        page = MagicMock()
        submit_button = MagicMock()
        submit_button.get_attribute = AsyncMock(
            side_effect=lambda name: "true" if name == "aria-disabled" else None
        )

        async def find_elements(_page, selector):
            if "ytpp-self-certification-predictor" in selector:
                return [submit_button]
            return []

        with patch(
            "auto_yt.services.browser_youtube_uploader._has_visible_element",
            new=AsyncMock(return_value=False),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._visible_upload_dialog_elements",
            side_effect=find_elements,
        ):
            saved = await _wait_for_ad_suitability_saved(page, timeout_seconds=1.0)

        self.assertTrue(saved)

    async def test_complete_ad_suitability_new_dom_emits_verified_without_upload(self):
        page = MagicMock()
        page.set_input_files = AsyncMock()
        questionnaire = MagicMock()
        questionnaire.scroll_into_view_if_needed = AsyncMock()
        progress = MagicMock()
        checkpoint = MagicMock()
        require_click = AsyncMock()

        with patch(
            "auto_yt.services.browser_youtube_uploader._wait_for_ad_suitability",
            new=AsyncMock(return_value=questionnaire),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._set_checkbox",
            new=AsyncMock(return_value=True),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._require_click",
            require_click,
        ), patch(
            "auto_yt.services.browser_youtube_uploader._wait_for_ad_suitability_saved",
            new=AsyncMock(return_value=True),
        ):
            await _complete_ad_suitability(
                page,
                progress=progress,
                persist_checkpoint=checkpoint,
            )

        progress.assert_called_once_with(
            "Đang hoàn tất tự đánh giá quảng cáo...",
            "self_certification",
            70,
        )
        self.assertIn(
            "ytpp-self-certification-predictor ytcp-button#submit-questionnaire-button",
            require_click.await_args.args[1],
        )
        checkpoint.assert_called_once_with(
            "suitability_verified",
            {
                "stage": "suitability_verified",
                "ad_suitability_state": "none_of_the_above",
            },
        )
        page.set_input_files.assert_not_awaited()

    async def test_complete_ad_suitability_missing_or_locked_controls_fail_closed(self):
        page = MagicMock()
        page.set_input_files = AsyncMock()
        progress = MagicMock()
        checkpoint = MagicMock()

        with patch(
            "auto_yt.services.browser_youtube_uploader._wait_for_ad_suitability",
            new=AsyncMock(return_value=None),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._has_visible_element",
            new=AsyncMock(return_value=True),
        ):
            with self.assertRaises(BrowserUploadNeedsReview):
                await _complete_ad_suitability(
                    page,
                    progress=progress,
                    persist_checkpoint=checkpoint,
                )

        progress.assert_not_called()
        checkpoint.assert_not_called()
        page.set_input_files.assert_not_awaited()

        questionnaire = MagicMock()
        questionnaire.scroll_into_view_if_needed = AsyncMock()
        require_click = AsyncMock()
        with patch(
            "auto_yt.services.browser_youtube_uploader._wait_for_ad_suitability",
            new=AsyncMock(return_value=questionnaire),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._set_checkbox",
            new=AsyncMock(return_value=False),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._require_click",
            require_click,
        ):
            with self.assertRaises(BrowserUploadNeedsReview):
                await _complete_ad_suitability(
                    page,
                    progress=progress,
                    persist_checkpoint=checkpoint,
                )

        require_click.assert_not_awaited()
        checkpoint.assert_not_called()
        page.set_input_files.assert_not_awaited()

    async def test_complete_ad_suitability_requires_saved_confirmation(self):
        page = MagicMock()
        questionnaire = MagicMock()
        questionnaire.scroll_into_view_if_needed = AsyncMock()
        checkpoint = MagicMock()

        with patch(
            "auto_yt.services.browser_youtube_uploader._wait_for_ad_suitability",
            new=AsyncMock(return_value=questionnaire),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._set_checkbox",
            new=AsyncMock(return_value=True),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._require_click",
            new=AsyncMock(),
        ), patch(
            "auto_yt.services.browser_youtube_uploader._wait_for_ad_suitability_saved",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(BrowserUploadNeedsReview):
                await _complete_ad_suitability(
                    page,
                    progress=MagicMock(),
                    persist_checkpoint=checkpoint,
                )

        checkpoint.assert_not_called()

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

    def test_classify_file_upload_status_separates_checks_percent(self):
        from auto_yt.services.browser_youtube_uploader import _classify_file_upload_status

        status = _classify_file_upload_status({
            "dialogFound": True,
            "monitorFound": True,
            "uploadText": "Đã lưu ở chế độ riêng tư",
            "checksText": "",
            "progressText": "Đã lưu ở chế độ riêng tư",
            "footerText": "",
            "monitorText": "Đang kiểm tra 20% ... Còn 8 phút",
        })

        self.assertTrue(status["isDone"])
        self.assertEqual(status["uploadPercent"], 100)
        self.assertEqual(status["checksPercent"], 20)

    def test_classify_file_upload_status_keeps_waiting_for_real_upload(self):
        from auto_yt.services.browser_youtube_uploader import _classify_file_upload_status

        status = _classify_file_upload_status({
            "dialogFound": True,
            "monitorFound": True,
            "uploadText": "Đang tải lên 35% ... Còn 10 phút",
            "checksText": "Đang kiểm tra 20% ... Còn 8 phút",
            "progressText": "",
            "footerText": "",
            "monitorText": "Đang tải lên 35% ... Còn 10 phút",
        })

        self.assertFalse(status["isDone"])
        self.assertEqual(status["uploadPercent"], 35)
        self.assertEqual(status["checksPercent"], 20)

    def test_classify_file_upload_status_preserves_interruption_without_dialog(self):
        from auto_yt.services.browser_youtube_uploader import _classify_file_upload_status

        status = _classify_file_upload_status({
            "dialogFound": False,
            "monitorFound": False,
            "bodyText": "Quá trình tải lên bị gián đoạn",
        })

        self.assertFalse(status["isDone"])
        self.assertTrue(status["isInterrupted"])
        self.assertIsNone(status["uploadPercent"])

    async def test_wait_for_file_upload_complete_does_not_wait_for_checks(self):
        from auto_yt.services.browser_youtube_uploader import _wait_for_file_upload_complete

        page = AsyncMock()
        page.evaluate.return_value = {
            "dialogFound": True,
            "monitorFound": True,
            "uploadText": "Đã lưu ở chế độ riêng tư",
            "checksText": "",
            "progressText": "Đã lưu ở chế độ riêng tư",
            "footerText": "",
            "monitorText": "Đang kiểm tra 20% ... Còn 8 phút",
        }
        progress_called = []

        await _wait_for_file_upload_complete(
            page,
            timeout_seconds=0.1,
            progress=lambda msg, stage, pct: progress_called.append((msg, stage, pct)),
        )

        page.evaluate.assert_awaited_once()
        self.assertEqual(progress_called, [
            ("Đã tải xong 100% file video lên YouTube.", "upload_complete", 65),
        ])

    async def test_wait_for_file_upload_complete_dict_success(self):
        from auto_yt.services.browser_youtube_uploader import _wait_for_file_upload_complete
        page = AsyncMock()
        page.evaluate.return_value = {
            "isDone": True,
            "isInterrupted": False,
            "pctVal": 100,
            "cleanText": "Đã tải lên 100%",
        }
        progress_called = []
        def on_prog(msg, st, pct):
            progress_called.append((msg, st, pct))

        await _wait_for_file_upload_complete(page, timeout_seconds=5.0, progress=on_prog)
        self.assertTrue(len(progress_called) > 0)
        self.assertEqual(progress_called[-1][1], "upload_complete")

    async def test_wait_for_file_upload_complete_timeout_raises_error(self):
        from auto_yt.services.browser_youtube_uploader import _wait_for_file_upload_complete, BrowserUploadError
        page = AsyncMock()
        page.evaluate.return_value = {
            "isDone": False,
            "isInterrupted": False,
            "pctVal": 35,
            "cleanText": "Đang tải lên 35% ... Còn 10 phút",
        }
        with self.assertRaises(BrowserUploadError) as ctx:
            await _wait_for_file_upload_complete(page, timeout_seconds=0.1)
        self.assertIn("Hết thời gian chờ", str(ctx.exception))

    async def test_wait_for_file_upload_complete_interrupted_raises_error(self):
        from auto_yt.services.browser_youtube_uploader import _wait_for_file_upload_complete, BrowserUploadError
        page = AsyncMock()
        page.evaluate.return_value = {
            "isDone": False,
            "isInterrupted": True,
            "pctVal": None,
            "cleanText": "Quá trình tải lên bị gián đoạn",
        }
        with self.assertRaises(BrowserUploadError) as ctx:
            await _wait_for_file_upload_complete(page, timeout_seconds=5.0)
        self.assertIn("gián đoạn", str(ctx.exception).lower())

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

    def test_build_upload_metadata_uses_description_template_over_raw_source_description(self):
        from auto_yt.services.youtube_publisher import build_upload_metadata

        video = {
            "title": "Old Source Title",
            "description": "Old Raw Description from Source YouTube Channel",
            "generated_title": "AI Title 2026",
            "generated_script": (
                "### [TIÊU ĐỀ]\nAI Title 2026\n\n"
                "### [MÔ TẢ]\nĐây là mô tả AI tóm tắt câu chuyện.\n\n"
                "### [HASHTAGS]\n#AiStory #VanSam\n\n"
                "### [CHAPTERS]\n00:00 - Mở đầu\n05:00 - Diễn biến\n10:00 - Kết thúc"
            ),
        }
        publishing_settings = {
            "description_template": "{title}\n\n{description}\n\n{chapters}\n\nMIỄN TRỪ TRÁCH NHIỆM\n\n{hashtags}",
            "category_id": "24",
            "made_for_kids": False,
        }

        meta = build_upload_metadata(video, publishing_settings)
        desc = meta["snippet"]["description"]

        self.assertIn("AI Title 2026", desc)
        self.assertIn("Đây là mô tả AI tóm tắt câu chuyện.", desc)
        self.assertIn("00:00 - Mở đầu", desc)
        self.assertIn("MIỄN TRỪ TRÁCH NHIỆM", desc)
        self.assertIn("#AiStory #VanSam", desc)
        self.assertNotIn("Old Raw Description from Source YouTube Channel", desc)

    def test_build_upload_metadata_falls_back_to_raw_description_if_template_yields_empty(self):
        from auto_yt.services.youtube_publisher import build_upload_metadata

        video = {
            "title": "Raw Title",
            "description": "Raw Source Fallback Description",
            "generated_script": "",
        }
        publishing_settings = {
            "description_template": "",
            "category_id": "24",
            "made_for_kids": False,
        }

        meta = build_upload_metadata(video, publishing_settings)
        self.assertEqual(meta["snippet"]["description"], "Raw Source Fallback Description")

    def test_build_upload_metadata_does_not_fallback_to_source_title_for_generated_video(self):
        from auto_yt.services.youtube_publisher import (
            YouTubePublishError,
            build_upload_metadata,
        )

        video = {
            "source_type": "generated",
            "title": "Original source title must not be published",
            "generated_title": "",
            "generated_script": "### [TIÊU ĐỀ]\n" + ("Mô tả dài bị đặt sai vị trí. " * 8),
            "description": "Original source description",
        }

        with self.assertRaisesRegex(YouTubePublishError, "tiêu đề được tạo"):
            build_upload_metadata(video, {"description_template": "{description}"})

    def test_build_upload_metadata_prefers_valid_script_title_for_generated_video(self):
        from auto_yt.services.youtube_publisher import build_upload_metadata

        script_title = "Tiêu Đề Mới Trong Script | MC Văn Sâm"
        description = (
            "Đây là phần mô tả hợp lệ cho video, trình bày rõ nội dung câu chuyện "
            "và thông điệp để người xem hiểu chính xác trước khi theo dõi."
        )
        video = {
            "source_type": "generated",
            "title": "Original source title",
            "generated_title": "Stale generated title",
            "generated_script": (
                f"### [TIÊU ĐỀ]\n{script_title}\n\n"
                f"### [MÔ TẢ]\n{description}"
            ),
        }

        meta = build_upload_metadata(
            video,
            {"description_template": "{title}\n\n{description}"},
        )

        self.assertEqual(meta["snippet"]["title"], script_title)
        self.assertTrue(meta["snippet"]["description"].startswith(script_title))


if __name__ == "__main__":
    unittest.main()
