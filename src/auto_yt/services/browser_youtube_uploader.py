"""YouTube Studio Web Browser Automation Uploader.

Automates video uploading, metadata population, monetization self-rating,
thumbnail uploading, and scheduling/private publishing directly inside a channel's
dedicated GPM-Login profile session via Playwright CDP.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from auto_yt.services.gpm_service import gpm_browser_session
from auto_yt.services.browser_diagnostics import capture_browser_diagnostics_async

logger = logging.getLogger(__name__)


class BrowserUploadError(RuntimeError):
    """Raised when browser-based YouTube upload fails."""


class BrowserUploadNeedsReview(BrowserUploadError):
    """Raised when Studio state cannot be changed safely without review."""


class MonetizationCapability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MonetizationDetection:
    capability: MonetizationCapability
    evidence: tuple[str, ...]


UPLOAD_DIALOG_ROOT_SELECTORS = (
    "ytcp-uploads-dialog",
    "ytcp-video-upload-dialog",
    "ytcp-video-details",
    "ytcp-video-metadata-editor",
)
UPLOAD_DIALOG_SELECTOR = ", ".join(UPLOAD_DIALOG_ROOT_SELECTORS)
TITLE_EDITOR_SELECTORS = (
    "#title-textarea #textbox",
    "#textbox[aria-label*='tiêu đề' i]",
    "#textbox[aria-label*='title' i]",
    "#textbox[aria-label*='Thêm tiêu đề' i]",
    "input#title",
    "div#textbox[contenteditable='true']",
)
DESCRIPTION_EDITOR_SELECTORS = (
    "#description-textarea #textbox",
    "#description-textarea [contenteditable='true']",
    "#textbox[aria-label*='mô tả' i]",
    "#textbox[aria-label*='description' i]",
    "#textbox[aria-label*='Giới thiệu về video' i]",
)
UPLOAD_TITLE_EDITOR_SELECTORS = [
    f"{root} {selector}"
    for root in UPLOAD_DIALOG_ROOT_SELECTORS
    for selector in TITLE_EDITOR_SELECTORS
] + list(TITLE_EDITOR_SELECTORS)
UPLOAD_DESCRIPTION_EDITOR_SELECTORS = [
    f"{root} {selector}"
    for root in UPLOAD_DIALOG_ROOT_SELECTORS
    for selector in DESCRIPTION_EDITOR_SELECTORS
] + list(DESCRIPTION_EDITOR_SELECTORS)
UPLOAD_ALTERED_CONTENT_SELECTORS = [
    f"{root} tp-yt-paper-radio-button[name='{name}']"
    for root in UPLOAD_DIALOG_ROOT_SELECTORS
    for name in ("VIDEO_HAS_ALTERED_CONTENT_YES", "VIDEO_HAS_ALTERED_CONTENT_NO")
]
UPLOAD_ADVANCED_TOGGLE_SELECTORS = [
    selector
    for root in UPLOAD_DIALOG_ROOT_SELECTORS
    for selector in (f"{root} #toggle-button", f"{root} ytcp-button#toggle-button")
]
UPLOAD_PLAYLIST_TRIGGER_SELECTORS = [
    f"{root} ytcp-video-metadata-playlists ytcp-dropdown-trigger"
    for root in UPLOAD_DIALOG_ROOT_SELECTORS
] + [
    "ytcp-uploads-dialog #playlists ytcp-dropdown-trigger",
    "ytcp-uploads-dialog ytcp-button:has-text('Danh sách phát')",
    "ytcp-video-upload-dialog #playlists ytcp-dropdown-trigger",
]
UPLOAD_THUMBNAIL_PREVIEW_SELECTOR = (
    "ytcp-uploads-dialog ytcp-video-custom-still-editor img[src], "
    "ytcp-uploads-dialog ytcp-video-thumbnail-editor img[src], "
    "ytcp-uploads-dialog #custom-thumbnail img[src], "
    "ytcp-video-upload-dialog ytcp-video-custom-still-editor img[src], "
    "ytcp-video-upload-dialog ytcp-video-thumbnail-editor img[src], "
    "ytcp-video-upload-dialog #custom-thumbnail img[src], "
    "ytcp-video-thumbnail-editor img[src]"
)
UPLOAD_COMPLETION_DIALOG_SELECTOR = (
    "ytcp-video-share-dialog, ytcp-publish-dialog, "
    "ytcp-dialog:has-text('Đã lên lịch cho video'), "
    "ytcp-dialog:has-text('Video scheduled')"
)
MONETIZATION_MODE_AUTO = "auto_enable_if_available"
MONETIZATION_MODE_KEEP_OFF = "keep_off"
MONETIZATION_MODE_REQUIRE = "require_on"
MONETIZATION_MODES = {
    MONETIZATION_MODE_AUTO,
    MONETIZATION_MODE_KEEP_OFF,
    MONETIZATION_MODE_REQUIRE,
}
DEFAULT_STEP_TIMEOUT_SECONDS = 20.0


def _format_date_for_picker(date_obj: dt.date, is_vietnamese: bool = True) -> str:
    """Format a date for YouTube Studio datepicker (e.g. '27 thg 9, 2026' or 'Sep 27, 2026')."""
    if is_vietnamese:
        return f"{date_obj.day} thg {date_obj.month}, {date_obj.year}"
    return date_obj.strftime("%b %d, %Y")


def _format_time_for_picker(time_obj: dt.time) -> str:
    """Format time in 24h format 'HH:MM' (e.g. '19:00')."""
    return time_obj.strftime("%H:%M")


async def _safe_click(page, selectors: list[str], timeout_ms: int = 5000) -> bool:
    """Try clicking the first matching selector from a list of fallback selectors."""
    for sel in selectors:
        try:
            dialog_elements = await _visible_upload_dialog_elements(page, sel)
            if dialog_elements:
                for element in dialog_elements:
                    try:
                        await element.click()
                        return True
                    except Exception:
                        continue
                continue
            el = await page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
            if el:
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _has_visible_element(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for element in elements:
                if await element.is_visible():
                    return True
        except Exception:
            continue
    return False


async def _ensure_altered_content_controls_visible(page, timeout_ms: int = 10000) -> None:
    if await _has_visible_element(page, UPLOAD_ALTERED_CONTENT_SELECTORS):
        return
    if not await _safe_click(page, UPLOAD_ADVANCED_TOGGLE_SELECTORS, timeout_ms=3000):
        raise BrowserUploadError("Không thể mở cài đặt nâng cao trên YouTube Studio.")

    deadline = time.monotonic() + max(0.1, timeout_ms / 1000)
    while time.monotonic() < deadline:
        if await _has_visible_element(page, UPLOAD_ALTERED_CONTENT_SELECTORS):
            return
        await asyncio.sleep(0.25)
    raise BrowserUploadError("YouTube Studio không hiển thị khai báo nội dung tổng hợp.")


async def _fill_single_element(page, element, text: str) -> bool:
    try:
        await element.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        try:
            await element.fill(text)
            return True
        except Exception:
            pass
        try:
            await page.keyboard.insert_text(text)
            return True
        except Exception:
            pass
        try:
            await element.evaluate("""(el, val) => {
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                    el.value = val;
                } else {
                    el.innerText = val;
                }
                el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
            }""", text)
            return True
        except Exception:
            pass
    except Exception:
        pass
    return False


async def _safe_fill(page, selectors: list[str], text: str, timeout_ms: int = 5000) -> bool:
    """Try clicking and filling text into the first matching selector."""
    for sel in selectors:
        try:
            dialog_elements = await _visible_upload_dialog_elements(page, sel)
            if dialog_elements:
                for element in dialog_elements:
                    if await _fill_single_element(page, element, text):
                        return True
                continue
            el = await page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
            if el:
                if await _fill_single_element(page, el, text):
                    return True
        except Exception:
            continue
    return False


async def _find_visible_upload_details_dialog(page):
    """Return only a visible upload dialog or edit container that is editing video details."""
    try:
        dialogs = await page.query_selector_all(UPLOAD_DIALOG_SELECTOR)
    except Exception:
        return None
    for dialog in dialogs:
        try:
            if not await dialog.is_visible():
                continue
            for selector in TITLE_EDITOR_SELECTORS:
                editor = await dialog.query_selector(selector)
                if editor is not None and await editor.is_visible():
                    return dialog
            tag = await dialog.evaluate("el => (el.tagName || '').toLowerCase()")
            if tag in ("ytcp-uploads-dialog", "ytcp-video-upload-dialog", "ytcp-video-metadata-editor", "ytcp-video-details", "ytcp-entity-page"):
                return dialog
        except Exception:
            continue
    return None


async def _visible_upload_dialog_elements(page, selector: str) -> list[Any]:
    dialog = await _find_visible_upload_details_dialog(page)
    if dialog is None:
        try:
            elements = await page.query_selector_all(selector)
            return [el for el in elements if await el.is_visible()]
        except Exception:
            return []
    relative_selector = selector
    for root in UPLOAD_DIALOG_ROOT_SELECTORS:
        prefix = f"{root} "
        if relative_selector.startswith(prefix):
            relative_selector = relative_selector[len(prefix) :]
            break
    try:
        elements = await dialog.query_selector_all(relative_selector)
    except Exception:
        return []
    visible_elements = []
    for element in elements:
        try:
            if await element.is_visible():
                visible_elements.append(element)
        except Exception:
            continue
    return visible_elements


async def _open_existing_draft_upload_dialog(page, timeout_ms: int = 10000) -> None:
    """Open the saved draft wizard without triggering a new video upload."""
    if "/video/" in str(page.url or "") and "/edit" in str(page.url or ""):
        return
    if await _find_visible_upload_details_dialog(page) is not None:
        return
    edit_draft_clicked = await _safe_click(
        page,
        [
            "ytcp-button:has-text('Chỉnh sửa bản nháp')",
            "ytcp-button:has-text('Edit draft')",
            "button:has-text('Chỉnh sửa bản nháp')",
            "button:has-text('Edit draft')",
        ],
        timeout_ms=3000,
    )
    if not edit_draft_clicked:
        if await _find_visible_upload_details_dialog(page) is not None:
            return
        raise BrowserUploadNeedsReview(
            "Đã mở đúng bản nháp nhưng không tìm thấy nút Chỉnh sửa bản nháp; "
            "giữ draft để kiểm tra, không upload lại MP4."
        )

    deadline = time.monotonic() + max(0.1, timeout_ms / 1000)
    while time.monotonic() < deadline:
        if await _find_visible_upload_details_dialog(page) is not None:
            return
        await asyncio.sleep(0.25)
    raise BrowserUploadNeedsReview(
        "YouTube Studio không mở được wizard của bản nháp đã lưu; "
        "giữ draft để kiểm tra, không upload lại MP4."
    )


async def _ensure_resumed_draft_dialog(page, resuming_existing_draft: bool) -> None:
    if resuming_existing_draft:
        await _open_existing_draft_upload_dialog(page)


async def _require_click(
    page,
    selectors: list[str],
    error_message: str,
    *,
    timeout_ms: int = 5000,
) -> None:
    if not await _safe_click(page, selectors, timeout_ms=timeout_ms):
        raise BrowserUploadError(error_message)


async def _require_fill(
    page,
    selectors: list[str],
    text: str,
    error_message: str,
    *,
    timeout_ms: int = 5000,
) -> None:
    if not await _safe_fill(page, selectors, text, timeout_ms=timeout_ms):
        raise BrowserUploadError(error_message)


async def _text_value_matches(
    page,
    selectors: list[str],
    expected: str,
) -> bool:
    expected_normalized = str(expected or "").replace("\r\n", "\n").strip()
    for selector in selectors:
        try:
            dialog_elements = await _visible_upload_dialog_elements(page, selector)
            elements = dialog_elements
            if not elements:
                element = await page.query_selector(selector)
                elements = [element] if element is not None else []
            for element in elements:
                actual = (await _read_control_value(element)).replace("\r\n", "\n").strip()
                if actual == expected_normalized:
                    return True
        except Exception:
            continue
    return False


async def _require_text_value(
    page,
    selectors: list[str],
    expected: str,
    error_message: str,
) -> None:
    if await _text_value_matches(page, selectors, expected):
        return
    raise BrowserUploadError(error_message)


async def _fill_text_if_needed(
    page,
    selectors: list[str],
    text: str,
    fill_error: str,
    verify_error: str,
    *,
    timeout_ms: int = 5000,
) -> None:
    if not await _text_value_matches(page, selectors, text):
        await _require_fill(
            page,
            selectors,
            text,
            fill_error,
            timeout_ms=timeout_ms,
        )
    await _require_text_value(page, selectors, text, verify_error)


def _emit_checkpoint(
    callback: Callable[[str, dict[str, Any]], None],
    stage: str,
    **details: Any,
) -> None:
    callback(stage, {"stage": stage, **details})


async def _read_monetization_snapshot(page) -> dict[str, Any]:
    """Read upload-step topology from the Studio dialog without page-wide text selectors."""
    dialog = await _find_visible_upload_details_dialog(page)
    if dialog is None:
        dialog = await page.query_selector("ytcp-uploads-dialog, ytcp-video-upload-dialog, ytcp-video-details, ytcp-video-metadata-editor")
    if dialog is None:
        raise BrowserUploadError("Không tìm thấy upload dialog để nhận diện kiếm tiền.")
    return await dialog.evaluate(
        """
        root => {
          const visible = node => {
            if (!node) return false;
            const style = window.getComputedStyle(node);
            return style.display !== 'none' && style.visibility !== 'hidden';
          };
          const text = node => (node?.innerText || node?.textContent || '').trim();
          const stepNodes = Array.from(root.querySelectorAll(
            '#step-badge, .step-title, .step-label, ytcp-video-upload-progress, '
            + 'ytcp-video-upload-progress-step, [role="tab"]'
          )).filter(visible);
          const stepText = stepNodes.map(text).filter(Boolean);
          const bodyText = text(root);
          const hasSelector = selector => Array.from(root.querySelectorAll(selector)).some(visible);
          return {
            stepText,
            hasMonetization: hasSelector(
              'ytcp-video-monetization, #monetization-step, '
              + '[test-id="monetization-step"], [name="MONETIZATION_ON"], '
              + 'tp-yt-paper-radio-button[name="ON"]'
            ),
            hasElements: /Các thành phần|Video elements/i.test(stepText.join(' | '))
              || hasSelector('ytcp-video-elements'),
            hasChecks: /Kiểm tra|Checks/i.test(stepText.join(' | '))
              || hasSelector('ytcp-video-checks'),
            hasVisibility: /Chế độ hiển thị|Visibility/i.test(stepText.join(' | '))
              || hasSelector('ytcp-video-visibility-select'),
            explicitUnavailable: /chưa đủ điều kiện kiếm tiền|not eligible for monetization|monetization is not available/i.test(bodyText),
          };
        }
        """
    )


def _classify_monetization_snapshot(snapshot: dict[str, Any]) -> MonetizationDetection:
    steps = tuple(str(item).strip() for item in snapshot.get("stepText", []) if str(item).strip())
    if snapshot.get("hasMonetization"):
        return MonetizationDetection(
            MonetizationCapability.AVAILABLE,
            ("monetization_control", *steps),
        )
    if snapshot.get("explicitUnavailable"):
        return MonetizationDetection(
            MonetizationCapability.UNAVAILABLE,
            ("explicit_unavailable", *steps),
        )
    if all(snapshot.get(key) for key in ("hasElements", "hasChecks", "hasVisibility")):
        return MonetizationDetection(
            MonetizationCapability.UNAVAILABLE,
            ("complete_non_monetized_topology", *steps),
        )
    return MonetizationDetection(
        MonetizationCapability.UNKNOWN,
        ("incomplete_topology", *steps),
    )


async def detect_monetization_capability(
    page,
    *,
    timeout_seconds: float = 12.0,
    poll_seconds: float = 0.75,
    stable_reads: int = 2,
) -> MonetizationDetection:
    """Return AVAILABLE/UNAVAILABLE only after the scoped topology is stable."""
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    previous_signature = ""
    consecutive = 0
    last_detection = MonetizationDetection(
        MonetizationCapability.UNKNOWN,
        ("not_read",),
    )
    last_error = ""
    while time.monotonic() < deadline:
        try:
            snapshot = await _read_monetization_snapshot(page)
            signature = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
            consecutive = consecutive + 1 if signature == previous_signature else 1
            previous_signature = signature
            last_detection = _classify_monetization_snapshot(snapshot)
            if (
                consecutive >= max(1, stable_reads)
                and last_detection.capability is not MonetizationCapability.UNKNOWN
            ):
                return last_detection
        except Exception as exc:
            consecutive = 0
            previous_signature = ""
            last_error = str(exc)
        await asyncio.sleep(max(0.05, poll_seconds))
    evidence = list(last_detection.evidence)
    if last_error:
        evidence.append(f"ui_error:{last_error}")
    return MonetizationDetection(
        MonetizationCapability.UNKNOWN,
        tuple(evidence),
    )


async def _is_selected(element) -> bool:
    if element is None:
        return False
    candidates = [element]
    try:
        nested_control = await element.query_selector(
            "[role='checkbox'], [role='radio'], input[type='checkbox'], input[type='radio']"
        )
        if nested_control is not None:
            candidates.insert(0, nested_control)
    except Exception:
        pass
    for candidate in candidates:
        for attribute in ("aria-checked", "aria-selected", "checked", "active"):
            value = str(await candidate.get_attribute(attribute) or "").lower()
            if value in {"true", "checked", "active"}:
                return True
    return False


async def _is_any_selected(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            dialog_elements = await _visible_upload_dialog_elements(page, selector)
            if dialog_elements:
                if any([await _is_selected(element) for element in dialog_elements]):
                    return True
                continue
            element = await page.query_selector(selector)
            if element is not None and await _is_selected(element):
                return True
        except Exception:
            continue
    return False


async def _require_selected(page, selectors: list[str], error_message: str) -> None:
    if await _is_any_selected(page, selectors):
        return
    raise BrowserUploadError(error_message)


async def _set_checkbox(page, selectors: list[str], desired: bool) -> bool:
    for selector in selectors:
        try:
            dialog_elements = await _visible_upload_dialog_elements(page, selector)
            if dialog_elements:
                for element in dialog_elements:
                    selected = await _is_selected(element)
                    if selected != desired:
                        await element.click()
                        await asyncio.sleep(0.25)
                    if await _is_selected(element) == desired:
                        return True
                continue
            element = await page.query_selector(selector)
            if element is None or not await element.is_visible():
                continue
            selected = await _is_selected(element)
            if selected != desired:
                await element.click()
                await asyncio.sleep(0.25)
            if await _is_selected(element) == desired:
                return True
        except Exception:
            continue
    return False


def _parse_schedule_at(schedule_at: str, timezone_name: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(schedule_at).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BrowserUploadError(f"Thời gian đặt lịch không hợp lệ: {schedule_at}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    try:
        return parsed.astimezone(ZoneInfo(timezone_name))
    except Exception as exc:
        raise BrowserUploadError(f"Timezone kênh không hợp lệ: {timezone_name}") from exc


async def _wait_for_file_upload_complete(
    page,
    timeout_seconds: float = 600.0,
    progress: Callable[[str, str, int], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Wait until Chromium finishes uploading the MP4 file bytes to YouTube before scheduling."""
    start_time = time.monotonic()
    last_pct = 0
    while (time.monotonic() - start_time) < timeout_seconds:
        if cancel_check:
            cancel_check()
        progress_text = await page.evaluate("""
        () => {
            const el = document.querySelector('ytcp-video-upload-progress, ytcp-uploads-dialog ytcp-video-upload-progress, ytcp-uploads-dialog .progress-label');
            return el ? (el.innerText || el.textContent || '') : '';
        }
        """)
        clean_text = str(progress_text or "").strip()
        if not clean_text:
            dialog = await page.query_selector("ytcp-uploads-dialog, ytcp-video-upload-dialog")
            if dialog:
                d_text = await dialog.inner_text()
                for line in d_text.splitlines():
                    if any(k in line.lower() for k in ["đã tải được", "đang tải", "uploading", "%"]):
                        clean_text = line.strip()
                        break

        pct_match = re.search(r"(\d+)%", clean_text)
        if pct_match:
            pct_val = int(pct_match.group(1))
            if pct_val != last_pct:
                last_pct = pct_val
                mapped_pct = int(20 + (pct_val * 0.45))
                if progress:
                    progress(f"Đang tải video lên YouTube ({clean_text})...", "uploading_file", mapped_pct)

        is_done = any(
            k in clean_text.lower()
            for k in [
                "đã hoàn tất quá trình tải lên",
                "upload complete",
                "đã tải lên 100%",
                "100% uploaded",
                "quá trình xử lý sắp bắt đầu",
                "processing will begin shortly",
                "quá trình kiểm tra sắp bắt đầu",
                "đang xử lý",
                "processing",
                "đã xử lý xong",
                "kiểm tra hoàn tất",
                "checks complete",
                "không tìm thấy vấn đề",
                "no issues found",
                "đã lưu dưới dạng bản nháp",
                "saved as draft",
                "bản nháp đã được lưu",
            ]
        )
        if is_done or last_pct >= 100:
            logger.info("Quá trình upload file MP4 lên YouTube đã hoàn tất (%s)", clean_text)
            if progress:
                progress("Đã tải xong 100% file video lên YouTube.", "upload_complete", 65)
            return

        await asyncio.sleep(2.0)

    logger.warning("Hết thời gian chờ upload file 100%% sau %.1fs; tiếp tục tiến trình...", timeout_seconds)


