"""On-demand lifecycle manager for the local Muse AI Studio REST service."""

from __future__ import annotations

import json
import logging
import os
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

import auto_yt.services.database as db
from auto_yt.paths import DATA_DIR, LOGS_DIR
from auto_yt.services import muse_settings, process_registry


logger = logging.getLogger(__name__)

MUSE_API_HOST = "127.0.0.1"
MUSE_API_PORT = 18610
MUSE_CDP_PORT = 19210
MUSE_API_BASE_URL = f"http://{MUSE_API_HOST}:{MUSE_API_PORT}"
MUSE_IDLE_CLOSE_SECONDS = 60.0
MUSE_SERVICE_PROCESS_KEY = "muse-studio-service"
MUSE_SERVICE_STATE_PATH = DATA_DIR / "muse_studio_service.json"
MUSE_STUDIO_STDOUT_PATH = LOGS_DIR / "muse-studio-ui.current.stdout.log"
MUSE_STUDIO_STDERR_PATH = LOGS_DIR / "muse-studio-ui.current.stderr.log"
MUSE_STUDIO_STARTUP_CHECK_SECONDS = 1.5


class MuseStudioServiceError(RuntimeError):
    """Base lifecycle error for Muse AI Studio."""


class MuseStudioConfigurationError(MuseStudioServiceError):
    """Muse AI Studio is missing or has an incompatible layout."""


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


def _request_json(
    path: str,
    *,
    api_key: str = "",
    timeout: float = 3.0,
) -> dict:
    clean_path = "/" + str(path or "").lstrip("/")
    headers = {"Accept": "application/json", "User-Agent": "Auto_YT-Muse/1"}
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
        raise MuseStudioServiceUnavailable("Muse2API chưa có phiên do Auto_YT sở hữu.")
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
    return {
        "running": True,
        "owned": True,
        "api_ready": True,
        "accounts": int(ready.get("accounts") or 0),
        "browser_running": bool(ready.get("browser_running")),
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
    now = db.utc_now()
    for job in db.list_active_system_jobs("muse_media_generation"):
        status = str(job.get("status") or "").strip().lower()
        if status in {"queued", "running"}:
            return True
        if status == "retry_wait":
            next_retry_at = str(job.get("next_retry_at") or "").strip()
            if not next_retry_at or next_retry_at <= now:
                return True
    return False


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
        if _port_in_use(MUSE_API_PORT) or _port_in_use(MUSE_CDP_PORT):
            raise MuseStudioServiceBusy(
                "Cổng Muse 18610/19210 đang do tiến trình khác sử dụng; hãy đóng Muse Studio trước."
            )

        python_executable = Path(sys.executable).resolve()
        if not python_executable.is_file():
            raise MuseStudioConfigurationError("Không tìm thấy Python runtime của Auto_YT.")
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _stdout_handle = open(LOGS_DIR / "muse2api.current.stdout.log", "a", encoding="utf-8")
        _stderr_handle = open(LOGS_DIR / "muse2api.current.stderr.log", "a", encoding="utf-8")
        _api_key = "autoyt_muse_" + secrets.token_hex(32)
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
            return _probe_owned_service(timeout=2.0)
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
            raise MuseStudioServiceUnavailable("Muse2API chưa chạy dưới quyền Auto_YT.")
        return MUSE_API_BASE_URL, _api_key


def status(*, probe: bool = False) -> dict:
    settings = muse_settings.load_muse_settings()
    configured_path = str(settings.get("muse_studio_path") or "")
    with _lock:
        process = _process
        running = bool(process is not None and process.poll() is None)
        owned = running and bool(_api_key)
    result = {
        "configured": False,
        "studio_path": configured_path,
        "running": running,
        "owned": owned,
        "api_ready": False,
        "accounts": 0,
        "browser_running": False,
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
    """Stop only the Muse2API process created by this Auto_YT process."""

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
    if _has_active_muse_jobs():
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
    """Open the user-owned Muse Studio UI only while Auto_YT's service is idle."""

    studio_path = _validate_studio_path()
    with _lock:
        running = bool(_process is not None and _process.poll() is None)
    if running:
        raise MuseStudioServiceBusy(
            "Muse2API do Auto_YT đang chạy; hãy chờ service tự đóng sau thời gian rảnh."
        )
    if _port_in_use(MUSE_API_PORT) or _port_in_use(MUSE_CDP_PORT):
        raise MuseStudioServiceBusy(
            "Muse Studio hoặc Muse2API khác đang chạy; Auto_YT không mở thêm phiên mới."
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
