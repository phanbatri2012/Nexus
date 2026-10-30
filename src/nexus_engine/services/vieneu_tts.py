"""Offline VieNeu-TTS v3 Turbo runtime and managed audio profiles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
from pathlib import Path
from typing import Any

from nexus_engine.paths import (
    PROJECT_ROOT,
    SHORT_VIDEO_AUDIO_PROFILES_DIR,
    VIENEU_TTS_SETTINGS_PATH,
)
from nexus_engine.services import process_registry, security_logging


ENV_HOME = "NEXUS_VIENEU_TTS_HOME"
LOCK_FILENAME = "runtime.lock.json"
SUPPORTED_PROVIDER = "vieneu_v3_turbo"
SUPPORTED_AUDIO_SUFFIXES = frozenset({".wav"})
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict[tuple[Any, ...], dict] = {}


class VieNeuRuntimeError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VieNeuRuntimeError(f"Không đọc được {path.name}.") from exc
    if not isinstance(value, dict):
        raise VieNeuRuntimeError(f"{path.name} phải là JSON object.")
    return value


def resolve_runtime_home() -> Path | None:
    configured = str(os.environ.get(ENV_HOME) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if not VIENEU_TTS_SETTINGS_PATH.is_file():
        return None
    settings = _read_json(VIENEU_TTS_SETTINGS_PATH)
    configured = str(settings.get("home") or "").strip()
    return Path(configured).expanduser().resolve() if configured else None


def revision_signature() -> tuple[tuple[str, int, int], ...]:
    paths: list[Path] = []
    if VIENEU_TTS_SETTINGS_PATH.is_file():
        paths.append(VIENEU_TTS_SETTINGS_PATH)
    home = resolve_runtime_home()
    if home is not None and (home / LOCK_FILENAME).is_file():
        paths.append(home / LOCK_FILENAME)
    if SHORT_VIDEO_AUDIO_PROFILES_DIR.is_dir():
        paths.extend(
            path
            for path in SHORT_VIDEO_AUDIO_PROFILES_DIR.rglob("*")
            if path.is_file()
        )
    return tuple(
        (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(paths, key=lambda value: str(value).casefold())
    )


def _resolve_inside(root: Path, relative: str) -> Path:
    value = Path(str(relative or ""))
    if not relative or value.is_absolute():
        raise VieNeuRuntimeError("Runtime lock chứa đường dẫn không hợp lệ.")
    root = root.resolve()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise VieNeuRuntimeError("Runtime lock chứa path traversal.") from exc
    return candidate


def _runtime_signature(home: Path, lock: dict, lock_path: Path) -> tuple[Any, ...]:
    values: list[Any] = [str(home), lock_path.stat().st_mtime_ns, lock_path.stat().st_size]
    for artifact in lock.get("artifacts") or []:
        try:
            path = _resolve_inside(home, str(artifact.get("path") or ""))
            stat = path.stat()
            values.extend((str(path), stat.st_mtime_ns, stat.st_size))
        except (OSError, VieNeuRuntimeError):
            values.extend((str(artifact), -1, -1))
    return tuple(values)


def runtime_status(*, verify_hashes: bool = True) -> dict:
    home = resolve_runtime_home()
    if home is None:
        return {
            "available": False,
            "code": "runtime_home_unconfigured",
            "detail": f"Chưa cấu hình {ENV_HOME} hoặc vieneu_tts_settings.json.",
        }
    lock_path = home / LOCK_FILENAME
    if not home.is_dir() or not lock_path.is_file():
        return {
            "available": False,
            "code": "runtime_lock_missing",
            "detail": "Thiếu runtime.lock.json của VieNeu-TTS.",
        }
    try:
        lock = _read_json(lock_path)
        if lock.get("engine") != SUPPORTED_PROVIDER:
            raise VieNeuRuntimeError("Runtime lock không thuộc VieNeu-TTS v3 Turbo.")
        if str(lock.get("backend") or "").casefold() != "onnx":
            raise VieNeuRuntimeError("Runtime lock không dùng ONNX.")
        if str(lock.get("precision") or "").casefold() != "fp32":
            raise VieNeuRuntimeError("Runtime lock không dùng FP32.")
        python_path = _resolve_inside(
            home,
            str(lock.get("python") or ".venv/Scripts/python.exe"),
        )
        if not python_path.is_file():
            raise VieNeuRuntimeError("Thiếu Python runtime VieNeu-TTS.")
        artifacts = lock.get("artifacts") or []
        if not isinstance(artifacts, list) or not artifacts:
            raise VieNeuRuntimeError("Runtime lock chưa có danh sách model artifacts.")
        signature = _runtime_signature(home, lock, lock_path)
        with _STATUS_LOCK:
            cached = _STATUS_CACHE.get(signature)
        if cached is not None and verify_hashes:
            return deepcopy(cached)
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise VieNeuRuntimeError("Runtime artifact không hợp lệ.")
            path = _resolve_inside(home, str(artifact.get("path") or ""))
            expected_size = int(artifact.get("size") or 0)
            expected_hash = str(artifact.get("sha256") or "").casefold()
            if not path.is_file() or path.stat().st_size != expected_size:
                raise VieNeuRuntimeError("Model artifact thiếu hoặc sai kích thước.")
            if verify_hashes and (not expected_hash or file_sha256(path) != expected_hash):
                raise VieNeuRuntimeError("Model artifact không khớp SHA-256.")
        status = {
            "available": True,
            "code": "ready",
            "detail": "VieNeu-TTS v3 Turbo ONNX CPU FP32 sẵn sàng (offline).",
            "runtime_revision": str(lock.get("runtime_revision") or ""),
            "package_version": str(lock.get("package_version") or ""),
            "model_revision": deepcopy(lock.get("model_revision") or {}),
            "lock_sha256": file_sha256(lock_path),
            "watermark_available": bool(lock.get("watermark_available")),
        }
        if not status["runtime_revision"] or not status["model_revision"]:
            raise VieNeuRuntimeError("Runtime lock thiếu revision.")
        if not status["watermark_available"]:
            raise VieNeuRuntimeError("Runtime chưa có audio watermark theo cấu hình.")
        if verify_hashes:
            with _STATUS_LOCK:
                _STATUS_CACHE.clear()
                _STATUS_CACHE[signature] = deepcopy(status)
        return status
    except (OSError, ValueError, VieNeuRuntimeError) as exc:
        return {
            "available": False,
            "code": "runtime_invalid",
            "detail": security_logging.redact_sensitive(exc),
        }


def profile_directory(project_key: str, profile_version: int) -> Path:
    key = str(project_key or "").strip().casefold()
    if not key or any(part in key for part in ("/", "\\", "..")):
        raise VieNeuRuntimeError("Project key Audio Profile không hợp lệ.")
    version = max(1, int(profile_version or 1))
    return SHORT_VIDEO_AUDIO_PROFILES_DIR / key / f"v{version}"


def managed_profile_status(
    project_key: str,
    profile_version: int,
    *,
    verify_runtime_hashes: bool = True,
) -> dict:
    runtime = runtime_status(verify_hashes=verify_runtime_hashes)
    directory = profile_directory(project_key, profile_version)
    result = {
        "available": False,
        "runtime": runtime,
        "reference_ready": False,
        "detail": "Audio Profile chưa sẵn sàng.",
    }
    try:
        metadata = _read_json(directory / "reference.json")
        checksums = _read_json(directory / "checksums.json")
        reference = directory / "reference.wav"
        transcript = directory / "source_transcript.srt"
        expected = checksums.get("files") or {}
        for path in (reference, transcript, directory / "reference.json"):
            expected_hash = str(expected.get(path.name) or "").casefold()
            if not path.is_file() or not expected_hash or file_sha256(path) != expected_hash:
                raise VieNeuRuntimeError("Audio Profile thiếu file hoặc sai SHA-256.")
        if reference.suffix.casefold() not in SUPPORTED_AUDIO_SUFFIXES:
            raise VieNeuRuntimeError("Reference audio không phải WAV.")
        if int(metadata.get("sample_rate") or 0) != 48000:
            raise VieNeuRuntimeError("Reference audio không phải 48 kHz.")
        if int(metadata.get("channels") or 0) != 1:
            raise VieNeuRuntimeError("Reference audio không phải mono.")
        result.update(
            {
                "available": bool(runtime.get("available")),
                "reference_ready": True,
                "detail": (
                    "Audio Profile và runtime sẵn sàng."
                    if runtime.get("available")
                    else runtime.get("detail") or "Runtime VieNeu chưa sẵn sàng."
                ),
                "reference_sha256": expected["reference.wav"],
                "reference_duration_seconds": float(metadata.get("duration_seconds") or 0),
                "metadata": {
                    "profile_id": str(metadata.get("profile_id") or ""),
                    "profile_version": int(metadata.get("profile_version") or profile_version),
                    "sample_rate": 48000,
                    "channels": 1,
                    "clip_start_seconds": float(metadata.get("clip_start_seconds") or 0),
                    "clip_end_seconds": float(metadata.get("clip_end_seconds") or 0),
                    "transcript": str(metadata.get("transcript") or ""),
                },
            }
        )
    except (OSError, ValueError, VieNeuRuntimeError) as exc:
        result["detail"] = security_logging.redact_sensitive(exc)
    return result


def snapshot_profile(
    *,
    project_key: str,
    profile: dict,
    item_dir: Path,
) -> dict:
    version = int(profile.get("profile_version") or 1)
    status = managed_profile_status(project_key, version)
    if not status.get("available"):
        raise VieNeuRuntimeError(str(status.get("detail") or "Audio Profile chưa sẵn sàng."))
    source = profile_directory(project_key, version)
    destination = Path(item_dir) / "references" / "audio_profile"
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".audio-profile-", dir=destination.parent))
        try:
            for name in ("reference.wav", "reference.json", "source_transcript.srt", "checksums.json"):
                shutil.copy2(source / name, temporary / name)
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    reference = destination / "reference.wav"
    expected_hash = str(status.get("reference_sha256") or "")
    if not reference.is_file() or file_sha256(reference) != expected_hash:
        raise VieNeuRuntimeError("Snapshot Audio Profile không toàn vẹn.")
    runtime = status.get("runtime") or {}
    return {
        **deepcopy(profile),
        "runtime_revision": runtime.get("runtime_revision") or "",
        "package_version": runtime.get("package_version") or "",
        "model_revision": deepcopy(runtime.get("model_revision") or {}),
        "runtime_lock_sha256": runtime.get("lock_sha256") or "",
        "reference": {
            **deepcopy(status.get("metadata") or {}),
            "path": reference.relative_to(item_dir).as_posix(),
            "sha256": expected_hash,
        },
    }


def profile_source_reference(project_key: str, profile_version: int) -> tuple[Path, dict]:
    status = managed_profile_status(project_key, profile_version)
    if not status.get("available"):
        raise VieNeuRuntimeError(str(status.get("detail") or "Audio Profile chưa sẵn sàng."))
    return profile_directory(project_key, profile_version) / "reference.wav", status


class VieNeuGenerationSession:
    def __init__(
        self,
        *,
        job_id: str,
        reference_path: Path,
        work_dir: Path,
        timeout_seconds: float = 900,
    ) -> None:
        self.job_id = str(job_id)
        self.reference_path = Path(reference_path).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.timeout_seconds = float(timeout_seconds)
        self.process: subprocess.Popen[str] | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._runtime_status: dict = {}

    def __enter__(self) -> "VieNeuGenerationSession":
        status = runtime_status()
        if not status.get("available"):
            raise VieNeuRuntimeError(
                str(status.get("detail") or "Runtime VieNeu chưa sẵn sàng.")
            )
        home = resolve_runtime_home()
        if home is None:
            raise VieNeuRuntimeError("Runtime VieNeu chưa được cấu hình.")
        lock = _read_json(home / LOCK_FILENAME)
        python_path = _resolve_inside(
            home,
            str(lock.get("python") or ".venv/Scripts/python.exe"),
        )
        runner = PROJECT_ROOT / "scripts" / "vieneu_tts_runner.py"
        if not runner.is_file():
            raise VieNeuRuntimeError("Thiếu VieNeu subprocess runner.")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        request_path = self.work_dir / ".vieneu-session.json"
        request = {
            "reference_path": str(self.reference_path),
            "backbone_dir": str((home / "models" / "vieneu-v3-turbo").resolve()),
            "onnx_dir": str(
                (home / "models" / "vieneu-v3-turbo" / "onnx_update").resolve()
            ),
            "codec_dir": str((home / "models" / "moss-audio-tokenizer").resolve()),
            "cache_dir": str((home / "cache").resolve()),
            "backend": "onnx",
            "precision": "fp32",
            "sample_rate": 48000,
            "use_ref_codes": True,
            "denoise": False,
            "apply_watermark": True,
        }
        temporary = request_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, request_path)
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "HF_HOME": str((home / "cache").resolve()),
                "HF_HUB_CACHE": str((home / "cache" / "hub").resolve()),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
        self.process = subprocess.Popen(
            [str(python_path), str(runner), "--session", str(request_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            cwd=str(home),
        )
        process_registry.register_process(self.job_id, self.process)
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._runtime_status = status
        ready = self._read_response(timeout=max(300.0, self.timeout_seconds))
        if ready.get("status") != "ready":
            self.close()
            raise VieNeuRuntimeError("VieNeu-TTS subprocess không vào trạng thái ready.")
        return self

    def _read_response(self, *, timeout: float) -> dict:
        process = self.process
        executor = self._executor
        if process is None or process.stdout is None or executor is None:
            raise VieNeuRuntimeError("VieNeu-TTS session chưa khởi động.")
        deadline = time.monotonic() + max(1.0, float(timeout))
        ignored_lines = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise VieNeuRuntimeError("VieNeu-TTS vượt thời gian xử lý candidate.")
            future = executor.submit(process.stdout.readline)
            try:
                line = future.result(timeout=remaining)
            except FutureTimeoutError as exc:
                process.kill()
                raise VieNeuRuntimeError(
                    "VieNeu-TTS vượt thời gian xử lý candidate."
                ) from exc
            if not line:
                raise VieNeuRuntimeError("VieNeu-TTS subprocess kết thúc bất thường.")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                ignored_lines += 1
                if ignored_lines > 500:
                    process.kill()
                    raise VieNeuRuntimeError(
                        "VieNeu-TTS ghi quá nhiều log ngoài giao thức JSON."
                    )
                continue
            if not isinstance(payload, dict) or "status" not in payload:
                ignored_lines += 1
                continue
            if payload.get("status") == "error":
                raise VieNeuRuntimeError(
                    str(payload.get("error") or "VieNeu-TTS tạo candidate lỗi.")
                )
            return payload

    def generate(self, *, text: str, seed: int, output_path: Path) -> dict:
        process = self.process
        if process is None or process.stdin is None:
            raise VieNeuRuntimeError("VieNeu-TTS session chưa khởi động.")
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path = output_path.with_suffix(".receipt.json")
        command = {
            "op": "generate",
            "text": str(text or ""),
            "seed": int(seed),
            "output_path": str(output_path),
            "receipt_path": str(receipt_path),
        }
        process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
        process.stdin.flush()
        receipt = self._read_response(timeout=self.timeout_seconds)
        if not output_path.is_file() or not receipt_path.is_file():
            raise VieNeuRuntimeError("VieNeu-TTS không ghi đủ candidate/receipt.")
        receipt["request_hash"] = hashlib.sha256(
            json.dumps(
                {
                    "text": command["text"],
                    "seed": command["seed"],
                    "reference_sha256": file_sha256(self.reference_path),
                    "runtime_lock_sha256": self._runtime_status.get("lock_sha256"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return receipt

    def close(self) -> None:
        process = self.process
        if process is not None:
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write('{"op":"close"}\n')
                    process.stdin.flush()
                    process.wait(timeout=20)
            except Exception:
                if process.poll() is None:
                    process.kill()
            finally:
                process_registry.unregister_process(self.job_id, process)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        self.process = None
        self._executor = None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def run_candidate(
    *,
    job_id: str,
    text: str,
    seed: int,
    reference_path: Path,
    output_path: Path,
    timeout_seconds: float = 900,
) -> dict:
    with VieNeuGenerationSession(
        job_id=job_id,
        reference_path=reference_path,
        work_dir=Path(output_path).parent,
        timeout_seconds=timeout_seconds,
    ) as session:
        return session.generate(text=text, seed=seed, output_path=output_path)
