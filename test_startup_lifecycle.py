import unittest
from pathlib import Path


STARTUP_SCRIPT = Path(__file__).with_name("start_autoyt.ps1")
BROWSER_SERVICE_SCRIPTS = (
    Path(__file__).parent / "src" / "auto_yt" / "services" / "chatgpt_browser_service.py",
    Path(__file__).parent
    / "src"
    / "auto_yt"
    / "services"
    / "google_flow_browser_service.py",
)


class StartupLifecycleTests(unittest.TestCase):
    def test_optional_process_handles_are_initialized_before_readiness_checks(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")
        readiness_probe_position = script.index("$backendReady = Test-BackendReady")

        for declaration in ("$backendProcess = $null", "$frontendProcess = $null"):
            self.assertIn(declaration, script)
            self.assertLess(script.index(declaration), readiness_probe_position)

    def test_missing_browser_services_are_started_and_shown(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")

        for expected_action in (
            'Invoke-BrowserServiceAction "chatgpt_browser_service" "start" "ChatGPT"',
            'Invoke-BrowserServiceAction "google_flow_browser_service" "start" "Google Flow"',
            'Invoke-BrowserServiceAction "chatgpt_browser_service" "show" "ChatGPT"',
            'Invoke-BrowserServiceAction "google_flow_browser_service" "show" "Google Flow"',
        ):
            self.assertIn(expected_action, script)

    def test_startup_requires_every_registered_service(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "-not ($backendReady -and $frontendReady -and $omniVoiceReady "
            "-and $chatgptReady -and $flowReady)",
            script,
        )

    def test_visible_window_waits_for_restore_before_moving(self):
        for service_script in BROWSER_SERVICE_SCRIPTS:
            script = service_script.read_text(encoding="utf-8")
            function_start = script.index("def _set_cdp_window_visibility")
            hidden_branch_start = script.index("\n        else:", function_start)
            visible_branch = script[function_start:hidden_branch_start]

            restore_position = visible_branch.index('"windowState": "normal"')
            wait_position = visible_branch.index("time.sleep(SERVICE_POLL_SECONDS)")
            bounds_position = visible_branch.index("_visible_window_bounds()")
            self.assertLess(restore_position, wait_position)
            self.assertLess(wait_position, bounds_position)


if __name__ == "__main__":
    unittest.main()
