"""Facebook Reels web automation service via Playwright CDP connected to GPM-Login profile.

Automates the complete Facebook Reels scheduling / publishing workflow in Meta Business Suite:
- Video upload with progress tracking
- Multi-platform Facebook & Instagram caption syncing
- Custom thumbnail upload
- Tag / keyword assignment
- Playlist auto-selection
- Monetization and remix protection settings
- Precision scheduling
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from auto_yt.services.channel_scanner_service import channel_browser_session, cleanup_owned_page

logger = logging.getLogger(__name__)

REELS_COMPOSER_URL = "https://business.facebook.com/latest/reels_composer/"
CALENDAR_URL = "https://business.facebook.com/latest/content_calendar"


SCREENSHOTS_DIR = Path("data/logs/crossposter")

THUMBNAIL_SECTION_PATTERN = re.compile(r"^(Hình thu nhỏ|Ảnh bìa|Thumbnail|Cover)$", re.IGNORECASE)
THUMBNAIL_UPLOAD_PATTERN = re.compile(
    r"^(Tải hình ảnh lên|Tải ảnh lên|Tải lên hình ảnh|Upload image|Add image|Thêm hình ảnh)$",
    re.IGNORECASE,
)
THUMBNAIL_DEFAULT_PATTERN = re.compile(
    r"^(Chọn hình thu nhỏ gợi ý|Hình thu nhỏ gợi ý|Chọn khung|Suggested thumbnails?|Choose suggested thumbnail|Choose frame)$",
    re.IGNORECASE,
)

SILENCE_MEDIA_INIT_SCRIPT = """
(() => {
    const silenceElement = (el) => {
        try {
            if (el && (el.tagName === 'VIDEO' || el.tagName === 'AUDIO')) {
                el.muted = true;
                el.volume = 0.0;
                if (!el.paused) {
                    el.pause();
                }
            }
        } catch (e) {}
    };

    const silenceAll = () => {
        document.querySelectorAll('video, audio').forEach(silenceElement);
    };

    silenceAll();
    window.addEventListener('play', (e) => silenceElement(e.target), true);
    window.addEventListener('playing', (e) => silenceElement(e.target), true);
    window.addEventListener('loadeddata', (e) => silenceElement(e.target), true);
    window.addEventListener('loadedmetadata', (e) => silenceElement(e.target), true);

    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType === 1) {
                    if (node.tagName === 'VIDEO' || node.tagName === 'AUDIO') {
                        silenceElement(node);
                    } else if (node.querySelectorAll) {
                        node.querySelectorAll('video, audio').forEach(silenceElement);
                    }
                }
            }
        }
    });

    if (document.documentElement) {
        observer.observe(document.documentElement, { childList: true, subtree: true });
    } else {
        document.addEventListener('DOMContentLoaded', () => {
            observer.observe(document.documentElement, { childList: true, subtree: true });
        });
    }
})();
"""


async def upload_file_via_cdp(
    page: Any,
    file_path: Path | str,
    *,
    input_selector: str = 'input[type="file"]',
    trigger_button_locator: Any = None,
    timeout_seconds: float = 15.0,
) -> None:
    """Set file on an input element or via file chooser trigger using direct CDP / local Playwright transfer.

    Bypasses Playwright's 50MB WebSocket transfer limit for local browsers by disabling is_remote
    so Playwright passes local file paths natively to Chromium, with direct CDP fallback.
    """
    resolved_path = str(Path(file_path).resolve())
    if not Path(resolved_path).is_file():
        raise FileNotFoundError(f"File không tồn tại để upload: {resolved_path}")

    # 1. First priority: CDP FileChooser Intercept when trigger button is available (bypasses Playwright 50MB limit)
    if trigger_button_locator is not None:
        try:
            if await trigger_button_locator.is_visible():
                cdp = await page.context.new_cdp_session(page)
                try:
                    await cdp.send("Page.enable")
                    await cdp.send("DOM.enable")
                    fc_future = asyncio.get_event_loop().create_future()
                    cdp.on(
                        "Page.fileChooserOpened",
                        lambda params: not fc_future.done() and fc_future.set_result(params),
                    )
                    await cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})

                    await trigger_button_locator.click()

                    event_params = await asyncio.wait_for(
                        fc_future, timeout=min(max(float(timeout_seconds), 5.0), 20.0)
                    )
                    backend_node_id = event_params.get("backendNodeId")
                    if backend_node_id:
                        await cdp.send(
                            "DOM.setFileInputFiles",
                            {
                                "files": [resolved_path],
                                "backendNodeId": backend_node_id,
                            },
                        )
                        logger.info(
                            "Nạp file thành công qua CDP FileChooser intercept (backendNodeId=%s): %s",
                            backend_node_id,
                            resolved_path,
                        )
                        return
                finally:
                    try:
                        await cdp.detach()
                    except Exception:
                        pass
        except Exception as cdp_fc_err:
            logger.debug(
                "CDP file chooser intercept gặp sự cố (%s), tiếp tục thử các phương thức fallback...",
                cdp_fc_err,
            )

    # 2. Fallback: Direct input selector on page if already in DOM
    try:
        input_loc = page.locator(input_selector).first
        if await input_loc.count() > 0:
            await input_loc.set_input_files(resolved_path)
            logger.info("Nạp file thành công qua direct input selector %s: %s", input_selector, resolved_path)
            return
    except Exception as input_err:
        logger.debug("set_input_files trên direct selector %s gặp lỗi: %s", input_selector, input_err)

    # 3. Fallback: Playwright expect_file_chooser (for small files < 50MB)
    if trigger_button_locator is not None:
        try:
            if await trigger_button_locator.is_visible():
                async with page.expect_file_chooser(timeout=5000) as fc_info:
                    await trigger_button_locator.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(resolved_path)
                logger.info("Nạp file thành công qua standard file_chooser: %s", resolved_path)
                return
        except Exception as fc_err:
            logger.debug("expect_file_chooser fallback gặp lỗi: %s", fc_err)

    # 4. Fallback: Direct CDP DOM query evaluation
    cdp = await page.context.new_cdp_session(page)
    try:
        await cdp.send("DOM.enable")
        res = await cdp.send("Runtime.evaluate", {
            "expression": f"document.querySelector({json.dumps(input_selector)})",
            "returnByValue": False,
        })
        obj_id = (res.get("result") or {}).get("objectId")
        if obj_id:
            await cdp.send("DOM.setFileInputFiles", {
                "files": [resolved_path],
                "objectId": obj_id,
            })
            try:
                await cdp.send("Runtime.callFunctionOn", {
                    "functionDeclaration": """function() {
                        this.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        this.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                    }""",
                    "objectId": obj_id,
                })
            except Exception:
                pass
            logger.info("CDP setFileInputFiles OK: %s (%s)", resolved_path, input_selector)
            return

        raise RuntimeError(f"Không tìm thấy thẻ input tải file ({input_selector}) để nạp {resolved_path}")
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass


class FbBrowserAutomationError(RuntimeError):
    """Raised when an automation action fails on Meta Business Suite with checkpoint context."""

    def __init__(
        self,
        message: str,
        *,
        phase: str = "CP3_CDP_READY",
        screenshot_path: str = "",
        can_resume: bool = True,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.screenshot_path = screenshot_path
        self.can_resume = can_resume


def _format_schedule_time(dt_val: datetime.datetime | str | int | None) -> tuple[str, str, str]:
    """Parse date/time input into (date_str_slash, date_str_iso, time_str_24h).
    
    Example: 2026-09-29 19:00 -> ('29/9/2026', '2026-09-29', '19:00')
    """
    dt: datetime.datetime
    if isinstance(dt_val, (int, float)):
        dt = datetime.datetime.fromtimestamp(dt_val)
    elif isinstance(dt_val, str):
        clean = dt_val.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M"):
            try:
                dt = datetime.datetime.strptime(clean, fmt)
                break
            except ValueError:
                continue
        else:
            try:
                dt = datetime.datetime.fromisoformat(clean)
            except Exception:
                dt = datetime.datetime.now() + datetime.timedelta(hours=2)
    elif isinstance(dt_val, datetime.datetime):
        dt = dt_val
    else:
        dt = datetime.datetime.now() + datetime.timedelta(hours=2)

    date_str_slash = f"{dt.day}/{dt.month}/{dt.year}"
    date_str_iso = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")
    return date_str_slash, date_str_iso, time_str


def _parse_meta_date_value(value: str) -> datetime.date | None:
    """Parse the localized date value rendered by Meta's date control."""
    clean = str(value or "").strip()
    for pattern in (
        r"^(\d{1,2})/(\d{1,2})/(\d{4})$",
        r"^(\d{1,2})\s+Tháng\s+(\d{1,2}),?\s+(\d{4})$",
        r"^(\d{4})-(\d{1,2})-(\d{1,2})$",
    ):
        match = re.match(pattern, clean, flags=re.IGNORECASE)
        if not match:
            continue
        first, second, third = (int(part) for part in match.groups())
        try:
            if pattern.startswith("^(\\d{4})"):
                return datetime.date(first, second, third)
            return datetime.date(third, second, first)
        except ValueError:
            return None
    return None


