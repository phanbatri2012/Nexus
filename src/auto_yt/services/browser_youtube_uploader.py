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


UPLOAD_DIALOG_SELECTOR = "ytcp-uploads-dialog, ytcp-video-upload-dialog"
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
            el = await page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
            if el:
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _safe_fill(page, selectors: list[str], text: str, timeout_ms: int = 5000) -> bool:
    """Try clicking and filling text into the first matching selector."""
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
            if el:
                await el.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await el.fill(text)
                return True
        except Exception:
            continue
    return False


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


async def _require_text_value(
    page,
    selectors: list[str],
    expected: str,
    error_message: str,
) -> None:
    expected_normalized = str(expected or "").replace("\r\n", "\n").strip()
    for selector in selectors:
        try:
            element = await page.query_selector(selector)
            if element is None:
                continue
            actual = (await _read_control_value(element)).replace("\r\n", "\n").strip()
            if actual == expected_normalized:
                return
        except Exception:
            continue
    raise BrowserUploadError(error_message)


def _emit_checkpoint(
    callback: Callable[[str, dict[str, Any]], None],
    stage: str,
    **details: Any,
) -> None:
    callback(stage, {"stage": stage, **details})


async def _read_monetization_snapshot(page) -> dict[str, Any]:
    """Read upload-step topology from the Studio dialog without page-wide text selectors."""
    dialog = await page.query_selector(UPLOAD_DIALOG_SELECTOR)
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
    for attribute in ("aria-checked", "aria-selected", "checked", "active"):
        value = str(await element.get_attribute(attribute) or "").lower()
        if value in {"true", "checked", "active"}:
            return True
    return False


async def _require_selected(page, selectors: list[str], error_message: str) -> None:
    for selector in selectors:
        try:
            element = await page.query_selector(selector)
            if element is not None and await _is_selected(element):
                return
        except Exception:
            continue
    raise BrowserUploadError(error_message)


async def _set_checkbox(page, selectors: list[str], desired: bool) -> bool:
    for selector in selectors:
        try:
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