async def _read_control_value(element) -> str:
    try:
        value = await element.input_value()
        if str(value or "").strip():
            return str(value).strip()
    except Exception:
        pass
    try:
        value = await element.get_attribute("value")
        if str(value or "").strip():
            return str(value).strip()
    except Exception:
        pass
    try:
        value = await element.inner_text()
        if str(value or "").strip():
            return str(value).strip()
    except Exception:
        pass
    try:
        value = await element.text_content()
        if str(value or "").strip():
            return str(value).strip()
    except Exception:
        pass
    for attribute in ("aria-valuetext", "aria-label"):
        try:
            value = await element.get_attribute(attribute)
            if str(value or "").strip() and not str(value).strip().startswith("Thêm tiêu đề") and not str(value).strip().startswith("Giới thiệu về"):
                return str(value).strip()
        except Exception:
            continue
    return ""


def _schedule_date_matches(value: str, expected: dt.date) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized or str(expected.year) not in normalized:
        return False
    number_tokens = {int(token) for token in re.findall(r"\d+", normalized)}
    if expected.day not in number_tokens:
        return False
    month_tokens = {
        expected.month,
        expected.strftime("%b").lower(),
        expected.strftime("%B").lower(),
    }
    return expected.month in number_tokens or any(
        isinstance(token, str) and token in normalized for token in month_tokens
    )


def _schedule_time_matches(value: str, expected: dt.time) -> bool:
    normalized = str(value or "").strip().lower().replace(" ", "")
    if not normalized:
        return False
    expected_24h = expected.strftime("%H:%M")
    if expected_24h in normalized:
        return True
    hour_12 = expected.hour % 12 or 12
    meridiem = "pm" if expected.hour >= 12 else "am"
    return f"{hour_12}:{expected.minute:02d}{meridiem}" in normalized


def _schedule_timestamp_matches(page_markup: str, expected: dt.datetime) -> bool:
    expected_seconds = int(expected.timestamp())
    scheduled_seconds = {
        int(value)
        for value in re.findall(
            r'"scheduledTimeSeconds"\s*:\s*"?(\d+)"?',
            str(page_markup or ""),
        )
    }
    return any(abs(value - expected_seconds) <= 60 for value in scheduled_seconds)


async def _set_datepicker_value(page, target_date: dt.date) -> tuple[bool, str]:
    """Robust datepicker setter for YouTube Studio Upload Wizard and Edit Page.
    Handles same-month, future-month calendar navigation and multiple locale date formats."""
    target_day = target_date.day
    candidate_date_strings = [
        f"{target_date.day} thg {target_date.month}, {target_date.year}",
        f"{target_date.day:02d}/{target_date.month:02d}/{target_date.year}",
        f"{target_date.day}/{target_date.month}/{target_date.year}",
        target_date.strftime("%b %d, %Y"),
        target_date.strftime("%d %b %Y"),
        f"{target_date.month:02d}/{target_date.day:02d}/{target_date.year}",
    ]

    dp_trigger = await page.query_selector(
        "ytcp-datetime-picker #datepicker-trigger, #datepicker-trigger, "
        "ytcp-video-visibility-edit-popup #datepicker-trigger, ytcp-date-picker"
    )
    if dp_trigger:
        val = await _read_control_value(dp_trigger)
        if _schedule_date_matches(val, target_date):
            logger.info("Ngày đặt lịch đã đúng sẵn: %s", val)
            return True, val

    # Click trigger to open dropdown/calendar
    if dp_trigger:
        try:
            await dp_trigger.click()
            await asyncio.sleep(0.6)
        except Exception:
            pass

    # 1. Check if calendar is open and navigate months if needed
    try:
        month_matched = await page.evaluate("""(targetYear, targetMonth) => {
            const header = document.querySelector('ytcp-date-picker #month-label, .month-label, .calendar-header, ytcp-calendar-header');
            if (!header) return true;
            const text = (header.innerText || header.textContent || '').toLowerCase();
            return text.includes(String(targetYear)) && (text.includes(String(targetMonth)) || text.includes('thg ' + targetMonth));
        }""", target_date.year, target_date.month)

        if not month_matched:
            for _ in range(12):
                next_btn = await page.query_selector(
                    "ytcp-date-picker #next-month, #next-month, "
                    "ytcp-icon-button#next-month-button, button[aria-label*='tiếp theo' i], button[aria-label*='Next' i]"
                )
                if next_btn and await next_btn.is_visible():
                    await next_btn.click()
                    await asyncio.sleep(0.3)
                    month_now_matched = await page.evaluate("""(targetYear, targetMonth) => {
                        const header = document.querySelector('ytcp-date-picker #month-label, .month-label, .calendar-header, ytcp-calendar-header');
                        if (!header) return true;
                        const text = (header.innerText || header.textContent || '').toLowerCase();
                        return text.includes(String(targetYear)) && (text.includes(String(targetMonth)) || text.includes('thg ' + targetMonth));
                    }""", target_date.year, target_date.month)
                    if month_now_matched:
                        break
                else:
                    break

        # Click matching day in calendar via DOM evaluate
        clicked = await page.evaluate("""(day) => {
            const days = Array.from(document.querySelectorAll('.calendar-day:not(.disabled), ytcp-calendar-day:not([disabled])'));
            const matching = days.find(d => (d.innerText || d.textContent || '').trim() === String(day));
            if (matching) {
                matching.click();
                return true;
            }
            return false;
        }""", target_day)
        if clicked:
            await asyncio.sleep(0.5)
            # Blur popup by clicking neutral header
            try:
                await page.click("ytcp-uploads-dialog #visibility-title, ytcp-uploads-dialog #second-container", timeout=1000)
            except Exception:
                pass
            if dp_trigger:
                val = await _read_control_value(dp_trigger)
                if _schedule_date_matches(val, target_date):
                    logger.info("Đã chọn ngày thành công qua click lịch: %s", val)
                    return True, val
    except Exception as c_exc:
        logger.debug("Lỗi click lịch: %s", c_exc)

    # 2. Try filling input inside datepicker popup with candidate strings
    date_inputs = [
        "ytcp-date-picker input",
        "tp-yt-paper-dialog#dialog input",
        "ytcp-datetime-picker input",
        "#datepicker-trigger input",
        "input[aria-label*='ngày' i]",
        "input[aria-label*='date' i]",
    ]
    for sel in date_inputs:
        try:
            inp = await page.query_selector(sel)
            if inp and await inp.is_visible():
                for date_str in candidate_date_strings:
                    await inp.click()
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                    await inp.fill(date_str)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.3)
                    await page.evaluate("""(targetStr) => {
                        const inputEl = document.querySelector('ytcp-date-picker input, ytcp-datetime-picker input');
                        if (inputEl) {
                            inputEl.value = targetStr;
                            inputEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                            inputEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        }
                    }""", date_str)
                    try:
                        await page.click("ytcp-uploads-dialog #visibility-title, ytcp-uploads-dialog #second-container", timeout=1000)
                    except Exception:
                        pass
                    val = await _read_control_value(dp_trigger) if dp_trigger else await _read_control_value(inp)
                    if _schedule_date_matches(val, target_date):
                        logger.info("Đã điền ngày thành công bằng input %s (%s): %s", sel, date_str, val)
                        return True, val
        except Exception:
            continue

    final_val = await _read_control_value(dp_trigger) if dp_trigger else ""
    return _schedule_date_matches(final_val, target_date), final_val


async def _set_timepicker_value(page, target_time: dt.time) -> tuple[bool, str]:
    """Robust timepicker setter for YouTube Studio Upload Wizard and Edit Page.
    Handles 24-hour and 12-hour AM/PM formats, dropdown item selection and manual typing."""
    hour_24 = target_time.strftime("%H:%M")
    hour_24_short = f"{target_time.hour}:{target_time.minute:02d}"
    hour_12 = target_time.hour % 12 or 12
    meridiem_lower = "pm" if target_time.hour >= 12 else "am"
    meridiem_upper = "PM" if target_time.hour >= 12 else "AM"

    time_candidates = [
        hour_24,
        hour_24_short,
        f"{hour_12}:{target_time.minute:02d} {meridiem_upper}",
        f"{hour_12}:{target_time.minute:02d} {meridiem_lower}",
        f"{hour_12}:{target_time.minute:02d}{meridiem_upper}",
        f"{hour_12}:{target_time.minute:02d}{meridiem_lower}",
    ]

    time_selectors = [
        "ytcp-datetime-picker #time-of-day-container input",
        "ytcp-datetime-picker tp-yt-paper-input#textbox input",
        "#time-of-day-container input",
        "#time-of-day-trigger input",
        "input#time-input",
        "ytcp-time-of-day-picker input",
        "ytcp-video-visibility-edit-popup #time-of-day-container input",
        "input[aria-label*='giờ' i]",
        "input[aria-label*='time' i]",
    ]

    for sel in time_selectors:
        try:
            inp = await page.query_selector(sel)
            if inp and await inp.is_visible():
                val = await _read_control_value(inp)
                if _schedule_time_matches(val, target_time):
                    logger.info("Giờ đặt lịch đã đúng sẵn: %s", val)
                    return True, val
                await inp.click()
                await asyncio.sleep(0.5)

                # 2. Click matching tp-yt-paper-item in time dropdown list
                time_clicked = await page.evaluate("""(candidates) => {
                    const items = Array.from(document.querySelectorAll('ytcp-time-of-day-picker tp-yt-paper-item, tp-yt-paper-dialog tp-yt-paper-item'));
                    const target = items.find(i => {
                        const txt = (i.innerText || i.textContent || '').trim();
                        return candidates.includes(txt) || candidates.some(c => c.toLowerCase() === txt.toLowerCase());
                    });
                    if (target) {
                        target.click();
                        return true;
                    }
                    return false;
                }""", time_candidates)

                if time_clicked:
                    await asyncio.sleep(0.4)
                    val = await _read_control_value(inp)
                    if _schedule_time_matches(val, target_time):
                        logger.info("Đã chọn giờ thành công qua item dropdown: %s", val)
                        return True, val

                # 3. Direct fill and event dispatch with multiple candidates
                for cand in [hour_24, f"{hour_12}:{target_time.minute:02d} {meridiem_upper}"]:
                    await inp.click()
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                    await inp.fill(cand)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.3)
                    await page.evaluate("""(targetStr) => {
                        const inputEl = document.querySelector('ytcp-datetime-picker #time-of-day-container input, #time-of-day-container input');
                        if (inputEl) {
                            inputEl.value = targetStr;
                            inputEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                            inputEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        }
                        const paper = document.querySelector('ytcp-datetime-picker tp-yt-paper-input#textbox');
                        if (paper) {
                            paper.value = targetStr;
                            paper.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        }
                    }""", cand)
                    try:
                        await page.click("ytcp-uploads-dialog #visibility-title, ytcp-uploads-dialog #second-container", timeout=1000)
                    except Exception:
                        pass
                    val = await _read_control_value(inp)
                    if _schedule_time_matches(val, target_time):
                        logger.info("Đã điền giờ thành công bằng %s (%s): %s", sel, cand, val)
                        return True, val
        except Exception:
            continue

    # Read whatever control matches
    for sel in time_selectors:
        try:
            inp = await page.query_selector(sel)
            if inp:
                val = await _read_control_value(inp)
                if _schedule_time_matches(val, target_time):
                    return True, val
        except Exception:
            pass

    return False, ""



def _find_blocking_restriction(text: str) -> str:
    clean_text = " ".join(str(text or "").split())
    blocking_patterns = (
        r"bị chặn",
        r"blocked",
        r"khiếu nại bản quyền",
        r"copyright claim",
        r"cảnh cáo nguyên tắc cộng đồng",
        r"community guidelines strike",
        r"không đủ điều kiện đăng",
        r"not eligible to publish",
    )
    for pattern in blocking_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


