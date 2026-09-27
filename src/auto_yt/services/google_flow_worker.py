import asyncio
import datetime as dt
import io
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.async_api import Page, Locator

from auto_yt.services.google_flow_login import FLOW_HOME_URL

logger = logging.getLogger(__name__)

DEBUG_LOG_DIR = Path("data/logs")
REFERENCE_ATTACH_RETRY_COUNT = 2
REFERENCE_POST_UPLOAD_RETRY_COUNT = 3
REFERENCE_RESULT_ATTACHED = "attached"
REFERENCE_RESULT_NOT_FOUND = "not_found"
REFERENCE_RESULT_UI_ERROR = "ui_error"
REFERENCE_ASSET_SCAN_LIMIT = 100
IMAGE_GENERATION_TIMEOUT_SECONDS = 240.0
VIDEO_GENERATION_TIMEOUT_SECONDS = 180.0
GENERATION_START_TIMEOUT_SECONDS = 45.0
GENERATION_IDLE_GRACE_SECONDS = 10.0
VIDEO_MODE_TIMEOUT_SECONDS = 10.0
GENERATION_POLL_SECONDS = 1.0
SUBMISSION_ACK_TIMEOUT_SECONDS = 10.0
SUBMISSION_ACK_POLL_SECONDS = 0.5
MAX_CONSECUTIVE_UI_ERRORS = 3
IMAGE_BASELINE_STABILIZE_SECONDS = 2.0
IMAGE_BASELINE_POLL_SECONDS = 0.25
MIN_GENERATED_IMAGE_WIDTH = 800
MIN_GENERATED_IMAGE_HEIGHT = 400
MIN_GENERATED_IMAGE_ASPECT_RATIO = 1.15


class ReferenceAttachmentError(RuntimeError):
    """Raised when a required Flow reference cannot be attached safely."""


class FlowGenerationError(RuntimeError):
    """Raised when Flow explicitly rejects or fails a generation request."""


class FlowGenerationStartError(RuntimeError):
    """Raised when Flow never shows progress or a new result after submission."""


class FlowSubmissionError(RuntimeError):
    """Raised when Flow does not acknowledge a prompt submission."""


class FlowUiStateError(RuntimeError):
    """Raised when the Flow UI cannot be inspected reliably."""


class FlowResultMissingError(RuntimeError):
    """Raised when Flow becomes idle without exposing a new result."""


class FlowGenerationTimeout(RuntimeError):
    """Raised when Flow remains active beyond the hard generation deadline."""


class FlowModeError(RuntimeError):
    """Raised when the requested Flow generation mode cannot be confirmed."""


class FlowInvalidOutputError(RuntimeError):
    """Raised when Flow exposes a new image that fails output validation."""

# ---------------------------------------------------------------------------
# SynthID / Google Flow watermark removal
# ---------------------------------------------------------------------------
# Google Flow images carry a small SynthID badge in the bottom-right corner.
# We crop ~4 % from every edge (slightly more from the bottom-right) and
# resize back to the original dimensions so downstream code is unaffected.
CROP_RATIO_LEFT = 0.02
CROP_RATIO_TOP = 0.02
CROP_RATIO_RIGHT = 0.05
CROP_RATIO_BOTTOM = 0.05


def _strip_watermark(image_path: Path) -> None:
    """Remove the SynthID watermark by cropping edges and resizing back."""
    try:
        img = Image.open(image_path)
        w, h = img.size
        left = int(w * CROP_RATIO_LEFT)
        top = int(h * CROP_RATIO_TOP)
        right = w - int(w * CROP_RATIO_RIGHT)
        bottom = h - int(h * CROP_RATIO_BOTTOM)
        cropped = img.crop((left, top, right, bottom))
        # Resize back to original dimensions with high-quality resampling
        result = cropped.resize((w, h), Image.Resampling.LANCZOS)
        result.save(image_path)
        logger.info("Stripped watermark from %s (crop %dx%d -> resize %dx%d)", image_path, right - left, bottom - top, w, h)
    except Exception as e:
        logger.warning("Failed to strip watermark from %s: %s", image_path, e)


def _is_valid_flow_error_text(text: str) -> bool:
    """Verify that detected text is an actual system error and not user prompt or UI placeholder."""
    if not text or len(text.strip()) < 3:
        return False
    import unicodedata
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8").lower()
    ignored_keywords = [
        "ban muon tao gi",
        "keyboard_return",
        "avoid:",
        "cinematic",
        "narrative scene",
        "documentary realism",
        "35mm photography",
        "widescreen still photograph",
    ]
    if any(kw in norm for kw in ignored_keywords):
        return False
    return True


def _upgrade_google_cdn_image_url(url: str) -> str:
    """If url is a Google CDN image with thumbnail size params (e.g. =s512, =w512-h288), upgrade to =s0."""
    clean = str(url or "").strip()
    if not clean or clean.startswith("blob:") or clean.startswith("data:"):
        return clean
    lower = clean.lower()
    if "googleusercontent.com" in lower or "flow-content.google" in lower or "google.com" in lower:
        upgraded = re.sub(r"=(?:s\d+|w\d+-h\d+)(-[a-zA-Z0-9_-]+)?(?=[?#]|$)", "=s0", clean)
        return upgraded
    return clean


