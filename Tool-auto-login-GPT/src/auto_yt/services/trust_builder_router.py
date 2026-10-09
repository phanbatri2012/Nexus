"""FastAPI APIRouter for Channel Trust Builder module.

Provides endpoints for managing trust plans, reviewing activity logs,
triggering automated warmup sessions, and inspecting channel branding.
"""

from __future__ import annotations

from typing import List, Literal, Optional
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from auto_yt.services import database as db
from auto_yt.services.proxy_utils import parse_proxy_url
from auto_yt.services.trust_builder_service import (
    audit_channel_branding_for_plan,
    audit_feature_eligibility_for_plan,
    calculate_trust_score,
    get_active_plan_job,
    request_cancel_plan_jobs,
    validate_plan_browser_configuration,
)
from auto_yt.services import trust_builder_scheduler

router = APIRouter(tags=["Trust Builder"])


def _public_plan(plan: dict | None) -> dict | None:
    """Return plan data without exposing proxy credentials to the frontend."""
    if plan is None:
        return None
    public_plan = dict(plan)
    public_plan.pop("gpm_proxy_info", None)
    public_plan.pop("gpm_proxy_info_encrypted", None)
    channel = db.get_youtube_channel(int(public_plan.get("channel_db_id") or 0))
    public_plan["gpm_proxy_configured"] = bool(
        parse_proxy_url(str((channel or {}).get("gpm_proxy_info") or ""))
    )
    return public_plan


def _public_audit_result(result: dict) -> dict:
    public_result = dict(result)
    if isinstance(public_result.get("plan"), dict):
        public_result["plan"] = _public_plan(public_result["plan"])
    return public_result


