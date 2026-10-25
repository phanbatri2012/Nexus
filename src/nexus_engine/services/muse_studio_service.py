"""On-demand lifecycle manager for the local Muse AI Studio REST service."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
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
from typing import Any

import nexus_engine.services.database as db
from nexus_engine.paths import DATA_DIR, LOGS_DIR
from nexus_engine.services import muse_settings, process_registry


logger = logging.getLogger(__name__)

MUSE_API_HOST = "127.0.0.1"
MUSE_API_PORT = 18610
MUSE_CDP_PORT = 19210
MUSE_API_BASE_URL = f"http://{MUSE_API_HOST}:{MUSE_API_PORT}"
MUSE_IDLE_CLOSE_SECONDS = 60.0
MUSE_SERVICE_PROCESS_KEY = "muse-studio-service"
MUSE_SERVICE_STATE_PATH = DATA_DIR / "muse_studio_service.json"
MUSE_TASK_RECOVERY_RELEASE_PATH = DATA_DIR / "muse_task_recovery_release.json"
MUSE_STUDIO_STDOUT_PATH = LOGS_DIR / "muse-studio-ui.current.stdout.log"
MUSE_STUDIO_STDERR_PATH = LOGS_DIR / "muse-studio-ui.current.stderr.log"
MUSE_STUDIO_STARTUP_CHECK_SECONDS = 1.5
MUSE_ORPHAN_CLEANUP_TIMEOUT_SECONDS = 5.0


class MuseStudioServiceError(RuntimeError):
    """Base lifecycle error for Muse AI Studio."""


class MuseStudioConfigurationError(MuseStudioServiceError):
    """Muse AI Studio is missing or has an incompatible layout."""


class MuseStudioCapabilityDisabled(MuseStudioConfigurationError):
    """A required Muse capability exists but has not been released locally."""

    def __init__(self, capability: str, message: str):
        super().__init__(message)
        self.capability = str(capability or "").strip()


class MuseStudioServiceBusy(MuseStudioServiceError):
    """A required local port is owned by an external process."""


class MuseStudioServiceUnavailable(MuseStudioServiceError):
    """The owned Muse2API process could not become ready."""


_lock = threading.RLock()
_process: subprocess.Popen[Any] | None = None
_api_key = ""
_instance_id = ""
_studio_path: Path | None = None
_idle_timer: threading.Timer | None = None
_stdout_handle = None
_stderr_handle = None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise MuseStudioServiceUnavailable("Muse2API không được phép redirect khỏi localhost.")


def _validate_studio_path(value: str | Path | None = None) -> Path:
    configured = str(value or muse_settings.load_muse_settings().get("muse_studio_path") or "").strip()
    if not configured:
        raise MuseStudioConfigurationError("Chưa cấu hình đường dẫn Muse_AI_Studio.")
    path = Path(configured).expanduser().resolve()
    required = (path / "main.py", path / "muse2api_backend" / "app.py")
    if not path.is_dir() or not all(item.is_file() for item in required):
        raise MuseStudioConfigurationError(
            "Đường dẫn Muse_AI_Studio không hợp lệ; cần có main.py và muse2api_backend/app.py."
        )
    return path


def validate_studio_path(value: str | Path | None = None) -> str:
    """Validate and return the resolved Studio path without starting any process."""

    return str(_validate_studio_path(value))


def _studio_data_dir(studio_path: Path) -> Path:
    """Mirror Muse Studio's source-mode data-directory compatibility rule."""

    legacy = studio_path / "muse2api_backend" / "data"
    return legacy.resolve() if legacy.is_dir() else (studio_path / "muse2api_data").resolve()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((MUSE_API_HOST, int(port))) == 0