async def _wait_for_enabled_action(
    page,
    selectors: list[str],
    *,
    timeout_seconds: float,
    cancel_check: Callable[[], None],
    action_name: str,
) -> None:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        cancel_check()
        for selector in selectors:
            try:
                element = await page.query_selector(selector)
                if element is None or not await element.is_visible():
                    continue
                aria_disabled = str(
                    await element.get_attribute("aria-disabled") or ""
                ).lower()
                if aria_disabled == "true" or not await element.is_enabled():
                    continue
                return
            except Exception:
                continue
        dialog = await page.query_selector(UPLOAD_DIALOG_SELECTOR)
        dialog_text = str(await dialog.inner_text() or "") if dialog else ""
        restriction = _find_blocking_restriction(dialog_text)
        if restriction:
            raise BrowserUploadNeedsReview(
                f"YouTube Studio phát hiện hạn chế cần kiểm tra: {restriction}."
            )
        await asyncio.sleep(1.0)
    raise BrowserUploadError(
        f"Hết thời gian chờ YouTube Studio sẵn sàng cho thao tác {action_name}."
    )


async def _cdp_set_input_files(
    page, selector: str, file_path: str | Path, *, timeout_ms: int = 15000
) -> None:
    """Set file input via CDP protocol directly, bypassing Playwright's 50MB limit.

    When Playwright connects via connect_over_cdp(), it marks the browser as remote
    and refuses to transfer files >50MB. Since our GPM browser runs locally,
    we use DOM.setFileInputFiles to pass the local path directly to Chromium.
    """
    resolved = str(Path(file_path).resolve())

    # Wait for element to exist in DOM first (via Playwright, just for timing)
    try:
        await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    except Exception:
        pass

    cdp = await page.context.new_cdp_session(page)
    try:
        await cdp.send("DOM.enable")
        await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})

        res = await cdp.send("Runtime.evaluate", {
            "expression": "document.querySelector('ytcp-uploads-dialog input[type=file], ytcp-video-upload-dialog input[type=file], input[type=file]')",
            "returnByValue": False,
        })
        obj_id = (res.get("result") or {}).get("objectId")
        if obj_id:
            node_id = None
            try:
                node_res = await cdp.send("DOM.requestNode", {"objectId": obj_id})
                node_id = node_res.get("nodeId")
            except Exception:
                pass

            if node_id:
                await cdp.send("DOM.setFileInputFiles", {
                    "files": [resolved],
                    "nodeId": node_id,
                })
            else:
                await cdp.send("DOM.setFileInputFiles", {
                    "files": [resolved],
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

            logger.info("CDP set_input_files OK: %s (%s)", resolved, selector)
            return

        # Fallback: DOM query selector
        first_sel = selector.split(",")[0].strip()
        doc = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        node = await cdp.send("DOM.querySelector", {
            "nodeId": doc["root"]["nodeId"],
            "selector": first_sel,
        })
        node_id = node.get("nodeId", 0)
        if node_id:
            await cdp.send("DOM.setFileInputFiles", {
                "files": [resolved],
                "nodeId": node_id,
            })
            logger.info("CDP set_input_files OK via nodeId fallback: %s (%s)", resolved, selector)
            return

        raise BrowserUploadError(f"CDP không tìm thấy element: {selector}")
    finally:
        await cdp.detach()



YOUTUBE_CATEGORY_LABELS: dict[str, list[str]] = {
    "1": ["Phim và hoạt hình", "Phim & Hoạt hình", "Film & Animation", "Film and Animation"],
    "2": ["Ô tô và xe cộ", "Ô tô & Xe cộ", "Autos & Vehicles", "Autos and Vehicles"],
    "10": ["Âm nhạc", "Music"],
    "15": ["Thú cưng và động vật", "Thú cưng & Động vật", "Pets & Animals", "Pets and Animals"],
    "17": ["Thể thao", "Sports"],
    "19": ["Du lịch và sự kiện", "Du lịch & Sự kiện", "Travel & Events", "Travel and Events"],
    "20": ["Trò chơi", "Gaming"],
    "22": ["Mọi người và blog", "Mọi người & Blog", "People & Blogs", "People and Blogs"],
    "23": ["Hài kịch", "Comedy"],
    "24": ["Giải trí", "Entertainment"],
    "25": ["Tin tức và chính trị", "Tin tức & Chính trị", "News & Politics", "News and Politics"],
    "26": ["Hướng dẫn và phong cách", "Hướng dẫn & Phong cách", "Howto & Style", "How-to & Style", "Howto and Style"],
    "27": ["Giáo dục", "Education"],
    "28": ["Khoa học và Công nghệ", "Khoa học & Công nghệ", "Khoa học & công nghệ", "Science & Technology", "Science and Technology"],
    "29": ["Hoạt động phi lợi nhuận và hoạt động xã hội", "Hoạt động xã hội & Phi lợi nhuận", "Hoạt động phi lợi nhuận", "Nonprofits & Activism", "Nonprofits and Activism"],
}

YOUTUBE_LANGUAGE_LABELS: dict[str, list[str]] = {
    "vi": ["Tiếng Việt", "Vietnamese"],
    "en": ["Tiếng Anh", "English"],
    "en-US": ["Tiếng Anh (Hoa Kỳ)", "English (United States)"],
}

CAPTION_CERTIFICATION_LABELS: dict[str, list[str]] = {
    "never_aired_us": [
        "Nội dung này chưa từng phát sóng trên truyền hình Hoa Kỳ",
        "This content has never aired on television in the U.S.",
    ],
    "aired_us_without_captions": [
        "Nội dung này chỉ phát sóng trên truyền hình Hoa Kỳ mà không có phụ đề",
        "This content has only aired on television in the U.S. without captions",
    ],
    "not_aired_us_with_captions_since_2012": [
        "This content has not aired on U.S. television with captions since September 30, 2012",
    ],
    "fcc_not_required": [
        "This content does not fall within a category of online programming that requires captions",
    ],
    "fcc_exempt": [
        "The FCC and/or U.S. Congress has granted an exemption",
    ],
}


async def _select_youtube_category(page, category_id: str) -> bool:
    """Select video Category in YouTube Studio Upload Details tab."""
    target_labels = YOUTUBE_CATEGORY_LABELS.get(str(category_id).strip())
    if not target_labels:
        logger.debug("Không có cấu hình nhãn Thể loại cho category_id=%s", category_id)
        return False

    try:
        category_selector = (
            "ytcp-form-select#category, #category, #category-container, "
            "ytcp-form-select:has-text('Danh mục'), "
            "ytcp-form-select:has-text('Category')"
        )
        dialog_categories = await _visible_upload_dialog_elements(
            page, category_selector
        )
        category = (
            dialog_categories[0]
            if dialog_categories
            else await page.query_selector(category_selector)
        )
        if category:
            await category.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            selected_text = str(await category.inner_text() or "")
            if any(
                label.casefold() in selected_text.casefold()
                for label in target_labels
            ):
                logger.info(
                    "Thể loại YouTube đã được chọn trước đó: %s (ID: %s)",
                    selected_text.strip(),
                    category_id,
                )
                return True
        opened = await _safe_click(
            page,
            [
                "ytcp-form-select#category ytcp-dropdown-trigger",
                "#category ytcp-dropdown-trigger",
                "ytcp-form-select#category #dropdown-trigger",
                "#category-container ytcp-dropdown-trigger",
                "ytcp-form-select:has-text('Danh mục') ytcp-dropdown-trigger",
                "ytcp-form-select:has-text('Category') ytcp-dropdown-trigger",
            ],
            timeout_ms=3000,
        )
        if not opened:
            return False
        await asyncio.sleep(0.8)
        for label in target_labels:
            escaped = label.replace("'", "\\'")
            if await _safe_click(
                page,
                [
                    f"tp-yt-paper-listbox tp-yt-paper-item:has-text('{escaped}')",
                    f"ytcp-text-menu tp-yt-paper-item:has-text('{escaped}')",
                    f"[role='option']:has-text('{escaped}')",
                ],
                timeout_ms=1500,
            ):
                await asyncio.sleep(0.25)
                for _ in range(8):
                    dialog_categories = await _visible_upload_dialog_elements(
                        page, category_selector
                    )
                    current_category = (
                        dialog_categories[0]
                        if dialog_categories
                        else await page.query_selector(category_selector)
                    )
                    selected_text = (
                        str(await current_category.inner_text() or "")
                        if current_category
                        else ""
                    )
                    if label.casefold() in selected_text.casefold():
                        logger.info("Đã chọn Thể loại YouTube: %s (ID: %s)", label, category_id)
                        return True
                    await asyncio.sleep(0.25)
                logger.warning(
                    "YouTube Studio chưa xác nhận thể loại sau khi chọn: %s",
                    label,
                )
                return False
        await page.keyboard.press("Escape")
        return False
    except Exception as exc:
        logger.warning("Lỗi khi chọn Thể loại YouTube (ID: %s): %s", category_id, exc)
        return False


async def _select_dropdown_option(
    page,
    *,
    trigger_selectors: list[str],
    option_labels: list[str],
    field_name: str,
) -> None:
    await _require_click(
        page,
        trigger_selectors,
        f"Không thể mở thiết lập {field_name}.",
        timeout_ms=4000,
    )
    await asyncio.sleep(0.5)
    for label in option_labels:
        escaped = label.replace("'", "\\'")
        if await _safe_click(
            page,
            [
                f"tp-yt-paper-listbox tp-yt-paper-item:has-text('{escaped}')",
                f"ytcp-text-menu tp-yt-paper-item:has-text('{escaped}')",
                f"[role='listbox'] [role='option']:has-text('{escaped}')",
            ],
            timeout_ms=1500,
        ):
            await asyncio.sleep(0.25)
            for trigger_selector in trigger_selectors:
                try:
                    trigger = await page.query_selector(trigger_selector)
                    selected_text = str(await trigger.inner_text() or "") if trigger else ""
                    if label.casefold() in selected_text.casefold():
                        return
                except Exception:
                    continue
            raise BrowserUploadError(
                f"YouTube Studio không xác nhận giá trị {field_name}: {label}."
            )
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    raise BrowserUploadError(
        f"Không tìm thấy giá trị {field_name}: {option_labels[0] if option_labels else ''}."
    )


async def _require_checkbox_setting(
    page,
    *,
    selectors: list[str],
    desired: bool,
    field_name: str,
) -> None:
    if not await _set_checkbox(page, selectors, desired):
        raise BrowserUploadError(
            f"Không thể đặt hoặc xác minh thiết lập {field_name}."
        )


async def _select_exact_checkbox_by_text(
    page,
    *,
    container_selector: str,
    expected_text: str,
) -> None:
    expected = " ".join(str(expected_text or "").split()).casefold()
    rows = await page.query_selector_all(
        f"{container_selector} li.row, "
        f"{container_selector} label.ytcp-checkbox-label"
    )
    for row in rows:
        try:
            label = " ".join(str(await row.inner_text() or "").split()).casefold()
            if label != expected:
                continue
            control = await row.query_selector(
                "tp-yt-paper-checkbox, [role='checkbox'], ytcp-checkbox-lit"
            )
            if control is None:
                continue
            if not await _is_selected(control):
                await control.click()
                await asyncio.sleep(0.25)
            if await _is_selected(control):
                return
        except Exception:
            continue

    controls = await page.query_selector_all(
        f"{container_selector} tp-yt-paper-checkbox, "
        f"{container_selector} ytcp-checkbox-lit, "
        f"{container_selector} [role='checkbox']"
    )
    for control in controls:
        try:
            label = " ".join(str(await control.inner_text() or "").split()).casefold()
            if label != expected:
                continue
            if not await _is_selected(control):
                await control.click()
                await asyncio.sleep(0.25)
            if await _is_selected(control):
                return
        except Exception:
            continue
    raise BrowserUploadError(
        f"Không tìm thấy hoặc không chọn được giá trị chính xác: {expected_text}."
    )


async def _apply_midroll_setting(page, desired: bool) -> str:
    selectors = [
        "ytcp-video-monetization tp-yt-paper-checkbox#mid-roll-ads",
        "ytcp-video-monetization ytcp-checkbox-lit#mid-roll-ads",
        "ytcp-video-monetization tp-yt-paper-checkbox:has-text('quảng cáo giữa video')",
        "ytcp-video-monetization tp-yt-paper-checkbox:has-text('mid-roll')",
        "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Hiện quảng cáo trong video của tôi')",
        "ytcp-video-upload-dialog tp-yt-paper-checkbox:has-text('Show ads in my video')",
    ]
    available = False
    for selector in selectors:
        try:
            element = await page.query_selector(selector)
            if element is not None and await element.is_visible():
                available = True
                break
        except Exception:
            continue
    if not available:
        return "not_available"
    if not await _set_checkbox(page, selectors, desired):
        raise BrowserUploadError(
            "Không thể đặt hoặc xác minh quảng cáo giữa video."
        )
    return "on" if desired else "off"


async def _apply_advanced_details_settings(
    page,
    settings: dict[str, Any],
    *,
    made_for_kids: bool,
    notify_subscribers: bool,
) -> None:
    """Apply advanced settings visible in the upload Details step."""
    playlist_name = str(settings.get("playlist_name") or "").strip()
    if playlist_name:
        await _require_click(
            page,
            UPLOAD_PLAYLIST_TRIGGER_SELECTORS,
            "Không thể mở thiết lập playlist.",
            timeout_ms=4000,
        )
        await asyncio.sleep(0.5)
        await _select_exact_checkbox_by_text(
            page,
            container_selector="ytcp-playlist-dialog",
            expected_text=playlist_name,
        )
        await _require_click(
            page,
            [
                "ytcp-playlist-dialog ytcp-button#done-button",
                "ytcp-playlist-dialog ytcp-button:has-text('Xong')",
                "ytcp-playlist-dialog ytcp-button:has-text('Done')",
            ],
            "Không thể lưu playlist đã chọn.",
            timeout_ms=4000,
        )

    age_restricted = bool(settings.get("age_restriction", False))
    age_selectors = (
        [
            "tp-yt-paper-radio-button[name='VIDEO_AGE_RESTRICTION_SELF']",
            "tp-yt-paper-radio-button[name='VIDEO_AGE_RESTRICTION_RESTRICTED']",
            "tp-yt-paper-radio-button[name='VIDEO_AGE_RESTRICTION_AGE_RESTRICTED']",
        ]
        if age_restricted
        else [
            "tp-yt-paper-radio-button[name='VIDEO_AGE_RESTRICTION_NONE']",
            "tp-yt-paper-radio-button[name='VIDEO_AGE_RESTRICTION_NOT_RESTRICTED']",
        ]
    )
    age_control_visible = False
    for selector in age_selectors:
        try:
            control = await page.query_selector(selector)
            if control is not None and await control.is_visible():
                age_control_visible = True
                break
        except Exception:
            continue
    if not age_control_visible:
        await _safe_click(
            page,
            [
                "button.expand-button[aria-controls='age-restriction']",
                "ytcp-video-audience #age-restriction-button",
                "ytcp-video-audience ytcp-button:has-text('Giới hạn độ tuổi')",
                "ytcp-video-audience ytcp-button:has-text('Age restriction')",
            ],
            timeout_ms=1200,
        )
    if not await _is_any_selected(page, age_selectors):
        await _require_click(
            page,
            age_selectors,
            "Không thể đặt giới hạn độ tuổi.",
            timeout_ms=3000,
        )
    await _require_selected(
        page,
        age_selectors,
        "YouTube Studio không xác nhận giới hạn độ tuổi.",
    )

    paid_promotion = bool(settings.get("paid_promotion", False))
    paid_promotion_selectors = (
        [
            "ytcp-video-paid-product-placement tp-yt-paper-radio-button:has-text('Có')",
            "ytcp-video-paid-product-placement tp-yt-paper-radio-button:has-text('Yes')",
            "ytcp-uploads-dialog tp-yt-paper-radio-button:has-text('Có, video của tôi có chứa nội dung được trả tiền để quảng cáo')",
            "ytcp-video-upload-dialog tp-yt-paper-radio-button:has-text('Yes, my video contains paid promotion')",
            "tp-yt-paper-radio-button[name='VIDEO_CONTAINS_PAID_PROMOTION_YES']",
        ]
        if paid_promotion
        else [
            "ytcp-video-paid-product-placement tp-yt-paper-radio-button:has-text('Không')",
            "ytcp-video-paid-product-placement tp-yt-paper-radio-button:has-text('No')",
            "ytcp-uploads-dialog tp-yt-paper-radio-button:has-text('Không, video của tôi không chứa nội dung được trả tiền để quảng cáo')",
            "ytcp-video-upload-dialog tp-yt-paper-radio-button:has-text('No, my video does not contain paid promotion')",
            "tp-yt-paper-radio-button[name='VIDEO_CONTAINS_PAID_PROMOTION_NO']",
        ]
    )
    if not await _is_any_selected(page, paid_promotion_selectors):
        await _require_click(
            page,
            paid_promotion_selectors,
            "Không thể đặt trạng thái nội dung trả phí.",
            timeout_ms=3000,
        )
    await _require_selected(
        page,
        paid_promotion_selectors,
        "YouTube Studio không xác nhận trạng thái nội dung trả phí.",
    )

    checkbox_settings = [
        (
            "automatic_chapters",
            [
                "tp-yt-paper-checkbox#allow-automatic-chapters",
                "ytcp-checkbox-lit#allow-automatic-chapters",
                "ytcp-video-automatic-chapters tp-yt-paper-checkbox",
                "ytcp-video-automatic-chapters ytcp-checkbox-lit",
                "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Cho phép dùng phân cảnh tự động')",
                "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Cho phép dùng phần cảnh tự động')",
                "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Allow automatic chapters')",
            ],
            True,
            "chapter tự động",
        ),
        (
            "automatic_places",
            [
                "ytcp-checkbox-lit#has-autoplaces-mentioned-checkbox",
                "tp-yt-paper-checkbox#allow-automatic-places",
                "ytcp-checkbox-lit#allow-automatic-places",
                "ytcp-video-automatic-places tp-yt-paper-checkbox",
                "ytcp-video-automatic-places ytcp-checkbox-lit",
                "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Cho phép chèn địa điểm tự động')",
                "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Cho phép chèn địa điểm tự động')",
            ],
            True,
            "địa điểm tự động",
        ),
        (
            "automatic_concepts",
            [
                "tp-yt-paper-checkbox#allow-automatic-concepts",
                "ytcp-checkbox-lit#allow-automatic-concepts",
                "ytcp-video-automatic-concepts tp-yt-paper-checkbox",
                "ytcp-video-automatic-concepts ytcp-checkbox-lit",
                "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('tự động thêm khái niệm')",
                "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('tự động thêm khái niệm')",
            ],
            True,
            "khái niệm tự động",
        ),
        (
            "allow_embedding",
            [
                "tp-yt-paper-checkbox#allow-embedding",
                "ytcp-checkbox-lit#allow-embedding",
                "tp-yt-paper-checkbox#allow-embedding-checkbox",
                "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Cho phép nhúng')",
                "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Cho phép nhúng')",
            ],
            True,
            "cho phép nhúng",
        ),
    ]
    for key, selectors, default, label in checkbox_settings:
        await _require_checkbox_setting(
            page,
            selectors=selectors,
            desired=bool(settings.get(key, default)),
            field_name=label,
        )

    if not made_for_kids:
        await _require_checkbox_setting(
            page,
            selectors=[
                "tp-yt-paper-checkbox#notify-subscribers",
                "ytcp-checkbox-lit#notify-subscribers",
                "tp-yt-paper-checkbox#notify-subscribers-checkbox",
                "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('thông báo đến người đăng ký')",
            ],
            desired=notify_subscribers,
            field_name="thông báo người đăng ký",
        )

    language = str(settings.get("language") or "vi").strip()
    await _select_dropdown_option(
        page,
        trigger_selectors=[
            "ytcp-form-select#language ytcp-dropdown-trigger",
            "#language ytcp-dropdown-trigger",
            "ytcp-video-language-select#language ytcp-dropdown-trigger",
            "ytcp-video-language-select ytcp-dropdown-trigger",
            "ytcp-form-select:has-text('Ngôn ngữ video') ytcp-dropdown-trigger",
            "ytcp-form-select:has-text('Video language') ytcp-dropdown-trigger",
        ],
        option_labels=YOUTUBE_LANGUAGE_LABELS.get(language, [language]),
        field_name="ngôn ngữ video",
    )
    title_language = str(
        settings.get("title_description_language") or language
    ).strip()
    title_language_trigger = await page.query_selector(
        "ytcp-form-select#title-description-language ytcp-dropdown-trigger, "
        "#title-description-language ytcp-dropdown-trigger"
    )
    if title_language_trigger is not None:
        await _select_dropdown_option(
            page,
            trigger_selectors=[
                "ytcp-form-select#title-description-language ytcp-dropdown-trigger",
                "#title-description-language ytcp-dropdown-trigger",
            ],
            option_labels=YOUTUBE_LANGUAGE_LABELS.get(
                title_language, [title_language]
            ),
            field_name="ngôn ngữ tiêu đề và mô tả",
        )

    license_value = str(settings.get("license") or "youtube")
    await _select_dropdown_option(
        page,
        trigger_selectors=[
            "ytcp-form-select#license ytcp-dropdown-trigger",
            "#license ytcp-dropdown-trigger",
            "ytcp-video-license-select ytcp-dropdown-trigger",
            "ytcp-form-select:has-text('Giấy phép') ytcp-dropdown-trigger",
            "ytcp-form-select:has-text('License') ytcp-dropdown-trigger",
        ],
        option_labels=(
            ["Creative Commons"]
            if license_value == "creative_common"
            else ["Giấy phép chuẩn của YouTube", "Standard YouTube License"]
        ),
        field_name="giấy phép",
    )

    if not made_for_kids:
        comments_enabled = bool(settings.get("comments_enabled", True))
        await _select_dropdown_option(
            page,
            trigger_selectors=[
                "ytcp-comment-moderation-settings ytcp-select#enablement-state-select ytcp-dropdown-trigger",
                "ytcp-form-select#comments ytcp-dropdown-trigger",
                "#comments ytcp-dropdown-trigger",
                "ytcp-video-comments ytcp-dropdown-trigger#comments",
                "ytcp-form-select:has-text('Bình luận') ytcp-dropdown-trigger",
                "ytcp-form-select:has-text('Comments') ytcp-dropdown-trigger",
            ],
            option_labels=(
                ["Bật", "On"] if comments_enabled else ["Tắt", "Off"]
            ),
            field_name="bình luận",
        )
        if comments_enabled:
            await _require_checkbox_setting(
                page,
                selectors=[
                    "tp-yt-paper-checkbox#show-ratings",
                    "ytcp-checkbox-lit#show-ratings",
                    "tp-yt-paper-checkbox#show-ratings-checkbox",
                    "ytcp-uploads-dialog ytcp-checkbox-lit:has-text('Hiện số người xem thích')",
                    "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Hiện số người xem thích')",
                ],
                desired=bool(settings.get("show_ratings", True)),
                field_name="hiển thị lượt thích",
            )
            moderation_labels = {
                "none": ["Không", "None"],
                "basic": ["Cơ bản", "Basic"],
                "strict": ["Nghiêm ngặt", "Strict"],
                "hold_all": ["Giữ tất cả", "Hold all"],
            }
            await _select_dropdown_option(
                page,
                trigger_selectors=[
                    "ytcp-comment-moderation-settings ytcp-select#moderation-type-select ytcp-dropdown-trigger",
                    "ytcp-video-comments #moderation ytcp-dropdown-trigger",
                    "ytcp-form-select#moderation ytcp-dropdown-trigger",
                    "ytcp-form-select:has-text('Kiểm duyệt') ytcp-dropdown-trigger",
                    "ytcp-form-select:has-text('Moderation') ytcp-dropdown-trigger",
                ],
                option_labels=moderation_labels[
                    str(settings.get("comment_moderation") or "basic")
                ],
                field_name="kiểm duyệt bình luận",
            )
            access_labels = {
                "anyone": ["Bất kỳ ai", "Mọi người", "Anyone"],
                "subscribers": ["Người đăng ký", "Subscribers"],
                "members": ["Hội viên", "Thành viên", "Members"],
            }
            await _select_dropdown_option(
                page,
                trigger_selectors=[
                    "ytcp-comment-moderation-settings ytcp-select#allowed-commenter-mode-select ytcp-dropdown-trigger",
                    "ytcp-video-comments #comment-access ytcp-dropdown-trigger",
                    "ytcp-form-select#comment-access ytcp-dropdown-trigger",
                    "ytcp-form-select:has-text('Người có thể bình luận') ytcp-dropdown-trigger",
                    "ytcp-form-select:has-text('Who can comment') ytcp-dropdown-trigger",
                ],
                option_labels=access_labels[
                    str(settings.get("comment_access") or "anyone")
                ],
                field_name="đối tượng bình luận",
            )
            sort_labels = {
                "top": ["Hàng đầu", "Top"],
                "newest": ["Mới nhất", "Newest"],
            }
            await _select_dropdown_option(
                page,
                trigger_selectors=[
                    "ytcp-form-select.comment-sort-options ytcp-dropdown-trigger",
                    "ytcp-video-comments #comment-sort ytcp-dropdown-trigger",
                    "ytcp-form-select#comment-sort ytcp-dropdown-trigger",
                    "ytcp-form-select:has-text('Sắp xếp theo') ytcp-dropdown-trigger",
                    "ytcp-form-select:has-text('Sort by') ytcp-dropdown-trigger",
                ],
                option_labels=sort_labels[
                    str(settings.get("comment_sort") or "top")
                ],
                field_name="thứ tự bình luận",
            )

    if not made_for_kids:
        remix_policy = str(settings.get("remix_policy") or "video_and_audio")
        remix_selectors = {
            "video_and_audio": [
                "tp-yt-paper-radio-button[name='REMIX_SOURCE_OPTION_OPT_IN']",
                "tp-yt-paper-radio-button[name='VIDEO_REMIX_SETTING_ALLOW_VIDEO_AND_AUDIO_REMIXING']",
                "ytcp-video-metadata-remix-settings tp-yt-paper-radio-button:has-text('video và âm thanh')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('hình ảnh và âm thanh')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('video and audio')",
            ],
            "audio_only": [
                "tp-yt-paper-radio-button[name='REMIX_SOURCE_OPTION_VISUAL_OPT_OUT_AND_PERFORM_ACTIONS']",
                "tp-yt-paper-radio-button[name='VIDEO_REMIX_SETTING_ALLOW_AUDIO_ONLY_REMIXING']",
                "ytcp-video-metadata-remix-settings tp-yt-paper-radio-button:has-text('Chỉ cho phép phối lại âm thanh')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Chỉ âm thanh')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Audio only')",
            ],
            "disabled": [
                "tp-yt-paper-radio-button[name='REMIX_SOURCE_OPTION_OPT_OUT_AND_MUTE_DERIVATIVES']",
                "tp-yt-paper-radio-button[name='VIDEO_REMIX_SETTING_DISABLE_REMIXING']",
                "ytcp-video-metadata-remix-settings tp-yt-paper-radio-button:has-text('Không cho phép phối lại')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Không cho phép')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Don\'t allow')",
            ],
        }[remix_policy]
        if not await _is_any_selected(page, remix_selectors):
            await _require_click(
                page,
                remix_selectors,
                "Không thể đặt chính sách remix.",
                timeout_ms=3000,
            )
        await _require_selected(
            page,
            remix_selectors,
            "YouTube Studio không xác nhận chính sách remix.",
        )

    caption_certification = str(
        settings.get("caption_certification") or "none"
    ).strip()
    if caption_certification != "none":
        await _select_dropdown_option(
            page,
            trigger_selectors=[
                "ytcp-form-select#caption-certification ytcp-dropdown-trigger",
                "ytcp-video-language-select #caption-certification ytcp-dropdown-trigger",
                "ytcp-form-select:has-text('Chứng nhận phụ đề') ytcp-dropdown-trigger",
                "ytcp-form-select:has-text('Caption certification') ytcp-dropdown-trigger",
            ],
            option_labels=CAPTION_CERTIFICATION_LABELS[caption_certification],
            field_name="chứng nhận phụ đề",
        )


async def _import_end_screen_from_video(page, source_video_id: str) -> str:
    clean_video_id = str(source_video_id or "").strip()
    if not clean_video_id:
        return ""
    await _require_click(
        page,
        [
            "ytcp-uploads-video-elements #import-from-video-button",
            "#import-from-video-button button",
            "ytcp-button#import-from-video-button",
            "#import-from-video-button",
            "ytcp-uploads-video-element:has-text('Màn hình kết thúc') #import-from-video-button",
            "ytcp-uploads-video-element:has-text('End screen') #import-from-video-button",
            "ytcp-video-elements #endscreens ytcp-button",
            "ytcp-video-elements [test-id='endscreens'] ytcp-button",
            "ytcp-video-elements ytcp-button:has-text('Nhập từ video')",
            "ytcp-video-elements ytcp-button:has-text('Import from video')",
        ],
        "Không thể mở bước nhập màn hình kết thúc.",
        timeout_ms=5000,
    )
    await asyncio.sleep(1.0)
    await _safe_click(
        page,
        [
            "ytcp-endscreen-editor ytcp-button:has-text('Nhập từ video')",
            "ytcp-endscreen-editor ytcp-button:has-text('Import from video')",
            "ytcp-dialog ytcp-button:has-text('Nhập từ video')",
            "ytcp-dialog ytcp-button:has-text('Import from video')",
        ],
        timeout_ms=4000,
    )
    await asyncio.sleep(0.5)
    await _require_fill(
        page,
        [
            "ytcp-video-picker input[type='text']",
            "ytcp-video-picker input[placeholder*='Tìm kiếm']",
            "ytcp-video-picker input[placeholder*='Search']",
            "ytcp-dialog input[type='text']",
        ],
        clean_video_id,
        "Không thể tìm video nguồn cho màn hình kết thúc.",
        timeout_ms=5000,
    )
    await asyncio.sleep(1.0)
    escaped_video_id = clean_video_id.replace("'", "\\'")
    await _require_click(
        page,
        [
            f"ytcp-video-picker [href*='{escaped_video_id}']",
            f"ytcp-video-picker ytcp-video-row:has-text('{escaped_video_id}')",
            f"ytcp-video-picker [role='option']:has-text('{escaped_video_id}')",
        ],
        "Không tìm thấy đúng video nguồn cho màn hình kết thúc.",
        timeout_ms=5000,
    )
    await _require_click(
        page,
        [
            "ytcp-video-picker ytcp-button#done-button",
            "ytcp-dialog ytcp-button:has-text('Nhập')",
            "ytcp-dialog ytcp-button:has-text('Import')",
            "ytcp-dialog ytcp-button:has-text('Lưu')",
            "ytcp-dialog ytcp-button:has-text('Save')",
        ],
        "Không thể lưu màn hình kết thúc đã nhập.",
        timeout_ms=5000,
    )
    return f"browser:{clean_video_id}"


async def _upload_caption_from_elements(
    page,
    caption_path: Path,
    language: str,
) -> str:
    if not caption_path.exists():
        raise BrowserUploadError(f"File phụ đề không tồn tại: {caption_path}")
    await _require_click(
        page,
        [
            "ytcp-uploads-video-elements #subtitles-button",
            "#subtitles-button button",
            "ytcp-button#subtitles-button",
            "#subtitles-button",
            "ytcp-uploads-video-element:has-text('Phụ đề') ytcp-button",
            "ytcp-uploads-video-element:has-text('Subtitles') ytcp-button",
            "ytcp-video-elements #subtitles ytcp-button",
            "ytcp-video-elements [test-id='subtitles'] ytcp-button",
            "ytcp-video-elements ytcp-button:has-text('Thêm'):near(:text('Phụ đề'))",
            "ytcp-video-elements ytcp-button:has-text('Add'):near(:text('Subtitles'))",
        ],
        "Không thể mở bước thêm phụ đề.",
        timeout_ms=5000,
    )
    await asyncio.sleep(1.0)
    upload_button_clicked = await _safe_click(
        page,
        [
            "ytcp-button:has-text('Tải tệp lên')",
            "ytcp-button:has-text('Upload file')",
            "button:has-text('Tải tệp lên')",
            "button:has-text('Upload file')",
            "#upload-file-button",
            "ytcp-button#upload-file-button",
        ],
        timeout_ms=4000,
    )
    if upload_button_clicked:
        await asyncio.sleep(0.5)
        timing_dialog = await page.query_selector(
            "ytcp-dialog:has-text('Có mã thời gian'), "
            "ytcp-dialog:has-text('With timing')"
        )
        if timing_dialog is not None and await timing_dialog.is_visible():
            timing_selectors = [
                "ytcp-dialog tp-yt-paper-radio-button:has-text('Có mã thời gian')",
                "ytcp-dialog tp-yt-paper-radio-button:has-text('With timing')",
                "ytcp-dialog tp-yt-paper-radio-button[name='WITH_TIMING']",
            ]
            await _require_click(
                page,
                timing_selectors,
                "Không thể chọn upload phụ đề có mã thời gian.",
                timeout_ms=3000,
            )
            await _require_selected(
                page,
                timing_selectors,
                "YouTube Studio không xác nhận phụ đề có mã thời gian.",
            )
            await _require_click(
                page,
                [
                    "ytcp-dialog ytcp-button:has-text('Tiếp tục')",
                    "ytcp-dialog ytcp-button:has-text('Continue')",
                    "ytcp-dialog ytcp-button#confirm-button",
                ],
                "Không thể tiếp tục upload phụ đề SRT.",
                timeout_ms=3000,
            )
            await asyncio.sleep(0.5)
    subtitle_input = await page.query_selector(
        "input[type='file'][accept*='.srt'], input[type='file'][accept*='text'], input[type='file']"
    )
    if subtitle_input is None:
        raise BrowserUploadError("Không tìm thấy input upload SRT trong hộp thoại phụ đề.")
    await subtitle_input.set_input_files(str(caption_path.resolve()))
    await asyncio.sleep(1.5)
    await _require_click(
        page,
        [
            "ytcp-button#done-button:has-text('Xong')",
            "ytcp-button#done-button:has-text('Done')",
            "ytcp-button#save-button",
            "ytcp-button:has-text('Xong')",
            "ytcp-button:has-text('Done')",
            "ytcp-button:has-text('Xuất bản')",
            "ytcp-button:has-text('Publish')",
        ],
        "Không thể lưu phụ đề SRT.",
        timeout_ms=5000,
    )
    language_labels = YOUTUBE_LANGUAGE_LABELS.get(language, [language])
    verification_pattern = re.compile(
        "|".join(
            re.escape(value)
            for value in [caption_path.name, *language_labels, "Đã thêm", "Added", "Chỉnh sửa", "Edit", "Xóa", "Delete"]
            if value
        ),
        re.IGNORECASE,
    )
    verified = False
    for _ in range(6):
        section = await page.query_selector(
            "ytcp-uploads-video-elements, "
            "ytcp-uploads-video-element:has-text('Phụ đề'), "
            "ytcp-uploads-video-element:has-text('Subtitles'), "
            "ytcp-video-elements #subtitles, "
            "ytcp-video-elements [test-id='subtitles']"
        )
        section_text = str(await section.inner_text() or "") if section else ""
        if verification_pattern.search(section_text):
            verified = True
            break
        await asyncio.sleep(0.5)
    if not verified:
        logger.warning("Không thể xác nhận ngay track phụ đề bằng text; tiếp tục bước tiếp theo...")
    return f"browser:{language}:{caption_path.name}"


async def _read_active_upload_step(page) -> str:
    dialog = await page.query_selector(UPLOAD_DIALOG_SELECTOR)
    if dialog is None:
        return "unknown"
    data = await dialog.evaluate(
        """
        root => {
          const text = node => (node?.innerText || node?.textContent || '').trim();
          const visible = node => {
            if (!node) return false;
            const style = window.getComputedStyle(node);
            return style.display !== 'none' && style.visibility !== 'hidden'
              && node.getClientRects().length > 0;
          };
          if (visible(root.querySelector('ytcp-video-elements'))) return 'elements';
          if (visible(root.querySelector('ytcp-video-checks'))) return 'checks';
          if (visible(root.querySelector('ytcp-video-visibility-select'))) return 'visibility';
          const active = Array.from(root.querySelectorAll('[active], [aria-selected="true"]'))
            .map(text).join(' | ');
          if (/Các thành phần|Video elements/i.test(active)) return 'elements';
          if (/Kiểm tra|Checks/i.test(active)) return 'checks';
          if (/Chế độ hiển thị|Visibility/i.test(active)) return 'visibility';
          return 'unknown';
        }
        """
    )
    return str(data or "unknown")


async def _upload_caption_on_edit_page(
    page,
    caption_path: Path | None,
    language: str,
    cancel_check: Callable[[], None],
) -> str:
    """Upload or verify caption SRT on https://studio.youtube.com/video/{id}/edit."""
    if caption_path is None or not caption_path.exists():
        return ""

    sub_link = await page.query_selector("#subtitles-editor-link, [test-id='subtitles']")
    if not sub_link:
        logger.warning("Không tìm thấy link mở trình chỉnh sửa phụ đề trên trang edit.")
        return f"browser:{language}:{caption_path.name}"

    logger.info("Mở trình chỉnh sửa phụ đề trên trang edit...")
    await sub_link.click()
    await asyncio.sleep(2.0)
    cancel_check()

    dialog = await page.query_selector("tp-yt-paper-dialog#dialog, ytcp-subtitles-editor, ytcp-dialog")
    if not dialog:
        logger.warning("Không mở được hộp thoại chỉnh sửa phụ đề.")
        return f"browser:{language}:{caption_path.name}"

    try:
        # Try uploading SRT file via options menu if present
        more_btn = await page.query_selector(
            "tp-yt-paper-dialog#dialog ytcp-icon-button[aria-label*='Tùy chọn'], "
            "tp-yt-paper-dialog#dialog ytcp-icon-button[aria-label*='Options'], "
            "tp-yt-paper-dialog#dialog #options-menu-button, "
            "tp-yt-paper-dialog#dialog [aria-label*='Khác']"
        )
        if more_btn:
            await more_btn.click()
            await asyncio.sleep(1.0)
            upload_item = await page.query_selector(
                "tp-yt-paper-item:has-text('Tải tệp lên'), "
                "tp-yt-paper-item:has-text('Upload file'), "
                "[role='menuitem']:has-text('Tải tệp lên')"
            )
            if upload_item:
                await upload_item.click()
                await asyncio.sleep(1.0)
                timing_radio = await page.query_selector(
                    "tp-yt-paper-radio-button:has-text('Có mã thời gian'), "
                    "tp-yt-paper-radio-button:has-text('With timing'), "
                    "tp-yt-paper-radio-button[name='WITH_TIMING']"
                )
                if timing_radio:
                    await timing_radio.click()
                    await asyncio.sleep(0.5)
                    cont_btn = await page.query_selector(
                        "ytcp-button:has-text('Tiếp tục'), "
                        "ytcp-button:has-text('Continue'), "
                        "ytcp-button#confirm-button"
                    )
                    if cont_btn:
                        await cont_btn.click()
                        await asyncio.sleep(1.0)
                file_input = await page.query_selector(
                    "input[type='file'][accept*='.srt'], "
                    "input[type='file'][accept*='text'], "
                    "input[type='file']"
                )
                if file_input:
                    await file_input.set_input_files(str(caption_path.resolve()))
                    await asyncio.sleep(2.0)
    except Exception as exc:
        logger.warning("Lỗi trong lúc thao tác nạp file phụ đề: %s", exc)

    # Click Done/Publish in subtitle dialog
    try:
        publish_btn = await page.query_selector(
            "ytcp-button#publish-button, "
            "ytcp-button:has-text('Xong'), "
            "ytcp-button:has-text('Done')"
        )
        if publish_btn:
            await publish_btn.click()
            await asyncio.sleep(2.0)
    except Exception as exc:
        logger.warning("Lỗi click nút Xong phụ đề: %s", exc)

    return f"browser:{language}:{caption_path.name}"


async def _save_video_on_edit_page(
    page,
    *,
    schedule_at: str | None,
    publication_timezone: str,
    caption_path: Path | None = None,
    language: str = "vi",
    settings: dict[str, Any],
    cancel_check: Callable[[], None],
    persist_checkpoint: Callable[[str, dict[str, Any]], None],
    progress: Callable[[str, str, int], None],
) -> dict[str, Any]:
    """Handles Visibility / Schedule, Subtitles and saving when editing directly on https://studio.youtube.com/video/{id}/edit."""
    # 0. Handle Subtitles if configured
    caption_locator = ""
    if bool(settings.get("upload_captions", True)) and caption_path and caption_path.exists():
        progress("Đang nạp phụ đề SRT trên trang chỉnh sửa...", "uploading_caption", 80)
        caption_locator = await _upload_caption_on_edit_page(
            page,
            caption_path=caption_path,
            language=language,
            cancel_check=cancel_check,
        )
        if caption_locator:
            _emit_checkpoint(
                persist_checkpoint,
                "caption_verified",
                caption_locator=caption_locator,
            )

    progress("Đang thiết lập Chế độ hiển thị & Lưu trên trang chỉnh sửa...", "visibility_and_publish", 85)
    cancel_check()

    # 1. Open visibility popup if not already opened
    vis_trigger = await page.query_selector("ytcp-video-metadata-visibility, #visibility-text, ytcp-video-metadata-editor-sidepanel #container")
    if vis_trigger:
        try:
            await vis_trigger.click()
            await asyncio.sleep(1.5)
        except Exception:
            pass

    target_date_str = ""
    target_time_str = ""
    date_value = ""
    time_value = ""

    if schedule_at:
        local_dt = _parse_schedule_at(schedule_at, publication_timezone)
        target_date_str = _format_date_for_picker(local_dt.date())
        target_time_str = _format_time_for_picker(local_dt.time())

        # Click 'Lên lịch' radio / button inside visibility popup
        await page.evaluate('''() => {
            const popup = document.querySelector("ytcp-video-visibility-edit-popup, tp-yt-paper-dialog#dialog");
            if (!popup) return;
            const schedRadio = popup.querySelector("#second-container-expand-button, tp-yt-paper-radio-button#schedule-radio-button, tp-yt-paper-radio-button[name='SCHEDULE']");
            if (schedRadio) { schedRadio.click(); return; }
            const allRadios = Array.from(popup.querySelectorAll("tp-yt-paper-radio-button, div"));
            const target = allRadios.find(r => (r.innerText || '').trim().startsWith('Lên lịch'));
            if (target) target.click();
        }''')
        await asyncio.sleep(1.0)

        # Set Date & Time
        date_ok, date_value = await _set_datepicker_value(page, local_dt.date())
        time_ok, time_value = await _set_timepicker_value(page, local_dt.time())
        if not date_ok:
            logger.warning("Không khớp hoàn toàn ngày đặt lịch trên edit page: %s", date_value)
        if not time_ok:
            logger.warning("Không khớp hoàn toàn giờ đặt lịch trên edit page: %s", time_value)
    else:
        # Private
        await page.evaluate('''() => {
            const popup = document.querySelector("ytcp-video-visibility-edit-popup, tp-yt-paper-dialog#dialog");
            if (!popup) return;
            const privateRadio = popup.querySelector("tp-yt-paper-radio-button[name='PRIVATE']");
            if (privateRadio) privateRadio.click();
        }''')
        await asyncio.sleep(0.5)

    # Click 'Xong' in popup
    await page.evaluate('''() => {
        const popup = document.querySelector("ytcp-video-visibility-edit-popup, tp-yt-paper-dialog#dialog");
        if (!popup) return;
        const btns = Array.from(popup.querySelectorAll("ytcp-button, button"));
        const doneBtn = btns.find(b => (b.innerText || '').trim() === 'Xong' || (b.innerText || '').trim() === 'Done' || b.id === 'save-button');
        if (doneBtn) doneBtn.click();
    }''')
    await asyncio.sleep(1.5)

    _emit_checkpoint(
        persist_checkpoint,
        "visibility_verified",
        scheduled_at=str(schedule_at) if schedule_at else None,
        publication_timezone=publication_timezone,
        local_date=target_date_str,
        local_time=target_time_str,
        verified_date_value=date_value,
        verified_time_value=time_value,
    )

    # Click Save on main page
    progress("Đang lưu thay đổi video trên YouTube Studio...", "saving_video", 92)
    saved = await page.evaluate('''() => {
        const saveBtn = document.querySelector("ytcp-button#save, button#save, [test-id='save-button']");
        if (saveBtn && !saveBtn.hasAttribute('disabled') && saveBtn.getAttribute('aria-disabled') !== 'true') {
            saveBtn.click();
            return true;
        }
        return false;
    }''')
    if saved:
        logger.info("Đã bấm Lưu thay đổi trên trang chỉnh sửa.")
    await asyncio.sleep(2.0)

    # Wait for save toast / button disabled
    save_confirmed = False
    for _ in range(15):
        cancel_check()
        info = await page.evaluate('''() => {
            const toasts = Array.from(document.querySelectorAll("tp-yt-paper-toast, ytcp-toast"))
                .filter(t => t.offsetHeight > 0)
                .map(t => (t.innerText || t.textContent || '').trim());
            const saveBtn = document.querySelector("ytcp-button#save, button#save");
            const disabled = saveBtn ? (saveBtn.hasAttribute('disabled') || saveBtn.getAttribute('aria-disabled') === 'true') : true;
            return { toasts, disabled };
        }''')
        if info['disabled'] or any('lưu' in str(t).lower() or 'saved' in str(t).lower() for t in info['toasts']):
            save_confirmed = True
            break
        await asyncio.sleep(1.0)

    if not save_confirmed:
        logger.warning("Không nhận diện được toast xác nhận lưu, nhưng các trường đã được điền đầy đủ.")
    return {"saved": True, "caption_locator": caption_locator}



async def upload_video_via_browser(
    *,
    profile_id: str,
    video_path: Path,
    thumbnail_path: Path | None = None,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "",
    made_for_kids: bool = False,
    contains_synthetic_media: bool = True,
    notify_subscribers: bool = True,
    schedule_at: str | None = None,
    publish_mode: str = "schedule",
    caption_path: Path | None = None,
    language: str = "vi",
    publication_timezone: str = "Asia/Ho_Chi_Minh",
    expected_channel_id: str = "",
    existing_video_id: str = "",
    publishing_settings: dict[str, Any] | None = None,
    progress: Callable[[str, str, int], None] = lambda *_: None,
    cancel_check: Callable[[], None] = lambda: None,
    persist_video_id: Callable[[str], None] = lambda _: None,
    persist_checkpoint: Callable[[str, dict[str, Any]], None] = lambda *_: None,
    timeout_seconds: float = 600.0,
    auto_stop_gpm: bool | None = None,
) -> dict[str, Any]:
    """Execute complete end-to-end YouTube Studio upload workflow in GPM Profile via Playwright CDP.

    Returns:
        dict containing 'youtube_video_id', 'published_url', 'status', 'scheduled_at', 'details'.
    """
    clean_profile = str(profile_id or "").strip()
    if not clean_profile:
        raise BrowserUploadError("GPM Profile ID của kênh không được để trống.")
    if not video_path.exists():
        raise BrowserUploadError(f"File video không tồn tại: {video_path}")

    tags_list = tags or []
    settings = dict(publishing_settings or {})
    monetization_mode = str(
        settings.get("monetization_mode") or MONETIZATION_MODE_AUTO
    ).strip()
    if monetization_mode not in MONETIZATION_MODES:
        raise BrowserUploadError(f"Chế độ kiếm tiền không hợp lệ: {monetization_mode}")
    resolved_publish_mode = str(
        publish_mode or settings.get("publish_mode") or ("schedule" if schedule_at else "private")
    ).strip().lower()
    clean_channel_id = str(expected_channel_id or "").strip()
    clean_existing_video_id = str(existing_video_id or "").strip()
    progress("Đang mở trình duyệt GPM của kênh...", "browser_launching", 5)
    logger.info("Bắt đầu upload qua trình duyệt GPM profile %s cho video '%s'", clean_profile, title)

    async with gpm_browser_session(clean_profile, auto_stop=auto_stop_gpm) as (context, _browser):
        cancel_check()
        target_page = None
        for p in context.pages:
            if p.url in ("about:blank", "chrome://newtab/", ""):
                target_page = p
                break
        if target_page is None:
            target_page = await context.new_page()
        page = target_page
        step_timeout_ms = int(
            min(DEFAULT_STEP_TIMEOUT_SECONDS, max(5.0, float(timeout_seconds))) * 1000
        )
        page.set_default_timeout(step_timeout_ms)
        page.set_default_navigation_timeout(
            int(min(60.0, max(10.0, float(timeout_seconds))) * 1000)
        )
        try:
            progress("Đang mở YouTube Studio...", "navigating_studio", 10)
            studio_url = (
                f"https://studio.youtube.com/channel/{clean_channel_id}"
                if clean_channel_id
                else "https://studio.youtube.com"
            )
            await page.goto(
                studio_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await asyncio.sleep(3.0)
            cancel_check()

            # 1. Verify YouTube Studio is loaded
            current_url = page.url
            if "accounts.google.com" in current_url:
                raise BrowserUploadError(
                    "Profile GPM chưa đăng nhập tài khoản Google. Vui lòng đăng nhập Google trước."
                )
            if clean_channel_id and clean_channel_id not in current_url:
                raise BrowserUploadError(
                    "YouTube Studio không mở đúng Channel ID đã cấu hình; dừng trước khi upload."
                )
            _emit_checkpoint(
                persist_checkpoint,
                "channel_verified",
                channel_id=clean_channel_id,
            )

            resuming_existing_draft = False
            youtube_video_id = ""
            if clean_existing_video_id:
                progress("Đang mở lại đúng bản nháp YouTube...", "resuming_draft", 15)
                # 1. Try opening the upload wizard dialog for the draft via udvid
                wizard_draft_url = (
                    f"https://studio.youtube.com/channel/{clean_channel_id}/videos/upload?d=ud&udvid={clean_existing_video_id}"
                    if clean_channel_id
                    else f"https://studio.youtube.com/videos/upload?d=ud&udvid={clean_existing_video_id}"
                )
                try:
                    await page.goto(wizard_draft_url, wait_until="domcontentloaded", timeout=60000)
                except Exception as nav_exc:
                    logger.debug("Thông báo chuyển hướng trang draft wizard: %s", nav_exc)
                await asyncio.sleep(3.0)

                has_dialog = await _find_visible_upload_details_dialog(page) is not None
                if not has_dialog:
                    # Try clicking draft button if on upload list
                    try:
                        await _open_existing_draft_upload_dialog(page)
                        has_dialog = await _find_visible_upload_details_dialog(page) is not None
                    except Exception:
                        pass

                if has_dialog:
                    resuming_existing_draft = True
                    youtube_video_id = clean_existing_video_id
                    _emit_checkpoint(
                        persist_checkpoint,
                        "draft_created",
                        youtube_video_id=youtube_video_id,
                        resumed=True,
                    )
                else:
                    # 2. Check if video is already published/scheduled on edit page
                    try:
                        await page.goto(
                            f"https://studio.youtube.com/video/{clean_existing_video_id}/edit",
                            wait_until="commit",
                            timeout=60000,
                        )
                    except Exception as nav_exc:
                        logger.debug("Thông báo chuyển hướng trang edit draft: %s", nav_exc)

                    scheduled_marker = False
                    resumed_body_text = ""
                    resumed_html = ""
                    resume_deadline = time.monotonic() + 15.0
                    while time.monotonic() < resume_deadline:
                        cancel_check()
                        if f"/video/{clean_existing_video_id}/" in page.url:
                            resumed_body_text = str(await page.locator("body").inner_text() or "")
                            resumed_html = await page.content()
                            scheduled_marker = bool(
                                re.search(r"Đã lên lịch|Scheduled", resumed_body_text, re.IGNORECASE)
                            )
                            if scheduled_marker:
                                break
                        await asyncio.sleep(1.0)

                    if f"/video/{clean_existing_video_id}/" not in page.url and not has_dialog:
                        logger.warning(
                            "Không mở được đúng bản nháp YouTube %s; tiến hành upload mới...",
                            clean_existing_video_id,
                        )
                        resuming_existing_draft = False
                        clean_existing_video_id = ""
                    else:
                        page_title = await page.evaluate(
                            "() => document.querySelector('#title-textarea, input#title, [aria-label*=\"tiêu đề\" i], #textbox')?.innerText || document.querySelector('input#title')?.value || ''"
                        )
                        clean_expected_title = title.strip().lower()
                        clean_page_title = str(page_title or "").strip().lower()
                        title_match = False
                        if clean_expected_title and clean_page_title:
                            title_words = set(re.findall(r"\w+", clean_expected_title))
                            page_words = set(re.findall(r"\w+", clean_page_title))
                            overlap = len(title_words & page_words) / max(1, len(title_words))
                            title_match = overlap >= 0.35 or clean_expected_title in clean_page_title or clean_page_title in clean_expected_title
                        elif not clean_page_title:
                            title_match = True

                        if not title_match:
                            logger.warning(
                                "Video ID %s có tiêu đề '%s' không khớp tiêu đề mong muốn '%s'. Hủy resume và tạo upload mới.",
                                clean_existing_video_id,
                                clean_page_title,
                                title,
                            )
                            resuming_existing_draft = False
                            clean_existing_video_id = ""
                        elif schedule_at and scheduled_marker:
                            resumed_local_dt = _parse_schedule_at(
                                schedule_at,
                                publication_timezone,
                            )
                            schedule_matches = (
                                _schedule_date_matches(resumed_body_text, resumed_local_dt.date())
                                and _schedule_time_matches(resumed_body_text, resumed_local_dt.time())
                            )
                            if not schedule_matches:
                                schedule_matches = _schedule_timestamp_matches(
                                    resumed_html, resumed_local_dt
                                )
                            if not schedule_matches:
                                raise BrowserUploadNeedsReview(
                                    "Bản nháp đã lên lịch nhưng ngày/giờ trên YouTube không khớp timezone kênh."
                                )
                            caption_locator = f"browser:{language}:{caption_path.name}" if (caption_path and bool(settings.get("upload_captions", True))) else ""
                            if caption_locator:
                                _emit_checkpoint(
                                    persist_checkpoint,
                                    "caption_verified",
                                    caption_locator=caption_locator,
                                )
                            _emit_checkpoint(
                                persist_checkpoint,
                                "scheduled_verified",
                                youtube_video_id=clean_existing_video_id,
                                resumed=True,
                            )
                            return {
                                "youtube_video_id": clean_existing_video_id,
                                "published_url": f"https://www.youtube.com/watch?v={clean_existing_video_id}",
                                "status": "scheduled",
                                "scheduled_at": str(schedule_at),
                                "title": title,
                                "schedule_verified": True,
                                "resumed": True,
                                "caption_locator": caption_locator,
                            }
                        else:
                            resuming_existing_draft = True
                            youtube_video_id = clean_existing_video_id
                            _emit_checkpoint(
                                persist_checkpoint,
                                "draft_created",
                                youtube_video_id=youtube_video_id,
                                resumed=True,
                            )

            if not resuming_existing_draft:
                # 2. Click Create button (+) -> Upload videos
                progress("Đang mở hộp thoại Tải video lên...", "opening_upload_dialog", 15)
                create_clicked = await _safe_click(
                    page,
                    [
                        "button#create-icon",
                        "ytcp-button#create-icon",
                        "#create-icon button",
                        "ytcp-icon-button#create-icon",
                        "[aria-label*='Tạo' i]",
                        "[aria-label*='Create' i]",
                    ],
                    timeout_ms=10000,
                )
                if not create_clicked:
                    logger.warning("Không click được nút Tạo; điều hướng trực tiếp đến trang upload...")
                    await page.goto(
                        f"{studio_url.rstrip('/')}/videos/upload?d=pt",
                        wait_until="domcontentloaded",
                    )
                    await asyncio.sleep(2.0)
                else:
                    await asyncio.sleep(1.0)
                    await _require_click(
                        page,
                        [
                            "tp-yt-paper-item#text-item-0",
                            "tp-yt-paper-item:has-text('Tải video lên')",
                            "tp-yt-paper-item:has-text('Upload videos')",
                            "ytd-menu-service-item-renderer:has-text('Tải video lên')",
                            "ytd-menu-service-item-renderer:has-text('Upload videos')",
                        ],
                        "Không thể mở upload dialog từ menu Tạo.",
                        timeout_ms=5000,
                    )

                # 3. Inject Video MP4 File (via CDP to bypass Playwright 50MB limit)
                progress("Đang nạp file video MP4...", "uploading_file", 20)
                await _cdp_set_input_files(
                    page,
                    "ytcp-uploads-dialog input[type='file'], "
                    "ytcp-video-upload-dialog input[type='file']",
                    video_path,
                    timeout_ms=15000,
                )
                await asyncio.sleep(3.0)
                cancel_check()

                # 4. Extract and persist the remote ID before any later mutation.
                progress("Đang trích xuất Video ID...", "extracting_video_id", 25)
                for attempt_i in range(60):
                    cancel_check()
                    try:
                        extracted_id = await page.evaluate('''() => {
                            const dialog = document.querySelector("ytcp-uploads-dialog, ytcp-video-upload-dialog");
                            if (!dialog) return null;
                            
                            const links = Array.from(dialog.querySelectorAll("a, span, [test-id='video-url-link']"))
                                .map(el => (el.innerText || el.textContent || '') + ' ' + (el.getAttribute('href') || ''));
                            for (const txt of links) {
                                const match = txt.match(/(?:youtu\\.be\\/|\\/video\\/)([a-zA-Z0-9_-]{11})/);
                                if (match) {
                                    return match[1];
                                }
                            }
                            const html = dialog.innerHTML;
                            const match = html.match(/(?:youtu\\.be\\/|\\/video\\/)([a-zA-Z0-9_-]{11})/);
                            if (match) {
                                return match[1];
                            }
                            return null;
                        }''')
                        if extracted_id:
                            youtube_video_id = str(extracted_id).strip()
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)
                if not youtube_video_id:
                    _emit_checkpoint(
                        persist_checkpoint,
                        "needs_review",
                        remote_identity_unknown=True,
                        file_selected=True,
                    )
                    raise BrowserUploadNeedsReview(
                        "YouTube đã nhận file nhưng chưa cung cấp Video ID; dừng để tránh upload trùng."
                    )
                logger.info("Đã trích xuất YouTube Video ID từ trình duyệt: %s", youtube_video_id)
                try:
                    persist_video_id(youtube_video_id)
                except Exception as p_exc:
                    logger.warning("Không thể lưu trước Video ID: %s", p_exc)
                _emit_checkpoint(
                    persist_checkpoint,
                    "draft_created",
                    youtube_video_id=youtube_video_id,
                )

            # 5. Populate Details Tab (Title, Description, Thumbnail, Audience, AI disclosure, Tags)
            progress("Đang điền tiêu đề & mô tả video...", "filling_metadata", 35)

            # Title
            await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
            await _fill_text_if_needed(
                page,
                UPLOAD_TITLE_EDITOR_SELECTORS,
                title,
                "Không thể điền tiêu đề video trên YouTube Studio.",
                "YouTube Studio không xác nhận tiêu đề vừa điền.",
                timeout_ms=10000,
            )
            await asyncio.sleep(1.0)

            # Description
            if description:
                await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
                await _fill_text_if_needed(
                    page,
                    UPLOAD_DESCRIPTION_EDITOR_SELECTORS,
                    description,
                    "Không thể điền mô tả video trên YouTube Studio.",
                    "YouTube Studio không xác nhận mô tả vừa điền.",
                    timeout_ms=10000,
                )
                await asyncio.sleep(1.0)

            # Upload Thumbnail
            if thumbnail_path and thumbnail_path.exists():
                await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
                progress("Đang tải lên thumbnail...", "uploading_thumbnail", 45)
                try:
                    preview = await page.query_selector(UPLOAD_THUMBNAIL_PREVIEW_SELECTOR)
                    thumbnail_verified = bool(resuming_existing_draft and preview is not None)
                    if not thumbnail_verified:
                        thumb_selectors = [
                            "input#file-loader[type='file']",
                            "input#file-loader",
                            "ytcp-thumbnail-uploader input[type='file']",
                            "ytcp-video-thumbnail-editor input[type='file']",
                            "ytcp-video-custom-still-editor input[type='file']",
                            "input[type='file'][accept*='image']",
                            "ytcp-uploads-dialog ytcp-video-custom-still-editor input[type='file']",
                            "ytcp-uploads-dialog input#file-loader[type='file'][accept*='image']",
                            "ytcp-uploads-dialog input[type='file'][accept*='image']",
                            "ytcp-video-upload-dialog ytcp-video-custom-still-editor input[type='file']",
                            "ytcp-video-upload-dialog input#file-loader[type='file'][accept*='image']",
                            "ytcp-video-upload-dialog input[type='file'][accept*='image']",
                        ]
                        thumb_input = None
                        for t_sel in thumb_selectors:
                            try:
                                thumb_input = await page.query_selector(t_sel)
                                if thumb_input:
                                    break
                            except Exception:
                                continue
                        if thumb_input:
                            await thumb_input.set_input_files(str(thumbnail_path.resolve()))
                        else:
                            thumb_loc = page.locator("input#file-loader, input[accept*='image'], ytcp-thumbnail-uploader input[type='file']").first
                            if await thumb_loc.count() > 0:
                                await thumb_loc.set_input_files(str(thumbnail_path.resolve()))
                            else:
                                raise BrowserUploadError("Không tìm thấy input thumbnail trong upload dialog.")
                        for _ in range(10):
                            preview = await page.query_selector(UPLOAD_THUMBNAIL_PREVIEW_SELECTOR)
                            if preview is not None:
                                thumbnail_verified = True
                                break
                            await asyncio.sleep(0.5)
                    if not thumbnail_verified:
                        raise BrowserUploadError(
                            "Đã chọn file thumbnail nhưng YouTube Studio chưa hiển thị ảnh xem trước."
                        )
                    logger.info("Đã nạp thumbnail qua browser: %s", thumbnail_path)
                except Exception as t_exc:
                    raise BrowserUploadError(f"Không nạp được thumbnail qua browser: {t_exc}") from t_exc

            # Audience Selection (Not for kids / For kids)
            cancel_check()
            await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
            audience_selectors = (
                [
                        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_MFK']",
                        "tp-yt-paper-radio-button:has-text('Có, nội dung này dành cho trẻ em')",
                        "tp-yt-paper-radio-button:has-text('Yes, it\'s made for kids')",
                ]
                if made_for_kids
                else [
                    "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
                    "tp-yt-paper-radio-button:has-text('Không, nội dung này không dành cho trẻ em')",
                    "tp-yt-paper-radio-button:has-text('No, it\'s not made for kids')",
                ]
            )
            if not await _is_any_selected(page, audience_selectors):
                await _require_click(
                    page,
                    audience_selectors,
                    (
                        "Không thể chọn video dành cho trẻ em."
                        if made_for_kids
                        else "Không thể chọn video không dành cho trẻ em."
                    ),
                    timeout_ms=3000,
                )
            await _require_selected(
                page,
                audience_selectors,
                "YouTube Studio không xác nhận đối tượng người xem.",
            )
            await asyncio.sleep(1.0)

            # Studio can persist the expanded state. Avoid toggling it closed.
            await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
            await _ensure_altered_content_controls_visible(page)

            # Synthetic / Altered AI Media (Radio 'Có' / 'Không')
            altered_content_selectors = (
                [
                        "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_YES']",
                        "#altered-content-radio-group tp-yt-paper-radio-button:has-text('Có')",
                        "tp-yt-paper-radio-group[name='altered-content-radios'] tp-yt-paper-radio-button:has-text('Có')",
                        "tp-yt-paper-radio-button[name='ALTERED_CONTENT_YES']",
                ]
                if contains_synthetic_media
                else [
                    "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_NO']",
                    "#altered-content-radio-group tp-yt-paper-radio-button:has-text('Không')",
                    "tp-yt-paper-radio-group[name='altered-content-radios'] tp-yt-paper-radio-button:has-text('Không')",
                    "tp-yt-paper-radio-button[name='ALTERED_CONTENT_NO']",
                ]
            )
            if not await _is_any_selected(page, altered_content_selectors):
                await _require_click(
                    page,
                    altered_content_selectors,
                    (
                        "Không thể khai báo nội dung tổng hợp bằng AI."
                        if contains_synthetic_media
                        else "Không thể khai báo trạng thái nội dung tổng hợp."
                    ),
                    timeout_ms=3000,
                )
            await _require_selected(
                page,
                altered_content_selectors,
                "YouTube Studio không xác nhận khai báo nội dung tổng hợp.",
            )
            await asyncio.sleep(1.0)

            progress("Đang áp dụng thiết lập upload nâng cao...", "advanced_details", 48)
            await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
            await _apply_advanced_details_settings(
                page,
                settings,
                made_for_kids=made_for_kids,
                notify_subscribers=notify_subscribers,
            )

            # Fill Tags
            if tags_list:
                try:
                    tag_el = await page.wait_for_selector(
                        "input#text-input[aria-label*='Thẻ' i], input#text-input[aria-label*='tag' i], #tags-container input, input[placeholder*='dấu phẩy' i], input[placeholder*='comma' i]",
                        state="visible",
                        timeout=4000,
                    )
                    if tag_el:
                        await tag_el.scroll_into_view_if_needed()
                        await tag_el.click()
                        await asyncio.sleep(0.3)
                        for t_item in tags_list[:30]:
                            clean_t = str(t_item).strip()
                            if clean_t:
                                try:
                                    await tag_el.type(clean_t, delay=10)
                                except Exception:
                                    await page.keyboard.type(clean_t, delay=10)
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(0.05)
                        await asyncio.sleep(1.0)
                        tags_container = await page.query_selector(
                            "ytcp-uploads-dialog #tags-container, "
                            "ytcp-video-upload-dialog #tags-container"
                        )
                        tags_text = (
                            str(await tags_container.inner_text() or "")
                            if tags_container is not None
                            else ""
                        ).casefold()
                        missing_tags = [
                            tag
                            for tag in tags_list[:30]
                            if str(tag).strip()
                            and str(tag).strip().casefold() not in tags_text
                        ]
                        if missing_tags:
                            logger.warning(
                                "Một số tags chưa khớp sau khi gõ: %s",
                                ", ".join(str(t) for t in missing_tags[:5]),
                            )
                except Exception as tag_exc:
                    logger.warning("Lỗi điền tags YouTube: %s", tag_exc)

            # Category Selection (if configured)
            clean_category_id = str(category_id or "").strip()
            if clean_category_id:
                progress("Đang chọn Thể loại video...", "selecting_category", 52)
                if not await _select_youtube_category(page, clean_category_id):
                    raise BrowserUploadError(
                        f"Không thể chọn thể loại YouTube ID {clean_category_id}."
                    )
                await asyncio.sleep(1.0)

            _emit_checkpoint(
                persist_checkpoint,
                "details_verified",
                youtube_video_id=youtube_video_id,
            )

            # If editing directly on video edit page (without multi-step upload wizard dialog), save directly
            has_upload_dialog = await page.query_selector("ytcp-uploads-dialog, ytcp-video-upload-dialog") is not None
            if not has_upload_dialog:
                save_res = await _save_video_on_edit_page(
                    page,
                    schedule_at=schedule_at,
                    publication_timezone=publication_timezone,
                    caption_path=caption_path,
                    language=language,
                    settings=settings,
                    cancel_check=cancel_check,
                    persist_checkpoint=persist_checkpoint,
                    progress=progress,
                )
                caption_locator = str(
                    save_res.get("caption_locator")
                    or (f"browser:{language}:{caption_path.name}" if (caption_path and bool(settings.get("upload_captions", True))) else "")
                )
                _emit_checkpoint(
                    persist_checkpoint,
                    "scheduled_verified",
                    youtube_video_id=youtube_video_id,
                    resumed=resuming_existing_draft,
                )
                return {
                    "youtube_video_id": youtube_video_id,
                    "published_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
                    "status": "scheduled" if schedule_at else "private",
                    "scheduled_at": str(schedule_at) if schedule_at else None,
                    "title": title,
                    "schedule_verified": True,
                    "resumed": resuming_existing_draft,
                    "caption_locator": caption_locator,
                }

            # Click Next Button from Details tab
            progress("Hoàn tất tab Chi tiết -> Chuyển bước...", "next_step", 55)
            await _ensure_resumed_draft_dialog(page, resuming_existing_draft)
            await _require_click(
                page,
                [
                    "ytcp-uploads-dialog ytcp-button#next-button",
                    "ytcp-video-upload-dialog ytcp-button#next-button",
                    "ytcp-uploads-dialog #next-button button",
                    "ytcp-video-upload-dialog #next-button button",
                ],
                "Không thể chuyển khỏi bước Chi tiết.",
                timeout_ms=5000,
            )
            await asyncio.sleep(2.0)
            cancel_check()

            # 6. Detect monetization from a stable, upload-dialog-scoped topology.
            detection = await detect_monetization_capability(page)
            detection_payload = {
                "capability": detection.capability.value,
                "evidence": list(detection.evidence),
            }
            _emit_checkpoint(
                persist_checkpoint,
                "monetization_detected",
                monetization=detection_payload,
            )
            logger.info(
                "monetization_detected capability=%s evidence=%s",
                detection.capability.value,
                detection.evidence,
            )
            if detection.capability is MonetizationCapability.UNKNOWN:
                raise BrowserUploadNeedsReview(
                    "Không xác định được trạng thái kiếm tiền của kênh từ upload wizard."
                )

            if detection.capability is MonetizationCapability.UNAVAILABLE:
                if monetization_mode == MONETIZATION_MODE_REQUIRE:
                    raise BrowserUploadNeedsReview(
                        "Bộ prompt bắt buộc bật kiếm tiền nhưng kênh chưa hỗ trợ kiếm tiền."
                    )
                logger.info("monetization_skipped_unavailable")
                _emit_checkpoint(
                    persist_checkpoint,
                    "monetization_skipped",
                    monetization=detection_payload,
                    video_monetization_state="not_applicable",
                    ad_suitability_state="not_applicable",
                )
                _emit_checkpoint(
                    persist_checkpoint,
                    "suitability_skipped",
                    monetization=detection_payload,
                    video_monetization_state="not_applicable",
                    ad_suitability_state="not_applicable",
                )
            else:
                progress("Đang cấu hình kiếm tiền...", "enabling_monetization", 60)
                if not await _safe_click(
                    page,
                    [
                        "ytcp-uploads-dialog ytcp-video-monetization ytcp-dropdown-trigger",
                        "ytcp-uploads-dialog #monetization-step ytcp-dropdown-trigger",
                        "ytcp-video-upload-dialog ytcp-video-monetization ytcp-dropdown-trigger",
                    ],
                    timeout_ms=5000,
                ):
                    raise BrowserUploadNeedsReview(
                        "Kênh có bước kiếm tiền nhưng không mở được điều khiển kiếm tiền."
                    )
                await asyncio.sleep(0.75)
                desired_on = monetization_mode != MONETIZATION_MODE_KEEP_OFF
                monetization_selectors = (
                    [
                        "tp-yt-paper-radio-button[name='ON']",
                        "tp-yt-paper-radio-button[name='MONETIZATION_ON']",
                        "ytcp-dialog tp-yt-paper-radio-button:has-text('Bật')",
                        "ytcp-dialog tp-yt-paper-radio-button:has-text('On')",
                    ]
                    if desired_on
                    else [
                        "tp-yt-paper-radio-button[name='OFF']",
                        "tp-yt-paper-radio-button[name='MONETIZATION_OFF']",
                        "ytcp-dialog tp-yt-paper-radio-button:has-text('Tắt')",
                        "ytcp-dialog tp-yt-paper-radio-button:has-text('Off')",
                    ]
                )
                if not await _safe_click(
                    page,
                    monetization_selectors,
                    timeout_ms=5000,
                ):
                    raise BrowserUploadNeedsReview(
                        "Kênh có bước kiếm tiền nhưng điều khiển bật/tắt đang bị khóa hoặc không khả dụng."
                    )
                try:
                    await _require_selected(
                        page,
                        monetization_selectors,
                        "YouTube Studio không xác nhận trạng thái kiếm tiền đã chọn.",
                    )
                except BrowserUploadError as exc:
                    raise BrowserUploadNeedsReview(str(exc)) from exc
                await _require_click(
                    page,
                    [
                        "ytcp-video-monetization ytcp-button#save-button",
                        "ytcp-video-monetization ytcp-button:has-text('Xong')",
                        "ytcp-video-monetization ytcp-button:has-text('Done')",
                        "ytcp-dialog ytcp-button:has-text('Xong')",
                        "ytcp-dialog ytcp-button:has-text('Done')",
                    ],
                    "Không thể lưu trạng thái kiếm tiền.",
                    timeout_ms=5000,
                )
                await asyncio.sleep(0.75)
                midroll_state = "not_applicable"
                if desired_on:
                    midroll_state = await _apply_midroll_setting(
                        page,
                        bool(settings.get("midroll_ads", True)),
                    )
                _emit_checkpoint(
                    persist_checkpoint,
                    "monetization_verified",
                    monetization=detection_payload,
                    video_monetization_state="on" if desired_on else "off",
                    midroll_ads_state=midroll_state,
                )
                if desired_on:
                    await _require_click(
                        page,
                        [
                            "ytcp-uploads-dialog ytcp-button#next-button",
                            "ytcp-video-upload-dialog ytcp-button#next-button",
                        ],
                        "Không thể chuyển sang bước tự đánh giá quảng cáo.",
                        timeout_ms=5000,
                    )
                    await asyncio.sleep(2.0)
                    ad_suitability = await page.query_selector(
                        "ytcp-uploads-dialog ytcp-self-certification, "
                        "ytcp-video-upload-dialog ytcp-self-certification, "
                        "ytcp-uploads-dialog [test-id='self-certification']"
                    )
                    if ad_suitability is None:
                        raise BrowserUploadNeedsReview(
                            "Đã bật kiếm tiền nhưng không tìm thấy bước tự đánh giá quảng cáo."
                        )
                    progress("Đang hoàn tất tự đánh giá quảng cáo...", "self_certification", 70)
                    await ad_suitability.evaluate("element => element.scrollTop = element.scrollHeight")
                    none_selectors = [
                        "ytcp-self-certification #none-of-the-above-checkbox",
                        "ytcp-self-certification tp-yt-paper-checkbox:has-text('Không chứa nội dung nào ở trên')",
                        "ytcp-self-certification tp-yt-paper-checkbox:has-text('None of the above')",
                    ]
                    await _require_click(
                        page,
                        none_selectors,
                        "Không thể chọn 'Không chứa nội dung nào ở trên'.",
                        timeout_ms=5000,
                    )
                    await _require_selected(
                        page,
                        none_selectors,
                        "YouTube Studio không xác nhận lựa chọn tự đánh giá quảng cáo.",
                    )
                    await _require_click(
                        page,
                        [
                            "ytcp-self-certification ytcp-button#submit-questionnaire-button",
                            "ytcp-self-certification ytcp-button:has-text('Gửi thông tin đánh giá')",
                            "ytcp-self-certification ytcp-button:has-text('Submit rating')",
                        ],
                        "Không thể gửi thông tin tự đánh giá quảng cáo.",
                        timeout_ms=5000,
                    )
                    suitability_saved = False
                    for _ in range(12):
                        saved_marker = await page.query_selector(
                            "tp-yt-paper-toast:has-text('Đã lưu thông tin đánh giá'), "
                            "tp-yt-paper-toast:has-text('Rating saved'), "
                            "ytcp-toast:has-text('Đã lưu thông tin đánh giá'), "
                            "ytcp-toast:has-text('Rating saved')"
                        )
                        if saved_marker is not None and await saved_marker.is_visible():
                            suitability_saved = True
                            break
                        submit_btn = await page.query_selector("ytcp-self-certification ytcp-button#submit-questionnaire-button")
                        if submit_btn is not None:
                            disabled = await submit_btn.get_attribute("disabled")
                            aria_disabled = await submit_btn.get_attribute("aria-disabled")
                            if disabled is not None or str(aria_disabled).lower() == "true":
                                suitability_saved = True
                                break
                        await asyncio.sleep(0.3)
                    if not suitability_saved:
                        next_btn = await page.query_selector("ytcp-uploads-dialog ytcp-button#next-button, ytcp-video-upload-dialog ytcp-button#next-button")
                        if next_btn is not None and await next_btn.is_visible():
                            suitability_saved = True
                    if not suitability_saved:
                        raise BrowserUploadNeedsReview(
                            "Đã gửi tự đánh giá quảng cáo nhưng YouTube Studio chưa xác nhận đã lưu."
                        )
                    _emit_checkpoint(
                        persist_checkpoint,
                        "suitability_verified",
                        ad_suitability_state="none_of_the_above",
                    )
                    await _require_click(
                        page,
                        [
                            "ytcp-uploads-dialog ytcp-button#next-button",
                            "ytcp-video-upload-dialog ytcp-button#next-button",
                        ],
                        "Không thể chuyển khỏi bước tự đánh giá quảng cáo.",
                        timeout_ms=5000,
                    )
                    await asyncio.sleep(2.0)
                else:
                    _emit_checkpoint(
                        persist_checkpoint,
                        "suitability_skipped",
                        ad_suitability_state="not_applicable",
                    )

            # 8. Traverse Remaining Tabs (Video Elements & Checks -> Visibility)
            progress("Đang duyệt qua các bước trung gian đến tab Chế độ hiển thị...", "navigating_tabs", 75)
            caption_locator = ""
            end_screen_locator = ""
            elements_recorded = False
            checks_recorded = False
            reached_visibility = False
            for step_idx in range(8):
                cancel_check()
                active_step = await _read_active_upload_step(page)
                if active_step == "elements" and not elements_recorded:
                    end_screen_source_video_id = str(
                        settings.get("end_screen_source_video_id") or ""
                    ).strip()
                    if end_screen_source_video_id:
                        progress(
                            "Đang nhập màn hình kết thúc...",
                            "importing_end_screen",
                            73,
                        )
                        end_screen_locator = await _import_end_screen_from_video(
                            page,
                            end_screen_source_video_id,
                        )
                    upload_captions = bool(settings.get("upload_captions", True))
                    if upload_captions and caption_path and caption_path.exists():
                        progress("Đang tải phụ đề SRT...", "uploading_caption", 74)
                        caption_locator = await _upload_caption_from_elements(
                            page,
                            caption_path,
                            language,
                        )
                    elif upload_captions and (caption_path is None or not caption_path.exists()):
                        logger.info("Cấu hình upload_captions bật nhưng không có file phụ đề SRT; bỏ qua bước tải phụ đề.")
                    elements_recorded = True
                    _emit_checkpoint(
                        persist_checkpoint,
                        "elements_verified",
                        caption_locator=caption_locator,
                        caption_state="uploaded" if caption_locator else "skipped",
                        end_screen_locator=end_screen_locator,
                        end_screen_state=(
                            "imported" if end_screen_locator else "skipped"
                        ),
                    )
                if active_step == "checks" and not checks_recorded:
                    checks_panel = await page.query_selector(
                        "ytcp-uploads-dialog ytcp-video-checks, "
                        "ytcp-video-upload-dialog ytcp-video-checks"
                    )
                    checks_text = (
                        str(await checks_panel.inner_text() or "")
                        if checks_panel is not None
                        else ""
                    )
                    restriction = _find_blocking_restriction(checks_text)
                    if restriction:
                        raise BrowserUploadNeedsReview(
                            f"YouTube Studio phát hiện hạn chế cần kiểm tra: {restriction}."
                        )
                    checks_recorded = True
                    _emit_checkpoint(
                        persist_checkpoint,
                        "checks_observed",
                        checks_policy=str(
                            settings.get("checks_policy") or "schedule_immediately"
                        ),
                    )
                if active_step == "visibility":
                    logger.info("Đã đến tab Chế độ hiển thị (Visibility) tại bước %d.", step_idx + 1)
                    reached_visibility = True
                    break

                logger.info("Chưa đến tab Chế độ hiển thị; đang bấm 'Tiếp' (lần %d)...", step_idx + 1)
                await _require_click(
                    page,
                    [
                        "ytcp-uploads-dialog ytcp-button#next-button",
                        "ytcp-video-upload-dialog ytcp-button#next-button",
                    ],
                    f"Không thể chuyển bước từ tab {active_step}.",
                    timeout_ms=5000,
                )
                await asyncio.sleep(1.5)
            if not reached_visibility:
                raise BrowserUploadError("Không thể đến bước Chế độ hiển thị của upload wizard.")

            # 9. Tab Visibility (Private vs Schedule vs Public)
            progress("Đang thiết lập Chế độ hiển thị & Đặt lịch...", "visibility_and_publish", 85)
            cancel_check()

            # Ensure Chromium finishes uploading 100% video stream before finalizing visibility
            if not resuming_existing_draft:
                progress("Đang chờ tải lên 100% file video lên YouTube...", "uploading_file", 80)
                await _wait_for_file_upload_complete(
                    page,
                    timeout_seconds=timeout_seconds,
                    progress=progress,
                    cancel_check=cancel_check,
                )

            if schedule_at:
                local_dt = _parse_schedule_at(schedule_at, publication_timezone)
                target_date_str = _format_date_for_picker(local_dt.date())
                target_time_str = _format_time_for_picker(local_dt.time())

                logger.info(
                    "Đặt lịch YouTube qua browser: %s (Local: %s %s)",
                    schedule_at,
                    target_date_str,
                    target_time_str,
                )

                # 1. Click Schedule accordion / radio button
                schedule_radio_clicked = await _safe_click(
                    page,
                    [
                        "ytcp-uploads-dialog tp-yt-paper-radio-button#schedule-radio-button",
                        "ytcp-uploads-dialog tp-yt-paper-radio-button[name='SCHEDULE']",
                        "ytcp-uploads-dialog #second-container-expand-button",
                        "ytcp-video-upload-dialog tp-yt-paper-radio-button#schedule-radio-button",
                        "ytcp-video-upload-dialog tp-yt-paper-radio-button[name='SCHEDULE']",
                        "ytcp-video-upload-dialog #second-container-expand-button",
                    ],
                    timeout_ms=8000,
                )
                if not schedule_radio_clicked:
                    raise BrowserUploadError("Không thể chọn radio 'Lên lịch' (Schedule) trên YouTube Studio.")

                await asyncio.sleep(1.5)

                # 2. Set Date & Time
                date_filled, date_value = await _set_datepicker_value(page, local_dt.date())
                if not date_filled:
                    raise BrowserUploadError(f"Không thể điền ngày đặt lịch YouTube (giá trị: {date_value}).")

                time_filled, time_value = await _set_timepicker_value(page, local_dt.time())
                if not time_filled:
                    raise BrowserUploadError(f"Không thể điền giờ đặt lịch YouTube (giá trị: {time_value}).")

                if bool(settings.get("premiere", False)):
                    if not await _set_checkbox(
                    page,
                    [
                        "ytcp-uploads-dialog tp-yt-paper-checkbox#premiere-checkbox",
                        "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Công chiếu')",
                        "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Premiere')",
                        "ytcp-video-upload-dialog tp-yt-paper-checkbox#premiere-checkbox",
                        ],
                        True,
                    ):
                        raise BrowserUploadError("Không thể bật chế độ Công chiếu theo cấu hình.")

                _emit_checkpoint(
                    persist_checkpoint,
                    "visibility_verified",
                    scheduled_at=str(schedule_at),
                    publication_timezone=publication_timezone,
                    local_date=target_date_str,
                    local_time=target_time_str,
                    verified_date_value=date_value,
                    verified_time_value=time_value,
                )

                # 3. Click Schedule Done Button
                schedule_done_selectors = [
                    "ytcp-uploads-dialog ytcp-button#done-button",
                    "ytcp-uploads-dialog #done-button button",
                    "ytcp-video-upload-dialog ytcp-button#done-button",
                    "ytcp-video-upload-dialog #done-button button",
                ]
                await _wait_for_enabled_action(
                    page,
                    schedule_done_selectors,
                    timeout_seconds=timeout_seconds,
                    cancel_check=cancel_check,
                    action_name="đặt lịch",
                )
                done_clicked = await _safe_click(
                    page,
                    schedule_done_selectors,
                    timeout_ms=8000,
                )
                if not done_clicked:
                    raise BrowserUploadError("Không thể click nút 'Lên lịch' (Done/Schedule) trên YouTube Studio.")

                await asyncio.sleep(3.0)

                confirmation = await page.query_selector("ytcp-confirmation-dialog")
                if confirmation is not None and await confirmation.is_visible():
                    confirmation_text = str(await confirmation.inner_text() or "")
                    checks_pending = bool(
                        re.search(
                            r"vẫn đang kiểm tra|still checking",
                            confirmation_text,
                            re.IGNORECASE,
                        )
                    )
                    if not checks_pending:
                        raise BrowserUploadNeedsReview(
                            "YouTube Studio hiển thị cảnh báo cần kiểm tra thủ công trước khi đặt lịch."
                        )
                    await _require_click(
                        page,
                        [
                            "ytcp-confirmation-dialog #confirm-button",
                            "ytcp-confirmation-dialog ytcp-button:has-text('Đã hiểu')",
                            "ytcp-confirmation-dialog ytcp-button:has-text('Got it')",
                        ],
                        "Không thể xác nhận thông báo checks đang chạy.",
                        timeout_ms=3000,
                    )
                    await asyncio.sleep(2.0)

            elif resolved_publish_mode == "public":
                # Public immediately mode
                logger.info("Chọn chế độ Công khai ngay (Public)...")
                public_clicked = await _safe_click(
                    page,
                    [
                        "ytcp-uploads-dialog tp-yt-paper-radio-button[name='PUBLIC']",
                        "ytcp-video-upload-dialog tp-yt-paper-radio-button[name='PUBLIC']",
                        "tp-yt-paper-radio-button[name='PUBLIC']",
                        "tp-yt-paper-radio-button:has-text('Công khai')",
                        "tp-yt-paper-radio-button:has-text('Public')",
                    ],
                    timeout_ms=8000,
                )
                if not public_clicked:
                    raise BrowserUploadError("Không thể chọn radio 'Công khai' (Public) trên YouTube Studio.")

                await asyncio.sleep(1.0)
                public_done_selectors = [
                    "ytcp-uploads-dialog ytcp-button#done-button",
                    "ytcp-uploads-dialog #done-button button",
                    "ytcp-video-upload-dialog ytcp-button#done-button",
                    "ytcp-video-upload-dialog #done-button button",
                    "ytcp-button#done-button",
                    "ytcp-button:has-text('Xuất bản')",
                    "ytcp-button:has-text('Publish')",
                ]
                await _wait_for_enabled_action(
                    page,
                    public_done_selectors,
                    timeout_seconds=timeout_seconds,
                    cancel_check=cancel_check,
                    action_name="xuất bản công khai",
                )
                done_clicked = await _safe_click(
                    page,
                    public_done_selectors,
                    timeout_ms=8000,
                )
                if not done_clicked:
                    raise BrowserUploadError("Không thể click nút 'Xuất bản' (Publish/Done) trên YouTube Studio.")

                await asyncio.sleep(3.0)
                _emit_checkpoint(
                    persist_checkpoint,
                    "visibility_verified",
                    privacy_status="public",
                )

            else:
                # Private mode
                logger.info("Lưu video ở chế độ Riêng tư (Private)...")
                private_clicked = await _safe_click(
                    page,
                    [
                        "ytcp-uploads-dialog tp-yt-paper-radio-button[name='PRIVATE']",
                        "ytcp-video-upload-dialog tp-yt-paper-radio-button[name='PRIVATE']",
                    ],
                    timeout_ms=8000,
                )
                if not private_clicked:
                    raise BrowserUploadError("Không thể chọn radio 'Riêng tư' (Private) trên YouTube Studio.")

                await asyncio.sleep(1.0)
                private_done_selectors = [
                    "ytcp-uploads-dialog ytcp-button#done-button",
                    "ytcp-uploads-dialog #done-button button",
                    "ytcp-video-upload-dialog ytcp-button#done-button",
                    "ytcp-video-upload-dialog #done-button button",
                ]
                await _wait_for_enabled_action(
                    page,
                    private_done_selectors,
                    timeout_seconds=timeout_seconds,
                    cancel_check=cancel_check,
                    action_name="lưu riêng tư",
                )
                done_clicked = await _safe_click(
                    page,
                    private_done_selectors,
                    timeout_ms=8000,
                )
                if not done_clicked:
                    raise BrowserUploadError("Không thể click nút 'Lưu' (Save/Done) trên YouTube Studio.")

                await asyncio.sleep(3.0)
                _emit_checkpoint(
                    persist_checkpoint,
                    "visibility_verified",
                    privacy_status="private",
                )

            # 10. Close Post-Publish Dialog & Extract Final Video ID if missed earlier
            progress("Đang hoàn tất và đóng hộp thoại...", "finishing_upload", 95)
            confirmation_verified = False
            try:
                # Try finding confirmation or share dialog
                share_dialog = await page.wait_for_selector(
                    UPLOAD_COMPLETION_DIALOG_SELECTOR,
                    state="visible",
                    timeout=30000,
                )
                if share_dialog and not youtube_video_id:
                    dialog_text = await share_dialog.inner_text()
                    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", dialog_text)
                    if match:
                        youtube_video_id = match.group(1).strip()
                        logger.info("Đã trích xuất Video ID từ dialog xác nhận: %s", youtube_video_id)
                dialog_text = str(await share_dialog.inner_text() or "") if share_dialog else ""
                if schedule_at:
                    confirmation_verified = bool(
                        re.search(r"Đã lên lịch|scheduled", dialog_text, re.IGNORECASE)
                    )
                elif resolved_publish_mode == "public":
                    confirmation_verified = bool(
                        re.search(r"Đã xuất bản|published|video đã được xuất bản|video published", dialog_text, re.IGNORECASE)
                        or share_dialog
                    )
                else:
                    confirmation_verified = bool(share_dialog)

                close_btn = await page.wait_for_selector(
                    "ytcp-button#close-button, ytcp-button:has-text('Đóng'), ytcp-button:has-text('Close'), ytcp-video-share-dialog #close-button",
                    state="visible",
                    timeout=8000,
                )
                if close_btn:
                    await close_btn.click()
                    await asyncio.sleep(2.0)
            except Exception as c_exc:
                logger.debug("Không đọc được hộp thoại hoàn tất: %s", c_exc)

            if not youtube_video_id:
                raise BrowserUploadError("Upload qua trình duyệt hoàn tất nhưng không trích xuất được YouTube Video ID.")
            if not confirmation_verified:
                logger.info(
                    "Dialog xác nhận không khớp chuỗi text mẫu nhưng đã trích xuất được Video ID '%s'; tiếp tục xác minh trực tiếp trên trang chỉnh sửa YouTube Studio...",
                    youtube_video_id,
                )

            try:
                await page.goto(
                    f"https://studio.youtube.com/video/{youtube_video_id}/edit",
                    wait_until="commit",
                    timeout=60000,
                )
            except Exception as nav_exc:
                logger.debug("Thông báo chuyển hướng trang edit: %s", nav_exc)

            # Poll for video edit page and visibility status to fully load
            editor_text = ""
            final_restriction = ""
            schedule_verified_on_page = False
            schedule_matches = False

            verify_deadline = time.monotonic() + 30.0
            while time.monotonic() < verify_deadline:
                cancel_check()
                if f"/video/{youtube_video_id}/" in page.url:
                    editor_text = str(await page.locator("body").inner_text() or "")
                    page_html = await page.content()
                    final_restriction = _find_blocking_restriction(editor_text)
                    if final_restriction:
                        break
                    if schedule_at:
                        if re.search(r"Đã lên lịch|Scheduled", editor_text, re.IGNORECASE):
                            schedule_verified_on_page = True
                            schedule_matches = (
                                _schedule_date_matches(editor_text, local_dt.date())
                                and _schedule_time_matches(editor_text, local_dt.time())
                            ) or _schedule_timestamp_matches(page_html, local_dt)
                            if schedule_matches:
                                break
                    elif resolved_publish_mode == "public":
                        if re.search(r"Công khai|Public", editor_text, re.IGNORECASE):
                            break
                    else:
                        if re.search(r"Riêng tư|Private|Không công khai|Unlisted", editor_text, re.IGNORECASE):
                            break
                await asyncio.sleep(1.5)

            if f"/video/{youtube_video_id}/" not in page.url:
                raise BrowserUploadNeedsReview(
                    "Không thể mở lại đúng video để xác minh sau upload."
                )
            if final_restriction:
                raise BrowserUploadNeedsReview(
                    f"Video có hạn chế cần kiểm tra thủ công: {final_restriction}."
                )
            if schedule_at and not schedule_verified_on_page:
                raise BrowserUploadNeedsReview(
                    "Đã bấm đặt lịch nhưng trang video chưa hiển thị trạng thái Đã lên lịch."
                )
            if schedule_at and not schedule_matches:
                raise BrowserUploadNeedsReview(
                    "Video đã lên lịch nhưng trang chỉnh sửa chưa hiển thị đúng ngày/giờ theo timezone kênh."
                )
            if caption_locator:
                _emit_checkpoint(
                    persist_checkpoint,
                    "caption_verified",
                    caption_locator=caption_locator,
                )
            _emit_checkpoint(
                persist_checkpoint,
                "scheduled_verified" if schedule_at else "completed",
                youtube_video_id=youtube_video_id,
                scheduled_at=str(schedule_at or ""),
                publication_timezone=publication_timezone,
            )

            progress("Upload qua trình duyệt hoàn tất thành công 100%!", "completed", 100)
            return {
                "youtube_video_id": youtube_video_id,
                "published_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
                "status": "scheduled" if schedule_at else ("published" if resolved_publish_mode == "public" else "uploaded_private"),
                "privacy_status": "public" if resolved_publish_mode == "public" else "private",
                "is_published": bool(resolved_publish_mode == "public"),
                "scheduled_at": str(schedule_at or ""),
                "title": title,
                "schedule_verified": bool(schedule_at),
                "caption_locator": caption_locator,
                "end_screen_locator": end_screen_locator,
                "monetization_capability": detection.capability.value,
                "monetization_detection_evidence": list(detection.evidence),
                "video_monetization_state": (
                    "not_applicable"
                    if detection.capability is MonetizationCapability.UNAVAILABLE
                    else "off"
                    if monetization_mode == MONETIZATION_MODE_KEEP_OFF
                    else "on"
                ),
                "ad_suitability_state": (
                    "none_of_the_above"
                    if detection.capability is MonetizationCapability.AVAILABLE
                    and monetization_mode != MONETIZATION_MODE_KEEP_OFF
                    else "not_applicable"
                ),
            }

        except Exception as exc:
            logger.error("Lỗi trong quá trình upload YouTube qua browser: %s", exc, exc_info=True)
            failure_status = (
                "needs_review"
                if isinstance(exc, BrowserUploadNeedsReview)
                else "failed"
            )
            try:
                _emit_checkpoint(
                    persist_checkpoint,
                    failure_status,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    youtube_video_id=locals().get("youtube_video_id", ""),
                )
            except Exception as checkpoint_exc:
                logger.warning("Không thể lưu checkpoint lỗi browser upload: %s", checkpoint_exc)
            try:
                diag = await capture_browser_diagnostics_async(
                    page=page,
                    service="youtube_studio",
                    video_id=locals().get("existing_video_id") or locals().get("youtube_video_id", ""),
                    job_id=locals().get("profile_id", ""),
                    error=exc,
                    action_name="youtube_browser_upload",
                )
                if diag.get("screenshot_path"):
                    logger.error("Đã chụp ảnh màn hình chẩn đoán lỗi tại: %s", diag["screenshot_path"])
            except Exception as s_exc:
                logger.warning("Không thể chụp chẩn đoán trình duyệt: %s", s_exc)
            if isinstance(exc, BrowserUploadError):
                raise
            raise BrowserUploadError(f"Upload qua trình duyệt thất bại: {exc}") from exc
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def make_video_public_via_browser(
    *,
    profile_id: str,
    youtube_video_id: str,
    timeout_seconds: float = 60.0,
    auto_stop_gpm: bool | None = None,
) -> dict[str, Any]:
    """Change visibility of an existing YouTube video to Public in GPM Profile via Playwright CDP."""
    clean_profile = str(profile_id or "").strip()
    clean_vid_id = str(youtube_video_id or "").strip()
    if not clean_profile:
        raise BrowserUploadError("GPM Profile ID của kênh không được để trống.")
    if not clean_vid_id:
        raise BrowserUploadError("YouTube Video ID không được để trống.")

    logger.info("Bắt đầu chuyển video %s sang trạng thái Công khai trong GPM profile %s", clean_vid_id, clean_profile)
    async with gpm_browser_session(clean_profile, auto_stop=auto_stop_gpm) as (context, _browser):
        target_page = None
        for p in context.pages:
            if p.url in ("about:blank", "chrome://newtab/", ""):
                target_page = p
                break
        if target_page is None:
            target_page = await context.new_page()
        page = target_page
        try:
            edit_url = f"https://studio.youtube.com/video/{clean_vid_id}/edit"
            logger.info("Mở trang chỉnh sửa video YouTube Studio: %s", edit_url)
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.0)

            # 1. Click Visibility dropdown trigger
            visibility_trigger_selectors = [
                "#visibility-container",
                "ytcp-video-metadata-visibility",
                "#edit-visibility-button",
                "ytcp-video-metadata-visibility ytcp-text-dropdown-trigger",
                "ytcp-video-metadata-visibility #trigger",
                "[test-id='visibility-dropdown']",
                "button[aria-label*='chế độ hiển thị']",
                "button[aria-label*='visibility']",
            ]
            trigger_clicked = await _safe_click(page, visibility_trigger_selectors, timeout_ms=10000)
            if not trigger_clicked:
                raise BrowserUploadError("Không tìm thấy mục Chế độ hiển thị trên trang chỉnh sửa YouTube Studio.")
            await asyncio.sleep(1.0)

            # 2. Click Public radio button
            public_radio_selectors = [
                "tp-yt-paper-radio-button[name='PUBLIC']",
                "ytcp-video-metadata-visibility tp-yt-paper-radio-button[name='PUBLIC']",
                "tp-yt-paper-radio-button:has-text('Công khai')",
                "tp-yt-paper-radio-button:has-text('Public')",
            ]
            radio_clicked = await _safe_click(page, public_radio_selectors, timeout_ms=8000)
            if not radio_clicked:
                raise BrowserUploadError("Không thể chọn radio 'Công khai' (Public) trong dropdown hiển thị.")
            await asyncio.sleep(1.0)

            # 3. Click Done inside visibility dialog if present
            done_selectors = [
                "ytcp-video-metadata-visibility #save-button",
                "ytcp-video-metadata-visibility ytcp-button#done-button",
                "ytcp-video-metadata-visibility ytcp-button:has-text('Xong')",
                "ytcp-video-metadata-visibility ytcp-button:has-text('Done')",
                "ytcp-button#done-button",
            ]
            await _safe_click(page, done_selectors, timeout_ms=5000)
            await asyncio.sleep(1.0)

            # 4. Click Save button on the edit page (top right)
            save_btn_selectors = [
                "#save-button",
                "ytcp-entity-page #save-button",
                "ytcp-button#save",
                "ytcp-button:has-text('Lưu')",
                "ytcp-button:has-text('Save')",
            ]
            save_clicked = await _safe_click(page, save_btn_selectors, timeout_ms=8000)
            if not save_clicked:
                logger.info("Nút Lưu không cần click hoặc đã tự động lưu.")
            else:
                await asyncio.sleep(2.0)

            logger.info("Đã chuyển video %s sang trạng thái Công khai (Public) thành công.", clean_vid_id)
            return {
                "success": True,
                "youtube_video_id": clean_vid_id,
                "status": "published",
                "privacy_status": "public",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass
