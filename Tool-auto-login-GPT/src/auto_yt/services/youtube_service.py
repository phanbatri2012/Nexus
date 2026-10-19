import urllib.parse as urlparse
import re
from html import unescape
from typing import Iterable

from curl_cffi import requests as curl_requests
import truststore
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeRequestFailed,
)
from youtube_transcript_api.proxies import GenericProxyConfig
from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget
from auto_yt.services.network_security import validate_https_url
from auto_yt.services.proxy_utils import ProxyConfigurationError, parse_proxy_url


YOUTUBE_REQUEST_TIMEOUT_SECONDS = 30
MAX_YOUTUBE_RESPONSE_BYTES = 10 * 1024 * 1024
YOUTUBE_PAGE_HOSTS = ("youtube.com", "youtu.be")
YOUTUBE_MEDIA_HOSTS = ("youtube.com", "googlevideo.com", "ytimg.com")
PREFERRED_TRANSCRIPT_LANGUAGES = ("vi", "en")


class TranscriptRetrievalError(RuntimeError):
    """A classified transcript failure safe for bounded job recovery."""

    def __init__(self, message: str, *, code: str, retryable: bool):
        super().__init__(message)
        self.code = str(code or "transcript_error")
        self.retryable = bool(retryable)


class TranscriptTemporarilyUnavailable(TranscriptRetrievalError):
    def __init__(self, message: str, *, code: str = "transcript_temporarily_unavailable"):
        super().__init__(message, code=code, retryable=True)


class TranscriptUnavailable(TranscriptRetrievalError):
    def __init__(self, message: str, *, code: str = "transcript_unavailable"):
        super().__init__(message, code=code, retryable=False)


def _resolved_proxy(raw_proxy: str | None, *, require_proxy: bool) -> str | None:
    proxy_url = parse_proxy_url(raw_proxy)
    if require_proxy and not proxy_url:
        raise ProxyConfigurationError(
            "Kênh chưa có proxy GPM hợp lệ; việc tải transcript trực tiếp đã bị chặn."
        )
    return proxy_url


def _language_priority(language_code: str) -> tuple[int, str]:
    normalized = str(language_code or "").strip().casefold().replace("_", "-")
    if normalized == "vi":
        return (0, normalized)
    if normalized.startswith("vi-"):
        return (1, normalized)
    if normalized == "en":
        return (2, normalized)
    if normalized.startswith("en-"):
        return (3, normalized)
    return (100, normalized)


def _preferred_language_codes(language_codes: Iterable[str]) -> list[str]:
    unique = {
        str(code or "").strip(): _language_priority(str(code or ""))
        for code in language_codes
        if str(code or "").strip()
    }
    return [
        code
        for code, priority in sorted(unique.items(), key=lambda item: item[1])
        if priority[0] < 100
    ]


def validate_youtube_video_url(url: str) -> str:
    return validate_https_url(
        url,
        allowed_hosts=YOUTUBE_PAGE_HOSTS,
        allow_subdomains=True,
    )


def _configure_system_trust_store() -> None:
    # Honor managed OS certificate authorities while keeping TLS verification enabled.
    truststore.inject_into_ssl()


_configure_system_trust_store()

def get_video_title(
    url: str,
    *,
    proxy: str | None = None,
    require_proxy: bool = False,
) -> str:
    """Fetches the video title from YouTube URL."""
    proxy_url = _resolved_proxy(proxy, require_proxy=require_proxy)
    try:
        response = curl_requests.get(
            validate_youtube_video_url(url),
            impersonate="chrome",
            timeout=YOUTUBE_REQUEST_TIMEOUT_SECONDS,
            proxy=proxy_url,
        )
        response.raise_for_status()
        response_url = getattr(response, "url", url)
        validate_youtube_video_url(
            str(response_url) if isinstance(response_url, (str, bytes)) else url
        )
        response_content = getattr(response, "content", b"")
        if isinstance(response_content, bytes) and len(response_content) > MAX_YOUTUBE_RESPONSE_BYTES:
            raise ValueError("YouTube page exceeds the configured size limit")
        page_html = response.text
        match = re.search(r'<title>(.*?)</title>', page_html)
        if match:
            return unescape(match.group(1)).replace(' - YouTube', '').strip()
    except Exception as e:
        print(f"Failed to fetch title: {e}")
    return "Unknown Title"