def _windows_port_process(port: int) -> dict | None:
    """Return sanitized ownership metadata for one Windows listening port."""

    if os.name != "nt":
        return None
    script = f"""
$connection = Get-NetTCPConnection -State Listen -LocalPort {int(port)} -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($connection) {{
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$connection.OwningProcess) -ErrorAction SilentlyContinue
    if ($process) {{
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$process.ParentProcessId) -ErrorAction SilentlyContinue
        [pscustomobject]@{{
            process_id = [int]$process.ProcessId
            parent_process_id = [int]$process.ParentProcessId
            parent_running = [bool]($null -ne $parent)
            name = [string]$process.Name
            executable_path = [string]$process.ExecutablePath
            command_line = [string]$process.CommandLine
        }} | ConvertTo-Json -Compress
    }}
}}
"""
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=MUSE_ORPHAN_CLEANUP_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = str(completed.stdout or "").strip()
        if completed.returncode != 0 or not raw:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        return {
            "process_id": int(payload.get("process_id") or 0),
            "parent_process_id": int(payload.get("parent_process_id") or 0),
            "parent_running": bool(payload.get("parent_running")),
            "name": str(payload.get("name") or ""),
            "executable_path": str(payload.get("executable_path") or ""),
            "command_line": str(payload.get("command_line") or ""),
        }
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def _windows_process(process_id: int) -> dict | None:
    """Return sanitized metadata for one Windows process without logging its command."""

    if os.name != "nt" or int(process_id or 0) <= 0:
        return None
    script = f"""
$process = Get-CimInstance Win32_Process -Filter ("ProcessId=" + {int(process_id)}) -ErrorAction SilentlyContinue
if ($process) {{
    $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$process.ParentProcessId) -ErrorAction SilentlyContinue
    [pscustomobject]@{{
        process_id = [int]$process.ProcessId
        parent_process_id = [int]$process.ParentProcessId
        parent_running = [bool]($null -ne $parent)
        name = [string]$process.Name
        executable_path = [string]$process.ExecutablePath
        command_line = [string]$process.CommandLine
    }} | ConvertTo-Json -Compress
}}
"""
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=MUSE_ORPHAN_CLEANUP_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = str(completed.stdout or "").strip()
        if completed.returncode != 0 or not raw:
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        return {
            "process_id": int(payload.get("process_id") or 0),
            "parent_process_id": int(payload.get("parent_process_id") or 0),
            "parent_running": bool(payload.get("parent_running")),
            "name": str(payload.get("name") or ""),
            "executable_path": str(payload.get("executable_path") or ""),
            "command_line": str(payload.get("command_line") or ""),
        }
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def _is_expected_muse_browser(process: dict, studio_path: Path) -> bool:
    if str(process.get("name") or "").strip().lower() not in {"chrome.exe", "chromium.exe"}:
        return False
    command_line = str(process.get("command_line") or "")
    normalized_command = command_line.replace("/", "\\").casefold()
    if "--headless" not in normalized_command:
        return False
    if f"--remote-debugging-port={MUSE_CDP_PORT}" not in normalized_command:
        return False
    profile_match = re.search(r'--user-data-dir=(?:"([^"]+)"|(\S+))', command_line, re.IGNORECASE)
    if not profile_match:
        return False
    profile_path = str(profile_match.group(1) or profile_match.group(2) or "")
    normalized_profile = profile_path.replace("/", "\\").rstrip("\\").casefold()
    expected_roots = (
        studio_path / "chrome_user_data" / "muse_profiles",
        Path.home() / "muse2api-profiles",
    )
    return any(
        normalized_profile == normalized_root
        or normalized_profile.startswith(normalized_root + "\\")
        for root in expected_roots
        for normalized_root in (
            str(root.resolve()).replace("/", "\\").rstrip("\\").casefold(),
        )
    )


