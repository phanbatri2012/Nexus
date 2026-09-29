"""Unified Browser Diagnostics & Blocker Detection Engine.

Automatically captures screenshots, sanitizes and logs DOM HTML, and detects
abnormal modals/blockers (Cloudflare Turnstile, CAPTCHA, Expired Sessions,
Terms of Service updates, Quota limits, Policy triggers, 2FA Checkpoints)
across all Playwright automation services (ChatGPT, Google Flow, YouTube Studio).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auto_yt.paths import DATA_DIR, DIAGNOSTICS_DIR, LOGS_DIR

logger = logging.getLogger(__name__)

# Category constants
CATEGORY_CLOUDFLARE = "cloudflare_turnstile"
CATEGORY_SESSION_EXPIRED = "session_expired"
CATEGORY_TERMS_CONSENT = "terms_consent"
CATEGORY_RATE_LIMIT_QUOTA = "rate_limit_quota"
CATEGORY_CONTENT_POLICY = "content_policy"
CATEGORY_VERIFY_2FA = "verify_identity_2fa"
CATEGORY_UI_RENDER_ERROR = "ui_render_error"
CATEGORY_GENERIC_ERROR = "generic_error"


@dataclass(frozen=True)
class BlockerDetectionResult:
    category: str
    confidence: str  # "high" | "medium" | "low"
    matched_rule: str
    message: str
    action_required: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Blocker heuristics rules
_BLOCKER_RULES: list[dict[str, Any]] = [
    # 1. Cloudflare / Turnstile / Anti-bot challenges
    {
        "category": CATEGORY_CLOUDFLARE,
        "confidence": "high",
        "action_required": "manual_captcha_resolution",
        "message": "Trình duyệt đang bị chặn bởi Cloudflare Turnstile / CAPTCHA.",
        "url_patterns": [
            re.compile(r"challenges\.cloudflare\.com", re.IGNORECASE),
            re.compile(r"cf-chl", re.IGNORECASE),
            re.compile(r"/__cf_chl_tk=", re.IGNORECASE),
        ],
        "title_patterns": [
            re.compile(r"Just a moment\.\.\.", re.IGNORECASE),
            re.compile(r"Attention Required! \| Cloudflare", re.IGNORECASE),
            re.compile(r"Cloudflare", re.IGNORECASE),
        ],
        "html_markers": [
            "challenges.cloudflare.com",
            "cf-turnstile",
            "cf-chl-widget",
            "Ray ID:",
            "Verify you are human",
            "Checking your browser before accessing",
            "Please turn off your ad blocker or VPN",
            "Xác minh bạn là con người",
        ],
    },
    # 2. Session / Auth Expiration
    {
        "category": CATEGORY_SESSION_EXPIRED,
        "confidence": "high",
        "action_required": "re_authenticate",
        "message": "Phiên đăng nhập đã hết hạn hoặc yêu cầu đăng nhập lại.",
        "url_patterns": [
            re.compile(r"accounts\.google\.com/signin", re.IGNORECASE),
            re.compile(r"accounts\.google\.com/ServiceLogin", re.IGNORECASE),
            re.compile(r"chatgpt\.com/auth/login", re.IGNORECASE),
            re.compile(r"auth\.openai\.com", re.IGNORECASE),
            re.compile(r"auth0\.com", re.IGNORECASE),
            re.compile(r"login\.live\.com", re.IGNORECASE),
        ],
        "title_patterns": [
            re.compile(r"Đăng nhập - Tài khoản Google", re.IGNORECASE),
            re.compile(r"Sign in - Google Accounts", re.IGNORECASE),
            re.compile(r"Log in to ChatGPT", re.IGNORECASE),
            re.compile(r"Sign in to ChatGPT", re.IGNORECASE),
            re.compile(r"Đăng nhập", re.IGNORECASE),
        ],
        "html_patterns": [
            re.compile(r"phiên\s+đăng\s+nhập.*?hết\s+hạn", re.IGNORECASE),
            re.compile(r"session\s+has\s+expired", re.IGNORECASE),
            re.compile(r"log\s+in\s+to\s+continue", re.IGNORECASE),
            re.compile(r"sign\s+in\s+to\s+continue", re.IGNORECASE),
        ],
        "html_markers": [
            "Phiên đăng nhập đã hết hạn",
            "Phiên đăng nhập của bạn đã hết hạn",
            "Phiên đăng nhập ChatGPT đã hết hạn",
            "Your session has expired",
            "Sign in to continue",
            "Log in to continue",
            "Sign in to confirm you're not a bot",
            "Đăng nhập để tiếp tục",
            "Please sign in again",
            "Session expired",
            "Phiên của bạn đã hết thời gian sử dụng",
        ],
    },

    # 3. Terms of Service / Policy Consent Modals
    {
        "category": CATEGORY_TERMS_CONSENT,
        "confidence": "high",
        "action_required": "accept_terms",
        "message": "Xuất hiện cửa sổ cập nhật Điều khoản Dịch vụ hoặc Đồng ý chính sách.",
        "url_patterns": [
            re.compile(r"consent\.google\.com", re.IGNORECASE),
            re.compile(r"consent\.youtube\.com", re.IGNORECASE),
        ],
        "title_patterns": [
            re.compile(r"Trước khi bạn tiếp tục", re.IGNORECASE),
            re.compile(r"Before you continue", re.IGNORECASE),
        ],
        "html_markers": [
            "We've updated our Terms of Service",
            "Chúng tôi đã cập nhật Điều khoản dịch vụ",
            "Trước khi bạn tiếp tục sử dụng",
            "Before you continue to YouTube",
            "Cập nhật Điều khoản dịch vụ của YouTube",
            "We updated our Terms of Use",
            "Đồng ý với tất cả",
            "Accept all",
        ],
    },
    # 4. Quota / Rate Limits
    {
        "category": CATEGORY_RATE_LIMIT_QUOTA,
        "confidence": "high",
        "action_required": "rate_limit_backoff",
        "message": "Đã chạm giới hạn sử dụng hoặc hạn mức yêu cầu (Rate Limit / Quota).",
        "url_patterns": [],
        "title_patterns": [],
        "html_markers": [
            "You've hit the Free plan limit",
            "You've reached your GPT-4 limit",
            "You've reached your GPT-4o limit",
            "You've reached the current usage cap",
            "Too many requests in 1 hour",
            "Try again after",
            "Daily upload limit reached",
            "Hạn mức tải lên hằng ngày",
            "Generation quota exceeded",
            "Daily limit reached",
            "You've reached your image generation limit",
            "Quá nhiều yêu cầu",
            "Please try again later",
        ],
    },
    # 5. Content Policy / Safety Filter
    {
        "category": CATEGORY_CONTENT_POLICY,
        "confidence": "high",
        "action_required": "review_prompt_safety",
        "message": "Nội dung yêu cầu vi phạm chính sách an toàn hoặc bộ lọc kiểm duyệt.",
        "url_patterns": [],
        "title_patterns": [],
        "html_markers": [
            "Prompt violated safety policies",
            "This prompt may violate our usage policies",
            "Nội dung vi phạm chính sách an toàn",
            "Content flagged as inappropriate",
            "Your request was rejected as a result of our safety system",
            "Safety filter triggered",
            "Vi phạm nguyên tắc cộng đồng",
        ],
    },
    # 6. 2FA / Identity Verification Checkpoints
    {
        "category": CATEGORY_VERIFY_2FA,
        "confidence": "high",
        "action_required": "manual_2fa_verification",
        "message": "Yêu cầu xác minh danh tính 2 bước (2FA) hoặc gửi mã OTP xác nhận.",
        "url_patterns": [
            re.compile(r"accounts\.google\.com/v3/signin/challenge", re.IGNORECASE),
        ],
        "title_patterns": [
            re.compile(r"Xác minh 2 bước", re.IGNORECASE),
            re.compile(r"2-Step Verification", re.IGNORECASE),
        ],
        "html_markers": [
            "Verify it's you",
            "Xác minh danh tính",
            "Xác minh bạn chính là người đang thực hiện",
            "Check your phone",
            "Mã xác minh gồm 2 chữ số",
            "Nhấn vào số trên điện thoại",
            "Security check",
            "Enter the code sent to",
        ],
    },
]


def _sanitize_dom_html(html: str) -> str:
    """Strip bulky base64 data URIs from HTML to keep file sizes lean and performant."""
    if not html:
        return ""
    # Truncate image/video/font base64 blobs
    cleaned = re.sub(
        r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+",
        "data:image/...[TRUNCATED_BASE64]",
        html,
    )
    cleaned = re.sub(
        r"data:font/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+",
        "data:font/...[TRUNCATED_BASE64]",
        cleaned,
    )
    cleaned = re.sub(
        r"data:video/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+",
        "data:video/...[TRUNCATED_BASE64]",
        cleaned,
    )
    return cleaned


def detect_browser_blockers(
    url: str = "",
    title: str = "",
    html_text: str = "",
) -> list[dict[str, Any]]:
    """Inspect page URL, title, and HTML to detect blockers or abnormal modal popups."""
    results: list[BlockerDetectionResult] = []
    norm_url = str(url or "").strip()
    norm_title = str(title or "").strip()
    norm_html = str(html_text or "")

    for rule in _BLOCKER_RULES:
        category = rule["category"]
        confidence = rule["confidence"]
        message = rule["message"]
        action = rule["action_required"]

        # Check URL
        matched = False
        matched_rule = ""
        for pat in rule.get("url_patterns", []):
            if pat.search(norm_url):
                matched = True
                matched_rule = f"URL pattern match: {pat.pattern}"
                break

        # Check Title
        if not matched:
            for pat in rule.get("title_patterns", []):
                if pat.search(norm_title):
                    matched = True
                    matched_rule = f"Title pattern match: {pat.pattern}"
                    break

        # Check HTML / text markers
        if not matched and norm_html:
            for pat in rule.get("html_patterns", []):
                if pat.search(norm_html):
                    matched = True
                    matched_rule = f"HTML pattern match: {pat.pattern}"
                    break
            if not matched:
                for marker in rule.get("html_markers", []):
                    if marker.lower() in norm_html.lower():
                        matched = True
                        matched_rule = f"HTML marker match: '{marker}'"
                        break


        if matched:
            results.append(
                BlockerDetectionResult(
                    category=category,
                    confidence=confidence,
                    matched_rule=matched_rule,
                    message=message,
                    action_required=action,
                )
            )

    return [r.to_dict() for r in results]


def _build_safe_identifier(
    service: str,
    job_id: str | None = None,
    video_id: int | str | None = None,
) -> str:
    """Build a filesystem-safe identifier string."""
    parts = [str(service).strip().lower() or "browser"]
    if video_id is not None and str(video_id).strip():
        parts.append(f"vid_{video_id}")
    if job_id is not None and str(job_id).strip():
        # Sanitize job_id
        safe_job = re.sub(r"[^a-zA-Z0-9_-]", "_", str(job_id).strip())
        parts.append(safe_job)
    return "_".join(parts)


def capture_browser_diagnostics_sync(
    page: Any | None,
    service: str,
    job_id: str | None = None,
    video_id: int | str | None = None,
    error: Exception | str | None = None,
    action_name: str = "",
    logs_dir: Path | None = None,
) -> dict[str, Any]:
    """Capture screenshot, sanitized DOM HTML, and blocker evidence synchronously.

    Safe against disconnected or dead pages. Never throws unhandled exceptions.
    """
    target_dir = logs_dir or DIAGNOSTICS_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Could not create diagnostics directory %s: %s", target_dir, e)
        target_dir = LOGS_DIR

    timestamp_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    safe_id = _build_safe_identifier(service, job_id, video_id)
    base_name = f"debug_{safe_id}_{timestamp_str}"

    screenshot_file: Path | None = None
    dom_file: Path | None = None
    meta_file: Path = target_dir / f"{base_name}.json"

    current_url = ""
    page_title = ""
    html_content = ""
    screenshot_captured = False

    if page is not None:
        try:
            current_url = str(page.url or "")
        except Exception:
            current_url = ""

        try:
            page_title = str(page.title() or "")
        except Exception:
            page_title = ""

        # DOM snapshot
        try:
            raw_html = str(page.content() or "")
            html_content = _sanitize_dom_html(raw_html)
            dom_file = target_dir / f"{base_name}.html"
            dom_file.write_text(html_content, encoding="utf-8")
        except Exception as dom_exc:
            logger.debug("Failed to dump DOM content: %s", dom_exc)

        # Screenshot capture (with full_page fallback)
        try:
            screenshot_file = target_dir / f"{base_name}.png"
            try:
                page.screenshot(path=str(screenshot_file), full_page=True, timeout=5000)
            except Exception:
                # Fallback to viewport screenshot
                page.screenshot(path=str(screenshot_file), full_page=False, timeout=5000)
            screenshot_captured = True
        except Exception as shot_exc:
            logger.debug("Failed to take screenshot: %s", shot_exc)
            screenshot_file = None

    # Detect blockers
    detected_blockers = detect_browser_blockers(
        url=current_url,
        title=page_title,
        html_text=html_content,
    )

    error_str = str(error) if error is not None else ""
    error_type = type(error).__name__ if isinstance(error, BaseException) else "UnknownError"
    stack_trace = ""
    if isinstance(error, BaseException):
        stack_trace = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )

    evidence_dict: dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "service": service,
        "action_name": action_name,
        "job_id": str(job_id) if job_id else None,
        "video_id": str(video_id) if video_id is not None else None,
        "current_url": current_url,
        "page_title": page_title,
        "error_type": error_type,
        "error_message": error_str,
        "stack_trace": stack_trace,
        "screenshot_captured": screenshot_captured,
        "screenshot_path": str(screenshot_file) if screenshot_file else None,
        "dom_path": str(dom_file) if dom_file else None,
        "detected_blockers": detected_blockers,
    }

    try:
        meta_file.write_text(
            json.dumps(evidence_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as meta_exc:
        logger.warning("Could not write diagnostics metadata: %s", meta_exc)

    logger.warning(
        "[BrowserDiagnostics] Captured failure diagnostics for '%s': screenshot=%s, dom=%s, blockers=%d",
        service,
        screenshot_file.name if screenshot_file else "None",
        dom_file.name if dom_file else "None",
        len(detected_blockers),
    )
    return evidence_dict


async def capture_browser_diagnostics_async(
    page: Any | None,
    service: str,
    job_id: str | None = None,
    video_id: int | str | None = None,
    error: Exception | str | None = None,
    action_name: str = "",
    logs_dir: Path | None = None,
) -> dict[str, Any]:
    """Capture screenshot, sanitized DOM HTML, and blocker evidence asynchronously.

    Safe against disconnected or dead pages. Never throws unhandled exceptions.
    """
    target_dir = logs_dir or DIAGNOSTICS_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Could not create diagnostics directory %s: %s", target_dir, e)
        target_dir = LOGS_DIR

    timestamp_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
    safe_id = _build_safe_identifier(service, job_id, video_id)
    base_name = f"debug_{safe_id}_{timestamp_str}"

    screenshot_file: Path | None = None
    dom_file: Path | None = None
    meta_file: Path = target_dir / f"{base_name}.json"

    current_url = ""
    page_title = ""
    html_content = ""
    screenshot_captured = False

    if page is not None:
        try:
            current_url = str(page.url or "")
        except Exception:
            current_url = ""

        try:
            page_title = str(await page.title() if asyncio.iscoroutinefunction(page.title) else page.title() or "")
        except Exception:
            page_title = ""

        # DOM snapshot
        try:
            raw_html = str(await page.content() if asyncio.iscoroutinefunction(page.content) else page.content() or "")
            html_content = _sanitize_dom_html(raw_html)
            dom_file = target_dir / f"{base_name}.html"
            dom_file.write_text(html_content, encoding="utf-8")
        except Exception as dom_exc:
            logger.debug("Failed to dump DOM content: %s", dom_exc)

        # Screenshot capture (with full_page fallback)
        try:
            screenshot_file = target_dir / f"{base_name}.png"
            try:
                await page.screenshot(path=str(screenshot_file), full_page=True, timeout=5000)
            except Exception:
                await page.screenshot(path=str(screenshot_file), full_page=False, timeout=5000)
            screenshot_captured = True
        except Exception as shot_exc:
            logger.debug("Failed to take screenshot: %s", shot_exc)
            screenshot_file = None

    # Detect blockers
    detected_blockers = detect_browser_blockers(
        url=current_url,
        title=page_title,
        html_text=html_content,
    )

    error_str = str(error) if error is not None else ""
    error_type = type(error).__name__ if isinstance(error, BaseException) else "UnknownError"
    stack_trace = ""
    if isinstance(error, BaseException):
        stack_trace = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )

    evidence_dict: dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "service": service,
        "action_name": action_name,
        "job_id": str(job_id) if job_id else None,
        "video_id": str(video_id) if video_id is not None else None,
        "current_url": current_url,
        "page_title": page_title,
        "error_type": error_type,
        "error_message": error_str,
        "stack_trace": stack_trace,
        "screenshot_captured": screenshot_captured,
        "screenshot_path": str(screenshot_file) if screenshot_file else None,
        "dom_path": str(dom_file) if dom_file else None,
        "detected_blockers": detected_blockers,
    }

    try:
        meta_file.write_text(
            json.dumps(evidence_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as meta_exc:
        logger.warning("Could not write diagnostics metadata: %s", meta_exc)

    logger.warning(
        "[BrowserDiagnostics] Captured failure diagnostics for '%s': screenshot=%s, dom=%s, blockers=%d",
        service,
        screenshot_file.name if screenshot_file else "None",
        dom_file.name if dom_file else "None",
        len(detected_blockers),
    )
    return evidence_dict


def cleanup_old_diagnostics(
    logs_dir: Path | None = None,
    max_age_days: int = 7,
    max_total_mb: int = 500,
) -> int:
    """Clean up diagnostics files older than max_age_days or when exceeding max_total_mb."""
    target_dir = logs_dir or DIAGNOSTICS_DIR
    if not target_dir.exists() or not target_dir.is_dir():
        return 0

    deleted_count = 0
    now = dt.datetime.now()
    cutoff_time = now - dt.timedelta(days=max_age_days)

    all_files: list[Path] = [
        f for f in target_dir.glob("debug_*") if f.is_file()
    ]

    # 1. Delete files older than max_age_days
    remaining_files: list[Path] = []
    for file_path in all_files:
        try:
            mtime = dt.datetime.fromtimestamp(file_path.stat().st_mtime)
            if mtime < cutoff_time:
                file_path.unlink(missing_ok=True)
                deleted_count += 1
            else:
                remaining_files.append(file_path)
        except OSError:
            pass

    # 2. Check total size threshold
    total_bytes = sum(f.stat().st_size for f in remaining_files if f.exists())
    max_bytes = max_total_mb * 1024 * 1024

    if total_bytes > max_bytes:
        # Sort remaining files by mtime ascending (oldest first)
        remaining_files.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0)
        for file_path in remaining_files:
            if total_bytes <= max_bytes:
                break
            try:
                size = file_path.stat().st_size
                file_path.unlink(missing_ok=True)
                total_bytes -= size
                deleted_count += 1
            except OSError:
                pass

    if deleted_count > 0:
        logger.info(
            "[BrowserDiagnostics] Cleaned up %d obsolete diagnostics files in %s",
            deleted_count,
            target_dir,
        )
    return deleted_count
