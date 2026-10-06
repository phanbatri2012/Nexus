"""Trust Builder Service: Orchestrator, session runner, and scoring engine.

Coordinates warmup sessions, calculates 100-point trust heuristic scores,
generates contextual AI comments, and records granular activity logs.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import random
import re
from typing import Any

from auto_yt.services import database as db
from auto_yt.services.channel_scanner_service import (
    channel_browser_session,
    cleanup_owned_page,
    is_profile_browser_busy,
)
from auto_yt.services.trust_builder_actions import (
    action_audit_channel_branding,
    action_comment_video,
    action_like_video,
    action_search_and_pick_video,
    action_subscribe_channel,
    action_watch_video,
)

logger = logging.getLogger(__name__)

# Mutex lock per plan_id to prevent concurrent runs on the same plan
_plan_locks: dict[int, asyncio.Lock] = {}
_plan_guard = asyncio.Lock()


async def _get_plan_lock(plan_id: int) -> asyncio.Lock:
    async with _plan_guard:
        if plan_id not in _plan_locks:
            _plan_locks[plan_id] = asyncio.Lock()
        return _plan_locks[plan_id]


def calculate_trust_score(plan: dict[str, Any], stats: dict[str, Any]) -> int:
    """Calculate an internal 0-100 heuristic trust score based on 8 criteria."""
    score = 0.0

    # 1. Account Aging (Max 15 pts) - based on days since plan creation
    created_at_str = str(plan.get("created_at") or "")
    days_active = 1
    if created_at_str:
        try:
            created_dt = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            days_active = max(1, (datetime.datetime.now(datetime.timezone.utc) - created_dt).days)
        except Exception:
            days_active = 1
    score += min(days_active / 14.0, 1.0) * 15.0

    # 2. Branding Completeness (Max 15 pts)
    checklist = plan.get("branding_checklist") or {}
    checked_count = sum(1 for v in checklist.values() if v is True or v == "advanced" or v == "intermediate")
    score += min(checked_count / 7.0, 1.0) * 15.0

    # 3. Feature Eligibility (Max 10 pts)
    flevel = str(checklist.get("feature_level", "standard")).lower()
    if flevel == "advanced":
        score += 10.0
    elif flevel == "intermediate":
        score += 6.0
    else:
        score += 2.0

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
    active_days = int(stats.get("active_days") or 1)
    score += min(active_days / 7.0, 1.0) * 10.0

    final_score = int(round(min(100.0, max(0.0, score))))
    return final_score


def generate_contextual_comment(video_title: str) -> str:
    """Generate a realistic, appreciative comment tailored to the video title."""
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
    return random.choice(templates)


async def run_warmup_session(plan_id: int) -> dict[str, Any]:
    """Execute one full, safe warmup session for a channel trust plan."""
    lock = await _get_plan_lock(plan_id)
    if lock.locked():
        logger.warning("Session cho Plan ID %d đang chạy, bỏ qua yêu cầu trùng lặp.", plan_id)
        return {"success": False, "message": "Session cho kênh này đang được thực thi."}

    async with lock:
        plan = db.get_channel_trust_plan(plan_id)
        if not plan:
            return {"success": False, "message": f"Không tìm thấy Trust Plan ID {plan_id}."}

        profile_id = str(plan.get("gpm_profile_id") or "").strip()
        if not profile_id:
            msg = "Kênh chưa được gán Profile GPM. Vui lòng vào Channel Hub để gán Profile GPM trước."
            db.update_channel_trust_plan(plan_id, error_message=msg, status="error")
            return {"success": False, "message": msg}

        # Check if browser profile is currently busy with another operation (e.g. Upload, Comment)
        if is_profile_browser_busy(profile_id):
            logger.info("Profile GPM %s đang bận tác vụ khác. Hoãn phiên nuôi kênh.", profile_id)
            return {"success": False, "message": "Profile trình duyệt đang bận tác vụ khác."}

        # Update status to active
        db.update_channel_trust_plan(plan_id, status="active", error_message="")

        keywords = plan.get("niche_keywords") or []
        target_channels = plan.get("target_channels") or []
        
        if not keywords:
            keywords = ["công nghệ", "tin tức xu hướng", "kiến thức thú vị"]

        selected_keyword = random.choice(keywords)
        selected_target_channel = random.choice(target_channels) if target_channels else ""

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
            async with channel_browser_session(profile_id) as (context, _browser, _profile_meta):
                page = await context.new_page()
                try:
                    # 1. Search & Pick Video
                    pick_res = await action_search_and_pick_video(
                        page,
                        keyword=selected_keyword,
                        target_channel=selected_target_channel,
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

                    # 2. Watch Video
                    watch_res = await action_watch_video(page, video_url=video_url)
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

                    # 3. Like Video (Probabilistic based on daily target)
                    daily_like_target = int(plan.get("daily_like_target") or 3)
                    if daily_like_target > 0 and random.random() < 0.75:
                        like_ok = await action_like_video(page)
                        if like_ok:
                            session_result["liked"] = True
                            db.create_trust_activity_log(
                                plan_id=plan_id,
                                activity_type="like",
                                target_url=video_url,
                                target_title=video_title,
                                success=True,
                            )

                    # 4. Comment Video (if phase allows: phase_2_engage or phase_3_ready)
                    warmup_phase = str(plan.get("warmup_phase") or "phase_1_consumer")
                    daily_comment_target = int(plan.get("daily_comment_target") or 1)
                    if warmup_phase in ("phase_2_engage", "phase_3_ready") and daily_comment_target > 0 and random.random() < 0.50:
                        comment_text = generate_contextual_comment(video_title)
                        comment_ok = await action_comment_video(page, comment_text)
                        if comment_ok:
                            session_result["commented"] = True
                            db.create_trust_activity_log(
                                plan_id=plan_id,
                                activity_type="comment",
                                target_url=video_url,
                                target_title=video_title,
                                detail_json={"comment_text": comment_text},
                                success=True,
                            )

                    # 5. Subscribe Channel (if phase allows)
                    daily_sub_target = int(plan.get("daily_subscribe_target") or 1)
                    if warmup_phase in ("phase_2_engage", "phase_3_ready") and daily_sub_target > 0 and random.random() < 0.40:
                        sub_ok = await action_subscribe_channel(page)
                        if sub_ok:
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

            # Update accumulated counters and recalculate Trust Score
            stats = db.get_trust_activity_stats(plan_id)
            updated_score = calculate_trust_score(plan, stats)
            
            # Auto-advance phase if threshold met
            next_phase = warmup_phase
            if warmup_phase == "idle" or warmup_phase == "phase_1_consumer":
                if stats.get("watch_count", 0) >= 10 and stats.get("search_count", 0) >= 6:
                    next_phase = "phase_2_engage"
            elif warmup_phase == "phase_2_engage":
                if updated_score >= 70:
                    next_phase = "phase_3_ready"

            db.update_channel_trust_plan(
                plan_id=plan_id,
                total_videos_watched=stats.get("watch_count", 0),
                total_searches=stats.get("search_count", 0),
                total_likes=stats.get("like_count", 0),
                total_comments=stats.get("comment_count", 0),
                total_subscriptions=stats.get("subscribe_count", 0),
                trust_score_estimated=updated_score,
                warmup_phase=next_phase,
                last_session_at=db.utc_now(),
                error_message="",
            )

            logger.info("Hoàn thành session nuôi kênh cho Plan ID %d. Điểm Trust hiện tại: %d/100", plan_id, updated_score)
            return {
                "success": True,
                "plan_id": plan_id,
                "trust_score": updated_score,
                "warmup_phase": next_phase,
                "details": session_result,
            }

        except Exception as exc:
            logger.error("Lỗi khi chạy session nuôi kênh (Plan ID %d): %s", plan_id, exc)
            db.update_channel_trust_plan(plan_id, error_message=str(exc))
            db.create_trust_activity_log(
                plan_id=plan_id,
                activity_type="error",
                error_message=str(exc),
                success=False,
            )
            return {"success": False, "message": f"Lỗi session: {exc}"}


async def audit_channel_branding_for_plan(plan_id: int) -> dict[str, Any]:
    """Run a branding audit for the channel via GPM browser session."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        return {"success": False, "message": f"Không tìm thấy Plan ID {plan_id}."}

    profile_id = str(plan.get("gpm_profile_id") or "").strip()
    if not profile_id:
        return {"success": False, "message": "Kênh chưa được gán Profile GPM."}

    try:
        async with channel_browser_session(profile_id) as (context, _browser, _profile_meta):
            page = await context.new_page()
            try:
                checklist = await action_audit_channel_branding(page)
            finally:
                await cleanup_owned_page(context, page)

        # Update checklist and recalculate score
        stats = db.get_trust_activity_stats(plan_id)
        plan["branding_checklist"] = checklist
        new_score = calculate_trust_score(plan, stats)

        db.update_channel_trust_plan(
            plan_id=plan_id,
            branding_checklist=checklist,
            trust_score_estimated=new_score,
        )

        db.create_trust_activity_log(
            plan_id=plan_id,
            activity_type="branding_audit",
            detail_json=checklist,
            success=True,
        )

        return {
            "success": True,
            "checklist": checklist,
            "trust_score": new_score,
        }
    except Exception as exc:
        logger.error("Lỗi khi Audit Branding kênh: %s", exc)
        return {"success": False, "message": f"Không thể audit kênh: {exc}"}