def _cdp_is_muse_browser() -> bool:
    try:
        request = urllib.request.Request(
            f"http://{MUSE_API_HOST}:{MUSE_CDP_PORT}/json/version",
            headers={"Accept": "application/json", "User-Agent": "Nexus-Studio-Engine/1"},
            method="GET",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return False
        browser = str(payload.get("Browser") or "").strip().lower()
        return browser.startswith(("chrome/", "chromium/", "headlesschrome/"))
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return False


def _is_expected_muse_api_process(process: dict, *, require_nexus_python: bool = False) -> bool:
    if str(process.get("name") or "").strip().lower() not in {"python.exe", "pythonw.exe"}:
        return False
    command_line = str(process.get("command_line") or "")
    if not re.search(
        r"(?:^|\s)-m\s+uvicorn(?:\.main)?\s+app:app(?:\s|$)",
        command_line,
        re.IGNORECASE,
    ):
        return False
    if not re.search(
        rf"(?:--port(?:=|\s+)){MUSE_API_PORT}(?:\s|$)",
        command_line,
        re.IGNORECASE,
    ):
        return False
    if not require_nexus_python:
        return True
    try:
        actual = Path(str(process.get("executable_path") or "")).resolve()
        expected = Path(sys.executable).resolve()
    except (OSError, ValueError):
        return False
    return os.path.normcase(str(actual)) == os.path.normcase(str(expected))


def _probe_idle_unowned_muse_runtime() -> dict | None:
    """Recognize an idle Muse2API runtime using only its public loopback contract."""

    try:
        health = _request_json("/healthz", timeout=1.5)
        ready = _request_json("/readyz", timeout=1.5)
        runtime_id = str(ready.get("runtime_id") or "").strip()
        active_media_tasks = int(ready["active_media_tasks"])
        accounts = int(ready.get("accounts") or 0)
    except (KeyError, TypeError, ValueError, OSError, urllib.error.URLError):
        return None
    if (
        str(health.get("status") or "").strip().lower() != "ok"
        or str(ready.get("status") or "").strip().lower() != "ready"
        or not runtime_id
        or len(runtime_id) > 128
        or active_media_tasks != 0
        or accounts <= 0
    ):
        return None
    return {
        "runtime_id": runtime_id,
        "active_media_tasks": active_media_tasks,
    }


def _recover_orphaned_muse_runtime(studio_path: Path) -> bool:
    """Remove an idle, verified Muse process tree whose Nexus ownership marker was lost."""

    if os.name != "nt" or MUSE_SERVICE_STATE_PATH.exists():
        return False
    with _lock:
        if _process is not None or _api_key:
            return False
    if not _has_active_muse_jobs():
        return False
    if not _port_in_use(MUSE_API_PORT) or not _port_in_use(MUSE_CDP_PORT):
        return False

    api_process = _windows_port_process(MUSE_API_PORT)
    browser_process = _windows_port_process(MUSE_CDP_PORT)
    if (
        not api_process
        or not browser_process
        or not _is_expected_muse_api_process(api_process)
        or not _is_expected_muse_browser(browser_process, studio_path)
        or int(browser_process.get("parent_process_id") or 0)
        != int(api_process.get("process_id") or 0)
        or not _cdp_is_muse_browser()
    ):
        return False

    api_pid = int(api_process.get("process_id") or 0)
    root_process = _windows_process(int(api_process.get("parent_process_id") or 0))
    if root_process and _is_expected_muse_api_process(
        root_process,
        require_nexus_python=True,
    ):
        root_pid = int(root_process.get("process_id") or 0)
    elif _is_expected_muse_api_process(api_process, require_nexus_python=True):
        root_pid = api_pid
    else:
        return False
    if root_pid <= 0:
        return False

    runtime = _probe_idle_unowned_muse_runtime()
    if not runtime:
        return False

    confirmed_api = _windows_port_process(MUSE_API_PORT)
    confirmed_browser = _windows_port_process(MUSE_CDP_PORT)
    confirmed_runtime = _probe_idle_unowned_muse_runtime()
    confirmed_root = (
        confirmed_api
        if root_pid == api_pid
        else _windows_process(root_pid)
    )
    if (
        not confirmed_api
        or not confirmed_browser
        or not confirmed_root
        or not confirmed_runtime
        or confirmed_runtime.get("runtime_id") != runtime.get("runtime_id")
        or int(confirmed_api.get("process_id") or 0) != api_pid
        or (
            root_pid != api_pid
            and int(confirmed_api.get("parent_process_id") or 0) != root_pid
        )
        or int(confirmed_browser.get("process_id") or 0)
        != int(browser_process.get("process_id") or 0)
        or int(confirmed_browser.get("parent_process_id") or 0) != api_pid
        or not _is_expected_muse_api_process(confirmed_api)
        or not _is_expected_muse_api_process(
            confirmed_root,
            require_nexus_python=True,
        )
        or not _is_expected_muse_browser(confirmed_browser, studio_path)
        or MUSE_SERVICE_STATE_PATH.exists()
    ):
        return False

    try:
        completed = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(root_pid)],
            capture_output=True,
            timeout=MUSE_ORPHAN_CLEANUP_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not _port_in_use(MUSE_API_PORT) and not _port_in_use(MUSE_CDP_PORT):
            logger.info(
                "Recovered idle Muse2API orphan runtime_id=%s root_pid=%s api_pid=%s reason=missing_ownership_marker",
                runtime["runtime_id"],
                root_pid,
                api_pid,
            )
            return True
        time.sleep(0.1)
    return False


