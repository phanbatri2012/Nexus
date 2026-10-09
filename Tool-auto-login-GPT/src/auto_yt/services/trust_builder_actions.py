"""Playwright CDP Browser Actions for YouTube Channel Trust Building (Warm-up).

Implements human-like interactions including natural search, variable watch duration,
social engagements (like, comment, subscribe), and channel branding auditing.
All actions execute strictly inside an authenticated GPM-Login profile session via CDP.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import unicodedata
import urllib.parse
from collections.abc import Callable
from typing import Any

from auto_yt.services import database as db, security_logging, trust_builder_safety as safety

logger = logging.getLogger(__name__)

ACTION_PERFORMED = "performed"
ACTION_ALREADY_DONE = "already_done"
ACTION_NOT_FOUND = "not_found"
ACTION_FAILED = "failed"


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
    excluded_video_ids: set[str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Search YouTube for a niche keyword, scroll naturally, and pick a competitor video."""
    logger.info("Thực hiện tìm kiếm từ khóa ngách: '%s' (Kênh mục tiêu: '%s')", keyword, target_channel)
    
    # 1. Navigate to YouTube homepage
    await page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
    await asyncio.sleep(random.uniform(1.5, 3.0))
    await dismiss_common_popups(page)

    # 2. Find and click search input or fallback to direct search URL
    search_input_sel = "input#search, input[name='search_query'], ytd-searchbox input#search, form#search-form input"
    search_elem = None
    try:
        search_elem = await page.wait_for_selector(search_input_sel, state="attached", timeout=8000)
    except Exception:
        search_elem = None

    if search_elem:
        try:
            await search_elem.click()
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            
            # Human-like typing
            for char in keyword:
                await search_elem.type(char, delay=random.randint(45, 115))
            
            await asyncio.sleep(random.uniform(0.4, 0.9))
            await page.keyboard.press("Enter")
        except Exception as exc:
            logger.debug(
                "Không thể gõ vào ô tìm kiếm, chuyển hướng sang URL kết quả: %s",
                security_logging.redact_sensitive(exc),
            )
            search_elem = None

    if not search_elem:
        encoded_kw = urllib.parse.quote_plus(keyword)
        await page.goto(f"https://www.youtube.com/results?search_query={encoded_kw}", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))

    # 4. Wait for search results
    await asyncio.sleep(random.uniform(2.0, 3.5))
    try:
        await page.wait_for_function(
            """() => {
                const links = document.querySelectorAll('ytd-video-renderer a#video-title, ytd-rich-item-renderer a#video-title, a#thumbnail[href*="/watch?v="], a[href*="/watch?v="]');
                return links.length > 0;
            }""",
            timeout=15000,
        )
    except Exception:
        try:
            await page.wait_for_selector(
                "ytd-video-renderer, ytd-rich-item-renderer, ytd-item-section-renderer, a[href*='/watch?v=']",
                state="attached",
                timeout=8000,
            )
        except Exception:
            pass
    
    # 5. Natural scrolling down search results
    await natural_scroll(page, min_scrolls=2, max_scrolls=4)
    await asyncio.sleep(random.uniform(1.0, 2.0))

    # 6. Extract candidate video links
    candidates: list[dict[str, str]] = await page.evaluate(
        """() => {
            const results = [];
            const seen = new Set();
            const videoNodes = document.querySelectorAll(
                'ytd-video-renderer, ytd-rich-item-renderer, ytd-item-section-renderer, ytd-grid-video-renderer, #contents ytd-video-renderer'
            );
            for (const node of videoNodes) {
                const titleElem = node.querySelector('a#video-title, a#video-title-link, h3 a, #video-title, .ytd-video-renderer h3 a');
                const thumbElem = node.querySelector('a#thumbnail[href*="/watch?v="], a[href*="/watch?v="]');
                const channelNode = node.querySelector('ytd-channel-name a, #channel-info a, #byline a, #text.ytd-channel-name, .ytd-channel-name, a[href*="/@"], a[href*="/channel/"]');
                
                let rawUrl = (titleElem && titleElem.href) || (thumbElem && thumbElem.href) || '';
                if (rawUrl && rawUrl.includes('/watch?v=')) {
                    if (!seen.has(rawUrl)) {
                        seen.add(rawUrl);
                        let titleText = '';
                        if (titleElem) {
                            titleText = (titleElem.getAttribute('title') || titleElem.innerText || titleElem.getAttribute('aria-label') || '').trim();
                        }
                        if (!titleText || /^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(titleText)) {
                            if (thumbElem) {
                                const thumbAria = thumbElem.getAttribute('aria-label') || '';
                                if (thumbAria) {
                                    titleText = thumbAria.replace(/\\s+bởi\\s+.*$/i, '').replace(/\\s+by\\s+.*$/i, '').replace(/\\s+\\d+(\\.\\d+)?\\s*(triệu|nghìn|lượt xem|views|view).*$/i, '').trim();
                                }
                            }
                        }
                        const channelText = channelNode ? (channelNode.innerText || channelNode.getAttribute('title') || '').trim() : '';
                        const channelHref = (channelNode && channelNode.href) ? channelNode.href : '';
                        if (titleText && !/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(titleText)) {
                            results.push({
                                url: rawUrl,
                                title: titleText,
                                channel: channelText,
                                channel_href: channelHref
                            });
                        }
                    }
                }
            }
            if (results.length === 0) {
                const allLinks = document.querySelectorAll('a#video-title, a[href*="/watch?v="]');
                for (const link of allLinks) {
                    if (link.href && link.href.includes('/watch?v=') && !seen.has(link.href)) {
                        seen.add(link.href);
                        const title = (link.getAttribute('title') || link.innerText || link.getAttribute('aria-label') || '').trim();
                        if (title && title.length > 5 && !/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(title)) {
                            results.push({
                                url: link.href,
                                title: title,
                                channel: '',
                                channel_href: ''
                            });
                        }
                    }
                }
            }
            return results;
        }"""
    )

    if not candidates:
        encoded_kw = urllib.parse.quote_plus(keyword)
        logger.info("Không tìm thấy kết quả từ DOM hiện tại, thử load trực tiếp search_query: %s", encoded_kw)
        await page.goto(f"https://www.youtube.com/results?search_query={encoded_kw}", wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(random.uniform(2.5, 4.0))
        await natural_scroll(page, min_scrolls=2, max_scrolls=3)
        candidates = await page.evaluate(
            """() => {
                const list = [];
                const links = document.querySelectorAll('a#video-title, a[href*="/watch?v="]');
                const seen = new Set();
                for (const a of links) {
                    if (a.href && !seen.has(a.href)) {
                        seen.add(a.href);
                        const title = (a.getAttribute('title') || a.innerText || a.getAttribute('aria-label') || '').trim();
                        if (title && title.length > 5 && !/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(title)) {
                            list.push({ url: a.href, title, channel: '', channel_href: '' });
                        }
                    }
                }
                return list;
            }"""
        )

    if not candidates:
        raise RuntimeError(f"Không tìm thấy video kết quả nào cho từ khóa '{keyword}'.")

    # Safety Shield Gatekeeper: Filter out subversive/hostile content
    safe_candidates = []
    for cand in candidates:
        is_safe, blocked_reason = safety.is_safe_for_interaction(
            title=cand.get("title", ""),
            channel_name=cand.get("channel", ""),
            channel_url=cand.get("channel_href", ""),
            channel_handle=cand.get("channel", ""),
        )
        if is_safe:
            safe_candidates.append(cand)
        else:
            logger.warning("SAFETY SHIELD: Bỏ qua video '%s' (%s) do vi phạm: %s", cand.get("title"), cand.get("channel"), blocked_reason)
    candidates = safe_candidates
    if not candidates:
        raise RuntimeError(f"Tất cả video cho từ khóa '{keyword}' đã bị chặn bởi lá chắn an toàn quốc gia.")

    excluded_ids = excluded_video_ids or set()
    if excluded_ids:
        candidates = [
            candidate
            for candidate in candidates
            if not (
                (match := re.search(r"[?&]v=([a-zA-Z0-9_-]+)", candidate["url"]))
                and match.group(1) in excluded_ids
            )
        ]
        if not candidates:
            raise RuntimeError("Không còn video mới phù hợp sau khi loại các video đã xem.")

    def _normalize_channel_key(text: str) -> str:
        unquoted = urllib.parse.unquote(str(text or ""))
        return re.sub(r"[\s\W_]+", "", unquoted).casefold()

    def _strip_diacritics(text: str) -> str:
        nfd = unicodedata.normalize("NFD", text)
        stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
        return stripped.replace("đ", "d").replace("Đ", "d")

    def _matches_target_channel(cand: dict[str, str], target: str) -> bool:
        if not target:
            return False
        norm_target = _normalize_channel_key(target)
        if not norm_target:
            return False
        unaccent_target = _strip_diacritics(norm_target)

        c_name = str(cand.get("channel") or "")
        c_href = str(cand.get("channel_href") or "")

        norm_name = _normalize_channel_key(c_name)
        unaccent_name = _strip_diacritics(norm_name)

        norm_href = _normalize_channel_key(c_href)
        unaccent_href = _strip_diacritics(norm_href)

        # 1. Exact normalized match on name or href
        if norm_target == norm_name or norm_target in norm_href:
            return True
        # 2. Substring matching on normalized strings
        if (len(norm_target) >= 3 and norm_target in norm_name) or (len(norm_name) >= 3 and norm_name in norm_target):
            return True
        # 3. Unaccented matching (for Vietnamese and multilingual handles)
        if unaccent_target == unaccent_name or unaccent_target in unaccent_href:
            return True
        if (len(unaccent_target) >= 3 and unaccent_target in unaccent_name) or (len(unaccent_name) >= 3 and unaccent_name in unaccent_target):
            return True
        return False

    # 7. Select video: if target_channel is set, find match or perform targeted search
    selected_video: dict[str, str] | None = None
    if target_channel:
        for cand in candidates:
            if _matches_target_channel(cand, target_channel):
                selected_video = cand
                break

        page_closed = False
        try:
            closed_fn = getattr(page, "is_closed", None)
            if callable(closed_fn):
                res = closed_fn()
                if inspect.isawaitable(res):
                    page_closed = bool(await res)
                else:
                    page_closed = bool(res)
        except Exception:
            page_closed = False

        if not selected_video and not page_closed:
            # Secondary targeted search directly for target channel
            logger.info("Chưa thấy video của kênh mục tiêu '%s' trong kết quả từ khóa chung, thực hiện tìm kiếm trực tiếp kênh...", target_channel)
            encoded_target = urllib.parse.quote_plus(target_channel)
            try:
                await page.goto(f"https://www.youtube.com/results?search_query={encoded_target}", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
                await asyncio.sleep(random.uniform(2.0, 3.5))
                await natural_scroll(page, min_scrolls=2, max_scrolls=3)
                targeted_candidates = await page.evaluate(
                    """() => {
                        const list = [];
                        const seen = new Set();
                        const videoNodes = document.querySelectorAll(
                            'ytd-video-renderer, ytd-rich-item-renderer, ytd-item-section-renderer, ytd-grid-video-renderer, #contents ytd-video-renderer'
                        );
                        for (const node of videoNodes) {
                            const titleElem = node.querySelector('a#video-title, a#video-title-link, h3 a, #video-title, .ytd-video-renderer h3 a');
                            const thumbElem = node.querySelector('a#thumbnail[href*="/watch?v="], a[href*="/watch?v="]');
                            const channelNode = node.querySelector('ytd-channel-name a, #channel-info a, #byline a, #text.ytd-channel-name, .ytd-channel-name, a[href*="/@"], a[href*="/channel/"]');
                            let rawUrl = (titleElem && titleElem.href) || (thumbElem && thumbElem.href) || '';
                            if (rawUrl && rawUrl.includes('/watch?v=')) {
                                if (!seen.has(rawUrl)) {
                                    seen.add(rawUrl);
                                    let titleText = '';
                                    if (titleElem) {
                                        titleText = (titleElem.getAttribute('title') || titleElem.innerText || titleElem.getAttribute('aria-label') || '').trim();
                                    }
                                    if (!titleText || /^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(titleText)) {
                                        if (thumbElem) {
                                            const thumbAria = thumbElem.getAttribute('aria-label') || '';
                                            if (thumbAria) {
                                                titleText = thumbAria.replace(/\\s+bởi\\s+.*$/i, '').replace(/\\s+by\\s+.*$/i, '').replace(/\\s+\\d+(\\.\\d+)?\\s*(triệu|nghìn|lượt xem|views|view).*$/i, '').trim();
                                            }
                                        }
                                    }
                                    const channelText = channelNode ? (channelNode.innerText || channelNode.getAttribute('title') || '').trim() : '';
                                    const channelHref = (channelNode && channelNode.href) ? channelNode.href : '';
                                    if (titleText && !/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(titleText)) {
                                        list.push({
                                            url: rawUrl,
                                            title: titleText,
                                            channel: channelText,
                                            channel_href: channelHref
                                        });
                                    }
                                }
                            }
                        }
                        return list;
                    }"""
                )
                if targeted_candidates:
                    # Safety Shield filter on targeted candidates
                    safe_targeted = []
                    for c in targeted_candidates:
                        is_safe, blocked_reason = safety.is_safe_for_interaction(
                            title=c.get("title", ""),
                            channel_name=c.get("channel", ""),
                            channel_url=c.get("channel_href", ""),
                            channel_handle=c.get("channel", ""),
                        )
                        if is_safe:
                            safe_targeted.append(c)
                        else:
                            logger.warning("SAFETY SHIELD: Bỏ qua video kênh mục tiêu '%s' do vi phạm: %s", c.get("title"), blocked_reason)
                    targeted_candidates = safe_targeted

                    if excluded_ids:
                        targeted_candidates = [
                            c for c in targeted_candidates
                            if not (
                                (match := re.search(r"[?&]v=([a-zA-Z0-9_-]+)", c["url"]))
                                and match.group(1) in excluded_ids
                            )
                        ]
                    for cand in targeted_candidates:
                        if _matches_target_channel(cand, target_channel):
                            selected_video = cand
                            break
                    if not selected_video and targeted_candidates:
                        selected_video = targeted_candidates[0]
            except Exception as search_err:
                logger.debug("Lỗi khi tìm kiếm trực tiếp kênh mục tiêu: %s", search_err)

        if not selected_video:
            raise RuntimeError(
                f"Không tìm thấy video thuộc đúng kênh mục tiêu '{target_channel}'."
            )
    else:
        # Pick from top min(8, len(candidates))
        pool = candidates[:min(8, len(candidates))]
        selected_video = random.choice(pool)

    # Final Gatekeeper verification on selected video
    is_safe_final, final_reason = safety.is_safe_for_interaction(
        title=selected_video.get("title", ""),
        channel_name=selected_video.get("channel", ""),
        channel_url=selected_video.get("channel_href", ""),
        channel_handle=selected_video.get("channel", ""),
    )
    if not is_safe_final:
        raise RuntimeError(f"Video được chọn vi phạm lá chắn an toàn quốc gia ({final_reason}).")

    logger.info("Đã chọn video đối thủ an toàn: '%s' (%s) từ kênh '%s'", selected_video["title"], selected_video["url"], selected_video["channel"])
    return selected_video


async def action_watch_video(
    page: Any,
    video_url: str,
    min_pct: float = 60.0,
    max_pct: float = 90.0,
    min_watch_seconds: float = 600.0,
    max_watch_seconds: float = 1200.0,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Watch a video and count only observed main-video playback progress."""
    logger.info("Bắt đầu xem video: %s", video_url)

    current_url = page.url
    if not (video_url and video_url in current_url):
        await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2.0, 3.5))

    await dismiss_common_popups(page)
    try:
        await page.wait_for_selector("video.html5-main-video, video", state="attached", timeout=15000)
    except Exception as exc:
        raise RuntimeError("Không tìm thấy trình phát video YouTube.") from exc

    expected_video_id = ""
    match = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", video_url)
    if match:
        expected_video_id = match.group(1)

    snapshot_script = """() => {
        const video = document.querySelector('video.html5-main-video, video');
        const params = new URL(window.location.href).searchParams;
        return {
            duration: Number(video?.duration || 0),
            currentTime: Number(video?.currentTime || 0),
            paused: Boolean(video?.paused),
            ended: Boolean(video?.ended),
            readyState: Number(video?.readyState || 0),
            adShowing: Boolean(document.querySelector('.ad-showing, .ytp-ad-player-overlay')),
            videoId: params.get('v') || ''
        };
    }"""

    await page.evaluate(
        """() => {
            const v = document.querySelector('video.html5-main-video, video');
            if (v) {
                if (v.paused) v.play().catch(() => {});
            }
        }"""
    )

    initial_snapshot: dict[str, Any] = {}
    for _ in range(15):
        if cancel_check and cancel_check():
            raise asyncio.CancelledError
        initial_snapshot = await page.evaluate(snapshot_script) or {}
        if initial_snapshot.get("adShowing"):
            await handle_ad_skipping(page)
        elif float(initial_snapshot.get("duration") or 0.0) > 0:
            break
        await asyncio.sleep(1.0)

    total_duration = float(initial_snapshot.get("duration") or 0.0)
    if total_duration <= 0:
        raise RuntimeError("Không đọc được thời lượng video chính.")
    if expected_video_id and initial_snapshot.get("videoId") not in {"", expected_video_id}:
        raise RuntimeError("YouTube đã điều hướng sang video khác với video được chọn.")

    if total_duration <= min_watch_seconds:
        target_seconds = total_duration
        chosen_pct = 1.0
    else:
        chosen_pct = random.uniform(min_pct, max_pct) / 100.0
        calc_seconds = total_duration * chosen_pct
        effective_max = max(min_watch_seconds, max_watch_seconds)
        target_seconds = max(min_watch_seconds, min(effective_max, calc_seconds))

    logger.info(
        "Tổng thời lượng: %.1fs (%.1f phút) | Mục tiêu xem: %.1fs (%.1f phút, %.1f%%)",
        total_duration, total_duration / 60.0, target_seconds, target_seconds / 60.0, chosen_pct * 100
    )

    watched_seconds = 0.0
    wall_seconds = 0.0
    step = 5.0
    last_interaction = 0.0
    previous_time = float(initial_snapshot.get("currentTime") or 0.0)
    max_wall_seconds = max(target_seconds * 2.0, target_seconds + 300.0)

    while watched_seconds < target_seconds and wall_seconds < max_wall_seconds:
        if cancel_check and cancel_check():
            raise asyncio.CancelledError
        await asyncio.sleep(step)
        wall_seconds += step
        await handle_ad_skipping(page)
        try:
            snapshot = await page.evaluate(snapshot_script) or {}
            current_time = float(snapshot.get("currentTime") or 0.0)
            current_video_id = str(snapshot.get("videoId") or "")
            if expected_video_id and current_video_id not in {"", expected_video_id}:
                raise RuntimeError("YouTube đã tự chuyển sang video khác trong lúc xem.")
            if not snapshot.get("adShowing") and not snapshot.get("paused") and int(snapshot.get("readyState") or 0) >= 2:
                progress = max(0.0, min(current_time - previous_time, step * 1.5))
                watched_seconds += progress
            previous_time = current_time
            if snapshot.get("ended") or current_time >= max(0.0, total_duration - 1.0):
                watched_seconds = min(total_duration, max(watched_seconds, current_time))
                logger.info("Video đã phát đến cuối. Hoàn tất xem.")
                break
        except RuntimeError:
            raise
        except Exception as exc:
            logger.debug(
                "Không đọc được trạng thái phát video: %s",
                security_logging.redact_sensitive(exc),
            )

        if (watched_seconds - last_interaction) > random.uniform(70.0, 130.0) and watched_seconds < (target_seconds - 15.0):
            last_interaction = watched_seconds
            action_choice = random.choice(["scroll_comments", "pause_briefly", "mouse_move"])
            try:
                if action_choice == "scroll_comments":
                    scroll_amount = random.randint(180, 450)
                    await page.mouse.wheel(0, scroll_amount)
                    await asyncio.sleep(random.uniform(2.0, 4.5))
                    await page.mouse.wheel(0, -scroll_amount)
                elif action_choice == "pause_briefly":
                    pause_time = random.uniform(2.5, 6.0)
                    logger.debug("Giả lập tạm dừng video trong %.1fs...", pause_time)
                    await page.evaluate("() => document.querySelector('video.html5-main-video, video')?.pause()")
                    await asyncio.sleep(pause_time)
                    await page.evaluate("() => document.querySelector('video.html5-main-video, video')?.play().catch(() => {})")
                elif action_choice == "mouse_move":
                    await page.mouse.move(random.randint(150, 700), random.randint(150, 500))
            except Exception as exc:
                logger.debug(
                    "Không thể thực hiện tương tác xem tự nhiên: %s",
                    security_logging.redact_sensitive(exc),
                )

    if watched_seconds < min(target_seconds, total_duration) - 1.0:
        raise RuntimeError(
            f"Video chỉ phát thực tế {watched_seconds:.1f}s, chưa đạt mục tiêu {target_seconds:.1f}s."
        )
    logger.info(
        "Hoàn tất phiên xem video: %.1fs phát thực tế (%.1f phút)",
        watched_seconds,
        watched_seconds / 60.0,
    )
    return {
        "watched_seconds": round(watched_seconds, 2),
        "total_duration": total_duration,
        "retention_percentage": round(min(100.0, (watched_seconds / total_duration) * 100), 2),
    }


async def action_like_video(page: Any) -> str:
    """Click the Like button if not already liked."""
    logger.info("Thực hiện hành động Like video...")
    try:
        like_btn_info = await page.evaluate(
            """() => {
                const selectors = [
                    'like-button-view-model button',
                    '#top-level-buttons-computed ytd-toggle-button-renderer button',
                    'ytd-watch-metadata #segmented-like-button button',
                    'segmented-like-dislike-button-view-model like-button-view-model button',
                    'button[aria-label^="Like" i]',
                    'button[aria-label^="Thích" i]'
                ];
                for (const sel of selectors) {
                    const btn = Array.from(document.querySelectorAll(sel)).find(node => {
                        const rect = node.getBoundingClientRect();
                        const label = (node.getAttribute('aria-label') || '').toLowerCase();
                        return rect.width > 0 && rect.height > 0
                            && !label.includes('dislike') && !label.includes('không thích');
                    });
                    if (btn) {
                        const ariaPressed = btn.getAttribute('aria-pressed');
                        const isPressed = ariaPressed === 'true';
                        btn.setAttribute('data-trust-builder-action', 'like');
                        return {
                            found: true,
                            pressed: isPressed,
                            selector: '[data-trust-builder-action="like"]'
                        };
                    }
                }
                return { found: false, pressed: false, selector: '' };
            }"""
        )

        if not like_btn_info.get("found"):
            logger.warning("Không tìm thấy nút Like trên giao diện video.")
            return ACTION_NOT_FOUND

        if like_btn_info.get("pressed"):
            logger.info("Video đã được Like trước đó. Bỏ qua.")
            return ACTION_ALREADY_DONE

        matched_sel = like_btn_info.get("selector")
        btn = await page.query_selector(matched_sel) if matched_sel else None
        if btn:
            await btn.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await btn.click()
            await asyncio.sleep(random.uniform(1.2, 2.5))
            logger.info("Đã Like video thành công!")
            return ACTION_PERFORMED
        return ACTION_NOT_FOUND
    except Exception as exc:
        logger.warning(
            "Lỗi khi click Like video: %s",
            security_logging.redact_sensitive(exc),
        )
        return ACTION_FAILED


async def action_comment_video(page: Any, comment_text: str) -> str:
    """Add a contextual comment to the current video."""
    clean_text = comment_text.strip()
    if not clean_text:
        return ACTION_FAILED

    logger.info("Thực hiện bình luận video: '%s'", clean_text)
    try:
        # Scroll to comments section
        await natural_scroll(page, min_scrolls=2, max_scrolls=3)
        await asyncio.sleep(random.uniform(1.5, 2.5))

        # Focus comment box
        placeholder_sel = "#placeholder-area, #simplebox-placeholder, ytd-comment-simplebox-renderer #placeholder-area, ytd-comments-header-renderer"
        try:
            placeholder = await page.wait_for_selector(placeholder_sel, state="attached", timeout=8000)
            if placeholder:
                await placeholder.scroll_into_view_if_needed()
                await placeholder.click()
                await asyncio.sleep(random.uniform(0.6, 1.2))
        except Exception:
            pass

        # Find contenteditable input
        input_sel = "#contenteditable-root, ytd-commentbox #contenteditable-root, div#contenteditable-textarea"
        input_elem = await page.wait_for_selector(input_sel, state="attached", timeout=8000)
        if not input_elem:
            logger.warning("Không tìm thấy ô soạn thảo bình luận.")
            return ACTION_NOT_FOUND

        await input_elem.scroll_into_view_if_needed()
        await input_elem.click()
        await asyncio.sleep(random.uniform(0.3, 0.6))

        for char in clean_text:
            await input_elem.type(char, delay=random.randint(40, 110))
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.1, 0.3))

        await asyncio.sleep(random.uniform(1.0, 2.0))

        # Submit comment
        submit_btn_sel = "#submit-button button, ytd-commentbox #submit-button button, ytd-button-renderer#submit-button button"
        submit_btn = await page.query_selector(submit_btn_sel)
        if submit_btn and await submit_btn.is_enabled():
            await submit_btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            logger.info("Đã gửi bình luận thành công!")
            return ACTION_PERFORMED

        logger.warning("Nút Gửi bình luận không kích hoạt.")
        return ACTION_FAILED
    except Exception as exc:
        logger.warning(
            "Lỗi khi đăng bình luận: %s",
            security_logging.redact_sensitive(exc),
        )
        return ACTION_FAILED


async def action_subscribe_channel(page: Any) -> str:
    """Subscribe to the channel of the current video if not subscribed."""
    logger.info("Thực hiện hành động Đăng ký kênh (Subscribe)...")
    try:
        sub_info = await page.evaluate(
            """() => {
                const selectors = [
                    'ytd-watch-metadata #subscribe-button button',
                    'ytd-subscribe-button-renderer button',
                    'subscribe-button-view-model button',
                    '#subscribe-button-shape button'
                ];
                for (const sel of selectors) {
                    const subBtn = document.querySelector(sel);
                    if (subBtn) {
                        const rect = subBtn.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        const text = (subBtn.innerText || subBtn.getAttribute('aria-label') || '').toLowerCase();
                        const subscribed = text.includes('subscribed') || text.includes('đã đăng ký');
                        subBtn.setAttribute('data-trust-builder-action', 'subscribe');
                        return {
                            found: true,
                            subscribed,
                            selector: '[data-trust-builder-action="subscribe"]'
                        };
                    }
                }
                return { found: false, subscribed: false, selector: '' };
            }"""
        )

        if not sub_info.get("found"):
            logger.warning("Không tìm thấy nút Subscribe trên trang.")
            return ACTION_NOT_FOUND

        if sub_info.get("subscribed"):
            logger.info("Kênh đã được đăng ký trước đó. Bỏ qua.")
            return ACTION_ALREADY_DONE

        matched_sel = sub_info.get("selector")
        btn = await page.query_selector(matched_sel) if matched_sel else None
        if btn:
            await btn.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            logger.info("Đã bấm Subscribe kênh đối thủ thành công!")
            return ACTION_PERFORMED
        return ACTION_NOT_FOUND
    except Exception as exc:
        logger.warning(
            "Lỗi khi bấm Subscribe: %s",
            security_logging.redact_sensitive(exc),
        )
        return ACTION_FAILED


async def action_audit_channel_branding(
    page: Any,
    channel_id: str = "",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Audit channel profile completeness (Avatar, Banner, Handle, About/Description, Email)."""
    logger.info("Thực hiện Audit Branding kênh trên YouTube Studio / YouTube (channel_id=%s)...", channel_id)
    
    checklist = {
        "avatar": False,
        "banner": False,
        "about": False,
        "handle": False,
        "contact_email": False,
        "country": None,
        "two_factor_auth": None,
        "feature_level": "unknown",
    }

    clean_cid = (channel_id or "").strip()

    # 1. If channel_id is not provided, attempt to detect UCID from Studio root
    if not clean_cid:
        try:
            await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(random.uniform(2.5, 3.5))
            match = re.search(r"/channel/(UC[a-zA-Z0-9_-]+)", page.url or "")
            if match:
                clean_cid = match.group(1)
                logger.info("Đã phát hiện Channel UCID từ Studio URL: %s", clean_cid)
        except Exception as exc:
            logger.debug(
                "Không thể tự động phát hiện channel UCID từ Studio URL: %s",
                security_logging.redact_sensitive(exc),
            )

    studio_accessible = False

    # 2. Studio Customization Audit (Branding and Basic Info)
    if clean_cid:
        branding_url = f"https://studio.youtube.com/channel/{clean_cid}/editing/images"
        details_url = f"https://studio.youtube.com/channel/{clean_cid}/editing/details"
    else:
        branding_url = "https://studio.youtube.com/editing/images"
        details_url = "https://studio.youtube.com/editing/details"

    try:
        await page.goto(branding_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
        await asyncio.sleep(random.uniform(2.5, 4.0))

        current_url = str(page.url or "")
        page_content_sample = ""
        try:
            page_content_sample = (await page.content()) if hasattr(page, "content") else ""
        except Exception:
            pass

        # Check for permission denied or auth barriers
        is_permission_denied = (
            "accounts.google.com" in current_url
            or "không có quyền xem trang này" in page_content_sample
            or "not have permission" in page_content_sample
            or "access_denied" in page_content_sample
        )

        if not is_permission_denied:
            studio_accessible = True

            # Evaluate Studio Branding elements (Avatar, Banner)
            branding_info = await page.evaluate(
                """() => {
                    const avatarImgs = Array.from(document.querySelectorAll('#image-card img, ytcp-img-with-fallback img, #avatar img, img.style-scope.ytcp-img-with-fallback, .image-preview img'));
                    const validAvatar = avatarImgs.find(img => img.src && (img.src.includes('googleusercontent.com') || img.src.includes('ggpht.com')) && !img.src.includes('default_user') && !img.src.includes('silhouette') && !img.src.includes('blank_user'));
                    
                    const bannerImgs = Array.from(document.querySelectorAll('#banner-card img, ytcp-banner-editor img, #banner img, ytcp-image-upload#banner-image img, .banner-preview img'));
                    const validBanner = bannerImgs.find(img => img.src && (img.src.includes('googleusercontent.com') || img.src.includes('ggpht.com')));
                    
                    const buttons = Array.from(document.querySelectorAll('button, ytcp-button'));
                    const hasChangeOrRemoveBtn = buttons.some(b => {
                        const txt = (b.textContent || '').trim().toLowerCase();
                        return txt.includes('thay đổi') || txt.includes('xóa') || txt.includes('change') || txt.includes('remove');
                    });

                    return {
                        hasAvatar: Boolean(validAvatar || (avatarImgs.length > 0 && hasChangeOrRemoveBtn)),
                        hasBanner: Boolean(validBanner)
                    };
                }"""
            )
            if branding_info.get("hasAvatar"):
                checklist["avatar"] = True
            if branding_info.get("hasBanner"):
                checklist["banner"] = True

            # Navigate to Basic Info tab in Studio
            await page.goto(details_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(random.uniform(2.5, 4.0))

            info_data = await page.evaluate(
                """() => {
                    const handleInput = document.querySelector('#handle-input input, input[name="handle"], ytcp-form-input-container[label*="Handle" i] input, ytcp-form-input-container[label*="người dùng" i] input, #input-container input');
                    const descArea = document.querySelector('#description-textarea textarea, textarea[name="description"], ytcp-form-textarea #textarea, textarea[aria-label*="mô tả" i], textarea[aria-label*="description" i]');
                    const emailInput = document.querySelector('#email-input input, #contact-email-input input, input[name="email"], input[type="email"], ytcp-form-input-container[label*="Email" i] input');
                    
                    const handleVal = (handleInput && handleInput.value) ? handleInput.value.trim() : '';
                    const descVal = (descArea && descArea.value) ? descArea.value.trim() : '';
                    const emailVal = (emailInput && emailInput.value) ? emailInput.value.trim() : '';
                    
                    return {
                        hasHandle: handleVal.length > 1,
                        hasDesc: descVal.length > 10,
                        hasEmail: emailVal.includes('@') && emailVal.includes('.'),
                    };
                }"""
            )
            if info_data.get("hasHandle"):
                checklist["handle"] = True
            if info_data.get("hasDesc"):
                checklist["about"] = True
            if info_data.get("hasEmail"):
                checklist["contact_email"] = True

    except Exception as exc:
        logger.warning(
            "Studio inspection gặp ngoại lệ: %s",
            security_logging.redact_sensitive(exc),
        )

    # 3. Dual-Layer Fallback: Verify via Public YouTube Channel Page
    needs_public_audit = (
        not studio_accessible
        or not (checklist["avatar"] and checklist["banner"] and checklist["handle"] and checklist["about"])
    )
    if clean_cid and needs_public_audit:
        try:
            logger.info("Thực hiện quét bổ trợ qua trang công khai YouTube của kênh %s...", clean_cid)
            channel_public_url = f"https://www.youtube.com/channel/{clean_cid}"
            await page.goto(channel_public_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(random.uniform(2.5, 4.0))

            public_info = await page.evaluate(
                """() => {
                    // Avatar on channel header
                    const avatarImg = document.querySelector('yt-img-shadow#avatar img, #channel-header img#img, ytd-channel-avatar-editor img, #avatar img, .ytd-channel-header-renderer img');
                    const hasAvatar = Boolean(avatarImg && avatarImg.src && (avatarImg.src.includes('googleusercontent.com') || avatarImg.src.includes('ggpht.com')) && !avatarImg.src.includes('default_user') && !avatarImg.src.includes('silhouette'));

                    // Banner on channel header
                    const bannerImg = document.querySelector('#banner img, ytd-c4-tabbed-header-renderer #header-container img, yt-image-banner-view-model img, #channel-banner img, .ytd-c4-tabbed-header-renderer #banner img');
                    const hasBanner = Boolean(bannerImg && bannerImg.src && (bannerImg.src.includes('googleusercontent.com') || bannerImg.src.includes('ggpht.com')));

                    // Handle & Title
                    const handleElem = document.querySelector('#channel-handle, ytd-channel-name #text, yt-content-metadata-view-model, .ytd-channel-name');
                    const handleTxt = handleElem ? (handleElem.textContent || '').trim() : '';
                    const hasHandle = Boolean(handleTxt && handleTxt.length > 1);

                    // About snippet or description text
                    const descElem = document.querySelector('#description-inline, #description-container, .yt-core-attributed-string, #channel-header-container #description');
                    const descTxt = descElem ? (descElem.textContent || '').trim() : '';
                    const hasDesc = Boolean(descTxt && descTxt.length > 5);

                    // Check if email in description text
                    const emailRegex = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/;
                    const hasEmail = emailRegex.test(descTxt);

                    return { hasAvatar, hasBanner, hasHandle, hasDesc, hasEmail };
                }"""
            )

            if not checklist["avatar"] and public_info.get("hasAvatar"):
                checklist["avatar"] = True
            if not checklist["banner"] and public_info.get("hasBanner"):
                checklist["banner"] = True
            if not checklist["handle"] and public_info.get("hasHandle"):
                checklist["handle"] = True
            if not checklist["about"] and public_info.get("hasDesc"):
                checklist["about"] = True
            if not checklist["contact_email"] and public_info.get("hasEmail"):
                checklist["contact_email"] = True

        except Exception as exc:
            logger.warning(
                "Lỗi trong quá trình quét trang công khai YouTube: %s",
                security_logging.redact_sensitive(exc),
            )

    logger.info("Kết quả Audit Branding hoàn tất: %s", checklist)
    return checklist


async def action_audit_feature_eligibility(
    page: Any,
    channel_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Read feature eligibility conservatively; unknown is never treated as verified."""
    clean_channel_id = str(channel_id or "").strip()
    if not clean_channel_id:
        return {"feature_level": "unknown", "verified": False}

    target_url = (
        f"https://studio.youtube.com/channel/{clean_channel_id}/feature-eligibility"
    )
    try:
        await page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=int(timeout_seconds * 1000),
        )
        await asyncio.sleep(random.uniform(2.5, 4.0))
        result = await page.evaluate(
            r"""() => {
                const normalize = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const enabledTerms = ['enabled', 'eligible', 'đã bật', 'đủ điều kiện'];
                const disabledTerms = [
                    'not enabled', 'not eligible', 'disabled', 'ineligible',
                    'chưa bật', 'không đủ điều kiện', 'không được bật'
                ];
                const nodes = Array.from(document.querySelectorAll(
                    'ytcp-feature-eligibility-card, ytcp-feature-eligibility-item, '
                    + '[class*="feature-eligibility"], [id*="feature-eligibility"]'
                ));
                const bodyText = normalize(document.body?.innerText || '');
                const readLevel = terms => {
                    const node = nodes.find(item => {
                        const text = normalize(item.innerText || item.textContent || '');
                        return terms.some(term => text.includes(term));
                    });
                    if (!node) return false;
                    const text = normalize(node.innerText || node.textContent || '');
                    if (disabledTerms.some(term => text.includes(term))) return false;
                    return enabledTerms.some(term => text.includes(term));
                };
                const advanced = readLevel(['advanced features', 'tính năng nâng cao']);
                const intermediate = readLevel(['intermediate features', 'tính năng trung cấp']);
                const pageRecognized = bodyText.includes('feature eligibility')
                    || bodyText.includes('điều kiện sử dụng tính năng')
                    || nodes.length > 0;
                return { advanced, intermediate, pageRecognized };
            }"""
        ) or {}
        if result.get("advanced"):
            return {"feature_level": "advanced", "verified": True}
        if result.get("intermediate"):
            return {"feature_level": "intermediate", "verified": True}
        return {
            "feature_level": "unknown",
            "verified": False,
            "page_recognized": bool(result.get("pageRecognized")),
        }
    except Exception as exc:
        logger.warning(
            "Không thể đọc Feature Eligibility: %s",
            security_logging.redact_sensitive(exc),
        )
        return {"feature_level": "unknown", "verified": False}
