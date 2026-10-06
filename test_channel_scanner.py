"""Unit and integration tests for Channel Scanner and Local Browser Service."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.main import app
from auto_yt.services import api_security, channel_scanner_service
from auto_yt.services.local_browser_service import (
    list_local_browser_profiles,
)
from auto_yt.services.channel_scanner_service import (
    cleanup_owned_page,
    parse_profile_target,
)


class TestChannelScanner(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        session_res = self.client.get("/api/security/session", headers={"Origin": "http://127.0.0.1:5173"})
        if session_res.status_code == 200:
            csrf_token = session_res.json().get("csrf_token")
            self.client.headers.update({
                "Origin": "http://127.0.0.1:5173",
                api_security.CSRF_HEADER_NAME: csrf_token,
            })

    def test_local_browser_discovery(self):
        """Verify local Chromium browsers are detected and profiles parsed."""
        profiles = list_local_browser_profiles()
        self.assertIsInstance(profiles, list)
        for p in profiles:
            self.assertIn("id", p)
            self.assertIn("browser_name", p)
            self.assertIn("profile_dir", p)
            self.assertIn("display_label", p)
            self.assertEqual(p["type"], "local")

    def test_parse_profile_target(self):
        """Verify parsing of GPM vs Local profile identifiers."""
        local_target = parse_profile_target("local_coccoc_default")
        self.assertEqual(local_target["type"], "local")
        self.assertEqual(local_target["browser_key"], "coccoc")

        gpm_target = parse_profile_target("gpm_uuid_12345")
        self.assertEqual(gpm_target["type"], "gpm")
        self.assertEqual(gpm_target["id"], "gpm_uuid_12345")

    def test_api_local_profiles_endpoint(self):
        """GET /api/channels/local-profiles should return list of local profiles."""
        response = self.client.get("/api/channels/local-profiles")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertIsInstance(data["items"], list)

    @patch(
        "auto_yt.services.channel_scanner_service.find_running_gpm_profile_coordinates",
        return_value={
            "profile_id": "gpm-no-cdp",
            "process_id": 4321,
            "already_running_no_cdp": True,
            "status": "no_cdp",
        },
    )
    @patch(
        "auto_yt.services.channel_scanner_service.get_gpm_profile_detail",
        return_value={"id": "gpm-no-cdp", "name": "No CDP Profile"},
    )
    def test_running_profile_without_cdp_requires_user_action(
        self, _mock_detail, _mock_running
    ):
        readiness = channel_scanner_service.inspect_profile_browser_readiness(
            "gpm-no-cdp"
        )

        self.assertEqual(
            readiness["state"],
            channel_scanner_service.PROFILE_RUNNING_WITHOUT_CDP,
        )
        self.assertTrue(readiness["requires_user_action"])
        with patch.object(channel_scanner_service, "start_gpm_profile") as start_profile:
            with self.assertRaises(channel_scanner_service.BrowserAutomationBlocked):
                channel_scanner_service.ensure_profile_automation_ready("gpm-no-cdp")
        start_profile.assert_not_called()

    @patch("auto_yt.services.channel_scanner_service.inspect_profile_browser_readiness")
    def test_api_profile_automation_status_is_read_only(self, mock_inspect):
        mock_inspect.return_value = {
            "profile_id": "profile-ready",
            "state": "running_cdp_ready",
            "automation_ready": True,
            "requires_user_action": False,
        }

        response = self.client.get(
            "/api/gpm/profiles/profile-ready/automation-status"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["automation_ready"])
        mock_inspect.assert_called_once_with("profile-ready")

    @patch("auto_yt.services.channel_scanner_service.open_channel_platform_browser")
    def test_api_open_browser_endpoint(self, mock_open):
        """POST /api/channels/open-browser should trigger browser opening."""
        mock_open.return_value = {
            "success": True,
            "profile_id": "local_coccoc_default",
            "platform": "facebook",
            "url": "https://www.facebook.com/pages",
            "message": "Đã mở Cốc Cốc",
        }

        response = self.client.post(
            "/api/channels/open-browser",
            json={"profile_id": "local_coccoc_default", "platform": "facebook"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["platform"], "facebook")

    @patch("auto_yt.services.channel_scanner_service.is_profile_browser_busy", return_value=True)
    @patch("auto_yt.services.gpm_service.stop_gpm_profile")
    def test_stop_profile_is_blocked_while_browser_job_is_active(self, mock_stop, _mock_busy):
        response = self.client.post("/api/gpm/profiles/profile-busy/stop")
        self.assertEqual(response.status_code, 409)
        mock_stop.assert_not_called()

    def test_cleanup_owned_page_closes_only_task_tab(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.close = AsyncMock()
        other_page = MagicMock()
        other_page.is_closed.return_value = False
        context = MagicMock()
        context.pages = [other_page, page]

        asyncio.run(cleanup_owned_page(context, page))

        page.close.assert_awaited_once()
        other_page.close.assert_not_called()

    def test_cleanup_owned_page_keeps_last_tab_alive(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.goto = AsyncMock()
        page.close = AsyncMock()
        context = MagicMock()
        context.pages = [page]

        asyncio.run(cleanup_owned_page(context, page))

        page.goto.assert_awaited_once_with("about:blank")
        page.close.assert_not_called()

    @patch("auto_yt.services.channel_scanner_service.scan_youtube_channel")
    def test_api_scan_youtube_endpoint(self, mock_scan):
        """POST /api/channels/scan/youtube should return scanned channel details."""
        mock_scan.return_value = {
            "success": True,
            "logged_in": True,
            "channel": {
                "channel_id": "UC_TEST_123456",
                "title": "Kênh Đánh Giá Công Nghệ",
                "thumbnail_url": "https://yt3.ggpht.com/avatar.jpg",
                "handle": "@review_tech",
                "gpm_profile_id": "local_coccoc_default",
            },
            "message": "Đã quét kênh thành công",
        }

        response = self.client.post(
            "/api/channels/scan/youtube",
            json={"profile_id": "local_coccoc_default", "auto_save": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["channel"]["channel_id"], "UC_TEST_123456")
        self.assertEqual(data["channel"]["title"], "Kênh Đánh Giá Công Nghệ")

    @patch("auto_yt.services.channel_scanner_service.scan_facebook_pages")
    def test_api_scan_facebook_endpoint(self, mock_scan):
        """POST /api/channels/scan/facebook should return discovered fanpages."""
        mock_scan.return_value = {
            "success": True,
            "logged_in": True,
            "pages": [
                {
                    "id": "fb_1029384756",
                    "name": "TechReview Official Page",
                    "page_id": "1029384756",
                    "avatar_url": "https://fb.com/avatar.png",
                    "link": "https://facebook.com/1029384756",
                }
            ],
            "message": "Tìm thấy 1 Fanpage",
        }

        response = self.client.post(
            "/api/channels/scan/facebook",
            json={"profile_id": "local_coccoc_default"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["logged_in"])
        self.assertEqual(len(data["pages"]), 1)
        self.assertEqual(data["pages"][0]["name"], "TechReview Official Page")

    @patch("auto_yt.services.channel_scanner_service.scan_tiktok_account")
    def test_api_scan_tiktok_endpoint(self, mock_scan):
        """POST /api/channels/scan/tiktok should return scanned TikTok account."""
        mock_scan.return_value = {
            "success": True,
            "logged_in": True,
            "account": {
                "id": "tt_techreview_official",
                "name": "Tech Review VN",
                "handle": "@techreview_official",
                "avatar_url": "https://tiktok.com/avatar.jpg",
            },
            "message": "Quét thành công",
        }

        response = self.client.post(
            "/api/channels/scan/tiktok",
            json={"profile_id": "local_coccoc_default"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["account"]["handle"], "@techreview_official")


if __name__ == "__main__":
    unittest.main()
