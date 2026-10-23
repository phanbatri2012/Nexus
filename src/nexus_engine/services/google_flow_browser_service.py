"""On-demand local Chromium owner for all Google Flow automation workers.

The first worker lease starts this process and later workers attach through a
loopback-only CDP endpoint. The shared owner remains hidden and stops after a
bounded idle interval instead of opening a new browser for every operation.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from nexus_engine.paths import DATA_DIR, PROJECT_ROOT, google_flow_profile_dir
from nexus_engine.services import account_store, google_flow_account
from nexus_engine.services.on_demand_browser import OnDemandBrowserLifecycle
from nexus_engine.services.secret_store import write_private_text


DEFAULT_GOOGLE_FLOW_PROFILE = "PROFILE_GOOGLE_FLOW_1"
SERVICE_STATE_PATH = DATA_DIR / "google_flow_browser_service.json"
SERVICE_STOP_PATH = DATA_DIR / "google_flow_browser_service.stop.json"
SERVICE_ERROR_PATH = DATA_DIR / "google_flow_browser_service.error.log"
SERVICE_LOG_DIR = DATA_DIR / "logs"
SERVICE_START_TIMEOUT_SECONDS = 30.0
SERVICE_STOP_TIMEOUT_SECONDS = 15.0
SERVICE_POLL_SECONDS = 0.25
SERVICE_HEALTH_CHECK_SECONDS = 5.0
SERVICE_IDLE_TIMEOUT_SECONDS = 300.0
SERVICE_IDLE_POLL_SECONDS = 5.0
SERVICE_RESTART_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)
SERVICE_HOME_URL = "https://labs.google/fx/flow"
SERVICE_NAVIGATION_TIMEOUT_MS = 60_000
CDP_HOST = "127.0.0.1"
WINDOWS_BACKGROUND_BROWSER_ARGS = (
    "--window-position=-32000,-32000",
    "--window-size=1280,800",
    "--start-minimized",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
)
COMMON_BROWSER_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-notifications",
    "--disable-session-crashed-bubble",
    "--no-first-run",
    "--no-default-browser-check",
)

_manager_lock = threading.RLock()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    write_private_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((CDP_HOST, 0))
        return int(listener.getsockname()[1])


def _cdp_is_ready(endpoint: str, timeout: float = 0.8) -> bool:
    if not endpoint.startswith(f"http://{CDP_HOST}:"):
        return False
    try:
        with urllib.request.urlopen(
            f"{endpoint.rstrip('/')}/json/version",
            timeout=max(0.1, timeout),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return bool(payload.get("webSocketDebuggerUrl"))


def _read_last_error() -> str:
    try:
        return SERVICE_ERROR_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_last_error(message: str) -> None:
    write_private_text(SERVICE_ERROR_PATH, str(message or "").strip() + "\n")


def get_browser_service_launch_options(
    browser_settings: dict,
    *,
    cdp_port: int,
    platform_name: str | None = None,
) -> dict:
    """Build launch options for the single browser owned by the service."""
    hidden_requested = bool(
        browser_settings.get("worker_headless", True)
        or browser_settings.get("game_mode", False)
    )
    platform_name = platform_name or sys.platform
    args = [
        *COMMON_BROWSER_ARGS,
        f"--remote-debugging-address={CDP_HOST}",
        f"--remote-debugging-port={int(cdp_port)}",
    ]
    if hidden_requested and platform_name == "win32":
        # True headless is more likely to trigger Google Flow/Cloudflare checks.
        # The one headed window is created once, minimized and kept off-screen.
        args.extend(WINDOWS_BACKGROUND_BROWSER_ARGS)
        return {"headless": False, "args": args, "background": True}
    return {
        "headless": hidden_requested,
        "args": args,
        "background": hidden_requested,
    }


def _read_browser_service_status() -> dict:
    """Return process and endpoint health without starting Chromium."""
    state = _read_json(SERVICE_STATE_PATH)
    pid = int(state.get("pid") or 0)
    endpoint = str(state.get("cdp_url") or "")
    process_alive = _pid_is_alive(pid)
    connected = process_alive and _cdp_is_ready(endpoint)
    window_visible = bool(state.get("window_visible", False))
    if connected and window_visible:
        service_state = "connected"
        message = "Trình duyệt Google Flow đang hiển thị trên màn hình."
    elif connected:
        service_state = "connected"
        message = "Trình duyệt Google Flow nền đã kết nối."
    elif process_alive:
        service_state = "starting"
        message = "Trình duyệt Google Flow nền đang khởi động."
    else:
        service_state = "stopped"
        message = _read_last_error() or "Trình duyệt Google Flow nền chưa chạy."
    return {
        "running": connected,
        "connected": connected,
        "process_alive": process_alive,
        "state": service_state,
        "pid": pid or None,
        "started_at": str(state.get("started_at") or ""),
        "window_visible": window_visible,
        "generation_active": bool(state.get("generation_active", False)),
        "generation_checked_at": str(state.get("generation_checked_at") or ""),
        "message": message,
    }


def get_browser_service_status() -> dict:
    status = _read_browser_service_status()
    lifecycle = globals().get("_on_demand_lifecycle")
    if lifecycle is not None:
        status.update(lifecycle.snapshot())
        if status.get("connected") and not status.get("window_visible"):
            if status.get("in_use") or status.get("generation_active"):
                status["message"] = (
                    "Trình duyệt Google Flow đang được tác vụ sử dụng ở chế độ ẩn."
                )
            elif status.get("idle_shutdown_at"):
                status["message"] = (
                    "Trình duyệt Google Flow đang chờ tự tắt sau 5 phút rảnh."
                )
        elif not status.get("process_alive") and not _read_last_error():
            status["message"] = (
                "Trình duyệt Google Flow đang chờ và sẽ tự mở khi có tác vụ."
            )
    else:
        status.update(
            {
                "in_use": False,
                "active_lease_count": 0,
                "idle_shutdown_at": "",
                "idle_timeout_seconds": int(SERVICE_IDLE_TIMEOUT_SECONDS),
            }
        )
    return status


def get_browser_service_endpoint() -> str:
    """Return the healthy loopback CDP endpoint, or an empty string."""
    state = _read_json(SERVICE_STATE_PATH)
    endpoint = str(state.get("cdp_url") or "")
    pid = int(state.get("pid") or 0)
    if _pid_is_alive(pid) and _cdp_is_ready(endpoint):
        return endpoint
    return ""


def _service_environment() -> dict[str, str]:
    environment = dict(os.environ)
    source_root = str(PROJECT_ROOT / "src")
    existing_pythonpath = str(environment.get("PYTHONPATH") or "").strip()
    environment["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else source_root
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def _spawn_service_process() -> None:
    from nexus_engine.services.win32_window import spawn_service_on_interactive_desktop

    python_executable = sys.executable
    venv_pythonw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_pythonw.exists():
        python_executable = str(venv_pythonw)
    elif venv_python.exists():
        python_executable = str(venv_python)

    SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = SERVICE_LOG_DIR / "google-flow-browser-service.stdout.log"
    stderr_path = SERVICE_LOG_DIR / "google-flow-browser-service.stderr.log"
    spawn_service_on_interactive_desktop(
        [
            python_executable,
            "-m",
            "nexus_engine.services.google_flow_browser_service",
            "--serve",
        ],
        cwd=PROJECT_ROOT,
        env=_service_environment(),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def _arm_idle_after_start(status: dict) -> dict:
    lifecycle = globals().get("_on_demand_lifecycle")
    if lifecycle is not None and status.get("connected"):
        lifecycle.service_started()
        status.update(lifecycle.snapshot())
    return status


def start_browser_service(
    timeout: float = SERVICE_START_TIMEOUT_SECONDS,
) -> dict:
    """Start the shared browser owner once for manual or leased use."""
    with _manager_lock:
        status = _read_browser_service_status()
        if status["connected"]:
            result = status
        else:
            if status["process_alive"]:
                deadline = time.monotonic() + max(0.0, timeout)
            else:
                SERVICE_STATE_PATH.unlink(missing_ok=True)
                SERVICE_STOP_PATH.unlink(missing_ok=True)
                SERVICE_ERROR_PATH.unlink(missing_ok=True)
                _spawn_service_process()
                deadline = time.monotonic() + max(0.0, timeout)

            result = status
            while time.monotonic() < deadline:
                status = _read_browser_service_status()
                result = status
                if status["connected"]:
                    break
                if not status["process_alive"] and SERVICE_ERROR_PATH.exists():
                    break
                time.sleep(SERVICE_POLL_SECONDS)
            else:
                result = _read_browser_service_status()
    return _arm_idle_after_start(result)


def _stop_browser_service_process(
    timeout: float = SERVICE_STOP_TIMEOUT_SECONDS,
) -> dict:
    """Ask the owning process to close its context and persist fresh cookies."""
    with _manager_lock:
        state = _read_json(SERVICE_STATE_PATH)
        pid = int(state.get("pid") or 0)
        instance_id = str(state.get("instance_id") or "")
        if not _pid_is_alive(pid):
            SERVICE_STATE_PATH.unlink(missing_ok=True)
            SERVICE_STOP_PATH.unlink(missing_ok=True)
            return _read_browser_service_status()

        _write_json(
            SERVICE_STOP_PATH,
            {"instance_id": instance_id, "requested_at": _utc_now()},
        )
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if not _pid_is_alive(pid):
                break
            time.sleep(SERVICE_POLL_SECONDS)
        return _read_browser_service_status()


def stop_browser_service(
    timeout: float = SERVICE_STOP_TIMEOUT_SECONDS,
    *,
    force: bool = False,
) -> dict:
    lifecycle = globals().get("_on_demand_lifecycle")
    if force or lifecycle is None:
        result = _stop_browser_service_process(timeout)
    else:
        result = lifecycle.stop_now(lambda: _stop_browser_service_process(timeout))
    if lifecycle is not None:
        result.update(lifecycle.snapshot())
    return result


def _visible_window_bounds() -> dict[str, int]:
    """Choose a useful on-screen size without assuming a fixed monitor size."""
    screen_width = 1440
    screen_height = 900
    if sys.platform == "win32":
        try:
            screen_width = max(800, int(ctypes.windll.user32.GetSystemMetrics(0)))
            screen_height = max(600, int(ctypes.windll.user32.GetSystemMetrics(1)))
        except (AttributeError, OSError, ValueError):
            pass
    width = min(1440, max(800, screen_width - 160))
    height = min(960, max(600, screen_height - 120))
    return {
        "left": max(0, (screen_width - width) // 2),
        "top": max(0, (screen_height - height) // 2),
        "width": width,
        "height": height,
    }


def _set_connected_browser_window_visibility(browser, context, visible: bool) -> None:
    """Move and hide/show an already attached shared Chromium window."""
    from nexus_engine.services.win32_window import set_desktop_window_visibility

    browser_pids: set[int] = set()
    page = context.pages[0] if context.pages else context.new_page()

    try:
        browser_cdp = browser.new_browser_cdp_session()
        sys_info = browser_cdp.send("SystemInfo.getProcessInfo")
        for proc in sys_info.get("processInfo", []):
            proc_id = proc.get("id")
            if isinstance(proc_id, int) and proc_id > 0:
                browser_pids.add(proc_id)
    except Exception:
        pass

    session = context.new_cdp_session(page)
    window_id = session.send("Browser.getWindowForTarget")["windowId"]
    session.send(
        "Browser.setWindowBounds",
        {"windowId": window_id, "bounds": {"windowState": "normal"}},
    )
    time.sleep(SERVICE_POLL_SECONDS)
    if visible:
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": _visible_window_bounds()},
        )
        try:
            page.bring_to_front()
        except Exception:
            pass
    else:
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": -32000,
                    "top": -32000,
                    "width": 1280,
                    "height": 800,
                },
            },
        )
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
        )

    if sys.platform == "win32" and browser_pids:
        set_desktop_window_visibility(browser_pids, visible)


def _set_cdp_window_visibility(endpoint: str, visible: bool) -> None:
    """Move the existing Chromium window; never launch or replace a browser."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        if not browser.contexts:
            raise RuntimeError("Browser Service chưa có browser context.")
        context = browser.contexts[0]
        _set_connected_browser_window_visibility(browser, context, visible)