def _schedule_values_match(
    date_value: str,
    hour_value: str | int,
    minute_value: str | int,
    expected: datetime.datetime,
) -> bool:
    """Compare Meta's separate date/hour/minute controls with the requested time."""
    try:
        return (
            _parse_meta_date_value(date_value) == expected.date()
            and int(hour_value) == expected.hour
            and int(minute_value) == expected.minute
        )
    except (TypeError, ValueError):
        return False


async def _silence_composer_video(page: Any) -> None:
    """Mute and pause preview video player in Meta Reels Composer to eliminate background noise."""
    if page is None:
        return
    try:
        if page.is_closed():
            return
    except Exception:
        return

    # 1. Direct DOM pause and mute on all media elements
    try:
        await page.evaluate(
            """() => {
                document.querySelectorAll('video, audio').forEach(el => {
                    try {
                        el.muted = true;
                        el.volume = 0.0;
                        if (!el.paused) {
                            el.pause();
                        }
                    } catch (e) {}
                });
            }"""
        )
    except Exception as exc:
        logger.debug("Lỗi khi pause/mute video qua DOM evaluate: %s", exc)

    # 2. UI Click fallback if mute / pause icon exists in Meta preview player
    try:
        mute_or_pause_btn = page.locator(
            'div[aria-label*="Tắt tiếng" i], button[aria-label*="Tắt tiếng" i], '
            'div[aria-label*="Mute" i], button[aria-label*="Mute" i], '
            'div[aria-label*="Tạm dừng" i], button[aria-label*="Tạm dừng" i], '
            'div[aria-label*="Pause" i], button[aria-label*="Pause" i]'
        ).first
        if await mute_or_pause_btn.is_visible():
            try:
                await mute_or_pause_btn.click(timeout=800)
            except Exception:
                await mute_or_pause_btn.evaluate("el => el.click()")
    except Exception as exc:
        logger.debug("Lỗi khi click nút tắt tiếng / tạm dừng UI: %s", exc)


async def _dismiss_unwanted_modals(page: Any) -> bool:
    """Detect and dismiss unwanted blocking modals, promo popups, or sub-feature dialogs (e.g. 'Bản nhạc đã dịch')."""
    if page is None:
        return False
    try:
        if page.is_closed():
            return False
    except Exception:
        return False

    await _silence_composer_video(page)

    dismissed = False
    try:
        for _ in range(3):
            dialogs = page.locator('div[role="dialog"]')
            d_count = await dialogs.count()
            found_modal = False
            for idx in range(d_count):
                dlg = dialogs.nth(idx)
                if not await dlg.is_visible():
                    continue

                try:
                    dlg_text = (await dlg.inner_text() or "").strip()
                except Exception:
                    dlg_text = ""

                # Look for close button 'X' or Cancel button
                close_btn = dlg.locator(
                    'button:has-text("Hủy"), div[role="button"]:has-text("Hủy"), '
                    'button:has-text("Cancel"), div[role="button"]:has-text("Cancel"), '
                    'button:has-text("Bỏ qua"), div[role="button"]:has-text("Bỏ qua"), '
                    'button:has-text("Để sau"), div[role="button"]:has-text("Để sau"), '
                    'button:has-text("Không phải bây giờ"), div[role="button"]:has-text("Không phải bây giờ"), '
                    'button:has-text("Đã hiểu"), div[role="button"]:has-text("Đã hiểu"), '
                    'button:has-text("Got it"), div[role="button"]:has-text("Got it"), '
                    'button[aria-label*="Đóng" i], div[aria-label*="Đóng" i], '
                    'button[aria-label*="Close" i], div[aria-label*="Close" i], '
                    'button[aria-label*="Dismiss" i], div[aria-label*="Dismiss" i]'
                ).first

                if await close_btn.is_visible():
                    logger.info("Phát hiện dialog/modal nổi trên Meta (%s)... Đang đóng...", dlg_text[:60].replace("\n", " "))
                    try:
                        await close_btn.click(timeout=3000)
                    except Exception:
                        await close_btn.evaluate("el => el.click()")
                    await asyncio.sleep(1.0)
                    found_modal = True
                    dismissed = True
                else:
                    # Fallback to Escape key
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.8)
                    found_modal = True
                    dismissed = True

            if not found_modal:
                break
    except Exception as exc:
        logger.debug("Lỗi khi kiểm tra / đóng modal phụ: %s", exc)

    return dismissed


async def _verify_video_attachment_present(page: Any) -> bool:
    """Verify that a video asset is currently attached and active in Meta Reels Composer."""
    try:
        if page is None or page.is_closed():
            return False

        # 1. Dismiss any overlay modals that might be blocking DOM view
        await _dismiss_unwanted_modals(page)

        # 2. Check video tag in DOM
        if await page.locator("video").count() > 0:
            return True

        # 3. Check for video indicators in composer (Step 1, Step 2, or Step 3)
        indicators = page.locator(
            'div:has-text("100%"), div:has-text("Video của bạn an toàn"), '
            'div:has-text("an toàn để đăng"), span:has-text(".mp4"), '
            'div:has-text("Âm thanh gốc"), div:has-text("Original audio"), '
            'div:has-text("Thay thế video"), div:has-text("Replace video"), '
            'div[aria-label*="video" i], div[aria-label*="thước phim" i], '
            'div[aria-label*="Xem trước" i], div[aria-label*="Preview" i], '
            'input[type="range"], div[role="slider"]'
        )
        if await indicators.count() > 0:
            for idx in range(await indicators.count()):
                if await indicators.nth(idx).is_visible():
                    return True

        # 4. Check page HTML content for known video / player markers
        content = await page.content()
        markers = [
            "<video",
            "Video của bạn an toàn",
            "an toàn để đăng",
            "Âm thanh gốc",
            "Original audio",
            "Thay thế video",
            "Replace video",
            "reels_composer",
        ]
        if any(marker in content for marker in markers):
            if "Không thể tải video lên" not in content and "Upload failed" not in content:
                return True

        # 5. Check if Step 2 or Step 3 is reached (Meta only allows Step 2/3 when video is attached)
        step_indicators = page.locator(
            'div:has-text("Chia sẻ"), button:has-text("Lên lịch"), '
            'div[role="button"]:has-text("Lên lịch"), button:has-text("Chia sẻ ngay"), '
            'div[role="button"]:has-text("Chia sẻ ngay")'
        )
        if await step_indicators.count() > 0:
            return True

        return False
    except Exception as exc:
        logger.debug("Kiểm tra đính kèm video gặp lỗi: %s", exc)
        return True


STAGE_SHARE_READY = "STAGE_SHARE_READY"
STAGE_EDIT_READY = "STAGE_EDIT_READY"
STAGE_CREATE_READY = "STAGE_CREATE_READY"
STAGE_INVALID_OR_STALE = "STAGE_INVALID"


async def _inspect_composer_stage(page: Any) -> str:
    """Inspect an open Reels Composer page to detect its current wizard stage and readiness."""
    try:
        if page is None:
            return STAGE_INVALID_OR_STALE
        try:
            if page.is_closed():
                return STAGE_INVALID_OR_STALE
        except Exception:
            return STAGE_INVALID_OR_STALE

        # Dismiss any overlay modals first
        await _dismiss_unwanted_modals(page)

        current_url = str(page.url or "")
        if "facebook.com/login" in current_url or "checkpoint" in current_url or "accounts/login" in current_url:
            return STAGE_INVALID_OR_STALE

        if "reels_composer" not in current_url:
            return STAGE_INVALID_OR_STALE

        # Check 1: Step 3 (Chia sẻ)
        step3_indicators = page.locator(
            'div:has-text("Lên lịch")[role="button"], div:has-text("Lên lịch")[role="radio"], '
            'button:has-text("Lên lịch"), div:has-text("Chia sẻ ngay")[role="button"], '
            'button:has-text("Chia sẻ ngay"), div:has-text("Công cụ kiếm tiền"), '
            'div:has-text("Thêm vào danh sách phát"), div:has-text("Cho phép phối lại")'
        )
        if await step3_indicators.count() > 0:
            for idx in range(await step3_indicators.count()):
                if await step3_indicators.nth(idx).is_visible():
                    return STAGE_SHARE_READY

        # Check 2: Step 2 (Chỉnh sửa)
        step2_indicators = page.locator(
            'div[role="contentinfo"] button:has-text("Tiếp"), '
            'div[role="contentinfo"] div[role="button"]:has-text("Tiếp"), '
            'button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp")'
        )
        has_edit_tools = page.locator('div:has-text("Cắt"), div:has-text("Âm thanh"), div:has-text("Phụ đề")')
        if (await step2_indicators.count() > 0) and (await has_edit_tools.count() > 0):
            return STAGE_EDIT_READY

        # Check 3: Step 1 (Tạo) with uploaded video
        if await _verify_video_attachment_present(page):
            return STAGE_CREATE_READY

        return STAGE_INVALID_OR_STALE
    except Exception as exc:
        logger.debug("Lỗi khi kiểm tra stage của Reels Composer: %s", exc)
        return STAGE_INVALID_OR_STALE


