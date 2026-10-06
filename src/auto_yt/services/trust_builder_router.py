"""FastAPI APIRouter for Channel Trust Builder module.

Provides endpoints for managing trust plans, reviewing activity logs,
triggering automated warmup sessions, and inspecting channel branding.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from auto_yt.services import database as db
from auto_yt.services.trust_builder_service import (
    audit_channel_branding_for_plan,
    calculate_trust_score,
    run_warmup_session,
)

router = APIRouter(tags=["Trust Builder"])


class TrustPlanCreateRequest(BaseModel):
    channel_db_id: int
    niche_keywords: List[str] = Field(default_factory=list)
    target_channels: List[str] = Field(default_factory=list)
    daily_watch_target: int = 5
    daily_search_target: int = 3
    daily_like_target: int = 3
    daily_comment_target: int = 1
    daily_subscribe_target: int = 1
    warmup_phase: str = "phase_1_consumer"


class TrustPlanUpdateRequest(BaseModel):
    niche_keywords: Optional[List[str]] = None
    target_channels: Optional[List[str]] = None
    daily_watch_target: Optional[int] = None
    daily_search_target: Optional[int] = None
    daily_like_target: Optional[int] = None
    daily_comment_target: Optional[int] = None
    daily_subscribe_target: Optional[int] = None
    warmup_phase: Optional[str] = None
    status: Optional[str] = None
    branding_checklist: Optional[dict] = None


@router.get("/plans")
def list_plans():
    """List all channel trust plans with channel info and estimated trust score."""
    plans = db.list_channel_trust_plans()
    return {"plans": plans, "total": len(plans)}


@router.get("/plans/{plan_id}")
def get_plan_detail(plan_id: int):
    """Get single trust plan detail with latest stats."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    stats = db.get_trust_activity_stats(plan_id)
    return {"plan": plan, "stats": stats}


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
        warmup_phase=req.warmup_phase,
        status="draft",
    )
    return {"success": True, "plan": new_plan}


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, req: TrustPlanUpdateRequest):
    """Update trust plan configuration or targets."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")

    changes = req.model_dump(exclude_unset=True)
    updated = db.update_channel_trust_plan(plan_id, **changes)
    return {"success": True, "plan": updated}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    """Delete trust plan and all associated logs."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    ok = db.delete_channel_trust_plan(plan_id)
    return {"success": ok}


@router.post("/plans/{plan_id}/start")
def start_plan(plan_id: int):
    """Start automated trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    
    current_phase = plan.get("warmup_phase")
    if current_phase == "idle":
        current_phase = "phase_1_consumer"

    updated = db.update_channel_trust_plan(
        plan_id,
        status="active",
        warmup_phase=current_phase,
        error_message="",
    )
    return {"success": True, "plan": updated}


@router.post("/plans/{plan_id}/pause")
def pause_plan(plan_id: int):
    """Pause automated trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    updated = db.update_channel_trust_plan(plan_id, status="paused")
    return {"success": True, "plan": updated}


@router.post("/plans/{plan_id}/resume")
def resume_plan(plan_id: int):
    """Resume paused trust building for a plan."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    updated = db.update_channel_trust_plan(plan_id, status="active", error_message="")
    return {"success": True, "plan": updated}


@router.post("/plans/{plan_id}/run-session")
async def trigger_run_session(plan_id: int, background_tasks: BackgroundTasks):
    """Manually trigger one immediate warmup session."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    
    # Run in background to prevent blocking request
    background_tasks.add_task(run_warmup_session, plan_id)
    return {
        "success": True,
        "message": "Đã bắt đầu phiên nuôi kênh trong nền. Vui lòng theo dõi Activity Log.",
        "plan_id": plan_id,
    }


@router.get("/plans/{plan_id}/activities")
def list_activities(
    plan_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List paginated activity logs for a trust plan."""
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


@router.get("/plans/{plan_id}/branding-audit")
async def audit_branding(plan_id: int):
    """Audit channel profile branding elements via GPM browser session."""
    res = await audit_channel_branding_for_plan(plan_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "Lỗi khi audit kênh."))
    return res


@router.post("/plans/{plan_id}/verify-features")
def verify_features(plan_id: int):
    """Simulate feature verification update."""
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Không tìm thấy Trust Plan.")
    
    checklist = plan.get("branding_checklist") or {}
    checklist["feature_level"] = "intermediate"
    
    stats = db.get_trust_activity_stats(plan_id)
    plan["branding_checklist"] = checklist
    new_score = calculate_trust_score(plan, stats)
    
    updated = db.update_channel_trust_plan(
        plan_id,
        branding_checklist=checklist,
        trust_score_estimated=new_score,
    )
    return {"success": True, "plan": updated, "trust_score": new_score}