async def _set_async_connected_browser_window_visibility(
    browser,
    context,
    visible: bool,
) -> None:
    from nexus_engine.services.win32_window import set_desktop_window_visibility

    browser_pids: set[int] = set()
    page = context.pages[0] if context.pages else await context.new_page()
    try:
        browser_cdp = await browser.new_browser_cdp_session()
        sys_info = await browser_cdp.send("SystemInfo.getProcessInfo")
        for proc in sys_info.get("processInfo", []):
            proc_id = proc.get("id")
            if isinstance(proc_id, int) and proc_id > 0:
                browser_pids.add(proc_id)
    except Exception:
        pass

    session = await context.new_cdp_session(page)
    window_id = (await session.send("Browser.getWindowForTarget"))["windowId"]
    await session.send(
        "Browser.setWindowBounds",
        {"windowId": window_id, "bounds": {"windowState": "normal"}},
    )
    if visible:
        await session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": _visible_window_bounds()},
        )
        try:
            await page.bring_to_front()
        except Exception:
            pass
    else:
        await session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": -32000,
                    "top": -32000,
                    "width": 1280,
                    "height": 800,
                },
            },
        )
        await session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
        )
    if sys.platform == "win32" and browser_pids:
        set_desktop_window_visibility(browser_pids, visible)


