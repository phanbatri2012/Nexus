"""Trust Builder Service: Orchestrator, session runner, and scoring engine.

Coordinates warmup sessions, calculates 100-point trust heuristic scores,
generates contextual AI comments, and records granular activity logs.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re
from typing import Any

from auto_yt.services import database as db, security_logging, trust_builder_safety as safety
from auto_yt.services.channel_scanner_service import (
    channel_browser_session,
    cleanup_owned_page,
    inspect_profile_browser_readiness,
    is_profile_browser_busy,
    parse_profile_target,
)
from auto_yt.services.gpm_service import get_gpm_profile_detail
from auto_yt.services.gpm_youtube_automation import verify_youtube_login
from auto_yt.services.proxy_utils import parse_proxy_url
from auto_yt.services.trust_builder_actions import (
    ACTION_PERFORMED,
    action_audit_feature_eligibility,
    action_audit_channel_branding,
    action_comment_video,
    action_like_video,
    action_search_and_pick_video,
    action_subscribe_channel,
    action_watch_video,
)

logger = logging.getLogger(__name__)

TRUST_JOB_TYPE = "trust_builder_session"

# Mutex lock per plan_id to prevent concurrent runs on the same plan
_plan_locks: dict[int, asyncio.Lock] = {}
_plan_guard = asyncio.Lock()


async def _get_plan_lock(plan_id: int) -> asyncio.Lock:
    async with _plan_guard:
        if plan_id not in _plan_locks:
            _plan_locks[plan_id] = asyncio.Lock()
        return _plan_locks[plan_id]


def calculate_trust_score(plan: dict[str, Any], stats: dict[str, Any]) -> int:
    """Calculate an internal readiness score; this is not a YouTube trust score."""
    score = 0.0

    # 1. Warm-up duration (Max 15 pts) - explicitly based on plan age.
    created_at_str = str(plan.get("created_at") or "")
    days_active = 1
    if created_at_str:
        try:
            created_dt = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            days_active = max(1, (datetime.datetime.now(datetime.timezone.utc) - created_dt).days)
        except Exception:
            days_active = 1
    score += min(days_active / 14.0, 1.0) * 15.0

    # 2. Verified branding completeness (Max 15 pts)
    checklist = plan.get("branding_checklist") or {}
    branding_fields = ("avatar", "banner", "about", "handle", "contact_email", "country")
    checked_count = sum(checklist.get(field) is True for field in branding_fields)
    score += min(checked_count / len(branding_fields), 1.0) * 15.0

    # 3. Feature Eligibility (Max 10 pts)
    flevel = str(checklist.get("feature_level", "standard")).lower()
    if flevel == "advanced":
        score += 10.0
    elif flevel == "intermediate":
        score += 6.0

    # 4. Total Videos Watched (Max 15 pts)
    watched_count = int(stats.get("watch_count") or plan.get("total_videos_watched") or 0)
    score += min(watched_count / 30.0, 1.0) * 15.0

    # 5. Total Searches (Max 10 pts)
    search_count = int(stats.get("search_count") or plan.get("total_searches") or 0)
    score += min(search_count / 20.0, 1.0) * 10.0

    # 6. Watch Duration & Retention (Max 10 pts)
    total_seconds = float(stats.get("total_watch_seconds") or 0.0)
    avg_seconds = total_seconds / max(1, watched_count)
    # Benchmark: Average watch >= 90 seconds per video yields max score
    score += min(avg_seconds / 90.0, 1.0) * 10.0

    # 7. Social Engagement (Likes, Comments, Subs) (Max 15 pts)
    likes = int(stats.get("like_count") or plan.get("total_likes") or 0)
    comments = int(stats.get("comment_count") or plan.get("total_comments") or 0)
    subs = int(stats.get("subscribe_count") or plan.get("total_subscriptions") or 0)
    eng_score = ((min(likes / 15.0, 1.0) * 0.4) + (min(comments / 5.0, 1.0) * 0.3) + (min(subs / 5.0, 1.0) * 0.3)) * 15.0
    score += eng_score

    # 8. Activity Consistency (Active Days) (Max 10 pts)
    active_days = int(stats.get("active_days") or 0)
    score += min(active_days / 7.0, 1.0) * 10.0

    final_score = int(round(min(100.0, max(0.0, score))))
    return final_score


def generate_contextual_comment(
    video_title: str,
    excluded_comments: set[str] | None = None,
) -> str:
    """Choose a non-repeated, language-matched comment template."""
    clean_title = video_title.strip()
    
    # Check language
    is_vietnamese = bool(re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", clean_title, re.IGNORECASE))
    
    if is_vietnamese:
        templates = [
            "Video phân tích rất chi tiết và dễ hiểu, cảm ơn kênh nhiều!",
            "Nội dung chia sẻ rất thực tế và giá trị. Hóng các video tiếp theo của kênh.",
            "Góc nhìn rất hay và sâu sắc, giải thích rất rõ ràng ạ.",
            "Thông tin rất hữu ích cho người mới tìm hiểu như mình. Like mạnh!",
            "Cảm ơn bạn đã tổng hợp đầy đủ và dễ nắm bắt.",
            "Kênh làm nội dung ngày càng chất lượng. Chúc kênh ngày càng phát triển!",
        ]
    else:
        templates = [
            "Great analysis and very well explained! Thanks for sharing.",
            "Super insightful video, really appreciate the detailed breakdown.",
            "Solid points made here. Looking forward to your next upload!",
            "Very informative content, explained in a simple and clear way.",
            "Thanks for breaking this down so clearly. High quality work!",
            "Awesome video, learned a lot from this breakdown!",
        ]
    excluded = {item.strip().casefold() for item in (excluded_comments or set())}
    available = [item for item in templates if item.casefold() not in excluded]
    return random.choice(available) if available else ""


def validate_plan_browser_configuration(plan: dict[str, Any]) -> str:
    """Return the dedicated proxied GPM profile id or raise a safe validation error."""
    channel_db_id = int(plan.get("channel_db_id") or 0)
    channel = db.get_youtube_channel(channel_db_id) if channel_db_id else None
    profile_id = str((channel or {}).get("gpm_profile_id") or plan.get("gpm_profile_id") or "").strip()
    if not profile_id:
        raise ValueError(
            "Kênh chưa được gán Profile GPM. Vui lòng cấu hình trong Channel Hub."
        )
    parsed = parse_profile_target(profile_id)
    if parsed.get("type") != "gpm":
        raise ValueError("Trust Builder chỉ cho phép Profile GPM chuyên dụng.")
    proxy_info = str(
        (channel or {}).get("gpm_proxy_info") or plan.get("gpm_proxy_info") or parsed.get("proxy_info") or ""
    ).strip()
    if not parse_proxy_url(proxy_info):
        raise ValueError("Profile GPM phải có proxy riêng; kết nối Direct bị từ chối.")
    for channel in db.list_youtube_channels():
        if int(channel.get("id") or 0) == channel_db_id:
            continue
        if str(channel.get("gpm_profile_id") or "").strip() == profile_id:
            raise ValueError("Profile GPM này đang được gán cho một kênh khác.")
    return profile_id


def _readiness_check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
    *,
    required: bool = False,
    action: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "required": required,
        "action": action,
    }


def _readiness_summary(checks: list[dict[str, Any]]) -> tuple[int, list[str]]:
    weights = {"pass": 1.0, "warn": 0.5, "unknown": 0.0, "fail": 0.0}
    percentage = int(round(100 * sum(weights.get(str(item["status"]), 0.0) for item in checks) / max(1, len(checks))))
    blockers = [
        str(item["label"])
        for item in checks
        if item.get("required") and item.get("status") != "pass"
    ]
    return percentage, blockers


def _profile_baseline(profile_id: str, detail: dict[str, Any]) -> dict[str, str]:
    """Keep only stable, non-secret profile properties for drift detection."""
    return {
        "profile_id": profile_id,
        "profile_name": str(detail.get("name") or detail.get("profile_name") or "").strip(),
        "os": str(detail.get("os") or "").strip(),
        "browser_type": str(detail.get("browser_type") or detail.get("browser") or "").strip(),
        "browser_version": str(detail.get("browser_version") or "").strip(),
    }


def run_passive_readiness_check(
    plan_id: int,
    *,
    identity_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate readiness without launching or navigating a browser profile."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise ValueError("Không tìm thấy kế hoạch readiness.")
    channel = db.get_youtube_channel(int(plan.get("channel_db_id") or 0)) or {}
    profile_id = str(channel.get("gpm_profile_id") or "").strip()
    proxy_info = str(channel.get("gpm_proxy_info") or "").strip()
    checks: list[dict[str, Any]] = []
    checks.append(_readiness_check(
        "profile_assigned", "Đã gán Profile GPM", "pass" if profile_id else "fail",
        "Kênh đã có profile chuyên dụng." if profile_id else "Chưa gán Profile GPM cho kênh.",
        required=True, action="Cấu hình profile trong Channel Hub",
    ))

    parsed: dict[str, Any] = {}
    if profile_id:
        try:
            parsed = parse_profile_target(profile_id)
        except Exception:
            parsed = {}
    checks.append(_readiness_check(
        "gpm_only", "Profile chuyên dụng", "pass" if parsed.get("type") == "gpm" else "fail",
        "Đang dùng GPM profile." if parsed.get("type") == "gpm" else "Chỉ chấp nhận GPM profile chuyên dụng.",
        required=True,
    ))
    checks.append(_readiness_check(
        "proxy_isolation", "Proxy riêng", "pass" if parse_proxy_url(proxy_info) else "fail",
        "Proxy đã được cấu hình và giữ kín." if parse_proxy_url(proxy_info) else "Thiếu proxy hợp lệ; hệ thống sẽ fail closed.",
        required=True, action="Cấu hình proxy riêng trong Channel Hub",
    ))
    duplicate = False
    if profile_id:
        duplicate = any(
            int(item.get("id") or 0) != int(channel.get("id") or 0)
            and str(item.get("gpm_profile_id") or "").strip() == profile_id
            for item in db.list_youtube_channels()
        )
    checks.append(_readiness_check(
        "exclusive_mapping", "Ánh xạ 1 kênh / 1 profile", "fail" if duplicate else ("pass" if profile_id else "unknown"),
        "Profile không bị dùng chung." if profile_id and not duplicate else ("Profile đang được gán cho kênh khác." if duplicate else "Chưa thể kiểm tra khi thiếu profile."),
        required=True,
    ))

    profile_detail: dict[str, Any] = {}
    profile_error = ""
    if profile_id and parsed.get("type") == "gpm":
        try:
            profile_detail = get_gpm_profile_detail(profile_id)
        except Exception as exc:
            profile_error = security_logging.redact_sensitive(exc)
    checks.append(_readiness_check(
        "profile_exists", "Profile tồn tại trong GPM", "pass" if profile_detail else "unknown",
        "GPM API đã xác nhận profile." if profile_detail else (profile_error or "Chưa thể xác nhận profile qua GPM API."),
        required=True, action="Mở GPM và kiểm tra dịch vụ",
    ))

    runtime = inspect_profile_browser_readiness(profile_id) if profile_id else {}
    runtime_status = "warn" if runtime.get("busy") or runtime.get("requires_user_action") else ("pass" if profile_id else "unknown")
    checks.append(_readiness_check(
        "runtime_state", "Trạng thái browser", runtime_status,
        "Profile đang bận hoặc cần người dùng xử lý." if runtime_status == "warn" else (f"Trạng thái: {runtime.get('state', 'closed')}." if profile_id else "Chưa có profile."),
        action="Đóng tác vụ đang dùng profile rồi kiểm tra lại",
    ))

    current_baseline = _profile_baseline(profile_id, profile_detail) if profile_detail else {}
    saved_baseline = plan.get("profile_baseline") or {}
    comparable_keys = ("profile_id", "os", "browser_type", "browser_version")
    drifted = bool(saved_baseline and current_baseline) and any(
        str(saved_baseline.get(key) or "") != str(current_baseline.get(key) or "")
        for key in comparable_keys
        if saved_baseline.get(key) or current_baseline.get(key)
    )
    baseline_status = "fail" if drifted else ("pass" if current_baseline else "unknown")
    checks.append(_readiness_check(
        "profile_baseline", "Baseline profile", baseline_status,
        "Phát hiện thay đổi thuộc tính profile." if drifted else ("Baseline ổn định hoặc vừa được ghi nhận." if baseline_status == "pass" else "Chưa thể ghi nhận baseline."),
        required=True, action="Xác nhận thay đổi profile trước khi tiếp tục",
    ))

    profile_pct, profile_blockers = _readiness_summary(checks)
    profile_snapshot = {
        "checks": checks,
        "percentage": profile_pct,
        "blockers": profile_blockers,
        "checked_at": db.utc_now(),
        "passive": True,
    }

    channel_checks: list[dict[str, Any]] = []
    channel_checks.append(_readiness_check(
        "channel_record", "Kênh đã kết nối", "pass" if channel.get("channel_id") else "fail",
        "Đã có bản ghi kênh YouTube." if channel.get("channel_id") else "Không tìm thấy Channel ID.",
        required=True,
    ))
    branding = plan.get("branding_checklist") or {}
    for field, label in (("avatar", "Ảnh đại diện"), ("banner", "Ảnh bìa"), ("about", "Mô tả kênh"), ("handle", "Handle")):
        value = branding.get(field)
        status = "pass" if value is True else ("warn" if value is False else "unknown")
        channel_checks.append(_readiness_check(
            f"branding_{field}", label, status,
            "Đã xác minh." if status == "pass" else ("Chưa hoàn thiện." if status == "warn" else "Chưa kiểm tra tương tác."),
            action="Chạy kiểm tra tương tác",
        ))
    feature_level = str(branding.get("feature_level") or "unknown").lower()
    feature_status = "pass" if feature_level in {"intermediate", "advanced"} else ("warn" if feature_level == "standard" else "unknown")
    channel_checks.append(_readiness_check(
        "feature_eligibility", "Cấp tính năng", feature_status,
        f"Mức đã xác minh: {feature_level}." if feature_level != "unknown" else "Chưa xác minh cấp tính năng.",
        action="Chạy kiểm tra tương tác",
    ))

    previous_identity = next(
        (item for item in (plan.get("channel_readiness") or {}).get("checks", []) if item.get("id") == "studio_identity"),
        None,
    )
    if identity_result is not None:
        verified = bool(identity_result.get("identity_verified"))
        identity_check = _readiness_check(
            "studio_identity", "Đúng tài khoản YouTube Studio", "pass" if verified else "fail",
            str(identity_result.get("message") or ("Đã đối chiếu Channel ID." if verified else "Không thể đối chiếu Channel ID.")),
            required=True, action="Đăng nhập đúng kênh trong GPM profile",
        )
    elif previous_identity:
        identity_check = dict(previous_identity)
    else:
        identity_check = _readiness_check(
            "studio_identity", "Đúng tài khoản YouTube Studio", "unknown",
            "Cần kiểm tra tương tác để đối chiếu Channel ID.", required=True,
            action="Chạy kiểm tra tương tác",
        )
    channel_checks.append(identity_check)
    channel_pct, channel_blockers = _readiness_summary(channel_checks)
    channel_snapshot = {
        "checks": channel_checks,
        "percentage": channel_pct,
        "blockers": channel_blockers,
        "checked_at": db.utc_now(),
    }
    all_blockers = profile_blockers + channel_blockers
    state = "ready" if not all_blockers and profile_pct >= 70 and channel_pct >= 70 else "needs_attention"
    baseline_to_store = saved_baseline or current_baseline
    updated = db.update_channel_trust_plan(
        plan_id,
        mode="guided",
        requires_review=False,
        profile_readiness=profile_snapshot,
        channel_readiness=channel_snapshot,
        profile_readiness_pct=profile_pct,
        channel_readiness_pct=channel_pct,
        readiness_state=state,
        profile_baseline=baseline_to_store,
        last_readiness_check_at=db.utc_now(),
        trust_score_estimated=0,
    )
    return {
        "state": state,
        "profile": profile_snapshot,
        "channel": channel_snapshot,
        "blockers": all_blockers,
        "plan": updated,
    }


async def run_interactive_readiness_check(plan_id: int) -> dict[str, Any]:
    """Run user-requested Studio checks inside the assigned GPM profile."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise ValueError("Không tìm thấy kế hoạch readiness.")
    profile_id = validate_plan_browser_configuration(plan)
    channel_id = str(plan.get("youtube_channel_ucid") or "").strip()
    identity = await verify_youtube_login(
        profile_id,
        expected_channel_id=channel_id,
    )
    if identity.get("logged_in"):
        await audit_channel_branding_for_plan(plan_id)
        await audit_feature_eligibility_for_plan(plan_id)
    return run_passive_readiness_check(plan_id, identity_result=identity)


