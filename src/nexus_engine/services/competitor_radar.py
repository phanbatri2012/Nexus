"""Competitor channel discovery, isolated feed retrieval, and scan scheduling."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nexus_engine.services import channel_scanner_service
from nexus_engine.services.proxy_utils import create_proxy_opener, parse_proxy_url


BASELINE_VIDEO_COUNT = 15
PREDICTED_WINDOW_MINUTES = 60
PREDICTED_SCAN_SECONDS = 2 * 60
COLD_START_SCAN_SECONDS = 5 * 60
OFF_WINDOW_SCAN_SECONDS = 20 * 60
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_CHANNEL_PAGE_BYTES = 4 * 1024 * 1024
MAX_VIDEO_PAGE_BYTES = 2 * 1024 * 1024
LONG_FORM_MIN_SECONDS = 180
DISCOVERY_CANDIDATE_LIMIT = 60
RSS_RETRY_DELAYS_SECONDS = (2.0, 5.0)
RETRYABLE_HTTP_STATUSES = {404, 408, 425, 429, 500, 502, 503, 504}
RADAR_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
CHANNEL_ID_PATTERN = re.compile(r"\bUC[A-Za-z0-9_-]{20,30}\b")
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


class CompetitorRadarError(RuntimeError):
    """Base error for deterministic competitor Radar failures."""


class CompetitorResolutionError(CompetitorRadarError):
    """Raised when a competitor reference cannot be resolved through GPM."""


class CompetitorSourceUnavailable(CompetitorRadarError):
    """Raised when one isolated discovery source cannot provide usable data."""

    def __init__(self, source: str, message: str, *, status_code: int = 0):
        super().__init__(message)
        self.source = str(source or "unknown")
        self.status_code = int(status_code or 0)


def normalize_competitor_reference(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError("Vui lòng nhập URL, handle hoặc Channel ID của đối thủ.")
    if CHANNEL_ID_PATTERN.fullmatch(cleaned):
        return f"https://www.youtube.com/channel/{cleaned}"
    if cleaned.startswith("@"):
        handle = cleaned[1:].strip()
        if not handle or not re.fullmatch(r"[A-Za-z0-9._-]+", handle):
            raise ValueError("Handle YouTube không hợp lệ.")
        return f"https://www.youtube.com/@{handle}"
    if "://" not in cleaned:
        cleaned = "https://" + cleaned
    parsed = urllib.parse.urlsplit(cleaned)
    host = (parsed.hostname or "").casefold()
    if host not in YOUTUBE_HOSTS:
        raise ValueError("Radar chỉ chấp nhận URL kênh youtube.com.")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if not (
        path.startswith("/@")
        or path.startswith("/channel/")
        or path.startswith("/c/")
        or path.startswith("/user/")
    ):
        raise ValueError("URL phải trỏ tới một kênh YouTube.")
    return urllib.parse.urlunsplit(("https", "www.youtube.com", path.rstrip("/"), "", ""))


async def resolve_competitor_channel(reference: str, gpm_profile_id: str) -> dict[str, str]:
    """Resolve a channel only through the target channel's authenticated GPM profile."""
    target_url = normalize_competitor_reference(reference)
    profile_id = str(gpm_profile_id or "").strip()
    if not profile_id:
        raise CompetitorResolutionError("Kênh đích chưa có GPM profile; Radar đã dừng an toàn.")

    async with channel_scanner_service.channel_browser_session(
        profile_id,
        target_url=target_url,
        timeout_seconds=45,
    ) as (context, _browser, _profile):
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(800)
        current_url = str(page.url or target_url)
        html = await page.content()
        title = str(await page.title() or "").replace(" - YouTube", "").strip()
        canonical = await page.locator('link[rel="canonical"]').get_attribute("href")
        thumbnail = await page.locator('meta[property="og:image"]').get_attribute("content")
        handle = ""
        parsed_path = urllib.parse.urlsplit(str(canonical or current_url)).path
        handle_match = re.search(r"/@([^/?#]+)", parsed_path)
        if handle_match:
            handle = "@" + urllib.parse.unquote(handle_match.group(1))

    channel_id = ""
    if canonical:
        canonical_match = CHANNEL_ID_PATTERN.search(canonical)
        if canonical_match:
            channel_id = canonical_match.group(0)

    if not channel_id:
        url_match = CHANNEL_ID_PATTERN.search(current_url) or CHANNEL_ID_PATTERN.search(target_url)
        if url_match:
            channel_id = url_match.group(0)

    if not channel_id:
        ext_match = re.search(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{20,30})"', html)
        if ext_match:
            channel_id = ext_match.group(1)

    if not channel_id:
        for pattern in (
            r'/channel/(UC[A-Za-z0-9_-]{20,30})',
            r'<meta itemprop="channelId" content="(UC[A-Za-z0-9_-]{20,30})"',
            r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,30})"',
        ):
            match = re.search(pattern, html)
            if match:
                channel_id = match.group(1)
                break
    if not channel_id:
        raise CompetitorResolutionError(
            "Không tìm thấy Channel ID trong trang YouTube qua GPM."
        )
    return {
        "channel_id": channel_id,
        "canonical_url": f"https://www.youtube.com/channel/{channel_id}",
        "handle": handle,
        "title": title or handle or channel_id,
        "thumbnail_url": str(thumbnail or ""),
    }


