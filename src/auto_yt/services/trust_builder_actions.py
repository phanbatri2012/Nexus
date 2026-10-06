"""Playwright CDP Browser Actions for YouTube Channel Trust Building (Warm-up).

Implements human-like interactions including natural search, variable watch duration,
social engagements (like, comment, subscribe), and channel branding auditing.
All actions execute strictly inside an authenticated GPM-Login profile session via CDP.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)


async def human_type(page: Any, selector: str, text: str, min_delay_ms: int = 40, max_delay_ms: int = 120) -> None:
    """Type text into an element character by character with randomized human delay."""
    element = await page.wait_for_selector(selector, state="visible", timeout=10000)
    if not element:
        raise ValueError(f"Không tìm thấy phần tử {selector} để nhập văn bản.")
    
    await element.click()
    await asyncio.sleep(random.uniform(0.2, 0.5))

    for char in text:
        await element.type(char, delay=random.randint(min_delay_ms, max_delay_ms))
        if random.random() < 0.08:  # 8% chance to pause slightly while thinking
            await asyncio.sleep(random.uniform(0.15, 0.4))


async def natural_scroll(page: Any, min_scrolls: int = 2, max_scrolls: int = 4) -> None:
    """Perform smooth, human-like page scrolls with variable distance and pauses."""
    scroll_count = random.randint(min_scrolls, max_scrolls)
    for _ in range(scroll_count):
        scroll_delta = random.randint(250, 650)
        await page.mouse.wheel(0, scroll_delta)
        await asyncio.sleep(random.uniform(0.8, 2.2))


async def dismiss_common_popups(page: Any) -> None:
    """Dismiss common YouTube consent, premium, or sign-in reminder dialogs if present."""
    dismiss_selectors = [
        "ytd-button-renderer#dismiss-button button",
        "tp-yt-paper-button#dismiss-button",
        "button[aria-label='Dismiss']",
        "button[aria-label='Bỏ qua']",
        "button[aria-label='Accept all']",
        "button[aria-label='I agree']",
        "button[aria-label='Tôi đồng ý']",
    ]
    for sel in dismiss_selectors:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                await elem.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass


async def handle_ad_skipping(page: Any) -> bool:
    """Check for skip ad buttons and click if available."""
    skip_selectors = [
        ".ytp-ad-skip-button",
        ".ytp-ad-skip-button-modern",
        ".ytp-skip-ad-button",
        "button.ytp-ad-skip-button-text",
    ]
    for sel in skip_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                logger.info("Đã bấm Bỏ qua quảng cáo (Skip Ad).")
                return True
        except Exception:
            pass
    return False


async def action_search_and_pick_video(
    page: Any,
    keyword: str,
    target_channel: str = "",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Search YouTube for a niche keyword, scroll naturally, and pick a competitor video."""
    logger.info("Thực hiện tìm kiếm từ khóa ngách: '%s' (Kênh mục tiêu: '%s')", keyword, target_channel)
    
    # 1. Navigate to YouTube homepage
    await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
    await asyncio.sleep(random.uniform(1.5, 3.0))
    await dismiss_common_popups(page)

    # 2. Find and click search input
    search_input_sel = "input#search, input[name='search_query']"
    search_elem = await page.wait_for_selector(search_input_sel, state="visible", timeout=12000)
    if not search_elem:
        raise RuntimeError("Không tìm thấy ô tìm kiếm của YouTube.")

    # 3. Type keyword and submit
    await search_elem.click()
    await asyncio.sleep(random.uniform(0.3, 0.7))
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    
    # Human-like typing
    for char in keyword:
        await search_elem.type(char, delay=random.randint(45, 115))
    
    await asyncio.sleep(random.uniform(0.4, 0.9))
    await page.keyboard.press("Enter")
    
    # 4. Wait for search results
    await asyncio.sleep(random.uniform(2.5, 4.5))
    await page.wait_for_selector("ytd-video-renderer, ytd-rich-item-renderer, ytd-item-section-renderer", timeout=15000)
    
    # 5. Natural scrolling down search results
    await natural_scroll(page, min_scrolls=2, max_scrolls=4)
    await asyncio.sleep(random.uniform(1.0, 2.0))

    # 6. Extract candidate video links
    candidates: list[dict[str, str]] = await page.evaluate(
        """() => {
            const results = [];
            const videoNodes = document.querySelectorAll('ytd-video-renderer, ytd-rich-item-renderer');
            for (const node of videoNodes) {
                const titleLink = node.querySelector('a#video-title, a#thumbnail');
                const channelNode = node.querySelector('ytd-channel-name a, #channel-info a');
                if (titleLink && titleLink.href && titleLink.href.includes('/watch?v=')) {
                    results.push({
                        url: titleLink.href,
                        title: (titleLink.innerText || titleLink.getAttribute('title') || '').trim(),
                        channel: channelNode ? (channelNode.innerText || '').trim() : ''
                    });
                }
            }
            return results;
        }"""
    )

    if not candidates:
        raise RuntimeError(f"Không tìm thấy video kết quả nào cho từ khóa '{keyword}'.")

    # 7. Select video: filter by target_channel if specified, else pick random from Top 10
    selected_video: dict[str, str] | None = None
    if target_channel:
        clean_target = target_channel.lower().replace("@", "").strip()
        for cand in candidates:
            if clean_target in cand["channel"].lower():
                selected_video = cand
                break

    if not selected_video:
        # Pick from top min(8, len(candidates))
        pool = candidates[:min(8, len(candidates))]
        selected_video = random.choice(pool)

    logger.info("Đã chọn video đối thủ: '%s' (%s) từ kênh '%s'", selected_video["title"], selected_video["url"], selected_video["channel"])
    return selected_video


async def action_watch_video(
    page: Any,
    video_url: str,
    min_pct: float = 40.0,
    max_pct: float = 80.0,
    max_watch_seconds: float = 240.0,
    min_watch_seconds: float = 30.0,
) -> dict[str, Any]:
    """Watch video naturally with random pauses, seeks, and ad-skipping."""
    logger.info("Bắt đầu xem video: %s", video_url)
    
    current_url = page.url
    if not (video_url and video_url in current_url):
        await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2.0, 3.5))

    await dismiss_common_popups(page)

    # Ensure video is playing
    await page.evaluate(
        """() => {
            const v = document.querySelector('video');
            if (v && v.paused) {
                v.play().catch(() => {});
            }
        }"""
    )

    # Get total video duration
    total_duration = 0.0
    for _ in range(6):
        total_duration = float(await page.evaluate("() => document.querySelector('video')?.duration || 0") or 0.0)
        if total_duration > 0:
            break
        await asyncio.sleep(1.0)

    if total_duration <= 0:
        total_duration = 300.0  # Fallback 5 minutes

    # Calculate target watch duration
    chosen_pct = random.uniform(min_pct, max_pct) / 100.0
    calc_seconds = total_duration * chosen_pct
    target_seconds = min(max_watch_seconds, max(min_watch_seconds, calc_seconds))

    logger.info(
        "Tổng thời lượng: %.1fs | Mục tiêu xem: %.1fs (%.1f%%)",
        total_duration, target_seconds, chosen_pct * 100
    )

    # Watch loop
    elapsed = 0.0
    step = 5.0
    has_paused = False
    
    while elapsed < target_seconds:
        await asyncio.sleep(step)
        elapsed += step
        
        # Check and handle ads
        await handle_ad_skipping(page)

        # Random human pause (once per watch session, around 40-60% of elapsed time)
        if not has_paused and elapsed > (target_seconds * 0.4) and random.random() < 0.25:
            has_paused = True
            pause_time = random.uniform(3.0, 7.0)
            logger.debug("Giả lập tạm dừng video trong %.1fs...", pause_time)
            await page.evaluate("() => document.querySelector('video')?.pause()")
            # Slight scroll down to mimic reading comments
            await page.mouse.wheel(0, random.randint(150, 350))
            await asyncio.sleep(pause_time)
            # Scroll back up and resume
            await page.mouse.wheel(0, -random.randint(150, 350))
            await page.evaluate("() => document.querySelector('video')?.play().catch(() => {})")

    logger.info("Hoàn tất phiên xem video: %.1fs", elapsed)
    return {
        "watched_seconds": elapsed,
        "total_duration": total_duration,
        "retention_percentage": round((elapsed / total_duration) * 100, 2) if total_duration > 0 else 50.0,
    }


async def action_like_video(page: Any) -> bool:
    """Click the Like button if not already liked."""
    logger.info("Thực hiện hành động Like video...")
    try:
        # Check like button state
        like_btn_info = await page.evaluate(
            """() => {
                const likeBtn = document.querySelector(
                    'like-button-view-model button, #top-level-buttons-computed ytd-toggle-button-renderer button, ytd-watch-metadata #segmented-like-button button'
                );
                if (!likeBtn) return { found: false, pressed: false };
                const pressed = likeBtn.getAttribute('aria-pressed') === 'true';
                return { found: true, pressed };
            }"""
        )

        if not like_btn_info.get("found"):
            logger.warning("Không tìm thấy nút Like trên giao diện video.")
            return False

        if like_btn_info.get("pressed"):
            logger.info("Video đã được Like trước đó. Bỏ qua.")
            return True

        # Click like
        like_btn_sel = "like-button-view-model button, ytd-watch-metadata #segmented-like-button button"
        btn = await page.query_selector(like_btn_sel)
        if btn:
            await btn.click()
            await asyncio.sleep(random.uniform(1.2, 2.5))
            logger.info("Đã Like video thành công!")
            return True
        return False
    except Exception as exc:
        logger.warning("Lỗi khi click Like video: %s", exc)
        return False


async def action_comment_video(page: Any, comment_text: str) -> bool:
    """Add a contextual comment to the current video."""
    clean_text = comment_text.strip()
    if not clean_text:
        return False

    logger.info("Thực hiện bình luận video: '%s'", clean_text)
    try:
        # Scroll to comments section
        await natural_scroll(page, min_scrolls=2, max_scrolls=3)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # Focus comment box
        placeholder_sel = "#placeholder-area, #simplebox-placeholder"
        placeholder = await page.wait_for_selector(placeholder_sel, state="visible", timeout=12000)
        if not placeholder:
            logger.warning("Không tìm thấy khung nhập bình luận.")
            return False

        await placeholder.click()
        await asyncio.sleep(random.uniform(0.6, 1.2))

        # Type comment
        input_sel = "#contenteditable-root, ytd-commentbox #contenteditable-root"
        input_elem = await page.wait_for_selector(input_sel, state="visible", timeout=8000)
        if not input_elem:
            logger.warning("Không tìm thấy ô soạn thảo bình luận.")
            return False

        for char in clean_text:
            await input_elem.type(char, delay=random.randint(40, 110))
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.1, 0.3))

        await asyncio.sleep(random.uniform(1.0, 2.0))

        # Submit comment
        submit_btn_sel = "#submit-button button, ytd-commentbox #submit-button button"
        submit_btn = await page.query_selector(submit_btn_sel)
        if submit_btn and await submit_btn.is_enabled():
            await submit_btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            logger.info("Đã gửi bình luận thành công!")
            return True

        logger.warning("Nút Gửi bình luận không kích hoạt.")
        return False
    except Exception as exc:
        logger.warning("Lỗi khi đăng bình luận: %s", exc)
        return False


