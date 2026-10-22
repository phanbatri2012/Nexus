"""Muse provider backed by the local Muse AI Studio REST service."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import mimetypes
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Callable, Protocol

import auto_yt.services.database as db
from auto_yt.services import muse_studio_service


MUSE_IMAGE_TIMEOUT_SECONDS = 360
MUSE_VIDEO_TIMEOUT_SECONDS = 900
MUSE_POLL_INTERVAL_SECONDS = 2.0
MUSE_PROVIDER_NAME = "muse_studio_api"


class MuseError(RuntimeError):
    """Base error for Muse operations."""


class MuseTransientError(MuseError):
    """The operation failed before submission or can be reconciled safely."""


class MuseAdmissionRetry(MuseTransientError):
    """Muse2API rejected the request before accepting a generation task."""


class MuseServiceBusy(MuseTransientError):
    """The local Muse ports are occupied or the owned service is busy."""


class MuseAuthenticationError(MuseError):
    """Muse AI Studio has no usable account or rejected local authentication."""


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
_AUTH_ERROR_TOKENS = ("auth", "login", "cookie", "session", "account")


def classify_muse_failure(message: str) -> type[MuseError]:
    """Classify provider failures without broad language-specific substrings."""

    lowered = str(message or "").casefold()
    if any(token in lowered for token in _REFUSAL_ERROR_TOKENS):
        return MuseRefusalError
    if any(token in lowered for token in _REFERENCE_ERROR_TOKENS):
        return MuseReferenceError
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
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict: ...

    async def create_video(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        request_hash: str = "",
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
    def image(prompt: str, *, has_references: bool) -> str:
        prefix = (
            "Use only the reference images attached to this request as canonical visual "
            "references. Preserve each referenced character's identity and appearance."
            if has_references
            else "Create a completely new image from text; do not use images from chat history."
        )
        return (
            f"{prefix}\nCreate one 16:9 widescreen image at 1920x1080. {prompt.strip()}\n"
            "Clean cinematic frame, no text, subtitles, watermark, signature, border, or logo."
        )

    @staticmethod
    def video(prompt: str, *, reference_count: int, duration_seconds: float = 10.0) -> str:
        mapping = "@Image1 is the required first frame for the video."
        if reference_count > 1:
            mapping += (
                " @Image2 through @Image%d are canonical character references; keep their "
                "identities separate and unchanged." % reference_count
            )
        return (
            f"{mapping}\nGenerate one 16:9 widescreen image-to-video clip at 1920x1080, "
            f"approximately {max(1, min(10, round(duration_seconds)))} seconds. {prompt.strip()}\n"
            "Natural cinematic motion, no text, subtitles, watermark, signature, border, or logo."
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise MuseError("Muse2API trả redirect; Auto_YT chỉ chấp nhận media từ localhost.")


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

    def __init__(self, *, job_id: str = "", video_id: int | None = None):
        self.job_id = job_id
        self.video_id = video_id

    @contextlib.asynccontextmanager
    async def open_session(self) -> AsyncGenerator["MuseStudioApiProvider", None]:
        try:
            await asyncio.to_thread(muse_studio_service.acquire)
        except muse_studio_service.MuseStudioServiceBusy as exc:
            raise MuseServiceBusy(str(exc)) from exc
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
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict:
        references = _validate_reference_paths(reference_paths, limit=6)
        payload = {
            "prompt": prompt.strip(),
            "model": "muse-image",
            "aspect_ratio": "16:9",
            "response_format": "url",
            "timeout": MUSE_IMAGE_TIMEOUT_SECONDS,
            "reference_image": [str(path) for path in references] or None,
        }
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
        reference_paths: list[Path],
        destination: Path,
        *,
        request_hash: str = "",
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict:
        references = _validate_reference_paths(reference_paths, limit=6)
        if not references:
            raise MuseReferenceError("Video Muse bắt buộc phải có ảnh scene làm @Image1.")
        payload = {
            "prompt": prompt.strip(),
            "model": "muse-video",
            "aspect_ratio": "16:9",
            "resolution": "1920x1080",
            "duration": 10,
            "timeout": MUSE_VIDEO_TIMEOUT_SECONDS,
            "reference_image": [str(path) for path in references],
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
        error = classify_muse_failure(message)(message)
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
    ) -> dict:
        base_url, api_key = muse_studio_service.api_credentials()
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme or parsed.netloc:
            raise MuseError("Muse2API endpoint phải là đường dẫn localhost tương đối.")
        path = "/" + endpoint.lstrip("/")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Auto_YT-Muse/1",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(base_url + path, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = _safe_error_body(exc) or f"Muse2API HTTP {exc.code}"
            if exc.code in {401, 403}:
                raise MuseAuthenticationError(detail) from exc
            if exc.code == 429:
                raise MuseAdmissionRetry(detail) from exc
            if exc.code in {400, 404, 409, 422}:
                raise MuseError(detail) from exc
            raise MuseTransientError(detail) from exc

    def _get_json(self, endpoint: str) -> dict:
        return self._request_json("GET", endpoint)

    def _post_json(self, endpoint: str, payload: dict, idempotency_key: str = "") -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._request_json("POST", endpoint, payload, headers)

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
                "User-Agent": "Auto_YT-Muse/1",
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