async def _visible_locators(locator: Any) -> list[Any]:
    visible: list[Any] = []
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            visible.append(candidate)
    return visible


def _normalize_ui_text(value: Any) -> str:
    return " ".join(str(value or "").split())


async def _find_thumbnail_section(page: Any) -> Any | None:
    """Return the smallest visible composer card that owns thumbnail controls."""
    headings = page.get_by_text(THUMBNAIL_SECTION_PATTERN, exact=True)
    for heading_index in range(await headings.count()):
        heading = headings.nth(heading_index)
        if not await heading.is_visible():
            continue
        for depth in range(1, 8):
            container = heading.locator(
                f"xpath=ancestor::*[self::section or self::div][{depth}]"
            )
            if await container.count() == 0:
                continue
            text = _normalize_ui_text(await container.inner_text())
            if len(text) > 4000:
                break
            upload_controls = container.get_by_text(THUMBNAIL_UPLOAD_PATTERN, exact=True)
            default_controls = container.get_by_text(THUMBNAIL_DEFAULT_PATTERN, exact=True)
            if await upload_controls.count() or await default_controls.count():
                return container
    return None


async def _visible_exact_text_candidates(scope: Any, pattern: re.Pattern[str]) -> list[Any]:
    candidates = scope.locator(
        'button, [role="button"], [role="tab"], [role="radio"], a, label, span, div'
    ).filter(has_text=pattern)
    exact: list[Any] = []
    for index in range(await candidates.count()):
        candidate = candidates.nth(index)
        if not await candidate.is_visible():
            continue
        if pattern.fullmatch(_normalize_ui_text(await candidate.inner_text())):
            exact.append(candidate)
    return exact


async def _find_thumbnail_upload_trigger(section: Any) -> Any | None:
    """Prefer the upload CTA over the identically named upload tab."""
    candidates = await _visible_exact_text_candidates(section, THUMBNAIL_UPLOAD_PATTERN)
    for candidate in reversed(candidates):
        if await _is_thumbnail_selector(candidate):
            continue
        return candidate
    return None


async def _is_thumbnail_selector(candidate: Any) -> bool:
    role = str(await candidate.get_attribute("role") or "").casefold()
    aria_selected = str(await candidate.get_attribute("aria-selected") or "").casefold()
    inside_selector = await candidate.evaluate(
        "el => Boolean(el.closest('[role=tab], [role=radio]'))"
    )
    return role in {"tab", "radio"} or aria_selected in {"true", "false"} or inside_selector


async def _thumbnail_preview_signature(section: Any) -> dict[str, Any]:
    return await section.evaluate(
        """root => {
            const visible = element => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            };
            const images = Array.from(root.querySelectorAll('img'))
                .filter(visible)
                .map(image => image.currentSrc || image.src || '')
                .filter(Boolean);
            const backgrounds = Array.from(root.querySelectorAll('*'))
                .filter(visible)
                .map(element => window.getComputedStyle(element).backgroundImage)
                .filter(value => value && value !== 'none');
            const fileNames = Array.from(root.querySelectorAll('input[type=file]'))
                .flatMap(input => Array.from(input.files || []).map(file => file.name));
            return {
                images: [...new Set(images)],
                backgrounds: [...new Set(backgrounds)],
                fileNames,
            };
        }"""
    )


def _thumbnail_upload_verified(
    before: dict[str, Any],
    after: dict[str, Any],
    file_name: str,
    *,
    crop_confirmed: bool,
) -> bool:
    if crop_confirmed:
        return True
    if file_name in set(after.get("fileNames") or []):
        return True
    for key in ("images", "backgrounds"):
        if set(after.get(key) or []) - set(before.get(key) or []):
            return True
    return False


async def _set_scoped_thumbnail_input(section: Any, thumbnail_path: Path) -> bool:
    """Set a generic file input only when it belongs to the thumbnail card."""
    inputs = section.locator('input[type="file"]')
    preferred: list[Any] = []
    generic: list[Any] = []
    for index in range(await inputs.count()):
        candidate = inputs.nth(index)
        accept = str(await candidate.get_attribute("accept") or "").casefold()
        if "video" in accept:
            continue
        if "image" in accept:
            preferred.append(candidate)
        elif not accept:
            generic.append(candidate)
    for candidate in preferred + generic:
        try:
            await candidate.set_input_files(str(thumbnail_path.resolve()))
            return True
        except Exception:
            continue
    return False


async def _restore_default_thumbnail(page: Any, section: Any | None) -> bool:
    """Leave Meta in its automatic-frame mode after an optional thumbnail failure."""
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    if section is None:
        return False
    candidates = await _visible_exact_text_candidates(section, THUMBNAIL_DEFAULT_PATTERN)
    for candidate in candidates:
        try:
            await candidate.click(timeout=5000)
            await asyncio.sleep(0.8)
            return True
        except Exception:
            continue
    return False


async def _upload_optional_thumbnail(page: Any, thumbnail_path: Path) -> dict[str, Any]:
    """Best-effort custom thumbnail upload that never falls back to the video input."""
    section = await _find_thumbnail_section(page)
    if section is None:
        return {
            "status": "thumbnail_unavailable",
            "warning": "Không tìm thấy khu vực hình thu nhỏ của Meta",
            "fallback_restored": False,
        }

    before = await _thumbnail_preview_signature(section)
    try:
        upload_candidates = await _visible_exact_text_candidates(section, THUMBNAIL_UPLOAD_PATTERN)
        for candidate in upload_candidates:
            if await _is_thumbnail_selector(candidate):
                await candidate.click(timeout=5000)
                await asyncio.sleep(1.0)
                break

        trigger = await _find_thumbnail_upload_trigger(section)
        try:
            if trigger is None:
                raise RuntimeError("Không tìm thấy nút mở trình chọn thumbnail")
            async with page.expect_file_chooser(timeout=10000) as chooser_info:
                await trigger.click()
            chooser = await chooser_info.value
            await chooser.set_files(str(thumbnail_path.resolve()))
        except Exception as exc:
            if not await _set_scoped_thumbnail_input(section, thumbnail_path):
                raise exc

        crop_confirmed = False
        crop_save_btn = page.locator(
            'div[role="dialog"] button:has-text("Lưu"), div[role="dialog"] div[role="button"]:has-text("Lưu"), '
            'div[role="dialog"] button:has-text("Save"), div[role="dialog"] button:has-text("Áp dụng"), '
            'div[role="dialog"] button:has-text("Xong"), div[role="dialog"] button:has-text("Done")'
        ).first
        try:
            await crop_save_btn.wait_for(state="visible", timeout=5000)
            await crop_save_btn.click()
            crop_confirmed = True
        except Exception:
            pass

        for _ in range(15):
            await asyncio.sleep(1.0)
            after = await _thumbnail_preview_signature(section)
            if _thumbnail_upload_verified(
                before,
                after,
                thumbnail_path.name,
                crop_confirmed=crop_confirmed,
            ):
                return {
                    "status": "custom_uploaded",
                    "warning": "",
                    "fallback_restored": False,
                }
        raise RuntimeError("Meta chưa hiển thị bằng chứng thumbnail tùy chỉnh đã được gắn")
    except Exception as exc:
        restored = await _restore_default_thumbnail(page, section)
        return {
            "status": "fallback_default" if restored else "thumbnail_unavailable",
            "warning": str(exc),
            "fallback_restored": restored,
        }