async def action_subscribe_channel(page: Any) -> bool:
    """Subscribe to the channel of the current video if not subscribed."""
    logger.info("Thực hiện hành động Đăng ký kênh (Subscribe)...")
    try:
        sub_info = await page.evaluate(
            """() => {
                const subBtn = document.querySelector(
                    'ytd-watch-metadata #subscribe-button button, ytd-subscribe-button-renderer button'
                );
                if (!subBtn) return { found: false, subscribed: false };
                const text = (subBtn.innerText || subBtn.getAttribute('aria-label') || '').toLowerCase();
                const subscribed = text.includes('subscribed') || text.includes('đã đăng ký');
                return { found: true, subscribed, text };
            }"""
        )

        if not sub_info.get("found"):
            logger.warning("Không tìm thấy nút Subscribe trên trang.")
            return False

        if sub_info.get("subscribed"):
            logger.info("Kênh đã được đăng ký trước đó. Bỏ qua.")
            return True

        btn = await page.query_selector("ytd-watch-metadata #subscribe-button button, ytd-subscribe-button-renderer button")
        if btn:
            await btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            logger.info("Đã bấm Subscribe kênh đối thủ thành công!")
            return True
        return False
    except Exception as exc:
        logger.warning("Lỗi khi bấm Subscribe: %s", exc)
        return False


async def action_audit_channel_branding(page: Any, timeout_seconds: float = 25.0) -> dict[str, Any]:
    """Audit channel profile completeness (Avatar, Banner, Handle, About/Description)."""
    logger.info("Thực hiện Audit Branding kênh trên YouTube Studio...")
    
    checklist = {
        "avatar": False,
        "banner": False,
        "about": False,
        "handle": False,
        "contact_email": False,
        "country": False,
        "feature_level": "standard",  # 'standard' | 'intermediate' | 'advanced'
    }

    try:
        # Navigate to Studio customization page
        await page.goto("https://studio.youtube.com/channel/customization", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        await asyncio.sleep(random.uniform(2.5, 4.0))

        # Check if login required
        if "accounts.google.com" in page.url:
            return checklist

        # Check Branding elements (Avatar, Banner)
        branding_info = await page.evaluate(
            """() => {
                const avatarImg = document.querySelector('#avatar-img, img.channel-avatar, ytcp-img-with-fallback img');
                const hasAvatar = Boolean(avatarImg && avatarImg.src && !avatarImg.src.includes('default_user'));
                const bannerImg = document.querySelector('#banner-image, img.channel-banner');
                const hasBanner = Boolean(bannerImg && bannerImg.src);
                return { hasAvatar, hasBanner };
            }"""
        )
        checklist["avatar"] = bool(branding_info.get("hasAvatar"))
        checklist["banner"] = bool(branding_info.get("hasBanner"))

        # Navigate to Basic info tab
        await page.goto("https://studio.youtube.com/channel/customization/info", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        await asyncio.sleep(random.uniform(2.0, 3.5))

        info_data = await page.evaluate(
            """() => {
                const handleInput = document.querySelector('#handle-input input, input[name="handle"]');
                const descArea = document.querySelector('#description-textarea textarea, textarea[name="description"]');
                const emailInput = document.querySelector('#contact-email-input input');
                
                const hasHandle = Boolean(handleInput && handleInput.value && handleInput.value.length > 2);
                const hasDesc = Boolean(descArea && descArea.value && descArea.value.length > 30);
                const hasEmail = Boolean(emailInput && emailInput.value && emailInput.value.includes('@'));
                
                return { hasHandle, hasDesc, hasEmail };
            }"""
        )
        checklist["handle"] = bool(info_data.get("hasHandle"))
        checklist["about"] = bool(info_data.get("hasDesc"))
        checklist["contact_email"] = bool(info_data.get("hasEmail"))

        # Assume standard / intermediate check
        checklist["country"] = True
        checklist["feature_level"] = "intermediate"

    except Exception as exc:
        logger.warning("Lỗi trong quá trình Audit Branding kênh: %s", exc)

    return checklist
