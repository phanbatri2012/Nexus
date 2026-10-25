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

from nexus_engine.services import database as db, security_logging, trust_builder_safety as safety

logger = logging.getLogger(__name__)

ACTION_PERFORMED = "performed"
ACTION_ALREADY_DONE = "already_done"
ACTION_NOT_FOUND = "not_found"
ACTION_FAILED = "failed"
ACTION_BLOCKED = "blocked"
ACTION_UNCERTAIN = "uncertain"


def action_result(
    status: str,
    reason_code: str = "",
    message: str = "",
) -> dict[str, str]:
    """Return a stable, persistence-safe result for an engagement action."""
    return {
        "status": status,
        "reason_code": reason_code,
        "message": message,
    }


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


def _youtube_video_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    host = parsed.netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0]
    if parsed.path == "/watch":
        return str(urllib.parse.parse_qs(parsed.query).get("v", [""])[0]).strip()
    for prefix in ("/shorts/", "/live/", "/embed/"):
        if parsed.path.startswith(prefix):
            return parsed.path[len(prefix):].split("/", 1)[0].strip()
    return ""


async def action_get_current_youtube_video_id(page: Any) -> str:
    """Read the exact video ID currently loaded in the authenticated page."""
    try:
        current = await page.evaluate(
            r"""() => {
                const url = new URL(window.location.href);
                const pathMatch = url.pathname.match(/^\/(?:shorts|live|embed)\/([^/?#]+)/);
                return url.searchParams.get('v') || (pathMatch ? pathMatch[1] : '');
            }"""
        )
        if str(current or "").strip():
            return str(current).strip()
    except Exception as exc:
        logger.debug(
            "Không thể đọc YouTube video ID từ DOM: %s",
            security_logging.redact_sensitive(exc),
        )
    return _youtube_video_id_from_url(str(getattr(page, "url", "") or ""))


def _sample_context_segments(segments: list[str], maximum: int = 8) -> list[str]:
    cleaned = [re.sub(r"\s+", " ", str(item or "")).strip() for item in segments]
    cleaned = [item for item in cleaned if item]
    if len(cleaned) <= maximum:
        return cleaned
    indexes = sorted(
        {
            round(index * (len(cleaned) - 1) / max(1, maximum - 1))
            for index in range(maximum)
        }
    )
    return [cleaned[index] for index in indexes]


async def action_collect_video_context(
    page: Any,
    *,
    watched_from_seconds: float = 0.0,
    watched_until_seconds: float = 0.0,
) -> dict[str, Any]:
    """Collect bounded public context from the current YouTube watch page."""
    try:
        await page.evaluate(
            r"""() => {
                const candidates = Array.from(document.querySelectorAll(
                    'ytd-video-description-transcript-section-renderer button, '
                    + 'button[aria-label*="transcript" i], button[aria-label*="bản chép lời" i]'
                ));
                const button = candidates.find(node => {
                    const text = `${node.textContent || ''} ${node.getAttribute('aria-label') || ''}`.toLowerCase();
                    return text.includes('transcript') || text.includes('bản chép lời');
                });
                if (button) {
                    button.click();
                    return true;
                }
                return false;
            }"""
        )
        await asyncio.sleep(1.0)
    except Exception as exc:
        logger.debug(
            "Không thể mở transcript YouTube để thu thập ngữ cảnh: %s",
            security_logging.redact_sensitive(exc),
        )

    snapshot = await page.evaluate(
        r"""() => {
            const firstText = selectors => {
                for (const selector of selectors) {
                    const node = document.querySelector(selector);
                    const text = (node?.textContent || '').trim();
                    if (text) return text;
                }
                return '';
            };
            const uniqueTexts = (selectors, limit) => {
                const values = [];
                const seen = new Set();
                for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
                        if (text && !seen.has(text)) {
                            seen.add(text);
                            values.push(text);
                        }
                        if (values.length >= limit) return values;
                    }
                }
                return values;
            };
            const url = new URL(window.location.href);
            const pathMatch = url.pathname.match(/^\/(?:shorts|live|embed)\/([^/?#]+)/);
            const transcript = Array.from(document.querySelectorAll(
                'ytd-transcript-segment-renderer, ytd-transcript-segment-list-renderer [role="button"]'
            )).slice(0, 200).map(node => {
                const timestamp = (node.querySelector('.segment-timestamp, [class*="timestamp"]')?.textContent || '').trim();
                const text = (node.querySelector('.segment-text, [class*="segment-text"]')?.textContent || node.textContent || '')
                    .replace(/\s+/g, ' ').trim();
                return { timestamp, text };
            }).filter(item => item.text);
            return {
                youtube_video_id: url.searchParams.get('v') || (pathMatch ? pathMatch[1] : ''),
                title: firstText(['h1.ytd-watch-metadata yt-formatted-string', 'h1.title']),
                channel: firstText(['ytd-channel-name #text', '#owner #channel-name', '#upload-info #channel-name']),
                description: firstText([
                    '#description-inline-expander yt-attributed-string',
                    '#description-inline-expander',
                    'ytd-text-inline-expander#description-inline-expander',
                    '#description'
                ]),
                chapters: uniqueTexts([
                    'ytd-macro-markers-list-item-renderer #details',
                    'ytd-chapter-renderer #details',
                    '#chapters ytd-macro-markers-list-item-renderer'
                ], 20),
                top_comments: uniqueTexts([
                    'ytd-comment-thread-renderer #content-text',
                    'ytd-comment-view-model #content-text',
                    '#comments #content-text'
                ], 5),
                transcript
            };
        }"""
    ) or {}

    def timestamp_seconds(value: str) -> float:
        try:
            parts = [float(part) for part in str(value or "").strip().split(":")]
        except ValueError:
            return 0.0
        total = 0.0
        for part in parts:
            total = total * 60.0 + part
        return total

    raw_segments = snapshot.pop("transcript", []) if isinstance(snapshot, dict) else []
    segment_texts: list[str] = []
    for item in raw_segments if isinstance(raw_segments, list) else []:
        if not isinstance(item, dict):
            continue
        timestamp = str(item.get("timestamp") or "").strip()
        if timestamp:
            segment_second = timestamp_seconds(timestamp)
            if watched_from_seconds > 0 and segment_second < max(
                0.0, watched_from_seconds - 10.0
            ):
                continue
            if watched_until_seconds > 0 and segment_second > watched_until_seconds + 10:
                continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if timestamp and text.startswith(timestamp):
            text = text[len(timestamp):].strip()
        if text:
            segment_texts.append(text)
    snapshot["transcript_segments"] = _sample_context_segments(segment_texts, maximum=8)
    snapshot["top_comments"] = list(snapshot.get("top_comments") or [])[:5]
    return snapshot


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


