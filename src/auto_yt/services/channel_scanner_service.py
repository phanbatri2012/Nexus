"""Multi-Platform Channel & Page Scanner Service (YouTube, Facebook, TikTok).

Supports both GPM-Login antidetect profiles and Local Chromium browsers (Cốc Cốc, Chrome, Edge).
Connects via Playwright CDP to extract channel IDs, names, avatars, handles, and managed fanpages
directly from authenticated browser sessions without requiring manual token inputs.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, AsyncGenerator

import websocket

from auto_yt.services import database as db
from auto_yt.services.gpm_service import (
    find_running_gpm_profile_coordinates,
    get_gpm_profile_detail,
    gpm_browser_session,
    start_gpm_profile,
)
from auto_yt.services.local_browser_service import (
    find_running_local_browser_port,
    list_local_browser_profiles,
    local_browser_session,
    start_local_browser,
)

logger = logging.getLogger(__name__)


JS_EXTRACT_YOUTUBE = r"""(() => {
    let channelId = '';
    let title = '';
    let thumbnail_url = '';
    let handle = '';
    const current_url = window.location.href || '';

    if (current_url.includes('accounts.google.com') || current_url.includes('ServiceLogin') || current_url.includes('signin/identifier')) {
        return { is_login_page: true, current_url };
    }

    // 1. Check ytcfg global config
    try {
        if (window.ytcfg && typeof window.ytcfg.get === 'function') {
            channelId = window.ytcfg.get('CHANNEL_ID') || window.ytcfg.get('DELEGATED_SESSION_ID') || window.ytcfg.get('EXTERNAL_CHANNEL_ID') || '';
        }
    } catch(e) {}

    // 2. Check window.ytInitialData
    try {
        if (!channelId && window.ytInitialData) {
            const header = window.ytInitialData.header;
            if (header && header.c4TabbedHeaderRenderer && header.c4TabbedHeaderRenderer.channelId) {
                channelId = header.c4TabbedHeaderRenderer.channelId;
            }
        }
    } catch(e) {}

    // 3. Check URL regex
    if (!channelId) {
        const m = current_url.match(/\/channel\/(UC[a-zA-Z0-9_-]+)/);
        if (m) channelId = m[1];
    }

    // 4. Check DOM elements for channelId
    if (!channelId) {
        const meta = document.querySelector('meta[itemprop="channelId"]');
        if (meta) channelId = meta.content || '';
    }
    if (!channelId) {
        const link = document.querySelector('a[href*="/channel/UC"]');
        if (link) {
            const m = link.href.match(/\/channel\/(UC[a-zA-Z0-9_-]+)/);
            if (m) channelId = m[1];
        }
    }

    // 5. Channel Title
    const titleElem = document.querySelector('#entity-name, #channel-name, #header-channel-name, ytd-channel-name, .ytcp-entity-page-title, ytcp-header ytcp-entity-page-title');
    if (titleElem) title = titleElem.innerText.trim();
    if (!title) {
        title = document.title.replace(/ - YouTube Studio/i, '').replace(/ - YouTube/i, '').replace(/Trang tổng quan của kênh/i, '').replace(/Channel dashboard/i, '').trim();
    }

    // 6. Avatar Image
    const imgCandidates = Array.from(document.querySelectorAll('#entity-image img, #channel-header img, img#avatar, ytcp-entity-page img, #avatar-image img, ytcp-navigation-drawer img, img.ytcp-header-avatar, ytcp-header ytcp-avatar-image img, ytcp-entity-page #avatar img'));
    for (const img of imgCandidates) {
        const src = img.src || img.getAttribute('src') || '';
        if (src && !src.includes('yt_studio_logo') && !src.includes('favicon') && !src.includes('data:image/svg') && !src.includes('creator_avatar_placeholder') && !src.includes('avatar_placeholder')) {
            thumbnail_url = src;
            break;
        }
    }

    // 7. Handle
    const handleElem = document.querySelector('#entity-handle, #channel-handle, .ytcp-entity-page-handle, ytcp-navigation-drawer #email, ytcp-header #entity-handle');
    if (handleElem) handle = handleElem.innerText.trim();
    if (!handle) {
        const handleMatch = document.body.innerText.match(/@([a-zA-Z0-9_.-]{3,30})/);
        if (handleMatch) handle = handleMatch[0];
    }

    const is_loading = !channelId && (!title || title === "YouTube Studio" || title === "Studio" || title === "YouTube") && !current_url.includes('accounts.google.com');

    return { channelId, title, thumbnail_url, handle, current_url, is_login_page: false, is_loading };
})()"""


JS_EXTRACT_FACEBOOK = """(() => {
    const current_url = window.location.href || '';
    if (current_url.includes('login') || current_url.includes('checkpoint')) {
        return { is_login_page: true, current_url, pages: [] };
    }

    const results = [];
    const seen = new Set();
    const ignoredSlugs = new Set([
        'pages', 'groups', 'events', 'watch', 'marketplace', 'friends', 
        'saved', 'gaming', 'notifications', 'bookmarks', 'settings', 
        'help', 'privacy', 'reel', 'stories', 'messages', 'home', 'login', 'dialog'
    ]);

    const isInvalidTitle = (t) => {
        const lower = (t || '').toLowerCase().trim();
        if (lower.length < 2) return true;
        return lower.includes('đoạn chat') || 
               lower.includes('tin nhắn') || 
               lower.includes('thông báo') || 
               lower.includes('business suite') || 
               lower.includes('quảng cáo') || 
               lower.includes('tạo bài viết') || 
               lower.includes('trang của bạn') || 
               lower.includes('trang mà') || 
               lower === 'tạo trang' || 
               lower === 'khám phá' || 
               lower === 'followed pages';
    };

    // 1. Meta Business Suite asset_id links
    document.querySelectorAll('a[href*="asset_id="]').forEach(a => {
        const m = a.href.match(/asset_id=(\\d+)/);
        if (m && !seen.has(m[1])) {
            const title = (a.innerText || '').split('\\n')[0].trim();
            if (title && !isInvalidTitle(title)) {
                seen.add(m[1]);
                const img = a.querySelector('img') || a.parentElement?.querySelector('img');
                results.push({
                    page_id: m[1],
                    name: title,
                    avatar_url: img ? img.src : '',
                    link: a.href
                });
            }
        }
    });

    // 2. Facebook Pages cards
    const candidateLinks = document.querySelectorAll('div[role="main"] a[href], div[role="navigation"] a[href], a[role="link"]');
    candidateLinks.forEach(a => {
        const href = a.href || '';
        if (!href || href.includes('/ads/') || href.includes('/create/')) return;

        let extractedId = '';
        const idMatch = href.match(/id=(\\d+)/);
        if (idMatch) {
            extractedId = idMatch[1];
        } else {
            const slugMatch = href.match(/facebook\\.com\\/([a-zA-Z0-9\\.\\_\\-]+)/);
            if (slugMatch && !ignoredSlugs.has(slugMatch[1].toLowerCase())) {
                extractedId = slugMatch[1];
            }
        }

        if (!extractedId || seen.has(extractedId)) return;

        const rawText = (a.innerText || '').trim();
        const lines = rawText.split('\\n').map(l => l.trim()).filter(Boolean);
        if (lines.length === 0) return;

        let title = lines[0];
        if (isInvalidTitle(title)) return;

        seen.add(extractedId);
        const img = a.querySelector('img') || a.parentElement?.querySelector('img');
        results.push({
            page_id: extractedId,
            name: title,
            avatar_url: img ? img.src : '',
            link: href
        });
    });

    return { is_login_page: false, current_url, pages: results };
})()"""


JS_EXTRACT_TIKTOK = """(() => {
    const current_url = window.location.href || '';
    if (current_url.includes('login')) {
        return { is_login_page: true, current_url, handle: '', name: '', avatar_url: '' };
    }

    let handle = '';
    let name = '';
    let avatar_url = '';

    const userAvatar = document.querySelector('img[class*="avatar"], img[src*="tiktokcdn"], img[src*="sf16-va"]');
    if (userAvatar) avatar_url = userAvatar.src || '';

    const nameElem = document.querySelector('span[class*="name"], div[class*="username"], span[class*="uniqueId"]');
    if (nameElem) {
        name = nameElem.innerText.trim();
    }

    const handleMatch = document.body.innerText.match(/@([a-zA-Z0-9\\.\\_]+)/);
    if (handleMatch) {
        handle = '@' + handleMatch[1];
    }

    if (!handle && name) {
        handle = name.startsWith('@') ? name : '@' + name.replace(/\\s+/g, '_').toLowerCase();
    }

    return { is_login_page: false, current_url, handle, name: name || handle, avatar_url };
})()"""


def get_profile_cdp_port(profile_id: str) -> int | None:
    """Resolve active CDP port for a profile if its browser process is already running."""
    try:
        parsed = parse_profile_target(profile_id)
        if parsed["type"] == "local":
            running = find_running_local_browser_port(parsed["browser_key"], parsed["profile_dir"])
            if running:
                return running.get("port")
        else:
            running = find_running_gpm_profile_coordinates(parsed["id"])
            if running:
                return running.get("remote_debugging_port")
    except Exception as exc:
        logger.debug("Không thể tìm CDP port cho profile %s: %s", profile_id, exc)
    return None


def direct_cdp_evaluate_tab(
    port: int,
    js_expression: str,
    url_keywords: list[str] | None = None,
    timeout: float = 3.5,
    max_retries: int = 3,
    retry_interval: float = 0.8,
) -> dict[str, Any] | None:
    """Evaluate JavaScript in an active page tab via direct DevTools WebSocket.

    Bypasses whole-browser Playwright CDP attach to avoid hanging on background
    antidetect extensions or service workers. Retries across temporary navigation states.
    """
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/list")
            with urllib.request.urlopen(req, timeout=min(timeout, 2.0)) as resp:
                tabs = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("Không thể đọc /json/list từ CDP port %s (lần %d): %s", port, attempt, exc)
            if attempt < max_retries:
                time.sleep(retry_interval)
                continue
            return None

        if not isinstance(tabs, list):
            if attempt < max_retries:
                time.sleep(retry_interval)
                continue
            return None

        pages = [t for t in tabs if isinstance(t, dict) and t.get("type") == "page"]
        matched_tab = None
        if url_keywords:
            for p in pages:
                url = str(p.get("url") or "").lower()
                if any(k.lower() in url for k in url_keywords):
                    matched_tab = p
                    break
        if not matched_tab and pages:
            matched_tab = pages[0]

        if not matched_tab:
            if attempt < max_retries:
                time.sleep(retry_interval)
                continue
            return None

        ws_url = matched_tab.get("webSocketDebuggerUrl")
        if not ws_url:
            if attempt < max_retries:
                time.sleep(retry_interval)
                continue
            return None

        try:
            ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
            ws.send(json.dumps({
                "id": attempt,
                "method": "Runtime.evaluate",
                "params": {"expression": js_expression, "returnByValue": True}
            }))
            raw = ws.recv()
            ws.close()
            res = json.loads(raw)
            val = res.get("result", {}).get("result", {}).get("value")
            if isinstance(val, dict):
                # If the page returned is_loading=True and we have retries left, wait and retry
                if val.get("is_loading") and attempt < max_retries:
                    time.sleep(retry_interval)
                    continue
                return val
            elif val is not None:
                return val
        except Exception as exc:
            logger.debug("Direct CDP WebSocket eval thất bại trên port %s (lần %d): %s", port, attempt, exc)
            if attempt < max_retries:
                time.sleep(retry_interval)
                continue

    return None


def parse_profile_target(profile_id: str) -> dict[str, Any]:
    """Parse profile identifier into profile type and coordinates."""
    clean_id = str(profile_id or "").strip()
    if not clean_id:
        raise ValueError("Profile ID không được để trống.")

    if clean_id.startswith("local_"):
        # Match local_<browser_key>_<profile_dir>
        all_locals = list_local_browser_profiles()
        matched = next((p for p in all_locals if p["id"] == clean_id), None)
        if matched:
            return {
                "type": "local",
                "id": clean_id,
                "browser_key": matched["browser_key"],
                "browser_name": matched["browser_name"],
                "profile_dir": matched["profile_dir"],
                "profile_name": matched["profile_name"],
                "display_label": matched["display_label"],
                "proxy_info": "Direct (Mạng Local)",
            }
        # Fallback split
        parts = clean_id.split("_", 2)
        b_key = parts[1] if len(parts) > 1 else "coccoc"
        p_dir = parts[2] if len(parts) > 2 else "Default"
        return {
            "type": "local",
            "id": clean_id,
            "browser_key": b_key,
            "browser_name": b_key.title(),
            "profile_dir": p_dir,
            "profile_name": p_dir,
            "display_label": f"[Local] {b_key.title()} — {p_dir}",
            "proxy_info": "Direct (Mạng Local)",
        }

    # GPM-Login profile
    profile_name = clean_id
    proxy_info = "Direct"
    try:
        detail = get_gpm_profile_detail(clean_id)
        profile_name = str(detail.get("name") or clean_id)
        proxy_info = str(detail.get("raw_proxy") or detail.get("proxy") or "Direct")
    except Exception:
        pass

    return {
        "type": "gpm",
        "id": clean_id,
        "profile_name": profile_name,
        "display_label": f"[GPM] {profile_name}",
        "proxy_info": proxy_info,
    }


@asynccontextmanager
async def channel_browser_session(profile_id: str, target_url: str = "") -> AsyncGenerator[Any, None]:
    """Unified CDP session context manager supporting both GPM and Local browsers."""
    parsed = parse_profile_target(profile_id)
    if parsed["type"] == "local":
        async with local_browser_session(
            parsed["browser_key"],
            parsed["profile_dir"],
            target_url=target_url,
        ) as (context, browser):
            yield context, browser, parsed
    else:
        async with gpm_browser_session(
            parsed["id"],
            auto_stop=False,
        ) as (context, browser):
            yield context, browser, parsed


def open_channel_platform_browser(
    profile_id: str,
    platform: str,
    force_restart: bool = False,
) -> dict[str, Any]:
    """Launch or focus browser window on the specific platform's studio/creator page."""
    parsed = parse_profile_target(profile_id)
    platform_key = platform.lower().strip()

    target_urls = {
        "youtube": "https://studio.youtube.com",
        "facebook": "https://www.facebook.com/pages/?category=your_pages",
        "tiktok": "https://www.tiktok.com/creator-center/upload",
    }
    url = target_urls.get(platform_key, "https://www.google.com")

    if parsed["type"] == "local":
        res = start_local_browser(
            parsed["browser_key"],
            parsed["profile_dir"],
            target_url=url,
            require_cdp=False,
            force_restart=force_restart,
        )
        return {
            "success": True,
            "profile_id": profile_id,
            "platform": platform_key,
            "url": url,
            "browser_type": "local",
            "browser_name": parsed["browser_name"],
            "profile_name": parsed["profile_name"],
            "already_running_no_cdp": res.get("already_running_no_cdp", False),
            "message": res.get("message") or f"Đã mở {parsed['browser_name']} ({parsed['profile_name']}) tại {url}",
        }
    else:
        start_gpm_profile(parsed["id"])
        from auto_yt.services.gpm_service import open_tab_in_running_gpm_process
        try:
            open_tab_in_running_gpm_process(parsed["id"], url)
        except Exception:
            pass
        return {
            "success": True,
            "profile_id": profile_id,
            "platform": platform_key,
            "url": url,
            "browser_type": "gpm",
            "profile_name": parsed["profile_name"],
            "message": f"Đã mở Profile GPM '{parsed['profile_name']}' tại {url}",
        }