async def keep_attached_browser_hidden(browser, context) -> None:
    """Hide a worker-attached window unless the user explicitly showed it."""
    if bool(_read_json(SERVICE_STATE_PATH).get("window_visible", False)):
        return
    await _set_async_connected_browser_window_visibility(browser, context, False)
    if bool(_read_json(SERVICE_STATE_PATH).get("window_visible", False)):
        await _set_async_connected_browser_window_visibility(browser, context, True)


def set_browser_service_window_visibility(visible: bool) -> dict:
    """Show or hide the one shared browser without touching the active page/job."""
    if visible:
        status = get_browser_service_status()
        if not status.get("connected"):
            status = start_browser_service()
            if not status.get("connected"):
                return status

    with _manager_lock:
        status = _read_browser_service_status()
        if not status.get("connected"):
            return status
        state = _read_json(SERVICE_STATE_PATH)
        endpoint = str(state.get("cdp_url") or "")
        _set_cdp_window_visibility(endpoint, bool(visible))
        state["window_visible"] = bool(visible)
        state["window_visibility_changed_at"] = _utc_now()
        _write_json(SERVICE_STATE_PATH, state)
        result = _read_browser_service_status()
    lifecycle = globals().get("_on_demand_lifecycle")
    if lifecycle is not None:
        lifecycle.visibility_changed(bool(visible))
        result = get_browser_service_status()
    return result