def create_guided_readiness_session(plan_id: int) -> dict[str, Any]:
    """Create a human-operated research checklist; it performs no engagement."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise ValueError("Không tìm thấy kế hoạch readiness.")
    topics = [str(item).strip() for item in (plan.get("niche_keywords") or []) if str(item).strip()]
    sources = [str(item).strip() for item in (plan.get("approved_sources") or []) if str(item).strip()]
    agenda = {
        "notice": "Bạn tự thao tác trong browser; hệ thống không search, xem, cuộn hay tương tác thay bạn.",
        "topics": topics[:5],
        "approved_sources": sources[:10],
        "steps": [
            {"id": "google_search", "label": "Tự tìm kiếm chủ đề trên Google", "required": True},
            {"id": "web_reading", "label": "Tự đọc nguồn đã duyệt", "required": True},
            {"id": "youtube_research", "label": "Tự nghiên cứu YouTube, không ép tương tác", "required": True},
            {"id": "notes", "label": "Ghi lại insight hữu ích", "required": False},
        ],
    }
    return db.create_trust_guided_session(plan_id, agenda)


def get_active_plan_job(plan_id: int) -> dict[str, Any] | None:
    for job in db.list_active_system_jobs(TRUST_JOB_TYPE):
        if int((job.get("payload") or {}).get("plan_id") or 0) == plan_id:
            return job
    return None


def request_cancel_plan_jobs(plan_id: int) -> int:
    canceled = 0
    for job in db.list_active_system_jobs(TRUST_JOB_TYPE):
        if int((job.get("payload") or {}).get("plan_id") or 0) != plan_id:
            continue
        db.request_cancel_system_job(str(job["id"]))
        canceled += 1
    return canceled


def _job_cancel_requested(job_id: str) -> bool:
    if not job_id:
        return False
    job = db.get_system_job(job_id)
    return not job or bool(job.get("cancel_requested"))


def _recent_session_context(plan_id: int) -> tuple[set[str], set[str]]:
    video_ids: set[str] = set()
    comments: set[str] = set()
    for log in db.list_trust_activity_logs(plan_id, limit=200):
        match = re.search(
            r"[?&]v=([a-zA-Z0-9_-]+)",
            str(log.get("target_url") or ""),
        )
        if match:
            video_ids.add(match.group(1))
        comment_text = str((log.get("detail_json") or {}).get("comment_text") or "").strip()
        if comment_text:
            comments.add(comment_text)
    return video_ids, comments


async def run_warmup_session(
    plan_id: int,
    *,
    job_id: str = "",
    require_active: bool = False,
) -> dict[str, Any]:
    """Legacy entry point retained only to reject automated engagement."""
    return {
        "success": False,
        "disabled": True,
        "message": "Automated warm-up đã bị vô hiệu hóa; hãy dùng Guided Readiness.",
    }

    # The legacy implementation remains below temporarily for history/reference,
    # but is intentionally unreachable and cannot launch a browser.
    lock = await _get_plan_lock(plan_id)
    if lock.locked():
        logger.warning("Session cho Plan ID %d đang chạy, bỏ qua yêu cầu trùng lặp.", plan_id)
        return {
            "success": False,
            "skipped": True,
            "message": "Session cho kênh này đang được thực thi.",
        }

    async with lock:
        plan = db.get_channel_trust_plan(plan_id)
        if not plan:
            return {"success": False, "message": f"Không tìm thấy Trust Plan ID {plan_id}."}
        if require_active and plan.get("status") != "active":
            return {
                "success": False,
                "canceled": True,
                "message": "Plan không còn ở trạng thái active.",
            }

        try:
            profile_id = validate_plan_browser_configuration(plan)
        except ValueError as exc:
            message = str(exc)
            db.update_channel_trust_plan(
                plan_id,
                error_message=message,
                status="error" if require_active else plan.get("status", "draft"),
            )
            return {"success": False, "message": message}

        if is_profile_browser_busy(profile_id):
            logger.info("Profile GPM đang bận tác vụ khác. Hoãn Plan ID %d.", plan_id)
            return {
                "success": False,
                "skipped": True,
                "retryable": True,
                "message": "Profile trình duyệt đang bận tác vụ khác.",
            }

        timezone_name = str(plan.get("publication_timezone") or "Asia/Ho_Chi_Minh")
        daily_stats = db.get_trust_daily_activity_stats(plan_id, timezone_name)
        daily_search_target = int(plan.get("daily_search_target") or 0)
        daily_watch_target = int(plan.get("daily_watch_target") or 0)
        if (
            daily_stats.get("search_count", 0) >= daily_search_target
            or daily_stats.get("watch_count", 0) >= daily_watch_target
        ):
            return {
                "success": True,
                "skipped": True,
                "quota_reached": True,
                "message": "Đã đạt quota Search/Watch hôm nay.",
            }

        keywords = plan.get("niche_keywords") or []
        target_channels = plan.get("target_channels") or []
        if not keywords:
            keywords = ["công nghệ", "tin tức xu hướng", "kiến thức thú vị"]

        selected_keyword = random.choice(keywords)
        if target_channels and (not keywords or random.random() < 0.60):
            selected_target_channel = random.choice(target_channels)
        else:
            selected_target_channel = ""
        warmup_phase = str(plan.get("warmup_phase") or "phase_1_consumer")
        excluded_video_ids, used_comments = _recent_session_context(plan_id)

        def should_cancel() -> bool:
            if _job_cancel_requested(job_id):
                return True
            if not require_active:
                return False
            current_plan = db.get_channel_trust_plan(plan_id)
            return not current_plan or current_plan.get("status") != "active"

        session_result = {
            "keyword": selected_keyword,
            "target_channel": selected_target_channel,
            "searched": False,
            "watched": False,
            "liked": False,
            "commented": False,
            "subscribed": False,
            "video_url": "",
            "video_title": "",
        }

        try:
            if should_cancel():
                raise asyncio.CancelledError
            async with channel_browser_session(profile_id) as (context, _browser, _profile_meta):
                page = await context.new_page()
                try:
                    pick_res = await action_search_and_pick_video(
                        page,
                        keyword=selected_keyword,
                        target_channel=selected_target_channel,
                        excluded_video_ids=excluded_video_ids,
                    )
                    video_url = pick_res.get("url", "")
                    video_title = pick_res.get("title", "")
                    session_result["video_url"] = video_url
                    session_result["video_title"] = video_title
                    session_result["searched"] = True

                    db.create_trust_activity_log(
                        plan_id=plan_id,
                        activity_type="search",
                        target_url=video_url,
                        target_title=video_title,
                        detail_json={"keyword": selected_keyword, "matched_channel": pick_res.get("channel", "")},
                        success=True,
                    )

                    if should_cancel():
                        raise asyncio.CancelledError
                    min_watch_minutes = int(plan.get("min_watch_minutes") or 10)
                    min_watch_sec = float(min_watch_minutes * 60)
                    max_watch_sec = max(min_watch_sec, 1200.0)
                    watch_res = await action_watch_video(
                        page,
                        video_url=video_url,
                        min_pct=60.0,
                        max_pct=90.0,
                        min_watch_seconds=min_watch_sec,
                        max_watch_seconds=max_watch_sec,
                        cancel_check=should_cancel,
                    )
                    session_result["watched"] = True

                    db.create_trust_activity_log(
                        plan_id=plan_id,
                        activity_type="watch",
                        target_url=video_url,
                        target_title=video_title,
                        duration_seconds=watch_res.get("watched_seconds", 0.0),
                        detail_json={
                            "total_duration": watch_res.get("total_duration", 0.0),
                            "retention_pct": watch_res.get("retention_percentage", 0.0),
                        },
                        success=True,
                    )

                    # Safety Shield Gatekeeper: Validate safety before any social engagement
                    is_safe_engage, engage_reason = safety.is_safe_for_interaction(
                        title=video_title,
                        channel_name=pick_res.get("channel", ""),
                        channel_url=video_url,
                        channel_handle=pick_res.get("channel", ""),
                    )
                    if not is_safe_engage:
                        logger.warning("SAFETY SHIELD: Hủy tương tác Like/Comment/Sub trên video '%s' do vi phạm: %s", video_title, engage_reason)
                        db.create_trust_activity_log(
                            plan_id=plan_id,
                            activity_type="safety_shield",
                            target_url=video_url,
                            target_title=video_title,
                            error_message=f"Bỏ qua tương tác do vi phạm an toàn: {engage_reason}",
                            success=False,
                        )

                    daily_stats = db.get_trust_daily_activity_stats(plan_id, timezone_name)
                    if warmup_phase == "phase_2_engage" and is_safe_engage and not should_cancel():
                        daily_like_target = int(plan.get("daily_like_target") or 0)
                        if (
                            daily_stats.get("like_count", 0) < daily_like_target
                            and random.random() < 0.75
                        ):
                            like_status = await action_like_video(page)
                            session_result["like_status"] = like_status
                            if like_status == ACTION_PERFORMED:
                                session_result["liked"] = True
                                db.create_trust_activity_log(
                                    plan_id=plan_id,
                                    activity_type="like",
                                    target_url=video_url,
                                    target_title=video_title,
                                    success=True,
                                )

                    daily_stats = db.get_trust_daily_activity_stats(plan_id, timezone_name)
                    if warmup_phase == "phase_2_engage" and not should_cancel():
                        daily_comment_target = int(plan.get("daily_comment_target") or 0)
                        if (
                            daily_stats.get("comment_count", 0) < daily_comment_target
                            and random.random() < 0.50
                        ):
                            comment_text = generate_contextual_comment(
                                video_title,
                                excluded_comments=used_comments,
                            )
                            comment_status = (
                                await action_comment_video(page, comment_text)
                                if comment_text
                                else "already_done"
                            )
                            session_result["comment_status"] = comment_status
                            if comment_status == ACTION_PERFORMED:
                                session_result["commented"] = True
                                db.create_trust_activity_log(
                                    plan_id=plan_id,
                                    activity_type="comment",
                                    target_url=video_url,
                                    target_title=video_title,
                                    detail_json={"comment_text": comment_text},
                                    success=True,
                                )

                    daily_stats = db.get_trust_daily_activity_stats(plan_id, timezone_name)
                    if warmup_phase == "phase_2_engage" and not should_cancel():
                        daily_sub_target = int(plan.get("daily_subscribe_target") or 0)
                        if (
                            daily_stats.get("subscribe_count", 0) < daily_sub_target
                            and random.random() < 0.40
                        ):
                            subscribe_status = await action_subscribe_channel(page)
                            session_result["subscribe_status"] = subscribe_status
                            if subscribe_status == ACTION_PERFORMED:
                                session_result["subscribed"] = True
                                db.create_trust_activity_log(
                                    plan_id=plan_id,
                                    activity_type="subscribe",
                                    target_url=video_url,
                                    target_title=video_title,
                                    success=True,
                                )

                finally:
                    await cleanup_owned_page(context, page)

            stats = db.get_trust_activity_stats(plan_id)
            updated_score = calculate_trust_score(plan, stats)
            next_phase = warmup_phase
            next_status = str(plan.get("status") or "draft")
            phase_started_at = str(plan.get("phase_started_at") or "")
            if warmup_phase in {"idle", "phase_1_consumer"}:
                if stats.get("watch_count", 0) >= 10 and stats.get("search_count", 0) >= 6:
                    next_phase = "phase_2_engage"
                    phase_started_at = db.utc_now()
            elif warmup_phase == "phase_2_engage":
                if updated_score >= 70:
                    next_phase = "phase_3_ready"
                    next_status = "completed"
                    phase_started_at = db.utc_now()

            db.update_channel_trust_plan(
                plan_id=plan_id,
                total_videos_watched=stats.get("watch_count", 0),
                total_searches=stats.get("search_count", 0),
                total_likes=stats.get("like_count", 0),
                total_comments=stats.get("comment_count", 0),
                total_subscriptions=stats.get("subscribe_count", 0),
                trust_score_estimated=updated_score,
                warmup_phase=next_phase,
                phase_started_at=phase_started_at,
                status=next_status,
                last_session_at=db.utc_now(),
                error_message="",
            )

            logger.info(
                "Hoàn thành session Trust Builder cho Plan ID %d. Readiness: %d/100",
                plan_id,
                updated_score,
            )
            return {
                "success": True,
                "plan_id": plan_id,
                "trust_score": updated_score,
                "warmup_phase": next_phase,
                "details": session_result,
            }
        except asyncio.CancelledError:
            logger.info("Session Trust Builder Plan ID %d đã dừng tại checkpoint.", plan_id)
            return {"success": False, "canceled": True, "message": "Session đã được dừng."}
        except Exception as exc:
            safe_error = security_logging.redact_sensitive(exc)
            logger.error(
                "Lỗi khi chạy Trust Builder session (Plan ID %d): %s",
                plan_id,
                safe_error,
            )
            if db.get_channel_trust_plan(plan_id):
                db.update_channel_trust_plan(plan_id, error_message=safe_error)
                db.create_trust_activity_log(
                    plan_id=plan_id,
                    activity_type="error",
                    error_message=safe_error,
                    success=False,
                )
            return {"success": False, "message": f"Lỗi session: {safe_error}"}


async def audit_channel_branding_for_plan(plan_id: int) -> dict[str, Any]:
    """Run a branding audit for the channel via GPM browser session."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        return {"success": False, "message": f"Không tìm thấy Plan ID {plan_id}."}

    try:
        profile_id = validate_plan_browser_configuration(plan)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    channel_ucid = str(plan.get("youtube_channel_ucid") or plan.get("channel_id") or "").strip()

    try:
        async with channel_browser_session(profile_id) as (context, _browser, _profile_meta):
            page = await context.new_page()
            try:
                checklist = await action_audit_channel_branding(page, channel_id=channel_ucid)
            finally:
                await cleanup_owned_page(context, page)

        db.update_channel_trust_plan(
            plan_id=plan_id,
            branding_checklist=checklist,
            trust_score_estimated=0,
        )

        db.create_trust_activity_log(
            plan_id=plan_id,
            activity_type="branding_audit",
            detail_json=checklist,
            success=True,
            activity_source="interactive_readiness",
        )

        return {
            "success": True,
            "checklist": checklist,
        }
    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Lỗi khi Audit Branding kênh: %s", safe_error)
        return {"success": False, "message": f"Không thể audit kênh: {safe_error}"}


