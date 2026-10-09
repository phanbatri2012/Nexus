"""FastAPI routes for explainable channel and profile readiness."""

from __future__ import annotations

from typing import List, Literal, Optional
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from auto_yt.services import database as db, security_logging
from auto_yt.services.proxy_utils import parse_proxy_url
from auto_yt.services.trust_builder_service import (
    create_guided_readiness_session,
    get_active_plan_job,
    request_cancel_plan_jobs,
    run_interactive_readiness_check,
    run_passive_readiness_check,
)
from auto_yt.services import trust_builder_scheduler

router = APIRouter(tags=["Channel & Profile Readiness"])


def _public_plan(plan: dict | None) -> dict | None:
    """Return plan data without exposing proxy credentials to the frontend."""
    if plan is None:
        return None
    public_plan = dict(plan)
    public_plan.pop("gpm_proxy_info", None)
    channel = db.get_youtube_channel(int(public_plan.get("channel_db_id") or 0))
    public_plan["gpm_proxy_configured"] = bool(parse_proxy_url(str((channel or {}).get("gpm_proxy_info") or "")))
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
    approved_sources: List[str] = Field(default_factory=list, max_length=20)
    reminder_enabled: bool = False
    reminder_time_local: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

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

    @field_validator("approved_sources")
    @classmethod
    def validate_sources(cls, values: List[str]) -> List[str]:
        from urllib.parse import urlparse

        normalized: list[str] = []
        for value in values:
            clean_value = str(value or "").strip()
            parsed = urlparse(clean_value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Nguồn đã duyệt phải là URL HTTPS công khai, không chứa credential.")
            if clean_value not in normalized:
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
    approved_sources: Optional[List[str]] = Field(default=None, max_length=20)
    reminder_enabled: Optional[bool] = None
    reminder_time_local: Optional[str] = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @field_validator("niche_keywords", "target_channels")
    @classmethod
    def validate_optional_tags(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        if values is None:
            return None
        return TrustPlanCreateRequest.validate_tags(values)

    @field_validator("approved_sources")
    @classmethod
    def validate_optional_sources(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        return None if values is None else TrustPlanCreateRequest.validate_sources(values)


class GuidedSessionUpdateRequest(BaseModel):
    status: Optional[Literal["ready", "in_progress", "completed", "abandoned"]] = None
    checklist: Optional[dict[str, bool]] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


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
        approved_sources=req.approved_sources,
        warmup_phase=req.warmup_phase,
        status="paused",
    )
    new_plan = db.update_channel_trust_plan(
        int(new_plan["id"]),
        mode="guided",
        requires_review=False,
        reminder_enabled=req.reminder_enabled,
        reminder_time_local=req.reminder_time_local,
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
    """Retired: automated engagement is intentionally unavailable."""
    raise HTTPException(status_code=410, detail="Automated warm-up đã ngừng. Hãy dùng Guided Readiness.")


@router.post("/plans/{plan_id}/pause")
def pause_plan(plan_id: int):
    """Pause automated trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    request_cancel_plan_jobs(plan_id)
    updated = db.update_channel_trust_plan(plan_id, status="paused", reminder_enabled=False, next_run_at="")
    return {"success": True, "plan": _public_plan(updated)}


@router.post("/plans/{plan_id}/resume")
def resume_plan(plan_id: int):
    """Retired: automated engagement is intentionally unavailable."""
    raise HTTPException(status_code=410, detail="Automated warm-up đã ngừng. Hãy bật reminder cho Guided Readiness.")


@router.post("/plans/{plan_id}/run-session")
async def trigger_run_session(plan_id: int):
    """Retired: automated engagement is intentionally unavailable."""
    raise HTTPException(status_code=410, detail="Automated warm-up đã ngừng. Hãy tạo Guided Session.")


@router.get("/plans/{plan_id}/readiness")
def get_readiness(plan_id: int):
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy kế hoạch readiness.")
    return {
        "state": plan.get("readiness_state") or "not_checked",
        "profile": plan.get("profile_readiness") or {},
        "channel": plan.get("channel_readiness") or {},
        "last_checked_at": plan.get("last_readiness_check_at") or "",
    }


@router.post("/plans/{plan_id}/checks/passive")
def check_passive_readiness(plan_id: int):
    try:
        result = run_passive_readiness_check(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["plan"] = _public_plan(result.get("plan"))
    return result


@router.post("/plans/{plan_id}/checks/interactive")
async def check_interactive_readiness(plan_id: int):
    try:
        result = await run_interactive_readiness_check(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_id = security_logging.report_exception("interactive readiness check", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Không thể kiểm tra Studio. Mã lỗi: {error_id}.",
        ) from exc
    result["plan"] = _public_plan(result.get("plan"))
    return result


@router.post("/plans/{plan_id}/guided-sessions")
def create_guided_session(plan_id: int):
    try:
        session = create_guided_readiness_session(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "session": session}


@router.get("/plans/{plan_id}/guided-sessions")
def list_guided_sessions(plan_id: int, limit: int = Query(30, ge=1, le=200)):
    if not db.get_channel_trust_plan(plan_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy kế hoạch readiness.")
    return {"sessions": db.list_trust_guided_sessions(plan_id, limit=limit)}


@router.patch("/plans/{plan_id}/guided-sessions/{session_id}")
def update_guided_session(plan_id: int, session_id: int, req: GuidedSessionUpdateRequest):
    session = db.get_trust_guided_session(session_id)
    if not session or int(session.get("plan_id") or 0) != plan_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy Guided Session.")
    changes = req.model_dump(exclude_unset=True)
    now = db.utc_now()
    if changes.get("status") == "in_progress" and not session.get("started_at"):
        changes["started_at"] = now
    if changes.get("status") in {"completed", "abandoned"}:
        changes["completed_at"] = now
    updated = db.update_trust_guided_session(session_id, **changes)
    return {"success": True, "session": updated}


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
    """Expose legacy activity only as historical context, never as trust."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    stats = db.get_trust_activity_stats(plan_id)
    return {
        "legacy_activity": stats,
        "profile_readiness_pct": int(plan.get("profile_readiness_pct") or 0),
        "channel_readiness_pct": int(plan.get("channel_readiness_pct") or 0),
    }


@router.post("/plans/{plan_id}/branding-audit")
async def audit_branding(plan_id: int):
    raise HTTPException(status_code=410, detail="Dùng endpoint checks/interactive để kiểm tra có đối chiếu danh tính.")


@router.post("/plans/{plan_id}/verify-features")
async def verify_features(plan_id: int):
    raise HTTPException(status_code=410, detail="Dùng endpoint checks/interactive để kiểm tra có đối chiếu danh tính.")


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
            changes["remote_sync_url"] = safety.validate_remote_sync_url(changes["remote_sync_url"])
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