def extract_video_id(url: str) -> str:
    """Extracts the video ID from a YouTube URL."""
    url_data = urlparse.urlparse(validate_youtube_video_url(url))
    query = urlparse.parse_qs(url_data.query)
    if 'v' in query:
        return query['v'][0]
    elif url_data.netloc == 'youtu.be':
        return url_data.path.lstrip('/')
    else:
        raise ValueError("Could not extract video ID from URL")

def _get_transcript_with_ytdlp(url: str, *, proxy_url: str | None = None) -> str:
    url = validate_youtube_video_url(url)
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['vi.*', 'en.*'],
        'quiet': True,
        'javascript_runtimes': ['node'],
        'impersonate': ImpersonateTarget(client='chrome')
    }
    if proxy_url:
        ydl_opts['proxy'] = proxy_url
    
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        subs = {}
        for cap_type in ['subtitles', 'automatic_captions']:
            if info.get(cap_type):
                for lang, formats in info[cap_type].items():
                    if lang not in subs:
                        subs[lang] = []
                    subs[lang].extend(formats)

        target_fmt = None
        available_languages = _preferred_language_codes(subs)
        for lang in available_languages:
            if lang in subs:
                for fmt in subs[lang]:
                    if fmt['ext'] == 'json3':
                        target_fmt = fmt
                        break
                if target_fmt:
                    break

        if not target_fmt:
            if subs:
                raise TranscriptUnavailable(
                    "Video có transcript nhưng chưa có track tiếng Việt hoặc tiếng Anh phù hợp.",
                    code="unsupported_transcript_language",
                )
            raise NoTranscriptFound(extract_video_id(url), PREFERRED_TRANSCRIPT_LANGUAGES, {})

        transcript_url = validate_https_url(
            target_fmt['url'],
            allowed_hosts=YOUTUBE_MEDIA_HOSTS,
            allow_subdomains=True,
        )
        response = curl_requests.get(
            transcript_url,
            impersonate="chrome",
            timeout=YOUTUBE_REQUEST_TIMEOUT_SECONDS,
            proxy=proxy_url,
        )
        response.raise_for_status()
        response_url = getattr(response, "url", transcript_url)
        validate_https_url(
            str(response_url) if isinstance(response_url, (str, bytes)) else transcript_url,
            allowed_hosts=YOUTUBE_MEDIA_HOSTS,
            allow_subdomains=True,
        )
        response_content = getattr(response, "content", b"")
        if isinstance(response_content, bytes) and len(response_content) > MAX_YOUTUBE_RESPONSE_BYTES:
            raise ValueError("YouTube transcript exceeds the configured size limit")
        data = response.json()

        text_lines = []
        for event in data.get('events', []):
            if 'segs' in event:
                text = "".join(seg.get('utf8', '') for seg in event['segs'])
                if text.strip():
                    text_lines.append(text.strip())

        transcript = " ".join(text_lines).replace("\n", " ").strip()
        if not transcript:
            raise ValueError("YouTube returned an empty transcript")
        return transcript


def _select_transcript(transcript_list):
    transcripts = list(transcript_list)
    preferred_codes = _preferred_language_codes(
        transcript.language_code for transcript in transcripts
    )
    for language_code in preferred_codes:
        for prefer_generated in (False, True):
            for transcript in transcripts:
                if (
                    transcript.language_code == language_code
                    and bool(transcript.is_generated) is prefer_generated
                ):
                    return transcript

    for transcript in transcripts:
        if transcript.is_translatable:
            translation_codes = {
                str(item.language_code or "").strip().casefold()
                for item in transcript.translation_languages
            }
            if "vi" in translation_codes:
                return transcript.translate("vi")

    if transcripts:
        raise TranscriptUnavailable(
            "Video có transcript nhưng chưa có track tiếng Việt hoặc tiếng Anh phù hợp.",
            code="unsupported_transcript_language",
        )
    raise TranscriptUnavailable(
        "YouTube không trả về track transcript nào.",
        code="transcript_track_missing",
    )


