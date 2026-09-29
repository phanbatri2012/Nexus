import datetime as dt
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from auto_yt.services import browser_youtube_uploader as uploader


class BrowserYouTubeUploaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_click_prefers_upload_dialog_control_over_background_duplicate(self):
        dialog_radio = MagicMock()
        dialog_radio.click = AsyncMock()
        dialog_radio.is_visible = AsyncMock(return_value=True)
        dialog = MagicMock()
        dialog.query_selector = AsyncMock(return_value=dialog_radio)
        dialog.query_selector_all = AsyncMock(return_value=[dialog_radio])
        background_radio = MagicMock()
        background_radio.click = AsyncMock()
        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[dialog])
        page.wait_for_selector = AsyncMock(return_value=background_radio)

        clicked = await uploader._safe_click(
            page,
            ["tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"],
        )

        self.assertTrue(clicked)
        dialog_radio.click.assert_awaited_once()
        background_radio.click.assert_not_awaited()

    def test_playlist_selector_supports_current_studio_markup(self):
        self.assertIn(
            "ytcp-uploads-dialog ytcp-video-metadata-playlists ytcp-dropdown-trigger",
            uploader.UPLOAD_PLAYLIST_TRIGGER_SELECTORS,
        )

    def test_completion_dialog_supports_current_scheduled_title(self):
        self.assertIn(
            "ytcp-dialog:has-text('Đã lên lịch cho video')",
            uploader.UPLOAD_COMPLETION_DIALOG_SELECTOR,
        )

    async def test_existing_youtube_category_is_not_selected_again(self):
        category = MagicMock()
        category.scroll_into_view_if_needed = AsyncMock()
        category.inner_text = AsyncMock(return_value="Tin tức và chính trị")
        background_category = MagicMock()
        background_category.inner_text = AsyncMock(return_value="Hướng dẫn và phong cách")
        page = MagicMock()
        page.query_selector = AsyncMock(return_value=background_category)

        with (
            patch.object(
                uploader,
                "_visible_upload_dialog_elements",
                AsyncMock(return_value=[category]),
            ),
            patch.object(uploader, "_safe_click", AsyncMock()) as click,
        ):
            selected = await uploader._select_youtube_category(page, "25")

        self.assertTrue(selected)
        click.assert_not_awaited()
        background_category.inner_text.assert_not_awaited()

    async def test_playlist_selection_reads_row_label_next_to_checkbox(self):
        checkbox = MagicMock()
        checkbox.click = AsyncMock()
        checkbox.get_attribute = AsyncMock(
            side_effect=lambda name: {
                "aria-checked": "true" if checkbox.click.await_count else "false",
                "checked": None,
            }.get(name)
        )
        checkbox.is_checked = AsyncMock(return_value=False)
        row = MagicMock()
        row.inner_text = AsyncMock(return_value="Tự Hào Việt Nam")
        row.query_selector = AsyncMock(return_value=checkbox)
        podcast_row = MagicMock()
        podcast_row.inner_text = AsyncMock(return_value="Tự Hào Việt Nam Podcast")
        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[podcast_row, row])

        await uploader._select_exact_checkbox_by_text(
            page,
            container_selector="ytcp-playlist-dialog",
            expected_text="Tự Hào Việt Nam",
        )

        checkbox.click.assert_awaited_once()
        podcast_row.query_selector.assert_not_called()

    async def test_selected_state_reads_nested_lit_checkbox_control(self):
        nested_control = MagicMock()
        nested_control.get_attribute = AsyncMock(
            side_effect=lambda name: "true" if name == "aria-checked" else None
        )
        host = MagicMock()
        host.query_selector = AsyncMock(return_value=nested_control)
        host.get_attribute = AsyncMock(return_value=None)

        self.assertTrue(await uploader._is_selected(host))

    async def test_selected_state_falls_back_to_saved_page_during_dialog_reload(self):
        dialog = MagicMock()
        title_editor = MagicMock()
        title_editor.is_visible = AsyncMock(return_value=True)
        dialog.query_selector = AsyncMock(return_value=title_editor)
        dialog.query_selector_all = AsyncMock(return_value=[])
        selected_radio = MagicMock()
        selected_radio.query_selector = AsyncMock(return_value=None)
        selected_radio.get_attribute = AsyncMock(
            side_effect=lambda name: "true" if name == "aria-checked" else None
        )
        page = MagicMock()
        page.query_selector_all = AsyncMock(return_value=[dialog])
        page.query_selector = AsyncMock(return_value=selected_radio)

        selected = await uploader._is_any_selected(
            page,
            ["tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"],
        )

        self.assertTrue(selected)

    async def test_altered_content_controls_are_not_toggled_when_already_visible(self):
        page = MagicMock()
        visible_radio = MagicMock()
        visible_radio.is_visible = AsyncMock(return_value=True)
        page.query_selector_all = AsyncMock(return_value=[visible_radio])

        with patch.object(uploader, "_safe_click", AsyncMock()) as click:
            await uploader._ensure_altered_content_controls_visible(page)

        click.assert_not_awaited()

    async def test_altered_content_controls_expand_only_when_hidden(self):
        page = MagicMock()
        hidden_radio = MagicMock()
        hidden_radio.is_visible = AsyncMock(return_value=False)
        visible_radio = MagicMock()
        visible_radio.is_visible = AsyncMock(return_value=True)
        page.query_selector_all = AsyncMock(
            side_effect=(
                [[hidden_radio]] * len(uploader.UPLOAD_ALTERED_CONTENT_SELECTORS)
                + [[visible_radio]]
            )
        )

        with patch.object(
            uploader, "_safe_click", AsyncMock(return_value=True)
        ) as click:
            await uploader._ensure_altered_content_controls_visible(page)

        click.assert_awaited_once_with(
            page, uploader.UPLOAD_ADVANCED_TOGGLE_SELECTORS, timeout_ms=3000
        )

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

    async def test_existing_text_value_is_not_filled_again(self):
        page = MagicMock()
        with (
            patch.object(uploader, "_text_value_matches", AsyncMock(return_value=True)),
            patch.object(uploader, "_require_fill", AsyncMock()) as fill,
        ):
            await uploader._fill_text_if_needed(
                page,
                ["#textbox"],
                "Existing value",
                "fill failed",
                "verify failed",
            )

        fill.assert_not_awaited()

    async def test_changed_text_value_is_filled_and_verified(self):
        page = MagicMock()
        with (
            patch.object(
                uploader,
                "_text_value_matches",
                AsyncMock(side_effect=[False, True]),
            ),
            patch.object(uploader, "_require_fill", AsyncMock()) as fill,
        ):
            await uploader._fill_text_if_needed(
                page,
                ["#textbox"],
                "Updated value",
                "fill failed",
                "verify failed",
                timeout_ms=1234,
            )

        fill.assert_awaited_once_with(
            page,
            ["#textbox"],
            "Updated value",
            "fill failed",
            timeout_ms=1234,
        )

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

    async def test_resumed_draft_reopens_dialog_before_followup_actions(self):
        page = MagicMock()
        with patch.object(
            uploader, "_open_existing_draft_upload_dialog", AsyncMock()
        ) as open_dialog:
            await uploader._ensure_resumed_draft_dialog(page, True)

        open_dialog.assert_awaited_once_with(page)

    async def test_new_upload_does_not_try_to_reopen_a_draft(self):
        page = MagicMock()
        with patch.object(
            uploader, "_open_existing_draft_upload_dialog", AsyncMock()
        ) as open_dialog:
            await uploader._ensure_resumed_draft_dialog(page, False)

        open_dialog.assert_not_awaited()

    async def test_age_restriction_uses_current_expand_button(self):
        page = MagicMock()
        page.query_selector = AsyncMock(return_value=None)
        with (
            patch.object(uploader, "_safe_click", AsyncMock(return_value=True)) as click,
            patch.object(
                uploader,
                "_require_click",
                AsyncMock(side_effect=[None, RuntimeError("stop after age restriction")]),
            ),
            patch.object(uploader, "_require_selected", AsyncMock()),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after age restriction"):
                await uploader._apply_advanced_details_settings(
                    page,
                    {"age_restriction": False},
                    made_for_kids=False,
                    notify_subscribers=True,
                )

        self.assertIn(
            "button.expand-button[aria-controls='age-restriction']",
            click.await_args.args[1],
        )

    async def test_age_restriction_supports_current_restricted_value(self):
        page = MagicMock()
        selected_control = MagicMock()
        selected_control.is_visible = AsyncMock(return_value=True)
        page.query_selector = AsyncMock(return_value=selected_control)
        with (
            patch.object(
                uploader,
                "_require_click",
                AsyncMock(side_effect=[None, RuntimeError("stop after age restriction")]),
            ) as click,
            patch.object(uploader, "_require_selected", AsyncMock()),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after age restriction"):
                await uploader._apply_advanced_details_settings(
                    page,
                    {"age_restriction": True},
                    made_for_kids=False,
                    notify_subscribers=True,
                )

        self.assertIn(
            "tp-yt-paper-radio-button[name='VIDEO_AGE_RESTRICTION_SELF']",
            click.await_args_list[0].args[1],
        )

    async def test_advanced_checkboxes_support_current_studio_markup(self):
        page = MagicMock()
        visible_control = MagicMock()
        visible_control.is_visible = AsyncMock(return_value=True)
        page.query_selector = AsyncMock(return_value=visible_control)
        with (
            patch.object(uploader, "_require_click", AsyncMock()),
            patch.object(uploader, "_require_selected", AsyncMock()),
            patch.object(
                uploader,
                "_require_checkbox_setting",
                AsyncMock(
                    side_effect=[
                        None,
                        None,
                        None,
                        RuntimeError("stop after advanced checkboxes"),
                    ]
                ),
            ) as checkbox_setting,
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after advanced checkboxes"):
                await uploader._apply_advanced_details_settings(
                    page,
                    {"age_restriction": False},
                    made_for_kids=False,
                    notify_subscribers=True,
                )

        self.assertIn(
            "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Cho phép dùng phân cảnh tự động')",
            checkbox_setting.await_args_list[0].kwargs["selectors"],
        )
        self.assertIn(
            "ytcp-checkbox-lit#has-autoplaces-mentioned-checkbox",
            checkbox_setting.await_args_list[1].kwargs["selectors"],
        )
        self.assertIn(
            "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('tự động thêm khái niệm')",
            checkbox_setting.await_args_list[2].kwargs["selectors"],
        )
        self.assertIn(
            "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Cho phép nhúng')",
            checkbox_setting.await_args_list[3].kwargs["selectors"],
        )

    async def test_comments_support_current_studio_dropdown(self):
        page = MagicMock()
        visible_control = MagicMock()
        visible_control.is_visible = AsyncMock(return_value=True)

        async def query_selector(selector):
            if "VIDEO_AGE_RESTRICTION_NONE" in selector:
                return visible_control
            return None

        page.query_selector = AsyncMock(side_effect=query_selector)
        with (
            patch.object(uploader, "_require_click", AsyncMock()),
            patch.object(uploader, "_require_selected", AsyncMock()),
            patch.object(uploader, "_require_checkbox_setting", AsyncMock()),
            patch.object(
                uploader,
                "_select_dropdown_option",
                AsyncMock(
                    side_effect=[
                        None,
                        None,
                        RuntimeError("stop at comments dropdown"),
                    ]
                ),
            ) as select_dropdown,
        ):
            with self.assertRaisesRegex(RuntimeError, "stop at comments dropdown"):
                await uploader._apply_advanced_details_settings(
                    page,
                    {"age_restriction": False},
                    made_for_kids=False,
                    notify_subscribers=True,
                )

        self.assertIn(
            "ytcp-comment-moderation-settings ytcp-select#enablement-state-select ytcp-dropdown-trigger",
            select_dropdown.await_args_list[2].kwargs["trigger_selectors"],
        )

    async def test_remix_policy_supports_current_studio_values(self):
        page = MagicMock()
        visible_control = MagicMock()
        visible_control.is_visible = AsyncMock(return_value=True)

        async def query_selector(selector):
            if "VIDEO_AGE_RESTRICTION_NONE" in selector:
                return visible_control
            return None

        page.query_selector = AsyncMock(side_effect=query_selector)
        with (
            patch.object(uploader, "_require_click", AsyncMock()) as click,
            patch.object(uploader, "_require_selected", AsyncMock()),
            patch.object(uploader, "_require_checkbox_setting", AsyncMock()),
            patch.object(uploader, "_select_dropdown_option", AsyncMock()),
        ):
            await uploader._apply_advanced_details_settings(
                page,
                {"age_restriction": False},
                made_for_kids=False,
                notify_subscribers=True,
            )

        self.assertIn(
            "tp-yt-paper-radio-button[name='REMIX_SOURCE_OPTION_OPT_IN']",
            click.await_args_list[2].args[1],
        )

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

    def test_schedule_timestamp_matcher_accepts_studio_prefetch_data(self):
        target = dt.datetime(
            2026, 9, 30, 19, 0, tzinfo=dt.timezone(dt.timedelta(hours=7))
        )
        markup = '"scheduledTimeSeconds":"1790769600"'

        self.assertTrue(uploader._schedule_timestamp_matches(markup, target))
        self.assertFalse(
            uploader._schedule_timestamp_matches(
                '"scheduledTimeSeconds":"1790773200"', target
            )
        )


if __name__ == "__main__":
    unittest.main()
