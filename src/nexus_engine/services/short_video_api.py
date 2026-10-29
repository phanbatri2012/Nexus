"""FastAPI routes for the generic Short Video project registry and workflow."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nexus_engine.services import database as db
from nexus_engine.services import security_logging, short_video_pipeline, short_video_worker
from nexus_engine.services.short_video_registry import registry


router = APIRouter(prefix="/api/short-video", tags=["short-video"])
CHARACTER_ERA_PATTERN = r"^(?:auto|[a-z0-9]+(?:_[a-z0-9]+)*)$"
MAX_CHARACTER_IMAGE_BYTES = 20 * 1024 * 1024


class CreateShortVideoRequest(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    poem: str = Field(min_length=1, max_length=50000)
    client_request_id: str = Field(min_length=8, max_length=200)
    visual_provider: Literal["muse", "google_flow"] | None = None
    voice_id: str | None = Field(default=None, min_length=1, max_length=200)
    character_era: str = Field(
        default="auto",
        min_length=1,
        max_length=100,
        pattern=CHARACTER_ERA_PATTERN,
    )


class CharacterEraRequest(BaseModel):
    era_key: str = Field(
        min_length=1,
        max_length=100,
        pattern=CHARACTER_ERA_PATTERN,
    )


def _public_profile_snapshot(profile: dict) -> dict:
    safe = deepcopy(profile) if isinstance(profile, dict) else {}
    character = safe.get("resolved_character_profile")
    if isinstance(character, dict):
        for era in character.get("eras") or []:
            for asset in era.get("references") or []:
                asset.pop("snapshot_path", None)
                asset.pop("sha256", None)
    return safe


def _public_character_state(item: dict) -> dict | None:
    profile = item.get("manifest") if isinstance(item.get("manifest"), dict) else {}
    character = profile.get("resolved_character_profile")
    if not isinstance(character, dict):
        return None
    options = item.get("options") if isinstance(item.get("options"), dict) else {}
    return {
        "profile_key": character.get("key") or "",
        "profile_version": character.get("profile_version") or 0,
        "requested_era": options.get("character_era") or "auto",
        "resolved_era": options.get("resolved_character_era") or "",
        "source": options.get("character_era_source") or "",
        "confidence": options.get("character_era_confidence"),
        "reason": options.get("character_era_reason") or "",
        "eras": [
            {
                "key": era.get("key") or "",
                "label": era.get("label") or "",
                "year_from": era.get("year_from"),
                "year_to": era.get("year_to"),
            }
            for era in character.get("eras") or []
        ],
    }


def _profile_with_asset_urls(project_key: str, profile: dict) -> dict:
    public = deepcopy(profile)
    for era in public.get("eras") or []:
        for asset in era.get("assets") or []:
            asset["asset_url"] = (
                f"/api/short-video/projects/{project_key}/character-profile/assets/"
                f"{asset.get('id') or ''}"
            )
    return public


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
        "character": _public_character_state(item),
    }
    if detail:
        public["source_text"] = item.get("source_text") or ""
        public["profile"] = _public_profile_snapshot(item.get("manifest") or {})
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


@router.get("/projects/{key}/character-profile")
def get_short_video_character_profile(key: str):
    normalized_key = str(key or "").strip().casefold()
    profile = registry.character_profile_for_project(normalized_key)
    if profile is None:
        raise HTTPException(status_code=404, detail="Project chưa khai báo Character Profile.")
    return _profile_with_asset_urls(normalized_key, profile)


@router.post("/projects/{key}/character-profile/assets", status_code=201)
async def upload_short_video_character_profile_asset(
    key: str,
    era_key: str = Form(min_length=1, max_length=100, pattern=CHARACTER_ERA_PATTERN),
    role: Literal["primary_portrait", "half_body", "full_body", "profile_context"] = Form(),
    pose: str = Form(min_length=1, max_length=300),
    source_label: str = Form(min_length=1, max_length=500),
    source_year: str = Form(min_length=1, max_length=100),
    rights_note: str = Form(min_length=1, max_length=1000),
    image: UploadFile = File(),
):
    normalized_key = str(key or "").strip().casefold()
    try:
        content = await image.read(MAX_CHARACTER_IMAGE_BYTES + 1)
    finally:
        await image.close()
    try:
        profile = registry.add_character_profile_asset(
            normalized_key,
            era_key=era_key,
            original_filename=image.filename or "",
            content=content,
            role=role,
            pose=pose,
            source_label=source_label,
            source_year=source_year,
            rights_note=rights_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_with_asset_urls(normalized_key, profile)


@router.delete("/projects/{key}/character-profile/assets/{asset_id}")
def remove_short_video_character_profile_asset(key: str, asset_id: str):
    normalized_key = str(key or "").strip().casefold()
    try:
        profile = registry.remove_character_profile_asset(normalized_key, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_with_asset_urls(normalized_key, profile)


@router.get("/projects/{key}/character-profile/assets/{asset_id}")
def get_short_video_character_profile_asset(key: str, asset_id: str):
    normalized_key = str(key or "").strip().casefold()
    path = registry.character_profile_asset_path(normalized_key, asset_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Ảnh Character Profile không khả dụng.")
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.casefold(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=mime_type,
        filename=f"{asset_id}{path.suffix.casefold()}",
        headers={"Cache-Control": "private, no-store"},
    )


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
    client_request_id = request.client_request_id.strip()
    item_uuid = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nexus-short-video:{normalized_key}:{client_request_id}",
    )
    item_id = str(item_uuid)
    job_id = f"short-video-{item_uuid}"
    existing = db.get_short_video_item(item_id)
    if existing is not None:
        existing_job = db.get_system_job(str(existing.get("job_id") or job_id)) or {}
        return {
            "created": False,
            "item": _public_item(existing, detail=True),
            "job": {
                "id": existing_job.get("id") or job_id,
                "status": existing_job.get("status") or "queued",
            },
        }
    try:
        snapshot = registry.snapshot_for_create(
            normalized_key,
            visual_provider=str(request.visual_provider or ""),
            voice_id=str(request.voice_id or ""),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Dự án không khả dụng.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    title, poem = _validate_create_input(snapshot, request.title, request.poem)
    if isinstance(snapshot.get("character_profile"), dict):
        try:
            item_dir = short_video_pipeline.prepare_item_directory(normalized_key, item_id)
            snapshot = registry.snapshot_character_profile(snapshot, item_dir)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        era_keys = {
            str(era.get("key") or "")
            for era in (snapshot.get("resolved_character_profile") or {}).get("eras") or []
        }
        if request.character_era != "auto" and request.character_era not in era_keys:
            raise HTTPException(status_code=422, detail="Thời kỳ hình ảnh không thuộc Character Profile.")
    elif request.character_era != "auto":
        raise HTTPException(status_code=422, detail="Project chưa kích hoạt Character Profile.")
    options = {
        "visual_provider": str((snapshot.get("visual_profile") or {}).get("provider") or "muse"),
        "voice_id": str((snapshot.get("resolved_voice_snapshot") or {}).get("voice_id") or ""),
        "character_era": request.character_era,
    }
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
        options=options,
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
    options: dict | None = None,
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
            options=options,
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


@router.post("/items/{item_id}/character-era", status_code=202)
def update_short_video_character_era(item_id: str, request: CharacterEraRequest):
    item = _get_item_or_404(item_id)
    if item.get("status") not in {
        "awaiting_review",
        "needs_attention",
        "error",
        "completed",
    }:
        raise HTTPException(status_code=409, detail="Không thể đổi thời kỳ hình ảnh lúc này.")
    character = (item.get("manifest") or {}).get("resolved_character_profile")
    if not isinstance(character, dict):
        raise HTTPException(status_code=409, detail="Video này không có Character Profile.")
    era_keys = {str(era.get("key") or "") for era in character.get("eras") or []}
    if request.era_key != "auto" and request.era_key not in era_keys:
        raise HTTPException(status_code=422, detail="Thời kỳ hình ảnh không thuộc snapshot của video.")
    options = dict(item.get("options") or {})
    options["character_era"] = request.era_key
    for field in (
        "resolved_character_era",
        "character_era_source",
        "character_era_confidence",
        "character_era_reason",
    ):
        options.pop(field, None)
    return _create_follow_up_job(
        item,
        action="regenerate_visuals",
        stage="regenerate_visuals",
        options=options,
    )