def _restore_saved_google_flow_session(context) -> bool:
    try:
        current_cookies = context.cookies()
        account = google_flow_account.load_account()
        restorable = google_flow_account.select_restorable_google_flow_cookies(
            account.get("session_cookies") or [],
            current_cookies,
        )
        if not restorable:
            return False
        context.add_cookies(restorable)
        return True
    except Exception as exc:
        print(f"Google Flow session restore skipped: {exc}", file=sys.stderr)
        return False


def _save_current_google_flow_session(context) -> None:
    try:
        cookies = [
            cookie
            for cookie in context.cookies()
            if google_flow_account.is_google_flow_cookie(cookie)
        ]
        if not google_flow_account.has_google_flow_auth_cookie(cookies):
            return
        account = google_flow_account.load_account()
        account["session_cookies"] = cookies
        google_flow_account.save_account(account)
    except Exception as exc:
        print(f"Google Flow session save skipped: {exc}", file=sys.stderr)


def _initialize_service_page(context) -> None:
    """Replace Playwright's visible blank startup page with Google Flow."""
    page = context.pages[0] if context.pages else context.new_page()
    if str(page.url or "").strip().casefold() != "about:blank":
        return
    try:
        page.goto(
            SERVICE_HOME_URL,
            wait_until="domcontentloaded",
            timeout=SERVICE_NAVIGATION_TIMEOUT_MS,
        )
    except Exception as exc:
        # CDP ownership is still useful when Google Flow is temporarily unreachable;
        # workers perform their own bounded navigation and attention checks.
        print(
            f"Google Flow Browser Service initial navigation skipped: {exc}",
            file=sys.stderr,
        )


