"""Unit tests for the Unified Browser Diagnostics & Blocker Detection Engine."""

import asyncio
import datetime as dt
import json
import os
import shutil
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from unittest.mock import AsyncMock, MagicMock

from auto_yt.services.browser_diagnostics import (


    CATEGORY_CLOUDFLARE,
    CATEGORY_CONTENT_POLICY,
    CATEGORY_RATE_LIMIT_QUOTA,
    CATEGORY_SESSION_EXPIRED,
    CATEGORY_TERMS_CONSENT,
    CATEGORY_VERIFY_2FA,
    _sanitize_dom_html,
    capture_browser_diagnostics_async,
    capture_browser_diagnostics_sync,
    cleanup_old_diagnostics,
    detect_browser_blockers,
)


class TestBlockerDetector(unittest.TestCase):
    def test_detect_cloudflare_turnstile_url(self):
        url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"
        blockers = detect_browser_blockers(url=url, title="Just a moment...", html_text="")
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_CLOUDFLARE)
        self.assertEqual(blockers[0]["action_required"], "manual_captcha_resolution")

    def test_detect_cloudflare_turnstile_html(self):
        html = '<div class="cf-turnstile-wrapper">Please verify you are human to proceed.</div>'
        blockers = detect_browser_blockers(url="https://chatgpt.com", title="ChatGPT", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_CLOUDFLARE)

    def test_detect_session_expired_google(self):
        url = "https://accounts.google.com/signin/v2/identifier"
        blockers = detect_browser_blockers(url=url, title="Đăng nhập - Tài khoản Google", html_text="")
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_SESSION_EXPIRED)
        self.assertEqual(blockers[0]["action_required"], "re_authenticate")

    def test_detect_session_expired_chatgpt(self):
        html = '<div>Phiên đăng nhập ChatGPT đã hết hạn. Vui lòng đăng nhập lại.</div>'
        blockers = detect_browser_blockers(url="https://chatgpt.com", title="ChatGPT", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_SESSION_EXPIRED)

    def test_detect_terms_consent_modal(self):
        html = '<h2>Trước khi bạn tiếp tục sử dụng YouTube</h2><p>Chúng tôi đã cập nhật Điều khoản dịch vụ</p>'
        blockers = detect_browser_blockers(url="https://studio.youtube.com", title="YouTube Studio", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_TERMS_CONSENT)
        self.assertEqual(blockers[0]["action_required"], "accept_terms")

    def test_detect_rate_limit_quota(self):
        html = '<div>Daily upload limit reached. You can upload more videos in 24 hours.</div>'
        blockers = detect_browser_blockers(url="https://studio.youtube.com", title="YouTube Studio", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_RATE_LIMIT_QUOTA)
        self.assertEqual(blockers[0]["action_required"], "rate_limit_backoff")

    def test_detect_chatgpt_free_plan_limit(self):
        html = "<div>You've reached your GPT-4o limit. Try again after 4:30 PM.</div>"
        blockers = detect_browser_blockers(url="https://chatgpt.com", title="ChatGPT", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_RATE_LIMIT_QUOTA)

    def test_detect_content_policy_safety(self):
        html = '<div class="error">Prompt violated safety policies. Generation was blocked.</div>'
        blockers = detect_browser_blockers(url="https://flow.google.com", title="Google Flow", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_CONTENT_POLICY)
        self.assertEqual(blockers[0]["action_required"], "review_prompt_safety")

    def test_detect_2fa_verification(self):
        html = '<div>Verify it\'s you. Check your phone for the notification.</div>'
        blockers = detect_browser_blockers(url="https://accounts.google.com/v3/signin/challenge/pwd", title="Security", html_text=html)
        self.assertTrue(len(blockers) > 0)
        self.assertEqual(blockers[0]["category"], CATEGORY_VERIFY_2FA)
        self.assertEqual(blockers[0]["action_required"], "manual_2fa_verification")

    def test_clean_page_no_blockers(self):
        html = '<html><body><h1>Dashboard</h1><button id="submit">Create Video</button></body></html>'
        blockers = detect_browser_blockers(url="https://studio.youtube.com/channel/123", title="Studio", html_text=html)
        self.assertEqual(len(blockers), 0)


class TestDomSanitization(unittest.TestCase):
    def test_strip_base64_data_uri(self):
        raw_html = '<div><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA..." alt="pic" /></div>'
        sanitized = _sanitize_dom_html(raw_html)
        self.assertIn("data:image/...[TRUNCATED_BASE64]", sanitized)
        self.assertNotIn("iVBORw0KGgo", sanitized)


