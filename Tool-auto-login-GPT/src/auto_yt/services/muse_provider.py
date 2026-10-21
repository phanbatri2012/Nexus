"""Native Muse browser provider running inside the assigned GPM profile."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import mimetypes
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Protocol

import auto_yt.services.database as db
from auto_yt.services import (
    browser_diagnostics,
    channel_scanner_service,
    gpm_service,
    muse_settings,
)
from auto_yt.services.proxy_utils import ProxyConfigurationError, parse_proxy_url


logger = logging.getLogger(__name__)
MUSE_URL = "https://muse.ai/thread/new"
MUSE_IDLE_CLOSE_SECONDS = 60.0
MUSE_IMAGE_TIMEOUT_SECONDS = 360
MUSE_VIDEO_TIMEOUT_SECONDS = 900


class MuseError(RuntimeError):
    """Base error for Muse operations."""


class MuseTransientError(MuseError):
    """A request may be retried without user intervention."""


class MuseProfileBusy(MuseTransientError):
    """The configured GPM profile is currently used by another operation."""


class MuseAuthenticationError(MuseError):
    """The GPM browser is not logged into Muse."""


class MuseRefusalError(MuseError):
    """Muse refused the prompt instead of producing media."""


class MuseReferenceError(MuseError):
    """Reference files are missing or were not accepted by the composer."""


class MuseAmbiguousSubmissionError(MuseError):
    """A request may have consumed credit but has no recoverable receipt."""


@dataclass(frozen=True)
class MuseReceipt:
    thread_url: str
    turn_id: str
    submitted_at: str


class MuseProvider(Protocol):
    async def create_image(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
    ) -> dict: ...

    async def create_video(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
    ) -> dict: ...


class MuseSelectorRegistry:
    """UI selectors ordered from stable attributes to fragile CSS/text fallbacks."""

    COMPOSER = (
        '[data-testid="hatch-composer-placeholder-overlay"]',
        'textarea[aria-label*="message" i]',
        'textarea[placeholder*="message" i]',
        "textarea",
    )
    FILE_INPUT = (
        'form input[type="file"]',
        '[data-testid="hatch-composer-placeholder-overlay"] ~ input[type="file"]',
        'input[type="file"]',
    )
    SEND = (
        '[data-testid*="send" i]',
        'button[aria-label*="send" i]',
        'button[aria-label*="gửi" i]',
        'button:has-text("Send")',
    )
    STOP = (
        '[data-testid="hatch-composer-stop-button"]',
        'button[aria-label*="stop" i]',
        'button[aria-label*="dừng" i]',
    )
    RESULT = '[data-testid^="hatch-chat-attachment-presentation-"]'
    AGENT_MEDIA = 'div[class*="hatch-agent-bubble-bg"] img, div[class*="hatch-agent-bubble-bg"] video'
    REMOVE_ATTACHMENT = (
        'button[aria-label*="remove attachment" i]',
        'button[aria-label*="xóa tệp" i]',
        'button[aria-label*="移除附件" i]',
        'button[aria-label*="删除附件" i]',
    )
    DOWNLOAD = (
        'button[aria-label*="download" i]',
        '[role="button"][aria-label*="download" i]',
        'button[aria-label*="tải xuống" i]',
        'button[aria-label*="下载" i]',
        'button[aria-label*="保存" i]',
    )


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


async def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


class MuseMediaExtractor:
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

    @staticmethod
    def _extension(mime_type: str, url: str, expected: str) -> str:
        clean_mime = str(mime_type or "").lower()
        for needle, extension in (
            ("mp4", ".mp4"),
            ("webm", ".webm"),
            ("quicktime", ".mov"),
            ("png", ".png"),
            ("jpeg", ".jpg"),
            ("webp", ".webp"),
        ):
            if needle in clean_mime:
                return extension
        lower_url = str(url or "").lower()
        for extension in MuseMediaExtractor.VIDEO_EXTENSIONS | MuseMediaExtractor.IMAGE_EXTENSIONS:
            if extension in lower_url:
                return extension
        return ".mp4" if expected == "video" else ".png"

    async def extract(
        self,
        page: Any,
        attachment: dict,
        destination: Path,
        expected: str,
    ) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        src = str(
            (
                attachment.get("video_src")
                if expected == "video"
                else attachment.get("src")
            )
            or ""
        )
        if expected == "video" and not src:
            src = str(attachment.get("src") or "")
        if not src:
            raise MuseTransientError("Muse đã tạo kết quả nhưng không cung cấp media URL.")

        root = page.locator(
            f'[data-testid="{attachment.get("test_id", "")}"]'
        ).first
        if attachment.get("test_id") and await root.count():
            for selector in MuseSelectorRegistry.DOWNLOAD:
                button = root.locator(selector).first
                try:
                    if not await button.count() or not await button.is_visible():
                        continue
                    async with page.expect_download(timeout=15000) as download_info:
                        await button.click()
                    download = await download_info.value
                    suggested = str(download.suggested_filename or "")
                    extension = Path(suggested).suffix.lower() or self._extension("", src, expected)
                    target = destination.with_suffix(extension)
                    await download.save_as(str(target))
                    return self._validate_result(target, expected, "download", "")
                except Exception:
                    continue

        payload = await page.evaluate(
            """async ({src}) => {
                try {
                    const response = await fetch(src, {credentials: 'include'});
                    if (!response.ok) return {ok: false, error: `http-${response.status}`};
                    const blob = await response.blob();
                    const bytes = new Uint8Array(await blob.arrayBuffer());
                    let binary = '';
                    for (let i = 0; i < bytes.length; i += 0x8000) {
                        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
                    }
                    return {ok: true, mime: blob.type || '', data: btoa(binary)};
                } catch (error) {
                    return {ok: false, error: String(error)};
                }
            }""",
            {"src": src},
        )
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise MuseTransientError("Không thể tải media Muse qua browser session.")
        try:
            media_bytes = base64.b64decode(str(payload.get("data") or ""), validate=True)
        except ValueError as exc:
            raise MuseTransientError("Media Muse trả về dữ liệu không hợp lệ.") from exc
        extension = self._extension(str(payload.get("mime") or ""), src, expected)
        target = destination.with_suffix(extension)
        target.write_bytes(media_bytes)
        return self._validate_result(target, expected, "browser_blob", str(payload.get("mime") or ""))

    def _validate_result(
        self, path: Path, expected: str, method: str, mime_type: str
    ) -> dict:
        if not path.is_file() or path.stat().st_size <= 0:
            raise MuseTransientError("File media Muse rỗng hoặc không tồn tại.")
        suffix = path.suffix.lower()
        if expected == "video" and suffix not in self.VIDEO_EXTENSIONS:
            raise MuseError("Muse trả về ảnh trong khi job yêu cầu video; đã dừng fail-closed.")
        if expected == "image" and suffix not in self.IMAGE_EXTENSIONS:
            raise MuseError("Muse trả về sai loại media cho cảnh ảnh.")
        return {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "mime_type": mime_type or mimetypes.guess_type(path.name)[0] or "",
            "download_method": method,
            "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        }


class MuseBrowserAdapter:
    def __init__(self, context: Any, *, job_id: str = "", video_id: int | None = None):
        self.context = context
        self.job_id = job_id
        self.video_id = video_id
        self.extractor = MuseMediaExtractor()

    async def capability_probe(self, page: Any) -> dict:
        body = (await page.locator("body").inner_text(timeout=15000))[:4000]
        if re.search(r"log in|sign in|create an account|đăng nhập|登录", body, re.I):
            raise MuseAuthenticationError(
                "Profile GPM chưa đăng nhập Muse; hãy đăng nhập thủ công một lần."
            )
        if "Connecting..." in body:
            raise MuseTransientError("Muse đang Connecting; chưa sẵn sàng nhận request.")
        composer = await _first_visible(page, MuseSelectorRegistry.COMPOSER)
        file_input = None
        for selector in MuseSelectorRegistry.FILE_INPUT:
            locator = page.locator(selector).first
            if await locator.count():
                file_input = locator
                break
        if composer is None or file_input is None:
            raise MuseError("Capability probe thất bại: thiếu composer hoặc file input Muse.")
        textarea = page.locator("textarea").first
        if not await textarea.count() or not await textarea.is_visible() or not await textarea.is_enabled():
            raise MuseError("Capability probe thất bại: textarea Muse chưa sẵn sàng.")
        send_ready = False
        try:
            # Muse only renders/enables Send after React receives non-empty input.
            # Fill then clear a local probe; never click, so this consumes no credit.
            await textarea.fill("Auto_YT capability probe - do not send")
            for _ in range(20):
                send = await _first_visible(page, MuseSelectorRegistry.SEND)
                if send is not None and await send.is_enabled():
                    send_ready = True
                    break
                await page.wait_for_timeout(50)
        finally:
            await textarea.fill("")
        browser_capabilities = await page.evaluate(
            """({resultSelector, downloadSelectors}) => {
                try {
                    document.querySelectorAll(resultSelector);
                    downloadSelectors.forEach((selector) => document.querySelectorAll(selector));
                    return {
                        result_detection: true,
                        download_extraction: typeof window.fetch === 'function'
                            && typeof window.Blob === 'function'
                            && typeof window.FileReader === 'function',
                    };
                } catch (_) {
                    return {result_detection: false, download_extraction: false};
                }
            }""",
            {
                "resultSelector": MuseSelectorRegistry.RESULT,
                "downloadSelectors": list(MuseSelectorRegistry.DOWNLOAD),
            },
        )
        if not send_ready:
            raise MuseError("Capability probe thất bại: nút Send Muse không sẵn sàng.")
        if not browser_capabilities.get("result_detection"):
            raise MuseError("Capability probe thất bại: selector kết quả Muse không hợp lệ.")
        if not browser_capabilities.get("download_extraction"):
            raise MuseError("Capability probe thất bại: browser không hỗ trợ trích xuất media Muse.")
        return {
            "composer": True,
            "file_input": True,
            "send_detection": send_ready,
            "result_detection": bool(browser_capabilities["result_detection"]),
            "download_extraction": bool(browser_capabilities["download_extraction"]),
            "url": page.url,
        }

    async def verify(self) -> dict:
        page = await self.context.new_page()
        try:
            await page.goto(MUSE_URL, wait_until="domcontentloaded", timeout=45000)
            return await self.capability_probe(page)
        finally:
            await channel_scanner_service.cleanup_owned_page(self.context, page)

    async def generate(
        self,
        *,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        expected: str,
        timeout_seconds: int,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict:
        page = await self.context.new_page()
        submission_sent = False
        receipt_persisted = False
        try:
            await page.goto(MUSE_URL, wait_until="domcontentloaded", timeout=45000)
            await self.capability_probe(page)
            await self._clear_attachments(page)
            baseline = await self._attachments(page)
            baseline_sources = {
                value
                for item in baseline
                for value in (item.get("src"), item.get("video_src"))
                if value
            }
            await self._upload_references(page, reference_paths)
            await self._send_prompt(page, prompt)
            submission_sent = True
            for _ in range(20):
                if str(page.url or "").rstrip("/") != MUSE_URL.rstrip("/"):
                    break
                await page.wait_for_timeout(250)
            submitted_at = db.utc_now()
            receipt = MuseReceipt(
                thread_url=str(page.url or MUSE_URL),
                turn_id=uuid.uuid4().hex,
                submitted_at=submitted_at,
            )
            if on_submitted and receipt.thread_url.rstrip("/") != MUSE_URL.rstrip("/"):
                on_submitted(receipt)
                receipt_persisted = True
            attachment = await self._wait_for_result(
                page,
                expected=expected,
                baseline_sources=baseline_sources,
                timeout_seconds=timeout_seconds,
                cancel_check=cancel_check,
            )
            result = await self.extractor.extract(page, attachment, destination, expected)
            return {
                **result,
                "thread_url": str(page.url or receipt.thread_url),
                "turn_id": str(attachment.get("test_id") or receipt.turn_id),
                "submitted_at": submitted_at,
                "width": int(attachment.get("width") or 0),
                "height": int(attachment.get("height") or 0),
            }
        except MuseTransientError as exc:
            await browser_diagnostics.capture_browser_diagnostics_async(
                page,
                "muse",
                job_id=self.job_id,
                video_id=self.video_id,
                action_name=f"generate_{expected}",
            )
            if submission_sent and not receipt_persisted:
                raise MuseAmbiguousSubmissionError(
                    "Muse đã nhận prompt nhưng chưa có thread receipt; dừng để tránh trừ credit hai lần."
                ) from exc
            raise
        except (MuseError, asyncio.CancelledError):
            await browser_diagnostics.capture_browser_diagnostics_async(
                page,
                "muse",
                job_id=self.job_id,
                video_id=self.video_id,
                action_name=f"generate_{expected}",
            )
            raise
        except Exception as exc:
            if exc.__class__.__name__ == "VideoJobCanceled":
                raise
            await browser_diagnostics.capture_browser_diagnostics_async(
                page,
                "muse",
                job_id=self.job_id,
                video_id=self.video_id,
                error=exc,
                action_name=f"generate_{expected}",
            )
            if submission_sent and not receipt_persisted:
                raise MuseAmbiguousSubmissionError(
                    "Muse đã nhận prompt nhưng chưa có thread receipt; dừng để tránh trừ credit hai lần."
                ) from exc
            raise MuseTransientError(f"Tự động hóa Muse gặp lỗi: {exc}") from exc
        finally:
            await channel_scanner_service.cleanup_owned_page(self.context, page)

    async def reconcile(
        self,
        *,
        receipt: MuseReceipt,
        destination: Path,
        expected: str,
    ) -> dict | None:
        if not receipt.thread_url.startswith("https://muse.ai/"):
            raise MuseAmbiguousSubmissionError("Receipt Muse không có thread URL hợp lệ.")
        page = await self.context.new_page()
        try:
            await page.goto(receipt.thread_url, wait_until="domcontentloaded", timeout=45000)
            await self.capability_probe(page)
            attachments = await self._attachments(page)
            matching = [item for item in attachments if self._matches_expected(item, expected)]
            if not matching:
                return None
            result = await self.extractor.extract(page, matching[-1], destination, expected)
            return {
                **result,
                "thread_url": receipt.thread_url,
                "turn_id": str(matching[-1].get("test_id") or receipt.turn_id),
                "submitted_at": receipt.submitted_at,
                "width": int(matching[-1].get("width") or 0),
                "height": int(matching[-1].get("height") or 0),
            }
        finally:
            await channel_scanner_service.cleanup_owned_page(self.context, page)

    async def _clear_attachments(self, page: Any) -> None:
        for selector in MuseSelectorRegistry.REMOVE_ATTACHMENT:
            locator = page.locator(selector)
            while await locator.count():
                try:
                    await locator.first.click(timeout=1000)
                except Exception:
                    break

    async def _upload_references(self, page: Any, paths: list[Path]) -> None:
        if not paths:
            return
        if len(paths) > 6:
            raise MuseReferenceError("Muse chỉ hỗ trợ tối đa 6 ảnh reference mỗi request.")
        resolved: list[str] = []
        for path in paths:
            candidate = Path(path).resolve()
            if not candidate.is_file() or candidate.suffix.lower() not in MuseMediaExtractor.IMAGE_EXTENSIONS:
                raise MuseReferenceError(f"Ảnh reference không hợp lệ: {candidate.name}")
            resolved.append(str(candidate))
        file_input = None
        for selector in MuseSelectorRegistry.FILE_INPUT:
            locator = page.locator(selector).first
            if await locator.count():
                file_input = locator
                break
        if file_input is None:
            raise MuseReferenceError("Không tìm thấy file input của Muse.")
        await file_input.set_input_files(resolved)
        await page.wait_for_timeout(500)
        confirmed = await page.evaluate(
            """(expected) => {
                const labels = [...document.querySelectorAll('button[aria-label]')]
                    .filter((el) => /remove attachment|xóa tệp|移除附件|删除附件/i.test(el.getAttribute('aria-label') || ''));
                const overlay = document.querySelector('[data-testid="hatch-composer-placeholder-overlay"]');
                const form = overlay && overlay.closest('form');
                const media = form ? form.querySelectorAll('img,video').length : 0;
                return Math.max(labels.length, media) >= expected;
            }""",
            len(resolved),
        )
        if not confirmed:
            raise MuseReferenceError("Muse không xác nhận đủ ảnh reference đã upload.")

    async def _send_prompt(self, page: Any, prompt: str) -> None:
        composer = await _first_visible(page, MuseSelectorRegistry.COMPOSER)
        if composer is None:
            raise MuseError("Không tìm thấy composer Muse.")
        tag_name = await composer.evaluate("el => el.tagName.toLowerCase()")
        if tag_name != "textarea":
            composer = page.locator("textarea").first
        await composer.fill(prompt)
        for _ in range(30):
            button = await _first_visible(page, MuseSelectorRegistry.SEND)
            if button is not None and await button.is_enabled():
                await button.click()
                return
            await page.wait_for_timeout(100)
        await composer.press("Enter")
        await page.wait_for_timeout(250)
        if (await composer.input_value()).strip():
            raise MuseAmbiguousSubmissionError(
                "Không xác nhận được Muse đã nhận prompt; dừng để tránh trừ credit hai lần."
            )

    async def _attachments(self, page: Any) -> list[dict]:
        return await page.evaluate(
            """({resultSelector, agentSelector}) => {
                const list = [];
                const seen = new Set();
                function add(node, testId) {
                    if (!node || node.closest('form,[class*=chat-user-bubble],[class*="group/msg"]')) return;
                    const video = node.matches?.('video') ? node : node.querySelector('video');
                    const image = node.matches?.('img') ? node : node.querySelector('img');
                    const primary = video || image;
                    if (!primary) return;
                    const src = primary.currentSrc || primary.src || '';
                    if (!src || seen.has(src) || src.includes('avatar') || src.includes('emoji')) return;
                    seen.add(src);
                    list.push({
                        test_id: testId || node.getAttribute?.('data-testid') || 'agent-media',
                        src,
                        video_src: video ? (video.currentSrc || video.src || '') : '',
                        has_video: !!video,
                        width: video ? video.videoWidth : image.naturalWidth,
                        height: video ? video.videoHeight : image.naturalHeight,
                    });
                }
                document.querySelectorAll(resultSelector).forEach((node) => add(node, node.getAttribute('data-testid')));
                document.querySelectorAll(agentSelector).forEach((node) => add(node.parentElement || node, 'agent-media'));
                return list;
            }""",
            {
                "resultSelector": MuseSelectorRegistry.RESULT,
                "agentSelector": MuseSelectorRegistry.AGENT_MEDIA,
            },
        )

    @staticmethod
    def _matches_expected(item: dict, expected: str) -> bool:
        src = str(item.get("video_src") or item.get("src") or "").lower()
        is_video = bool(item.get("has_video")) or any(
            extension in src for extension in MuseMediaExtractor.VIDEO_EXTENSIONS
        )
        return is_video if expected == "video" else not is_video

    async def _wait_for_result(
        self,
        page: Any,
        *,
        expected: str,
        baseline_sources: set[str],
        timeout_seconds: int,
        cancel_check: Callable[[], None] | None,
    ) -> dict:
        deadline = time.monotonic() + timeout_seconds
        last_text = ""
        stable_text_count = 0
        while time.monotonic() < deadline:
            if cancel_check is not None:
                cancel_check()
            attachments = await self._attachments(page)
            matching = [
                item
                for item in attachments
                if self._matches_expected(item, expected)
                and (item.get("video_src") or item.get("src")) not in baseline_sources
            ]
            if matching:
                return matching[-1]
            body_tail = (await page.locator("body").inner_text())[-1600:]
            if re.search(r"out of credits|credit limit|quota|额度不足|积分不足|达到上限", body_tail, re.I):
                raise MuseError("Tài khoản Muse không còn đủ credit.")
            if re.search(r"rate limit|too many requests|try again later|请求过于频繁", body_tail, re.I):
                raise MuseTransientError("Muse đang rate-limit; sẽ thử lại theo lịch retry.")
            if re.search(r"log in|sign in|create an account|đăng nhập|登录", body_tail, re.I):
                raise MuseAuthenticationError("Phiên đăng nhập Muse đã hết hạn.")
            stop_visible = await _first_visible(page, MuseSelectorRegistry.STOP)
            agent_text = await page.evaluate(
                """() => {
                    const bubbles = [...document.querySelectorAll('div[class*="hatch-agent-bubble-bg"]')];
                    return bubbles.length ? (bubbles[bubbles.length - 1].innerText || '').trim() : '';
                }"""
            )
            if agent_text and stop_visible is None:
                if agent_text == last_text:
                    stable_text_count += 1
                else:
                    last_text = agent_text
                    stable_text_count = 0
                if stable_text_count >= 8 and not re.search(
                    r"\.png|\.jpe?g|\.webp|\.mp4|\.webm|generated|artifact|deliverable",
                    agent_text,
                    re.I,
                ):
                    raise MuseRefusalError(
                        "Muse chỉ trả về văn bản thay vì media: " + agent_text[:180]
                    )
            await page.wait_for_timeout(750)
        raise MuseTransientError("Muse generation hết thời gian chờ mà chưa có media mới.")


_owned_profiles: set[str] = set()
_idle_timers: dict[str, threading.Timer] = {}
_lifecycle_lock = threading.RLock()


def _cancel_idle_timer(profile_id: str) -> None:
    with _lifecycle_lock:
        timer = _idle_timers.pop(profile_id, None)
        if timer:
            timer.cancel()


def _has_active_muse_jobs() -> bool:
    return bool(db.list_active_system_jobs("muse_media_generation"))


def _close_owned_profile_when_idle(profile_id: str) -> None:
    with _lifecycle_lock:
        _idle_timers.pop(profile_id, None)
        if profile_id not in _owned_profiles:
            return
    if _has_active_muse_jobs():
        _schedule_idle_close(profile_id)
        return
    busy, _ = channel_scanner_service.is_profile_busy_or_queued(profile_id)
    if busy:
        _schedule_idle_close(profile_id)
        return
    try:
        gpm_service.stop_gpm_profile(profile_id)
    finally:
        with _lifecycle_lock:
            _owned_profiles.discard(profile_id)


def _schedule_idle_close(profile_id: str) -> None:
    with _lifecycle_lock:
        previous = _idle_timers.pop(profile_id, None)
        if previous:
            previous.cancel()
        if profile_id not in _owned_profiles:
            return
        timer = threading.Timer(
            MUSE_IDLE_CLOSE_SECONDS,
            _close_owned_profile_when_idle,
            args=(profile_id,),
        )
        timer.daemon = True
        _idle_timers[profile_id] = timer
        timer.start()


def shutdown_muse_resources() -> None:
    """Release only Muse profiles that this process started."""
    with _lifecycle_lock:
        timers = list(_idle_timers.values())
        profile_ids = list(_owned_profiles)
        _idle_timers.clear()
        _owned_profiles.clear()
    for timer in timers:
        timer.cancel()
    for profile_id in profile_ids:
        try:
            gpm_service.stop_gpm_profile(profile_id)
        except Exception as exc:
            logger.warning("Không thể đóng GPM Muse do Auto_YT sở hữu: %s", exc)


def mark_profile_user_owned(profile_id: str) -> None:
    """Prevent auto-close after the user explicitly opens the profile."""
    clean_id = str(profile_id or "").strip()
    if not clean_id:
        return
    _cancel_idle_timer(clean_id)
    with _lifecycle_lock:
        _owned_profiles.discard(clean_id)


class MuseBrowserProvider:
    def __init__(self, *, job_id: str = "", video_id: int | None = None):
        self.job_id = job_id
        self.video_id = video_id

    @staticmethod
    def configured_profile_id() -> str:
        profile_id = str(muse_settings.load_muse_settings().get("gpm_profile_id") or "").strip()
        if not profile_id:
            raise MuseAuthenticationError("Chưa cấu hình GPM profile dành riêng cho Muse.")
        return profile_id

    @staticmethod
    def validate_profile(profile_id: str) -> dict:
        detail = gpm_service.get_gpm_profile_detail(profile_id)
        raw_proxy = str(detail.get("raw_proxy") or detail.get("proxy") or "").strip()
        if not parse_proxy_url(raw_proxy):
            raise ProxyConfigurationError(
                "GPM profile Muse không có proxy hợp lệ; kết nối direct-WAN đã bị chặn."
            )
        return detail

    @contextlib.asynccontextmanager
    async def open_session(self) -> AsyncGenerator[MuseBrowserAdapter, None]:
        profile_id = self.configured_profile_id()
        await asyncio.to_thread(self.validate_profile, profile_id)
        if channel_scanner_service.is_profile_browser_busy(profile_id):
            raise MuseProfileBusy("GPM profile Muse đang được tác vụ browser khác sử dụng.")
        busy, reason = channel_scanner_service.is_profile_busy_or_queued(profile_id)
        if busy:
            raise MuseProfileBusy(reason or "GPM profile Muse đang bận.")
        _cancel_idle_timer(profile_id)
        preexisting = await asyncio.to_thread(
            gpm_service.find_running_gpm_profile_coordinates, profile_id
        )
        async with channel_scanner_service.profile_browser_operation(profile_id):
            try:
                async with gpm_service.gpm_browser_session(
                    profile_id,
                    auto_stop=False,
                    skip_proxy_check=False,
                ) as (context, _browser):
                    if not preexisting:
                        with _lifecycle_lock:
                            _owned_profiles.add(profile_id)
                    yield MuseBrowserAdapter(
                        context,
                        job_id=self.job_id,
                        video_id=self.video_id,
                    )
            finally:
                _schedule_idle_close(profile_id)

    async def verify(self) -> dict:
        async with self.open_session() as adapter:
            return await adapter.verify()

    async def create_image(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
    ) -> dict:
        async with self.open_session() as adapter:
            return await adapter.generate(
                prompt=prompt,
                reference_paths=reference_paths,
                destination=destination,
                expected="image",
                timeout_seconds=MUSE_IMAGE_TIMEOUT_SECONDS,
                on_submitted=on_submitted,
            )

    async def create_video(
        self,
        prompt: str,
        reference_paths: list[Path],
        destination: Path,
        *,
        on_submitted: Callable[[MuseReceipt], None] | None = None,
    ) -> dict:
        async with self.open_session() as adapter:
            return await adapter.generate(
                prompt=prompt,
                reference_paths=reference_paths,
                destination=destination,
                expected="video",
                timeout_seconds=MUSE_VIDEO_TIMEOUT_SECONDS,
                on_submitted=on_submitted,
            )