def _get_transcript_with_transcript_api(
    url: str,
    *,
    proxy_url: str | None = None,
) -> str:
    video_id = extract_video_id(url)
    api = YouTubeTranscriptApi(
        proxy_config=(
            GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
            if proxy_url
            else None
        )
    )
    transcript = _select_transcript(api.list(video_id))
    fetched_transcript = transcript.fetch()
    text_lines = []
    for snippet in fetched_transcript:
        text = (
            snippet.get("text", "")
            if isinstance(snippet, dict)
            else getattr(snippet, "text", "")
        )
        if str(text).strip():
            text_lines.append(str(text).strip())
    transcript = " ".join(text_lines).replace("\n", " ").strip()
    if not transcript:
        raise ValueError("YouTube returned an empty transcript")
    return transcript


def _classify_transcript_failure(error: BaseException) -> TranscriptRetrievalError:
    if isinstance(error, TranscriptRetrievalError):
        return error
    if isinstance(error, (RequestBlocked, IpBlocked)):
        return TranscriptTemporarilyUnavailable(
            "YouTube đang chặn tạm thời kết nối lấy transcript.",
            code="transcript_request_blocked",
        )
    if isinstance(error, PoTokenRequired):
        return TranscriptTemporarilyUnavailable(
            "YouTube tạm yêu cầu xác minh bổ sung để tải transcript.",
            code="transcript_po_token_required",
        )
    if isinstance(error, YouTubeRequestFailed):
        return TranscriptTemporarilyUnavailable(
            "Yêu cầu tải transcript từ YouTube tạm thời thất bại.",
            code="transcript_request_failed",
        )
    if isinstance(error, TranscriptsDisabled):
        return TranscriptUnavailable(
            "YouTube xác nhận transcript đã bị tắt cho video này.",
            code="transcripts_disabled",
        )
    if isinstance(error, NoTranscriptFound):
        return TranscriptUnavailable(
            "Không tìm thấy track transcript tiếng Việt hoặc tiếng Anh phù hợp.",
            code="transcript_track_missing",
        )
    if isinstance(error, (VideoUnavailable, InvalidVideoId, AgeRestricted)):
        return TranscriptUnavailable(
            "Video không khả dụng để lấy transcript.",
            code="transcript_video_unavailable",
        )

    normalized = str(error).casefold()
    transient_markers = (
        "429",
        "too many requests",
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "network is unreachable",
        "proxy",
    )
    if any(marker in normalized for marker in transient_markers):
        return TranscriptTemporarilyUnavailable(
            "Kết nối lấy transcript từ YouTube tạm thời thất bại.",
            code="transcript_network_error",
        )
    return TranscriptTemporarilyUnavailable(
        "Không thể tải transcript từ YouTube ở thời điểm hiện tại.",
        code="transcript_provider_error",
    )


def get_video_transcript(
    url: str,
    *,
    proxy: str | None = None,
    require_proxy: bool = False,
) -> str:
    """Fetch a transcript with an independent fallback provider."""
    proxy_url = _resolved_proxy(proxy, require_proxy=require_proxy)
    try:
        return _get_transcript_with_ytdlp(url, proxy_url=proxy_url)
    except Exception as primary_error:
        try:
            return _get_transcript_with_transcript_api(url, proxy_url=proxy_url)
        except Exception as fallback_error:
            classified_fallback = _classify_transcript_failure(fallback_error)
            if isinstance(classified_fallback, TranscriptUnavailable):
                raise classified_fallback from fallback_error
            classified_primary = _classify_transcript_failure(primary_error)
            if isinstance(classified_primary, TranscriptUnavailable):
                raise classified_fallback from fallback_error
            raise TranscriptTemporarilyUnavailable(
                "Không thể tải transcript từ YouTube qua cả hai nguồn ở thời điểm hiện tại.",
                code=classified_fallback.code,
            ) from fallback_error