class TestDiagnosticsCapture(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_diag_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sync_diagnostics_capture(self):
        mock_page = MagicMock()
        mock_page.url = "https://chatgpt.com/c/test-chat"
        mock_page.title.return_value = "ChatGPT Error"
        mock_page.content.return_value = "<html><body><h1>Error</h1><div>You've hit the Free plan limit</div></body></html>"
        
        # Test full_page screenshot fallback
        call_count = 0
        def fake_screenshot(path=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Full page failed")
            if path:
                Path(path).write_bytes(b"dummy_png")
        mock_page.screenshot.side_effect = fake_screenshot



        diag = capture_browser_diagnostics_sync(
            page=mock_page,
            service="chatgpt",
            job_id="job_test_123",
            video_id=99,
            error=TimeoutError("Timed out waiting for #prompt-textarea"),
            action_name="send_prompt",
            logs_dir=self.test_dir,
        )

        self.assertEqual(diag["service"], "chatgpt")
        self.assertEqual(diag["job_id"], "job_test_123")
        self.assertEqual(diag["video_id"], "99")
        self.assertEqual(diag["error_type"], "TimeoutError")
        self.assertTrue(diag["screenshot_captured"])
        self.assertEqual(len(diag["detected_blockers"]), 1)
        self.assertEqual(diag["detected_blockers"][0]["category"], CATEGORY_RATE_LIMIT_QUOTA)

        # Verify files were generated on disk
        meta_files = list(self.test_dir.glob("debug_chatgpt_vid_99_job_test_123_*.json"))
        dom_files = list(self.test_dir.glob("debug_chatgpt_vid_99_job_test_123_*.html"))
        png_files = list(self.test_dir.glob("debug_chatgpt_vid_99_job_test_123_*.png"))

        self.assertEqual(len(meta_files), 1)
        self.assertEqual(len(dom_files), 1)
        self.assertEqual(len(png_files), 1)

        meta_data = json.loads(meta_files[0].read_text(encoding="utf-8"))
        self.assertEqual(meta_data["action_name"], "send_prompt")
        self.assertEqual(meta_data["current_url"], "https://chatgpt.com/c/test-chat")

    def test_async_diagnostics_capture(self):
        async def _run():
            mock_page = MagicMock()
            mock_page.url = "https://challenges.cloudflare.com/cf-turnstile"
            mock_page.title = AsyncMock(return_value="Just a moment...")
            mock_page.content = AsyncMock(return_value="<html><body><div>Verify you are human</div></body></html>")
            mock_page.screenshot = AsyncMock(return_value=None)

            diag = await capture_browser_diagnostics_async(
                page=mock_page,
                service="google_flow",
                job_id="flow_gen_456",
                video_id=101,
                error="UI element missing",
                action_name="wait_for_editor",
                logs_dir=self.test_dir,
            )

            self.assertEqual(diag["service"], "google_flow")
            self.assertEqual(diag["job_id"], "flow_gen_456")
            self.assertEqual(diag["video_id"], "101")
            self.assertTrue(diag["screenshot_captured"])
            self.assertTrue(len(diag["detected_blockers"]) >= 1)
            self.assertEqual(diag["detected_blockers"][0]["category"], CATEGORY_CLOUDFLARE)

        asyncio.run(_run())

    def test_sync_capture_with_dead_page(self):
        diag = capture_browser_diagnostics_sync(
            page=None,
            service="chatgpt",
            error=RuntimeError("Browser process terminated unexpectedly"),
            action_name="navigate",
            logs_dir=self.test_dir,
        )

        self.assertEqual(diag["service"], "chatgpt")
        self.assertFalse(diag["screenshot_captured"])
        self.assertEqual(diag["error_type"], "RuntimeError")
        self.assertIsNone(diag["screenshot_path"])

        meta_files = list(self.test_dir.glob("debug_chatgpt_*.json"))
        self.assertEqual(len(meta_files), 1)


class TestDiagnosticsCleanup(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_diag_cleanup_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cleanup_old_files(self):
        # Create dummy old file (10 days old)
        old_file = self.test_dir / "debug_old_20260101_000000.png"
        old_file.write_text("dummy")
        old_mtime = (dt.datetime.now() - dt.timedelta(days=10)).timestamp()
        os.utime(old_file, (old_mtime, old_mtime))

        # Create dummy recent file
        recent_file = self.test_dir / "debug_new_20260929_120000.png"
        recent_file.write_text("dummy")

        deleted = cleanup_old_diagnostics(logs_dir=self.test_dir, max_age_days=7)
        self.assertEqual(deleted, 1)
        self.assertFalse(old_file.exists())
        self.assertTrue(recent_file.exists())


if __name__ == "__main__":
    unittest.main()