def _page_has_active_flow_generation(page) -> bool:
    if "/project/" not in str(page.url or ""):
        return False
    try:
        result = page.evaluate(
            r'''() => {
                const visible = (element) => {
                    if (!element) return false;
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        rect.width > 0 && rect.height > 0;
                };
                const stopButtons = Array.from(document.querySelectorAll(
                    "flow-creative-agent-prompt-box button, flow-base-prompt-box button, " +
                    "flow-prompt-box button, button[aria-label], [role='button'][aria-label]"
                ));
                if (stopButtons.some((button) => {
                    if (!visible(button)) return false;
                    const text = String(button.textContent || '').trim().toLowerCase();
                    const aria = String(button.getAttribute('aria-label') || '').trim().toLowerCase();
                    return text === 'stop' || text === 'dừng' || aria === 'stop' || aria === 'dừng' ||
                        aria.includes('stop generation') || aria.includes('dừng tạo');
                })) return true;
                return Array.from(document.querySelectorAll(
                    "flow-chat-thinking-indicator, [aria-busy='true'], [role='progressbar'], " +
                    "[data-state='generating'], [data-state='processing']"
                )).some(visible);
            }'''
        )
    except Exception:
        return False
    return result is True


def _refresh_generation_state(state: dict, context) -> None:
    persisted_state = _read_json(SERVICE_STATE_PATH)
    if persisted_state.get("instance_id") == state.get("instance_id"):
        state["window_visible"] = bool(
            persisted_state.get("window_visible", state.get("window_visible", False))
        )
    state["generation_active"] = any(
        _page_has_active_flow_generation(page) for page in context.pages
    )
    state["generation_checked_at"] = _utc_now()
    _write_json(SERVICE_STATE_PATH, state)


def _stop_requested(instance_id: str) -> bool:
    request = _read_json(SERVICE_STOP_PATH)
    return bool(request) and request.get("instance_id") == instance_id


def run_browser_service() -> int:
    """Own the persistent context until the backend or user requests a stop."""
    from playwright.sync_api import sync_playwright

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        sys.stdout = open(
            SERVICE_LOG_DIR / "google-flow-browser-service.stdout.log",
            "a",
            encoding="utf-8",
            buffering=1,
        )
        sys.stderr = open(
            SERVICE_LOG_DIR / "google-flow-browser-service.stderr.log",
            "a",
            encoding="utf-8",
            buffering=1,
        )
    except Exception:
        pass

    instance_id = uuid.uuid4().hex
    cdp_port = _reserve_loopback_port()
    endpoint = f"http://{CDP_HOST}:{cdp_port}"
    state = {
        "pid": os.getpid(),
        "instance_id": instance_id,
        "cdp_url": endpoint,
        "profile_dir": str(google_flow_profile_dir("PROFILE_GOOGLE_FLOW_1").resolve()),
        "started_at": _utc_now(),
        "ready": False,
        "window_visible": False,
    }
    SERVICE_STOP_PATH.unlink(missing_ok=True)
    SERVICE_ERROR_PATH.unlink(missing_ok=True)
    _write_json(SERVICE_STATE_PATH, state)

    stop_event = threading.Event()

    def request_stop(_signum=None, _frame=None) -> None:
        stop_event.set()

    for signal_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            try:
                signal.signal(signal_value, request_stop)
            except (OSError, ValueError):
                pass

    context = None
    exit_code = 0
    try:
        profile_dir = google_flow_profile_dir("PROFILE_GOOGLE_FLOW_1")
        profile_dir.mkdir(parents=True, exist_ok=True)
        for lock_file in profile_dir.glob("Singleton*"):
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
        browser_settings = account_store.get_browser_automation_settings()
        launch_options = get_browser_service_launch_options(
            browser_settings,
            cdp_port=cdp_port,
        )
        state["window_visible"] = not bool(launch_options["background"])
        _write_json(SERVICE_STATE_PATH, state)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=launch_options["headless"],
                args=launch_options["args"],
                viewport={"width": 1280, "height": 800},
            )
            _restore_saved_google_flow_session(context)
            _initialize_service_page(context)
            ready_deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS
            while time.monotonic() < ready_deadline and not _cdp_is_ready(endpoint):
                time.sleep(SERVICE_POLL_SECONDS)
            if not _cdp_is_ready(endpoint):
                raise RuntimeError("Chromium did not expose its local CDP endpoint.")
            state["ready"] = True
            _refresh_generation_state(state, context)
            print(f"Google Flow Browser Service ready at {endpoint}", flush=True)

            next_health_check = time.monotonic() + SERVICE_HEALTH_CHECK_SECONDS
            while not stop_event.wait(SERVICE_POLL_SECONDS):
                if _stop_requested(instance_id):
                    break
                if time.monotonic() >= next_health_check:
                    if not _cdp_is_ready(endpoint):
                        raise RuntimeError(
                            "Chromium closed or its CDP endpoint was lost."
                        )
                    _refresh_generation_state(state, context)
                    next_health_check = (
                        time.monotonic() + SERVICE_HEALTH_CHECK_SECONDS
                    )
            _save_current_google_flow_session(context)
    except Exception as exc:
        exit_code = 1
        _write_last_error(str(exc))
        print(f"Google Flow Browser Service failed: {exc}", file=sys.stderr, flush=True)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        current_state = _read_json(SERVICE_STATE_PATH)
        if current_state.get("instance_id") == instance_id:
            SERVICE_STATE_PATH.unlink(missing_ok=True)
        stop_request = _read_json(SERVICE_STOP_PATH)
        if stop_request.get("instance_id") == instance_id:
            SERVICE_STOP_PATH.unlink(missing_ok=True)
    return exit_code


