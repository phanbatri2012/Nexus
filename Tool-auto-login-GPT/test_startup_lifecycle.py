import unittest
from pathlib import Path


STARTUP_SCRIPT = Path(__file__).with_name("start_autoyt.ps1")
STOP_SCRIPT = Path(__file__).with_name("stop_autoyt.ps1")
RESTART_SCRIPT = Path(__file__).with_name("restart_autoyt.ps1")
ADMIN_HELPER = Path(__file__).with_name("lifecycle_admin.ps1")
RESTART_WRAPPER = Path(__file__).with_name("run_autoyt_restart.bat")
ROOT_DIR = Path(__file__).parent.parent
DOUBLE_CLICK_ENTRYPOINTS = (
    (
        ROOT_DIR / "run_autoyt.bat",
        Path(__file__).with_name("run_autoyt.bat"),
        STARTUP_SCRIPT,
    ),
    (
        ROOT_DIR / "run_autoyt_stop.bat",
        Path(__file__).with_name("run_autoyt_stop.bat"),
        STOP_SCRIPT,
    ),
    (
        ROOT_DIR / "run_autoyt_restart.bat",
        Path(__file__).with_name("run_autoyt_restart.bat"),
        RESTART_SCRIPT,
    ),
)
MAIN_MODULE = Path(__file__).parent / "src" / "auto_yt" / "main.py"
OMNIVOICE_CLIENT = (
    Path(__file__).parent / "src" / "auto_yt" / "services" / "omnivoice_client.py"
)
BROWSER_SERVICE_SCRIPTS = (
    Path(__file__).parent / "src" / "auto_yt" / "services" / "chatgpt_browser_service.py",
    Path(__file__).parent
    / "src"
    / "auto_yt"
    / "services"
    / "google_flow_browser_service.py",
)


class StartupLifecycleTests(unittest.TestCase):
    def test_lifecycle_entrypoints_elevate_before_runtime_work(self):
        expected_runtime_markers = {
            STARTUP_SCRIPT: "$projectRoot =",
            STOP_SCRIPT: "$projectRoot =",
            RESTART_SCRIPT: "$powershellPath =",
        }

        for script_path, runtime_marker in expected_runtime_markers.items():
            script = script_path.read_text(encoding="utf-8")
            elevation_position = script.index("Invoke-AutoYTElevatedScript")
            self.assertIn("Test-AutoYTAdministrator", script)
            self.assertLess(elevation_position, script.index(runtime_marker))

    def test_elevation_waits_and_propagates_the_administrator_exit_code(self):
        helper = ADMIN_HELPER.read_text(encoding="utf-8")

        self.assertIn('$powershellPath = Join-Path $PSHOME "powershell.exe"', helper)
        self.assertNotIn("GetCurrentProcess().MainModule.FileName", helper)
        self.assertIn("-Verb RunAs", helper)
        self.assertIn("-Wait", helper)
        self.assertIn("-PassThru", helper)
        self.assertIn("return [int]$process.ExitCode", helper)

    def test_double_click_entrypoints_reach_an_elevating_lifecycle_script(self):
        for root_wrapper_path, canonical_wrapper_path, lifecycle_script_path in (
            DOUBLE_CLICK_ENTRYPOINTS
        ):
            root_wrapper = root_wrapper_path.read_text(encoding="utf-8")
            canonical_wrapper = canonical_wrapper_path.read_text(encoding="utf-8")
            lifecycle_script = lifecycle_script_path.read_text(encoding="utf-8")

            self.assertIn(f"call {canonical_wrapper_path.name} %*", root_wrapper)
            self.assertIn(lifecycle_script_path.name, canonical_wrapper)
            self.assertIn("Test-AutoYTAdministrator", lifecycle_script)
            self.assertIn("Invoke-AutoYTElevatedScript", lifecycle_script)

    def test_restart_uses_one_elevated_powershell_flow(self):
        restart_script = RESTART_SCRIPT.read_text(encoding="utf-8")
        wrapper = RESTART_WRAPPER.read_text(encoding="utf-8")

        self.assertIn("restart_autoyt.ps1", wrapper)
        self.assertNotIn("call \"%~dp0run_autoyt_stop.bat\"", wrapper)
        self.assertNotIn("call \"%~dp0run_autoyt.bat\"", wrapper)
        self.assertIn('$env:AUTOYT_REQUIRE_GPM_IDLE = "1"', restart_script)
        self.assertIn('$env:AUTOYT_REQUIRE_IDLE = "1"', restart_script)
        self.assertIn("stop_autoyt.ps1", restart_script)
        self.assertIn("start_autoyt.ps1", restart_script)
        self.assertIn(
            '$powershellPath = Join-Path $PSHOME "powershell.exe"', restart_script
        )

    def test_only_canonical_wrapper_starts_omnivoice_worker(self):
        startup_script = STARTUP_SCRIPT.read_text(encoding="utf-8")
        main_source = MAIN_MODULE.read_text(encoding="utf-8")
        client_source = OMNIVOICE_CLIENT.read_text(encoding="utf-8")

        self.assertIn("Start-OmniVoiceWorker", startup_script)
        self.assertNotIn("omnivoice-api-startup", main_source)
        self.assertNotIn("subprocess.Popen", client_source)
        self.assertNotIn("DETACHED_PROCESS", client_source)

    def test_optional_process_handles_are_initialized_before_readiness_checks(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")
        readiness_probe_position = script.index("$backendReady = Test-BackendReady")

        for declaration in ("$backendProcess = $null", "$frontendProcess = $null"):
            self.assertIn(declaration, script)
            self.assertLess(script.index(declaration), readiness_probe_position)

    def test_missing_browser_services_are_started_in_background_without_forcing_show(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")

        for expected_action in (
            'Invoke-BrowserServiceAction "chatgpt_browser_service" "start" "ChatGPT"',
            'Invoke-BrowserServiceAction "google_flow_browser_service" "start" "Google Flow"',
        ):
            self.assertIn(expected_action, script)

        for forbidden_action in (
            'Invoke-BrowserServiceAction "chatgpt_browser_service" "show" "ChatGPT"',
            'Invoke-BrowserServiceAction "google_flow_browser_service" "show" "Google Flow"',
        ):
            self.assertNotIn(forbidden_action, script)

    def test_startup_requires_every_registered_service(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "-not ($backendReady -and $frontendReady -and $omniVoiceReady "
            "-and $chatgptReady -and $flowReady)",
            script,
        )

    def test_browser_readiness_probes_real_loopback_cdp_endpoint(self):
        script = STARTUP_SCRIPT.read_text(encoding="utf-8")

        self.assertGreaterEqual(script.count("/json/version"), 2)
        self.assertGreaterEqual(script.count("webSocketDebuggerUrl"), 2)
        self.assertGreaterEqual(script.count("^http://127\\.0\\.0\\.1:"), 2)

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