def _recover_orphaned_muse_browser(studio_path: Path) -> bool:
    """Remove only a verified Muse Chrome orphan that owns the CDP port."""

    if os.name != "nt" or _port_in_use(MUSE_API_PORT) or not _port_in_use(MUSE_CDP_PORT):
        return False
    process = _windows_port_process(MUSE_CDP_PORT)
    if not process or process.get("parent_running"):
        return False
    if int(process.get("process_id") or 0) <= 0:
        return False
    if not _is_expected_muse_browser(process, studio_path) or not _cdp_is_muse_browser():
        return False

    pid = int(process["process_id"])
    confirmed_process = _windows_port_process(MUSE_CDP_PORT)
    if (
        not confirmed_process
        or int(confirmed_process.get("process_id") or 0) != pid
        or confirmed_process.get("parent_running")
        or not _is_expected_muse_browser(confirmed_process, studio_path)
    ):
        return False
    try:
        completed = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=MUSE_ORPHAN_CLEANUP_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _port_in_use(MUSE_CDP_PORT):
            logger.info("Đã thu hồi Muse browser orphan PID %s trên cổng %s.", pid, MUSE_CDP_PORT)
            return True
        time.sleep(0.1)
    return False


def _muse_ports_available(studio_path: Path) -> bool:
    api_busy = _port_in_use(MUSE_API_PORT)
    cdp_busy = _port_in_use(MUSE_CDP_PORT)
    if api_busy and cdp_busy and _recover_orphaned_muse_runtime(studio_path):
        api_busy = _port_in_use(MUSE_API_PORT)
        cdp_busy = _port_in_use(MUSE_CDP_PORT)
    if not api_busy and cdp_busy and _recover_orphaned_muse_browser(studio_path):
        api_busy = _port_in_use(MUSE_API_PORT)
        cdp_busy = _port_in_use(MUSE_CDP_PORT)
    return not api_busy and not cdp_busy


def _write_state(process: subprocess.Popen[Any], studio_path: Path) -> None:
    payload = {
        "pid": int(process.pid),
        "instance_id": _instance_id,
        "owned": True,
        "studio_path": str(studio_path),
        "api_port": MUSE_API_PORT,
        "cdp_port": MUSE_CDP_PORT,
        "started_at": db.utc_now(),
    }
    MUSE_SERVICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MUSE_SERVICE_STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(MUSE_SERVICE_STATE_PATH)


def _remove_state() -> None:
    try:
        MUSE_SERVICE_STATE_PATH.unlink(missing_ok=True)
    except OSError:
        logger.warning("Không thể xóa Muse service state marker.")


