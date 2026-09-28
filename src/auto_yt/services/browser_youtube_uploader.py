"""YouTube Studio Web Browser Automation Uploader.

Automates video uploading, metadata population, monetization self-rating,
thumbnail uploading, and scheduling/private publishing directly inside a channel's
dedicated GPM-Login profile session via Playwright CDP.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any, Callable

from auto_yt.services.gpm_service import gpm_browser_session

logger = logging.getLogger(__name__)


class BrowserUploadError(RuntimeError):
    """Raised when browser-based YouTube upload fails."""


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


async def upload_video_via_browser(
    *,
    profile_id: str,
    video_path: Path,
    thumbnail_path: Path | None = None,
    title: str,
    description: str,
    tags: list[str] | None = None,
    made_for_kids: bool = False,
    contains_synthetic_media: bool = True,
    notify_subscribers: bool = True,
    schedule_at: str | None = None,
    progress: Callable[[str, str, int], None] = lambda *_: None,
    cancel_check: Callable[[], None] = lambda: None,
    persist_video_id: Callable[[str], None] = lambda _: None,
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
    progress("Đang mở trình duyệt GPM của kênh...", "browser_launching", 5)
    logger.info("Bắt đầu upload qua trình duyệt GPM profile %s cho video '%s'", clean_profile, title)

    async with gpm_browser_session(clean_profile, auto_stop=auto_stop_gpm) as (context, _browser):
        cancel_check()
        page = await context.new_page()
        try:
            progress("Đang mở YouTube Studio...", "navigating_studio", 10)
            await page.goto(
                "https://studio.youtube.com",
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
                # Try direct URL navigation fallback if create button not clickable
                logger.warning("Không click được nút Tạo; điều hướng trực tiếp đến trang upload...")
                await page.goto(f"{current_url.rstrip('/')}/videos/upload?d=pt", wait_until="domcontentloaded")
                await asyncio.sleep(2.0)
            else:
                await asyncio.sleep(1.0)
                await _safe_click(
                    page,
                    [
                        "tp-yt-paper-item#text-item-0",
                        "tp-yt-paper-item:has-text('Tải video lên')",
                        "tp-yt-paper-item:has-text('Upload videos')",
                        "ytd-menu-service-item-renderer:has-text('Tải video lên')",
                        "ytd-menu-service-item-renderer:has-text('Upload videos')",
                    ],
                    timeout_ms=5000,
                )

            # 3. Inject Video MP4 File (via CDP to bypass Playwright 50MB limit)
            progress("Đang nạp file video MP4...", "uploading_file", 20)
            await _cdp_set_input_files(page, "input[type='file']", video_path, timeout_ms=15000)
            await asyncio.sleep(3.0)
            cancel_check()

            # 4. Extract YouTube Video ID immediately from draft sharing link
            progress("Đang trích xuất Video ID...", "extracting_video_id", 25)
            youtube_video_id = ""
            for _ in range(15):
                cancel_check()
                try:
                    info_el = await page.query_selector(
                        "a.ytcp-video-info, span.ytcp-video-info, a[href*='youtu.be'], [test-id='video-url-link']"
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

            if youtube_video_id:
                logger.info("Đã trích xuất YouTube Video ID từ trình duyệt: %s", youtube_video_id)
                try:
                    persist_video_id(youtube_video_id)
                except Exception as p_exc:
                    logger.warning("Không thể lưu trước Video ID: %s", p_exc)
            else:
                logger.warning("Chưa bắt được Video ID từ link chia sẻ; tiếp tục các bước nhập liệu...")

            # 5. Populate Details Tab (Title, Description, Thumbnail, Audience, AI disclosure, Tags)
            progress("Đang điền tiêu đề & mô tả video...", "filling_metadata", 35)

            # Title
            await _safe_fill(
                page,
                [
                    "#title-textarea #textbox",
                    "#textbox[aria-label*='tiêu đề' i]",
                    "#textbox[aria-label*='title' i]",
                    "input#title",
                ],
                title,
                timeout_ms=10000,
            )
            await asyncio.sleep(1.0)

            # Description
            if description:
                await _safe_fill(
                    page,
                    [
                        "#description-textarea #textbox",
                        "#description-textarea [contenteditable='true']",
                        "#textbox[aria-label*='mô tả' i]",
                        "#textbox[aria-label*='description' i]",
                    ],
                    description,
                    timeout_ms=10000,
                )
                await asyncio.sleep(1.0)

            # Upload Thumbnail
            if thumbnail_path and thumbnail_path.exists():
                progress("Đang tải lên thumbnail...", "uploading_thumbnail", 45)
                try:
                    thumb_input = await page.query_selector(
                        "input#file-loader[type='file'], input[type='file'][accept*='image']"
                    )
                    if thumb_input:
                        await thumb_input.set_input_files(str(thumbnail_path))
                        await asyncio.sleep(2.0)
                        logger.info("Đã nạp thumbnail qua browser: %s", thumbnail_path)
                except Exception as t_exc:
                    logger.warning("Không nạp được thumbnail qua browser: %s", t_exc)

            # Audience Selection (Not for kids / For kids)
            cancel_check()
            if made_for_kids:
                await _safe_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_MFK']",
                        "tp-yt-paper-radio-button:has-text('Có, nội dung này dành cho trẻ em')",
                        "tp-yt-paper-radio-button:has-text('Yes, it\'s made for kids')",
                    ],
                    timeout_ms=3000,
                )
            else:
                await _safe_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
                        "tp-yt-paper-radio-button:has-text('Không, nội dung này không dành cho trẻ em')",
                        "tp-yt-paper-radio-button:has-text('No, it\'s not made for kids')",
                    ],
                    timeout_ms=3000,
                )
            await asyncio.sleep(1.0)

            # Click Show More button
            await _safe_click(
                page,
                [
                    "#toggle-button",
                    "ytcp-button#toggle-button",
                    "button:has-text('Hiện thêm')",
                    "button:has-text('Show more')",
                    "ytcp-button:has-text('Hiện thêm')",
                ],
                timeout_ms=3000,
            )
            await asyncio.sleep(1.0)

            # Synthetic / Altered AI Media (Radio 'Có' / 'Không')
            if contains_synthetic_media:
                await _safe_click(
                    page,
                    [
                        "#altered-content-radio-group tp-yt-paper-radio-button:has-text('Có')",
                        "tp-yt-paper-radio-group[name='altered-content-radios'] tp-yt-paper-radio-button:has-text('Có')",
                        "tp-yt-paper-radio-button[name='ALTERED_CONTENT_YES']",
                        "tp-yt-paper-radio-button:has-text('Yes')",
                    ],
                    timeout_ms=3000,
                )
            else:
                await _safe_click(
                    page,
                    [
                        "#altered-content-radio-group tp-yt-paper-radio-button:has-text('Không')",
                        "tp-yt-paper-radio-group[name='altered-content-radios'] tp-yt-paper-radio-button:has-text('Không')",
                        "tp-yt-paper-radio-button[name='ALTERED_CONTENT_NO']",
                    ],
                    timeout_ms=3000,
                )
            await asyncio.sleep(1.0)

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
                except Exception as tag_exc:
                    logger.debug("Không tìm thấy ô nhập tags (hoặc đã được điền sẵn): %s", tag_exc)

            # Notify Subscribers Checkbox
            if not notify_subscribers:
                try:
                    notify_cb = await page.query_selector(
                        "tp-yt-paper-checkbox#notify-subscribers[aria-checked='true'], tp-yt-paper-checkbox:has-text('thông báo đến người đăng ký')[aria-checked='true']"
                    )
                    if notify_cb:
                        await notify_cb.click()
                        await asyncio.sleep(0.5)
                except Exception:
                    pass

            # Click Next Button from Details tab
            progress("Hoàn tất tab Chi tiết -> Chuyển bước...", "next_step", 55)
            await _safe_click(
                page,
                [
                    "ytcp-button#next-button",
                    "#next-button button",
                    "ytcp-button:has-text('Tiếp')",
                    "ytcp-button:has-text('Next')",
                ],
                timeout_ms=5000,
            )
            await asyncio.sleep(2.0)
            cancel_check()

            # 6. Adaptive Monetization Tab (If channel is monetized)
            monetization_tab = await page.query_selector(
                "ytcp-video-monetization, #monetization-step, [test-id='monetization-step'], div:has-text('Quảng cáo trên Trang xem và YouTube Premium')"
            )
            if monetization_tab:
                progress("Kênh có Bật kiếm tiền -> Tự động kích hoạt On...", "enabling_monetization", 60)
                logger.info("Phát hiện Tab Kiếm tiền; đang chọn Bật (On)...")
                # Open dropdown
                await _safe_click(
                    page,
                    [
                        "ytcp-video-monetization ytcp-dropdown-trigger",
                        "#monetization-step ytcp-dropdown-trigger",
                        "ytcp-video-monetization #dropdown-trigger",
                        "[aria-label*='Kiếm tiền' i]",
                    ],
                    timeout_ms=4000,
                )
                await asyncio.sleep(1.0)
                # Click On
                await _safe_click(
                    page,
                    [
                        "tp-yt-paper-radio-button:has-text('Bật')",
                        "tp-yt-paper-radio-button[name='ON']",
                        "[name='MONETIZATION_ON']",
                        "tp-yt-paper-radio-button:has-text('On')",
                    ],
                    timeout_ms=4000,
                )
                await asyncio.sleep(0.5)
                # Click Done
                await _safe_click(
                    page,
                    [
                        "ytcp-button:has-text('Xong')",
                        "ytcp-button:has-text('Done')",
                        "#save-button",
                    ],
                    timeout_ms=4000,
                )
                await asyncio.sleep(1.0)
                # Next to Ad suitability
                await _safe_click(
                    page,
                    [
                        "ytcp-button#next-button",
                        "#next-button button",
                        "ytcp-button:has-text('Tiếp')",
                    ],
                    timeout_ms=5000,
                )
                await asyncio.sleep(2.0)
                cancel_check()

                # 7. Adaptive Ad Suitability Self-Rating Questionnaire
                ad_suitability = await page.query_selector(
                    "ytcp-self-certification, [test-id='self-certification'], h2:has-text('Mức độ phù hợp để chạy quảng cáo'), h2:has-text('Ad suitability')"
                )
                if ad_suitability:
                    progress("Đang hoàn tất tự đánh giá quảng cáo...", "self_certification", 70)
                    logger.info("Phát hiện Tab Tự đánh giá quảng cáo; chọn 'Không chứa nội dung nào ở trên'...")
                    # Scroll to bottom
                    await page.evaluate("window.scrollBy(0, 1500)")
                    await asyncio.sleep(1.0)
                    # Check 'None of the above'
                    await _safe_click(
                        page,
                        [
                            "tp-yt-paper-checkbox:has-text('Không chứa nội dung nào ở trên')",
                            "tp-yt-paper-checkbox:has-text('None of the above')",
                            "#none-of-the-above-checkbox",
                            "ytcp-checkbox-lit:has-text('Không chứa')",
                        ],
                        timeout_ms=5000,
                    )
                    await asyncio.sleep(1.5)
                    # Click Submit rating button
                    await _safe_click(
                        page,
                        [
                            "ytcp-button:has-text('Gửi thông tin đánh giá')",
                            "ytcp-button:has-text('Submit rating')",
                            "ytcp-button#submit-questionnaire-button",
                        ],
                        timeout_ms=5000,
                    )
                    await asyncio.sleep(2.0)
                    # Next to Video Elements
                    await _safe_click(
                        page,
                        [
                            "ytcp-button#next-button",
                            "#next-button button",
                            "ytcp-button:has-text('Tiếp')",
                        ],
                        timeout_ms=5000,
                    )
                    await asyncio.sleep(2.0)

            # 8. Traverse Remaining Tabs (Video Elements & Checks -> Visibility)
            progress("Đang duyệt qua các bước trung gian đến tab Chế độ hiển thị...", "navigating_tabs", 75)
            for step_idx in range(6):
                cancel_check()
                # Check if Visibility tab content is VISIBLE on screen
                visibility_active = await page.query_selector(
                    "tp-yt-paper-radio-button[name='SCHEDULE']:not([hidden]), "
                    "tp-yt-paper-radio-button#schedule-radio-button:not([hidden]), "
                    "tp-yt-paper-radio-button[name='PRIVATE']:not([hidden]), "
                    "ytcp-video-visibility-select"
                )
                if visibility_active and await visibility_active.is_visible():
                    logger.info("Đã đến tab Chế độ hiển thị (Visibility) tại bước %d.", step_idx + 1)
                    break

                logger.info("Chưa đến tab Chế độ hiển thị; đang bấm 'Tiếp' (lần %d)...", step_idx + 1)
                clicked = await _safe_click(
                    page,
                    [
                        "ytcp-button#next-button",
                        "#next-button button",
                        "ytcp-button:has-text('Tiếp')",
                        "ytcp-button:has-text('Next')",
                    ],
                    timeout_ms=5000,
                )
                if not clicked:
                    logger.warning("Không thể bấm nút 'Tiếp' ở lần duyệt %d", step_idx + 1)
                await asyncio.sleep(1.5)

            # 9. Tab Visibility (Private vs Schedule)
            progress("Đang thiết lập Chế độ hiển thị & Đặt lịch...", "visibility_and_publish", 85)
            cancel_check()

            if schedule_at:
                # Convert UTC to local system timezone (ICT / UTC+7 in VN)
                try:
                    parsed_dt = dt.datetime.fromisoformat(str(schedule_at).replace("Z", "+00:00"))
                    local_dt = parsed_dt.astimezone()
                    target_date_str = _format_date_for_picker(local_dt.date())
                    target_time_str = _format_time_for_picker(local_dt.time())
                except Exception as tz_err:
                    logger.warning("Không thể chuyển đổi timezone cho schedule_at '%s': %s", schedule_at, tz_err)
                    parsed_dt = dt.datetime.now()
                    target_date_str = _format_date_for_picker(parsed_dt.date())
                    target_time_str = _format_time_for_picker(parsed_dt.time())

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
                        "tp-yt-paper-radio-button#schedule-radio-button",
                        "tp-yt-paper-radio-button[name='SCHEDULE']",
                        "tp-yt-paper-radio-button:has-text('Lên lịch')",
                        "tp-yt-paper-radio-button:has-text('Schedule')",
                        "#second-container-expand-button",
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
                            date_filled = True
                    except Exception as dp_exc:
                        logger.warning("Không điền được datepicker trigger: %s", dp_exc)

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
                            time_filled = True
                    except Exception as tp_exc:
                        logger.warning("Không điền được timepicker trigger: %s", tp_exc)

                # 4. Click Schedule Done Button
                done_clicked = await _safe_click(
                    page,
                    [
                        "ytcp-button#done-button:has-text('Lên lịch')",
                        "ytcp-button#done-button:has-text('Schedule')",
                        "ytcp-button#done-button",
                        "#done-button button",
                    ],
                    timeout_ms=8000,
                )
                if not done_clicked:
                    raise BrowserUploadError("Không thể click nút 'Lên lịch' (Done/Schedule) trên YouTube Studio.")

                await asyncio.sleep(3.0)

                # Handle Copyright Check Warning Modal if it appears (Edge Case)
                try:
                    warning_modal = await page.wait_for_selector(
                        "ytcp-button:has-text('Đã hiểu'), ytcp-button:has-text('Got it'), ytcp-confirmation-dialog #confirm-button",
                        state="visible",
                        timeout=4000,
                    )
                    if warning_modal:
                        logger.info("Phát hiện modal cảnh báo kiểm tra nội dung; click 'Đã hiểu'...")
                        await _safe_click(
                            page,
                            [
                                "ytcp-button:has-text('Đã hiểu')",
                                "ytcp-button:has-text('Got it')",
                                "ytcp-confirmation-dialog #confirm-button",
                            ],
                            timeout_ms=3000,
                        )
                        await asyncio.sleep(2.0)
                except Exception:
                    pass

            else:
                # Private mode
                logger.info("Lưu video ở chế độ Riêng tư (Private)...")
                private_clicked = await _safe_click(
                    page,
                    [
                        "tp-yt-paper-radio-button[name='PRIVATE']",
                        "tp-yt-paper-radio-button:has-text('Riêng tư')",
                        "tp-yt-paper-radio-button:has-text('Private')",
                    ],
                    timeout_ms=8000,
                )
                if not private_clicked:
                    raise BrowserUploadError("Không thể chọn radio 'Riêng tư' (Private) trên YouTube Studio.")

                await asyncio.sleep(1.0)
                done_clicked = await _safe_click(
                    page,
                    [
                        "ytcp-button#done-button:has-text('Lưu')",
                        "ytcp-button#done-button:has-text('Save')",
                        "ytcp-button#done-button",
                        "#done-button button",
                    ],
                    timeout_ms=8000,
                )
                if not done_clicked:
                    raise BrowserUploadError("Không thể click nút 'Lưu' (Save/Done) trên YouTube Studio.")

                await asyncio.sleep(3.0)

            # 10. Close Post-Publish Dialog & Extract Final Video ID if missed earlier
            progress("Đang hoàn tất và đóng hộp thoại...", "finishing_upload", 95)
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

                close_btn = await page.wait_for_selector(
                    "ytcp-button#close-button, ytcp-button:has-text('Đóng'), ytcp-button:has-text('Close'), ytcp-video-share-dialog #close-button",
                    state="visible",
                    timeout=8000,
                )
                if close_btn:
                    await close_btn.click()
                    await asyncio.sleep(2.0)
            except Exception as c_exc:
                logger.debug("Hộp thoại hoàn tất tự đóng hoặc không xuất hiện: %s", c_exc)

            if not youtube_video_id:
                # Fallback: check latest video in Studio content list URL
                try:
                    await page.goto("https://studio.youtube.com/channel/videos/upload", wait_until="domcontentloaded")
                    await asyncio.sleep(2.0)
                    first_row = await page.query_selector("ytcp-video-row a#video-title, #row-container a#thumbnail")
                    if first_row:
                        href = str(await first_row.get_attribute("href") or "")
                        match = re.search(r"/video/([a-zA-Z0-9_-]+)/edit", href)
                        if match:
                            youtube_video_id = match.group(1).strip()
                except Exception:
                    pass

            if not youtube_video_id:
                raise BrowserUploadError("Upload qua trình duyệt hoàn tất nhưng không trích xuất được YouTube Video ID.")

            progress("Upload qua trình duyệt hoàn tất thành công 100%!", "completed", 100)
            return {
                "youtube_video_id": youtube_video_id,
                "published_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
                "status": "scheduled" if schedule_at else "uploaded_private",
                "scheduled_at": str(schedule_at or ""),
                "title": title,
            }

        except Exception as exc:
            logger.error("Lỗi trong quá trình upload YouTube qua browser: %s", exc, exc_info=True)
            try:
                logs_dir = Path("data/logs")
                logs_dir.mkdir(parents=True, exist_ok=True)
                timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = logs_dir / f"yt_upload_error_{timestamp}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                logger.error("Đã chụp ảnh màn hình chẩn đoán lỗi tại: %s", screenshot_path)
            except Exception as s_exc:
                logger.warning("Không thể chụp ảnh màn hình chẩn đoán: %s", s_exc)
            raise BrowserUploadError(f"Upload qua trình duyệt thất bại: {exc}") from exc
        finally:
            try:
                await page.close()
            except Exception:
                pass
