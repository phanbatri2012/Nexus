import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from auto_yt.services.browser_youtube_uploader import (
    UPLOAD_QUICK_ICON_SELECTORS,
    UPLOAD_CREATE_BUTTON_SELECTORS,
    UPLOAD_CREATE_MENU_ITEM_SELECTORS,
    UPLOAD_MONETIZATION_TRIGGER_SELECTORS,
    AD_SUITABILITY_ROOT_SELECTORS,
    AD_SUITABILITY_NONE_SELECTORS,
    AD_SUITABILITY_SUBMIT_SELECTORS,
    BrowserUploadError,
)


class TestYouTubeUploadDialogSelectors(unittest.TestCase):
    def test_create_button_selectors_exclude_creator_chat(self):
        """Ensure no selector in UPLOAD_CREATE_BUTTON_SELECTORS or UPLOAD_QUICK_ICON_SELECTORS
        matches the YPP Partner Support Chat button (which previously caused misclicks)."""
        chat_aria_label = "Trò chuyện với Bộ phận hỗ trợ nhà sáng tạo"

        for selector in UPLOAD_CREATE_BUTTON_SELECTORS + UPLOAD_QUICK_ICON_SELECTORS:
            self.assertNotIn("*='Tạo'", selector)
            self.assertNotIn("*=\"Tạo\"", selector)
            self.assertNotIn("*='Create'", selector)

            if "aria-label=" in selector:
                for exact_target in ("Tạo", "Create", "Tải video lên", "Upload videos"):
                    if f"aria-label='{exact_target}'" in selector or f'aria-label="{exact_target}"' in selector:
                        self.assertNotEqual(exact_target, chat_aria_label)

    def test_create_button_selectors_contain_exact_targets(self):
        """Ensure exact match selectors exist for both Vietnamese and English Studio UI."""
        self.assertIn("button[aria-label='Tạo']", UPLOAD_CREATE_BUTTON_SELECTORS)
        self.assertIn("button[aria-label='Create']", UPLOAD_CREATE_BUTTON_SELECTORS)
        self.assertIn("ytcp-button.ytcpAppHeaderCreateIcon button", UPLOAD_CREATE_BUTTON_SELECTORS)
        self.assertIn("ytcp-icon-button#upload-icon", UPLOAD_QUICK_ICON_SELECTORS)

    def test_monetization_trigger_selectors_support_lit_and_legacy(self):
        """Ensure monetization dropdown trigger contains both new Lit and legacy Polymer elements."""
        self.assertTrue(any(".m10n-text" in s for s in UPLOAD_MONETIZATION_TRIGGER_SELECTORS))
        self.assertTrue(any(".edit-button" in s for s in UPLOAD_MONETIZATION_TRIGGER_SELECTORS))
        self.assertTrue(any("ytcp-dropdown-trigger" in s for s in UPLOAD_MONETIZATION_TRIGGER_SELECTORS))

    def test_ad_suitability_selectors_cover_new_questionnaire(self):
        """Ensure Ad Suitability questionnaire supports both ytpp-self-certification and legacy ytcp."""
        self.assertTrue(any("ytpp-self-certification-questionnaire" in s for s in AD_SUITABILITY_ROOT_SELECTORS))
        self.assertTrue(any("ytcp-self-certification" in s for s in AD_SUITABILITY_ROOT_SELECTORS))
        self.assertTrue(any("all-none-checkbox" in s for s in AD_SUITABILITY_NONE_SELECTORS))
        self.assertTrue(any("predictor" in s for s in AD_SUITABILITY_SUBMIT_SELECTORS))


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
