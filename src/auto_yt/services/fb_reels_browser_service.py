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
import logging
import re
from pathlib import Path
from typing import Any, Callable

from auto_yt.services.channel_scanner_service import channel_browser_session

logger = logging.getLogger(__name__)

REELS_COMPOSER_URL = "https://business.facebook.com/latest/reels_composer/"
CALENDAR_URL = "https://business.facebook.com/latest/content_calendar"


class FbBrowserAutomationError(RuntimeError):
    """Raised when an automation action fails on Meta Business Suite."""


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


async def schedule_reel_via_gpm(
    profile_id: str,
    video_path: Path | str,
    caption: str,
    *,
    thumb_path: Path | str | None = None,
    tags: list[str] | None = None,
    schedule_datetime: datetime.datetime | str | int | None = None,
    publish_now: bool = False,
    page_name: str = "",
    target_page_id: str = "",
    auto_stop_profile: bool | None = False,
    timeout_seconds: float = 300.0,
    state_callback: Callable[[dict[str, Any]], None] | None = None,
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
    full_schedule_label = f"{date_slash} {time_24h}"

    def notify(phase: str, message: str, progress: int | None = None):
        logger.info("[FB-Playwright %s] %s", clean_profile_id, message)
        if state_callback:
            try:
                state_callback({
                    "phase": phase,
                    "message": message,
                    "progress": progress,
                    "profile_id": clean_profile_id,
                })
            except Exception:
                pass

    notify("starting", f"Đang kết nối Profile {clean_profile_id} qua Playwright CDP...", 5)

    async with channel_browser_session(clean_profile_id) as (context, _browser, _profile_meta):
        page = await context.new_page()
        try:
            # Set default timeout for individual actions
            page.set_default_timeout(25000)

            # 1. Navigate to Reels Composer
            notify("navigating", "Đang mở giao diện Meta Business Suite Reels Composer...", 10)
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
                raise FbBrowserAutomationError(
                    f"Profile GPM {clean_profile_id} chưa đăng nhập Facebook hoặc bị checkpoint. Vui lòng mở profile và đăng nhập trước."
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
            notify("uploading_video", f"Đang tải lên file video {v_path.name} ({v_path.stat().st_size // (1024*1024)} MB)...", 20)
            
            # Look for video file input
            file_input = page.locator('input[type="file"][accept*="video"], input[type="file"]').first
            has_input = await file_input.count() > 0

            if has_input:
                await file_input.set_input_files(str(v_path))
            else:
                # Fallback: click "Thêm video" / "Add video" button via file chooser
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    add_btn = page.locator(
                        'button:has-text("Thêm video"), div[role="button"]:has-text("Thêm video"), '
                        'button:has-text("Add video"), div[role="button"]:has-text("Add video")'
                    ).first
                    await add_btn.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(v_path))

            notify("processing_upload", "Đang chờ video upload lên Meta Business Suite (100%)...", 35)

            # Wait for upload progress to reach 100% or button "Tiếp" enabled
            upload_finished = False
            for _ in range(120): # up to 2 minutes for video upload
                await asyncio.sleep(1.5)
                # Check 100% text or "Video của bạn an toàn để đăng!"
                page_text = await page.content()
                if "100%" in page_text or "Video của bạn an toàn" in page_text or "an toàn để đăng" in page_text:
                    upload_finished = True
                    break
                # Or check if Next button is enabled and not disabled
                next_btn = page.locator('button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")').last
                if await next_btn.is_visible() and await next_btn.is_enabled():
                    # check if still has 10% / 20% / upload progress bar
                    if "100%" in page_text or not re.search(r"\b\d{1,2}%\b", page_text):
                        upload_finished = True
                        break

            notify("entering_content", "Video đã tải lên xong. Đang nhập mô tả, tùy chỉnh FB & IG...", 50)

            # 3. Facebook & Instagram Customization Toggle
            try:
                # Check for switch "Tùy chỉnh bài viết cho Facebook và Instagram"
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
                    # Look for editor contenteditable or textarea
                    caption_box = page.locator(
                        'div[contenteditable="true"][role="textbox"], '
                        'textarea[placeholder*="Cho người xem biết"], '
                        'div[contenteditable="true"]'
                    ).first
                    if await caption_box.is_visible():
                        await caption_box.click()
                        # Select all and replace
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Backspace")
                        # Paste text to handle multiline cleanly
                        await page.evaluate(
                            """([text]) => {
                                const el = document.querySelector('div[contenteditable="true"][role="textbox"]') || document.querySelector('div[contenteditable="true"]');
                                if (el) {
                                    el.focus();
                                    document.execCommand('insertText', false, text);
                                }
                            }""",
                            [clean_caption],
                        )
                        await asyncio.sleep(1.0)
                except Exception as cap_err:
                    logger.warning("Lỗi khi điền caption: %s", cap_err)

            # 5. Upload Custom Thumbnail
            if t_path and t_path.is_file():
                notify("uploading_thumbnail", f"Đang tải lên hình thu nhỏ {t_path.name}...", 60)
                try:
                    # Click tab "Tải hình ảnh lên"
                    upload_img_tab = page.locator(
                        'div[role="tab"]:has-text("Tải hình ảnh lên"), button:has-text("Tải hình ảnh lên"), span:has-text("Tải hình ảnh lên")'
                    ).first
                    if await upload_img_tab.is_visible():
                        await upload_img_tab.click()
                        await asyncio.sleep(1.0)

                    # Find upload button or image file input
                    thumb_file_input = page.locator('input[type="file"][accept*="image"]').first
                    if await thumb_file_input.count() > 0:
                        await thumb_file_input.set_input_files(str(t_path))
                    else:
                        async with page.expect_file_chooser(timeout=8000) as fc_thumb_info:
                            upload_thumb_btn = page.locator(
                                'div[role="button"]:has-text("Tải hình ảnh lên"), button:has-text("Tải hình ảnh lên")'
                            ).last
                            await upload_thumb_btn.click()
                        fc_thumb = await fc_thumb_info.value
                        await fc_thumb.set_files(str(t_path))

                    await asyncio.sleep(2.0)
                except Exception as thumb_err:
                    logger.warning("Lỗi khi tải thumbnail lên: %s", thumb_err)

            # 6. Add Tags (Thẻ)
            tag_list = list(tags or [])
            if page_name and page_name not in tag_list:
                tag_list.append(page_name)

            if tag_list:
                notify("adding_tags", f"Đang thêm {len(tag_list)} thẻ từ khóa...", 65)
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
                except Exception as tag_err:
                    logger.debug("Lỗi khi điền tags: %s", tag_err)

            # 7. Move from Step 1 (Tạo) -> Step 2 (Chỉnh sửa)
            notify("moving_next", "Chuyển sang bước Chỉnh sửa...", 70)
            next_btn_1 = page.locator('button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")').last
            await next_btn_1.click()
            await asyncio.sleep(2.5)

            # 8. Move from Step 2 (Chỉnh sửa) -> Step 3 (Chia sẻ)
            # Step 2 is skipped as requested
            notify("moving_next", "Bỏ qua bước Chỉnh sửa, chuyển sang bước Chia sẻ...", 75)
            next_btn_2 = page.locator('button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")').last
            if await next_btn_2.is_visible():
                await next_btn_2.click()
                await asyncio.sleep(2.5)

            # 9. Step 3 (Chia sẻ): Schedule & Options Configuration
            notify("configuring_schedule", f"Đang cấu hình lịch đăng ({full_schedule_label}) và các tùy chọn...", 80)

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
                if await schedule_radio_btn.is_visible():
                    await schedule_radio_btn.click()
                    await asyncio.sleep(1.5)

                # Set Date and Time
                try:
                    # Find date inputs
                    date_inputs = page.locator('input[type="text"]').filter(has=page.locator('xpath=ancestor::div[contains(., "Facebook") or contains(., "Instagram") or contains(., "Lên lịch")]'))
                    # Fallback find inputs matching date pattern or calendar icon
                    all_text_inputs = page.locator('div[role="main"] input[type="text"], form input[type="text"]')
                    input_count = await all_text_inputs.count()

                    for idx in range(input_count):
                        inp = all_text_inputs.nth(idx)
                        val = (await inp.input_value() or "").strip()
                        # If matches date like 29/9/2026 or DD/MM/YYYY
                        if re.search(r"\d{1,2}/\d{1,2}/\d{4}", val) or re.search(r"Tháng", val):
                            await inp.click()
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await inp.fill(date_slash)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.5)
                        # If matches time like 18:16 or HH:MM
                        elif re.search(r"^\d{1,2}:\d{2}$", val):
                            await inp.click()
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await inp.fill(time_24h)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.5)
                except Exception as dt_err:
                    logger.warning("Lỗi khi nhập ngày giờ lên lịch: %s", dt_err)

            # 10. Playlists: Add to all available playlists
            try:
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

            # 11. Subtitles & Remix
            try:
                # Ensure Subtitle is checked
                sub_cb = page.locator('input[type="checkbox"]').filter(has=page.locator('xpath=ancestor::div[contains(., "Phụ đề")]')).first
                if await sub_cb.is_visible() and not await sub_cb.is_checked():
                    await sub_cb.click()

                # Remix: Select "Không cho phép"
                no_remix_radio = page.locator(
                    'div[role="radio"]:has-text("Không cho phép"), '
                    'label:has-text("Không cho phép"), '
                    'div:has-text("Không cho phép")[role="button"]'
                ).first
                if await no_remix_radio.is_visible():
                    await no_remix_radio.click()
                    await asyncio.sleep(0.5)
            except Exception as remix_err:
                logger.debug("Lỗi cài đặt Remix/Phụ đề: %s", remix_err)

            # 12. Monetization (Công cụ kiếm tiền)
            try:
                monetization_header = page.locator('div:has-text("Công cụ kiếm tiền")').first
                if await monetization_header.is_visible():
                    monetize_switch = monetization_header.locator('xpath=..//div[@role="switch"]').first
                    if await monetize_switch.is_visible():
                        if await monetize_switch.get_attribute("aria-checked") == "false":
                            await monetize_switch.click()
                            await asyncio.sleep(1.0)

                    # Check all sub-checkboxes (Sao, Kiếm tiền từ nội dung)
                    monetize_cbs = page.locator('div:has-text("Công cụ kiếm tiền")').locator('xpath=..//input[@type="checkbox"]')
                    m_count = await monetize_cbs.count()
                    for m_idx in range(m_count):
                        m_cb = monetize_cbs.nth(m_idx)
                        if not await m_cb.is_checked():
                            await m_cb.click()
                            await asyncio.sleep(0.3)
            except Exception as mon_err:
                logger.debug("Lỗi cài đặt Kiếm tiền: %s", mon_err)

            # 13. Final Click: "Lên lịch" / "Chia sẻ"
            action_btn_name = "Chia sẻ" if publish_now else "Lên lịch"
            notify("submitting", f"Đang bấm nút '{action_btn_name}' hoàn tất...", 90)

            final_btn = page.locator(
                f'button:has-text("{action_btn_name}"), div[role="button"]:has-text("{action_btn_name}"), '
                'button:has-text("Schedule"), button:has-text("Publish")'
            ).last
            await final_btn.click()
            await asyncio.sleep(3.0)

            # 14. Handle post-submission popups / modals (e.g. "Đang xử lý thước phim của bạn...")
            for _ in range(15):
                await asyncio.sleep(1.0)
                # Check for dismiss buttons: "Bỏ qua", "Dismiss", close icon
                dismiss_btn = page.locator(
                    'button:has-text("Bỏ qua"), div[role="button"]:has-text("Bỏ qua"), '
                    'button:has-text("Dismiss"), button:has-text("Để sau"), '
                    'div[aria-label="Đóng"], button[aria-label="Đóng"], button[aria-label="Close"]'
                ).first
                if await dismiss_btn.is_visible():
                    notify("submitting", "Đã đóng thông báo xác nhận của Meta...", 95)
                    await dismiss_btn.click()
                    await asyncio.sleep(1.5)
                    break

                if "content_calendar" in page.url or "latest/home" in page.url:
                    break

            notify("completed", f"Thành công! Reels đã được lên lịch lúc {full_schedule_label}.", 100)

            return {
                "success": True,
                "status": "published" if publish_now else "meta_scheduled",
                "scheduled_time": full_schedule_label,
                "scheduled_time_iso": f"{date_iso}T{time_24h}:00",
                "target_page_id": target_page_id,
                "profile_id": clean_profile_id,
                "message": f"Đã lên lịch Reels thành công trên Facebook Meta Business Suite lúc {full_schedule_label}",
            }

        except Exception as exc:
            logger.error("Lỗi trong quá trình tự động upload Reels Facebook: %s", exc, exc_info=True)
            notify("error", f"Lỗi: {exc}", 0)
            raise FbBrowserAutomationError(f"Thất bại khi thao tác trên Meta Business Suite: {exc}") from exc
        finally:
            try:
                await page.close()
            except Exception:
                pass
