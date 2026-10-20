import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from auto_yt.services.browser_youtube_uploader import (
    UPLOAD_QUICK_ICON_SELECTORS,
    UPLOAD_CREATE_BUTTON_SELECTORS,
    UPLOAD_CREATE_MENU_ITEM_SELECTORS,
    BrowserUploadError,
)


class TestYouTubeUploadDialogSelectors(unittest.TestCase):
    def test_create_button_selectors_exclude_creator_chat(self):
        """Ensure no selector in UPLOAD_CREATE_BUTTON_SELECTORS or UPLOAD_QUICK_ICON_SELECTORS
        matches the YPP Partner Support Chat button (which previously caused misclicks)."""
        chat_aria_label = "Trò chuyện với Bộ phận hỗ trợ nhà sáng tạo"

        # Check each selector
        for selector in UPLOAD_CREATE_BUTTON_SELECTORS + UPLOAD_QUICK_ICON_SELECTORS:
            # We must never have substring match *='Tạo' or *='Create' that matches 'nhà sáng tạo'
            self.assertNotIn("*='Tạo'", selector)
            self.assertNotIn("*=\"Tạo\"", selector)
            self.assertNotIn("*='Create'", selector)

            # If selector checks aria-label, ensure it does not match chat_aria_label
            if "aria-label=" in selector:
                # Extract expected label from selector, e.g. [aria-label='Tạo']
                for exact_target in ("Tạo", "Create", "Tải video lên", "Upload videos"):
                    if f"aria-label='{exact_target}'" in selector or f'aria-label="{exact_target}"' in selector:
                        self.assertNotEqual(exact_target, chat_aria_label)

    def test_create_button_selectors_contain_exact_targets(self):
        """Ensure exact match selectors exist for both Vietnamese and English Studio UI."""
        self.assertIn("button[aria-label='Tạo']", UPLOAD_CREATE_BUTTON_SELECTORS)
        self.assertIn("button[aria-label='Create']", UPLOAD_CREATE_BUTTON_SELECTORS)
        self.assertIn("ytcp-button.ytcpAppHeaderCreateIcon button", UPLOAD_CREATE_BUTTON_SELECTORS)
        self.assertIn("ytcp-icon-button#upload-icon", UPLOAD_QUICK_ICON_SELECTORS)


class TestYouTubeUploadDialogFlow(unittest.IsolatedAsyncioTestCase):
    async def test_safe_click_with_exact_selectors(self):
        from auto_yt.services.browser_youtube_uploader import _safe_click

        page = AsyncMock()
        mock_element = AsyncMock()
        page.wait_for_selector = AsyncMock(return_value=mock_element)

        with patch("auto_yt.services.browser_youtube_uploader._visible_upload_dialog_elements", new=AsyncMock(return_value=[])):
            result = await _safe_click(page, ["button[aria-label='Tạo']"], timeout_ms=1000)

        self.assertTrue(result)
        mock_element.click.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