def task_recovery_release_status() -> dict:
    """Return the sanitized local release decision for the recovery contract."""

    default = {
        "enabled": False,
        "contract_version": 0,
        "successful_runs": 0,
        "verified_at": "",
        "evidence_sha256": "",
    }
    try:
        payload = json.loads(MUSE_TASK_RECOVERY_RELEASE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default
    if not isinstance(payload, dict):
        return default
    try:
        contract_version = max(0, int(payload.get("contract_version") or 0))
        successful_runs = max(0, int(payload.get("successful_runs") or 0))
    except (TypeError, ValueError):
        return default
    verified_at = str(payload.get("verified_at") or "").strip()
    evidence_sha256 = str(payload.get("evidence_sha256") or "").strip().casefold()
    evidence_valid = bool(re.fullmatch(r"[0-9a-f]{64}", evidence_sha256))
    enabled = bool(
        payload.get("enabled") is True
        and contract_version >= 1
        and successful_runs >= 3
        and verified_at
        and evidence_valid
    )
    return {
        "enabled": enabled,
        "contract_version": contract_version,
        "successful_runs": successful_runs,
        "verified_at": verified_at,
        "evidence_sha256": evidence_sha256 if evidence_valid else "",
    }


def record_task_recovery_e2e_verification(
    *,
    successful_runs: int,
    evidence_sha256: str,
) -> dict:
    """Publish recovery only after three strict start/end E2E runs passed."""

    run_count = max(0, int(successful_runs))
    evidence_digest = str(evidence_sha256 or "").strip().casefold()
    if run_count < 3:
        raise ValueError("Cần ít nhất 3 lượt E2E Muse start/end thành công.")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_digest):
        raise ValueError("Evidence digest E2E Muse không hợp lệ.")
    payload = {
        "enabled": True,
        "contract_version": 1,
        "successful_runs": run_count,
        "verified_at": db.utc_now(),
        "evidence_sha256": evidence_digest,
    }
    MUSE_TASK_RECOVERY_RELEASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MUSE_TASK_RECOVERY_RELEASE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(MUSE_TASK_RECOVERY_RELEASE_PATH)
    return task_recovery_release_status()


