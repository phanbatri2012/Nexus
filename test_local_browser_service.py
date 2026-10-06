import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.services import local_browser_service


class LocalBrowserServiceTests(unittest.TestCase):
    @patch("auto_yt.services.local_browser_service.find_free_port", return_value=9222)
    @patch("auto_yt.services.local_browser_service.find_running_local_browser_process", return_value=None)
    @patch("auto_yt.services.local_browser_service.resolve_browser_paths")
    @patch("subprocess.Popen")
    @patch("urllib.request.urlopen")
    def test_closed_browser_launches_once_then_opens_new_tab(
        self,
        mock_urlopen,
        mock_popen,
        mock_paths,
        _mock_running_process,
        _mock_free_port,
    ):
        mock_paths.return_value = (
            Path(r"C:\Program Files\CocCoc\browser.exe"),
            Path(r"C:\User Data"),
        )
        version_response = MagicMock()
        version_response.read.return_value = (
            b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/test"}'
        )
        new_tab_response = MagicMock()
        mock_urlopen.return_value.__enter__.side_effect = [
            version_response,
            new_tab_response,
        ]

        result = local_browser_service.start_local_browser(
            "coccoc",
            "Default",
            target_url="https://studio.youtube.com",
        )

        self.assertTrue(result["success"])
        launch_args = mock_popen.call_args.args[0]
        self.assertNotIn("https://studio.youtube.com", launch_args)
        requested_urls = [
            call.args[0].full_url if hasattr(call.args[0], "full_url") else str(call.args[0])
            for call in mock_urlopen.call_args_list
        ]
        self.assertTrue(any("/json/new?" in url for url in requested_urls))

    @patch("auto_yt.services.local_browser_service.find_running_local_browser_port", return_value=None)
    @patch("auto_yt.services.local_browser_service.resolve_browser_paths")
    @patch("subprocess.run")
    def test_terminate_local_browser_targets_selected_profile_pid(
        self, mock_run, mock_paths, _mock_find_port
    ):
        mock_paths.return_value = (
            Path(r"C:\Program Files\CocCoc\browser.exe"),
            Path(r"C:\User Data"),
        )
        process_query = MagicMock()
        process_query.stdout = (
            '[{"ProcessId":1234,"CommandLine":"browser.exe '
            '--user-data-dir=\\"C:\\\\User Data\\" '
            '--profile-directory=\\"Profile 1\\""}]'
        )
        mock_run.side_effect = [process_query, MagicMock()]

        result = local_browser_service.terminate_local_browser_processes(
            "coccoc", "Profile 1"
        )

        self.assertTrue(result)
        taskkill_args = mock_run.call_args_list[1].args[0]
        self.assertIn("/PID", taskkill_args)
        self.assertIn("1234", taskkill_args)
        self.assertNotIn("/IM", taskkill_args)

    @patch("auto_yt.services.local_browser_service.terminate_local_browser_processes")
    @patch("auto_yt.services.local_browser_service.find_running_local_browser_process")
    @patch("auto_yt.services.local_browser_service.resolve_browser_paths")
    @patch("subprocess.Popen")
    @patch("urllib.request.urlopen")
    def test_start_local_browser_when_running_no_cdp_never_restarts_browser(
        self, mock_urlopen, mock_popen, mock_paths, mock_running_process, mock_terminate
    ):
        mock_paths.return_value = (Path(r"C:\Program Files\CocCoc\browser.exe"), Path(r"C:\User Data"))
        mock_running_process.return_value = {
            "process_id": 1234,
            "browser_key": "coccoc",
            "profile_dir": "Default",
            "cdp_ready": False,
        }
        mock_terminate.return_value = True

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        # When require_cdp=False (default for opening browser/tab), should open tab without killing
        res = local_browser_service.start_local_browser("coccoc", "Default", target_url="https://facebook.com", require_cdp=False)
        self.assertTrue(res["success"])
        self.assertTrue(res["already_running_no_cdp"])
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertIn("https://facebook.com", args)
        mock_terminate.assert_not_called()

        # Automation requests preserve the running non-CDP browser without opening a tab.
        res_cdp = local_browser_service.start_local_browser(
            "coccoc", "Default", target_url="https://facebook.com", require_cdp=True
        )
        self.assertTrue(res_cdp["success"])
        self.assertTrue(res_cdp["already_running_no_cdp"])
        mock_popen.assert_called_once()
        mock_terminate.assert_not_called()

    @patch("auto_yt.services.local_browser_service.sys.platform", "win32")
    @patch("auto_yt.services.local_browser_service.resolve_browser_paths")
    @patch("auto_yt.services.local_browser_service.subprocess.run")
    def test_running_process_detection_is_scoped_to_exact_profile(
        self, mock_run, mock_paths
    ):
        mock_paths.return_value = (
            Path(r"C:\Program Files\Google\Chrome\chrome.exe"),
            Path(r"C:\Chrome Data"),
        )
        process_query = MagicMock()
        process_query.stdout = (
            '[{"ProcessId":111,"CommandLine":"chrome.exe '
            '--user-data-dir=\\"C:\\\\Chrome Data\\" '
            '--profile-directory=\\"Profile 1\\""},'
            '{"ProcessId":222,"CommandLine":"chrome.exe '
            '--user-data-dir=\\"C:\\\\Chrome Data\\" '
            '--profile-directory=\\"Profile 2\\""}]'
        )
        mock_run.return_value = process_query

        result = local_browser_service.find_running_local_browser_process(
            "chrome", "Profile 2"
        )

        self.assertEqual(result["process_id"], 222)
        self.assertFalse(result["cdp_ready"])

    @patch("auto_yt.services.local_browser_service.start_local_browser")
    @patch("playwright.async_api.async_playwright")
    def test_local_browser_session_does_not_close_browser(self, mock_playwright_fn, mock_start_browser):
        async def run():
            mock_start_browser.return_value = {
                "success": True,
                "port": 9222,
                "ws_url": "ws://127.0.0.1:9222/devtools",
            }
            mock_pw = AsyncMock()
            mock_browser = AsyncMock()
            mock_context = AsyncMock()
            mock_browser.contexts = [mock_context]
            mock_pw.chromium.connect_over_cdp.return_value = mock_browser

            mock_cm = AsyncMock()
            mock_cm.start.return_value = mock_pw
            mock_playwright_fn.return_value = mock_cm

            async with local_browser_service.local_browser_session("coccoc", "Default") as (context, browser):
                self.assertEqual(context, mock_context)
                self.assertEqual(browser, mock_browser)

            # Assert browser.close() was NEVER called
            mock_browser.close.assert_not_called()
            # Assert playwright.stop() was called
            mock_pw.stop.assert_called_once()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