async def action_browse_and_pick_video(
    page: Any,
    target_channel: str = "",
    excluded_video_ids: set[str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Pick a safe video without consuming another keyword-search action."""
    clean_target = str(target_channel or "").strip()
    browse_url = "https://www.youtube.com"
    if clean_target.startswith("@"):  # Direct channel browsing is not a search.
        encoded_handle = urllib.parse.quote(clean_target, safe="@._-")
        browse_url = f"https://www.youtube.com/{encoded_handle}/videos"
    elif clean_target.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(clean_target)
        host = str(parsed.hostname or "").lower()
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            browse_url = f"https://www.youtube.com{parsed.path.rstrip('/')}"
            if not browse_url.endswith("/videos"):
                browse_url += "/videos"

    logger.info("Chọn video Watch-only từ: %s", browse_url)
    await page.goto(
        browse_url,
        wait_until="domcontentloaded",
        timeout=int(timeout_seconds * 1000),
    )
    await asyncio.sleep(random.uniform(1.5, 3.0))
    await dismiss_common_popups(page)
    try:
        await page.wait_for_selector(
            "a#video-title[href*='/watch?v='], a#thumbnail[href*='/watch?v='], a[href*='/watch?v=']",
            state="attached",
            timeout=12000,
        )
    except Exception:
        pass
    await natural_scroll(page, min_scrolls=1, max_scrolls=3)

    candidates: list[dict[str, str]] = await page.evaluate(
        """targetChannel => {
            const results = [];
            const seen = new Set();
            const nodes = document.querySelectorAll(
                'ytd-video-renderer, ytd-rich-item-renderer, ytd-grid-video-renderer, ytd-rich-grid-media'
            );
            for (const node of nodes) {
                const titleNode = node.querySelector('a#video-title, a#video-title-link, h3 a');
                const thumbNode = node.querySelector('a#thumbnail[href*="/watch?v="], a[href*="/watch?v="]');
                const channelNode = node.querySelector(
                    'ytd-channel-name a, #channel-info a, #byline a, a[href*="/@"], a[href*="/channel/"]'
                );
                const url = (titleNode?.href || thumbNode?.href || '').trim();
                const title = (
                    titleNode?.getAttribute('title')
                    || titleNode?.innerText
                    || titleNode?.getAttribute('aria-label')
                    || ''
                ).trim();
                if (!url.includes('/watch?v=') || !title || seen.has(url)) continue;
                seen.add(url);
                results.push({
                    url,
                    title,
                    channel: (channelNode?.innerText || channelNode?.getAttribute('title') || targetChannel || '').trim(),
                    channel_href: (channelNode?.href || '').trim(),
                });
            }
            if (results.length === 0) {
                for (const link of document.querySelectorAll('a#video-title[href*="/watch?v="], a[href*="/watch?v="]')) {
                    const url = (link.href || '').trim();
                    const title = (
                        link.getAttribute('title')
                        || link.innerText
                        || link.getAttribute('aria-label')
                        || ''
                    ).trim();
                    if (!url.includes('/watch?v=') || title.length < 6 || seen.has(url)) continue;
                    seen.add(url);
                    results.push({url, title, channel: targetChannel || '', channel_href: ''});
                }
            }
            return results;
        }""",
        clean_target,
    )
    if not candidates:
        raise RuntimeError("Không tìm thấy video phù hợp cho phiên Watch-only.")

    excluded_ids = excluded_video_ids or set()
    safe_candidates: list[dict[str, str]] = []
    for candidate in candidates:
        match = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", candidate.get("url", ""))
        if match and match.group(1) in excluded_ids:
            continue
        is_safe, blocked_reason = safety.is_safe_for_interaction(
            title=candidate.get("title", ""),
            channel_name=candidate.get("channel", ""),
            channel_url=candidate.get("channel_href", ""),
            channel_handle=candidate.get("channel", ""),
        )
        if is_safe:
            safe_candidates.append(candidate)
        else:
            logger.warning(
                "SAFETY SHIELD: Bỏ qua video Watch-only '%s' do vi phạm: %s",
                candidate.get("title", ""),
                blocked_reason,
            )
    if not safe_candidates:
        raise RuntimeError("Không còn video Watch-only mới và an toàn.")

    selected = random.choice(safe_candidates[: min(8, len(safe_candidates))])
    logger.info(
        "Đã chọn video Watch-only an toàn: '%s' từ kênh '%s'",
        selected.get("title", ""),
        selected.get("channel", ""),
    )
    return selected


async def action_refocus_video_player(
    page: Any,
    cancel_check: Callable[[], bool] | None = None,
) -> float:
    """Refocus visual attention and mouse onto the video player after scrolling back from comments.

    Aligns player into the upper viewport, smoothly glides the mouse cursor into the player container,
    and pauses naturally so YouTube's player controls overlay (ytp-chrome-bottom) appears and fades out.
    Returns the time spent in seconds during this refocus behavior.
    """
    logger.info("Chuyển tiêu điểm chú ý chuột trở lại khung phát video (Refocus Player)...")
    time_spent = 0.0

    try:
        if cancel_check and cancel_check():
            return 0.0

        # 1. Fetch player bounding rect and ensure it is properly aligned in viewport
        player_info = await page.evaluate(
            """() => {
                const player = document.querySelector('#movie_player, video.html5-main-video, .html5-video-player');
                if (!player) return null;
                const rect = player.getBoundingClientRect();
                return {
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    scrollY: Math.round(window.scrollY || window.pageYOffset || 0)
                };
            }"""
        )

        if not isinstance(player_info, dict) or player_info.get("width", 0) <= 0:
            logger.debug("Không xác định được tọa độ player để refocus.")
            return 0.0

        p_left = float(player_info.get("left", 0))
        p_top = float(player_info.get("top", 0))
        p_width = float(player_info.get("width", 0))
        p_height = float(player_info.get("height", 0))
        scroll_y = float(player_info.get("scrollY", 0))

        # 2. Check if player top is shifted off-screen or too low; align smoothly if needed
        if p_top < -30 or p_top > 120 or scroll_y > 200:
            logger.debug("Video player chưa ở vị trí tối ưu (top=%.1f, scrollY=%.1f). Căn chỉnh lại...", p_top, scroll_y)
            await page.evaluate("() => window.scrollTo({ top: 0, behavior: 'smooth' })")
            align_pause = random.uniform(0.4, 0.7)
            await asyncio.sleep(align_pause)
            time_spent += align_pause
            try:
                new_info = await page.evaluate(
                    """() => {
                        const player = document.querySelector('#movie_player, video.html5-main-video, .html5-video-player');
                        if (!player) return null;
                        const rect = player.getBoundingClientRect();
                        return {
                            left: Math.round(rect.left),
                            top: Math.round(rect.top),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height)
                        };
                    }"""
                )
                if isinstance(new_info, dict) and new_info.get("width", 0) > 0:
                    p_left = float(new_info.get("left", p_left))
                    p_top = float(new_info.get("top", p_top))
                    p_width = float(new_info.get("width", p_width))
                    p_height = float(new_info.get("height", p_height))
            except Exception:
                pass

        if cancel_check and cancel_check():
            return time_spent

        # 3. Calculate target hover coordinates inside the player (around center with natural offset)
        target_x = max(100.0, p_left + p_width * random.uniform(0.35, 0.65))
        target_y = max(100.0, p_top + p_height * random.uniform(0.40, 0.70))

        # 4. Glide mouse smoothly towards player (steps emulate natural cursor glide)
        glide_steps = random.randint(5, 9)
        await page.mouse.move(target_x, target_y, steps=glide_steps)
        glide_pause = random.uniform(0.1, 0.25)
        await asyncio.sleep(glide_pause)
        time_spent += glide_pause

        if cancel_check and cancel_check():
            return time_spent

        # 5. Natural attention dwell: let player controls pop up (ytp-chrome-bottom) and observe
        dwell_pause = random.uniform(1.5, 2.6)
        await asyncio.sleep(dwell_pause)
        time_spent += dwell_pause

        # 6. Micro-interaction: subtle jitter or glance towards progress bar
        if random.random() < 0.35 and not (cancel_check and cancel_check()):
            sub_x = target_x + random.uniform(-40.0, 40.0)
            sub_y = min(p_top + p_height - 30.0, target_y + random.uniform(20.0, 50.0))
            await page.mouse.move(sub_x, sub_y, steps=random.randint(3, 5))
            micro_pause = random.uniform(0.5, 1.0)
            await asyncio.sleep(micro_pause)
            time_spent += micro_pause

        # 7. Verify playback is still active; resume if accidentally paused
        try:
            is_paused = await page.evaluate(
                "() => Boolean(document.querySelector('video.html5-main-video, video')?.paused)"
            )
            if is_paused:
                logger.info("Video đang tạm dừng sau khi cuộn lên, tiếp tục phát...")
                await page.evaluate(
                    "() => document.querySelector('video.html5-main-video, video')?.play().catch(() => {})"
                )
        except Exception:
            pass

        logger.info("Hoàn tất chuyển tiêu điểm chuột vào player (thời gian tương tác: %.1fs).", time_spent)
    except Exception as exc:
        logger.debug(
            "Lỗi trong quá trình refocus video player: %s",
            security_logging.redact_sensitive(exc),
        )

    return round(time_spent, 2)



async def action_browse_and_read_comments(
    page: Any,
    min_dwell_seconds: float = 15.0,
    max_dwell_seconds: float = 35.0,
    cancel_check: Callable[[], bool] | None = None,
    return_to_top: bool = True,
) -> dict[str, Any]:
    """Scroll down into the comments section, spend realistic time reading comments, and return.

    Performs human-like incremental scrolling, dwell pauses proportional to reading speed,
    intermittent micro-adjustments/mouse movements, and extracts visible comments for telemetry.
    """
    logger.info("Bắt đầu lướt đọc bình luận tự nhiên...")
    extracted_comments: list[str] = []
    time_spent = 0.0

    try:
        # 1. Smoothly scroll down towards comments section (typically 600 - 1000px down)
        initial_scrolls = random.randint(2, 4)
        for _ in range(initial_scrolls):
            if cancel_check and cancel_check():
                break
            delta = random.randint(260, 480)
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.4, 0.9))

        target_dwell = max(min_dwell_seconds, random.uniform(min_dwell_seconds, max_dwell_seconds))

        # Try to read visible comments without throwing if evaluation fails
        try:
            raw_extracted = await page.evaluate(
                """() => {
                    const selectors = [
                        'ytd-comment-thread-renderer #content-text',
                        'ytd-comment-view-model #content-text',
                        '#comment #content-text',
                        '#contents ytd-comment-thread-renderer yt-formatted-string#content-text'
                    ];
                    for (const sel of selectors) {
                        const nodes = Array.from(document.querySelectorAll(sel));
                        if (nodes.length > 0) {
                            return nodes.slice(0, 5).map(n => (n.textContent || '').trim()).filter(t => t.length > 0);
                        }
                    }
                    return [];
                }"""
            )
            if isinstance(raw_extracted, list):
                extracted_comments = [str(c).strip() for c in raw_extracted if str(c).strip()]
                if extracted_comments:
                    logger.info(
                        "Đã lướt thấy %d bình luận đầu trang. Bắt đầu dừng lại đọc chi tiết...",
                        len(extracted_comments),
                    )
        except Exception:
            extracted_comments = []

        # 2. Reading loop with variable pauses and micro-scrolls
        while time_spent < target_dwell:
            if cancel_check and cancel_check():
                break

            # Dwell slice for reading a comment (3.5 - 7.5s)
            slice_time = min(random.uniform(3.5, 7.5), max(1.0, target_dwell - time_spent))
            await asyncio.sleep(slice_time)
            time_spent += slice_time

            if time_spent >= target_dwell or (cancel_check and cancel_check()):
                break

            # Human micro-interaction while reading
            sub_action = random.choices(["scroll_next", "mouse_hover", "scroll_back"], weights=[0.55, 0.30, 0.15])[0]
            if sub_action == "scroll_next":
                # Scroll down slightly to read the next comment thread
                scroll_down = random.randint(140, 280)
                await page.mouse.wheel(0, scroll_down)
            elif sub_action == "mouse_hover":
                # Move cursor over comment area naturally
                await page.mouse.move(random.randint(200, 650), random.randint(250, 600))
            elif sub_action == "scroll_back":
                # Small scroll back up as if re-reading
                scroll_up = random.randint(60, 150)
                await page.mouse.wheel(0, -scroll_up)

            micro_pause = random.uniform(0.3, 0.8)
            await asyncio.sleep(micro_pause)
            time_spent += micro_pause

        # 3. Smoothly scroll back up towards the video player if requested
        if return_to_top:
            return_scrolls = random.randint(2, 4)
            for _ in range(return_scrolls):
                if cancel_check and cancel_check():
                    break
                delta = random.randint(280, 520)
                await page.mouse.wheel(0, -delta)
                await asyncio.sleep(random.uniform(0.3, 0.6))

            # Attention Refocus Telemetry: smoothly glide cursor into player and observe
            if not (cancel_check and cancel_check()):
                refocus_dwell = await action_refocus_video_player(page, cancel_check=cancel_check)
                time_spent += refocus_dwell

        logger.info(
            "Hoàn tất lướt đọc bình luận: %.1fs dừng đọc thực tế (%d bình luận trích xuất).",
            time_spent,
            len(extracted_comments),
        )
    except Exception as exc:
        logger.debug(
            "Lỗi khi lướt đọc bình luận tự nhiên: %s",
            security_logging.redact_sensitive(exc),
        )

    return {
        "dwell_seconds": round(time_spent, 2),
        "comments": extracted_comments,
    }


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
    observed_comments: list[str] = []
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

        if (watched_seconds - last_interaction) > random.uniform(50.0, 95.0) and watched_seconds < (target_seconds - 15.0):
            last_interaction = watched_seconds
            action_choice = random.choices(
                ["scroll_comments", "pause_briefly", "mouse_move"],
                weights=[0.55, 0.25, 0.20],
            )[0]
            try:
                if action_choice == "scroll_comments":
                    remaining_watch = max(5.0, target_seconds - watched_seconds - 5.0)
                    min_dwell = min(15.0, remaining_watch)
                    max_dwell = min(35.0, max(min_dwell + 3.0, remaining_watch))
                    read_res = await action_browse_and_read_comments(
                        page,
                        min_dwell_seconds=min_dwell,
                        max_dwell_seconds=max_dwell,
                        cancel_check=cancel_check,
                    )
                    for observed in read_res.get("comments") or []:
                        clean_observed = str(observed or "").strip()
                        if clean_observed and clean_observed not in observed_comments:
                            observed_comments.append(clean_observed)
                    dwell_done = float(read_res.get("dwell_seconds") or 0.0)
                    try:
                        snapshot = await page.evaluate(snapshot_script) or {}
                        current_time = float(snapshot.get("currentTime") or 0.0)
                        current_video_id = str(snapshot.get("videoId") or "")
                        if expected_video_id and current_video_id not in {"", expected_video_id}:
                            raise RuntimeError("YouTube đã tự chuyển sang video khác trong lúc xem.")
                        if not snapshot.get("adShowing") and not snapshot.get("paused") and int(snapshot.get("readyState") or 0) >= 2:
                            time_diff = max(0.0, current_time - previous_time)
                            progress = min(time_diff, max(step * 1.5, dwell_done * 1.5))
                            watched_seconds += progress
                        previous_time = current_time
                        wall_seconds += dwell_done
                        if snapshot.get("ended") or current_time >= max(0.0, total_duration - 1.0):
                            watched_seconds = min(total_duration, max(watched_seconds, current_time))
                            logger.info("Video đã phát đến cuối. Hoàn tất xem.")
                            break
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        logger.debug(
                            "Không đọc được trạng thái phát video sau khi đọc bình luận: %s",
                            security_logging.redact_sensitive(exc),
                        )
                elif action_choice == "pause_briefly":
                    pause_time = random.uniform(2.5, 6.0)
                    logger.debug("Giả lập tạm dừng video trong %.1fs...", pause_time)
                    await page.evaluate("() => document.querySelector('video.html5-main-video, video')?.pause()")
                    await asyncio.sleep(pause_time)
                    await page.evaluate("() => document.querySelector('video.html5-main-video, video')?.play().catch(() => {})")
                elif action_choice == "mouse_move":
                    await page.mouse.move(random.randint(150, 700), random.randint(150, 500))
            except RuntimeError:
                raise
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
        "youtube_video_id": str(initial_snapshot.get("videoId") or expected_video_id),
        "first_position_seconds": round(
            max(0.0, float(initial_snapshot.get("currentTime") or 0.0)), 2
        ),
        "last_position_seconds": round(max(0.0, previous_time), 2),
        "observed_comments": observed_comments[:5],
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
            try:
                await page.wait_for_function(
                    """() => {
                        const selectors = [
                            '[data-trust-builder-action="like"]',
                            'like-button-view-model button',
                            'ytd-watch-metadata #segmented-like-button button'
                        ];
                        return selectors.some(selector => {
                            const node = document.querySelector(selector);
                            return node && node.getAttribute('aria-pressed') === 'true';
                        });
                    }""",
                    timeout=5000,
                )
            except Exception:
                logger.warning("YouTube chưa xác nhận trạng thái Like sau khi click.")
                return ACTION_FAILED
            logger.info("YouTube đã xác nhận video ở trạng thái Like.")
            return ACTION_PERFORMED
        return ACTION_NOT_FOUND
    except Exception as exc:
        logger.warning(
            "Lỗi khi click Like video: %s",
            security_logging.redact_sensitive(exc),
        )
        return ACTION_FAILED


async def action_comment_video(page: Any, comment_text: str) -> dict[str, str]:
    """Add a contextual comment to the current video."""
    clean_text = comment_text.strip()
    if not clean_text:
        return action_result(ACTION_FAILED, "empty_comment", "Nội dung bình luận trống.")

    logger.info("Thực hiện bình luận video: '%s'", clean_text)
    try:
        # Scroll to comments section and pause naturally as if reading
        await natural_scroll(page, min_scrolls=3, max_scrolls=5)
        await asyncio.sleep(random.uniform(6.0, 12.0))

        composer_info = await page.evaluate(
            """() => {
                const visible = node => {
                    if (!node) return false;
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0
                        && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const disabledText = Array.from(document.querySelectorAll(
                    'ytd-comments-message-renderer, ytd-message-renderer, #message, #contents-message'
                )).map(node => (node.textContent || '').toLowerCase()).join(' ');
                if (disabledText.includes('comments are turned off')
                    || disabledText.includes('comments are disabled')
                    || disabledText.includes('bình luận đã bị tắt')
                    || disabledText.includes('đã tắt tính năng bình luận')) {
                    return {found: false, blocked: true};
                }
                const selectors = [
                    'ytd-comments ytd-comment-simplebox-renderer #placeholder-area',
                    'ytd-comments #simplebox-placeholder',
                    'ytd-comment-simplebox-renderer #placeholder-area',
                    '#placeholder-area'
                ];
                for (const selector of selectors) {
                    const node = Array.from(document.querySelectorAll(selector)).find(visible);
                    if (!node) continue;
                    node.setAttribute('data-trust-builder-action', 'comment-placeholder');
                    return {found: true, blocked: false};
                }
                return {found: false, blocked: false};
            }"""
        )
        if composer_info.get("blocked"):
            logger.info("Video đã tắt tính năng bình luận.")
            return action_result(ACTION_BLOCKED, "comments_disabled", "Video đã tắt bình luận.")
        if not composer_info.get("found"):
            logger.warning("Không tìm thấy vùng mở trình soạn thảo bình luận.")
            return action_result(ACTION_NOT_FOUND, "comment_placeholder_missing", "Không tìm thấy vùng bình luận.")

        placeholder = await page.query_selector('[data-trust-builder-action="comment-placeholder"]')
        if not placeholder:
            return action_result(ACTION_NOT_FOUND, "comment_placeholder_stale", "Vùng bình luận không còn khả dụng.")
        await placeholder.scroll_into_view_if_needed()
        await placeholder.click()
        await asyncio.sleep(random.uniform(0.6, 1.2))

        try:
            await page.wait_for_function(
                """() => Array.from(document.querySelectorAll(
                    'ytd-commentbox #contenteditable-root[contenteditable="true"], '
                    + 'ytd-comment-simplebox-renderer #contenteditable-root[contenteditable="true"], '
                    + 'div#contenteditable-root[contenteditable="true"], '
                    + 'ytd-commentbox div[contenteditable="true"][role="textbox"]'
                )).some(node => {
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                })""",
                timeout=10000,
            )
        except Exception:
            logger.warning("Ô soạn thảo bình luận không xuất hiện sau khi mở.")
            return action_result(
                ACTION_NOT_FOUND,
                "comment_editor_timeout",
                "Ô soạn thảo bình luận không xuất hiện.",
            )
        input_info = await page.evaluate(
            """() => {
                const selectors = [
                    'ytd-commentbox #contenteditable-root[contenteditable="true"]',
                    'ytd-comment-simplebox-renderer #contenteditable-root[contenteditable="true"]',
                    'div#contenteditable-root[contenteditable="true"]',
                    'ytd-commentbox div[contenteditable="true"][role="textbox"]'
                ];
                for (const selector of selectors) {
                    const node = Array.from(document.querySelectorAll(selector)).find(item => {
                        const rect = item.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    });
                    if (!node) continue;
                    node.setAttribute('data-trust-builder-action', 'comment-editor');
                    return {found: true};
                }
                return {found: false};
            }"""
        )
        if not input_info.get("found"):
            logger.warning("Không tìm thấy ô soạn thảo bình luận đang hiển thị.")
            return action_result(ACTION_NOT_FOUND, "comment_editor_missing", "Không tìm thấy ô soạn thảo bình luận.")
        input_elem = await page.query_selector('[data-trust-builder-action="comment-editor"]')
        if not input_elem:
            return action_result(ACTION_NOT_FOUND, "comment_editor_stale", "Ô soạn thảo không còn khả dụng.")

        await input_elem.scroll_into_view_if_needed()
        await input_elem.click()
        await asyncio.sleep(random.uniform(0.3, 0.6))

        for char in clean_text:
            await input_elem.type(char, delay=random.randint(40, 110))
            if random.random() < 0.05:
                await asyncio.sleep(random.uniform(0.1, 0.3))

        await asyncio.sleep(random.uniform(1.0, 2.0))

        submit_info = await page.evaluate(
            r"""expectedText => {
                const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
                const editor = document.querySelector('[data-trust-builder-action="comment-editor"]');
                const selectors = [
                    'ytd-commentbox #submit-button button',
                    'ytd-commentbox ytd-button-renderer#submit-button button',
                    'ytd-comment-simplebox-renderer #submit-button button',
                    'ytd-comments button[aria-label="Comment"]',
                    'ytd-comments button[aria-label="Bình luận"]'
                ];
                for (const selector of selectors) {
                    const button = Array.from(document.querySelectorAll(selector)).find(node => {
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    });
                    if (!button) continue;
                    button.setAttribute('data-trust-builder-action', 'comment-submit');
                    return {
                        found: true,
                        enabled: !button.disabled && button.getAttribute('aria-disabled') !== 'true',
                        text_ready: normalize(editor?.textContent) === normalize(expectedText),
                    };
                }
                return {found: false, enabled: false, text_ready: false};
            }""",
            clean_text,
        )
        if not submit_info.get("text_ready"):
            logger.warning("YouTube không giữ đúng nội dung trong ô bình luận.")
            return action_result(ACTION_FAILED, "comment_text_not_ready", "Nội dung chưa sẵn sàng để gửi.")
        if not submit_info.get("found"):
            logger.warning("Không tìm thấy nút Gửi bình luận đang hiển thị.")
            return action_result(ACTION_NOT_FOUND, "comment_submit_missing", "Không tìm thấy nút gửi bình luận.")
        if not submit_info.get("enabled"):
            logger.warning("Nút Gửi bình luận không kích hoạt.")
            return action_result(ACTION_FAILED, "comment_submit_disabled", "Nút gửi bình luận chưa được kích hoạt.")

        submit_btn = await page.query_selector('[data-trust-builder-action="comment-submit"]')
        if submit_btn:
            await submit_btn.click()
            try:
                await page.wait_for_function(
                    r"""commentText => {
                        const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
                        const expected = normalize(commentText);
                        const visibleComment = Array.from(document.querySelectorAll(
                            'ytd-comment-thread-renderer #content-text, ytd-comment-renderer #content-text'
                        )).some(node => normalize(node.textContent) === expected);
                        const editor = document.querySelector('[data-trust-builder-action="comment-editor"]');
                        const submit = document.querySelector('[data-trust-builder-action="comment-submit"]');
                        const toastText = Array.from(document.querySelectorAll(
                            'tp-yt-paper-toast, yt-notification-action-renderer, ytd-notification-topbar-button-renderer'
                        )).map(node => normalize(node.textContent).toLowerCase()).join(' ');
                        const editorReset = Boolean(
                            (!editor || normalize(editor.textContent) === '')
                            && (!submit || submit.disabled || submit.getAttribute('aria-disabled') === 'true')
                        );
                        const toastConfirmed = toastText.includes('comment added')
                            || toastText.includes('đã thêm bình luận');
                        return visibleComment || editorReset || toastConfirmed;
                    }""",
                    clean_text,
                    timeout=12000,
                )
            except Exception:
                logger.warning("YouTube chưa xác nhận bình luận sau khi gửi.")
                return action_result(
                    ACTION_UNCERTAIN,
                    "comment_confirmation_timeout",
                    "Đã bấm gửi nhưng YouTube chưa xác nhận; sẽ không gửi lại trên video này.",
                )
            logger.info("YouTube đã xác nhận bình luận được gửi.")
            return action_result(ACTION_PERFORMED, "comment_confirmed", "YouTube đã xác nhận bình luận.")

        return action_result(ACTION_NOT_FOUND, "comment_submit_stale", "Nút gửi không còn khả dụng.")
    except Exception as exc:
        logger.warning(
            "Lỗi khi đăng bình luận: %s",
            security_logging.redact_sensitive(exc),
        )
        return action_result(ACTION_FAILED, "comment_interaction_error", "Không thể hoàn tất thao tác bình luận.")


async def action_subscribe_channel(page: Any) -> dict[str, str]:
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
                        const renderer = subBtn.closest('ytd-subscribe-button-renderer, subscribe-button-view-model');
                        const text = `${subBtn.innerText || ''} ${subBtn.getAttribute('aria-label') || ''}`.toLowerCase();
                        const subscribed = subBtn.getAttribute('aria-pressed') === 'true'
                            || renderer?.hasAttribute('subscribed')
                            || renderer?.getAttribute('is-subscribed') === 'true'
                            || text.includes('subscribed')
                            || text.includes('đã đăng ký')
                            || text.includes('unsubscribe')
                            || text.includes('hủy đăng ký');
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
            return action_result(ACTION_NOT_FOUND, "subscribe_button_missing", "Không tìm thấy nút đăng ký kênh.")

        if sub_info.get("subscribed"):
            logger.info("Kênh đã được đăng ký trước đó. Bỏ qua.")
            return action_result(ACTION_ALREADY_DONE, "already_subscribed", "Kênh đã được đăng ký trước đó.")

        matched_sel = sub_info.get("selector")
        btn = await page.query_selector(matched_sel) if matched_sel else None
        if btn:
            await btn.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await btn.click()
            try:
                await page.wait_for_function(
                    """() => {
                        const selectors = [
                            '[data-trust-builder-action="subscribe"]',
                            'ytd-watch-metadata #subscribe-button button',
                            'ytd-subscribe-button-renderer button',
                            'subscribe-button-view-model button',
                            '#subscribe-button-shape button'
                        ];
                        return selectors.some(selector => {
                            const node = document.querySelector(selector);
                            if (!node) return false;
                            const renderer = node.closest('ytd-subscribe-button-renderer, subscribe-button-view-model');
                            const text = `${node.innerText || ''} ${node.getAttribute('aria-label') || ''}`.toLowerCase();
                            return node.getAttribute('aria-pressed') === 'true'
                                || renderer?.hasAttribute('subscribed')
                                || renderer?.getAttribute('is-subscribed') === 'true'
                                || text.includes('subscribed')
                                || text.includes('đã đăng ký')
                                || text.includes('unsubscribe')
                                || text.includes('hủy đăng ký');
                        });
                    }""",
                    timeout=10000,
                )
            except Exception:
                logger.warning("YouTube chưa xác nhận trạng thái Subscribe sau khi click.")
                return action_result(
                    ACTION_UNCERTAIN,
                    "subscribe_confirmation_timeout",
                    "Đã click đăng ký nhưng YouTube chưa xác nhận; sẽ không click lại trên video này.",
                )
            logger.info("YouTube đã xác nhận kênh ở trạng thái Subscribed.")
            return action_result(ACTION_PERFORMED, "subscribe_confirmed", "YouTube đã xác nhận đăng ký kênh.")
        return action_result(ACTION_NOT_FOUND, "subscribe_button_stale", "Nút đăng ký không còn khả dụng.")
    except Exception as exc:
        logger.warning(
            "Lỗi khi bấm Subscribe: %s",
            security_logging.redact_sensitive(exc),
        )
        return action_result(ACTION_FAILED, "subscribe_interaction_error", "Không thể hoàn tất thao tác đăng ký kênh.")


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
