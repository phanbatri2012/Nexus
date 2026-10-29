"""Muse provider backed by the local Muse AI Studio REST service."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import mimetypes
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Callable, Protocol

import nexus_engine.services.database as db
from nexus_engine.services import muse_studio_service


MUSE_IMAGE_TIMEOUT_SECONDS = 360
MUSE_VIDEO_TIMEOUT_SECONDS = 900
MUSE_ADMISSION_TIMEOUT_SECONDS = 300
MUSE_POLL_INTERVAL_SECONDS = 2.0
MUSE_PROVIDER_NAME = "muse_studio_api"
MUSE_FRAME_CONTRACT_VERSION = "start_end_v1"


class MuseError(RuntimeError):
    """Base error for Muse operations."""


class MuseTransientError(MuseError):
    """The operation failed before submission or can be reconciled safely."""


class MuseTaskTransientFailure(MuseTransientError):
    """Muse confirmed that a task ended for a retryable provider-side reason."""


class MuseTaskWaitLimitExceeded(MuseTaskTransientFailure):
    """Muse exhausted its provider-side retry/wait budget for the task."""


class MuseServiceInterrupted(MuseTaskTransientFailure):
    """Muse2API restarted after accepting the task."""


class MuseAdmissionRetry(MuseTransientError):
    """Muse2API rejected the request before accepting a generation task."""


class MuseQuotaUnavailable(MuseError):
    """All Muse media accounts have confirmed exhausted capacity."""


class MuseServiceBusy(MuseTransientError):
    """The local Muse ports are occupied or the owned service is busy."""


class MuseAuthenticationError(MuseError):
    """Muse AI Studio has no usable account or rejected local authentication."""


class MuseRequestError(MuseError):
    """Muse2API rejected a caller-controlled request before doing work."""


class MuseRefusalError(MuseError):
    """Muse refused the prompt instead of producing media."""


class MuseReferenceError(MuseError):
    """Reference files are missing or were rejected by Muse AI Studio."""


class MuseAmbiguousSubmissionError(MuseError):
    """A request may have consumed credit but has no recoverable task id."""


_REFUSAL_ERROR_TOKENS = (
    "refus",
    "safety",
    "policy",
    "blocked",
    "模型未生成媒体",
    "仅返回文本",
    "chưa tạo được",
    "không thể tạo",
    "tôi không thể",
    "mình không thể",
)
_REFERENCE_ERROR_TOKENS = (
    "reference",
    "attachment",
    "upload",
    "ảnh tham chiếu",
    "ảnh đính kèm",
    "tệp ảnh",
    "không đọc được ảnh",
)
_AUTH_ERROR_TOKENS = (
    "auth",
    "login",
    "cookie",
    "session",
    "account",
    "relogin",
    "đăng nhập lại",
    "hết hạn session",
    "hết hạn cookie",
    "chưa đăng nhập",
    "không có tài khoản",
    "phiên đăng nhập",
    "重新登录",
    "未找到可用媒体账号",
    "没有可用账号",
    "会话不可用",
)
_QUOTA_ERROR_TOKENS = (
    "media_quota_exhausted",
    "quota_exhausted",
    "账号额度不足",
    "媒体额度已耗尽",
    "媒体额度均已耗尽",
    "账号额度已耗尽",
    "账号额度均已耗尽",
    "out of credits",
    "quota exceeded",
    "token limit",
    "hết quota",
    "hết hạn mức",
    "cạn hạn mức",
    "hết lượt tạo",
)
_TASK_WAIT_LIMIT_ERROR_TOKENS = (
    "total task wait limit reached",
    "total wait limit reached",
    "任务总等待时限已到",
    "总等待时限已到",
    "đã hết tổng thời gian chờ",
    "đạt giới hạn tổng thời gian chờ",
)
_TRANSIENT_TASK_ERROR_TOKENS = (
    "timeout",
    "timed out",
    "time out",
    "temporarily unavailable",
    "temporary failure",
    "service unavailable",
    "server busy",
    "rate limit",
    "too many requests",
    "等待生成超时",
    "生成超时",
    "未出现新的生成结果",
    "暂时不可用",
    "服务器繁忙",
    "hết thời gian chờ",
    "quá thời gian chờ",
    "tạm thời không khả dụng",
    "máy chủ đang bận",
    "服务重启中断了任务",
)


def classify_muse_failure(message: str) -> type[MuseError]:
    """Classify provider failures without broad language-specific substrings."""

    lowered = str(message or "").casefold()
    if "服务重启中断了任务" in lowered:
        return MuseServiceInterrupted
    if any(token in lowered for token in _QUOTA_ERROR_TOKENS):
        return MuseQuotaUnavailable
    if any(token in lowered for token in _REFUSAL_ERROR_TOKENS):
        return MuseRefusalError
    if any(token in lowered for token in _REFERENCE_ERROR_TOKENS):
        return MuseReferenceError
    if any(token in lowered for token in _TASK_WAIT_LIMIT_ERROR_TOKENS):
        return MuseTaskWaitLimitExceeded
    if any(token in lowered for token in _TRANSIENT_TASK_ERROR_TOKENS):
        return MuseTaskTransientFailure
    if any(token in lowered for token in _AUTH_ERROR_TOKENS):
        return MuseAuthenticationError
    return MuseError


@dataclass(frozen=True)
class MuseReceipt:
    provider_task_id: str
    endpoint: str
    submitted_at: str
    provider: str = MUSE_PROVIDER_NAME


class MuseProvider(Protocol):
    async def create_image(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        request_hash: str = "",
        aspect_ratio: str = "16:9",
        resolution: str = "",
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict: ...

    async def create_video(
        self,
        prompt: str,
        start_frame_path: Path,
        end_frame_path: Path,
        destination: Path,
        *,
        request_hash: str = "",
        aspect_ratio: str = "16:9",
        resolution: str = "1920x1080",
        duration_seconds: float = 10.0,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict: ...

    async def reconcile(
        self,
        *,
        receipt: MuseReceipt,
        destination: Path,
        expected: str,
    ) -> dict | None: ...


class MusePromptBuilder:
    @staticmethod
    def image(
        prompt: str,
        *,
        has_references: bool,
        aspect_ratio: str = "16:9",
        resolution: str = "1920x1080",
    ) -> str:
        prefix = (
            "Use only the reference images attached to this request as canonical visual "
            "references. Preserve each referenced character's identity and appearance."
            if has_references
            else "Create a completely new image from text; do not use images from chat history."
        )
        framing = "vertical portrait" if aspect_ratio == "9:16" else "widescreen"
        return (
            f"{prefix}\nCreate one {aspect_ratio} {framing} image at {resolution}. "
            f"{prompt.strip()}\n"
            "Clean cinematic frame, no text, subtitles, watermark, signature, border, or logo."
        )

    @staticmethod
    def video(
        prompt: str,
        *,
        duration_seconds: float = 10.0,
        aspect_ratio: str = "16:9",
        resolution: str = "1920x1080",
    ) -> str:
        mapping = (
            "@Image1 is the required first frame and @Image2 is the required final frame. "
            "Create one continuous, physically coherent transition from @Image1 to @Image2. "
            "Do not reinterpret either endpoint as a character or turnaround reference."
        )
        framing = "vertical portrait" if aspect_ratio == "9:16" else "widescreen"
        return (
            f"{mapping}\nGenerate one {aspect_ratio} {framing} image-to-video clip "
            f"at {resolution}, "
            f"approximately {max(1, min(10, round(duration_seconds)))} seconds. {prompt.strip()}\n"
            "Natural cinematic motion, no text, subtitles, watermark, signature, border, or logo."
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise MuseError("Muse2API trả redirect; Nexus Studio Engine chỉ chấp nhận media từ localhost.")


def _safe_error_body(error: urllib.error.HTTPError) -> str:
    try:
        raw = error.read(4096).decode("utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            return str(data.get("detail") or data.get("error") or "")[:1000]
        return str(data)[:1000]
    except Exception:
        return str(getattr(error, "reason", "") or "")[:1000]


def _is_connection_refused(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ConnectionRefusedError) or (
        isinstance(reason, OSError) and getattr(reason, "winerror", None) in {10061, 10060}
    )


def validate_account_id(account_id: str) -> str:
    normalized = str(account_id or "").strip()
    if not normalized or len(normalized) > 64 or not all(
        character.isalnum() or character in {"-", "_"}
        for character in normalized
    ):
        raise ValueError("Mã tài khoản Muse không hợp lệ.")
    return normalized


def refresh_account_quota(account_id: str) -> dict:
    """Refresh one account's quota through the authenticated loopback service."""

    normalized = validate_account_id(account_id)
    endpoint = f"/admin/accounts/{urllib.parse.quote(normalized, safe='')}/quota"
    return MuseStudioApiProvider._request_json(
        "POST",
        endpoint,
        timeout_seconds=90.0,
    )