class TrustPlanCreateRequest(BaseModel):
    channel_db_id: int = Field(gt=0)
    niche_keywords: List[str] = Field(default_factory=list, max_length=20)
    target_channels: List[str] = Field(default_factory=list, max_length=20)
    daily_watch_target: int = Field(default=5, ge=1, le=15)
    daily_search_target: int = Field(default=3, ge=1, le=10)
    daily_like_target: int = Field(default=3, ge=0, le=10)
    daily_comment_target: int = Field(default=0, ge=0, le=5)
    daily_subscribe_target: int = Field(default=0, ge=0, le=5)
    min_watch_minutes: int = Field(default=10, ge=1, le=30)
    warmup_phase: Literal["phase_1_consumer", "phase_2_engage"] = "phase_1_consumer"

    @field_validator("niche_keywords", "target_channels")
    @classmethod
    def validate_tags(cls, values: List[str]) -> List[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean_value = str(value or "").strip()
            if not clean_value or len(clean_value) > 120:
                raise ValueError("Từ khóa/kênh mục tiêu phải dài từ 1 đến 120 ký tự.")
            dedupe_key = clean_value.casefold()
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                normalized.append(clean_value)
        return normalized


class TrustPlanUpdateRequest(BaseModel):
    niche_keywords: Optional[List[str]] = Field(default=None, max_length=20)
    target_channels: Optional[List[str]] = Field(default=None, max_length=20)
    daily_watch_target: Optional[int] = Field(default=None, ge=1, le=15)
    daily_search_target: Optional[int] = Field(default=None, ge=1, le=10)
    daily_like_target: Optional[int] = Field(default=None, ge=0, le=10)
    daily_comment_target: Optional[int] = Field(default=None, ge=0, le=5)
    daily_subscribe_target: Optional[int] = Field(default=None, ge=0, le=5)
    min_watch_minutes: Optional[int] = Field(default=None, ge=1, le=30)

    @field_validator("niche_keywords", "target_channels")
    @classmethod
    def validate_optional_tags(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        if values is None:
            return None
        return TrustPlanCreateRequest.validate_tags(values)


@router.get("/plans")
def list_plans(response: Response):
    """List all channel trust plans with channel info and estimated trust score."""
    response.headers["Cache-Control"] = "no-store"
    plans = [_public_plan(plan) for plan in db.list_channel_trust_plans()]
    return {"plans": plans, "total": len(plans)}


@router.get("/plans/{plan_id}")
def get_plan_detail(plan_id: int, response: Response):
    """Get single trust plan detail with latest stats."""
    response.headers["Cache-Control"] = "no-store"
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    stats = db.get_trust_activity_stats(plan_id)
    daily_stats = db.get_trust_daily_activity_stats(
        plan_id,
        str(plan.get("publication_timezone") or "Asia/Ho_Chi_Minh"),
    )
    return {
        "plan": _public_plan(plan),
        "stats": stats,
        "daily_stats": daily_stats,
        "active_job": get_active_plan_job(plan_id),
    }


@router.post("/plans")
def create_plan(req: TrustPlanCreateRequest):
    """Create a new trust plan for a YouTube channel."""
    # Check if channel exists
    channel = db.get_youtube_channel(req.channel_db_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy YouTube Channel tương ứng.")

    # Check duplicate
    existing = db.get_channel_trust_plan_by_channel_id(req.channel_db_id)
    if existing:
        raise HTTPException(status_code=400, detail="Kênh này đã có Trust Plan. Vui lòng chỉnh sửa Plan hiện có.")

    new_plan = db.create_channel_trust_plan(
        channel_db_id=req.channel_db_id,
        niche_keywords=req.niche_keywords,
        target_channels=req.target_channels,
        daily_watch_target=req.daily_watch_target,
        daily_search_target=req.daily_search_target,
        daily_like_target=req.daily_like_target,
        daily_comment_target=req.daily_comment_target,
        daily_subscribe_target=req.daily_subscribe_target,
        min_watch_minutes=req.min_watch_minutes,
        warmup_phase=req.warmup_phase,
        status="draft",
    )
    return {"success": True, "plan": _public_plan(new_plan)}


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, req: TrustPlanUpdateRequest):
    """Update trust plan configuration or targets."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")

    changes = req.model_dump(exclude_unset=True)
    updated = db.update_channel_trust_plan(plan_id, **changes)
    return {"success": True, "plan": _public_plan(updated)}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    """Delete trust plan and all associated logs."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    request_cancel_plan_jobs(plan_id)
    ok = db.delete_channel_trust_plan(plan_id)
    return {"success": ok}


@router.post("/plans/{plan_id}/start")
def start_plan(plan_id: int):
    """Start automated trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    if plan.get("status") == "completed":
        raise HTTPException(
            status_code=400,
            detail="Plan đã hoàn tất. Hãy tạo plan mới nếu cần warm-up lại.",
        )
    try:
        validate_plan_browser_configuration(plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current_phase = plan.get("warmup_phase")
    if current_phase == "idle":
        current_phase = "phase_1_consumer"

    updated = db.update_channel_trust_plan(
        plan_id,
        status="active",
        mode="automated",
        requires_review=False,
        warmup_phase=current_phase,
        phase_started_at=plan.get("phase_started_at") or db.utc_now(),
        next_run_at="",
        error_message="",
    )
    return {"success": True, "plan": _public_plan(updated)}


@router.post("/plans/{plan_id}/pause")
def pause_plan(plan_id: int):
    """Pause automated trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    request_cancel_plan_jobs(plan_id)
    updated = db.update_channel_trust_plan(plan_id, status="paused")
    return {"success": True, "plan": _public_plan(updated)}


@router.post("/plans/{plan_id}/resume")
def resume_plan(plan_id: int):
    """Resume paused trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    try:
        validate_plan_browser_configuration(plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = db.update_channel_trust_plan(
        plan_id,
        status="active",
        mode="automated",
        requires_review=False,
        next_run_at="",
        error_message="",
    )
    return {"success": True, "plan": _public_plan(updated)}


@router.post("/plans/{plan_id}/run-session")
async def trigger_run_session(plan_id: int):
    """Manually trigger one immediate warmup session."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")

    try:
        job, created = trust_builder_scheduler.enqueue_trust_builder_session(
            plan_id,
            source="manual",
            run_immediately=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "created": created,
        "message": (
            "Đã xếp Trust Builder session vào hàng đợi."
            if created
            else "Plan đã có một Trust Builder session đang hoạt động."
        ),
        "plan_id": plan_id,
        "job": job,
    }


@router.get("/plans/{plan_id}/activities")
def list_activities(
    plan_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List paginated activity logs for a trust plan."""
    if not db.get_channel_trust_plan(plan_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    logs = db.list_trust_activity_logs(plan_id, limit=limit, offset=offset)
    return {"logs": logs, "limit": limit, "offset": offset}


@router.get("/plans/{plan_id}/stats")
def get_stats(plan_id: int):
    """Get aggregated activity stats for a trust plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    stats = db.get_trust_activity_stats(plan_id)
    score = calculate_trust_score(plan, stats)
    return {"stats": stats, "trust_score_estimated": score}


@router.post("/plans/{plan_id}/branding-audit")
async def audit_branding(plan_id: int):
    """Audit channel profile branding elements via GPM browser session."""
    res = await audit_channel_branding_for_plan(plan_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "Lỗi khi audit kênh."))
    return _public_audit_result(res)


@router.post("/plans/{plan_id}/verify-features")
async def verify_features(plan_id: int):
    """Inspect YouTube Studio and preserve unknown when eligibility is not provable."""
    result = await audit_feature_eligibility_for_plan(plan_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message", "Không thể kiểm tra cấp tính năng."),
        )
    return _public_audit_result(result)


@router.get("/scheduler-status")
def scheduler_status():
    return trust_builder_scheduler.get_trust_builder_scheduler_status()


# ---------------------------------------------------------------------------
# Safety Shield & Blacklist Endpoints
# ---------------------------------------------------------------------------


class SafetyConfigRequest(BaseModel):
    shield_enabled: Optional[bool] = None
    remote_sync_url: Optional[str] = None
    auto_sync_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)