def _text(node: ET.Element | None) -> str:
    return str(node.text or "").strip() if node is not None else ""


def parse_channel_feed(xml_bytes: bytes, *, limit: int = BASELINE_VIDEO_COUNT) -> list[dict[str, str]]:
    if not xml_bytes:
        raise CompetitorRadarError("RSS đối thủ trả về nội dung rỗng.")
    if len(xml_bytes) > MAX_FEED_BYTES:
        raise CompetitorRadarError("RSS đối thủ vượt giới hạn kích thước an toàn.")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise CompetitorRadarError("RSS đối thủ không phải XML hợp lệ.") from exc
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    videos: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", namespaces):
        video_id = _text(entry.find("yt:videoId", namespaces))
        if not video_id:
            continue
        link_node = entry.find("atom:link[@rel='alternate']", namespaces)
        link = str(link_node.attrib.get("href") or "") if link_node is not None else ""
        videos.append(
            {
                "youtube_video_id": video_id,
                "url": link or f"https://www.youtube.com/watch?v={video_id}",
                "title": _text(entry.find("atom:title", namespaces)),
                "published_at": _text(entry.find("atom:published", namespaces)),
                "updated_at": _text(entry.find("atom:updated", namespaces)),
            }
        )
        if len(videos) >= max(1, int(limit)):
            break
    if not videos:
        raise CompetitorRadarError("RSS đối thủ không chứa video hợp lệ.")
    return videos