def _wait_before_service_restart(delay_seconds: float, attempt: int) -> bool:
    """Keep the owner discoverable during backoff and return False when stopped."""
    instance_id = uuid.uuid4().hex
    state = {
        "pid": os.getpid(),
        "instance_id": instance_id,
        "cdp_url": "",
        "started_at": _utc_now(),
        "ready": False,
        "recovering": True,
        "restart_attempt": attempt,
    }
    _write_json(SERVICE_STATE_PATH, state)
    deadline = time.monotonic() + max(0.0, delay_seconds)
    while time.monotonic() < deadline:
        if _stop_requested(instance_id):
            SERVICE_STATE_PATH.unlink(missing_ok=True)
            SERVICE_STOP_PATH.unlink(missing_ok=True)
            return False
        time.sleep(min(SERVICE_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    return True


def run_browser_service_with_recovery() -> int:
    attempt = 0
    while True:
        exit_code = run_browser_service()
        if exit_code == 0:
            return 0
        delay = SERVICE_RESTART_BACKOFF_SECONDS[
            min(attempt, len(SERVICE_RESTART_BACKOFF_SECONDS) - 1)
        ]
        attempt += 1
        print(
            f"Google Flow Browser Service restarting in {delay:g}s "
            f"(attempt {attempt}).",
            file=sys.stderr,
            flush=True,
        )
        if not _wait_before_service_restart(delay, attempt):
            return 0


_on_demand_lifecycle = OnDemandBrowserLifecycle(
    name="Google Flow",
    start_service=start_browser_service,
    stop_service=_stop_browser_service_process,
    status_provider=_read_browser_service_status,
    external_busy=lambda status: bool(status.get("generation_active")),
    idle_timeout_seconds=SERVICE_IDLE_TIMEOUT_SECONDS,
    idle_poll_seconds=SERVICE_IDLE_POLL_SECONDS,
)


def acquire_browser_service():
    """Acquire one worker lease and lazily start the shared browser."""
    return _on_demand_lifecycle.acquire()


def arm_browser_service_idle_watchdog() -> None:
    """Adopt a pre-existing hidden owner without starting a new process."""
    _on_demand_lifecycle.arm_existing_service()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    arguments = parser.parse_args()
    if not arguments.serve:
        parser.error("--serve is required")
    return run_browser_service_with_recovery()


if __name__ == "__main__":
    raise SystemExit(main())