class GoogleFlowWorker:
    _session_uploaded_references: dict[str, set[str]] = {}
    _session_uploaded_image_urls: set[str] = set()

    def __init__(self, page: Page):
        self.page = page
        self._current_project_name = ""
        self._uploaded_image_urls = self._session_uploaded_image_urls
        self._project_root_url = self._project_root_from_url(str(page.url or ""))
        self._active_reference_keys: set[str] = set()
        self._active_reference_filenames: set[str] = set()
        self._rejected_image_urls: set[str] = set()
        self._inspected_detail_keys: set[str] = set()
        self._last_invalid_image_size: tuple[int, int] | None = None
        self._validated_image_payload: tuple[str, bytes] | None = None

    @staticmethod
    def _project_root_from_url(url: str) -> str:
        match = re.search(
            r"^(https?://[^/]+/project/[^/?#]+)",
            str(url or ""),
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else ""

    @staticmethod
    def _is_asset_edit_url(url: str) -> bool:
        path = urlsplit(str(url or "")).path.rstrip("/")
        return bool(re.fullmatch(r"/project/[^/]+/edit/[^/]+", path, flags=re.IGNORECASE))

    @staticmethod
    def _is_project_canvas_url(url: str) -> bool:
        path = urlsplit(str(url or "")).path.rstrip("/")
        return bool(re.fullmatch(r"/project/[^/]+", path, flags=re.IGNORECASE))

    def _remember_project_root(self) -> None:
        project_root = self._project_root_from_url(str(self.page.url or ""))
        if project_root:
            self._project_root_url = project_root

    async def _restore_project_canvas(self) -> None:
        current_url = str(self.page.url or "")
        if self._is_project_canvas_url(current_url):
            self._remember_project_root()
            return
        if not self._is_asset_edit_url(current_url):
            raise FlowUiStateError(
                f"Google Flow không ở canvas project hợp lệ. URL hiện tại: {current_url}"
            )

        project_root = self._project_root_url or self._project_root_from_url(current_url)
        done_button = self.page.locator(
            "button:has-text('Xong'), button:has-text('Done'), "
            "button[aria-label*='Xong' i], button[aria-label*='Done' i]"
        ).first
        try:
            if await done_button.is_visible(timeout=1000):
                await done_button.click(timeout=2000)
                await asyncio.sleep(0.5)
        except Exception as exc:
            logger.info(
                "flow_generation state=canvas_done_failed project=%s detail=%s",
                self._project_id(),
                exc,
            )

        if self._is_project_canvas_url(str(self.page.url or "")):
            self._remember_project_root()
            logger.info(
                "flow_generation state=canvas_restored project=%s method=done",
                self._project_id(),
            )
            return

        if not project_root:
            raise FlowUiStateError("Không xác định được URL canvas Google Flow để phục hồi.")
        try:
            await self.page.goto(project_root, wait_until="domcontentloaded", timeout=30000)
            await self.wait_for_load()
        except Exception as exc:
            logger.error(
                "flow_generation state=canvas_restore_failed project=%s detail=%s",
                self._project_id(),
                exc,
            )
            raise FlowUiStateError("Không thể quay lại canvas project Google Flow.") from exc

        if not self._is_project_canvas_url(str(self.page.url or "")):
            logger.error(
                "flow_generation state=canvas_restore_failed project=%s detail=unexpected_url",
                self._project_id(),
            )
            raise FlowUiStateError("Google Flow vẫn còn ở màn hình chỉnh sửa asset.")
        self._remember_project_root()
        logger.info(
            "flow_generation state=canvas_restored project=%s method=navigate",
            self._project_id(),
        )

    async def _ensure_project_canvas(self) -> None:
        current_url = str(self.page.url or "")
        if self._is_asset_edit_url(current_url):
            await self._restore_project_canvas()
            current_url = str(self.page.url or "")
        if not self._is_project_canvas_url(current_url):
            raise FlowUiStateError(
                f"Google Flow không ở canvas project hợp lệ. URL hiện tại: {current_url}"
            )
        self._remember_project_root()

    async def _close_media_detail(self) -> None:
        if self._is_asset_edit_url(str(self.page.url or "")):
            await self._restore_project_canvas()
            return
        close_button = self.page.locator(
            "flow-media-detail button:has-text('Xong'), "
            "flow-media-detail button:has-text('Done'), "
            "[data-testid*='media-detail' i] button[aria-label*='close' i], "
            "[data-testid*='media-detail' i] button[aria-label*='đóng' i]"
        ).first
        try:
            if await close_button.is_visible(timeout=300):
                await close_button.click(timeout=1500)
                await asyncio.sleep(0.3)
            else:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.2)
        except Exception:
            pass
        if self._is_asset_edit_url(str(self.page.url or "")):
            await self._restore_project_canvas()
        elif not self._is_project_canvas_url(str(self.page.url or "")):
            raise FlowUiStateError("Google Flow không trở lại canvas sau khi đóng media detail.")

    def _project_id(self) -> str:
        current_url = str(self.page.url or "")
        match = re.search(r"/project/([^/?#]+)", current_url, flags=re.IGNORECASE)
        if match:
            return match.group(1).casefold()
        return self._current_project_name.casefold() or "unknown-project"

    def _project_reference_cache(self) -> set[str]:
        return self._session_uploaded_references.setdefault(self._project_id(), set())

    @staticmethod
    def _reference_filename(reference_path: str) -> str:
        return Path(reference_path).name.strip()

    def _log_reference_event(self, status: str, filename: str, *, detail: str = "") -> None:
        log_method = logger.error if status == "attach_failed" else logger.info
        log_method(
            "flow_reference status=%s project=%s filename=%s detail=%s",
            status,
            self._project_id(),
            filename,
            detail,
        )

    async def wait_for_load(self, timeout_ms: int = 15000):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        await asyncio.sleep(1.5)

    async def dismiss_blocking_dialogs(self):
        """Automatically dismiss welcome, terms, or onboarding dialogs and overlay backdrops if present."""
        # 1. Dismiss Angular CDK overlay backdrops/drawers/menus
        try:
            backdrop = self.page.locator(".cdk-overlay-backdrop-showing, .cdk-overlay-backdrop").first
            if await backdrop.is_visible(timeout=300):
                logger.info("Found active cdk-overlay-backdrop, pressing Escape to dismiss...")
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.4)
                if await backdrop.is_visible(timeout=200):
                    logger.info("Backdrop still visible after Escape, clicking backdrop directly...")
                    try:
                        await backdrop.click(force=True, timeout=1000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
        except Exception:
            pass

        # 2. Dismiss standard dialog buttons (scoped to dialog containers to avoid touching prompt bar)
        dismiss_selectors = [
            ".cdk-overlay-pane button:has-text('Got it')",
            ".cdk-overlay-pane button:has-text('Dismiss')",
            ".cdk-overlay-pane button:has-text('I understand')",
            ".cdk-overlay-pane button:has-text('Accept')",
            ".cdk-overlay-pane button:has-text('Đồng ý')",
            ".cdk-overlay-pane button:has-text('Đóng')",
            ".cdk-overlay-pane button:has-text('Close')",
            ".mat-mdc-dialog-container button:has-text('Close')",
            "button:has-text('Got it')",
            "button:has-text('I understand')",
            "button:has-text('Accept')",
        ]
        for sel in dismiss_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=300):
                    logger.info("Dismissing blocking dialog with selector: %s", sel)
                    await btn.click()
                    await asyncio.sleep(0.3)
            except Exception:
                continue

        # 3. Check and auto-configure Agent Settings dialog if open
        await self.handle_agent_settings_dialog()

        # 4. Check and approve credit or assistant confirmation prompts
        await self.handle_confirmation_prompts()

    async def handle_agent_settings_dialog(self) -> bool:
        """If Agent Settings dialog ('Cài đặt tác nhân') is open, select 'Không bao giờ' (Never) and close."""
        try:
            settings_dialog = self.page.locator(
                ":is(.cdk-overlay-pane, mat-dialog-container, div):has-text('Cài đặt tác nhân'), "
                ":is(.cdk-overlay-pane, mat-dialog-container, div):has-text('Agent settings')"
            ).first
            if await settings_dialog.is_visible(timeout=300):
                never_radio = self.page.locator(
                    "mat-radio-button:has-text('Không bao giờ'), "
                    "[role='radio']:has-text('Không bao giờ'), "
                    "label:has-text('Không bao giờ'), "
                    "div:has-text('Không bao giờ'):not(:has(*)), "
                    "mat-radio-button:has-text('Never'), "
                    "[role='radio']:has-text('Never')"
                ).first
                if await never_radio.is_visible(timeout=300):
                    logger.info("Found 'Cài đặt tác nhân' dialog, selecting 'Không bao giờ' (Never ask confirmation)...")
                    await never_radio.click(force=True, timeout=2000)
                    await asyncio.sleep(0.4)

                close_btn = self.page.locator(
                    "button:has-text('close'), button mat-icon:has-text('close'), "
                    "button[aria-label*='close' i], button[aria-label*='đóng' i], "
                    "button.close-button"
                ).first
                if await close_btn.is_visible(timeout=400):
                    await close_btn.click(force=True, timeout=1500)
                    await asyncio.sleep(0.3)
                return True
        except Exception:
            pass
        return False

    async def handle_confirmation_prompts(self) -> bool:
        """Automatically approve credit spend or assistant confirmation prompts, strictly prioritizing 'Always approve'."""
        # Priority 1: Always approve (Luôn phê duyệt) - remembers permission for entire session
        always_approve_selectors = [
            "[role='button']:has-text('Luôn phê duyệt')",
            "div.chat-action-button:has-text('Luôn phê duyệt')",
            "div:has-text('Luôn phê duyệt')",
            "button:has-text('Luôn phê duyệt')",
            "[aria-label*='Luôn phê duyệt' i]",
            "[role='button']:has-text('Always approve')",
            "div.chat-action-button:has-text('Always approve')",
            "div:has-text('Always approve')",
            "button:has-text('Always approve')",
            "[aria-label*='Always approve' i]",
        ]

        # Priority 2: Single approve (Phê duyệt) - fallback if 'Always approve' is not present
        single_approve_selectors = [
            "[role='button']:has-text('Phê duyệt')",
            "div:has-text('Phê duyệt')",
            "button:has-text('Phê duyệt')",
            "button:has-text('Xác nhận')",
            "[aria-label*='Phê duyệt' i]",
            "[role='button']:has-text('Approve')",
            "div:has-text('Approve')",
            "button:has-text('Approve')",
            "[aria-label*='Approve' i]",
        ]

        for sel in always_approve_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=300):
                    logger.info("Found 'Always approve' prompt ('%s'), clicking to grant persistent permission...", sel)
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    await btn.click(force=True, timeout=2000)
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                continue

        for sel in single_approve_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=300):
                    logger.info("Found single confirmation prompt ('%s'), clicking to approve...", sel)
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    await btn.click(force=True, timeout=2000)
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                continue

        return False

    async def wait_for_editor(self, timeout: float = 30.0) -> Locator:
        """Wait for the prompt editor element to become visible and interactive."""
        if self._is_asset_edit_url(str(self.page.url or "")):
            raise FlowUiStateError(
                "Google Flow đang ở màn hình chỉnh sửa asset, không phải canvas tạo cảnh."
            )
        start_time = asyncio.get_event_loop().time()
        candidate_selectors = [
            "flow-prompt-box .ProseMirror",
            "flow-prompt-box div[contenteditable='true']",
            "flow-prompt-box textarea",
            "flow-prompt-box [role='textbox']",
            ".ProseMirror",
            "div[contenteditable='true']",
            "textarea[placeholder*='create' i]",
            "textarea[placeholder*='prompt' i]",
            "textarea[aria-label*='create' i]",
            "textarea[aria-label*='prompt' i]",
            "textarea",
            "input[placeholder*='create' i]",
            "[role='textbox']",
        ]

        while asyncio.get_event_loop().time() - start_time < timeout:
            if self._is_asset_edit_url(str(self.page.url or "")):
                raise FlowUiStateError(
                    "Google Flow đã chuyển sang màn hình chỉnh sửa asset khi chờ prompt."
                )
            if not self._is_project_canvas_url(str(self.page.url or "")):
                await asyncio.sleep(0.25)
                continue
            await self.dismiss_blocking_dialogs()

            for sel in candidate_selectors:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.is_visible(timeout=500):
                        try:
                            backdrop = self.page.locator(".cdk-overlay-backdrop-showing, .cdk-overlay-backdrop").first
                            if await backdrop.is_visible(timeout=200):
                                await self.dismiss_blocking_dialogs()
                        except Exception:
                            pass
                        return loc
                except Exception:
                    continue

            # If inside project but page seems stuck on loading / blank screen after 10s, reload once
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > 10 and "/project/" in str(self.page.url or ""):
                title = await self.page.title()
                if "loading" in title.lower():
                    logger.info("Page stuck in loading state ('%s'), reloading...", title)
                    try:
                        await self.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass

            await asyncio.sleep(1)

        # If timeout reached, capture debug screenshot
        await self._save_debug_screenshot("editor_not_found")
        raise RuntimeError(
            f"Không tìm thấy ô nhập prompt trên Google Flow sau {int(timeout)} giây. "
            f"URL hiện tại: {self.page.url}"
        )

    async def _get_page_title(self) -> str:
        """Safely retrieve page title whether mocked or real."""
        try:
            t_attr = getattr(self.page, "title", None)
            if callable(t_attr):
                res = t_attr()
                if hasattr(res, "__await__"):
                    return str(await res or "")
                if isinstance(res, str):
                    return res
                return ""
            if isinstance(t_attr, str):
                return t_attr
            return ""
        except Exception:
            return ""

    async def _find_project_link(self, project_name: str) -> Locator | None:
        """Find link/card corresponding to project_name on the Flow home page."""
        # 1. Direct link or title matching project_name
        try:
            link = self.page.locator(f"a[href*='/project/']:has-text('{project_name}')").first
            if await link.is_visible(timeout=1000):
                return link
        except Exception:
            pass

        # 2. Check card container with project_name that contains a project link
        try:
            card = self.page.locator(f"div:has-text('{project_name}') >> a[href*='/project/']").first
            if await card.is_visible(timeout=1000):
                return card
        except Exception:
            pass

        # 3. XPath ancestor lookup
        try:
            xpath_loc = self.page.locator(
                f"xpath=//*[contains(text(), '{project_name}')]/ancestor-or-self::*[.//a[contains(@href, '/project/')]]//a[contains(@href, '/project/')]"
            ).first
            if await xpath_loc.is_visible(timeout=1000):
                return xpath_loc
        except Exception:
            pass

        return None

    async def ensure_project(self, project_name: str, force_new: bool = False) -> str:
        """Ensure that the browser is inside an active project session matching project_name.
        If force_new is True, always create a brand-new project on Google Flow."""
        self._current_project_name = str(project_name or "").strip()
        current_url = str(self.page.url or "")
        current_title = (await self._get_page_title()).lower()

        if not force_new and self._is_asset_edit_url(current_url):
            await self._restore_project_canvas()
            current_url = str(self.page.url or "")
            current_title = (await self._get_page_title()).lower()

        # 1. Check if on 404/error page
        is_error_page = "/404" in current_url or "not found" in current_title

        # 2. If already inside a project URL, verify if it's the right project and editor is ready (only when NOT force_new)
        if not force_new and self._is_project_canvas_url(current_url) and not is_error_page:
            # Check for conflict: if current title explicitly has auto_yt_<other_id>
            has_conflict = False
            match = re.search(r"auto_yt_\d+", current_title)
            if match and project_name.lower().startswith("auto_yt_"):
                if match.group(0).lower() != project_name.lower():
                    has_conflict = True

            if not has_conflict:
                try:
                    await self.wait_for_editor(timeout=10.0)
                    self._remember_project_root()
                    return self.page.url
                except Exception:
                    logger.warning("Project editor not ready in current project, trying reload...")
                    try:
                        await self.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await self.wait_for_editor(timeout=10.0)
                        self._remember_project_root()
                        return self.page.url
                    except Exception:
                        logger.warning("Reload failed to restore editor; returning to home...")
            else:
                logger.info("Current project '%s' conflicts with '%s', returning to home...", current_title, project_name)

        # 3. Navigate to Flow home or ensure Flow URL
        try:
            await self.page.goto(FLOW_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await self.page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=30000)
        await self.wait_for_load()
        if force_new:
            self._uploaded_image_urls.clear()
        await self.dismiss_blocking_dialogs()

        # 4. Check if existing project link with project_name exists on home page (only when NOT force_new)
        if not force_new:
            project_link = await self._find_project_link(project_name)
            if project_link:
                logger.info("Found existing project '%s', opening...", project_name)
                try:
                    await project_link.click()
                    await self.wait_for_load()
                    await self.wait_for_editor(timeout=25.0)
                    self._remember_project_root()
                    return self.page.url
                except Exception as e:
                    logger.warning("Could not open existing project link: %s. Will try creating new project.", e)

        # 5. Click "New project" button
        new_btn_selectors = [
            "button:has-text('New project')",
            "span:has-text('New project')",
            "[aria-label*='New project' i]",
            "button:has-text('Dự án mới')",
            "span:has-text('Dự án mới')",
            "[aria-label*='Dự án mới' i]",
            "button:has-text('Create project')",
            "[aria-label*='Create' i]",
            "button.mat-mdc-unelevated-button:has-text('New')",
        ]
        clicked = False
        for sel in new_btn_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Maybe the page already loaded straight into an untitled project session
            if self._is_project_canvas_url(str(self.page.url or "")):
                await self.wait_for_editor(timeout=20.0)
                self._remember_project_root()
                return self.page.url
            await self._save_debug_screenshot("new_project_btn_missing")
            raise RuntimeError(
                f"Không thể bấm nút 'New project' trên Google Flow. URL: {self.page.url}"
            )

        await self.wait_for_load()
        await self.wait_for_editor(timeout=30.0)
        self._remember_project_root()

        # Optionally set title if title input is available
        try:
            title_input = self.page.locator("input.editable-text-input").first
            if await title_input.is_visible(timeout=4000):
                await title_input.click()
                await title_input.fill(project_name)
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(1)
                await self.dismiss_blocking_dialogs()
        except Exception:
            pass

        await self.dismiss_blocking_dialogs()
        return self.page.url

    async def _upload_reference_file(self, reference_path: str) -> None:
        filename = self._reference_filename(reference_path)
        if not filename or not Path(reference_path).is_file():
            raise RuntimeError(f"Reference image does not exist: {reference_path}")
        add_btn = self.page.locator("button[aria-label*='Add ingredients' i], button.add-menu-trigger").first
        if not await add_btn.is_visible(timeout=3000):
            raise RuntimeError("Add ingredients button is not visible for reference upload.")

        try:
            await add_btn.click()
            await asyncio.sleep(1)
            async with self.page.expect_file_chooser(timeout=5000) as fc_info:
                file_opt = self.page.locator(
                    "button:has-text('Upload media'), .sidebar-upload-btn, "
                    "button:has-text('Tải nội dung'), button:has-text('Tải lên'), "
                    "button:has-text('Upload'), button:has-text('File'), "
                    "[role='menuitem']:has-text('File'), [role='menuitem']:has-text('Upload'), "
                    "[role='menuitem']:has-text('Tải lên')"
                ).first
                if not await file_opt.is_visible(timeout=3000):
                    raise RuntimeError("Reference upload menu item is not visible.")
                await file_opt.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(reference_path)
            # Treat an accepted file chooser as an upload attempt immediately. This
            # prevents a transient UI verification failure from causing duplicates.
            self._project_reference_cache().add(filename.casefold())
            await self.wait_for_load()
            await asyncio.sleep(2.5)
            uploaded_snapshot = await self._get_existing_images()
            self._uploaded_image_urls.update(uploaded_snapshot)
        finally:
            await self.dismiss_blocking_dialogs()

    async def upload_reference(self, reference_path: str, label: str):
        """Upload a non-scene reference while preserving legacy best-effort behavior."""
        filename = self._reference_filename(reference_path)
        if not filename or not Path(reference_path).is_file():
            return
        if filename.casefold() in self._project_reference_cache():
            logger.info(
                "Reference '%s' already uploaded in project '%s', skipping re-upload.",
                label or filename,
                self._project_id(),
            )
            return
        try:
            await self._upload_reference_file(reference_path)
            self._log_reference_event("uploaded", filename, detail="legacy_upload")
        except Exception as exc:
            logger.warning("Could not upload reference '%s': %s", label or filename, exc)

    @staticmethod
    def _media_key(url: str) -> str:
        raw = str(url or "").split("?", 1)[0].strip().casefold()
        if "googleusercontent.com" in raw or "flow-content.google" in raw or "google.com" in raw:
            raw = re.sub(r"=(?:s\d+|w\d+-h\d+)(-[a-zA-Z0-9_-]+)?$", "", raw)
        return raw

    async def _collect_image_candidates(self) -> list[dict]:
        """Collect generated image candidates from the gallery and result panel."""
        result = await self.page.evaluate(r'''() => {
            const candidates = new Map();
            const imageUrl = (value) => {
                const url = String(value || '').trim();
                if (!url || url.startsWith('data:')) return '';
                const lower = url.toLowerCase();
                const valid = lower.startsWith('blob:') ||
                    lower.includes('flow-content.google/image') ||
                    lower.includes('googleusercontent.com') ||
                    lower.includes('google.com') ||
                    /\.(?:png|jpe?g|webp)(?:\?|$)/i.test(url);
                return valid && !/\.(?:mp4|webm)(?:\?|$)/i.test(url) ? url : '';
            };
            const nodes = document.querySelectorAll(
                "img[src], img[srcset], [data-image-url], [data-media-url], " +
                "[data-download-url], a[href], [style*='background-image'], " +
                "flow-media-tile, flow-canvas-tile, flow-image-tile"
            );
            for (const node of nodes) {
                if (node.closest(
                    "flow-ingredient-chip, .ingredient-chip, .mat-mdc-chip, flow-prompt-box, " +
                    "[role='menu'], flow-upload-card, [data-testid*='upload' i], .upload-card, " +
                    "[data-testid*='asset-picker' i], .asset-picker, .uploads-picker"
                )) continue;

                const urls = [];
                const addUrl = (value) => {
                    const url = imageUrl(value);
                    if (url && !urls.includes(url)) urls.push(url);
                };
                addUrl(node.currentSrc);
                addUrl(node.src);
                addUrl(node.getAttribute('data-image-url'));
                addUrl(node.getAttribute('data-media-url'));
                addUrl(node.getAttribute('data-download-url'));
                addUrl(node.href);
                const srcset = node.getAttribute('srcset') || '';
                for (const part of srcset.split(',')) addUrl(part.trim().split(/\s+/)[0]);
                const background = getComputedStyle(node).backgroundImage || '';
                for (const match of background.matchAll(/url\(["']?(.*?)["']?\)/g)) addUrl(match[1]);
                if (!urls.length) continue;

                const alt = (node.alt || '').toLowerCase();
                const className = String(node.className || '').toLowerCase();
                const aria = (node.getAttribute('aria-label') || '').toLowerCase();
                const parentAria = ((node.parentElement && node.parentElement.getAttribute('aria-label')) || '').toLowerCase();
                const lowerUrls = urls.join(' ').toLowerCase();
                if (lowerUrls.includes('s32-c-mo') || lowerUrls.includes('s96-c') ||
                    lowerUrls.includes('pr_32px') || lowerUrls.includes('/a/acg8oc') ||
                    alt.includes('google account') || alt.includes('tài khoản google') ||
                    parentAria.includes('google account') || parentAria.includes('tài khoản google') ||
                    className.includes('avatar') || className.includes('profile') ||
                    aria.includes('profile')) continue;

                const card = node.closest(
                    "flow-media-tile, [data-media-id], [data-asset-id], [data-testid*='media' i], .media-card, " +
                    "flow-canvas-tile, flow-canvas-item, .canvas-tile, flow-message-turn, .chat-message, " +
                    "[data-message-id], [data-turn-id], [data-response-id]"
                );
                const assetId = card ? (
                    card.getAttribute('data-media-id') || card.getAttribute('data-asset-id') ||
                    card.getAttribute('data-id') || ''
                ) : '';
                const key = assetId ? `asset:${assetId}` : urls[0];
                const existing = candidates.get(key);
                if (existing) {
                    for (const url of urls) {
                        if (!existing.urls.includes(url)) existing.urls.push(url);
                    }
                    existing.width = Math.max(existing.width, Number(node.naturalWidth || node.width || 0) || 0);
                    existing.height = Math.max(existing.height, Number(node.naturalHeight || node.height || 0) || 0);
                    continue;
                }
                candidates.set(key, {
                    src: urls[0],
                    urls,
                    assetId,
                    className,
                    labelText: String((card && card.innerText) || node.alt || node.getAttribute('aria-label') || ''),
                    width: Number(node.naturalWidth || node.videoWidth || node.width || 0) || 0,
                    height: Number(node.naturalHeight || node.videoHeight || node.height || 0) || 0,
                    source: node.closest('.sidebar, .mat-drawer, flow-prompt-history, [data-testid*="result" i], flow-chat-panel')
                        ? 'result_panel' : 'gallery'
                });
            }
            return Array.from(candidates.values());
        }''')
        if not isinstance(result, list):
            raise FlowUiStateError("Flow image candidate scan returned an invalid result.")
        return [item for item in result if isinstance(item, dict)]

    async def _collect_video_candidates(self) -> list[dict]:
        """Collect video URLs from players, media cards, and the result panel."""
        result = await self.page.evaluate(r'''() => {
            const candidates = [];
            const seen = new Set();
            const nodes = document.querySelectorAll(
                "video, flow-video-player video, a[href], [data-video-url], [data-media-url]"
            );
            for (const node of nodes) {
                if (node.closest(
                    "flow-ingredient-chip, flow-prompt-box, [role='menu'], flow-upload-card, " +
                    "[data-testid*='asset-picker' i], .asset-picker, .uploads-picker"
                )) continue;
                const src = node.currentSrc || node.src || node.href ||
                    node.getAttribute('data-video-url') || node.getAttribute('data-media-url') || '';
                if (!src || src.startsWith('data:')) continue;
                const lowerSrc = src.toLowerCase();
                const looksLikeVideo = node.tagName === 'VIDEO' || lowerSrc.startsWith('blob:') ||
                    /\.(?:mp4|webm)(?:\?|$)/i.test(src) || node.hasAttribute('data-video-url');
                if (!looksLikeVideo) continue;
                const card = node.closest(
                    "flow-media-tile, [data-media-id], [data-asset-id], [data-testid*='media' i], .media-card"
                );
                const assetId = card ? (
                    card.getAttribute('data-media-id') || card.getAttribute('data-asset-id') ||
                    card.getAttribute('data-id') || ''
                ) : '';
                const key = assetId ? `asset:${assetId}` : src;
                if (seen.has(key)) continue;
                seen.add(key);
                candidates.push({src, assetId});
            }
            return candidates;
        }''')
        if not isinstance(result, list):
            raise FlowUiStateError("Flow video candidate scan returned an invalid result.")
        return [item for item in result if isinstance(item, dict)]

    def _candidate_keys(self, candidate: dict) -> set[str]:
        keys = {
            self._media_key(url)
            for url in [
                candidate.get("src"),
                candidate.get("url"),
                *(candidate.get("urls") or []),
            ]
            if str(url or "").strip()
        }
        asset_id = str(candidate.get("assetId") or "").strip().casefold()
        if asset_id:
            keys.add(f"asset:{asset_id}")
        return keys

    def _candidate_matches_active_reference(self, candidate: dict) -> bool:
        keys = self._candidate_keys(candidate)
        if keys & self._active_reference_keys:
            return True
        label_lines = {
            line.strip().casefold()
            for line in str(candidate.get("labelText") or "").splitlines()
            if line.strip()
        }
        return bool(label_lines & self._active_reference_filenames)

    def _find_new_media_candidate(
        self,
        candidates: list[dict],
        baseline_keys: set[str],
        *,
        exclude_uploaded_images: bool = False,
    ) -> dict | None:
        uploaded_keys = {
            self._media_key(url) for url in self._uploaded_image_urls if str(url).strip()
        }
        ranked: list[tuple[int, dict]] = []
        for candidate in candidates:
            keys = self._candidate_keys(candidate)
            if not keys:
                continue
            if keys & baseline_keys:
                continue
            if exclude_uploaded_images and keys & uploaded_keys:
                continue
            if self._candidate_matches_active_reference(candidate):
                logger.info(
                    "flow_generation media=image state=reference_excluded project=%s asset=%s",
                    self._project_id(),
                    str(candidate.get("assetId") or "")[:80],
                )
                continue
            try:
                width = int(candidate.get("width") or 0)
                height = int(candidate.get("height") or 0)
            except (TypeError, ValueError):
                width = 0
                height = 0
            ranked.append((width * height, candidate))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0])
        return ranked[-1][1]

    def _find_new_media_source(
        self,
        candidates: list[dict],
        baseline_keys: set[str],
        *,
        exclude_uploaded_images: bool = False,
    ) -> str:
        candidate = self._find_new_media_candidate(
            candidates,
            baseline_keys,
            exclude_uploaded_images=exclude_uploaded_images,
        )
        if candidate is None:
            return ""
        return str(candidate.get("src") or candidate.get("url") or "").strip()

    async def _read_generation_activity(self) -> bool:
        result = await self.page.evaluate(r'''() => {
            const visible = (element) => {
                if (!element) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                    rect.width > 0 && rect.height > 0;
            };
            const controls = Array.from(document.querySelectorAll(
                "button, [role='button'], [role='progressbar'], [role='status'], " +
                "[aria-busy='true'], [data-state='generating'], [data-state='processing'], " +
                ".progress, .generating, .loading, .spinner, flow-media-tile, " +
                "mat-progress-spinner, mat-spinner, flow-thinking, " +
                "[data-testid*='loading' i], [data-testid*='progress' i], " +
                "[data-testid*='generating' i]"
            ));
            return controls.some((element) => {
                if (!visible(element)) return false;
                const text = (element.textContent || '').trim().toLowerCase();
                const aria = (element.getAttribute('aria-label') || '').trim().toLowerCase();
                const title = (element.getAttribute('title') || '').trim().toLowerCase();
                const state = (element.getAttribute('data-state') || '').trim().toLowerCase();
                const testId = (element.getAttribute('data-testid') || '').trim().toLowerCase();
                const html = (element.innerHTML || '').toLowerCase();
                return text === 'stop' || text === 'dừng' || aria.includes('stop') ||
                    aria.includes('dừng') || title.includes('stop') || title.includes('dừng') ||
                    element.getAttribute('role') === 'progressbar' ||
                    element.getAttribute('aria-busy') === 'true' || state === 'generating' ||
                    state === 'processing' || element.classList.contains('generating') ||
                    element.classList.contains('progress') || element.classList.contains('loading') ||
                    element.classList.contains('spinner') || /(^|\s)\d{1,3}%($|\s)/.test(text) ||
                    text.includes('đang tạo') || text.includes('generating') ||
                    text.includes('creating') || text.includes('hiện tiến trình tư duy') ||
                    testId.includes('loading') || testId.includes('progress') ||
                    testId.includes('generating') || html.includes('stop_circle') ||
                    html.includes('progress_spinner') || html.includes('hourglass');
            });
        }''')
        return result is True

    async def _capture_submission_marker(self) -> str:
        """Return a stable signature for the visible conversation/result panel."""
        result = await self.page.evaluate(r'''() => {
            const selectors = [
                'flow-prompt-history',
                'flow-chat-panel',
                'flow-session-panel',
                "[data-testid*='prompt-history' i]",
                "[data-testid*='conversation' i]",
                "[data-testid*='result-panel' i]",
                '.sidebar',
                '.mat-drawer'
            ];
            const roots = [];
            const seen = new Set();
            for (const selector of selectors) {
                for (const root of document.querySelectorAll(selector)) {
                    if (seen.has(root)) continue;
                    seen.add(root);
                    const style = getComputedStyle(root);
                    const rect = root.getBoundingClientRect();
                    if (style.display === 'none' || style.visibility === 'hidden' ||
                        rect.width <= 0 || rect.height <= 0) continue;
                    roots.push(root);
                }
            }
            const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
            const parts = [];
            for (const root of roots) {
                const clone = root.cloneNode(true);
                for (const node of clone.querySelectorAll(
                    'flow-prompt-box, textarea, input, [contenteditable="true"], script, style'
                )) node.remove();
                const text = normalize(clone.textContent).slice(-6000);
                const ids = Array.from(root.querySelectorAll(
                    '[data-message-id], [data-turn-id], [data-response-id], ' +
                    '[data-media-id], [data-asset-id]'
                )).map((node) =>
                    node.getAttribute('data-message-id') || node.getAttribute('data-turn-id') ||
                    node.getAttribute('data-response-id') || node.getAttribute('data-media-id') ||
                    node.getAttribute('data-asset-id') || ''
                ).filter(Boolean).slice(-50);
                parts.push(`${ids.join(',')}|${text}`);
            }
            return parts.join('||');
        }''')
        return str(result or "")

    @staticmethod
    def _normalize_prompt_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    async def _read_editor_text(self, editor: Locator) -> str:
        value = await editor.evaluate("el => el.innerText || el.value || ''")
        return str(value or "")

    async def _find_prompt_submit_button(self, editor: Locator) -> Locator | None:
        semantic_selectors = [
            "flow-prompt-box button[type='submit']",
            "flow-prompt-box button.generate-icon-button",
            "flow-prompt-box button[aria-label*='send' i]",
            "flow-prompt-box button[aria-label*='submit' i]",
            "flow-prompt-box button[aria-label*='generate' i]",
            "flow-prompt-box button[aria-label*='start generation' i]",
            "flow-prompt-box button[aria-label*='gửi' i]",
            "flow-prompt-box button[aria-label*='tạo' i]",
            "flow-prompt-box button:has-text('arrow_forward')",
            "flow-prompt-box button:has-text('send')",
        ]
        for selector in semantic_selectors:
            try:
                button = self.page.locator(selector).first
                if await button.is_visible(timeout=250) and await button.is_enabled(timeout=250):
                    return button
            except Exception:
                continue

        try:
            prompt_box = editor.locator("xpath=ancestor::flow-prompt-box[1]")
            if await prompt_box.count() == 0:
                prompt_box = self.page.locator("flow-prompt-box").first
            buttons = prompt_box.locator("button")
            count = await buttons.count()
        except Exception:
            return None

        excluded_terms = (
            "add", "plus", "upload", "ingredient", "setting", "tune", "option",
            "mode", "model", "thêm", "tải", "cài đặt", "tuỳ chọn", "tùy chọn",
        )
        for index in range(count - 1, -1, -1):
            button = buttons.nth(index)
            try:
                if not await button.is_visible(timeout=200) or not await button.is_enabled(timeout=200):
                    continue
                label = " ".join(
                    filter(
                        None,
                        [
                            await button.get_attribute("aria-label"),
                            await button.get_attribute("title"),
                            await button.inner_text(),
                        ],
                    )
                ).casefold()
                if any(term in label for term in excluded_terms):
                    continue
                return button
            except Exception:
                continue
        return None

    async def _wait_for_submission_ack(
        self,
        *,
        media_type: str,
        baseline_keys: set[str],
        baseline_marker: str,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + SUBMISSION_ACK_TIMEOUT_SECONDS
        while True:
            source = await self._resolve_new_media_source(media_type, baseline_keys)
            if source:
                return source, "output"
            if await self._read_generation_activity():
                return "", "activity"
            current_marker = await self._capture_submission_marker()
            if current_marker and current_marker != baseline_marker:
                return "", "conversation"
            if time.monotonic() >= deadline:
                return "", ""
            await asyncio.sleep(SUBMISSION_ACK_POLL_SECONDS)

    async def _has_prompt_in_conversation(self, prompt: str, baseline_marker: str) -> bool:
        current_marker = await self._capture_submission_marker()
        if current_marker and current_marker != baseline_marker:
            return True
        norm_prompt = self._normalize_prompt_text(prompt)
        if not norm_prompt:
            return False
        clean_prefix = re.sub(r"(?i)\. avoid:.*", "", norm_prompt).strip()[:50].casefold()
        if not clean_prefix:
            clean_prefix = norm_prompt[:50].casefold()
        try:
            return bool(await self.page.evaluate(
                r'''(sample) => {
                    const selectors = [
                        'flow-prompt-history', 'flow-chat-panel', 'flow-session-panel',
                        "[data-testid*='prompt-history' i]", "[data-testid*='conversation' i]",
                        "[data-testid*='result-panel' i]", '.sidebar', '.mat-drawer',
                        'flow-message-turn', '.chat-message', '[data-message-id]', '[data-turn-id]'
                    ];
                    for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            const text = (el.textContent || '').toLowerCase();
                            if (text.includes(sample)) return true;
                        }
                    }
                    return false;
                }''',
                clean_prefix,
            ))
        except Exception:
            return False

    async def _submit_prompt_and_wait_for_ack(
        self,
        *,
        editor: Locator,
        full_prompt: str,
        media_type: str,
        baseline_keys: set[str],
    ) -> tuple[float, str]:
        baseline_marker = await self._capture_submission_marker()
        attempted_methods: set[str] = set()

        for attempt in range(1, 3):
            button = await self._find_prompt_submit_button(editor)
            if button is not None and "button" not in attempted_methods:
                method = "button"
                attempted_methods.add(method)
                await self.dismiss_blocking_dialogs()
                try:
                    await button.click(timeout=3000)
                except Exception as exc:
                    logger.warning(
                        "flow_generation media=%s state=submit_click_failed project=%s "
                        "attempt=%d detail=%s",
                        media_type,
                        self._project_id(),
                        attempt,
                        exc,
                    )
                    continue
            elif "enter" not in attempted_methods:
                method = "enter"
                attempted_methods.add(method)
                try:
                    focus_result = editor.focus()
                    if asyncio.iscoroutine(focus_result):
                        await focus_result
                except Exception:
                    pass
                await self.page.keyboard.press("Enter")
            else:
                break

            attempted_at = time.monotonic()
            logger.info(
                "flow_generation media=%s state=submit_attempt project=%s attempt=%d method=%s",
                media_type,
                self._project_id(),
                attempt,
                method,
            )
            source, reason = await self._wait_for_submission_ack(
                media_type=media_type,
                baseline_keys=baseline_keys,
                baseline_marker=baseline_marker,
            )
            if reason:
                self._log_generation_event(
                    media_type,
                    "submit_acknowledged",
                    attempted_at,
                    detail=f"attempt={attempt} method={method} reason={reason}",
                )
                self._log_generation_event(
                    media_type,
                    "submitted",
                    attempted_at,
                    detail=f"method={method}",
                )
                return attempted_at, source

            if await self._has_prompt_in_conversation(full_prompt, baseline_marker):
                self._log_generation_event(
                    media_type,
                    "submit_acknowledged",
                    attempted_at,
                    detail=f"attempt={attempt} method={method} reason=conversation_verified",
                )
                self._log_generation_event(
                    media_type,
                    "submitted",
                    attempted_at,
                    detail=f"method={method}",
                )
                return attempted_at, ""

            editor_text = self._normalize_prompt_text(await self._read_editor_text(editor))
            if not editor_text:
                logger.warning(
                    "flow_generation media=%s state=submit_silent_drop project=%s "
                    "attempt=%d method=%s (editor empty but message unacknowledged)",
                    media_type,
                    self._project_id(),
                    attempt,
                    method,
                )
                if attempt < 2:
                    await self.dismiss_blocking_dialogs()
                    try:
                        await editor.click(timeout=2000)
                    except Exception:
                        pass
                    await self.page.keyboard.press("Control+A")
                    await self.page.keyboard.press("Backspace")
                    try:
                        await editor.fill(full_prompt)
                    except Exception:
                        await self.page.keyboard.insert_text(full_prompt)
                    await asyncio.sleep(0.5)
                    continue
                else:
                    break

            if editor_text != self._normalize_prompt_text(full_prompt):
                try:
                    await editor.fill(full_prompt)
                except Exception:
                    await editor.click(timeout=2000)
                    await self.page.keyboard.press("Control+A")
                    await self.page.keyboard.insert_text(full_prompt)
            if attempt < 2:
                logger.info(
                    "flow_generation media=%s state=submit_fallback project=%s "
                    "attempt=%d previous_method=%s",
                    media_type,
                    self._project_id(),
                    attempt + 1,
                    method,
                )

        await self._save_debug_screenshot(f"{media_type}_submit_failed")
        logger.error(
            "flow_generation media=%s state=submit_failed project=%s methods=%s",
            media_type,
            self._project_id(),
            ",".join(sorted(attempted_methods)),
        )
        raise FlowSubmissionError(
            f"Google Flow không tiếp nhận prompt tạo {media_type} sau "
            f"{len(attempted_methods)} lần thử."
        )

    async def _get_snackbar_error(self) -> str:
        error_loc = self.page.locator(
            ".mat-mdc-snack-bar-container [role='alert'], "
            ".mat-mdc-snack-bar-container, flow-toast-notification"
        ).first
        if not await error_loc.is_visible(timeout=200):
            return ""
        text = (await error_loc.inner_text() or "").strip()
        return text if _is_valid_flow_error_text(text) else ""

    def _log_generation_event(
        self,
        media_type: str,
        state: str,
        started_at: float,
        *,
        detail: str = "",
    ) -> None:
        logger.info(
            "flow_generation media=%s state=%s project=%s elapsed=%.2f detail=%s",
            media_type,
            state,
            self._project_id(),
            max(0.0, time.monotonic() - started_at),
            detail,
        )

    async def _get_existing_images(self, *, strict: bool = False) -> set[str]:
        """Return image URLs currently visible outside upload/reference controls."""
        try:
            return {
                str(url or "").strip()
                for item in await self._collect_image_candidates()
                for url in [item.get("src"), *(item.get("urls") or [])]
                if str(url or "").strip()
            }
        except Exception as exc:
            if strict:
                raise FlowUiStateError("Không thể chụp baseline ảnh của Google Flow.") from exc
            return set()

    async def _capture_stable_image_baseline(self) -> set[str]:
        deadline = time.monotonic() + IMAGE_BASELINE_STABILIZE_SECONDS
        previous_keys: set[str] | None = None
        latest_keys: set[str] = set()
        while True:
            candidates = await self._collect_image_candidates()
            latest_keys = set().union(
                *(self._candidate_keys(candidate) for candidate in candidates)
            ) if candidates else set()
            for candidate in candidates:
                if self._candidate_matches_active_reference(candidate):
                    self._active_reference_keys.update(self._candidate_keys(candidate))
            if previous_keys == latest_keys or time.monotonic() >= deadline:
                return latest_keys | self._active_reference_keys
            previous_keys = latest_keys
            await asyncio.sleep(IMAGE_BASELINE_POLL_SECONDS)

    def _set_active_reference_filenames(
        self,
        reference_ids: list[str] | None,
        reference_paths: dict[str, str] | None,
    ) -> None:
        filenames: set[str] = set()
        for reference_id in reference_ids or []:
            reference_path = str((reference_paths or {}).get(str(reference_id)) or "").strip()
            if reference_path:
                filename = self._reference_filename(reference_path)
                if filename:
                    filenames.add(filename.casefold())
        self._active_reference_filenames = filenames
        self._active_reference_keys = set()

    def _reset_image_candidate_state(self) -> None:
        self._rejected_image_urls = set()
        self._inspected_detail_keys = set()
        self._last_invalid_image_size = None
        self._validated_image_payload = None

    async def _get_existing_error_texts(self) -> set[str]:
        """Snapshot all existing error tile texts currently rendered on the page."""
        err_texts = set()
        canvas_err_selectors = [
            "flow-error-tile",
            ".canvas flow-error-tile",
            "flow-media-tile.error",
            "flow-media-tile[data-error='true']",
            "[data-tile-state='error']",
            ".error-tile",
        ]
        for sel in canvas_err_selectors:
            try:
                locs = self.page.locator(sel)
                count = await locs.count()
                for i in range(min(count, 10)):
                    t = (await locs.nth(i).inner_text() or "").strip()
                    if t:
                        err_texts.add(t)
            except Exception:
                pass
        return err_texts

    async def clear_ingredient_chips(self) -> None:
        """Clear all ingredient chips currently attached to the prompt box."""
        chip_selectors = [
            "flow-prompt-box flow-ingredient-chip",
            "flow-prompt-box flow-prompt-attachment",
            "flow-prompt-box flow-attachment",
            "flow-prompt-box flow-reference-chip",
            "flow-prompt-box [data-testid*='ingredient' i]",
            "flow-prompt-box [data-testid*='attachment' i]",
            "flow-prompt-box [data-testid*='reference' i]",
            "flow-prompt-box .ingredient-chip",
            "flow-prompt-box .reference-chip",
            "flow-prompt-box .attachment-chip",
            "flow-prompt-box .mat-mdc-chip",
            "flow-prompt-box mat-chip",
            "flow-ingredient-chip",
            ".ingredient-chip",
            "mat-chip",
            ".mat-mdc-chip",
            "[data-chip]",
            ".chip-item",
            ".reference-chip",
        ]

        clear_btn_selectors = [
            "button.clear-button",
            "button[aria-label*='Clear prompt' i]",
            "button[aria-label*='Clear all' i]",
            "button[aria-label*='Xóa câu lệnh' i]",
            "button[aria-label*='Xóa tất cả' i]",
            "flow-prompt-box button[aria-label*='Clear' i]",
            "flow-prompt-box button[aria-label*='Xóa' i]",
        ]

        for c_sel in clear_btn_selectors:
            try:
                c_btn = self.page.locator(c_sel).first
                if await c_btn.is_visible(timeout=500):
                    await c_btn.click()
                    await asyncio.sleep(0.3)
                    break
            except Exception:
                pass

        # Direct close/remove buttons inside prompt box
        direct_close_selectors = [
            "flow-prompt-box button[aria-label*='Remove' i]",
            "flow-prompt-box button[aria-label*='Delete' i]",
            "flow-prompt-box button[aria-label*='Xóa' i]",
            "flow-prompt-box button[aria-label*='Close' i]",
            "flow-prompt-box button[aria-label*='Đóng' i]",
            "flow-prompt-box button:has(mat-icon:has-text('close'))",
            "flow-prompt-box button:has(mat-icon:has-text('cancel'))",
            "flow-prompt-box mat-icon:has-text('close')",
            "flow-prompt-box mat-icon:has-text('cancel')",
        ]
        for d_sel in direct_close_selectors:
            try:
                btns = self.page.locator(d_sel)
                cnt = await btns.count()
                for i in range(cnt):
                    b = btns.nth(i)
                    if await b.is_visible(timeout=200):
                        await b.click(timeout=1000)
                        await asyncio.sleep(0.2)
            except Exception:
                pass

        # Dismiss / remove individual chips
        for _ in range(8):
            found_any = False
            for sel in chip_selectors:
                try:
                    chips = self.page.locator(sel)
                    count = await chips.count()
                    if count > 0:
                        found_any = True
                        for i in range(count):
                            c = chips.nth(i)
                            if await c.is_visible(timeout=300):
                                await c.hover()
                                await asyncio.sleep(0.1)
                                del_btn_selectors = [
                                    "mat-icon:has-text('cancel')",
                                    "mat-icon:has-text('close')",
                                    "mat-icon:has-text('clear')",
                                    "mat-icon:has-text('x')",
                                    ".hover-icon-overlay",
                                    "button.delete-button",
                                    "button[aria-label*='Remove' i]",
                                    "button[aria-label*='Delete' i]",
                                    "button[aria-label*='Xóa' i]",
                                    "button[aria-label*='Đóng' i]",
                                    "button[aria-label*='Close' i]",
                                    "button:has(mat-icon:has-text('close'))",
                                    "button:has(mat-icon:has-text('cancel'))",
                                    ".remove-chip-button",
                                    ".close-button",
                                ]
                                for d_sel in del_btn_selectors:
                                    try:
                                        d_btn = c.locator(d_sel).first
                                        if await d_btn.is_visible(timeout=300):
                                            await d_btn.click()
                                            await asyncio.sleep(0.2)
                                            break
                                    except Exception:
                                        pass
                except Exception:
                    pass
            if not found_any:
                break

    @staticmethod
    def _text_matches_asset_filename(text: str, filename: str) -> bool:
        expected = str(filename or "").strip().casefold()
        if not expected:
            return False
        lines = [line.strip().casefold() for line in str(text or "").splitlines()]
        return expected in lines

    async def _find_exact_picker_asset(self, filename: str) -> Locator | None:
        asset_items = self.page.locator(
            ".cdk-overlay-container button.asset-item, "
            ".cdk-overlay-container [role='option'], "
            ".cdk-overlay-container [role='listitem'], "
            ".cdk-overlay-container .asset-title, "
            ".cdk-overlay-container [data-testid*='asset']"
        )
        count = await asset_items.count()
        for index in range(min(count, REFERENCE_ASSET_SCAN_LIMIT)):
            item = asset_items.nth(index)
            text = await item.inner_text(timeout=1000)
            if self._text_matches_asset_filename(text, filename):
                return item
        return None

    async def _remember_reference_locator_keys(self, locator: Locator) -> None:
        try:
            identity = await locator.evaluate(r'''(node) => {
                const card = node.closest(
                    "flow-media-tile, [data-media-id], [data-asset-id], " +
                    "[data-testid*='asset' i], [role='option'], [role='listitem']"
                ) || node;
                const assetId = card.getAttribute('data-media-id') ||
                    card.getAttribute('data-asset-id') || card.getAttribute('data-id') || '';
                const urls = Array.from(card.querySelectorAll(
                    "img[src], img[srcset], [data-image-url], [data-media-url], a[href]"
                )).flatMap((item) => {
                    const values = [
                        item.currentSrc || '', item.src || '', item.href || '',
                        item.getAttribute('data-image-url') || '',
                        item.getAttribute('data-media-url') || ''
                    ];
                    const srcset = item.getAttribute('srcset') || '';
                    for (const part of srcset.split(',')) {
                        values.push(part.trim().split(/\s+/)[0]);
                    }
                    return values.filter(Boolean);
                });
                return {assetId, urls};
            }''')
        except Exception:
            return
        if not isinstance(identity, dict):
            return
        asset_id = str(identity.get("assetId") or "").strip().casefold()
        if asset_id:
            self._active_reference_keys.add(f"asset:{asset_id}")
        for url in identity.get("urls") or []:
            url_key = self._media_key(str(url or ""))
            if url_key:
                self._active_reference_keys.add(url_key)

    async def _has_ingredient_chip(self) -> bool:
        selectors = [
            "flow-prompt-box flow-ingredient-chip",
            "flow-prompt-box flow-prompt-attachment",
            "flow-prompt-box flow-attachment",
            "flow-prompt-box flow-reference-chip",
            "flow-prompt-box [data-testid*='ingredient' i]",
            "flow-prompt-box [data-testid*='attachment' i]",
            "flow-prompt-box [data-testid*='reference' i]",
            "flow-prompt-box .ingredient-chip",
            "flow-prompt-box .reference-chip",
            "flow-prompt-box .attachment-chip",
            "flow-prompt-box .mat-mdc-chip",
            "flow-prompt-box mat-chip",
            "flow-prompt-box img:not([alt*='profile' i]):not([alt*='account' i]):not([alt*='avatar' i])",
            "flow-prompt-box button:has(mat-icon:has-text('close'))",
            "flow-prompt-box button:has(mat-icon:has-text('cancel'))",
            "flow-prompt-box button[aria-label*='Remove' i]",
            "flow-prompt-box button[aria-label*='Delete' i]",
            "flow-prompt-box button[aria-label*='Xóa' i]",
            "flow-prompt-box mat-icon:has-text('close')",
            "flow-ingredient-chip",
            ".ingredient-chip",
            ".reference-chip",
            "[data-testid*='ingredient-chip']",
        ]
        for sel in selectors:
            try:
                chip = self.page.locator(sel).first
                if await chip.is_visible(timeout=300):
                    return True
            except Exception:
                continue

        try:
            return bool(await self.page.evaluate(r'''() => {
                const promptBox = document.querySelector('flow-prompt-box');
                if (!promptBox) return false;
                const images = promptBox.querySelectorAll('img');
                for (const img of images) {
                    const src = (img.src || img.currentSrc || '').toLowerCase();
                    const alt = (img.alt || '').toLowerCase();
                    if (src && !src.includes('profile') && !src.includes('s32-c-mo') &&
                        !alt.includes('profile') && !alt.includes('account')) {
                        return true;
                    }
                }
                const chipNodes = promptBox.querySelectorAll(
                    'flow-ingredient-chip, flow-attachment, flow-prompt-attachment, ' +
                    '.ingredient-chip, .reference-chip, .attachment-chip, mat-chip, .mat-mdc-chip, ' +
                    '[data-testid*="attachment" i], [data-testid*="reference" i]'
                );
                return chipNodes.length > 0;
            }'''))
        except Exception:
            return False

    async def _close_reference_ui(self) -> None:
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception:
            pass
        await self.dismiss_blocking_dialogs()

    async def _attach_reference_from_picker(self, filename: str) -> str:
        """Attach an existing upload by exact filename using the searchable picker."""
        await self.dismiss_blocking_dialogs()
        add_btn = self.page.locator(
            "button.add-menu-trigger, button[aria-label*='Add ingredients' i]"
        ).first
        try:
            if not await add_btn.is_visible(timeout=4000):
                return REFERENCE_RESULT_UI_ERROR
            await add_btn.click()
            await asyncio.sleep(0.8)

            uploads_tab = self.page.locator(
                ".cdk-overlay-container mat-list-item:has-text('Uploads'), "
                ".cdk-overlay-container mat-list-item:has-text('Tệp tải lên'), "
                ".cdk-overlay-container .side-nav-list-item:has-text('Uploads'), "
                ".cdk-overlay-container .side-nav-list-item:has-text('Tệp tải lên'), "
                ".cdk-overlay-container button:has-text('Uploads'), "
                ".cdk-overlay-container button:has-text('Tệp tải lên')"
            ).first
            if await uploads_tab.is_visible(timeout=2000):
                await uploads_tab.click()
                await asyncio.sleep(0.5)

            search_input = self.page.locator(
                ".cdk-overlay-container input[placeholder*='Search' i], "
                ".cdk-overlay-container input[aria-label*='Search' i], "
                ".cdk-overlay-container input[placeholder*='Tìm kiếm' i], "
                ".cdk-overlay-container input[aria-label*='Tìm kiếm' i]"
            ).first
            if not await search_input.is_visible(timeout=2000):
                await self._close_reference_ui()
                return REFERENCE_RESULT_UI_ERROR

            await search_input.fill(filename)
            await self.page.keyboard.press("Enter")
            await asyncio.sleep(0.8)
            asset_item = await self._find_exact_picker_asset(filename)
            if asset_item is None:
                await self._close_reference_ui()
                return REFERENCE_RESULT_NOT_FOUND

            await self._remember_reference_locator_keys(asset_item)
            await asset_item.click()
            await asyncio.sleep(0.5)
            if await self._has_ingredient_chip():
                await self._close_reference_ui()
                return REFERENCE_RESULT_ATTACHED

            add_to_prompt_btn = self.page.locator(
                ".cdk-overlay-container button.detail-add-to-prompt-btn, "
                ".cdk-overlay-container button:has-text('Thêm vào câu lệnh'), "
                ".cdk-overlay-container [role='menuitem']:has-text('Thêm vào câu lệnh'), "
                ".cdk-overlay-container button:has-text('Add to prompt'), "
                ".cdk-overlay-container [role='menuitem']:has-text('Add to prompt')"
            ).first
            if not await add_to_prompt_btn.is_visible(timeout=3000):
                await self._close_reference_ui()
                return REFERENCE_RESULT_UI_ERROR
            await add_to_prompt_btn.click()
            await asyncio.sleep(0.8)
            attached = await self._has_ingredient_chip()
            await self._close_reference_ui()
            return REFERENCE_RESULT_ATTACHED if attached else REFERENCE_RESULT_UI_ERROR
        except Exception as exc:
            logger.warning("Reference picker failed for '%s': %s", filename, exc)
            await self._close_reference_ui()
            return REFERENCE_RESULT_UI_ERROR

    async def _attach_reference_from_gallery(self, filename: str) -> str:
        """Attach an existing upload from its gallery card context menu."""
        await self._close_reference_ui()
        try:
            filename_label = self.page.get_by_text(filename, exact=True).first
            if not await filename_label.is_visible(timeout=2000):
                return REFERENCE_RESULT_NOT_FOUND

            card = filename_label.locator(
                "xpath=ancestor::*[self::flow-media-tile or @role='listitem' "
                "or contains(@class, 'media') or contains(@class, 'asset')][1]"
            )
            if await card.count() == 0:
                card = filename_label.locator("xpath=ancestor::*[.//button][1]")
            if await card.count() == 0:
                return REFERENCE_RESULT_UI_ERROR

            await self._remember_reference_locator_keys(card)
            await card.hover()
            menu_btn = card.locator(
                "button[aria-label*='More' i], button[aria-label*='Khác' i], "
                "button[aria-label*='menu' i], button:has(mat-icon:text-is('more_vert')), "
                "button:has-text('⋮')"
            ).first
            if not await menu_btn.is_visible(timeout=2000):
                return REFERENCE_RESULT_UI_ERROR
            await menu_btn.click()
            await asyncio.sleep(0.4)

            add_to_prompt = self.page.locator(
                "[role='menu'] [role='menuitem']:has-text('Thêm vào câu lệnh'), "
                "[role='menu'] [role='menuitem']:has-text('Add to prompt'), "
                ".cdk-overlay-container button:has-text('Thêm vào câu lệnh'), "
                ".cdk-overlay-container button:has-text('Add to prompt')"
            ).first
            if not await add_to_prompt.is_visible(timeout=2500):
                await self._close_reference_ui()
                return REFERENCE_RESULT_UI_ERROR
            await add_to_prompt.click()
            await asyncio.sleep(0.8)
            attached = await self._has_ingredient_chip()
            await self._close_reference_ui()
            return REFERENCE_RESULT_ATTACHED if attached else REFERENCE_RESULT_UI_ERROR
        except Exception as exc:
            logger.warning("Reference gallery fallback failed for '%s': %s", filename, exc)
            await self._close_reference_ui()
            return REFERENCE_RESULT_UI_ERROR

    async def _try_attach_existing_reference(self, filename: str) -> tuple[str, str]:
        picker_result = await self._attach_reference_from_picker(filename)
        if picker_result == REFERENCE_RESULT_ATTACHED:
            return picker_result, "reused_picker"

        gallery_result = await self._attach_reference_from_gallery(filename)
        if gallery_result == REFERENCE_RESULT_ATTACHED:
            return gallery_result, "reused_gallery"
        if (
            picker_result == REFERENCE_RESULT_NOT_FOUND
            and gallery_result == REFERENCE_RESULT_NOT_FOUND
        ):
            return REFERENCE_RESULT_NOT_FOUND, ""
        return REFERENCE_RESULT_UI_ERROR, ""

    async def _sync_required_scene_reference(
        self,
        reference_id: str,
        reference_path: str,
    ) -> None:
        filename = self._reference_filename(reference_path)
        if not filename or not Path(reference_path).is_file():
            self._log_reference_event(
                "attach_failed",
                filename or reference_id,
                detail="local_file_missing",
            )
            raise ReferenceAttachmentError(f"Ảnh tham chiếu không tồn tại: {reference_path}")

        cache = self._project_reference_cache()
        filename_key = filename.casefold()
        known_in_project = filename_key in cache
        confirmed_missing = False

        for _ in range(REFERENCE_ATTACH_RETRY_COUNT):
            result, source = await self._try_attach_existing_reference(filename)
            if result == REFERENCE_RESULT_ATTACHED:
                cache.add(filename_key)
                self._log_reference_event(source, filename)
                return
            if result == REFERENCE_RESULT_NOT_FOUND:
                confirmed_missing = True
                break
            await asyncio.sleep(0.5)

        if known_in_project:
            self._log_reference_event(
                "attach_failed",
                filename,
                detail="known_asset_could_not_be_attached",
            )
            raise ReferenceAttachmentError(
                f"Không thể gắn ảnh tham chiếu đã có '{filename}' trong project Flow."
            )
        if not confirmed_missing:
            self._log_reference_event(
                "attach_failed",
                filename,
                detail="library_lookup_ui_error",
            )
            raise ReferenceAttachmentError(
                f"Không thể kiểm tra thư viện Flow cho ảnh tham chiếu '{filename}'."
            )

        try:
            await self._upload_reference_file(reference_path)
        except Exception as exc:
            self._log_reference_event(
                "attach_failed",
                filename,
                detail=f"upload_failed:{type(exc).__name__}",
            )
            raise ReferenceAttachmentError(
                f"Không thể upload ảnh tham chiếu '{filename}': {exc}"
            ) from exc

        for _ in range(REFERENCE_POST_UPLOAD_RETRY_COUNT):
            if await self._has_ingredient_chip():
                self._log_reference_event("uploaded", filename, detail="attached_by_upload")
                return
            result, _ = await self._try_attach_existing_reference(filename)
            if result == REFERENCE_RESULT_ATTACHED:
                self._log_reference_event("uploaded", filename, detail="attached_after_upload")
                return
            await asyncio.sleep(1)

        self._log_reference_event(
            "attach_failed",
            filename,
            detail="uploaded_but_not_attachable",
        )
        raise ReferenceAttachmentError(
            f"Ảnh '{filename}' đã được gửi lên Flow nhưng không thể gắn vào câu lệnh."
        )

    async def sync_reference_ingredients(
        self,
        reference_ids: list[str] | None,
        reference_paths: dict[str, str] | None = None,
    ) -> None:
        """Ensure the prompt box has the desired reference image attached as an ingredient chip."""
        ref_ids = [str(r).strip() for r in (reference_ids or []) if str(r).strip()]

        if not ref_ids:
            await self.clear_ingredient_chips()
            return

        target_ref = ref_ids[0]

        # Clear existing chips to avoid mixing wrong references
        await self.clear_ingredient_chips()
        await self.dismiss_blocking_dialogs()

        reference_path = str((reference_paths or {}).get(target_ref) or "").strip()
        if reference_path:
            await self._sync_required_scene_reference(target_ref, reference_path)
            return

        add_btn = self.page.locator("button.add-menu-trigger, button[aria-label*='Add ingredients' i]").first
        if not await add_btn.is_visible(timeout=4000):
            logger.warning("Add ingredients button not visible; continuing without attached chip.")
            return

        try:
            await add_btn.click()
            await asyncio.sleep(0.8)
        except Exception as exc:
            logger.warning("Could not click add ingredients button: %s", exc)
            await self.dismiss_blocking_dialogs()
            return

        # Switch to Uploads tab inside the overlay
        uploads_tab = self.page.locator(
            ".cdk-overlay-container mat-list-item:has-text('Uploads'), "
            ".side-nav-list-item:has-text('Uploads'), "
            "button:has-text('Uploads')"
        ).first
        try:
            if await uploads_tab.is_visible(timeout=3000):
                await uploads_tab.click()
                await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning("Could not switch to Uploads tab in add menu: %s", exc)

        clean_ref = target_ref.replace(".jpg", "").replace(".png", "").replace(".webp", "").strip()

        # Select the asset card matching clean_ref or target_ref
        asset_btn = self.page.locator(
            f".cdk-overlay-container button.asset-item:has-text('{clean_ref}'), "
            f".cdk-overlay-container span.asset-title:has-text('{clean_ref}'), "
            f".cdk-overlay-container button.asset-item:has-text('{target_ref}')"
        ).first

        found = False
        try:
            if await asset_btn.is_visible(timeout=2500):
                await asset_btn.click()
                await asyncio.sleep(0.5)
                found = True
        except Exception:
            pass

        # If not directly visible in viewport, try using search input
        if not found:
            try:
                search_input = self.page.locator(
                    ".cdk-overlay-container input[placeholder*='Search' i], "
                    ".cdk-overlay-container input[aria-label*='Search' i]"
                ).first
                if await search_input.is_visible(timeout=1500):
                    await search_input.fill(clean_ref)
                    await self.page.keyboard.press("Enter")
                    await asyncio.sleep(0.6)
                    if await asset_btn.is_visible(timeout=2000):
                        await asset_btn.click()
                        await asyncio.sleep(0.5)
                        found = True
            except Exception:
                pass

        if not found:
            logger.warning("Asset item for reference '%s' not found in Uploads menu. Skipping reference attachment without fallback.", target_ref)
            await self.dismiss_blocking_dialogs()
            return

        # Click "Add to prompt" / "Thêm vào câu lệnh"
        add_to_prompt_btn = self.page.locator(
            ".cdk-overlay-container button.detail-add-to-prompt-btn, "
            "button:has-text('Thêm vào câu lệnh'), "
            "[role='menuitem']:has-text('Thêm vào câu lệnh'), "
            "button:has-text('Add to prompt'), "
            "[role='menuitem']:has-text('Add to prompt'), "
            ".cdk-overlay-container mat-icon:has-text('add')"
        ).first
        try:
            if await add_to_prompt_btn.is_visible(timeout=3000):
                await add_to_prompt_btn.click()
                await asyncio.sleep(0.8)
                logger.info("Successfully attached reference ingredient '%s' to prompt bar.", target_ref)
            else:
                logger.warning("'Add to prompt' / 'Thêm vào câu lệnh' button not visible.")
        except Exception as exc:
            logger.warning("Failed to click 'Add to prompt': %s", exc)
        finally:
            await self.dismiss_blocking_dialogs()

    async def _fetch_blob_payload(self, blob_url: str) -> bytes | None:
        try:
            base64_data = await self.page.evaluate(
                '''async (blobUrl) => {
                    const response = await fetch(blobUrl);
                    const blob = await response.blob();
                    return new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result);
                        reader.onerror = reject;
                        reader.readAsDataURL(blob);
                    });
                }''',
                blob_url,
            )
            if base64_data and "base64," in base64_data:
                import base64
                return base64.b64decode(base64_data.split("base64,")[1])
        except Exception:
            pass
        return None

    async def _validate_image_url(self, url: str) -> bool:
        url = str(url or "").strip()
        variant_key = url.casefold()
        if not url or not variant_key or variant_key in self._rejected_image_urls:
            return False
        body: bytes | None = None
        if url.startswith("blob:"):
            body = await self._fetch_blob_payload(url)
            if not body:
                return False
        else:
            try:
                response = await self.page.request.get(url, timeout=30000)
                if response.status != 200:
                    return False
                body = await response.body()
            except Exception as exc:
                logger.info(
                    "flow_generation media=image state=candidate_unreadable project=%s detail=%s",
                    self._project_id(),
                    type(exc).__name__,
                )
                return False

        if not body:
            return False

        try:
            with Image.open(io.BytesIO(body)) as image:
                image.load()
                width, height = image.size
        except Exception as exc:
            logger.info(
                "flow_generation media=image state=candidate_unreadable project=%s detail=%s",
                self._project_id(),
                type(exc).__name__,
            )
            return False

        if (
            width < MIN_GENERATED_IMAGE_WIDTH
            or height < MIN_GENERATED_IMAGE_HEIGHT
            or width / max(1, height) < MIN_GENERATED_IMAGE_ASPECT_RATIO
        ):
            self._rejected_image_urls.add(variant_key)
            self._last_invalid_image_size = (width, height)
            logger.info(
                "flow_generation media=image state=candidate_invalid project=%s size=%dx%d",
                self._project_id(),
                width,
                height,
            )
            return False

        self._validated_image_payload = (url, body)
        logger.info(
            "flow_generation media=image state=candidate_accepted project=%s size=%dx%d",
            self._project_id(),
            width,
            height,
        )
        return True

    async def _validate_image_candidate(self, candidate: dict) -> str:
        urls: list[str] = []
        for value in [
            candidate.get("src"),
            candidate.get("url"),
            *(candidate.get("urls") or []),
        ]:
            url = str(value or "").strip()
            if not url:
                continue
            upgraded = _upgrade_google_cdn_image_url(url)
            if upgraded and upgraded not in urls:
                urls.append(upgraded)
            if url not in urls:
                urls.append(url)

        for url in urls:
            if await self._validate_image_url(url):
                return url
        return ""

    async def _inspect_image_candidate_detail(
        self,
        candidate: dict,
        baseline_keys: set[str],
    ) -> str:
        candidate_keys = self._candidate_keys(candidate)
        detail_key = next(
            (key for key in candidate_keys if key.startswith("asset:")),
            next(iter(candidate_keys), ""),
        )
        if not detail_key or detail_key in self._inspected_detail_keys:
            return ""
        self._inspected_detail_keys.add(detail_key)

        payload = {
            "assetId": str(candidate.get("assetId") or ""),
            "urls": [
                str(url or "").strip()
                for url in [candidate.get("src"), *(candidate.get("urls") or [])]
                if str(url or "").strip()
            ],
        }
        clicked = await self.page.evaluate(
            r'''({assetId, urls}) => {
                const cards = Array.from(document.querySelectorAll(
                    "flow-media-tile, [data-media-id], [data-asset-id], " +
                    "[data-testid*='media' i], .media-card, " +
                    "flow-canvas-tile, flow-canvas-item, .canvas-tile, " +
                    "flow-message-turn img, [data-turn-id] img, .chat-message img, " +
                    "flow-chat-panel img, .chat-panel img"
                ));
                for (const card of cards) {
                    if (card.closest(
                        "flow-upload-card, [data-testid*='upload' i], " +
                        "[data-testid*='asset-picker' i], .asset-picker, .uploads-picker"
                    )) continue;
                    const cardId = card.getAttribute('data-media-id') ||
                        card.getAttribute('data-asset-id') || card.getAttribute('data-id') || '';
                    const cardUrls = Array.from(card.querySelectorAll(
                        "img[src], img[srcset], [data-image-url], [data-media-url], a[href]"
                    )).flatMap((node) => [
                        node.currentSrc || '', node.src || '', node.href || '',
                        node.getAttribute('data-image-url') || '',
                        node.getAttribute('data-media-url') || ''
                    ]).filter(Boolean);
                    if ((assetId && cardId === assetId) || cardUrls.some((url) => urls.includes(url))) {
                        card.click();
                        return true;
                    }
                }
                return false;
            }''',
            payload,
        )
        if not clicked:
            return ""

        logger.info(
            "flow_generation media=image state=detail_opened project=%s asset=%s",
            self._project_id(),
            str(candidate.get("assetId") or "")[:80],
        )
        await asyncio.sleep(0.5)
        try:
            detailed_candidates = await self._collect_image_candidates()
            asset_id = str(candidate.get("assetId") or "").casefold()
            matching = [
                item
                for item in detailed_candidates
                if not self._candidate_matches_active_reference(item)
                and (
                    not asset_id
                    or str(item.get("assetId") or "").casefold() == asset_id
                    or not str(item.get("assetId") or "").strip()
                )
                and not (self._candidate_keys(item) & baseline_keys)
            ]
            for item in reversed(matching):
                source = await self._validate_image_candidate(item)
                if source:
                    return source
            return ""
        finally:
            await self._close_media_detail()

    async def _resolve_new_media_source(
        self,
        media_type: str,
        baseline_keys: set[str],
        *,
        recover: bool = False,
    ) -> str:
        if media_type == "image":
            candidates = await self._collect_image_candidates()
            remaining = list(candidates)
            unresolved: list[dict] = []
            while remaining:
                selected = self._find_new_media_candidate(
                    remaining,
                    baseline_keys,
                    exclude_uploaded_images=True,
                )
                if selected is None:
                    break
                remaining.remove(selected)
                source = await self._validate_image_candidate(selected)
                if source:
                    return source
                unresolved.append(selected)

            for selected in unresolved:
                source = await self._inspect_image_candidate_detail(selected, baseline_keys)
                if source:
                    return source
            return ""

        candidates = await self._collect_video_candidates()
        source = self._find_new_media_source(candidates, baseline_keys)
        return source

    async def _raise_invalid_image_output(
        self,
        media_type: str,
        submitted_at: float,
    ) -> None:
        if media_type != "image" or not self._last_invalid_image_size:
            return
        width, height = self._last_invalid_image_size
        await self._save_debug_screenshot("image_invalid_output")
        self._log_generation_event(
            media_type,
            "invalid_output",
            submitted_at,
            detail=f"{width}x{height}",
        )
        raise FlowInvalidOutputError(
            "Google Flow trả về ảnh mới không đạt chuẩn 16:9 widescreen "
            f"({width}x{height})."
        )

    async def _wait_for_new_media(
        self,
        *,
        media_type: str,
        baseline_keys: set[str],
        initial_error_texts: set[str],
        submitted_at: float,
        timeout_seconds: float,
    ) -> str:
        hard_deadline = submitted_at + timeout_seconds
        start_deadline = submitted_at + GENERATION_START_TIMEOUT_SECONDS
        generation_started = False
        idle_since: float | None = None
        consecutive_ui_errors = 0
        start_logged = False

        while True:
            await asyncio.sleep(GENERATION_POLL_SECONDS)
            await self.handle_confirmation_prompts()
            now = time.monotonic()

            try:
                source = await self._resolve_new_media_source(media_type, baseline_keys)
                is_generating = await self._read_generation_activity()
                consecutive_ui_errors = 0
            except Exception as exc:
                consecutive_ui_errors += 1
                logger.warning(
                    "flow_generation media=%s state=ui_poll_error project=%s count=%d detail=%s",
                    media_type,
                    self._project_id(),
                    consecutive_ui_errors,
                    exc,
                )
                if consecutive_ui_errors >= MAX_CONSECUTIVE_UI_ERRORS:
                    await self._save_debug_screenshot(f"{media_type}_ui_failed")
                    self._log_generation_event(media_type, "ui_error", submitted_at, detail=str(exc))
                    raise FlowUiStateError(
                        f"Không thể đọc trạng thái giao diện Google Flow cho {media_type}."
                    ) from exc
                continue

            if source:
                self._log_generation_event(media_type, "completed", submitted_at)
                return source

            current_errors = await self._get_existing_error_texts()
            new_errors = [
                text for text in current_errors
                if text not in initial_error_texts and _is_valid_flow_error_text(text)
            ]
            snackbar_error = ""
            try:
                snackbar_error = await self._get_snackbar_error()
            except Exception:
                snackbar_error = ""
            explicit_error = new_errors[0] if new_errors else snackbar_error
            if explicit_error and explicit_error not in initial_error_texts:
                await self._save_debug_screenshot(f"{media_type}_explicit_error")
                self._log_generation_event(
                    media_type,
                    "explicit_error",
                    submitted_at,
                    detail=explicit_error,
                )
                raise FlowGenerationError(
                    f"Google Flow báo lỗi khi tạo {media_type}: {explicit_error}"
                )

            if is_generating:
                generation_started = True
                idle_since = None
                if not start_logged:
                    self._log_generation_event(media_type, "started", submitted_at)
                    start_logged = True
            elif not generation_started:
                if now >= start_deadline:
                    source = await self._resolve_new_media_source(
                        media_type,
                        baseline_keys,
                        recover=True,
                    )
                    if source:
                        self._log_generation_event(media_type, "completed", submitted_at)
                        return source
                    await self._raise_invalid_image_output(media_type, submitted_at)
                    await self._save_debug_screenshot(f"{media_type}_start_failed")
                    self._log_generation_event(media_type, "start_failed", submitted_at)
                    raise FlowGenerationStartError(
                        f"Google Flow không bắt đầu tạo {media_type} sau "
                        f"{GENERATION_START_TIMEOUT_SECONDS:.0f} giây."
                    )
            else:
                if idle_since is None:
                    idle_since = now
                elif now - idle_since >= GENERATION_IDLE_GRACE_SECONDS:
                    source = await self._resolve_new_media_source(
                        media_type,
                        baseline_keys,
                        recover=True,
                    )
                    if source:
                        self._log_generation_event(media_type, "completed", submitted_at)
                        return source
                    await self._raise_invalid_image_output(media_type, submitted_at)
                    await self._save_debug_screenshot(f"{media_type}_result_missing")
                    self._log_generation_event(media_type, "result_missing", submitted_at)
                    raise FlowResultMissingError(
                        f"Google Flow đã dừng nhưng không tìm thấy {media_type} mới."
                    )

            if now >= hard_deadline:
                source = await self._resolve_new_media_source(
                    media_type,
                    baseline_keys,
                    recover=True,
                )
                if source:
                    self._log_generation_event(media_type, "completed", submitted_at)
                    return source
                await self._raise_invalid_image_output(media_type, submitted_at)
                await self._save_debug_screenshot(f"{media_type}_hard_timeout")
                self._log_generation_event(media_type, "hard_timeout", submitted_at)
                raise FlowGenerationTimeout(
                    f"Google Flow không trả về {media_type} mới sau {timeout_seconds:.0f} giây."
                )

    async def generate_scene(
        self,
        prompt: str,
        avoid_prompt: str,
        reference_ids: list[str],
        reference_paths: dict[str, str] | None = None,
    ) -> str:
        self._set_active_reference_filenames(reference_ids, reference_paths)
        self._reset_image_candidate_state()
        await self._ensure_project_canvas()
        try:
            return await self._generate_scene_on_canvas(
                prompt,
                avoid_prompt,
                reference_ids,
                reference_paths,
            )
        finally:
            if self._is_asset_edit_url(str(self.page.url or "")):
                await self._restore_project_canvas()

    async def _generate_scene_on_canvas(
        self,
        prompt: str,
        avoid_prompt: str,
        reference_ids: list[str],
        reference_paths: dict[str, str] | None = None,
    ) -> str:
        # Clean any URL / bracket tags from prompt
        clean_prompt = re.sub(r"\[IMAGE_URL:[^\]]*\]", "", prompt)
        clean_prompt = re.sub(r"https?://\S+", "", clean_prompt)
        clean_prompt = re.sub(r"/api/thumbnails/\S+", "", clean_prompt).strip()

        # References must be visible before the baseline is captured so they can
        # never be mistaken for the result of the next prompt.
        await self.sync_reference_ingredients(reference_ids, reference_paths)
        baseline_keys = await self._capture_stable_image_baseline()
        initial_error_texts = await self._get_existing_error_texts()

        strict_avoid = "text, letters, words, typography, watermark, logo, headline, caption, subtitle, poster text, overlay, title banner"
        if avoid_prompt and avoid_prompt.strip():
            combined_avoid = f"{avoid_prompt.strip()}, {strict_avoid}"
        else:
            combined_avoid = strict_avoid

        full_prompt = f"{clean_prompt}. Avoid: {combined_avoid}"

        # Locate prompt editor
        editor = await self.wait_for_editor(timeout=25.0)
        
        # Click editor with bounded retry and backdrop recovery
        for click_attempt in range(3):
            await self.dismiss_blocking_dialogs()
            try:
                await editor.click(timeout=4000)
                break
            except Exception as e:
                err_msg = str(e).lower()
                if "intercept" in err_msg or "timeout" in err_msg:
                    logger.warning("Editor click attempt %d intercepted or timed out, dismissing backdrop and retrying: %s", click_attempt + 1, e)
                    try:
                        await self.page.keyboard.press("Escape")
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                    continue
                if click_attempt == 2:
                    raise
        await asyncio.sleep(0.3)

        # Clear existing text cleanly
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)

        # Fill prompt text
        try:
            await editor.fill(full_prompt)
        except Exception:
            # Fallback for complex contenteditable elements
            await self.page.keyboard.insert_text(full_prompt)

        # Verify text was entered
        current_text = await editor.evaluate("el => el.innerText || el.value || ''")
        if not current_text.strip():
            logger.info("Editor empty after fill, retrying with keyboard.insert_text...")
            await self.dismiss_blocking_dialogs()
            try:
                await editor.click(timeout=3000)
            except Exception:
                pass
            await self.page.keyboard.insert_text(full_prompt)

        await asyncio.sleep(0.5)

        submitted_at, immediate_source = await self._submit_prompt_and_wait_for_ack(
            editor=editor,
            full_prompt=full_prompt,
            media_type="image",
            baseline_keys=baseline_keys,
        )
        if immediate_source:
            self._log_generation_event("image", "completed", submitted_at)
            return immediate_source

        return await self._wait_for_new_media(
            media_type="image",
            baseline_keys=baseline_keys,
            initial_error_texts=initial_error_texts,
            submitted_at=submitted_at,
            timeout_seconds=IMAGE_GENERATION_TIMEOUT_SECONDS,
        )

    async def download_image(self, asset_url: str, save_path: str):
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        cached_payload = self._validated_image_payload
        if cached_payload and cached_payload[0] == str(asset_url or "").strip():
            target.write_bytes(cached_payload[1])
            self._validated_image_payload = None
            logger.info(
                "Wrote validated Flow image payload (%d bytes) to %s",
                len(cached_payload[1]),
                target,
            )
            _strip_watermark(target)
            return

        if str(asset_url or "").startswith("blob:"):
            body = await self._fetch_blob_payload(asset_url)
            if body and len(body) > 0:
                target.write_bytes(body)
                logger.info("Downloaded blob image successfully (%d bytes) to %s", len(body), target)
                _strip_watermark(target)
                return
            raise RuntimeError(f"Không thể tải ảnh blob từ Google Flow: {asset_url}")

        last_error = None
        for attempt in range(3):
            try:
                response = await self.page.request.get(asset_url, timeout=30000)
                if response.status == 200:
                    body = await response.body()
                    if body and len(body) > 0:
                        with open(target, "wb") as f:
                            f.write(body)
                        logger.info("Downloaded image successfully (%d bytes) to %s", len(body), target)
                        _strip_watermark(target)
                        return
                    raise RuntimeError(f"Tải ảnh từ Google Flow rỗng (0 bytes). Status: {response.status}")
                else:
                    raise RuntimeError(f"HTTP {response.status} khi tải ảnh từ Google Flow: {asset_url}")
            except Exception as e:
                last_error = e
                logger.warning("Attempt %d/3 download_image failed: %s", attempt + 1, e)
                await asyncio.sleep(2)

        raise RuntimeError(f"Không thể tải ảnh sau 3 lần thử: {last_error}")

    async def _get_existing_videos(self, *, strict: bool = False) -> set[str]:
        """Return video URLs currently visible outside upload/reference controls."""
        try:
            return {
                str(item.get("src") or "").strip()
                for item in await self._collect_video_candidates()
                if str(item.get("src") or "").strip()
            }
        except Exception as exc:
            if strict:
                raise FlowUiStateError("Không thể chụp baseline video của Google Flow.") from exc
            return set()

    async def _is_video_mode_active(self) -> bool:
        result = await self.page.evaluate('''() => {
            const visible = (element) => {
                if (!element) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' &&
                    rect.width > 0 && rect.height > 0;
            };
            const videoControls = Array.from(document.querySelectorAll(
                "[data-frame-position], [data-testid*='start-frame' i], " +
                "[data-testid*='end-frame' i], [aria-label*='start frame' i], " +
                "[aria-label*='end frame' i], [aria-label*='khung hình bắt đầu' i], " +
                "[aria-label*='khung hình kết thúc' i]"
            ));
            if (videoControls.some(visible)) return true;

            const toggles = Array.from(document.querySelectorAll(
                "mat-button-toggle, button, [role='tab'], [role='radio'], [role='option']"
            ));
            return toggles.some((element) => {
                if (!visible(element)) return false;
                const text = (element.textContent || '').trim().toLowerCase();
                const aria = (element.getAttribute('aria-label') || '').trim().toLowerCase();
                const isVideo = text === 'video' || aria === 'video' || aria.includes('video mode');
                if (!isVideo) return false;
                const className = String(element.className || '').toLowerCase();
                return element.getAttribute('aria-pressed') === 'true' ||
                    element.getAttribute('aria-selected') === 'true' ||
                    element.getAttribute('aria-checked') === 'true' ||
                    element.getAttribute('data-state') === 'active' ||
                    className.includes('selected') || className.includes('checked') ||
                    className.includes('active');
            });
        }''')
        return result is True

    async def _activate_video_mode(self) -> None:
        deadline = time.monotonic() + VIDEO_MODE_TIMEOUT_SECONDS
        last_error: Exception | None = None
        selectors = [
            "mat-button-toggle:has-text('Video')",
            "button:has-text('Video')",
            "[role='tab']:has-text('Video')",
            "[role='radio']:has-text('Video')",
            "[aria-label*='video mode' i]",
            "button.mode-toggle-video",
        ]
        while time.monotonic() < deadline:
            try:
                if await self._is_video_mode_active():
                    return
            except Exception as exc:
                last_error = exc
            for selector in selectors:
                try:
                    button = self.page.locator(selector).first
                    if await button.is_visible(timeout=300):
                        await button.click(timeout=1500)
                        await asyncio.sleep(0.5)
                        if await self._is_video_mode_active():
                            return
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        await self._save_debug_screenshot("video_mode_failed")
        raise FlowModeError(
            f"Không thể xác nhận chế độ Video của Google Flow sau "
            f"{VIDEO_MODE_TIMEOUT_SECONDS:.0f} giây."
            + (f" Chi tiết: {last_error}" if last_error else "")
        )

    async def _wait_for_video_frame_attachment(
        self,
        position: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = await self.page.evaluate(
                '''(framePosition) => {
                    const patterns = framePosition === 'start'
                        ? ['start-frame', 'start frame', 'khung hình bắt đầu', 'ảnh bắt đầu']
                        : ['end-frame', 'end frame', 'khung hình kết thúc', 'ảnh kết thúc'];
                    const candidates = Array.from(document.querySelectorAll(
                        "[data-frame-position], [data-testid], [aria-label], input[type='file']"
                    ));
                    return candidates.some((element) => {
                        const haystack = [
                            element.getAttribute('data-frame-position') || '',
                            element.getAttribute('data-testid') || '',
                            element.getAttribute('aria-label') || '',
                        ].join(' ').toLowerCase();
                        if (!patterns.some((pattern) => haystack.includes(pattern))) return false;
                        const slot = element.closest(
                            "[data-frame-position], [data-testid], [aria-label], .frame-slot"
                        ) || element;
                        const className = String(slot.className || '').toLowerCase();
                        const state = (slot.getAttribute('data-state') || '').toLowerCase();
                        const input = slot.matches("input[type='file']")
                            ? slot : slot.querySelector("input[type='file']");
                        return Boolean(
                            (input && input.files && input.files.length) ||
                            slot.querySelector('img, video, .preview, [data-media-id]') ||
                            state === 'filled' || state === 'ready' ||
                            className.includes('filled') || className.includes('has-media')
                        );
                    });
                }''',
                position,
            )
            if result is True:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _attach_video_frame(self, frame_path: Path, position: str) -> bool:
        if position not in {"start", "end"}:
            raise ValueError(f"Unsupported video frame position: {position}")
        if not frame_path.is_file():
            raise FlowModeError(f"Video {position} frame does not exist: {frame_path}")

        if position == "start":
            input_selectors = [
                "input[type='file'][data-frame-position='start']",
                "[data-testid*='start-frame' i] input[type='file']",
                "[aria-label*='start frame' i] input[type='file']",
                "[aria-label*='khung hình bắt đầu' i] input[type='file']",
                "[aria-label*='ảnh bắt đầu' i] input[type='file']",
            ]
            button_selectors = [
                "button[aria-label*='start frame' i]",
                "button[aria-label*='khung hình bắt đầu' i]",
                "button[aria-label*='ảnh bắt đầu' i]",
                "[data-testid*='start-frame' i] button",
            ]
        else:
            input_selectors = [
                "input[type='file'][data-frame-position='end']",
                "[data-testid*='end-frame' i] input[type='file']",
                "[aria-label*='end frame' i] input[type='file']",
                "[aria-label*='khung hình kết thúc' i] input[type='file']",
                "[aria-label*='ảnh kết thúc' i] input[type='file']",
            ]
            button_selectors = [
                "button[aria-label*='end frame' i]",
                "button[aria-label*='khung hình kết thúc' i]",
                "button[aria-label*='ảnh kết thúc' i]",
                "[data-testid*='end-frame' i] button",
            ]

        for selector in input_selectors:
            try:
                file_input = self.page.locator(selector).first
                if await file_input.count():
                    await file_input.set_input_files(str(frame_path))
                    return await self._wait_for_video_frame_attachment(position)
            except Exception:
                continue

        for selector in button_selectors:
            try:
                button = self.page.locator(selector).first
                if not await button.is_visible(timeout=300):
                    continue
                async with self.page.expect_file_chooser(timeout=3000) as chooser_info:
                    await button.click(timeout=1500)
                chooser = await chooser_info.value
                await chooser.set_files(str(frame_path))
                return await self._wait_for_video_frame_attachment(position)
            except Exception:
                continue
        return False

    async def generate_scene_video(
        self,
        prompt: str,
        avoid_prompt: str,
        start_frame_path: Path | None = None,
        end_frame_path: Path | None = None,
        reference_ids: list[str] | None = None,
    ) -> str:
        await self._ensure_project_canvas()
        try:
            return await self._generate_scene_video_on_canvas(
                prompt,
                avoid_prompt,
                start_frame_path=start_frame_path,
                end_frame_path=end_frame_path,
                reference_ids=reference_ids,
            )
        finally:
            if self._is_asset_edit_url(str(self.page.url or "")):
                await self._restore_project_canvas()

    async def _generate_scene_video_on_canvas(
        self,
        prompt: str,
        avoid_prompt: str,
        start_frame_path: Path | None = None,
        end_frame_path: Path | None = None,
        reference_ids: list[str] | None = None,
    ) -> str:
        """Generate a video clip from start (and optional end) frame using Veo on Google Flow."""
        baseline_keys = {
            self._media_key(source)
            for source in await self._get_existing_videos(strict=True)
        }
        initial_error_texts = await self._get_existing_error_texts()

        # Character/style references remain ingredients. Start/end images use
        # dedicated image-to-video frame slots and must not be mixed into this list.
        await self.sync_reference_ingredients(reference_ids or [])
        await self._activate_video_mode()

        if start_frame_path and Path(start_frame_path).is_file():
            if not await self._attach_video_frame(Path(start_frame_path), "start"):
                raise FlowModeError("Không thể gắn start frame vào chế độ Video của Google Flow.")
        if end_frame_path and Path(end_frame_path).is_file():
            attached = await self._attach_video_frame(Path(end_frame_path), "end")
            if not attached:
                logger.info(
                    "flow_generation media=video state=end_frame_unsupported project=%s path=%s",
                    self._project_id(),
                    end_frame_path,
                )

        # 2. Add strict negative prompt for text/watermarks and static still frames
        strict_avoid = "still frame, static image, cartoon, text, letters, words, typography, watermark, logo, headline, caption, subtitle, poster text"
        combined_avoid = f"{avoid_prompt}, {strict_avoid}" if avoid_prompt else strict_avoid
        clean_prompt = re.sub(r"\[IMAGE_URL:[^\]]*\]", "", prompt)
        clean_prompt = re.sub(r"https?://\S+", "", clean_prompt)
        clean_prompt = re.sub(r"/api/thumbnails/\S+", "", clean_prompt).strip()
        clean_prompt = re.sub(
            r"(?i)\b(?:still photograph|photography|35mm photography|photo|film still|raw photo|movie still|still photo)\b",
            "cinematic video",
            clean_prompt,
        )
        clean_prompt = re.sub(r"\s+", " ", clean_prompt).strip()
        full_prompt = f"{clean_prompt}. Avoid: {combined_avoid}"

        # 3. Locate prompt editor
        editor = await self.wait_for_editor(timeout=25.0)
        for click_attempt in range(3):
            await self.dismiss_blocking_dialogs()
            try:
                await editor.click(timeout=4000)
                break
            except Exception as e:
                if click_attempt == 2:
                    raise
                await asyncio.sleep(0.5)

        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)

        try:
            await editor.fill(full_prompt)
        except Exception:
            await self.page.keyboard.insert_text(full_prompt)

        await asyncio.sleep(0.5)

        submitted_at, immediate_source = await self._submit_prompt_and_wait_for_ack(
            editor=editor,
            full_prompt=full_prompt,
            media_type="video",
            baseline_keys=baseline_keys,
        )
        if immediate_source:
            self._log_generation_event("video", "completed", submitted_at)
            return immediate_source

        return await self._wait_for_new_media(
            media_type="video",
            baseline_keys=baseline_keys,
            initial_error_texts=initial_error_texts,
            submitted_at=submitted_at,
            timeout_seconds=VIDEO_GENERATION_TIMEOUT_SECONDS,
        )

    async def download_video(self, asset_url: str, save_path: str) -> None:
        """Download generated video file from URL/Blob to local path."""
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        last_error = None
        for attempt in range(3):
            try:
                if asset_url.startswith("blob:"):
                    # Extract blob data using in-page fetch
                    base64_data = await self.page.evaluate(
                        '''async (blobUrl) => {
                            const response = await fetch(blobUrl);
                            const blob = await response.blob();
                            return new Promise((resolve, reject) => {
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result);
                                reader.onerror = reject;
                                reader.readAsDataURL(blob);
                            });
                        }''',
                        asset_url,
                    )
                    import base64
                    if base64_data and "base64," in base64_data:
                        raw_bytes = base64.b64decode(base64_data.split("base64,")[1])
                        target.write_bytes(raw_bytes)
                        if target.stat().st_size > 1000:
                            logger.info("Downloaded video from blob successfully (%d bytes) to %s", len(raw_bytes), target)
                            return
                else:
                    response = await self.page.request.get(asset_url, timeout=60000)
                    if response.status == 200:
                        body = await response.body()
                        if body and len(body) > 1000:
                            target.write_bytes(body)
                            logger.info("Downloaded video successfully (%d bytes) to %s", len(body), target)
                            return
                        raise RuntimeError(f"Tải video từ Google Flow rỗng hoặc quá nhỏ ({len(body) if body else 0} bytes).")
                    raise RuntimeError(f"HTTP {response.status} khi tải video từ Google Flow: {asset_url}")
            except Exception as e:
                last_error = e
                logger.warning("Attempt %d/3 download_video failed: %s", attempt + 1, e)
                await asyncio.sleep(2)

        raise RuntimeError(f"Không thể tải video sau 3 lần thử: {last_error}")

    async def _save_debug_screenshot(self, prefix: str):
        """Save a timestamped screenshot to assist in diagnosing UI issues."""
        try:
            DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = DEBUG_LOG_DIR / f"{prefix}_{timestamp}.png"
            await self.page.screenshot(path=str(path))
            logger.info("Saved debug screenshot to %s", path)
        except Exception as e:
            logger.warning("Failed to save debug screenshot: %s", e)