async def _set_and_verify_schedule_controls(
    page: Any,
    expected: datetime.datetime,
) -> list[dict[str, str]]:
    """Set every visible Facebook/Instagram date and time control, then read it back."""
    date_inputs = await _visible_locators(
        page.locator('input[placeholder="dd/mm/yyyy"], input[type="date"]')
    )
    hour_inputs = await _visible_locators(
        page.locator('input[role="spinbutton"][aria-label="giờ" i], input[aria-label="hour" i]')
    )
    minute_inputs = await _visible_locators(
        page.locator('input[role="spinbutton"][aria-label="phút" i], input[aria-label="minute" i]')
    )
    if not date_inputs or len(date_inputs) != len(hour_inputs) or len(date_inputs) != len(minute_inputs):
        raise FbBrowserAutomationError(
            "Không tìm thấy đầy đủ bộ chọn ngày, giờ và phút của Meta",
            phase="CP7_SCHEDULE_SET",
            can_resume=True,
        )

    requested_date = expected.strftime("%d/%m/%Y")
    requested_hour = f"{expected.hour:02d}"
    requested_minute = f"{expected.minute:02d}"
    evidence: list[dict[str, str]] = []

    for index, (date_input, hour_input, minute_input) in enumerate(
        zip(date_inputs, hour_inputs, minute_inputs)
    ):
        date_value = ""
        hour_value = ""
        minute_value = ""
        for attempt in range(2):
            for control, value in (
                (date_input, requested_date),
                (hour_input, requested_hour),
                (minute_input, requested_minute),
            ):
                await control.click()
                await control.press("Control+A")
                await control.press_sequentially(value, delay=35)
                await control.press("Tab")
                await asyncio.sleep(0.2)

            date_value = (await date_input.input_value() or "").strip()
            hour_value = str(await hour_input.get_attribute("aria-valuenow") or "").strip()
            minute_value = str(await minute_input.get_attribute("aria-valuenow") or "").strip()
            if _schedule_values_match(date_value, hour_value, minute_value, expected):
                break
            if attempt == 0:
                await asyncio.sleep(0.5)
        else:
            raise FbBrowserAutomationError(
                f"Meta không giữ đúng lịch đã nhập cho nhóm #{index + 1}: "
                f"{date_value} {hour_value}:{minute_value}",
                phase="CP7_SCHEDULE_SET",
                can_resume=True,
            )
        evidence.append(
            {
                "date": expected.strftime("%Y-%m-%d"),
                "time": expected.strftime("%H:%M"),
            }
        )

    return evidence


def _calendar_week_position(
    target_date: datetime.date,
    today: datetime.date | None = None,
) -> tuple[int, int]:
    """Return the Sunday-based week offset and column index used by Meta Calendar."""
    current_date = today or datetime.date.today()
    current_week_start = current_date - datetime.timedelta(
        days=(current_date.weekday() + 1) % 7
    )
    target_week_start = target_date - datetime.timedelta(
        days=(target_date.weekday() + 1) % 7
    )
    return (
        (target_week_start - current_week_start).days // 7,
        (target_date.weekday() + 1) % 7,
    )