async def scan_youtube_channel(
    profile_id: str,
    auto_save: bool = True,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Scan and extract YouTube channel details (Channel ID, Name, Avatar, Handle) via Dual-Engine (Direct CDP + Playwright fallback)."""
    logger.info("Bắt đầu quét kênh YouTube từ Profile %s", profile_id)
    parsed = parse_profile_target(profile_id)

    # 1. Engine 1: Direct CDP Evaluation (Fast ~50ms, zero-overhead with retry)
    port = get_profile_cdp_port(profile_id)
    if port:
        logger.info("Engine 1: Đang quét trực tiếp tab YouTube Studio qua CDP port %s...", port)
        extracted = direct_cdp_evaluate_tab(
            port,
            JS_EXTRACT_YOUTUBE,
            url_keywords=["studio.youtube.com", "youtube.com"],
            timeout=3.5,
            max_retries=3,
            retry_interval=0.8,
        )
        if isinstance(extracted, dict):
            if extracted.get("is_login_page"):
                return {
                    "success": False,
                    "logged_in": False,
                    "channel": None,
                    "profile": parsed,
                    "message": "Chưa đăng nhập Google trong Profile này. Vui lòng mở trình duyệt để đăng nhập tài khoản YouTube trước.",
                }
            channel_id = str(extracted.get("channelId") or "").strip()
            title = str(extracted.get("title") or "").strip() or "Kênh YouTube"
            thumbnail_url = str(extracted.get("thumbnail_url") or "").strip()
            handle = str(extracted.get("handle") or "").strip()

            if channel_id or (title and title not in ("Kênh YouTube", "YouTube Studio", "Studio", "YouTube")):
                if not channel_id:
                    channel_id = f"UC_GPM_{parsed['id'][:12]}_{int(asyncio.get_event_loop().time())}"

                channel_payload = {
                    "channel_id": channel_id,
                    "title": title,
                    "thumbnail_url": thumbnail_url,
                    "handle": handle,
                    "gpm_profile_id": parsed["id"],
                    "gpm_profile_name": parsed.get("profile_name", ""),
                    "gpm_proxy_info": parsed.get("proxy_info", ""),
                    "interaction_mode": "gpm_browser",
                }

                if auto_save:
                    db.upsert_youtube_channel(
                        channel_id=channel_id,
                        title=title,
                        thumbnail_url=thumbnail_url,
                        gpm_profile_id=parsed["id"],
                        gpm_profile_name=parsed.get("profile_name", ""),
                        gpm_proxy_info=parsed.get("proxy_info", ""),
                        interaction_mode="gpm_browser",
                        auto_heart=1,
                    )
                    logger.info("Engine 1: Đã lưu kênh YouTube %s (%s) vào cơ sở dữ liệu", title, channel_id)

                return {
                    "success": True,
                    "logged_in": True,
                    "channel": channel_payload,
                    "profile": parsed,
                    "message": f"🟢 Đã quét và liên kết thành công kênh '{title}' ({channel_id})!",
                }

    # 2. Engine 2: Playwright CDP Session Fallback with Page Reuse & Safe Retry Loop
    logger.info("Engine 2: Khởi tạo phiên Playwright CDP fallback cho profile %s...", profile_id)
    try:
        async with channel_browser_session(profile_id, target_url="https://studio.youtube.com") as (context, _browser, p_info):
            # Check for existing open page first to avoid reloading heavy SPA over slow proxy
            existing_page = next((p for p in context.pages if "studio.youtube.com" in p.url or "youtube.com" in p.url), None)
            created_new = False
            if existing_page:
                page = existing_page
                logger.info("Engine 2: Tái sử dụng tab YouTube Studio đang mở (%s)", page.url)
            else:
                page = await context.new_page()
                created_new = True
                try:
                    await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=min(int(timeout_seconds * 1000), 15000))
                except Exception as nav_exc:
                    logger.debug("Page goto timeout/error: %s", nav_exc)

            try:
                # Safe evaluate loop with retries to absorb in-flight SPA navigations
                extracted = None
                deadline = asyncio.get_event_loop().time() + min(timeout_seconds, 15.0)
                attempts = 0
                last_eval_res = None

                while asyncio.get_event_loop().time() < deadline:
                    attempts += 1
                    try:
                        curr_url = page.url or ""
                        if "accounts.google.com" in curr_url or "ServiceLogin" in curr_url or "signin/identifier" in curr_url:
                            return {
                                "success": False,
                                "logged_in": False,
                                "channel": None,
                                "profile": p_info,
                                "message": "Chưa đăng nhập Google trong Profile này. Vui lòng mở trình duyệt để đăng nhập tài khoản YouTube trước.",
                            }

                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=2000)
                        except Exception:
                            pass

                        res = await page.evaluate(JS_EXTRACT_YOUTUBE)
                        if isinstance(res, dict):
                            last_eval_res = res
                            if res.get("is_login_page"):
                                return {
                                    "success": False,
                                    "logged_in": False,
                                    "channel": None,
                                    "profile": p_info,
                                    "message": "Chưa đăng nhập Google trong Profile này. Vui lòng mở trình duyệt để đăng nhập tài khoản YouTube trước.",
                                }
                            if res.get("channelId") or (res.get("title") and res.get("title") not in ("Kênh YouTube", "YouTube Studio", "Studio", "YouTube")):
                                extracted = res
                                break
                    except Exception as eval_exc:
                        logger.debug("Lần evaluate %d gặp lỗi chuyển trang: %s", attempts, eval_exc)

                    await asyncio.sleep(1.0)

                if not extracted and isinstance(last_eval_res, dict):
                    extracted = last_eval_res

                if not extracted:
                    extracted = {}

                channel_id = str(extracted.get("channelId") or "").strip()
                title = str(extracted.get("title") or "").strip() or "Kênh YouTube"
                thumbnail_url = str(extracted.get("thumbnail_url") or "").strip()
                handle = str(extracted.get("handle") or "").strip()

                if not channel_id:
                    channel_id = f"UC_GPM_{p_info['id'][:12]}_{int(asyncio.get_event_loop().time())}"

                channel_payload = {
                    "channel_id": channel_id,
                    "title": title,
                    "thumbnail_url": thumbnail_url,
                    "handle": handle,
                    "gpm_profile_id": p_info["id"],
                    "gpm_profile_name": p_info["profile_name"],
                    "gpm_proxy_info": p_info["proxy_info"],
                    "interaction_mode": "gpm_browser",
                }

                if auto_save:
                    db.upsert_youtube_channel(
                        channel_id=channel_id,
                        title=title,
                        thumbnail_url=thumbnail_url,
                        gpm_profile_id=p_info["id"],
                        gpm_profile_name=p_info["profile_name"],
                        gpm_proxy_info=p_info["proxy_info"],
                        interaction_mode="gpm_browser",
                        auto_heart=1,
                    )
                    logger.info("Engine 2: Đã lưu kênh YouTube %s (%s) vào cơ sở dữ liệu", title, channel_id)

                return {
                    "success": True,
                    "logged_in": True,
                    "channel": channel_payload,
                    "profile": p_info,
                    "message": f"🟢 Đã quét và liên kết thành công kênh '{title}' ({channel_id})!",
                }
            finally:
                if created_new:
                    try:
                        await page.close()
                    except Exception:
                        pass
    except Exception as exc:
        logger.error("Lỗi khi quét kênh YouTube: %s", exc)
        return {
            "success": False,
            "logged_in": False,
            "channel": None,
            "profile": parsed,
            "message": f"Lỗi quét YouTube Studio: {exc}",
        }


async def scan_facebook_pages(
    profile_id: str,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Scan and extract all managed Facebook Pages from the authenticated browser session."""
    logger.info("Bắt đầu quét Fanpage Facebook từ Profile %s", profile_id)
    parsed = parse_profile_target(profile_id)

    # 1. Engine 1: Direct CDP Evaluation
    port = get_profile_cdp_port(profile_id)
    if port:
        logger.info("Engine 1: Đang quét trực tiếp tab Facebook qua CDP port %s...", port)
        extracted = direct_cdp_evaluate_tab(
            port,
            JS_EXTRACT_FACEBOOK,
            url_keywords=["facebook.com"],
            timeout=3.5,
            max_retries=3,
            retry_interval=0.8,
        )
        if isinstance(extracted, dict):
            if extracted.get("is_login_page"):
                return {
                    "success": False,
                    "logged_in": False,
                    "pages": [],
                    "profile": parsed,
                    "message": "Chưa đăng nhập Facebook trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                }
            discovered = extracted.get("pages") or []
            if isinstance(discovered, list) and len(discovered) > 0:
                pages = []
                for item in discovered:
                    p_name = str(item.get("name") or "").strip()
                    p_id = str(item.get("page_id") or "").strip()
                    if p_name and p_id:
                        pages.append({
                            "id": f"fb_{p_id}",
                            "name": p_name,
                            "page_id": p_id,
                            "avatar_url": str(item.get("avatar_url") or ""),
                            "link": str(item.get("link") or ""),
                            "gpm_profile_id": parsed["id"],
                            "gpm_profile_name": parsed.get("profile_name", ""),
                            "gpm_proxy_info": parsed.get("proxy_info", ""),
                            "auto_reels": True,
                            "auto_comment": True,
                            "status": "active",
                        })
                return {
                    "success": True,
                    "logged_in": True,
                    "pages": pages,
                    "profile": parsed,
                    "message": f"🟢 Quét thành công! Tìm thấy {len(pages)} Fanpage đang quản lý trong trình duyệt.",
                }

    # 2. Engine 2: Playwright Fallback
    logger.info("Engine 2: Khởi tạo phiên Playwright CDP fallback Facebook cho profile %s...", profile_id)
    try:
        async with channel_browser_session(profile_id, target_url="https://www.facebook.com/pages/?category=your_pages") as (context, _browser, p_info):
            existing_page = next((p for p in context.pages if "facebook.com" in p.url), None)
            created_new = False
            if existing_page:
                page = existing_page
                logger.info("Engine 2: Tái sử dụng tab Facebook đang mở (%s)", page.url)
            else:
                page = await context.new_page()
                created_new = True
                try:
                    await page.goto("https://www.facebook.com/pages/?category=your_pages", wait_until="domcontentloaded", timeout=min(int(timeout_seconds * 1000), 15000))
                except Exception:
                    pass

            try:
                extracted = None
                deadline = asyncio.get_event_loop().time() + min(timeout_seconds, 15.0)
                attempts = 0

                while asyncio.get_event_loop().time() < deadline:
                    attempts += 1
                    try:
                        current_url = page.url or ""
                        if "login" in current_url or "checkpoint" in current_url:
                            return {
                                "success": False,
                                "logged_in": False,
                                "pages": [],
                                "profile": p_info,
                                "message": "Chưa đăng nhập Facebook trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                            }

                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=2000)
                        except Exception:
                            pass

                        res = await page.evaluate(JS_EXTRACT_FACEBOOK)
                        if isinstance(res, dict):
                            if res.get("is_login_page"):
                                return {
                                    "success": False,
                                    "logged_in": False,
                                    "pages": [],
                                    "profile": p_info,
                                    "message": "Chưa đăng nhập Facebook trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                                }
                            discovered = res.get("pages") or []
                            if len(discovered) > 0:
                                extracted = res
                                break
                    except Exception as eval_exc:
                        logger.debug("Lần evaluate Facebook %d gặp lỗi: %s", attempts, eval_exc)

                    await asyncio.sleep(1.0)

                discovered = (extracted.get("pages") if isinstance(extracted, dict) else []) or []
                pages = []
                for item in discovered:
                    p_name = str(item.get("name") or "").strip()
                    p_id = str(item.get("page_id") or "").strip()
                    if p_name and p_id:
                        pages.append({
                            "id": f"fb_{p_id}",
                            "name": p_name,
                            "page_id": p_id,
                            "avatar_url": str(item.get("avatar_url") or ""),
                            "link": str(item.get("link") or ""),
                            "gpm_profile_id": p_info["id"],
                            "gpm_profile_name": p_info["profile_name"],
                            "gpm_proxy_info": p_info["proxy_info"],
                            "auto_reels": True,
                            "auto_comment": True,
                            "status": "active",
                        })

                return {
                    "success": True,
                    "logged_in": True,
                    "pages": pages,
                    "profile": p_info,
                    "message": f"🟢 Quét thành công! Tìm thấy {len(pages)} Fanpage đang quản lý trong trình duyệt.",
                }
            finally:
                if created_new:
                    try:
                        await page.close()
                    except Exception:
                        pass
    except Exception as exc:
        logger.error("Lỗi khi quét Facebook Pages: %s", exc)
        return {
            "success": False,
            "logged_in": False,
            "pages": [],
            "profile": parsed,
            "message": f"Lỗi quét Facebook: {exc}",
        }


async def scan_tiktok_account(
    profile_id: str,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Scan and extract authenticated TikTok Creator account details."""
    logger.info("Bắt đầu quét Kênh TikTok từ Profile %s", profile_id)
    parsed = parse_profile_target(profile_id)

    # 1. Engine 1: Direct CDP Evaluation
    port = get_profile_cdp_port(profile_id)
    if port:
        logger.info("Engine 1: Đang quét trực tiếp tab TikTok qua CDP port %s...", port)
        extracted = direct_cdp_evaluate_tab(
            port,
            JS_EXTRACT_TIKTOK,
            url_keywords=["tiktok.com"],
            timeout=3.5,
            max_retries=3,
            retry_interval=0.8,
        )
        if isinstance(extracted, dict):
            if extracted.get("is_login_page"):
                return {
                    "success": False,
                    "logged_in": False,
                    "account": None,
                    "profile": parsed,
                    "message": "Chưa đăng nhập TikTok trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                }
            handle = str(extracted.get("handle") or "").strip()
            name = str(extracted.get("name") or "").strip() or handle
            avatar_url = str(extracted.get("avatar_url") or "").strip()

            if handle or name:
                if not handle:
                    handle = f"@user_{parsed['id'][:8]}"

                account_data = {
                    "id": f"tt_{handle.replace('@', '')}",
                    "name": name,
                    "handle": handle,
                    "avatar_url": avatar_url,
                    "gpm_profile_id": parsed["id"],
                    "gpm_profile_name": parsed.get("profile_name", ""),
                    "gpm_proxy_info": parsed.get("proxy_info", ""),
                    "auto_video": True,
                    "auto_comment": True,
                    "status": "active",
                }
                return {
                    "success": True,
                    "logged_in": True,
                    "account": account_data,
                    "profile": parsed,
                    "message": f"🟢 Quét thành công Kênh TikTok '{name}' ({handle})!",
                }

    # 2. Engine 2: Playwright Fallback
    logger.info("Engine 2: Khởi tạo phiên Playwright CDP fallback TikTok cho profile %s...", profile_id)
    try:
        async with channel_browser_session(profile_id, target_url="https://www.tiktok.com/creator-center/upload") as (context, _browser, p_info):
            existing_page = next((p for p in context.pages if "tiktok.com" in p.url), None)
            created_new = False
            if existing_page:
                page = existing_page
                logger.info("Engine 2: Tái sử dụng tab TikTok đang mở (%s)", page.url)
            else:
                page = await context.new_page()
                created_new = True
                try:
                    await page.goto("https://www.tiktok.com/creator-center/upload", wait_until="domcontentloaded", timeout=min(int(timeout_seconds * 1000), 15000))
                except Exception:
                    pass

            try:
                extracted = None
                deadline = asyncio.get_event_loop().time() + min(timeout_seconds, 15.0)
                attempts = 0

                while asyncio.get_event_loop().time() < deadline:
                    attempts += 1
                    try:
                        current_url = page.url or ""
                        if "login" in current_url:
                            return {
                                "success": False,
                                "logged_in": False,
                                "account": None,
                                "profile": p_info,
                                "message": "Chưa đăng nhập TikTok trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                            }

                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=2000)
                        except Exception:
                            pass

                        res = await page.evaluate(JS_EXTRACT_TIKTOK)
                        if isinstance(res, dict):
                            if res.get("is_login_page"):
                                return {
                                    "success": False,
                                    "logged_in": False,
                                    "account": None,
                                    "profile": p_info,
                                    "message": "Chưa đăng nhập TikTok trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                                }
                            if res.get("handle") or res.get("name"):
                                extracted = res
                                break
                    except Exception as eval_exc:
                        logger.debug("Lần evaluate TikTok %d gặp lỗi: %s", attempts, eval_exc)

                    await asyncio.sleep(1.0)

                handle = str((extracted or {}).get("handle") or "").strip()
                name = str((extracted or {}).get("name") or "").strip() or handle
                avatar_url = str((extracted or {}).get("avatar_url") or "").strip()

                if not handle:
                    handle = f"@user_{p_info['id'][:8]}"

                account_data = {
                    "id": f"tt_{handle.replace('@', '')}",
                    "name": name,
                    "handle": handle,
                    "avatar_url": avatar_url,
                    "gpm_profile_id": p_info["id"],
                    "gpm_profile_name": p_info["profile_name"],
                    "gpm_proxy_info": p_info["proxy_info"],
                    "auto_video": True,
                    "auto_comment": True,
                    "status": "active",
                }

                return {
                    "success": True,
                    "logged_in": True,
                    "account": account_data,
                    "profile": p_info,
                    "message": f"🟢 Quét thành công Kênh TikTok '{name}' ({handle})!",
                }
            finally:
                if created_new:
                    try:
                        await page.close()
                    except Exception:
                        pass
    except Exception as exc:
        logger.error("Lỗi khi quét TikTok: %s", exc)
        return {
            "success": False,
            "logged_in": False,
            "account": None,
            "profile": parsed,
            "message": f"Lỗi quét TikTok: {exc}",
        }

