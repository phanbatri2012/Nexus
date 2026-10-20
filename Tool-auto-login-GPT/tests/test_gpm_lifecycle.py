import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from auto_yt.services import database as db
from auto_yt.services.channel_scanner_service import (
    _profile_operation_counts,
    _profile_operation_guard,
    is_profile_busy_or_queued,
)
from auto_yt.services.gpm_service import (
    _active_gpm_profiles,
    _tool_automated_profiles,
    gpm_browser_session,
    start_gpm_profile,
    stop_gpm_profile,
)


class TestGpmLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.temp_dir.name) / "test-gpm.db"
        self.db_patch = patch.object(db, "DB_PATH", self.temp_db_path)
        self.db_patch.start()
        db.init_db()

        _tool_automated_profiles.clear()
        _active_gpm_profiles.clear()

        with _profile_operation_guard:
            _profile_operation_counts.clear()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()
        _tool_automated_profiles.clear()
        _active_gpm_profiles.clear()
        with _profile_operation_guard:
            _profile_operation_counts.clear()

    def test_is_profile_busy_when_lock_contended(self):
        clean_id = "test-prof-1"
        with _profile_operation_guard:
            _profile_operation_counts[clean_id] = 2

        is_busy, reason = is_profile_busy_or_queued(clean_id)
        self.assertTrue(is_busy)
        self.assertIn("đang chờ", reason)

    def test_is_profile_busy_when_job_queued(self):
        clean_id = "test-prof-2"
        channel = db.upsert_youtube_channel(
            channel_id="UC_TEST_QUEUED",
            title="Test Queued Channel",
            gpm_profile_id=clean_id,
        )

        job = db.create_system_job(
            "job-test-queued-1",
            "comment_publish",
            "Publish comment",
            payload={"channel_id": channel["id"]},
        )
        # Ensure status is queued
        db.update_system_job(job["id"], status="queued")

        is_busy, reason = is_profile_busy_or_queued(clean_id)
        self.assertTrue(is_busy)
        self.assertIn("queued", reason)

    def test_is_profile_idle_when_no_jobs_or_contention(self):
        clean_id = "test-prof-3"
        with _profile_operation_guard:
            _profile_operation_counts[clean_id] = 1

        is_busy, reason = is_profile_busy_or_queued(clean_id)
        self.assertFalse(is_busy)
        self.assertEqual(reason, "")

    def test_gpm_browser_session_skips_stop_when_busy(self):
        clean_id = "test-prof-4"

        fake_launch = {
            "already_running": False,
            "remote_debugging_port": 12345,
            "websocket_debugging_url": "ws://127.0.0.1:12345",
        }

        mock_context = MagicMock()
        mock_context.pages = []
        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_playwright = AsyncMock()
        mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)
        mock_playwright.stop = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.start = AsyncMock(return_value=mock_playwright)

        async def run_session():
            with patch("auto_yt.services.gpm_service.start_gpm_profile", return_value=fake_launch), \
                 patch("auto_yt.services.gpm_service.wait_for_cdp_readiness", new_callable=AsyncMock, return_value="ws://127.0.0.1:12345"), \
                 patch("playwright.async_api.async_playwright", return_value=mock_cm), \
                 patch("auto_yt.services.gpm_service.stop_gpm_profile") as mock_stop, \
                 patch("auto_yt.services.channel_scanner_service.is_profile_busy_or_queued", return_value=(True, "Lock contended")):
                async with gpm_browser_session(clean_id) as (ctx, browser):
                    pass
                mock_stop.assert_not_called()

        asyncio.run(run_session())

    def test_gpm_browser_session_stops_when_not_busy(self):
        clean_id = "test-prof-5"

        fake_launch = {
            "already_running": False,
            "remote_debugging_port": 12345,
            "websocket_debugging_url": "ws://127.0.0.1:12345",
        }

        mock_context = MagicMock()
        mock_context.pages = []
        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_playwright = AsyncMock()
        mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)
        mock_playwright.stop = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.start = AsyncMock(return_value=mock_playwright)

        async def run_session():
            with patch("auto_yt.services.gpm_service.start_gpm_profile", return_value=fake_launch), \
                 patch("auto_yt.services.gpm_service.wait_for_cdp_readiness", new_callable=AsyncMock, return_value="ws://127.0.0.1:12345"), \
                 patch("playwright.async_api.async_playwright", return_value=mock_cm), \
                 patch("auto_yt.services.gpm_service.stop_gpm_profile") as mock_stop, \
                 patch("auto_yt.services.channel_scanner_service.is_profile_busy_or_queued", return_value=(False, "")):
                async with gpm_browser_session(clean_id) as (ctx, browser):
                    pass
                mock_stop.assert_called_once_with(clean_id, api_url=None)

        asyncio.run(run_session())

    def test_automated_start_registers_tool_ownership_and_reuses_without_already_running(self):
        clean_id = "test-prof-auto-1"
        fake_api_resp = {
            "success": True,
            "data": {
                "remote_debugging_port": 12345,
                "websocket_debugging_url": "ws://127.0.0.1:12345",
            },
        }

        # 1. Automated start: Tool sends start request
        with patch("auto_yt.services.gpm_service._request_gpm_api", return_value=fake_api_resp), \
             patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=None):
            info = start_gpm_profile(clean_id, manual=False)
            self.assertIn(clean_id, _tool_automated_profiles)
            self.assertEqual(info.get("remote_debugging_port"), 12345)

        # 2. Subsequent call (e.g. inside gpm_browser_session after ensure_profile_automation_ready)
        running_coords = {
            "remote_debugging_port": 12345,
            "websocket_debugging_url": "ws://127.0.0.1:12345",
        }
        with patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=running_coords):
            reuse_info = start_gpm_profile(clean_id, manual=False)
            # Must NOT be marked as already_running so gpm_browser_session will auto-close it
            self.assertFalse(reuse_info.get("already_running"))

        # 3. Stop profile cleans up ownership
        with patch("auto_yt.services.gpm_service._request_gpm_api", return_value={"success": True}):
            stop_gpm_profile(clean_id)
            self.assertNotIn(clean_id, _tool_automated_profiles)

    def test_external_start_detected_as_already_running(self):
        clean_id = "test-prof-ext-1"
        running_coords = {
            "remote_debugging_port": 12345,
            "websocket_debugging_url": "ws://127.0.0.1:12345",
        }
        with patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=running_coords):
            info = start_gpm_profile(clean_id, manual=False)
            # Was NOT opened by tool -> must be marked already_running = True
            self.assertTrue(info.get("already_running"))
            self.assertNotIn(clean_id, _tool_automated_profiles)

    def test_manual_start_revokes_tool_ownership(self):
        clean_id = "test-prof-man-1"
        _tool_automated_profiles.add(clean_id)

        running_coords = {
            "remote_debugging_port": 12345,
            "websocket_debugging_url": "ws://127.0.0.1:12345",
        }
        with patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=running_coords):
            info = start_gpm_profile(clean_id, manual=True)
            # Manual start explicitly revokes tool ownership
            self.assertTrue(info.get("already_running"))
            self.assertNotIn(clean_id, _tool_automated_profiles)

    def test_gpm_browser_session_skips_stop_when_externally_running(self):
        clean_id = "test-prof-ext-session"

        fake_launch = {
            "already_running": True,
            "remote_debugging_port": 12345,
            "websocket_debugging_url": "ws://127.0.0.1:12345",
        }

        mock_context = MagicMock()
        mock_context.pages = []
        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_playwright = AsyncMock()
        mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)
        mock_playwright.stop = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.start = AsyncMock(return_value=mock_playwright)

        async def run_session():
            with patch("auto_yt.services.gpm_service.start_gpm_profile", return_value=fake_launch), \
                 patch("auto_yt.services.gpm_service.wait_for_cdp_readiness", new_callable=AsyncMock, return_value="ws://127.0.0.1:12345"), \
                 patch("playwright.async_api.async_playwright", return_value=mock_cm), \
                 patch("auto_yt.services.gpm_service.stop_gpm_profile") as mock_stop, \
                 patch("auto_yt.services.channel_scanner_service.is_profile_busy_or_queued", return_value=(False, "")):
                async with gpm_browser_session(clean_id) as (ctx, browser):
                    pass
                mock_stop.assert_not_called()

        asyncio.run(run_session())

    def test_stop_gpm_profile_prefers_close_and_fallbacks_to_stop(self):
        clean_id = "test-prof-close-1"
        _tool_automated_profiles.add(clean_id)

        # 1. When /profiles/close succeeds, it should not call /profiles/stop
        with patch("auto_yt.services.gpm_service._request_gpm_api", return_value={"success": True}) as mock_api, \
             patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=None):
            res = stop_gpm_profile(clean_id, verify_delay=0)
            self.assertTrue(res)
            mock_api.assert_called_once_with(f"/profiles/close/{clean_id}", api_url=None, timeout=15.0)
            self.assertNotIn(clean_id, _tool_automated_profiles)

        # 2. When /profiles/close fails, it falls back to /profiles/stop
        _tool_automated_profiles.add(clean_id)
        def mock_api_side_effect(endpoint, **kwargs):
            if "close" in endpoint:
                return {"success": False}
            if "stop" in endpoint:
                return {"success": True}
            return {}

        with patch("auto_yt.services.gpm_service._request_gpm_api", side_effect=mock_api_side_effect) as mock_fallback, \
             patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=None):
            res = stop_gpm_profile(clean_id, verify_delay=0)
            self.assertTrue(res)
            self.assertEqual(mock_fallback.call_count, 2)
            self.assertNotIn(clean_id, _tool_automated_profiles)

    def test_stop_gpm_profile_fail_safe_kills_lingering_process(self):
        clean_id = "test-prof-lingering"
        running_coords = {
            "remote_debugging_port": 12345,
            "process_id": 99999,
        }

        with patch("auto_yt.services.gpm_service._request_gpm_api", return_value={"success": True}), \
             patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=running_coords), \
             patch("auto_yt.services.gpm_service.kill_gpm_profile_processes") as mock_kill:
            stop_gpm_profile(clean_id, verify_delay=0)
            mock_kill.assert_called_once_with(clean_id, api_url=None)