def _request_json(
    path: str,
    *,
    api_key: str = "",
    timeout: float = 3.0,
) -> dict:
    clean_path = "/" + str(path or "").lstrip("/")
    headers = {"Accept": "application/json", "User-Agent": "Nexus-Studio-Engine/1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(MUSE_API_BASE_URL + clean_path, headers=headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _probe_owned_service(*, timeout: float = 3.0) -> dict:
    with _lock:
        key = _api_key
    if not key:
        raise MuseStudioServiceUnavailable("Muse2API chưa có phiên do Nexus Studio Engine sở hữu.")
    health = _request_json("/healthz", timeout=timeout)
    if health.get("status") != "ok":
        raise MuseStudioServiceUnavailable("Muse2API health check không đạt.")
    ready = _request_json("/readyz", timeout=timeout)
    if ready.get("status") != "ready" or int(ready.get("accounts") or 0) <= 0:
        raise MuseStudioConfigurationError(
            "Muse_AI_Studio chưa có account khả dụng; hãy mở Muse Studio để đăng nhập trước."
        )
    try:
        models = _request_json("/v1/models", api_key=key, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise MuseStudioConfigurationError(
                "Muse2API từ chối API key nội bộ; service không tương thích."
            ) from exc
        raise
    model_rows = models.get("data")
    model_ids = {
        str(item.get("id") or "").strip()
        for item in model_rows
        if isinstance(item, dict)
    } if isinstance(model_rows, list) else set()
    if not {"muse-image", "muse-video"}.issubset(model_ids):
        raise MuseStudioConfigurationError("Muse2API không tương thích: /v1/models sai contract.")
    capabilities = models.get("capabilities")
    try:
        start_end_version = int(
            capabilities.get("video_start_end_frames")
            if isinstance(capabilities, dict)
            else 0
        )
    except (TypeError, ValueError):
        start_end_version = 0
    if start_end_version < 1:
        raise MuseStudioConfigurationError(
            "Muse2API không hỗ trợ contract video start/end bắt buộc; hãy cập nhật Muse_AI_Studio."
        )
    try:
        task_recovery_version = int(
            capabilities.get("task_recovery")
            if isinstance(capabilities, dict)
            else 0
        )
    except (TypeError, ValueError):
        task_recovery_version = 0
    if task_recovery_version < 1:
        raise MuseStudioCapabilityDisabled(
            "task_recovery",
            "Muse2API chưa bật contract task recovery đã phát hành; "
            "checkpoint được giữ nguyên để tiếp tục sau khi xác minh cấu hình.",
        )
    try:
        active_media_tasks = max(0, int(ready.get("active_media_tasks") or 0))
    except (TypeError, ValueError) as exc:
        raise MuseStudioServiceUnavailable(
            "Muse2API /readyz trả active_media_tasks sai contract."
        ) from exc
    runtime_id = str(ready.get("runtime_id") or "").strip()
    if not runtime_id:
        raise MuseStudioServiceUnavailable("Muse2API /readyz thiếu runtime_id.")
    return {
        "running": True,
        "owned": True,
        "api_ready": True,
        "accounts": int(ready.get("accounts") or 0),
        "browser_running": bool(ready.get("browser_running")),
        "video_start_end_frames": start_end_version,
        "task_recovery": task_recovery_version,
        "active_media_tasks": active_media_tasks,
        "runtime_id": runtime_id,
        "api_url": MUSE_API_BASE_URL,
    }


def _close_log_handles() -> None:
    global _stdout_handle, _stderr_handle
    for handle in (_stdout_handle, _stderr_handle):
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
    _stdout_handle = None
    _stderr_handle = None


def _cancel_idle_timer() -> None:
    global _idle_timer
    with _lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
        _idle_timer = None


def _has_active_muse_jobs() -> bool:
    try:
        for job in db.list_system_jobs(limit=None, job_type="muse_media_generation"):
            status = str(job.get("status") or "").strip().lower()
            if status in {"processing", "queued", "running", "retry_wait"}:
                return True
        return False
    except Exception:
        logger.warning("Không thể xác minh trạng thái job Muse; giữ service để fail closed.")
        return True


def _has_active_provider_tasks() -> bool:
    """Fail closed unless Muse2API confirms that no media task is active."""

    with _lock:
        process = _process
    if process is None or process.poll() is not None:
        return False
    try:
        ready = _request_json("/readyz", timeout=2.0)
        if not str(ready.get("runtime_id") or "").strip():
            raise ValueError("missing runtime_id")
        return int(ready.get("active_media_tasks")) > 0
    except Exception:
        logger.warning("Không thể xác minh active media task Muse2API; hoãn idle shutdown.")
        return True


def ensure_started(*, timeout_seconds: float = 20.0) -> dict:
    """Start Muse2API only when requested and return its sanitized status."""

    global _process, _api_key, _instance_id, _studio_path, _stdout_handle, _stderr_handle
    _cancel_idle_timer()
    studio_path = _validate_studio_path()
    with _lock:
        if _process is not None and _process.poll() is None:
            return _probe_owned_service(timeout=min(5.0, timeout_seconds))
        if _process is not None:
            process_registry.unregister_process(MUSE_SERVICE_PROCESS_KEY, _process)
            _process = None
            _close_log_handles()
            _remove_state()
        if not _muse_ports_available(studio_path):
            raise MuseStudioServiceBusy(
                "Cổng Muse 18610/19210 đang do tiến trình khác sử dụng; hãy đóng Muse Studio trước."
            )
        release_status = task_recovery_release_status()
        if not release_status.get("enabled"):
            raise MuseStudioCapabilityDisabled(
                "task_recovery",
                "Muse task recovery chưa được phát hành trên máy này vì chưa có "
                "bằng chứng E2E start/end đạt 3 lượt.",
            )

        python_executable = Path(sys.executable).resolve()
        if not python_executable.is_file():
            raise MuseStudioConfigurationError("Không tìm thấy Python runtime của Nexus Studio Engine.")
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _stdout_handle = open(LOGS_DIR / "muse2api.current.stdout.log", "a", encoding="utf-8")
        _stderr_handle = open(LOGS_DIR / "muse2api.current.stderr.log", "a", encoding="utf-8")
        _api_key = "nexus_muse_" + secrets.token_hex(32)
        _instance_id = uuid.uuid4().hex
        _studio_path = studio_path
        environment = dict(os.environ)
        environment.update(
            {
                "MUSE2API_HOST": MUSE_API_HOST,
                "MUSE2API_PORT": str(MUSE_API_PORT),
                "MUSE2API_CDP_PORT": str(MUSE_CDP_PORT),
                "MUSE2API_KEY": _api_key,
                "MUSE2API_CONCURRENCY": "1",
                "MUSE2API_TASK_RECOVERY_ENABLED": "1",
                "MUSE2API_HOME": str((studio_path / "muse2api_backend").resolve()),
                "MUSE2API_DATA_DIR": str(_studio_data_dir(studio_path)),
                "MUSE2API_PROFILE_ROOT": str(
                    (studio_path / "chrome_user_data" / "muse_profiles").resolve()
                ),
                "MUSE2API_HOME_DIR": str(studio_path),
            }
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        try:
            _process = subprocess.Popen(
                [str(python_executable), str((studio_path / "main.py").resolve()), "--muse-service"],
                cwd=str(studio_path),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=_stdout_handle,
                stderr=_stderr_handle,
                creationflags=creationflags,
            )
        except Exception:
            _api_key = ""
            _instance_id = ""
            _studio_path = None
            _close_log_handles()
            raise
        process_registry.register_process(MUSE_SERVICE_PROCESS_KEY, _process)
        _write_state(_process, studio_path)

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        with _lock:
            process = _process
        if process is None or process.poll() is not None:
            break
        try:
            status_payload = _probe_owned_service(timeout=2.0)
            logger.info(
                "Muse2API ready instance_id=%s runtime_id=%s pid=%s active_media_tasks=%s",
                _instance_id,
                status_payload.get("runtime_id"),
                getattr(process, "pid", 0),
                status_payload.get("active_media_tasks", 0),
            )
            return status_payload
        except MuseStudioConfigurationError:
            shutdown_owned_service()
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    shutdown_owned_service()
    raise MuseStudioServiceUnavailable(
        "Muse2API không sẵn sàng trong thời hạn cho phép."
    ) from last_error


def api_credentials() -> tuple[str, str]:
    """Return loopback endpoint and the in-memory key to the provider only."""

    with _lock:
        if _process is None or _process.poll() is not None or not _api_key:
            raise MuseStudioServiceUnavailable("Muse2API chưa chạy dưới quyền Nexus Studio Engine.")
        return MUSE_API_BASE_URL, _api_key


def status(*, probe: bool = False) -> dict:
    settings = muse_settings.load_muse_settings()
    configured_path = str(settings.get("muse_studio_path") or "")
    with _lock:
        process = _process
        running = bool(process is not None and process.poll() is None)
        owned = running and bool(_api_key)
        pid = int(process.pid) if running and process is not None else 0
    release_status = task_recovery_release_status()
    result = {
        "configured": False,
        "studio_path": configured_path,
        "running": running,
        "owned": owned,
        "pid": pid,
        "api_ready": False,
        "accounts": 0,
        "browser_running": False,
        "video_start_end_frames": 0,
        "task_recovery": 0,
        "task_recovery_released": bool(release_status.get("enabled")),
        "task_recovery_e2e_runs": int(release_status.get("successful_runs") or 0),
        "active_media_tasks": 0,
        "runtime_id": "",
        "api_url": MUSE_API_BASE_URL,
    }
    try:
        _validate_studio_path(configured_path)
        result["configured"] = True
    except MuseStudioConfigurationError:
        return result
    if probe and running and owned:
        try:
            result.update(_probe_owned_service(timeout=1.5))
        except Exception:
            pass
    return result


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(int(process.pid))],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.kill()


def shutdown_owned_service() -> None:
    """Stop only the Muse2API process created by this Nexus process."""

    global _process, _api_key, _instance_id, _studio_path
    _cancel_idle_timer()
    with _lock:
        process = _process
    if process is not None:
        try:
            # `_process` is assigned only from this manager's successful Popen call.
            # The live handle is stronger ownership evidence than a second CIM lookup,
            # which can fail and otherwise leave an orphan with an in-memory API key.
            if process.poll() is None:
                logger.info(
                    "Stopping owned Muse2API instance_id=%s pid=%s reason=shutdown_owned_service",
                    _instance_id,
                    getattr(process, "pid", 0),
                )
                if os.name == "nt":
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process)
        finally:
            process_registry.unregister_process(MUSE_SERVICE_PROCESS_KEY, process)
    with _lock:
        _process = None
        _api_key = ""
        _instance_id = ""
        _studio_path = None
        _close_log_handles()
        _remove_state()


def _idle_close_callback() -> None:
    global _idle_timer
    with _lock:
        _idle_timer = None
    if _has_active_muse_jobs() or _has_active_provider_tasks():
        schedule_idle_close()
        return
    shutdown_owned_service()


def schedule_idle_close() -> None:
    global _idle_timer
    with _lock:
        if _process is None or _process.poll() is not None:
            return
        if _idle_timer is not None:
            _idle_timer.cancel()
        _idle_timer = threading.Timer(MUSE_IDLE_CLOSE_SECONDS, _idle_close_callback)
        _idle_timer.daemon = True
        _idle_timer.start()


def acquire() -> dict:
    _cancel_idle_timer()
    return ensure_started()


def release() -> None:
    schedule_idle_close()


def verify() -> dict:
    acquire()
    try:
        return _probe_owned_service(timeout=5.0)
    finally:
        release()


def open_studio() -> dict:
    """Open the user-owned Muse Studio UI only while Nexus is idle."""

    studio_path = _validate_studio_path()
    with _lock:
        running = bool(_process is not None and _process.poll() is None)
    if running:
        raise MuseStudioServiceBusy(
            "Muse2API do Nexus Studio Engine đang chạy; hãy chờ service tự đóng sau thời gian rảnh."
        )
    if not _muse_ports_available(studio_path):
        raise MuseStudioServiceBusy(
            "Muse Studio hoặc Muse2API khác đang chạy; Nexus Studio Engine không mở thêm phiên mới."
        )
    if sys.platform != "win32":
        raise MuseStudioConfigurationError("Mở Muse Studio chỉ được hỗ trợ trên Windows.")
    python_executable = Path(sys.executable).resolve()
    if not python_executable.is_file():
        raise MuseStudioConfigurationError("Không tìm thấy Python runtime của Nexus.")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    try:
        with (
            open(MUSE_STUDIO_STDOUT_PATH, "a", encoding="utf-8") as stdout_handle,
            open(MUSE_STUDIO_STDERR_PATH, "a", encoding="utf-8") as stderr_handle,
        ):
            process = subprocess.Popen(
                [str(python_executable), str((studio_path / "main.py").resolve())],
                cwd=str(studio_path),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=creationflags,
            )
    except OSError as exc:
        logger.exception("Không thể khởi chạy giao diện Muse Studio.")
        raise MuseStudioServiceUnavailable("Không thể khởi chạy Muse Studio.") from exc

    try:
        return_code = process.wait(timeout=MUSE_STUDIO_STARTUP_CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        return {
            "success": True,
            "studio_path": str(studio_path),
            "launcher": python_executable.name,
            "pid": int(process.pid),
        }
    raise MuseStudioServiceUnavailable(
        f"Muse Studio dừng ngay khi khởi động (mã {return_code}); "
        f"xem log {MUSE_STUDIO_STDERR_PATH.name}."
    )


def shutdown_muse_resources() -> None:
    shutdown_owned_service()


def manual_start_service(*, timeout_seconds: float = 20.0) -> dict:
    """Manually start Muse2API service without an idle timeout."""
    ensure_started(timeout_seconds=timeout_seconds)
    _cancel_idle_timer()
    return status(probe=True)


def manual_stop_service() -> dict:
    """Manually stop the owned Muse2API service."""
    shutdown_owned_service()
    return status(probe=False)


def manual_restart_service(*, timeout_seconds: float = 20.0) -> dict:
    """Manually restart the owned Muse2API service cleanly."""
    shutdown_owned_service()
    time.sleep(1.0)
    ensure_started(timeout_seconds=timeout_seconds)
    _cancel_idle_timer()
    return status(probe=True)

