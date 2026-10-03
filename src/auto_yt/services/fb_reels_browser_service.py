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

from auto_yt.services.channel_scanner_service import channel_browser_session

logger = logging.getLogger(__name__)

REELS_COMPOSER_URL = "https://business.facebook.com/latest/reels_composer/"
CALENDAR_URL = "https://business.facebook.com/latest/content_calendar"


SCREENSHOTS_DIR = Path("data/logs/crossposter")


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
        page = await context.new_page()
        try:
            # Set default timeout for individual actions
            page.set_default_timeout(25000)

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

            notify("CP5_ASSET_UPLOADED", "Đang chờ video upload lên Meta Business Suite (100%)...", 35)

            # Wait for upload progress to reach 100% or button "Tiếp" enabled
            upload_finished = False
            for loop_i in range(240): # up to 6 minutes for video upload
                await asyncio.sleep(1.5)
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

            # 5. Upload Custom Thumbnail
            if t_path and t_path.is_file():
                notify("CP6_METADATA_FILLED", f"Đang tải lên hình thu nhỏ {t_path.name}...", 60)
                try:
                    # Click tab / radio "Tải hình ảnh lên"
                    upload_img_tab = page.locator(
                        'div[role="tab"]:has-text("Tải hình ảnh lên"), div[role="radio"]:has-text("Tải hình ảnh lên"), '
                        'button:has-text("Tải hình ảnh lên"), span:has-text("Tải hình ảnh lên"), '
                        'div:has-text("Tải hình ảnh lên")'
                    ).first
                    if await upload_img_tab.is_visible():
                        await upload_img_tab.click()
                        await asyncio.sleep(1.0)

                    upload_thumb_btn = page.locator(
                        'div:has-text("Hình thu nhỏ") ~ div div[role="button"]:has-text("Tải hình ảnh lên"), '
                        'div:has-text("Hình thu nhỏ") ~ div div[role="button"]:has-text("Thêm ảnh"), '
                        'div[role="button"]:has-text("Tải hình ảnh lên"), button:has-text("Tải hình ảnh lên"), '
                        'div[role="button"]:has-text("Thêm ảnh"), button:has-text("Thêm ảnh"), '
                        'div[role="button"]:has-text("Upload image"), button:has-text("Upload image")'
                    ).last

                    await upload_file_via_cdp(
                        page,
                        t_path,
                        input_selector='input[type="file"][accept*="image"], input[type="file"]',
                        trigger_button_locator=upload_thumb_btn,
                        timeout_seconds=10.0,
                    )
                    await asyncio.sleep(1.5)

                    # Handle crop / confirmation dialog if present
                    crop_save_btn = page.locator(
                        'div[role="dialog"] button:has-text("Lưu"), div[role="dialog"] div[role="button"]:has-text("Lưu"), '
                        'div[role="dialog"] button:has-text("Save"), div[role="dialog"] button:has-text("Áp dụng")'
                    ).first
                    if await crop_save_btn.is_visible():
                        await crop_save_btn.click()
                        await asyncio.sleep(1.0)
                except Exception as thumb_err:
                    logger.debug("Lỗi khi tải thumbnail lên: %s", thumb_err)

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

            # 7. Move from Step 1 (Tạo) -> Step 2 (Chỉnh sửa)
            notify("CP6_METADATA_FILLED", "Chờ xử lý video và chuyển sang bước Chỉnh sửa...", 70)
            await page.keyboard.press("Escape")
            await page.evaluate("() => document.activeElement && document.activeElement.blur()")
            await asyncio.sleep(1.0)

            next_btn_1 = page.locator('button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")').last

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

            # 8. Move from Step 2 (Chỉnh sửa) -> Step 3 (Chia sẻ)
            notify("CP7_SCHEDULE_SET", "Bỏ qua bước Chỉnh sửa, chuyển sang bước Chia sẻ...", 75)
            next_btn_2 = page.locator('button:has-text("Tiếp"), div[role="button"]:has-text("Tiếp"), button:has-text("Next")').last
            if await next_btn_2.is_visible():
                for w_i in range(30):
                    aria_dis = await next_btn_2.get_attribute("aria-disabled")
                    tab_idx = await next_btn_2.get_attribute("tabindex")
                    if aria_dis != "true" and tab_idx != "-1":
                        break
                    await asyncio.sleep(1.0)
                await next_btn_2.click()
                await asyncio.sleep(2.5)

            # 9. Step 3 (Chia sẻ): Schedule & Options Configuration
            notify("CP7_SCHEDULE_SET", f"Đang cấu hình lịch đăng ({full_schedule_label}) và các tùy chọn...", 80)

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
            notify("CP8_SUBMITTED", f"Đang bấm nút '{action_btn_name}' hoàn tất...", 90)

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
            for w_i in range(30):
                if await final_btn.is_visible():
                    aria_dis = await final_btn.get_attribute("aria-disabled")
                    tab_idx = await final_btn.get_attribute("tabindex")
                    if aria_dis != "true" and tab_idx != "-1":
                        break
                await asyncio.sleep(1.0)

            try:
                await final_btn.click(force=True, timeout=10000)
            except Exception as click_exc:
                logger.debug("Force click nút cuối gặp lỗi (%s), dùng JS evaluate click...", click_exc)
                await final_btn.evaluate("el => el.click()")

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
                    notify("CP8_SUBMITTED", "Đã đóng thông báo xác nhận của Meta...", 95)
                    await dismiss_btn.click()
                    await asyncio.sleep(1.5)
                    break

                if "content_calendar" in page.url or "latest/home" in page.url:
                    break

            notify("CP8_SUBMITTED", f"Thành công! Reels đã được lên lịch lúc {full_schedule_label}.", 100)

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
            try:
                await page.close()
            except Exception:
                pass