def fetch_channel_feed(
    channel_id: str,
    raw_proxy: str,
    *,
    timeout_seconds: float = 30.0,
    limit: int = BASELINE_VIDEO_COUNT,
    retry_delays: Iterable[float] = RSS_RETRY_DELAYS_SECONDS,
) -> list[dict[str, str]]:
    """Fetch an RSS feed through the assigned proxy, with no direct fallback."""
    clean_channel_id = str(channel_id or "").strip()
    if not CHANNEL_ID_PATTERN.fullmatch(clean_channel_id):
        raise CompetitorRadarError("Channel ID đối thủ không hợp lệ.")
    if not parse_proxy_url(raw_proxy):
        raise CompetitorRadarError("Kênh đích chưa có proxy hợp lệ; Radar đã dừng an toàn.")
    opener = create_proxy_opener(raw_proxy, require_proxy=True)
    url = (
        "https://www.youtube.com/feeds/videos.xml?channel_id="
        + urllib.parse.quote(clean_channel_id, safe="")
    )
    delays = tuple(max(0.0, float(value)) for value in retry_delays)
    last_status = 0
    last_error = ""
    for attempt in range(len(delays) + 1):
        request = urllib.request.Request(
            url,
                headers={"User-Agent": "Nexus-Studio-Engine-Competitor-Radar/1.0"},
            method="GET",
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                content_type = str(response.headers.get("Content-Type") or "").casefold()
                if content_type and "xml" not in content_type and "atom" not in content_type:
                    raise CompetitorSourceUnavailable(
                        "rss", "YouTube RSS trả về kiểu nội dung không hợp lệ."
                    )
                data = response.read(MAX_FEED_BYTES + 1)
            return parse_channel_feed(data, limit=limit)
        except urllib.error.HTTPError as exc:
            last_status = int(exc.code or 0)
            last_error = f"HTTP {last_status}"
            try:
                exc.read(64 * 1024)
            finally:
                exc.close()
            if last_status not in RETRYABLE_HTTP_STATUSES:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        if attempt < len(delays):
            time.sleep(delays[attempt])
    detail = f" ({last_error})" if last_error else ""
    raise CompetitorSourceUnavailable(
        "rss",
        "YouTube RSS chưa khả dụng sau "
        f"{len(delays) + 1} lần thử{detail}; Radar sẽ chuyển nguồn.",
        status_code=last_status,
    )


def _read_proxied_page(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": RADAR_BROWSER_USER_AGENT,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            if content_type and "html" not in content_type:
                raise CompetitorSourceUnavailable(
                    "proxy_html", "YouTube trả về kiểu nội dung trang không hợp lệ."
                )
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        try:
            exc.read(64 * 1024)
        finally:
            exc.close()
        raise CompetitorSourceUnavailable(
            "proxy_html",
            f"Trang Videos của YouTube chưa khả dụng (HTTP {status}).",
            status_code=status,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CompetitorSourceUnavailable(
            "proxy_html", "Không thể đọc trang Videos qua proxy kênh đích."
        ) from exc
    if len(data) > max_bytes:
        raise CompetitorSourceUnavailable(
            "proxy_html", "Trang YouTube vượt giới hạn kích thước an toàn."
        )
    return data


def extract_channel_video_ids(html_bytes: bytes, channel_id: str) -> list[str]:
    """Extract ordered candidates from the channel Videos page only."""
    text = html_bytes.decode("utf-8", errors="replace")
    clean_channel_id = str(channel_id or "").strip()
    if clean_channel_id and clean_channel_id not in text:
        raise CompetitorSourceUnavailable(
            "proxy_html", "Trang Videos không khớp Channel ID đối thủ."
        )
    result: list[str] = []
    for video_id in re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', text):
        if video_id not in result:
            result.append(video_id)
        if len(result) >= DISCOVERY_CANDIDATE_LIMIT:
            break
    return result


def _json_text(value: str) -> str:
    try:
        return str(json.loads(f'"{value}"'))
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(value or "")


def _first_json_string(text: str, key: str) -> str:
    match = re.search(
        rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', text
    )
    return _json_text(match.group(1)).strip() if match else ""


def _extract_json_object(text: str, marker: str) -> dict:
    marker_index = text.find(marker)
    if marker_index < 0:
        return {}
    start = text.find("{", marker_index + len(marker))
    if start < 0:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except (TypeError, ValueError, json.JSONDecodeError):
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def _initial_player_response(text: str) -> dict:
    for marker in (
        "var ytInitialPlayerResponse =",
        "ytInitialPlayerResponse =",
        'window["ytInitialPlayerResponse"] =',
    ):
        value = _extract_json_object(text, marker)
        if value:
            return value
    return {}


def _known_video_record(
    row: dict,
    *,
    discovery_source: str,
    feed_item: dict | None,
) -> dict:
    published_at = str((feed_item or {}).get("published_at") or row.get("published_at") or "")
    confidence = (
        "exact" if (feed_item or {}).get("published_at") else str(row.get("published_at_confidence") or "unknown")
    )
    return {
        "youtube_video_id": str(row.get("youtube_video_id") or ""),
        "url": str(row.get("url") or ""),
        "title": str((feed_item or {}).get("title") or row.get("title") or ""),
        "published_at": published_at,
        "updated_at": str((feed_item or {}).get("updated_at") or ""),
        "published_at_confidence": confidence,
        "content_type": str(row.get("content_type") or "unknown"),
        "duration_seconds": int(row.get("duration_seconds") or 0),
        "eligibility_status": str(row.get("eligibility_status") or "metadata_pending"),
        "exclusion_reason": str(row.get("exclusion_reason") or ""),
        "discovery_source": discovery_source,
        "metadata_checked_at": str(row.get("metadata_checked_at") or ""),
    }


def _pending_video_record(
    video_id: str,
    *,
    discovery_source: str,
    feed_item: dict | None = None,
) -> dict:
    feed = feed_item or {}
    return {
        "youtube_video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": str(feed.get("title") or ""),
        "published_at": str(feed.get("published_at") or ""),
        "updated_at": str(feed.get("updated_at") or ""),
        "published_at_confidence": "exact" if feed.get("published_at") else "unknown",
        "content_type": "unknown",
        "duration_seconds": 0,
        "eligibility_status": "metadata_pending",
        "exclusion_reason": "metadata_unavailable",
        "discovery_source": discovery_source,
        "metadata_checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def parse_watch_video_metadata(
    html_bytes: bytes,
    *,
    video_id: str,
    expected_channel_id: str,
    discovery_source: str,
    feed_item: dict | None = None,
) -> dict:
    """Classify one candidate conservatively; unknown metadata is never eligible."""
    text = html_bytes.decode("utf-8", errors="replace")
    player = _initial_player_response(text)
    details = player.get("videoDetails") if isinstance(player.get("videoDetails"), dict) else {}
    microformat_root = player.get("microformat") if isinstance(player.get("microformat"), dict) else {}
    microformat = (
        microformat_root.get("playerMicroformatRenderer")
        if isinstance(microformat_root.get("playerMicroformatRenderer"), dict)
        else {}
    )
    playability = (
        player.get("playabilityStatus")
        if isinstance(player.get("playabilityStatus"), dict)
        else {}
    )
    owner_channel_id = str(
        details.get("channelId")
        or microformat.get("externalChannelId")
        or _first_json_string(text, "externalChannelId")
    ).strip()
    duration_raw = str(details.get("lengthSeconds") or _first_json_string(text, "lengthSeconds"))
    duration_seconds = int(duration_raw) if duration_raw.isdigit() else 0
    title = str(
        (feed_item or {}).get("title")
        or details.get("title")
        or _first_json_string(text, "title")
    )
    published_at = str(
        (feed_item or {}).get("published_at")
        or microformat.get("publishDate")
        or microformat.get("uploadDate")
        or _first_json_string(text, "publishDate")
        or _first_json_string(text, "uploadDate")
    )
    confidence = (
        "exact"
        if (feed_item or {}).get("published_at") or "T" in published_at
        else ("estimated" if published_at else "unknown")
    )
    live_details = microformat.get("liveBroadcastDetails")
    is_live = bool(details.get("isLiveContent"))
    was_live = isinstance(live_details, dict)
    is_upcoming = bool(
        details.get("isUpcoming")
        or playability.get("status") == "LIVE_STREAM_OFFLINE"
        or isinstance(microformat.get("upcomingEventData"), dict)
    )
    is_premiere = bool(details.get("isPremiere") or microformat.get("isPremiere"))
    is_short = bool(details.get("isShort"))

    content_type = "unknown"
    eligibility_status = "metadata_pending"
    exclusion_reason = "metadata_incomplete"
    if owner_channel_id and owner_channel_id != expected_channel_id:
        eligibility_status = "excluded"
        exclusion_reason = "channel_mismatch"
    elif is_upcoming:
        content_type = "upcoming"
        eligibility_status = "excluded"
        exclusion_reason = "upcoming"
    elif is_premiere:
        content_type = "premiere"
        eligibility_status = "excluded"
        exclusion_reason = "premiere"
    elif is_live:
        content_type = "live"
        eligibility_status = "excluded"
        exclusion_reason = "live"
    elif was_live:
        content_type = "live_replay"
        eligibility_status = "excluded"
        exclusion_reason = "live_replay"
    elif not owner_channel_id:
        exclusion_reason = "owner_metadata_missing"
    elif is_short or (duration_seconds and duration_seconds < LONG_FORM_MIN_SECONDS):
        content_type = "short"
        eligibility_status = "excluded"
        exclusion_reason = "duration_below_180_seconds"
    elif duration_seconds >= LONG_FORM_MIN_SECONDS and owner_channel_id == expected_channel_id:
        content_type = "long_form"
        eligibility_status = "eligible"
        exclusion_reason = ""

    return {
        "youtube_video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "published_at": published_at,
        "updated_at": str((feed_item or {}).get("updated_at") or ""),
        "published_at_confidence": confidence,
        "content_type": content_type,
        "duration_seconds": duration_seconds,
        "eligibility_status": eligibility_status,
        "exclusion_reason": exclusion_reason,
        "discovery_source": discovery_source,
        "metadata_checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _classify_video_ids_with_proxy(
    *,
    opener: urllib.request.OpenerDirector,
    video_ids: Iterable[str],
    channel_id: str,
    feed_by_id: dict[str, dict],
    known_by_id: dict[str, dict],
    source: str,
    limit: int,
    timeout_seconds: float,
) -> list[dict]:
    items: list[dict] = []
    eligible_count = 0
    for video_id in video_ids:
        if not VIDEO_ID_PATTERN.fullmatch(str(video_id or "")):
            continue
        known = known_by_id.get(video_id)
        if known and str(known.get("eligibility_status") or "") != "metadata_pending":
            item = _known_video_record(
                known, discovery_source=source, feed_item=feed_by_id.get(video_id)
            )
        else:
            try:
                data = _read_proxied_page(
                    opener,
                    f"https://www.youtube.com/watch?v={video_id}",
                    timeout_seconds=timeout_seconds,
                    max_bytes=MAX_VIDEO_PAGE_BYTES,
                )
                item = parse_watch_video_metadata(
                    data,
                    video_id=video_id,
                    expected_channel_id=channel_id,
                    discovery_source=source,
                    feed_item=feed_by_id.get(video_id),
                )
            except CompetitorSourceUnavailable:
                item = _pending_video_record(
                    video_id,
                    discovery_source=source,
                    feed_item=feed_by_id.get(video_id),
                )
        items.append(item)
        if item["eligibility_status"] == "eligible":
            eligible_count += 1
            if eligible_count >= max(1, int(limit)):
                break
    return items


async def _discover_with_gpm(
    *,
    channel_id: str,
    gpm_profile_id: str,
    feed_by_id: dict[str, dict],
    known_by_id: dict[str, dict],
    limit: int,
    timeout_seconds: float,
) -> list[dict]:
    if not str(gpm_profile_id or "").strip():
        raise CompetitorSourceUnavailable(
            "gpm_browser", "Kênh đích chưa có GPM profile cho Radar fallback."
        )
    target_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    async with channel_scanner_service.channel_browser_session(
        gpm_profile_id,
        target_url=target_url,
        timeout_seconds=timeout_seconds,
    ) as (context, _browser, _profile):
        page = await context.new_page()
        try:
            await page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=int(timeout_seconds * 1000),
            )
            await page.wait_for_timeout(800)
            ids = extract_channel_video_ids((await page.content()).encode(), channel_id)
            if not ids:
                raise CompetitorSourceUnavailable(
                    "gpm_browser",
                    "GPM không tìm thấy video trên tab /videos của đối thủ.",
                )
            items: list[dict] = []
            eligible_count = 0
            for video_id in ids:
                known = known_by_id.get(video_id)
                if known and str(known.get("eligibility_status") or "") != "metadata_pending":
                    item = _known_video_record(
                        known,
                        discovery_source="gpm_browser",
                        feed_item=feed_by_id.get(video_id),
                    )
                else:
                    response = await context.request.get(
                        f"https://www.youtube.com/watch?v={video_id}",
                        timeout=int(timeout_seconds * 1000),
                    )
                    if not response.ok:
                        item = _pending_video_record(
                            video_id,
                            discovery_source="gpm_browser",
                            feed_item=feed_by_id.get(video_id),
                        )
                    else:
                        item = parse_watch_video_metadata(
                            (await response.body()),
                            video_id=video_id,
                            expected_channel_id=channel_id,
                            discovery_source="gpm_browser",
                            feed_item=feed_by_id.get(video_id),
                        )
                items.append(item)
                if item["eligibility_status"] == "eligible":
                    eligible_count += 1
                    if eligible_count >= max(1, int(limit)):
                        break
            return items
        finally:
            await page.close()


def discover_long_form_videos(
    channel_id: str,
    raw_proxy: str,
    gpm_profile_id: str,
    *,
    limit: int = BASELINE_VIDEO_COUNT,
    timeout_seconds: float = 30.0,
    use_rss: bool = True,
    known_videos: Iterable[dict] = (),
) -> dict[str, Any]:
    """Discover standard long-form uploads with isolated automatic fallbacks."""
    clean_channel_id = str(channel_id or "").strip()
    if not CHANNEL_ID_PATTERN.fullmatch(clean_channel_id):
        raise CompetitorRadarError("Channel ID đối thủ không hợp lệ.")
    if not parse_proxy_url(raw_proxy):
        raise CompetitorRadarError("Kênh đích chưa có proxy hợp lệ; Radar đã dừng an toàn.")

    known_by_id = {
        str(item.get("youtube_video_id") or ""): item
        for item in known_videos
        if item.get("youtube_video_id")
    }
    feed_items: list[dict] = []
    rss_error = ""
    if use_rss:
        try:
            feed_items = fetch_channel_feed(
                clean_channel_id,
                raw_proxy,
                timeout_seconds=timeout_seconds,
                limit=BASELINE_VIDEO_COUNT,
            )
        except CompetitorRadarError as exc:
            rss_error = str(exc)
    feed_by_id = {
        str(item.get("youtube_video_id") or ""): item for item in feed_items
    }

    proxy_error = ""
    try:
        opener = create_proxy_opener(raw_proxy, require_proxy=True)
        channel_page = _read_proxied_page(
            opener,
            f"https://www.youtube.com/channel/{clean_channel_id}/videos",
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_CHANNEL_PAGE_BYTES,
        )
        video_ids = extract_channel_video_ids(channel_page, clean_channel_id)
        items = _classify_video_ids_with_proxy(
            opener=opener,
            video_ids=video_ids,
            channel_id=clean_channel_id,
            feed_by_id=feed_by_id,
            known_by_id=known_by_id,
            source="rss" if feed_items else "proxy_html",
            limit=limit,
            timeout_seconds=timeout_seconds,
        )
        if items and any(
            item["eligibility_status"] != "metadata_pending" for item in items
        ):
            return {
                "items": items,
                "scan_source": "rss" if feed_items else "proxy_html",
                "rss_error": rss_error,
                "fallback_error": "",
            }
        proxy_error = (
            "Proxy HTML không tìm thấy video trên tab /videos."
            if not items
            else "Không thể xác minh metadata video qua Proxy HTML."
        )
    except CompetitorRadarError as exc:
        proxy_error = str(exc)

    try:
        items = asyncio.run(
            _discover_with_gpm(
                channel_id=clean_channel_id,
                gpm_profile_id=gpm_profile_id,
                feed_by_id=feed_by_id,
                known_by_id=known_by_id,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception as exc:
        raise CompetitorSourceUnavailable(
            "gpm_browser",
            "Radar không thể đọc video dài qua RSS, Proxy HTML hoặc GPM. "
            f"RSS: {rss_error or 'không có dữ liệu'}; "
            f"Proxy HTML: {proxy_error or 'không có dữ liệu'}; "
            f"GPM: {type(exc).__name__}.",
        ) from exc
    if not items or all(
        item.get("eligibility_status") == "metadata_pending" for item in items
    ):
        raise CompetitorSourceUnavailable(
            "gpm_browser",
            "Radar không thể xác minh metadata video dài qua GPM; rule sẽ tự thử lại.",
        )
    return {
        "items": items,
        "scan_source": "gpm_browser",
        "rss_error": rss_error,
        "fallback_error": proxy_error,
    }


def parse_utc(value: str | dt.datetime | None) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        cleaned = str(value or "").strip()
        if not cleaned:
            return None
        try:
            parsed = dt.datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _minute_of_week(moment: dt.datetime) -> int:
    return moment.weekday() * 24 * 60 + moment.hour * 60 + moment.minute


def _circular_minute_distance(left: int, right: int) -> int:
    week = 7 * 24 * 60
    delta = abs(left - right)
    return min(delta, week - delta)


def _parse_clock(value: str) -> int | None:
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def is_in_manual_window(
    now: dt.datetime,
    windows: Iterable[dict[str, Any]],
    *,
    timezone_name: str,
) -> bool:
    try:
        local_now = now.astimezone(ZoneInfo(timezone_name or "Asia/Ho_Chi_Minh"))
    except ZoneInfoNotFoundError:
        local_now = now.astimezone(dt.timezone.utc)
    current = local_now.hour * 60 + local_now.minute
    weekday = local_now.weekday()
    for window in windows:
        if not isinstance(window, dict):
            continue
        days = window.get("days", list(range(7)))
        if not isinstance(days, list):
            continue
        start = _parse_clock(str(window.get("start") or ""))
        end = _parse_clock(str(window.get("end") or ""))
        if start is None or end is None:
            continue
        if start <= end and weekday in days and start <= current <= end:
            return True
        if start > end:
            if weekday in days and current >= start:
                return True
            if (weekday - 1) % 7 in days and current <= end:
                return True
    return False


def next_scan_delay_seconds(
    *,
    published_times: Iterable[str | dt.datetime],
    now: dt.datetime | None = None,
    window_mode: str = "learned",
    manual_windows: Iterable[dict[str, Any]] = (),
    timezone_name: str = "Asia/Ho_Chi_Minh",
) -> int:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    if str(window_mode).casefold() == "manual":
        return (
            PREDICTED_SCAN_SECONDS
            if is_in_manual_window(current, manual_windows, timezone_name=timezone_name)
            else OFF_WINDOW_SCAN_SECONDS
        )
    samples = [parsed for value in published_times if (parsed := parse_utc(value))]
    if len(samples) < 5:
        return COLD_START_SCAN_SECONDS
    current_minute = _minute_of_week(current)
    in_window = any(
        _circular_minute_distance(current_minute, _minute_of_week(sample))
        <= PREDICTED_WINDOW_MINUTES
        for sample in samples
    )
    return PREDICTED_SCAN_SECONDS if in_window else OFF_WINDOW_SCAN_SECONDS


def next_scan_at(
    *,
    published_times: Iterable[str | dt.datetime],
    now: dt.datetime | None = None,
    window_mode: str = "learned",
    manual_windows: Iterable[dict[str, Any]] = (),
    timezone_name: str = "Asia/Ho_Chi_Minh",
) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    delay = next_scan_delay_seconds(
        published_times=published_times,
        now=current,
        window_mode=window_mode,
        manual_windows=manual_windows,
        timezone_name=timezone_name,
    )
    return (current.astimezone(dt.timezone.utc) + dt.timedelta(seconds=delay)).isoformat()


def scan_error_delay_seconds(error_count: int) -> int:
    return (60, 300, 900, 1800)[min(max(0, int(error_count)), 3)]