async def _read_control_value(element) -> str:
    try:
        value = await element.input_value()
        if str(value or "").strip():
            return str(value).strip()
    except Exception:
        pass
    for attribute in ("value", "aria-label"):
        try:
            value = await element.get_attribute(attribute)
            if str(value or "").strip():
                return str(value).strip()
        except Exception:
            continue
    try:
        return str(await element.inner_text() or "").strip()
    except Exception:
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

    Uses pure CDP calls (no Playwright internals) for version compatibility.
    """
    resolved = str(Path(file_path).resolve())

    # Wait for element to exist in DOM first (via Playwright, just for timing)
    el = await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    if not el:
        raise BrowserUploadError(f"Không tìm thấy element: {selector}")

    cdp = await page.context.new_cdp_session(page)
    try:
        # Use pure CDP to find the element — no Playwright internals needed
        doc = await cdp.send("DOM.getDocument")
        node = await cdp.send("DOM.querySelector", {
            "nodeId": doc["root"]["nodeId"],
            "selector": selector,
        })
        node_id = node.get("nodeId", 0)
        if not node_id:
            raise BrowserUploadError(f"CDP không tìm thấy element: {selector}")

        await cdp.send("DOM.setFileInputFiles", {
            "files": [resolved],
            "nodeId": node_id,
        })
        logger.info("CDP set_input_files OK: %s (%s)", resolved, selector)
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
        category = await page.query_selector(
            "ytcp-form-select#category, #category, #category-container, "
            "ytcp-form-select:has-text('Danh mục'), "
            "ytcp-form-select:has-text('Category')"
        )
        if category:
            await category.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
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
                selected_text = str(await category.inner_text() or "") if category else ""
                if label.casefold() in selected_text.casefold():
                    logger.info("Đã chọn Thể loại YouTube: %s (ID: %s)", label, category_id)
                    return True
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
            [
                "ytcp-uploads-dialog #playlists ytcp-dropdown-trigger",
                "ytcp-uploads-dialog ytcp-button:has-text('Danh sách phát')",
                "ytcp-video-upload-dialog #playlists ytcp-dropdown-trigger",
            ],
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
                "ytcp-video-audience #age-restriction-button",
                "ytcp-video-audience ytcp-button:has-text('Giới hạn độ tuổi')",
                "ytcp-video-audience ytcp-button:has-text('Age restriction')",
            ],
            timeout_ms=1200,
        )
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
                "ytcp-uploads-dialog tp-yt-paper-checkbox:has-text('Cho phép dùng phần cảnh tự động')",
            ],
            True,
            "chapter tự động",
        ),
        (
            "automatic_places",
            [
                "tp-yt-paper-checkbox#allow-automatic-places",
                "ytcp-checkbox-lit#allow-automatic-places",
                "ytcp-video-automatic-places tp-yt-paper-checkbox",
                "ytcp-video-automatic-places ytcp-checkbox-lit",
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
                "tp-yt-paper-radio-button[name='VIDEO_REMIX_SETTING_ALLOW_VIDEO_AND_AUDIO_REMIXING']",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('hình ảnh và âm thanh')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('video and audio')",
            ],
            "audio_only": [
                "tp-yt-paper-radio-button[name='VIDEO_REMIX_SETTING_ALLOW_AUDIO_ONLY_REMIXING']",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Chỉ âm thanh')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Audio only')",
            ],
            "disabled": [
                "tp-yt-paper-radio-button[name='VIDEO_REMIX_SETTING_DISABLE_REMIXING']",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Không cho phép')",
                "ytcp-video-remix-settings tp-yt-paper-radio-button:has-text('Don\'t allow')",
            ],
        }[remix_policy]
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
        "input[type='file'][accept*='.srt'], input[type='file'][accept*='text']"
    )
    if subtitle_input is None:
        raise BrowserUploadError("Không tìm thấy input upload SRT trong hộp thoại phụ đề.")
    await subtitle_input.set_input_files(str(caption_path.resolve()))
    await asyncio.sleep(1.0)
    await _require_click(
        page,
        [
            "ytcp-button#done-button:has-text('Xong')",
            "ytcp-button#done-button:has-text('Done')",
            "ytcp-button#save-button",
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
            for value in [caption_path.name, *language_labels, "Đã thêm", "Added"]
            if value
        ),
        re.IGNORECASE,
    )
    verified = False
    for _ in range(6):
        section = await page.query_selector(
            "ytcp-video-elements #subtitles, "
            "ytcp-video-elements [test-id='subtitles']"
        )
        section_text = str(await section.inner_text() or "") if section else ""
        if verification_pattern.search(section_text):
            verified = True
            break
        await asyncio.sleep(0.5)
    if not verified:
        raise BrowserUploadNeedsReview(
            "Đã upload SRT nhưng YouTube Studio chưa xác nhận track/ngôn ngữ phụ đề."
        )
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
    clean_channel_id = str(expected_channel_id or "").strip()
    clean_existing_video_id = str(existing_video_id or "").strip()
    progress("Đang mở trình duyệt GPM của kênh...", "browser_launching", 5)
    logger.info("Bắt đầu upload qua trình duyệt GPM profile %s cho video '%s'", clean_profile, title)

    async with gpm_browser_session(clean_profile, auto_stop=auto_stop_gpm) as (context, _browser):
        cancel_check()
        page = await context.new_page()
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
                await page.goto(
                    f"https://studio.youtube.com/video/{clean_existing_video_id}/edit",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await asyncio.sleep(3.0)
                if f"/video/{clean_existing_video_id}/" not in page.url:
                    raise BrowserUploadNeedsReview(
                        "Không mở được đúng bản nháp YouTube đã lưu; không upload bản thứ hai."
                    )
                body_text = str(await page.locator("body").inner_text() or "")
                scheduled_marker = bool(
                    re.search(r"Đã lên lịch|Scheduled", body_text, re.IGNORECASE)
                )
                if schedule_at and scheduled_marker:
                    resumed_local_dt = _parse_schedule_at(
                        schedule_at,
                        publication_timezone,
                    )
                    if (
                        not _schedule_date_matches(body_text, resumed_local_dt.date())
                        or not _schedule_time_matches(body_text, resumed_local_dt.time())
                    ):
                        raise BrowserUploadNeedsReview(
                            "Bản nháp đã lên lịch nhưng ngày/giờ trên YouTube không khớp timezone kênh."
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
                    }
                await page.goto(
                    f"https://studio.youtube.com/video/{clean_existing_video_id}/edit?d=ud",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await asyncio.sleep(2.0)
                upload_dialog = await page.query_selector(UPLOAD_DIALOG_SELECTOR)
                if upload_dialog is None:
                    raise BrowserUploadNeedsReview(
                        "Đã mở đúng bản nháp nhưng YouTube Studio không cho tiếp tục upload wizard; "
                        "giữ draft để kiểm tra, không upload lại MP4."
                    )
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
                for _ in range(15):
                    cancel_check()
                    try:
                        info_el = await page.query_selector(
                            "ytcp-uploads-dialog a.ytcp-video-info, "
                            "ytcp-uploads-dialog span.ytcp-video-info, "
                            "ytcp-uploads-dialog a[href*='youtu.be'], "
                            "ytcp-uploads-dialog [test-id='video-url-link'], "
                            "ytcp-video-upload-dialog a.ytcp-video-info, "
                            "ytcp-video-upload-dialog span.ytcp-video-info, "
                            "ytcp-video-upload-dialog a[href*='youtu.be'], "
                            "ytcp-video-upload-dialog [test-id='video-url-link']"
                        )
                        if info_el:
                            href = str(await info_el.get_attribute("href") or "")
                            text = str(await info_el.inner_text() or "")
                            match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", href or text)
                            if match:
                                youtube_video_id = match.group(1).strip()
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
            await _require_fill(
                page,
                [
                    "#title-textarea #textbox",
                    "#textbox[aria-label*='tiêu đề' i]",
                    "#textbox[aria-label*='title' i]",
                    "input#title",
                ],
                title,
                "Không thể điền tiêu đề video trên YouTube Studio.",
                timeout_ms=10000,
            )
            await _require_text_value(
                page,
                [
                    "#title-textarea #textbox",
                    "#textbox[aria-label*='tiêu đề' i]",
                    "#textbox[aria-label*='title' i]",
                    "input#title",
                ],
                title,
                "YouTube Studio không xác nhận tiêu đề vừa điền.",
            )
            await asyncio.sleep(1.0)

            # Description
            if description:
                await _require_fill(
                    page,
                    [
                        "#description-textarea #textbox",
                        "#description-textarea [contenteditable='true']",
                        "#textbox[aria-label*='mô tả' i]",
                        "#textbox[aria-label*='description' i]",
                    ],
                    description,
                    "Không thể điền mô tả video trên YouTube Studio.",
                    timeout_ms=10000,
                )
                await _require_text_value(
                    page,
                    [
                        "#description-textarea #textbox",
                        "#description-textarea [contenteditable='true']",
                        "#textbox[aria-label*='mô tả' i]",
                        "#textbox[aria-label*='description' i]",
                    ],
                    description,
                    "YouTube Studio không xác nhận mô tả vừa điền.",
                )
                await asyncio.sleep(1.0)

            # Upload Thumbnail
            if thumbnail_path and thumbnail_path.exists():
                progress("Đang tải lên thumbnail...", "uploading_thumbnail", 45)
                try:
                    thumb_input = await page.query_selector(
                        "ytcp-uploads-dialog ytcp-video-custom-still-editor input[type='file'], "
                        "ytcp-uploads-dialog input#file-loader[type='file'][accept*='image'], "
                        "ytcp-uploads-dialog input[type='file'][accept*='image'], "
                        "ytcp-video-upload-dialog ytcp-video-custom-still-editor input[type='file'], "
                        "ytcp-video-upload-dialog input#file-loader[type='file'][accept*='image'], "
                        "ytcp-video-upload-dialog input[type='file'][accept*='image']"
                    )
                    if not thumb_input:
                        raise BrowserUploadError("Không tìm thấy input thumbnail trong upload dialog.")
                    await thumb_input.set_input_files(str(thumbnail_path.resolve()))
                    thumbnail_verified = False
                    for _ in range(10):
                        preview = await page.query_selector(
                            "ytcp-uploads-dialog ytcp-video-custom-still-editor img[src], "
                            "ytcp-uploads-dialog #custom-thumbnail img[src], "
                            "ytcp-video-upload-dialog ytcp-video-custom-still-editor img[src], "
                            "ytcp-video-upload-dialog #custom-thumbnail img[src]"
                        )
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
            if made_for_kids:
                await _require_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_MFK']",
                        "tp-yt-paper-radio-button:has-text('Có, nội dung này dành cho trẻ em')",
                        "tp-yt-paper-radio-button:has-text('Yes, it\'s made for kids')",
                    ],
                    "Không thể chọn video dành cho trẻ em.",
                    timeout_ms=3000,
                )
            else:
                await _require_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
                        "tp-yt-paper-radio-button:has-text('Không, nội dung này không dành cho trẻ em')",
                        "tp-yt-paper-radio-button:has-text('No, it\'s not made for kids')",
                    ],
                    "Không thể chọn video không dành cho trẻ em.",
                    timeout_ms=3000,
                )
            audience_selectors = (
                ["tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_MFK']"]
                if made_for_kids
                else ["tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"]
            )
            await _require_selected(
                page,
                audience_selectors,
                "YouTube Studio không xác nhận đối tượng người xem.",
            )
            await asyncio.sleep(1.0)

            # Click Show More button
            await _safe_click(
                page,
                [
                    "ytcp-uploads-dialog #toggle-button",
                    "ytcp-uploads-dialog ytcp-button#toggle-button",
                    "ytcp-video-upload-dialog #toggle-button",
                    "ytcp-video-upload-dialog ytcp-button#toggle-button",
                ],
                timeout_ms=3000,
            )
            await asyncio.sleep(1.0)

            # Synthetic / Altered AI Media (Radio 'Có' / 'Không')
            if contains_synthetic_media:
                await _require_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_YES']",
                        "#altered-content-radio-group tp-yt-paper-radio-button:has-text('Có')",
                        "tp-yt-paper-radio-group[name='altered-content-radios'] tp-yt-paper-radio-button:has-text('Có')",
                        "tp-yt-paper-radio-button[name='ALTERED_CONTENT_YES']",
                    ],
                    "Không thể khai báo nội dung tổng hợp bằng AI.",
                    timeout_ms=3000,
                )
            else:
                await _require_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_NO']",
                        "#altered-content-radio-group tp-yt-paper-radio-button:has-text('Không')",
                        "tp-yt-paper-radio-group[name='altered-content-radios'] tp-yt-paper-radio-button:has-text('Không')",
                        "tp-yt-paper-radio-button[name='ALTERED_CONTENT_NO']",
                    ],
                    "Không thể khai báo trạng thái nội dung tổng hợp.",
                    timeout_ms=3000,
                )
            altered_content_selectors = (
                [
                    "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_YES']",
                    "tp-yt-paper-radio-button[name='ALTERED_CONTENT_YES']",
                ]
                if contains_synthetic_media
                else [
                    "tp-yt-paper-radio-button[name='VIDEO_HAS_ALTERED_CONTENT_NO']",
                    "tp-yt-paper-radio-button[name='ALTERED_CONTENT_NO']",
                ]
            )
            await _require_selected(
                page,
                altered_content_selectors,
                "YouTube Studio không xác nhận khai báo nội dung tổng hợp.",
            )
            await asyncio.sleep(1.0)

            progress("Đang áp dụng thiết lập upload nâng cao...", "advanced_details", 48)
            await _apply_advanced_details_settings(
                page,
                settings,
                made_for_kids=made_for_kids,
                notify_subscribers=notify_subscribers,
            )

            # Fill Tags
            if tags_list:
                tags_str = ", ".join(tags_list[:30]) + ", "
                try:
                    tag_el = await page.wait_for_selector(
                        "input#text-input[aria-label*='Thẻ' i], input#text-input[aria-label*='tag' i], #tags-container input, input[placeholder*='dấu phẩy' i], input[placeholder*='comma' i]",
                        state="visible",
                        timeout=3000,
                    )
                    if tag_el:
                        await tag_el.click()
                        await tag_el.fill(tags_str)
                        await page.keyboard.press("Enter")
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
                            raise BrowserUploadError(
                                "YouTube Studio chưa xác nhận đầy đủ tags: "
                                + ", ".join(str(tag) for tag in missing_tags[:5])
                            )
                except Exception as tag_exc:
                    if isinstance(tag_exc, BrowserUploadError):
                        raise
                    raise BrowserUploadError(f"Không thể điền tags YouTube: {tag_exc}") from tag_exc

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

            # Click Next Button from Details tab
            progress("Hoàn tất tab Chi tiết -> Chuyển bước...", "next_step", 55)
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
                    for _ in range(10):
                        saved_marker = await page.query_selector(
                            "tp-yt-paper-toast:has-text('Đã lưu thông tin đánh giá'), "
                            "tp-yt-paper-toast:has-text('Rating saved'), "
                            "ytcp-toast:has-text('Đã lưu thông tin đánh giá'), "
                            "ytcp-toast:has-text('Rating saved')"
                        )
                        if saved_marker is not None and await saved_marker.is_visible():
                            suitability_saved = True
                            break
                        await asyncio.sleep(0.25)
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
                    if upload_captions:
                        if caption_path is None:
                            raise BrowserUploadError(
                                "Bộ prompt yêu cầu upload phụ đề nhưng không có file SRT."
                            )
                        progress("Đang tải phụ đề SRT...", "uploading_caption", 74)
                        caption_locator = await _upload_caption_from_elements(
                            page,
                            caption_path,
                            language,
                        )
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

            # 9. Tab Visibility (Private vs Schedule)
            progress("Đang thiết lập Chế độ hiển thị & Đặt lịch...", "visibility_and_publish", 85)
            cancel_check()

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

                # 2. Set Date
                date_filled = False
                date_selectors = [
                    "#datepicker-trigger input",
                    "input#datepicker-trigger",
                    "ytcp-date-picker input",
                    "#datepicker-trigger",
                    "input[aria-label*='ngày' i]",
                    "input[aria-label*='date' i]",
                    "input[placeholder*='ngày' i]",
                    "input[placeholder*='date' i]",
                ]
                date_value = ""
                for sel in date_selectors:
                    try:
                        date_input = await page.wait_for_selector(sel, state="visible", timeout=3000)
                        if date_input:
                            await date_input.click()
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await date_input.fill(target_date_str)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.5)
                            date_value = await _read_control_value(date_input)
                            if not _schedule_date_matches(date_value, local_dt.date()):
                                logger.debug(
                                    "Giá trị ngày chưa khớp sau khi điền bằng selector %s: %s",
                                    sel,
                                    date_value,
                                )
                                continue
                            date_filled = True
                            logger.info("Đã điền ngày đặt lịch: %s (selector: %s)", target_date_str, sel)
                            break
                    except Exception:
                        continue

                if not date_filled:
                    logger.warning("Không tìm thấy input datepicker tiêu chuẩn; thử click trực tiếp #datepicker-trigger...")
                    try:
                        dp_trigger = await page.query_selector("#datepicker-trigger")
                        if dp_trigger:
                            await dp_trigger.click()
                            await asyncio.sleep(0.5)
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await page.keyboard.type(target_date_str, delay=20)
                            await page.keyboard.press("Enter")
                            date_value = await _read_control_value(dp_trigger)
                            date_filled = _schedule_date_matches(
                                date_value,
                                local_dt.date(),
                            )
                    except Exception as dp_exc:
                        logger.warning("Không điền được datepicker trigger: %s", dp_exc)
                if not date_filled:
                    raise BrowserUploadError("Không thể điền ngày đặt lịch YouTube.")

                # 3. Set Time
                time_filled = False
                time_selectors = [
                    "#time-of-day-trigger input",
                    "input#time-input",
                    "ytcp-time-of-day-picker input",
                    "#time-of-day-trigger",
                    "ytcp-dropdown-trigger[aria-label*='giờ' i]",
                    "input[aria-label*='giờ' i]",
                    "input[aria-label*='time' i]",
                ]
                time_value = ""
                for sel in time_selectors:
                    try:
                        time_input = await page.wait_for_selector(sel, state="visible", timeout=3000)
                        if time_input:
                            await time_input.click()
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await time_input.fill(target_time_str)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.5)
                            time_value = await _read_control_value(time_input)
                            if not _schedule_time_matches(time_value, local_dt.time()):
                                logger.debug(
                                    "Giá trị giờ chưa khớp sau khi điền bằng selector %s: %s",
                                    sel,
                                    time_value,
                                )
                                continue
                            time_filled = True
                            logger.info("Đã điền giờ đặt lịch: %s (selector: %s)", target_time_str, sel)
                            break
                    except Exception:
                        continue

                if not time_filled:
                    logger.warning("Không tìm thấy input timepicker tiêu chuẩn; thử click trực tiếp #time-of-day-trigger...")
                    try:
                        tp_trigger = await page.query_selector("#time-of-day-trigger")
                        if tp_trigger:
                            await tp_trigger.click()
                            await asyncio.sleep(0.5)
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await page.keyboard.type(target_time_str, delay=20)
                            await page.keyboard.press("Enter")
                            time_value = await _read_control_value(tp_trigger)
                            time_filled = _schedule_time_matches(
                                time_value,
                                local_dt.time(),
                            )
                    except Exception as tp_exc:
                        logger.warning("Không điền được timepicker trigger: %s", tp_exc)
                if not time_filled:
                    raise BrowserUploadError("Không thể điền giờ đặt lịch YouTube.")

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

                # 4. Click Schedule Done Button
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
                    "ytcp-video-share-dialog, ytcp-publish-dialog",
                    state="visible",
                    timeout=10000,
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
                raise BrowserUploadNeedsReview(
                    "YouTube Studio chưa xác nhận thao tác lưu/đặt lịch thành công."
                )

            await page.goto(
                f"https://studio.youtube.com/video/{youtube_video_id}/edit",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await asyncio.sleep(2.0)
            if f"/video/{youtube_video_id}/" not in page.url:
                raise BrowserUploadNeedsReview(
                    "Không thể mở lại đúng video để xác minh sau upload."
                )
            editor_text = str(await page.locator("body").inner_text() or "")
            final_restriction = _find_blocking_restriction(editor_text)
            if final_restriction:
                raise BrowserUploadNeedsReview(
                    f"Video có hạn chế cần kiểm tra thủ công: {final_restriction}."
                )
            if schedule_at and not re.search(
                r"Đã lên lịch|Scheduled", editor_text, re.IGNORECASE
            ):
                raise BrowserUploadNeedsReview(
                    "Đã bấm đặt lịch nhưng trang video chưa hiển thị trạng thái Đã lên lịch."
                )
            if schedule_at and (
                not _schedule_date_matches(editor_text, local_dt.date())
                or not _schedule_time_matches(editor_text, local_dt.time())
            ):
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
                "status": "scheduled" if schedule_at else "uploaded_private",
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
                logs_dir = Path("data/logs")
                logs_dir.mkdir(parents=True, exist_ok=True)
                timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = logs_dir / f"yt_upload_error_{timestamp}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                evidence_path = logs_dir / f"yt_upload_error_{timestamp}.json"
                dialog = await page.query_selector(UPLOAD_DIALOG_SELECTOR)
                dialog_text = (
                    str(await dialog.inner_text() or "")[:20000]
                    if dialog is not None
                    else ""
                )
                try:
                    active_step_evidence = await _read_active_upload_step(page)
                except Exception:
                    active_step_evidence = "unknown"
                evidence_path.write_text(
                    json.dumps(
                        {
                            "url": page.url,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "active_step": active_step_evidence,
                            "dialog_text": dialog_text,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                logger.error("Đã chụp ảnh màn hình chẩn đoán lỗi tại: %s", screenshot_path)
            except Exception as s_exc:
                logger.warning("Không thể chụp ảnh màn hình chẩn đoán: %s", s_exc)
            if isinstance(exc, BrowserUploadError):
                raise
            raise BrowserUploadError(f"Upload qua trình duyệt thất bại: {exc}") from exc
        finally:
            try:
                await page.close()
            except Exception:
                pass
