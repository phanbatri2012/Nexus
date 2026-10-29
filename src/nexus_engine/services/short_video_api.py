"""FastAPI routes for the generic Short Video project registry and workflow."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nexus_engine.services import database as db
from nexus_engine.services import security_logging, short_video_pipeline, short_video_worker
from nexus_engine.services.short_video_registry import registry


router = APIRouter(prefix="/api/short-video", tags=["short-video"])


class CreateShortVideoRequest(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    poem: str = Field(min_length=1, max_length=50000)
    client_request_id: str = Field(min_length=8, max_length=200)
    visual_provider: Literal["muse", "google_flow"] | None = None


def _public_item(item: dict, *, detail: bool = False) -> dict:
    public = {
        "id": item["id"],
        "project_key": item["project_key"],
        "profile_version": item["profile_version"],
        "title": item["title"],
        "status": item["status"],
        "current_stage": item.get("current_stage") or "",
        "job_id": item.get("job_id") or "",
        "error": security_logging.redact_sensitive(str(item.get("error") or "")),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "reviewed_at": item.get("reviewed_at") or "",
        "completed_at": item.get("completed_at") or "",
        "artifact_urls": {
            "audio": f"/api/short-video/items/{item['id']}/artifacts/audio",
            "draft": f"/api/short-video/items/{item['id']}/artifacts/draft",
            "final": f"/api/short-video/items/{item['id']}/artifacts/final",
        },
    }
    if detail:
        public["source_text"] = item.get("source_text") or ""
        public["profile"] = item.get("manifest") or {}
        public["scenes"] = short_video_pipeline.public_scene_manifest(item)
        job = db.get_system_job(str(item.get("job_id") or ""))
        public["job"] = (
            {
                "id": job["id"],
                "status": job["status"],
                "progress": security_logging.redact_sensitive(
                    str(job.get("progress") or "")
                ),
                "error": security_logging.redact_sensitive(
                    str(job.get("error") or "")
                ),
                "created_at": job.get("created_at") or "",
                "updated_at": job.get("updated_at") or "",
            }
            if job
            else None
        )
    return public


def _get_item_or_404(item_id: str) -> dict:
    try:
        normalized = str(uuid.UUID(str(item_id)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy Short Video.") from exc
    item = db.get_short_video_item(normalized)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy Short Video.")
    return item


def _validate_create_input(snapshot: dict, title: str, poem: str) -> tuple[str, str]:
    normalized_title = str(title or "").strip()
    normalized_poem = str(poem or "").strip()
    schema = snapshot.get("input_schema") or {}
    if not normalized_title or not normalized_poem:
        raise HTTPException(
            status_code=422,
            detail="Tên bài thơ và nội dung bài thơ đều bắt buộc.",
        )
    if len(normalized_title) > int(schema.get("title_max_length") or 300):
        raise HTTPException(status_code=422, detail="Tên bài thơ quá dài.")
    if len(normalized_poem) > int(schema.get("poem_max_length") or 12000):
        raise HTTPException(status_code=422, detail="Nội dung bài thơ quá dài.")
    return normalized_title, normalized_poem


@router.get("/projects")
def list_short_video_projects(
    include_archived: bool = Query(default=False),
):
    return {
        "items": registry.list_projects(include_archived=include_archived),
        "reload_mode": "mtime",
    }


@router.get("/summary")
def get_short_video_summary():
    summary = db.get_short_video_summary()
    for project in registry.list_projects(include_archived=True):
        summary["projects"].setdefault(
            project["key"],
            {
                "running": 0,
                "awaiting_review": 0,
                "completed": 0,
                "errors": 0,
                "total": 0,
            },
        )
    summary["recent_errors"] = [
        _public_item(item) for item in summary.get("recent_errors") or []
    ]
    return summary


@router.get("/items")
def list_short_video_items(
    project: str = Query(default="", max_length=80),
    status: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    page = db.list_short_video_items(
        project_key=project.strip().casefold(),
        status=status.strip().casefold(),
        limit=limit,
        offset=offset,
    )
    page["items"] = [_public_item(item) for item in page["items"]]
    return page


@router.post("/projects/{key}/items", status_code=202)
def create_short_video_item(key: str, request: CreateShortVideoRequest):
    normalized_key = str(key or "").strip().casefold()
    try:
        snapshot = registry.snapshot_for_create(
            normalized_key,
            visual_provider=str(request.visual_provider or ""),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Dự án không khả dụng.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    title, poem = _validate_create_input(snapshot, request.title, request.poem)
    client_request_id = request.client_request_id.strip()
    item_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nexus-short-video:{normalized_key}:{client_request_id}",
    )
    item_id = str(item_uuid)
    job_id = f"short-video-{item_uuid}"
    voice_snapshot = snapshot["resolved_voice_snapshot"]
    payload = {
        "short_video_id": item_id,
        "project_key": normalized_key,
        "profile_snapshot": snapshot,
        "action": "generate",
        "checkpoint": {"version": 1, "stage": "queued"},
    }
    item, job, created = db.create_short_video_item_with_job(
        item_id=item_id,
        project_key=normalized_key,
        profile_version=str(snapshot["profile_version"]),
        title=title,
        source_text=poem,
        manifest=snapshot,
        job_id=job_id,
        payload=payload,
        voice_snapshot=voice_snapshot,
    )
    if created:
        short_video_worker.kick()
    return {
        "created": created,
        "item": _public_item(item, detail=True),
        "job": {
            "id": job.get("id") or job_id,
            "status": job.get("status") or "queued",
        },
    }


@router.get("/items/{item_id}")
def get_short_video_item(item_id: str):
    return _public_item(_get_item_or_404(item_id), detail=True)


@router.get("/items/{item_id}/artifacts/{kind}")
def get_short_video_artifact(item_id: str, kind: str):
    item = _get_item_or_404(item_id)
    try:
        path, mime_type = short_video_pipeline.artifact_path(item, kind)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=mime_type,
        filename=path.name,
        headers={"Cache-Control": "private, no-store"},
    )


def _create_follow_up_job(
    item: dict,
    *,
    action: str,
    stage: str,
    scene_index: int | None = None,
) -> dict:
    snapshot = item.get("manifest") or {}
    voice_snapshot = snapshot.get("resolved_voice_snapshot") or {}
    job_id = f"short-video-{uuid.uuid4()}"
    payload = {
        "short_video_id": item["id"],
        "project_key": item["project_key"],
        "profile_snapshot": snapshot,
        "action": action,
        "checkpoint": {"version": 1, "stage": stage},
    }
    if scene_index is not None:
        payload["scene_index"] = scene_index
    try:
        updated_item, job = db.create_short_video_job(
            item_id=item["id"],
            job_id=job_id,
            title=f"{action}: {item['title']}",
            payload=payload,
            voice_snapshot=voice_snapshot,
            stage=stage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    short_video_worker.kick()
    return {
        "success": True,
        "item": _public_item(updated_item, detail=True),
        "job": {"id": job["id"], "status": job["status"]},
    }


@router.post("/items/{item_id}/approve", status_code=202)
def approve_short_video(item_id: str):
    item = _get_item_or_404(item_id)
    if item.get("status") != "awaiting_review":
        raise HTTPException(status_code=409, detail="Short Video chưa ở bước chờ duyệt.")
    result = _create_follow_up_job(
        item,
        action="final_render",
        stage="final_render",
    )
    reviewed_item = db.update_short_video_item(item["id"], reviewed_at=db.utc_now())
    if reviewed_item:
        result["item"] = _public_item(reviewed_item, detail=True)
    return result


@router.post("/items/{item_id}/regenerate-audio", status_code=202)
def regenerate_short_video_audio(item_id: str):
    item = _get_item_or_404(item_id)
    if item.get("status") not in {
        "awaiting_review",
        "needs_attention",
        "error",
        "completed",
    }:
        raise HTTPException(status_code=409, detail="Không thể tạo lại audio lúc này.")
    return _create_follow_up_job(
        item,
        action="regenerate_audio",
        stage="regenerate_audio",
    )


@router.post("/items/{item_id}/scenes/{index}/regenerate", status_code=202)
def regenerate_short_video_scene(item_id: str, index: int):
    item = _get_item_or_404(item_id)
    if item.get("status") not in {"awaiting_review", "error", "completed"}:
        raise HTTPException(status_code=409, detail="Không thể tạo lại scene lúc này.")
    scenes = short_video_pipeline.public_scene_manifest(item)
    if index < 0 or index not in {int(scene["index"]) for scene in scenes}:
        raise HTTPException(status_code=404, detail="Scene không tồn tại.")
    return _create_follow_up_job(
        item,
        action="regenerate_scene",
        stage=f"regenerate_scene_{index}",
        scene_index=index,
    )