async def audit_feature_eligibility_for_plan(plan_id: int) -> dict[str, Any]:
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        return {"success": False, "message": f"Không tìm thấy Plan ID {plan_id}."}
    try:
        profile_id = validate_plan_browser_configuration(plan)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    channel_ucid = str(plan.get("youtube_channel_ucid") or "").strip()
    try:
        async with channel_browser_session(profile_id) as (context, _browser, _profile_meta):
            page = await context.new_page()
            try:
                feature_result = await action_audit_feature_eligibility(
                    page,
                    channel_id=channel_ucid,
                )
            finally:
                await cleanup_owned_page(context, page)

        checklist = dict(plan.get("branding_checklist") or {})
        checklist["feature_level"] = feature_result.get("feature_level", "unknown")
        updated = db.update_channel_trust_plan(
            plan_id,
            branding_checklist=checklist,
            trust_score_estimated=0,
        )
        db.create_trust_activity_log(
            plan_id=plan_id,
            activity_type="feature_audit",
            detail_json=feature_result,
            success=bool(feature_result.get("verified")),
            activity_source="interactive_readiness",
        )
        return {
            "success": True,
            "verified": bool(feature_result.get("verified")),
            "feature_level": checklist["feature_level"],
            "plan": updated,
        }
    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Lỗi khi Audit Feature Eligibility: %s", safe_error)
        return {"success": False, "message": f"Không thể kiểm tra tính năng: {safe_error}"}