class SafetyBlacklistCreateRequest(BaseModel):
    entry_type: Literal["channel", "keyword", "regex_pattern"] = "channel"
    entry_value: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=300)
    is_custom: int = 1
    is_enabled: bool = True


@router.get("/safety/config")
def get_safety_config_endpoint():
    """Get Vietnam Safety Shield configuration and total rule counts."""
    from auto_yt.services import trust_builder_safety as safety
    safety.ensure_default_safety_rules_seeded()
    config = db.get_safety_config()
    rules = db.get_all_active_safety_rules()
    return {
        "config": config,
        "counts": {
            "channels": len(rules.get("channels") or []),
            "keywords": len(rules.get("keywords") or []),
            "regex_patterns": len(rules.get("regex_patterns") or []),
            "total_active": len(rules.get("channels") or []) + len(rules.get("keywords") or []) + len(rules.get("regex_patterns") or []),
        },
    }


@router.post("/safety/config")
def update_safety_config_endpoint(req: SafetyConfigRequest):
    """Update Vietnam Safety Shield settings."""
    changes = req.model_dump(exclude_unset=True)
    if changes.get("remote_sync_url"):
        from auto_yt.services import trust_builder_safety as safety
        try:
            changes["remote_sync_url"] = safety.validate_remote_sync_url(
                changes["remote_sync_url"]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = db.update_safety_config(**changes)
    return {"success": True, "config": updated}


@router.post("/safety/sync")
def sync_safety_blacklist_endpoint():
    """Trigger manual 1-click sync of Core Safety Blacklist from remote URL."""
    from auto_yt.services import trust_builder_safety as safety
    result = safety.sync_safety_blacklist_from_remote()
    if not result.get("success") and not result.get("offline_fallback"):
        raise HTTPException(status_code=400, detail=result.get("message", "Lỗi đồng bộ danh mục đen."))
    return result


@router.get("/safety/blacklist")
def list_safety_blacklist_endpoint(
    entry_type: str = Query("", description="Filter by channel, keyword, or regex_pattern"),
    search: str = Query("", description="Search text in values or reasons"),
    is_custom: Optional[int] = Query(None, description="0 for core, 1 for custom"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List paginated blacklist rules with search and filter support."""
    from auto_yt.services import trust_builder_safety as safety
    safety.ensure_default_safety_rules_seeded()
    entries = db.list_safety_blacklist_entries(
        entry_type=entry_type,
        search=search,
        is_custom=is_custom,
        limit=limit,
        offset=offset,
    )
    return {"entries": entries, "limit": limit, "offset": offset, "total": len(entries)}


@router.post("/safety/blacklist")
def create_safety_blacklist_endpoint(req: SafetyBlacklistCreateRequest):
    """Add a new custom rule or block a channel in 1-click."""
    try:
        entry_value = req.entry_value
        if req.entry_type == "regex_pattern":
            from auto_yt.services import trust_builder_safety as safety
            entry_value = safety.validate_regex_pattern(entry_value)
        entry = db.create_safety_blacklist_entry(
            entry_type=req.entry_type,
            entry_value=entry_value,
            reason=req.reason or "Người dùng thêm thủ công",
            is_custom=req.is_custom,
            is_enabled=1 if req.is_enabled else 0,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "entry": entry}


@router.delete("/safety/blacklist/{entry_id}")
def delete_safety_blacklist_endpoint(entry_id: int):
    """Delete a blacklist rule by ID."""
    entry = db.get_safety_blacklist_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không tìm thấy quy tắc chặn.")
    deleted = db.delete_safety_blacklist_entry(entry_id)
    return {"success": deleted}