async def _verify_calendar_schedule(
    page: Any,
    *,
    content_title: str,
    expected: datetime.datetime,
    target_page_id: str,
) -> dict[str, Any]:
    """Read Meta Calendar after submission and verify the Facebook card's day and time."""
    clean_title = str(content_title or "").strip()
    if not clean_title:
        return {"verified": False, "reason": "missing_content_title"}

    calendar_url = CALENDAR_URL
    if target_page_id:
        calendar_url = f"{CALENDAR_URL}/?asset_id={target_page_id}"
    await page.goto(calendar_url, wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(3.0)

    week_button = page.locator('button:has-text("Tuần"), [role="button"]:has-text("Tuần")').first
    if await week_button.is_visible():
        await week_button.click()
        await asyncio.sleep(0.8)
    today_button = page.locator('[role="button"]:has-text("Hôm nay"), button:has-text("Hôm nay")').first
    if await today_button.is_visible():
        await today_button.click()
        await asyncio.sleep(0.8)

    week_offset, expected_column = _calendar_week_position(expected.date())
    direction = "Right" if week_offset > 0 else "Left"
    for _ in range(abs(week_offset)):
        navigation_button = page.locator(
            f'[role="button"]:has-text("{direction}"), button:has-text("{direction}")'
        ).first
        if not await navigation_button.is_visible():
            return {"verified": False, "reason": f"missing_{direction.lower()}_navigation"}
        await navigation_button.click()
        await asyncio.sleep(0.8)

    expected_time = expected.strftime("%H:%M")
    observed: list[dict[str, Any]] = []
    for verification_attempt in range(4):
        await asyncio.sleep(2.0 if verification_attempt == 0 else 3.0)
        candidates = page.locator("[aria-label]")
        observed = []
        for index in range(await candidates.count()):
            card = candidates.nth(index)
            aria_label = str(await card.get_attribute("aria-label") or "")
            if not aria_label.startswith(clean_title) or not await card.is_visible():
                continue
            card_time = (await card.inner_text() or "").strip()
            platforms = await card.locator("img").evaluate_all(
                "els => els.map(el => el.alt).filter(Boolean)"
            )
            column_index = await card.evaluate(
                """el => {
                    let node = el;
                    while (node && node.parentElement) {
                        const parent = node.parentElement;
                        const siblings = Array.from(parent.children).filter(child => {
                            const rect = child.getBoundingClientRect();
                            return rect.width > 100 && rect.height > 100;
                        });
                        if (siblings.length === 7 && siblings.includes(node)) {
                            return siblings.indexOf(node);
                        }
                        node = parent;
                    }
                    return -1;
                }"""
            )
            evidence = {
                "time": card_time,
                "platforms": platforms,
                "column_index": int(column_index),
            }
            observed.append(evidence)
            if (
                card_time == expected_time
                and "Facebook" in platforms
                and int(column_index) == expected_column
            ):
                return {
                    "verified": True,
                    "matches_requested": True,
                    "source": "meta_calendar",
                    "date": expected.strftime("%Y-%m-%d"),
                    "actual_scheduled_timestamp": int(expected.timestamp()),
                    **evidence,
                }

    target_week_start = expected.date() - datetime.timedelta(days=expected_column)
    for evidence in observed:
        if "Facebook" not in evidence["platforms"] or not 0 <= evidence["column_index"] <= 6:
            continue
        try:
            actual_time = datetime.datetime.strptime(evidence["time"], "%H:%M").time()
        except ValueError:
            continue
        actual_date = target_week_start + datetime.timedelta(days=evidence["column_index"])
        actual_datetime = datetime.datetime.combine(actual_date, actual_time)
        return {
            "verified": True,
            "matches_requested": False,
            "source": "meta_calendar",
            "date": actual_date.isoformat(),
            "actual_scheduled_timestamp": int(actual_datetime.timestamp()),
            **evidence,
        }

    return {
        "verified": False,
        "reason": "matching_facebook_card_not_found",
        "expected_date": expected.strftime("%Y-%m-%d"),
        "expected_time": expected_time,
        "expected_column": expected_column,
        "observed": observed,
    }


async def schedule_reel_via_gpm(
    profile_id: str,
    video_path: Path | str,
    caption: str,
    *,
    item_id: int | None = None,
    thumb_path: Path | str | None = None,
    tags: list[str] | None = None,
    schedule_datetime: datetime.datetime | str | int | None = None,
    publish_now: bool = False,
    content_title: str = "",
    page_name: str = "",
    target_page_id: str = "",
    timeout_seconds: float = 300.0,
    state_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Automate scheduling or publishing a Facebook Reel via Meta Business Suite inside a GPM profile session."""
    clean_profile_id = str(profile_id or "").strip()
    if not clean_profile_id:
        raise ValueError("Profile ID GPM không được để trống")

    v_path = Path(video_path)
    if not v_path.is_file():
        raise FileNotFoundError(f"File video không tồn tại: {video_path}")

    t_path: Path | None = None
    if thumb_path:
        t_path = Path(thumb_path)
        if not t_path.is_file():
            logger.warning("File thumbnail không tồn tại: %s. Sẽ bỏ qua upload thumbnail.", thumb_path)
            t_path = None

    date_slash, date_iso, time_24h = _format_schedule_time(schedule_datetime)
    expected_schedule = datetime.datetime.strptime(
        f"{date_iso} {time_24h}", "%Y-%m-%d %H:%M"
    )
    full_schedule_label = f"{date_slash} {time_24h}"

    current_checkpoint = "CP3_CDP_READY"
    page = None

    def notify(phase: str, message: str, progress: int | None = None, screenshot: str = ""):
        nonlocal current_checkpoint
        current_checkpoint = phase
        logger.info("[FB-Playwright %s][%s] %s", clean_profile_id, phase, message)
        if state_callback:
            try:
                state_callback({
                    "phase": phase,
                    "message": message,
                    "progress": progress,
                    "profile_id": clean_profile_id,
                    "item_id": item_id,
                    "screenshot": screenshot,
                })
            except Exception:
                pass

    notify("CP3_CDP_READY", f"Đang kết nối Profile {clean_profile_id} qua Playwright CDP...", 5)

    async def _capture_error_screenshot(target_page: Any) -> str:
        try:
            if not target_page or target_page.is_closed():
                return ""
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(datetime.datetime.now().timestamp())
            prefix = f"checkpoint_item_{item_id}" if item_id else f"checkpoint_profile_{clean_profile_id}"
            file_name = f"{prefix}_{ts}.png"
            dest = SCREENSHOTS_DIR / file_name
            await target_page.screenshot(path=str(dest), full_page=False)
            return str(dest)
        except Exception as ss_err:
            logger.debug("Không thể chụp màn hình lỗi: %s", ss_err)
            return ""

    async with channel_browser_session(clean_profile_id) as (context, _browser, _profile_meta):
        # 1. Look for existing open tab with Reels Composer
        page = None
        for p in list(context.pages):
            try:
                if not p.is_closed() and "reels_composer" in (p.url or ""):
                    page = p
                    break
            except Exception:
                pass

        initial_stage = STAGE_INVALID_OR_STALE
        if page:
            await page.bring_to_front()
            initial_stage = await _inspect_composer_stage(page)
            logger.info("Tìm thấy tab Reels Composer hiện có tại stage: %s", initial_stage)
        else:
            page = await context.new_page()
            await page.bring_to_front()

        try:
            await page.add_init_script(SILENCE_MEDIA_INIT_SCRIPT)
        except Exception as init_err:
            logger.debug("Không thể nạp init script mute media: %s", init_err)
        await _silence_composer_video(page)

        preserve_page = True
        try:
            # Set default timeout for individual actions
            page.set_default_timeout(25000)

            # SMART RESUME: If tab is already in Step 3 (Chia sẻ) or Step 2 (Chỉnh sửa), attempt fast jump!
            if initial_stage in {STAGE_SHARE_READY, STAGE_EDIT_READY}:
                try:
                    logger.info("Thực hiện Smart In-Page Resume tại stage %s...", initial_stage)
                    if initial_stage == STAGE_EDIT_READY:
                        notify("CP7_SCHEDULE_SET", "Nhận diện tab đang ở Bước 2 (Chỉnh sửa). Bỏ qua Chỉnh sửa sang Bước 3...", 75)
                        await _dismiss_unwanted_modals(page)
                        next_btn_2 = page.locator(
                            'div[role="contentinfo"] button:has-text("Tiếp"), '
                            'div[role="contentinfo"] div[role="button"]:has-text("Tiếp"), '
                            'button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp")'
                        ).last
                        if await next_btn_2.is_visible():
                            await next_btn_2.click()
                            await asyncio.sleep(2.0)
                        await _dismiss_unwanted_modals(page)

                    notify("CP7_SCHEDULE_SET", f"Tiếp tục trực tiếp từ Bước 3 ({full_schedule_label})...", 80)
                    await _dismiss_unwanted_modals(page)

                    schedule_control_evidence: list[dict[str, str]] = []
                    if publish_now:
                        now_btn = page.locator(
                            'div[role="button"]:has-text("Chia sẻ ngay"), button:has-text("Chia sẻ ngay"), '
                            'div[role="radio"]:has-text("Chia sẻ ngay")'
                        ).first
                        if await now_btn.is_visible():
                            await now_btn.click()
                            await asyncio.sleep(1.0)
                    else:
                        schedule_radio_btn = page.locator(
                            'div[role="button"]:has-text("Lên lịch"), button:has-text("Lên lịch"), '
                            'div[role="radio"]:has-text("Lên lịch")'
                        ).first
                        if await schedule_radio_btn.is_visible():
                            await schedule_radio_btn.click()
                            await asyncio.sleep(1.5)
                            schedule_control_evidence = await _set_and_verify_schedule_controls(
                                page,
                                expected_schedule,
                            )

                    # Step 13 Final Click
                    action_btn_name = "Chia sẻ" if publish_now else "Lên lịch"
                    notify("CP8_SUBMITTED", f"Đang bấm nút '{action_btn_name}' hoàn tất...", 90)

                    if cancel_check and cancel_check():
                        raise FbBrowserAutomationError(
                            "Tác vụ đã được yêu cầu dừng trước khi gửi lên Meta",
                            phase="CP7_SCHEDULE_SET",
                            can_resume=True,
                        )

                    # Verify Video Integrity
                    await _dismiss_unwanted_modals(page)
                    if not await _verify_video_attachment_present(page):
                        raise RuntimeError("Đính kèm video không xác nhận được trong Smart Resume")

                    await _dismiss_unwanted_modals(page)
                    await page.keyboard.press("Escape")
                    await page.evaluate("() => document.activeElement && document.activeElement.blur()")
                    await asyncio.sleep(1.0)

                    final_btn = page.locator(
                        f'div[role="contentinfo"] button:has-text("{action_btn_name}"), '
                        f'div[role="contentinfo"] div[role="button"]:has-text("{action_btn_name}"), '
                        f'div[role="main"] ~ div div[role="button"]:has-text("{action_btn_name}"), '
                        f'button:has-text("{action_btn_name}"), div[role="button"]:has-text("{action_btn_name}")'
                    ).last

                    final_button_ready = False
                    for w_i in range(15):
                        if await final_btn.is_visible():
                            aria_dis = await final_btn.get_attribute("aria-disabled")
                            tab_idx = await final_btn.get_attribute("tabindex")
                            if aria_dis != "true" and tab_idx != "-1":
                                final_button_ready = True
                                break
                        await asyncio.sleep(1.0)

                    if not final_button_ready:
                        raise RuntimeError(f"Nút '{action_btn_name}' chưa sẵn sàng trong Smart Resume")

                    try:
                        await final_btn.click(force=True, timeout=10000)
                    except Exception:
                        await final_btn.evaluate("el => el.click()")

                    await asyncio.sleep(3.0)

                    submission_confirmed = False
                    for _ in range(20):
                        await asyncio.sleep(1.0)
                        luc_khac_btn = page.locator(
                            'div[role="dialog"] button:has-text("Lúc khác"), div[role="dialog"] div[role="button"]:has-text("Lúc khác"), '
                            'button:has-text("Lúc khác"), div[role="button"]:has-text("Lúc khác"), '
                            'button:has-text("Maybe later"), div[role="button"]:has-text("Maybe later"), '
                            'button:has-text("Not now"), div[role="button"]:has-text("Not now"), '
                            'button:has-text("Để sau"), div[role="button"]:has-text("Để sau"), '
                            'button:has-text("Bỏ qua"), div[role="button"]:has-text("Bỏ qua"), '
                            'button:has-text("Dismiss"), div[role="button"]:has-text("Dismiss")'
                        ).first
                        if await luc_khac_btn.is_visible():
                            notify("CP8_SUBMITTED", "Đã tìm thấy thông báo xác nhận của Meta. Đang bấm 'Lúc khác'...", 94)
                            submission_confirmed = True
                            try:
                                await luc_khac_btn.click(timeout=5000)
                            except Exception:
                                await luc_khac_btn.evaluate("el => el.click()")
                            await asyncio.sleep(2.0)
                            break

                        dialog_close_btn = page.locator(
                            'div[role="dialog"] div[aria-label="Đóng"], div[role="dialog"] button[aria-label="Đóng"], '
                            'div[role="dialog"] button[aria-label="Close"], div[role="dialog"] div[aria-label="Close"], '
                            'div[aria-label="Đóng"], button[aria-label="Đóng"], button[aria-label="Close"]'
                        ).first
                        if await dialog_close_btn.is_visible():
                            notify("CP8_SUBMITTED", "Đã đóng modal xác nhận của Meta...", 94)
                            submission_confirmed = True
                            try:
                                await dialog_close_btn.click(timeout=5000)
                            except Exception:
                                await dialog_close_btn.evaluate("el => el.click()")
                            await asyncio.sleep(2.0)
                            break

                        if "content_calendar" in page.url or "latest/home" in page.url or "latest/posts" in page.url:
                            submission_confirmed = True
                            break

                    if submission_confirmed:
                        calendar_evidence: dict[str, Any] = {}
                        if not publish_now:
                            notify("CP8_SUBMITTED", "Đang đối chiếu lại lịch thực tế trên Meta Calendar...", 97)
                            try:
                                calendar_evidence = await _verify_calendar_schedule(
                                    page,
                                    content_title=content_title,
                                    expected=expected_schedule,
                                    target_page_id=target_page_id,
                                )
                            except Exception as cal_err:
                                calendar_evidence = {"verified": False, "reason": str(cal_err)}

                        actual_scheduled_timestamp = int(calendar_evidence.get("actual_scheduled_timestamp") or 0)
                        schedule_matches = bool(
                            publish_now
                            or (actual_scheduled_timestamp and abs(actual_scheduled_timestamp - int(expected_schedule.timestamp())) <= 60)
                        )
                        notify("CP8_SUBMITTED", f"Thành công! Reels đã được lên lịch lúc {full_schedule_label}.", 100)
                        preserve_page = False
                        return {
                            "success": True,
                            "status": "published" if publish_now else "meta_scheduled" if schedule_matches else "schedule_mismatch",
                            "schedule_verified": bool(publish_now or calendar_evidence.get("verified")),
                            "submission_confirmed": submission_confirmed,
                            "actual_scheduled_timestamp": 0 if publish_now else actual_scheduled_timestamp,
                            "verification_evidence": {
                                "source": "meta_calendar" if calendar_evidence else "meta_confirmation",
                                "controls": schedule_control_evidence,
                                "calendar": calendar_evidence,
                            },
                            "scheduled_time": full_schedule_label,
                            "scheduled_time_iso": f"{date_iso}T{time_24h}:00",
                            "target_page_id": target_page_id,
                            "profile_id": clean_profile_id,
                            "message": f"Đã tiếp tục và lên lịch Reels thành công trên Facebook Meta Business Suite lúc {full_schedule_label}",
                        }

                except Exception as jump_err:
                    logger.warning("Smart In-Page Resume gặp lỗi (%s). Tự động fallback sang quy trình nạp mới...", jump_err)

            # FULL WORKFLOW (Fallback or Fresh Start)
            # 1. Navigate to Reels Composer
            notify("CP4_COMPOSER_READY", "Đang mở giao diện Meta Business Suite Reels Composer...", 10)
            target_composer_url = REELS_COMPOSER_URL
            if target_page_id:
                target_composer_url = f"{REELS_COMPOSER_URL}?asset_id={target_page_id}"

            try:
                await page.goto(target_composer_url, wait_until="domcontentloaded", timeout=45000)
            except Exception as nav_err:
                logger.warning("Gặp lỗi tải trang ban đầu, thử lại: %s", nav_err)
                await page.goto(REELS_COMPOSER_URL, wait_until="load", timeout=45000)

            await asyncio.sleep(3.0)
            current_url = page.url

            # Check if redirected to login / checkpoint
            if "facebook.com/login" in current_url or "checkpoint" in current_url or "accounts/login" in current_url:
                ss_file = await _capture_error_screenshot(page)
                raise FbBrowserAutomationError(
                    f"Profile {clean_profile_id} chưa đăng nhập Facebook hoặc bị checkpoint an minh. Vui lòng đăng nhập trên trình duyệt rồi bấm Tiếp tục.",
                    phase="CP4_COMPOSER_READY",
                    screenshot_path=ss_file,
                    can_resume=True,
                )

            # If opened in Calendar or Home, click "Tạo bài viết" -> "Tạo thước phim"
            if "content_calendar" in current_url or "latest/home" in current_url:
                notify("navigating", "Đang điều hướng từ Lịch sang Tạo thước phim...", 15)
                try:
                    create_btn_arrow = page.locator(
                        'div[role="button"]:has-text("Tạo bài viết"), button:has-text("Tạo bài viết")'
                    ).locator('xpath=following-sibling::div | xpath=..//i | xpath=..//svg').first
                    if await create_btn_arrow.is_visible():
                        await create_btn_arrow.click()
                        await asyncio.sleep(1.0)
                        reel_opt = page.locator('div[role="menuitem"]:has-text("Tạo thước phim"), text="Tạo thước phim"').first
                        if await reel_opt.is_visible():
                            await reel_opt.click()
                            await page.wait_for_load_state("domcontentloaded")
                except Exception as click_err:
                    logger.debug("Không click được dropdown Tạo thước phim, điều hướng trực tiếp: %s", click_err)
                    await page.goto(REELS_COMPOSER_URL, wait_until="domcontentloaded", timeout=30000)

            await asyncio.sleep(2.5)

            # 2. Upload Video
            notify("CP5_ASSET_UPLOADED", f"Đang tải lên file video {v_path.name} ({v_path.stat().st_size // (1024*1024)} MB)...", 20)
            
            # Look for video file input or Add video button
            add_btn = page.locator(
                'button:has-text("Thêm video"), div[role="button"]:has-text("Thêm video"), '
                'button:has-text("Add video"), div[role="button"]:has-text("Add video")'
            ).first

            await upload_file_via_cdp(
                page,
                v_path,
                input_selector='input[type="file"][accept*="video"], input[type="file"]',
                trigger_button_locator=add_btn,
                timeout_seconds=15.0,
            )
            await _silence_composer_video(page)

            notify("CP5_ASSET_UPLOADED", "Đang chờ video upload lên Meta Business Suite (100%)...", 35)

            # Wait for upload progress to reach 100% or button "Tiếp" enabled
            upload_finished = False
            for loop_i in range(240): # up to 6 minutes for video upload
                await asyncio.sleep(1.5)
                await _silence_composer_video(page)
                # Check 100% text or "Video của bạn an toàn để đăng!"
                page_text = await page.content()
                if "100%" in page_text or "Video của bạn an toàn" in page_text or "an toàn để đăng" in page_text:
                    upload_finished = True
                    break

                # Update progress if percentage found
                pct_match = re.search(r"\b(\d{1,3})%", page_text)
                if pct_match:
                    try:
                        pct_num = int(pct_match.group(1))
                        if 0 <= pct_num <= 100:
                            notify("CP5_ASSET_UPLOADED", f"Đang upload video lên Meta Business Suite ({pct_num}%)...", 20 + int(pct_num * 0.25))
                    except Exception:
                        pass

                # Or check if Next button is enabled and not disabled
                next_btn = page.locator('button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")').last
                if await next_btn.is_visible() and await next_btn.is_enabled():
                    # check if still has 10% / 20% / upload progress bar
                    if "100%" in page_text or not re.search(r"\b\d{1,2}%\b", page_text):
                        upload_finished = True
                        break

            await _silence_composer_video(page)
            notify("CP6_METADATA_FILLED", "Video đã tải lên xong. Đang nhập mô tả, tùy chỉnh FB & IG...", 50)

            # 3. Facebook & Instagram Destination Handling
            try:
                # Open destination dropdown to ensure only valid target page is selected (and avoid IG length restrictions)
                dropdown = page.locator('div[role="button"]:has-text("Đăng lên"), div:has-text("Đăng lên") + div[role="button"]').first
                if await dropdown.is_visible():
                    await dropdown.click()
                    await asyncio.sleep(1.0)
                    # Deselect any extra selected accounts (like Instagram) that are not the target page
                    selected_opts = page.locator('div[role="listbox"] div[role="option"][aria-selected="true"]')
                    sel_count = await selected_opts.count()
                    for s_idx in range(sel_count):
                        opt = selected_opts.nth(s_idx)
                        txt = (await opt.inner_text() or "").strip()
                        if page_name and page_name in txt:
                            continue
                        logger.info("Bỏ chọn tài khoản đích bổ sung (%s) để tránh giới hạn video/Reels...", txt)
                        await opt.click()
                        await asyncio.sleep(0.5)
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
            except Exception as dest_err:
                logger.debug("Kiểm tra đích đăng gặp lỗi nhẹ: %s", dest_err)

            # Toggle Facebook & Instagram Customization if present
            try:
                ig_switch = page.locator(
                    'div[role="switch"]:has-text("Tùy chỉnh bài viết cho Facebook và Instagram"), '
                    'label:has-text("Tùy chỉnh bài viết cho Facebook và Instagram")'
                ).first
                if await ig_switch.is_visible():
                    aria_checked = await ig_switch.get_attribute("aria-checked")
                    if aria_checked == "false":
                        await ig_switch.click()
                        await asyncio.sleep(1.0)
            except Exception as switch_err:
                logger.debug("Không tìm thấy toggle tùy chỉnh FB/IG hoặc đã bật: %s", switch_err)

            # 4. Fill Caption / Text
            clean_caption = (caption or "").strip()
            if clean_caption:
                try:
                    caption_box = page.locator(
                        'div[contenteditable="true"][role="textbox"], '
                        'textarea[placeholder*="Cho người xem biết"], '
                        'div[contenteditable="true"]'
                    ).first
                    if await caption_box.is_visible():
                        await caption_box.click()
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Backspace")
                        await page.keyboard.insert_text(clean_caption)
                        await asyncio.sleep(1.0)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
                except Exception as cap_err:
                    logger.warning("Lỗi khi điền caption: %s", cap_err)

            # 5. Upload Custom Thumbnail (optional)
            thumbnail_result: dict[str, Any] = {
                "status": "not_requested",
                "warning": "",
                "fallback_restored": False,
            }
            if t_path and t_path.is_file():
                notify("CP6_METADATA_FILLED", f"Đang tải lên hình thu nhỏ {t_path.name}...", 60)
                thumbnail_result = await _upload_optional_thumbnail(page, t_path)
                if thumbnail_result["status"] == "custom_uploaded":
                    logger.info("Custom thumbnail đã tải lên thành công: %s", t_path.name)
                else:
                    screenshot_path = await _capture_error_screenshot(page)
                    logger.warning(
                        "Không thể xác minh custom thumbnail (%s); status=%s, fallback_restored=%s. "
                        "Tiếp tục với khung hình video mặc định: %s",
                        t_path.name,
                        thumbnail_result["status"],
                        thumbnail_result["fallback_restored"],
                        thumbnail_result["warning"],
                    )
                    notify(
                        "CP6_METADATA_FILLED",
                        "Thumbnail tùy chỉnh không khả dụng; tiếp tục với khung hình mặc định của Facebook.",
                        62,
                        screenshot=screenshot_path,
                    )

            # 6. Add Tags (Thẻ)
            tag_list = list(tags or [])
            if page_name and page_name not in tag_list:
                tag_list.append(page_name)

            if tag_list:
                notify("CP6_METADATA_FILLED", f"Đang thêm {len(tag_list)} thẻ từ khóa...", 65)
                try:
                    tag_input = page.locator(
                        'input[placeholder*="Thêm từ khóa liên quan"], '
                        'input[placeholder*="Add keywords"], '
                        'div:has-text("Thẻ") + div input'
                    ).first
                    if await tag_input.is_visible():
                        for t in tag_list[:6]: # Max 6 tags
                            clean_t = str(t).strip()
                            if not clean_t:
                                continue
                            await tag_input.click()
                            await tag_input.fill(clean_t)
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.5)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
                except Exception as tag_err:
                    logger.debug("Lỗi khi điền tags: %s", tag_err)

            # GUARD 1: Verify Video Integrity before moving to Step 2
            if not await _verify_video_attachment_present(page):
                ss_file = await _capture_error_screenshot(page)
                raise FbBrowserAutomationError(
                    "Đính kèm video bị mất trong trình soạn thảo Meta (có thể do lỗi nạp file). Dừng lại để tránh tạo bài viết text rác.",
                    phase="CP5_ASSET_UPLOADED",
                    screenshot_path=ss_file,
                    can_resume=True,
                )

            # 7. Move from Step 1 (Tạo) -> Step 2 (Chỉnh sửa)
            notify("CP6_METADATA_FILLED", "Chờ xử lý video và chuyển sang bước Chỉnh sửa...", 70)
            await _dismiss_unwanted_modals(page)
            await page.keyboard.press("Escape")
            await page.evaluate("() => document.activeElement && document.activeElement.blur()")
            await asyncio.sleep(1.0)

            next_btn_1 = page.locator(
                'div[role="contentinfo"] button:has-text("Tiếp"), '
                'div[role="contentinfo"] div[role="button"]:has-text("Tiếp"), '
                'button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")'
            ).last

            # Wait for next button to be fully enabled
            for w_i in range(60):
                if await next_btn_1.is_visible():
                    aria_dis = await next_btn_1.get_attribute("aria-disabled")
                    tab_idx = await next_btn_1.get_attribute("tabindex")
                    if aria_dis != "true" and tab_idx != "-1":
                        break
                await asyncio.sleep(2.0)

            await next_btn_1.click()
            await asyncio.sleep(2.5)
            await _dismiss_unwanted_modals(page)

            # 8. Move from Step 2 (Chỉnh sửa) -> Step 3 (Chia sẻ)
            notify("CP7_SCHEDULE_SET", "Bỏ qua bước Chỉnh sửa, chuyển sang bước Chia sẻ...", 75)
            await _dismiss_unwanted_modals(page)
            await page.keyboard.press("Escape")
            await page.evaluate("() => document.activeElement && document.activeElement.blur()")
            await asyncio.sleep(1.0)

            next_btn_2 = page.locator(
                'div[role="contentinfo"] button:has-text("Tiếp"), '
                'div[role="contentinfo"] div[role="button"]:has-text("Tiếp"), '
                'button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")'
            ).last
            if await next_btn_2.is_visible():
                for w_i in range(30):
                    aria_dis = await next_btn_2.get_attribute("aria-disabled")
                    tab_idx = await next_btn_2.get_attribute("tabindex")
                    if aria_dis != "true" and tab_idx != "-1":
                        break
                    await asyncio.sleep(1.0)
                await next_btn_2.click()
                await asyncio.sleep(2.5)
            await _dismiss_unwanted_modals(page)

            # 9. Step 3 (Chia sẻ): Schedule & Options Configuration
            notify("CP7_SCHEDULE_SET", f"Đang cấu hình lịch đăng ({full_schedule_label}) và các tùy chọn...", 80)
            await _dismiss_unwanted_modals(page)

            schedule_control_evidence: list[dict[str, str]] = []
            if publish_now:
                # Option: Chia sẻ ngay
                now_btn = page.locator(
                    'div[role="button"]:has-text("Chia sẻ ngay"), button:has-text("Chia sẻ ngay"), '
                    'div[role="radio"]:has-text("Chia sẻ ngay")'
                ).first
                if await now_btn.is_visible():
                    await now_btn.click()
                    await asyncio.sleep(1.0)
            else:
                # Option: Lên lịch (Schedule)
                schedule_radio_btn = page.locator(
                    'div[role="button"]:has-text("Lên lịch"), button:has-text("Lên lịch"), '
                    'div[role="radio"]:has-text("Lên lịch")'
                ).first
                if not await schedule_radio_btn.is_visible():
                    raise FbBrowserAutomationError(
                        "Không tìm thấy tùy chọn Lên lịch trên Meta",
                        phase="CP7_SCHEDULE_SET",
                        can_resume=True,
                    )
                await schedule_radio_btn.click()
                await asyncio.sleep(1.5)
                schedule_control_evidence = await _set_and_verify_schedule_controls(
                    page,
                    expected_schedule,
                )

            # 10. Playlists: Add to all available playlists
            try:
                await _dismiss_unwanted_modals(page)
                playlist_toggle = page.locator(
                    'div[role="switch"]:has-text("Thêm vào danh sách phát"), '
                    'label:has-text("Thêm vào danh sách phát")'
                ).first
                if await playlist_toggle.is_visible():
                    aria_checked = await playlist_toggle.get_attribute("aria-checked")
                    if aria_checked == "false":
                        await playlist_toggle.click()
                        await asyncio.sleep(1.0)

                    # Open playlist dropdown
                    playlist_dropdown = page.locator(
                        'div[role="button"]:has-text("Chọn danh sách phát"), '
                        'div:has-text("Danh sách phát") + div[role="button"], '
                        'div:has-text("Chọn danh sách phát")'
                    ).first
                    if await playlist_dropdown.is_visible():
                        await playlist_dropdown.click()
                        await asyncio.sleep(1.2)

                        # Select all checkboxes in playlist dropdown
                        playlist_checkboxes = page.locator('div[role="dialog"] input[type="checkbox"], div[role="menu"] input[type="checkbox"], div[role="listbox"] input[type="checkbox"]')
                        cb_count = await playlist_checkboxes.count()
                        for c_idx in range(cb_count):
                            cb = playlist_checkboxes.nth(c_idx)
                            if not await cb.is_checked():
                                await cb.click()
                                await asyncio.sleep(0.3)

                        # Click outside to close dropdown
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
            except Exception as pl_err:
                logger.debug("Lỗi khi chọn danh sách phát: %s", pl_err)
            finally:
                await _dismiss_unwanted_modals(page)

            # 11. Subtitles & Remix
            try:
                await _dismiss_unwanted_modals(page)
                # Ensure Subtitle is checked
                sub_cb = page.locator('input[type="checkbox"]').filter(has=page.locator('xpath=ancestor::div[contains(., "Phụ đề")]')).first
                if await sub_cb.is_visible() and not await sub_cb.is_checked():
                    await sub_cb.click()
                    await asyncio.sleep(0.5)
                    await _dismiss_unwanted_modals(page)

                # Remix: Select "Không cho phép"
                no_remix_radio = page.locator(
                    'div[role="radio"]:has-text("Không cho phép"), '
                    'label:has-text("Không cho phép"), '
                    'div:has-text("Không cho phép")[role="button"]'
                ).first
                if await no_remix_radio.is_visible():
                    await no_remix_radio.click()
                    await asyncio.sleep(0.5)
                    await _dismiss_unwanted_modals(page)
            except Exception as remix_err:
                logger.debug("Lỗi cài đặt Remix/Phụ đề: %s", remix_err)
            finally:
                await _dismiss_unwanted_modals(page)

            # 12. Monetization (Công cụ kiếm tiền)
            try:
                await _dismiss_unwanted_modals(page)
                monetization_header = page.locator('div:has-text("Công cụ kiếm tiền")').first
                if await monetization_header.is_visible():
                    monetize_switch = monetization_header.locator('xpath=..//div[@role="switch"]').first
                    if await monetize_switch.is_visible():
                        if await monetize_switch.get_attribute("aria-checked") == "false":
                            await monetize_switch.click()
                            await asyncio.sleep(1.0)
                            await _dismiss_unwanted_modals(page)

                    # Check all sub-checkboxes (Sao, Kiếm tiền từ nội dung)
                    monetize_cbs = page.locator('div:has-text("Công cụ kiếm tiền")').locator('xpath=..//input[@type="checkbox"]')
                    m_count = await monetize_cbs.count()
                    for m_idx in range(m_count):
                        m_cb = monetize_cbs.nth(m_idx)
                        if not await m_cb.is_checked():
                            await m_cb.click()
                            await asyncio.sleep(0.3)
                            await _dismiss_unwanted_modals(page)
            except Exception as mon_err:
                logger.debug("Lỗi cài đặt Kiếm tiền: %s", mon_err)
            finally:
                await _dismiss_unwanted_modals(page)

            # 13. Final Click: "Lên lịch" / "Chia sẻ"
            action_btn_name = "Chia sẻ" if publish_now else "Lên lịch"
            notify("CP8_SUBMITTED", f"Đang bấm nút '{action_btn_name}' hoàn tất...", 90)

            if cancel_check and cancel_check():
                raise FbBrowserAutomationError(
                    "Tác vụ đã được yêu cầu dừng trước khi gửi lên Meta",
                    phase="CP7_SCHEDULE_SET",
                    can_resume=True,
                )

            # GUARD 2: Verify Video Integrity before final submission
            await _dismiss_unwanted_modals(page)
            if not await _verify_video_attachment_present(page):
                ss_file = await _capture_error_screenshot(page)
                raise FbBrowserAutomationError(
                    "Đính kèm video không còn tồn tại trước khi gửi lên Meta. Dừng lại để tránh tạo bài viết text rác.",
                    phase="CP7_SCHEDULE_SET",
                    screenshot_path=ss_file,
                    can_resume=True,
                )

            await _dismiss_unwanted_modals(page)
            await page.keyboard.press("Escape")
            await page.evaluate("() => document.activeElement && document.activeElement.blur()")
            await asyncio.sleep(1.0)

            final_btn = page.locator(
                f'div[role="contentinfo"] button:has-text("{action_btn_name}"), '
                f'div[role="contentinfo"] div[role="button"]:has-text("{action_btn_name}"), '
                f'div[role="main"] ~ div div[role="button"]:has-text("{action_btn_name}"), '
                f'button:has-text("{action_btn_name}"), div[role="button"]:has-text("{action_btn_name}")'
            ).last

            # Wait for final button to be enabled (up to 30s)
            final_button_ready = False
            for w_i in range(30):
                if await final_btn.is_visible():
                    aria_dis = await final_btn.get_attribute("aria-disabled")
                    tab_idx = await final_btn.get_attribute("tabindex")
                    if aria_dis != "true" and tab_idx != "-1":
                        final_button_ready = True
                        break
                await asyncio.sleep(1.0)

            if not final_button_ready:
                raise FbBrowserAutomationError(
                    f"Nút '{action_btn_name}' không sẵn sàng để gửi",
                    phase="CP7_SCHEDULE_SET",
                    can_resume=True,
                )

            try:
                await final_btn.click(force=True, timeout=10000)
            except Exception as click_exc:
                logger.debug("Force click nút cuối gặp lỗi (%s), dùng JS evaluate click...", click_exc)
                await final_btn.evaluate("el => el.click()")

            await asyncio.sleep(3.0)

            # 14. Handle post-submission popups / modals (e.g. "Đã lên lịch đăng thước phim", "Đã đăng thước phim")
            submission_confirmed = False
            for _ in range(20):
                await asyncio.sleep(1.0)
                # Check for "Lúc khác" / "Maybe later" / "Not now" button first
                luc_khac_btn = page.locator(
                    'div[role="dialog"] button:has-text("Lúc khác"), div[role="dialog"] div[role="button"]:has-text("Lúc khác"), '
                    'button:has-text("Lúc khác"), div[role="button"]:has-text("Lúc khác"), '
                    'button:has-text("Maybe later"), div[role="button"]:has-text("Maybe later"), '
                    'button:has-text("Not now"), div[role="button"]:has-text("Not now"), '
                    'button:has-text("Để sau"), div[role="button"]:has-text("Để sau"), '
                    'button:has-text("Bỏ qua"), div[role="button"]:has-text("Bỏ qua"), '
                    'button:has-text("Dismiss"), div[role="button"]:has-text("Dismiss")'
                ).first

                if await luc_khac_btn.is_visible():
                    notify("CP8_SUBMITTED", "Đã tìm thấy thông báo xác nhận của Meta. Đang bấm 'Lúc khác'...", 94)
                    submission_confirmed = True
                    try:
                        await luc_khac_btn.click(timeout=5000)
                    except Exception:
                        await luc_khac_btn.evaluate("el => el.click()")

                    # Wait for page to finish loading / redirecting after clicking "Lúc khác"
                    notify("CP8_SUBMITTED", "Đang chờ trang tải xong sau khi bấm 'Lúc khác'...", 96)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception as load_err:
                        logger.debug("wait_for_load_state sau khi bấm Lúc khác: %s", load_err)
                    await asyncio.sleep(2.0)
                    break

                # Fallback: close button 'X' in modal dialog
                dialog_close_btn = page.locator(
                    'div[role="dialog"] div[aria-label="Đóng"], div[role="dialog"] button[aria-label="Đóng"], '
                    'div[role="dialog"] button[aria-label="Close"], div[role="dialog"] div[aria-label="Close"], '
                    'div[aria-label="Đóng"], button[aria-label="Đóng"], button[aria-label="Close"]'
                ).first
                if await dialog_close_btn.is_visible():
                    notify("CP8_SUBMITTED", "Đã đóng modal xác nhận của Meta...", 94)
                    submission_confirmed = True
                    try:
                        await dialog_close_btn.click(timeout=5000)
                    except Exception:
                        await dialog_close_btn.evaluate("el => el.click()")

                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception as load_err:
                        logger.debug("wait_for_load_state sau khi đóng popup: %s", load_err)
                    await asyncio.sleep(2.0)
                    break

                if "content_calendar" in page.url or "latest/home" in page.url or "latest/posts" in page.url:
                    submission_confirmed = True
                    break

            if not submission_confirmed:
                raise FbBrowserAutomationError(
                    "Meta chưa trả về xác nhận sau khi gửi; không ghi nhận là đã lên lịch để tránh sai lệch",
                    phase="CP8_SUBMITTED",
                    can_resume=False,
                )

            calendar_evidence: dict[str, Any] = {}
            if not publish_now:
                notify("CP8_SUBMITTED", "Đang đối chiếu lại lịch thực tế trên Meta Calendar...", 97)
                try:
                    calendar_evidence = await _verify_calendar_schedule(
                        page,
                        content_title=content_title,
                        expected=expected_schedule,
                        target_page_id=target_page_id,
                    )
                except Exception as cal_err:
                    logger.warning("Lỗi khi đối chiếu Meta Calendar: %s", cal_err)
                    calendar_evidence = {"verified": False, "reason": str(cal_err)}

                if not calendar_evidence.get("verified"):
                    if submission_confirmed:
                        logger.info("Meta Calendar verify chưa đọc được thẻ ngay, sử dụng xác nhận thành công từ modal.")
                    else:
                        raise FbBrowserAutomationError(
                            "Đã gửi video nhưng không đọc lại được đúng lịch trên Meta Calendar",
                            phase="CP8_SUBMITTED",
                            can_resume=False,
                        )

            actual_scheduled_timestamp = int(
                calendar_evidence.get("actual_scheduled_timestamp") or 0
            )
            schedule_matches = bool(
                publish_now
                or (
                    actual_scheduled_timestamp
                    and abs(actual_scheduled_timestamp - int(expected_schedule.timestamp())) <= 60
                )
            )
            if schedule_matches:
                notify("CP8_SUBMITTED", f"Thành công! Reels đã được lên lịch lúc {full_schedule_label}.", 100)
            else:
                notify("CP8_SUBMITTED", "Meta đã nhận video nhưng lịch thực tế khác lịch yêu cầu.", 100)
            preserve_page = False
            result_status = (
                "published"
                if publish_now
                else "meta_scheduled" if schedule_matches else "schedule_mismatch"
            )

            return {
                "success": True,
                "status": result_status,
                "schedule_verified": bool(publish_now or calendar_evidence.get("verified")),
                "submission_confirmed": submission_confirmed,
                "actual_scheduled_timestamp": 0 if publish_now else actual_scheduled_timestamp,
                "verification_evidence": {
                    "source": "meta_calendar" if calendar_evidence else "meta_confirmation",
                    "controls": schedule_control_evidence,
                    "calendar": calendar_evidence,
                    "thumbnail": thumbnail_result,
                },
                "scheduled_time": full_schedule_label,
                "scheduled_time_iso": f"{date_iso}T{time_24h}:00",
                "target_page_id": target_page_id,
                "profile_id": clean_profile_id,
                "message": (
                    f"Đã lên lịch Reels thành công trên Facebook Meta Business Suite lúc {full_schedule_label}"
                    + (
                        " (dùng khung hình mặc định của Facebook)"
                        if thumbnail_result["status"] in {"fallback_default", "thumbnail_unavailable"}
                        else ""
                    )
                ),
            }

        except Exception as exc:
            logger.error("Lỗi trong quá trình tự động upload Reels Facebook: %s", exc, exc_info=True)
            if isinstance(exc, FbBrowserAutomationError):
                notify(exc.phase, f"Lỗi: {exc}", 0, screenshot=exc.screenshot_path)
                raise
            ss_file = await _capture_error_screenshot(page)
            notify(current_checkpoint, f"Lỗi: {exc}", 0, screenshot=ss_file)
            raise FbBrowserAutomationError(
                f"Thao tác trình duyệt gặp lỗi tại bước {current_checkpoint}: {exc}",
                phase=current_checkpoint,
                screenshot_path=ss_file,
                can_resume=True,
            ) from exc
        finally:
            await cleanup_owned_page(context, page, preserve=preserve_page)