def relogin_account(account_id: str, credentials: dict) -> dict:
    """Replace one Muse session through the authenticated loopback service."""

    normalized = validate_account_id(account_id)
    endpoint = f"/admin/accounts/{urllib.parse.quote(normalized, safe='')}/relogin"
    return MuseStudioApiProvider._request_json(
        "POST",
        endpoint,
        payload={
            "email": str(credentials.get("email") or ""),
            "method": str(credentials.get("method") or ""),
            "password": str(credentials.get("password") or ""),
            "two_factor_secret": str(credentials.get("two_factor_secret") or ""),
            "mailbox_url": str(credentials.get("mailbox_url") or ""),
        },
        timeout_seconds=210.0,
    )


def _validate_reference_paths(paths: list[Path], *, limit: int) -> list[Path]:
    if len(paths) > limit:
        raise MuseReferenceError(f"Muse chỉ nhận tối đa {limit} ảnh reference cho request này.")
    resolved: list[Path] = []
    for path in paths:
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            raise MuseReferenceError(f"Ảnh reference Muse không tồn tại hoặc rỗng: {candidate.name}")
        resolved.append(candidate)
    return resolved


def _idempotency_key(prompt: str, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(prompt.encode("utf-8"))
    for path in paths:
        digest.update(str(path).encode("utf-8"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


class MuseStudioApiProvider:
    """Job-scoped adapter around Muse AI Studio's unchanged local API."""

    def __init__(
        self,
        *,
        job_id: str = "",
        video_id: int | None = None,
        heartbeat: Callable[[dict], None] | None = None,
    ):
        self.job_id = job_id
        self.video_id = video_id
        self.capacity_preflight: dict = {}
        self._heartbeat = heartbeat
        self._heartbeat_context: dict = {}
        self._last_heartbeat_at = time.monotonic()

    def set_heartbeat_context(self, *, phase: str, scene_index: int) -> None:
        self._heartbeat_context = {
            "phase": str(phase),
            "scene_index": int(scene_index),
        }
        self._last_heartbeat_at = time.monotonic()

    def set_heartbeat_callback(self, heartbeat: Callable[[dict], None] | None) -> None:
        self._heartbeat = heartbeat

    def _emit_heartbeat(self, task: dict) -> None:
        if self._heartbeat is None:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_at < 10.0:
            return
        self._last_heartbeat_at = now
        payload = {
            **self._heartbeat_context,
            "task_status": str(task.get("status") or ""),
            "progress": max(0, min(100, int(task.get("progress") or 0))),
        }
        self._heartbeat(payload)

    def _capture_media_capacity(self, response: dict) -> None:
        capacity = response.get("media_capacity")
        if not isinstance(capacity, dict):
            return
        try:
            self.capacity_preflight = {
                "status": str(capacity.get("status") or ""),
                "eligible": int(capacity.get("eligible") or 0),
                "unknown": int(capacity.get("unknown") or 0),
                "exhausted": int(capacity.get("exhausted") or 0),
                "skipped": int(capacity.get("skipped") or 0),
                "cache_hit": bool(capacity.get("cache_hit")),
                "checked_at": int(capacity.get("checked_at") or 0),
                "elapsed_seconds": float(capacity.get("elapsed_seconds") or 0.0),
            }
        except (TypeError, ValueError):
            self.capacity_preflight = {}

    @contextlib.asynccontextmanager
    async def open_session(self) -> AsyncGenerator["MuseStudioApiProvider", None]:
        try:
            await asyncio.to_thread(muse_studio_service.acquire)
        except muse_studio_service.MuseStudioServiceBusy as exc:
            raise MuseServiceBusy(str(exc)) from exc
        except muse_studio_service.MuseStudioCapabilityDisabled:
            raise
        except muse_studio_service.MuseStudioConfigurationError as exc:
            raise MuseAuthenticationError(str(exc)) from exc
        except muse_studio_service.MuseStudioServiceUnavailable as exc:
            raise MuseTransientError(str(exc)) from exc
        except muse_studio_service.MuseStudioServiceError as exc:
            raise MuseError(str(exc)) from exc
        try:
            yield self
        finally:
            muse_studio_service.release()

    async def verify(self) -> dict:
        try:
            return await asyncio.to_thread(muse_studio_service.verify)
        except muse_studio_service.MuseStudioServiceBusy as exc:
            raise MuseServiceBusy(str(exc)) from exc
        except muse_studio_service.MuseStudioCapabilityDisabled:
            raise
        except muse_studio_service.MuseStudioConfigurationError as exc:
            raise MuseAuthenticationError(str(exc)) from exc
        except muse_studio_service.MuseStudioServiceUnavailable as exc:
            raise MuseTransientError(str(exc)) from exc

    async def create_image(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        request_hash: str = "",
        aspect_ratio: str = "16:9",
        resolution: str = "",
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict:
        references = _validate_reference_paths(reference_paths, limit=6)
        payload = {
            "prompt": prompt.strip(),
            "model": "muse-image",
            "aspect_ratio": aspect_ratio,
            "response_format": "url",
            "timeout": MUSE_IMAGE_TIMEOUT_SECONDS,
            "reference_image": [str(path) for path in references] or None,
        }
        if resolution:
            payload["resolution"] = resolution
        return await self._submit_and_wait(
            submit_endpoint="/v1/images/tasks",
            status_prefix="/v1/images/tasks",
            payload=payload,
            destination=destination,
            expected="image",
            timeout_seconds=MUSE_IMAGE_TIMEOUT_SECONDS,
            idempotency_key=request_hash or _idempotency_key(prompt, references),
            on_submitted=on_submitted,
            cancel_check=cancel_check,
        )

    async def create_video(
        self,
        prompt: str,
        start_frame_path: Path,
        end_frame_path: Path,
        destination: Path,
        *,
        request_hash: str = "",
        aspect_ratio: str = "16:9",
        resolution: str = "1920x1080",
        duration_seconds: float = 10.0,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict:
        references = _validate_reference_paths(
            [start_frame_path, end_frame_path],
            limit=2,
        )
        if len(references) != 2:
            raise MuseReferenceError("Video Muse bắt buộc phải có đủ ảnh đầu và ảnh cuối.")
        payload = {
            "prompt": prompt.strip(),
            "model": "muse-video",
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "duration": max(1, min(10, round(duration_seconds))),
            "timeout": MUSE_VIDEO_TIMEOUT_SECONDS,
            "start_image": str(references[0]),
            "end_image": str(references[1]),
        }
        return await self._submit_and_wait(
            submit_endpoint="/v1/videos",
            status_prefix="/v1/videos",
            payload=payload,
            destination=destination,
            expected="video",
            timeout_seconds=MUSE_VIDEO_TIMEOUT_SECONDS,
            idempotency_key=request_hash or _idempotency_key(prompt, references),
            on_submitted=on_submitted,
            cancel_check=cancel_check,
        )

    async def reconcile(
        self,
        *,
        receipt: MuseReceipt,
        destination: Path,
        expected: str,
    ) -> dict | None:
        if receipt.provider != MUSE_PROVIDER_NAME or not receipt.provider_task_id:
            raise MuseAmbiguousSubmissionError(
                "Receipt thuộc Muse browser cũ; hãy dùng Recreate để tạo lại artifact Muse."
            )
        try:
            task = await asyncio.to_thread(self._get_json, receipt.endpoint)
        except MuseError:
            raise
        except Exception as exc:
            raise MuseTransientError(
                "Mất kết nối khi đối soát task Muse2API; không gửi lại generation."
            ) from exc
        status = str(task.get("status") or "").strip().lower()
        self._emit_heartbeat(task)
        if status in {"queued", "processing", "pending", "running"}:
            return None
        if status in {"failed", "error", "cancelled", "canceled"}:
            self._raise_task_failure(task)
        if status not in {"completed", "success", "succeeded", "done"}:
            raise MuseError(f"Muse2API trả trạng thái task không hỗ trợ: {status or 'empty'}")
        result = await asyncio.to_thread(self._download_completed_task, task, destination, expected)
        return {
            **result,
            "provider": receipt.provider,
            "provider_task_id": receipt.provider_task_id,
            "endpoint": receipt.endpoint,
            "submitted_at": receipt.submitted_at,
            "provider_elapsed_seconds": task.get("elapsed"),
        }

    async def _submit_and_wait(
        self,
        *,
        submit_endpoint: str,
        status_prefix: str,
        payload: dict,
        destination: Path,
        expected: str,
        timeout_seconds: int,
        idempotency_key: str,
        on_submitted: Callable[[MuseReceipt], None] | None,
        cancel_check: Callable[[], None] | None,
    ) -> dict:
        try:
            submitted = await asyncio.to_thread(
                self._post_json,
                submit_endpoint,
                payload,
                idempotency_key,
                MUSE_ADMISSION_TIMEOUT_SECONDS,
            )
        except MuseAdmissionRetry:
            raise
        except MuseAuthenticationError:
            raise
        except MuseTransientError as exc:
            if expected == "image" or idempotency_key:
                raise
            raise MuseAmbiguousSubmissionError(
                "POST video Muse2API có thể đã được nhận nhưng không trả task_id; không tự gửi lại."
            ) from exc
        except MuseError:
            raise
        except Exception as exc:
            if expected == "image" or idempotency_key or _is_connection_refused(exc):
                raise MuseTransientError("Không thể kết nối Muse2API trước khi nhận task.") from exc
            raise MuseAmbiguousSubmissionError(
                "POST video Muse2API mất kết nối trước khi nhận task_id; không tự gửi lại."
            ) from exc
        task_id = str(submitted.get("task_id") or submitted.get("id") or "").strip()
        self._capture_media_capacity(submitted)
        if not task_id:
            if expected == "video" and not idempotency_key:
                raise MuseAmbiguousSubmissionError(
                    "Muse2API đã nhận POST video nhưng không trả task_id; không tự gửi lại."
                )
            raise MuseTransientError(
                "Muse2API không trả task_id; sẽ retry an toàn bằng Idempotency-Key."
            )
        receipt = MuseReceipt(
            provider_task_id=task_id,
            endpoint=f"{status_prefix}/{urllib.parse.quote(task_id, safe='')}",
            submitted_at=db.utc_now(),
        )
        if on_submitted:
            on_submitted(receipt)
        return await self._wait_for_task(
            receipt=receipt,
            destination=destination,
            expected=expected,
            timeout_seconds=timeout_seconds,
            cancel_check=cancel_check,
        )

    async def _wait_for_task(
        self,
        *,
        receipt: MuseReceipt,
        destination: Path,
        expected: str,
        timeout_seconds: int,
        cancel_check: Callable[[], None] | None,
    ) -> dict:
        deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
        while asyncio.get_running_loop().time() < deadline:
            if cancel_check:
                cancel_check()
            try:
                task = await asyncio.to_thread(self._get_json, receipt.endpoint)
            except (MuseAuthenticationError, MuseError):
                raise
            except Exception as exc:
                raise MuseTransientError(
                    "Mất kết nối khi đang theo dõi task Muse2API; sẽ reconcile task cũ."
                ) from exc
            status = str(task.get("status") or "").strip().lower()
            self._emit_heartbeat(task)
            if status in {"completed", "success", "succeeded", "done"}:
                result = await asyncio.to_thread(
                    self._download_completed_task, task, destination, expected
                )
                return {
                    **result,
                    "provider": receipt.provider,
                    "provider_task_id": receipt.provider_task_id,
                    "endpoint": receipt.endpoint,
                    "submitted_at": receipt.submitted_at,
                    "provider_elapsed_seconds": task.get("elapsed"),
                }
            if status in {"failed", "error", "cancelled", "canceled"}:
                self._raise_task_failure(task)
            if status not in {"queued", "processing", "pending", "running"}:
                raise MuseError(f"Muse2API trả trạng thái task không hỗ trợ: {status or 'empty'}")
            await asyncio.sleep(MUSE_POLL_INTERVAL_SECONDS)
        raise MuseTransientError("Task Muse2API chưa hoàn tất trước timeout; sẽ reconcile task cũ.")

    @staticmethod
    def _raise_task_failure(task: dict) -> None:
        message = str(task.get("error") or "Muse2API generation thất bại.")[:1000]
        error_code = str(task.get("error_code") or "").strip().lower()
        retryable = bool(task.get("retryable"))
        if error_code == "service_restart_interrupted":
            error = MuseServiceInterrupted(message)
        elif error_code in {"authentication_failed", "auth_failed"}:
            error = MuseAuthenticationError(message)
        elif error_code in {"media_quota_exhausted", "quota_exhausted"}:
            error = MuseQuotaUnavailable(message)
        elif retryable or error_code in {"timeout", "transient_failure"}:
            error = MuseTaskTransientFailure(message)
        else:
            error = classify_muse_failure(message)(message)
        error.error_code = error_code
        error.retryable = retryable
        error.runtime_id = str(task.get("runtime_id") or "")
        capacity = task.get("media_capacity")
        if isinstance(error, MuseQuotaUnavailable) and isinstance(capacity, dict):
            error.capacity_preflight = {
                "status": str(capacity.get("status") or "exhausted"),
                "eligible": int(capacity.get("eligible") or 0),
                "unknown": int(capacity.get("unknown") or 0),
                "exhausted": int(capacity.get("exhausted") or 0),
                "skipped": int(capacity.get("skipped") or 0),
                "cache_hit": True,
                "checked_at": int(task.get("updated_at") or 0),
                "elapsed_seconds": 0.0,
            }
        try:
            error.provider_elapsed_seconds = float(task.get("elapsed") or 0)
        except (TypeError, ValueError):
            error.provider_elapsed_seconds = 0.0
        raise error

    @staticmethod
    def _request_json(
        method: str,
        endpoint: str,
        payload: dict | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict:
        base_url, api_key = muse_studio_service.api_credentials()
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme or parsed.netloc:
            raise MuseError("Muse2API endpoint phải là đường dẫn localhost tương đối.")
        path = "/" + endpoint.lstrip("/")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Nexus-Studio-Engine/1",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(base_url + path, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = _safe_error_body(exc) or f"Muse2API HTTP {exc.code}"
            if exc.code in {401, 403}:
                raise MuseAuthenticationError(detail) from exc
            if exc.code == 429:
                if classify_muse_failure(detail) is MuseQuotaUnavailable:
                    raise MuseQuotaUnavailable(detail) from exc
                raise MuseAdmissionRetry(detail) from exc
            if exc.code == 409:
                raise MuseServiceBusy(detail) from exc
            if exc.code in {400, 404, 422}:
                raise MuseRequestError(detail) from exc
            raise MuseTransientError(detail) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            raise MuseTransientError("Không thể kết nối ổn định tới Muse2API.") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MuseTransientError("Muse2API trả JSON không hợp lệ.") from exc

    def _get_json(self, endpoint: str) -> dict:
        return self._request_json("GET", endpoint)

    def _post_json(
        self,
        endpoint: str,
        payload: dict,
        idempotency_key: str = "",
        timeout_seconds: float = 20.0,
    ) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._request_json(
            "POST", endpoint, payload, headers, timeout_seconds
        )

    @staticmethod
    def _media_url(task: dict) -> str:
        direct = str(task.get("url") or "").strip()
        if direct:
            return direct
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        if result.get("url"):
            return str(result["url"])
        video = task.get("video") if isinstance(task.get("video"), dict) else {}
        if video.get("url"):
            return str(video["url"])
        data = task.get("data") if isinstance(task.get("data"), list) else []
        if data and isinstance(data[0], dict) and data[0].get("url"):
            return str(data[0]["url"])
        return ""

    def _download_completed_task(self, task: dict, destination: Path, expected: str) -> dict:
        media_url = self._media_url(task)
        if not media_url:
            raise MuseError("Task Muse2API hoàn tất nhưng không có media URL.")
        base_url, api_key = muse_studio_service.api_credentials()
        absolute = urllib.parse.urljoin(base_url + "/", media_url)
        parsed = urllib.parse.urlsplit(absolute)
        if (
            parsed.scheme != "http"
            or parsed.hostname != muse_studio_service.MUSE_API_HOST
            or parsed.port != muse_studio_service.MUSE_API_PORT
            or not parsed.path.startswith("/v1/media/")
        ):
            raise MuseError("Muse2API trả media URL ngoài localhost; đã từ chối tải.")
        request = urllib.request.Request(
            absolute,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Nexus-Studio-Engine/1",
            },
            method="GET",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        destination.parent.mkdir(parents=True, exist_ok=True)
        target: Path | None = None
        try:
            with opener.open(request, timeout=120) as response:
                content_type = str(response.headers.get_content_type() or "").lower()
                extension = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
                if not extension:
                    extension = mimetypes.guess_extension(content_type) or (
                        ".mp4" if expected == "video" else ".png"
                    )
                target = destination.with_suffix(extension)
                total = 0
                max_bytes = 2 * 1024 * 1024 * 1024 if expected == "video" else 100 * 1024 * 1024
                with target.open("wb") as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise MuseError("Media Muse2API vượt giới hạn kích thước an toàn.")
                        stream.write(chunk)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            if target is not None:
                target.unlink(missing_ok=True)
            raise MuseTransientError("Mất kết nối khi tải media từ Muse2API.") from exc
        except Exception:
            if target is not None:
                target.unlink(missing_ok=True)
            raise
        if target is None:
            raise MuseError("Muse2API không tạo được file media đích.")
        video_extensions = {".mp4", ".webm", ".mov"}
        image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
        if not target.is_file() or target.stat().st_size <= 0:
            raise MuseError("Muse2API trả media rỗng.")
        if expected == "video" and target.suffix.lower() not in video_extensions:
            target.unlink(missing_ok=True)
            raise MuseError("Muse2API trả ảnh trong khi scene yêu cầu video.")
        if expected == "image" and target.suffix.lower() not in image_extensions:
            target.unlink(missing_ok=True)
            raise MuseError("Muse2API trả sai loại media cho scene ảnh.")
        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "mime_type": content_type or mimetypes.guess_type(target.name)[0] or "",
            "download_method": "muse_studio_api",
            "checksum": _sha256_file(target),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shutdown_muse_resources() -> None:
    muse_studio_service.shutdown_muse_resources()
