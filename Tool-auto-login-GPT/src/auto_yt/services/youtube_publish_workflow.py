"""Durable, checkpointed YouTube publishing workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from auto_yt.paths import AUDIO_DIR
from auto_yt.services import database as db
from auto_yt.services import (
    browser_youtube_uploader,
    channel_scanner_service,
    chatgpt_projects,
    publication_scheduler,
    secret_store,
    youtube_comments,
    youtube_publisher,
)
from auto_yt.services.proxy_utils import parse_proxy_url

logger = logging.getLogger(__name__)

CAPTION_NAME = "Tiếng Việt"
ACTIVE_WORKFLOW_STATUSES = {
    "reserved",
    "running",
    "paused",
    "retry_wait",
    "reconcile_required",
    "waiting_for_browser",
}


class PublishConfigurationRequired(RuntimeError):
    def __init__(self, missing_configuration: list[str], message: str):
        super().__init__(message)
        self.missing_configuration = list(dict.fromkeys(missing_configuration))


def validate_browser_upload_readiness(
    channel: dict | None,
    *,
    schedule_enabled: bool,
) -> list[str]:
    """Validate prerequisites for browser-based YouTube upload via GPM Profile."""
    if channel is None:
        return ["default_youtube_channel_id"]
    missing = []
    profile_id = str(channel.get("gpm_profile_id") or "").strip()
    if not profile_id:
        missing.append("gpm_profile_id")
    elif any(
        int(other.get("id") or 0) != int(channel.get("id") or 0)
        and str(other.get("gpm_profile_id") or "").strip() == profile_id
        for other in db.list_youtube_channels()
    ):
        missing.append("gpm_profile_exclusive")
    if not parse_proxy_url(str(channel.get("gpm_proxy_info") or "").strip()):
        missing.append("gpm_proxy_info")
    if schedule_enabled:
        if bool(channel.get("publication_paused")):
            missing.append("publication_paused")
        try:
            publication_scheduler.validate_timezone(
                str(channel.get("publication_timezone") or "")
            )
        except ValueError:
            missing.append("publication_timezone")
        try:
            slots = publication_scheduler.validate_publication_slots(
                channel.get("publication_slots")
            )
        except ValueError:
            slots = []
        if not slots:
            missing.append("publication_slots")
        if int(channel.get("publication_daily_limit") or 0) < 1:
            missing.append("publication_daily_limit")
        if int(channel.get("publication_lead_minutes") or 0) < 1:
            missing.append("publication_lead_minutes")
    return list(dict.fromkeys(missing))


def validate_api_upload_readiness(
    channel: dict | None,
    *,
    schedule_enabled: bool,
) -> list[str]:
    """Validate prerequisites for official YouTube Data API v3 upload."""
    if channel is None:
        return ["default_youtube_channel_id"]
    missing = []
    if channel.get("status") != "connected" or not (
        channel.get("access_token_encrypted")
        or channel.get("refresh_token_encrypted")
        or channel.get("has_refresh_token")
    ):
        missing.append("youtube_oauth")
    if not parse_proxy_url(str(channel.get("gpm_proxy_info") or "").strip()):
        missing.append("gpm_proxy_info")
    if schedule_enabled:
        if bool(channel.get("publication_paused")):
            missing.append("publication_paused")
        try:
            publication_scheduler.validate_timezone(
                str(channel.get("publication_timezone") or "")
            )
        except ValueError:
            missing.append("publication_timezone")
        try:
            slots = publication_scheduler.validate_publication_slots(
                channel.get("publication_slots")
            )
        except ValueError:
            slots = []
        if not slots:
            missing.append("publication_slots")
        if int(channel.get("publication_daily_limit") or 0) < 1:
            missing.append("publication_daily_limit")
        if int(channel.get("publication_lead_minutes") or 0) < 1:
            missing.append("publication_lead_minutes")
        if not bool(channel.get("public_upload_verified")):
            missing.append("public_upload_verified")
    return list(dict.fromkeys(missing))


def _channel_missing_configuration(
    channel: dict | None,
    *,
    upload_method: str = "browser",
    schedule_enabled: bool,
) -> list[str]:
    if upload_method == "api":
        return validate_api_upload_readiness(
            channel,
            schedule_enabled=schedule_enabled,
        )
    return validate_browser_upload_readiness(
        channel,
        schedule_enabled=schedule_enabled,
    )


def resolve_publish_mode(
    pipeline: dict,
    publishing_settings: dict,
    payload_mode: str = "",
) -> tuple[str, bool]:
    clean_payload_mode = str(payload_mode or "").strip().lower()
    clean_pub_mode = str((publishing_settings or {}).get("publish_mode") or "").strip().lower()
    if clean_payload_mode == "public" or clean_pub_mode == "public":
        return "public", False
    if clean_payload_mode == "private":
        return "private", False
    if clean_pub_mode == "private" and not clean_payload_mode:
        return "private", False
    if bool(pipeline.get("youtube_schedule")):
        return "schedule", True
    return "private", False


def evaluate_prompt_publish_readiness(version: dict) -> dict:
    pipeline = version.get("pipeline") if isinstance(version.get("pipeline"), dict) else {}
    publishing_settings = (
        version.get("publishing_settings")
        if isinstance(version.get("publishing_settings"), dict)
        else {}
    )
    publish_mode, schedule_enabled = resolve_publish_mode(pipeline, publishing_settings)
    if not pipeline.get("youtube_upload"):
        return {"ready": True, "missing_configuration": [], "upload_method": "browser", "publish_mode": publish_mode}
    upload_method = str(publishing_settings.get("upload_method") or "browser").strip().lower()
    stable_channel_id = str(version.get("default_youtube_channel_id") or "").strip()
    channel = (
        db.get_youtube_channel_by_channel_id(stable_channel_id, include_tokens=True)
        if stable_channel_id
        else None
    )
    missing = _channel_missing_configuration(
        channel,
        upload_method=upload_method,
        schedule_enabled=schedule_enabled,
    )
    if not isinstance(publishing_settings.get("made_for_kids"), bool):
        missing.append("made_for_kids")
    missing = list(dict.fromkeys(missing))
    return {
        "ready": not missing,
        "missing_configuration": missing,
        "upload_method": upload_method,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _thumbnail_section(script: str, variant: str) -> str:
    label = "THUMBNAIL CÓ CHỮ" if variant == "with_text" else "THUMBNAIL KHÔNG CHỮ"
    match = re.search(
        rf"###\s*\[{re.escape(label)}\]\s*(.*?)(?=\n###\s*\[|\Z)",
        script or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _resolve_thumbnail_path(script: str, variant: str, thumbnails_dir: Path) -> Path | None:
    section = _thumbnail_section(script, variant)
    candidates = [
        value.strip()
        for value in re.findall(r"\[IMAGE_URL:([^\]]+)\]", section)
    ]
    candidates.extend(
        match.group(0)
        for match in re.finditer(
            r"(?:https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)?"
            r"/api/thumbnails/[^\s\]\)\"']+",
            section,
            flags=re.IGNORECASE,
        )
    )
    resolved_root = thumbnails_dir.resolve()
    for candidate in candidates:
        parsed = urlsplit(candidate)
        path_text = unquote(parsed.path or candidate)
        marker = "/api/thumbnails/"
        if marker not in path_text:
            continue
        filename = path_text.split(marker, 1)[1]
        if not filename or Path(filename).name != filename:
            continue
        path = (resolved_root / filename).resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError:
            continue
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _artifact_snapshot(artifact: dict | None) -> dict:
    if not isinstance(artifact, dict) or not artifact.get("id"):
        return {}
    path = Path(str(artifact.get("path") or ""))
    return {
        "id": int(artifact["id"]),
        "path": str(artifact.get("path") or ""),
        "content_hash": str(artifact.get("content_hash") or ""),
        "size_bytes": int(artifact.get("size_bytes") or 0),
        "sha256": _sha256_file(path) if path.is_file() else "",
    }


def _validate_artifact(
    artifact: dict | None,
    *,
    video_id: int,
    artifact_type: str,
    missing_key: str,
    allowed_suffixes: set[str],
) -> tuple[dict, Path]:
    path = Path(str((artifact or {}).get("path") or ""))
    if (
        not artifact
        or int(artifact.get("video_id") or 0) != int(video_id)
        or artifact.get("artifact_type") != artifact_type
        or artifact.get("status") != "ready"
        or not str(artifact.get("content_hash") or "")
        or not path.is_file()
        or path.stat().st_size <= 0
        or path.suffix.casefold() not in allowed_suffixes
    ):
        raise PublishConfigurationRequired(
            [missing_key],
            f"Artifact {artifact_type} chưa sẵn sàng hoặc không còn đúng bản đã lưu.",
        )
    return artifact, path


def _persist_refreshed_token(
    channel_id: int,
    encrypted_access_token: str,
    token_expiry: str,
    oauth_client_id: str,
) -> None:
    db.update_youtube_channel(
        channel_id,
        access_token_encrypted=encrypted_access_token,
        token_expiry=token_expiry,
        oauth_client_id=oauth_client_id,
    )


def _resolve_channel(
    *,
    workflow: dict | None,
    snapshot: dict,
    prompt_version: str,
    resolve_default_channel_id,
) -> dict:
    if workflow:
        channel = db.get_youtube_channel(
            int(workflow["youtube_channel_id"]), include_tokens=True
        )
        if channel:
            return channel
        raise PublishConfigurationRequired(
            ["default_youtube_channel_id"],
            "Kênh YouTube của workflow không còn tồn tại.",
        )

    stable_channel_id = str(snapshot.get("default_youtube_channel_id") or "").strip()
    current_channel_id = str(resolve_default_channel_id(prompt_version) or "").strip()
    channel_id = stable_channel_id or current_channel_id
    if not channel_id:
        raise PublishConfigurationRequired(
            ["default_youtube_channel_id"],
            "Bộ prompt chưa chọn kênh YouTube mặc định.",
        )
    channel = db.get_youtube_channel_by_channel_id(channel_id, include_tokens=True)
    if not channel:
        raise PublishConfigurationRequired(
            ["default_youtube_channel_id"],
            "Kênh YouTube mặc định không tồn tại hoặc đã bị ngắt kết nối.",
        )
    return channel


def _validate_dynamic_channel(
    channel: dict,
    *,
    upload_method: str = "browser",
    schedule_enabled: bool,
) -> str:
    missing = _channel_missing_configuration(
        channel,
        upload_method=upload_method,
        schedule_enabled=schedule_enabled,
    )
    raw_proxy = str(channel.get("gpm_proxy_info") or "").strip()
    if missing:
        raise PublishConfigurationRequired(
            missing,
            "Cấu hình kênh YouTube chưa đủ để tiếp tục tự động đăng.",
        )
    return raw_proxy


def _build_preflight_context(
    job: dict,
    *,
    resolve_default_channel_id,
    resolve_publishing_settings=None,
    thumbnails_dir: Path,
) -> dict:
    payload = job.get("payload") or {}
    workflow = None
    workflow_id = str(payload.get("workflow_id") or "").strip()
    if workflow_id:
        workflow = db.get_youtube_publish_workflow(workflow_id)
    if workflow is None:
        workflow = db.get_youtube_publish_workflow_by_job(job["id"])

    snapshot = dict((workflow or {}).get("snapshot") or payload.get("snapshot") or {})
    prompt_version = str(
        snapshot.get("prompt_version")
        or job.get("prompt_version")
        or payload.get("prompt_version")
        or ""
    )
    channel = _resolve_channel(
        workflow=workflow,
        snapshot=snapshot,
        prompt_version=prompt_version,
        resolve_default_channel_id=resolve_default_channel_id,
    )
    pipeline = snapshot.get("pipeline") if isinstance(snapshot.get("pipeline"), dict) else {}
    publishing_settings = snapshot.get("publishing_settings")
    if not isinstance(publishing_settings, dict):
        publishing_settings = {}
    if not isinstance(publishing_settings.get("made_for_kids"), bool):
        if callable(resolve_publishing_settings):
            live_pub_settings = resolve_publishing_settings(prompt_version)
            if isinstance(live_pub_settings, dict):
                merged_pub_settings = dict(live_pub_settings)
                for k, v in publishing_settings.items():
                    if v is not None and v != "":
                        merged_pub_settings[k] = v
                    elif k == "made_for_kids" and isinstance(v, bool):
                        merged_pub_settings[k] = v
                publishing_settings = merged_pub_settings
                snapshot["publishing_settings"] = publishing_settings

    publish_mode, schedule_enabled = resolve_publish_mode(
        pipeline, publishing_settings, payload.get("publish_mode")
    )
    upload_method = str(
        publishing_settings.get("upload_method") or "browser"
    ).strip().lower()
    proxy = _validate_dynamic_channel(
        channel,
        upload_method=upload_method,
        schedule_enabled=schedule_enabled,
    )

    video_id = int(
        (workflow or {}).get("video_id")
        or job.get("video_id")
        or payload.get("video_id")
        or 0
    )
    video = db.get_video(video_id)
    if not video:
        raise PublishConfigurationRequired(["video"], "Không tìm thấy video nội bộ.")

    artifact_id = int(
        (workflow or {}).get("artifact_id") or payload.get("artifact_id") or 0
    )
    final_artifact, video_path = _validate_artifact(
        db.get_video_artifact(artifact_id),
        video_id=video_id,
        artifact_type="final_mp4",
        missing_key="final_mp4",
        allowed_suffixes={".mp4"},
    )
    publishing_settings = snapshot.get("publishing_settings")
    if not isinstance(publishing_settings, dict):
        publishing_settings = {}
    if not isinstance(publishing_settings.get("made_for_kids"), bool):
        raise PublishConfigurationRequired(
            ["made_for_kids"],
            "Bộ prompt chưa chọn video có dành cho trẻ em hay không.",
        )
    publishing_settings = chatgpt_projects.normalize_publishing_settings(
        publishing_settings
    )
    snapshot["publishing_settings"] = publishing_settings
    upload_captions_enabled = bool(publishing_settings.get("upload_captions", True))

    raw_caption_art = db.get_latest_video_artifact(video_id, "captions", status="ready")

    # JIT Auto-Recovery: If upload captions is enabled but artifact is missing, auto-generate from local audio
    if upload_captions_enabled and not raw_caption_art:
        try:
            from auto_yt.services.video_production import create_srt
            audio_task = db.get_audio_task(video_id)
            audio_path = None
            if audio_task and audio_task.get("audio_url"):
                fname = Path(urlsplit(str(audio_task["audio_url"])).path).name
                if fname and (AUDIO_DIR / fname).is_file():
                    audio_path = AUDIO_DIR / fname
            if not audio_path and audio_task and audio_task.get("request_hash"):
                cand = AUDIO_DIR / f"video_{video_id}_{audio_task['request_hash'][:16]}.mp3"
                if cand.is_file():
                    audio_path = cand
            if not audio_path:
                matches = [
                    p for p in AUDIO_DIR.glob(f"video_{video_id}_*.mp3")
                    if "_preview_" not in p.name and p.is_file()
                ]
                if matches:
                    audio_path = max(matches, key=lambda p: p.stat().st_mtime)
            if audio_path and audio_path.exists():
                logger.info(
                    "Publish workflow: Auto-generating missing captions for video %s from %s",
                    video_id,
                    audio_path,
                )
                create_srt(audio_path, video_id, lambda msg, step="captions": None)
                raw_caption_art = db.get_latest_video_artifact(video_id, "captions", status="ready")
        except Exception as auto_caption_err:
            logger.warning(
                "Publish workflow: Auto-generation of captions failed for video %s: %s",
                video_id,
                auto_caption_err,
            )

    if upload_captions_enabled:
        caption_artifact, caption_path = _validate_artifact(
            raw_caption_art,
            video_id=video_id,
            artifact_type="captions",
            missing_key="captions_srt",
            allowed_suffixes={".srt"},
        )
    else:
        caption_artifact = raw_caption_art
        caption_path = Path(str(raw_caption_art.get("path") or "")) if raw_caption_art and raw_caption_art.get("path") else None

    metadata = snapshot.get("youtube_metadata")
    if not isinstance(metadata, dict) or not metadata.get("snippet"):
        try:
            metadata = youtube_publisher.build_upload_metadata(
                video, publishing_settings
            )
        except youtube_publisher.YouTubePublishError as exc:
            raise PublishConfigurationRequired(["metadata"], str(exc)) from exc
        snapshot["youtube_metadata"] = metadata

    image_settings = snapshot.get("image_generation_settings")
    if not isinstance(image_settings, dict):
        image_settings = {}
    thumbnail_variant = str(
        image_settings.get("thumbnail_variant") or "with_text"
    )
    thumbnail_path = _resolve_thumbnail_path(
        str(video.get("generated_script") or ""),
        thumbnail_variant,
        thumbnails_dir,
    )
    if thumbnail_path is None:
        raise PublishConfigurationRequired(
            ["thumbnail"],
            "Không tìm thấy ảnh local hợp lệ trong đúng mục thumbnail đã chọn.",
        )
    if thumbnail_path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise PublishConfigurationRequired(
            ["thumbnail"],
            "Thumbnail upload phải là PNG, JPG hoặc WebP.",
        )

    artifact_snapshot = snapshot.get("artifacts")
    current_video_snapshot = _artifact_snapshot(final_artifact)
    current_caption_snapshot = _artifact_snapshot(caption_artifact)
    current_thumbnail_snapshot = {
        "path": str(thumbnail_path),
        "sha256": _sha256_file(thumbnail_path),
    }
    if isinstance(artifact_snapshot, dict) and artifact_snapshot:
        expected_video = artifact_snapshot.get("final_mp4") or {}
        expected_caption = artifact_snapshot.get("captions") or {}
        expected_thumbnail = artifact_snapshot.get("thumbnail") or {}
        changed = []
        if any(
            expected_video.get(key) != current_video_snapshot.get(key)
            for key in ("id", "path", "content_hash", "size_bytes")
        ) or (
            expected_video.get("sha256")
            and expected_video.get("sha256") != current_video_snapshot["sha256"]
        ):
            changed.append("final_mp4")
        if upload_captions_enabled and expected_caption and current_caption_snapshot:
            if any(
                expected_caption.get(key) != current_caption_snapshot.get(key)
                for key in ("id", "path", "content_hash", "size_bytes")
            ) or (
                expected_caption.get("sha256")
                and expected_caption.get("sha256") != current_caption_snapshot["sha256"]
            ):
                changed.append("captions_srt")
        if (
            str(expected_thumbnail.get("path") or "")
            != current_thumbnail_snapshot["path"]
            or str(expected_thumbnail.get("sha256") or "")
            != current_thumbnail_snapshot["sha256"]
        ):
            changed.append("thumbnail")
        if changed:
            raise PublishConfigurationRequired(
                changed,
                "Artifact publish đã thay đổi so với snapshot; cần khôi phục đúng bản đã reserve.",
            )
    snapshot["artifacts"] = {
        "final_mp4": current_video_snapshot,
        "captions": current_caption_snapshot,
        "thumbnail": current_thumbnail_snapshot,
    }
    snapshot["prompt_version"] = prompt_version
    snapshot["default_youtube_channel_id"] = str(channel.get("channel_id") or "")

    if workflow is None:
        workflow, _ = db.reserve_youtube_publish_workflow(
            video_id=video_id,
            youtube_channel_id=int(channel["id"]),
            artifact_id=int(final_artifact["id"]),
            snapshot=snapshot,
            system_job_id=job["id"],
        )
    else:
        db.update_youtube_publish_workflow(
            workflow["id"],
            system_job_id=job["id"],
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )
        workflow = db.get_youtube_publish_workflow(workflow["id"])

    return {
        "workflow": workflow,
        "snapshot": snapshot,
        "video": video,
        "final_artifact": final_artifact,
        "caption_artifact": caption_artifact,
        "video_path": video_path,
        "caption_path": caption_path,
        "thumbnail_path": thumbnail_path,
        "publishing_settings": publishing_settings,
        "metadata": metadata,
        "channel": channel,
        "proxy": proxy,
        "schedule_enabled": schedule_enabled,
        "publish_mode": publish_mode,
        "upload_method": upload_method,
    }


def execute_browser_publish_workflow(
    context: dict,
    *,
    progress,
    cancel_check,
) -> dict:
    """Execute end-to-end browser-based YouTube upload & schedule via GPM Profile."""
    workflow = context["workflow"]
    workflow_id = str(workflow["id"])
    video_id = int(workflow["video_id"])

    gpm_profile_id = str(context["channel"].get("gpm_profile_id") or "").strip()
    if not gpm_profile_id:
        raise PublishConfigurationRequired(
            ["gpm_profile_id"],
            "Kênh chưa được gán GPM Profile để upload qua trình duyệt.",
        )
    progress("Đang kiểm tra khả năng tự động hóa của profile...", "browser_preflight", 0)
    channel_scanner_service.ensure_profile_automation_ready(gpm_profile_id)
    browser_state = dict(
        (workflow.get("snapshot") or context["snapshot"]).get(
            "browser_upload_state"
        )
        or {}
    )
    if (
        bool(browser_state.get("remote_identity_unknown"))
        and not str(workflow.get("youtube_video_id") or "").strip()
        and str(workflow.get("stage") or "") == "needs_review"
    ):
        raise browser_youtube_uploader.BrowserUploadNeedsReview(
            "YouTube đã nhận file ở lần chạy trước nhưng chưa lấy được Video ID; "
            "cần đối chiếu draft thủ công trước khi retry để tránh upload trùng."
        )

    target_schedule_at = ""
    if context["schedule_enabled"]:
        target_schedule_at = db.reserve_youtube_publication_slot(workflow_id)

    def persist_browser_video_id(vid_id: str) -> None:
        if not vid_id:
            return
        current_workflow = db.get_youtube_publish_workflow(workflow_id) or workflow
        current_stage = str(current_workflow.get("stage") or "")
        checkpoint_stage = (
            current_stage
            if str(current_workflow.get("youtube_video_id") or "") == vid_id
            and current_stage not in {"", "upload", "uploaded", "draft_created"}
            else "draft_created"
        )
        publication = db.save_video_publication(
            video_id=video_id,
            youtube_channel_id=int(workflow["youtube_channel_id"]),
            youtube_video_id=vid_id,
            published_url=f"https://www.youtube.com/watch?v={vid_id}",
            published_title=str(
                context["metadata"].get("snippet", {}).get("title") or ""
            ),
            published_at="",
            privacy_status="private",
            processing_status="processing",
            scheduled_at=target_schedule_at if context["schedule_enabled"] else "",
            artifact_hash=str(
                context["snapshot"].get("artifacts", {})
                .get("final_mp4", {})
                .get("sha256", "")
            ),
        )
        db.update_youtube_publish_workflow(
            workflow_id,
            youtube_video_id=vid_id,
            publication_id=int(publication["id"]),
            stage=checkpoint_stage,
            status="running",
        )

    def persist_browser_checkpoint(stage: str, details: dict) -> None:
        latest = db.get_youtube_publish_workflow(workflow_id) or workflow
        latest_snapshot = dict(latest.get("snapshot") or context["snapshot"])
        browser_state = dict(latest_snapshot.get("browser_upload_state") or {})
        browser_state.update(dict(details or {}))
        browser_state["checkpoint"] = stage
        latest_snapshot["browser_upload_state"] = browser_state
        monetization = dict(details.get("monetization") or {})
        if monetization:
            latest_snapshot["monetization_capability"] = str(
                monetization.get("capability") or ""
            )
            latest_snapshot["monetization_detection_evidence"] = list(
                monetization.get("evidence") or []
            )
        for state_key in ("video_monetization_state", "ad_suitability_state"):
            if state_key in details:
                latest_snapshot[state_key] = details[state_key]
        checkpoint_status = (
            "needs_review"
            if stage == "needs_review"
            else "error"
            if stage == "failed"
            else "running"
        )
        db.update_youtube_publish_workflow(
            workflow_id,
            snapshot_json=latest_snapshot,
            stage=stage,
            status=checkpoint_status,
            error=str(details.get("error") or "") if checkpoint_status != "running" else "",
        )

    title = str(context["metadata"].get("snippet", {}).get("title") or "")
    description = str(context["metadata"].get("snippet", {}).get("description") or "")
    tags = context["metadata"].get("snippet", {}).get("tags", [])
    category_id = str(context["publishing_settings"].get("category_id") or "").strip()
    made_for_kids = bool(context["publishing_settings"].get("made_for_kids", False))
    contains_synthetic_media = bool(
        context["publishing_settings"].get("contains_synthetic_media", True)
    )
    notify_subscribers = bool(
        context["publishing_settings"].get("notify_subscribers", True)
    )

    try:
        browser_result = asyncio.run(
            browser_youtube_uploader.upload_video_via_browser(
                profile_id=gpm_profile_id,
                video_path=context["video_path"],
                thumbnail_path=context["thumbnail_path"],
                title=title,
                description=description,
                tags=tags,
                category_id=category_id,
                made_for_kids=made_for_kids,
                contains_synthetic_media=contains_synthetic_media,
                notify_subscribers=notify_subscribers,
                schedule_at=target_schedule_at if context["schedule_enabled"] else None,
                publish_mode=context.get("publish_mode") or "schedule",
                caption_path=context["caption_path"],
                language=str(context["publishing_settings"].get("language") or "vi"),
                publication_timezone=str(
                    context["channel"].get("publication_timezone")
                    or "Asia/Ho_Chi_Minh"
                ),
                expected_channel_id=str(context["channel"].get("channel_id") or ""),
                existing_video_id=str(workflow.get("youtube_video_id") or ""),
                publishing_settings=context["publishing_settings"],
                progress=progress,
                cancel_check=cancel_check,
                persist_video_id=persist_browser_video_id,
                persist_checkpoint=persist_browser_checkpoint,
            )
        )
    except browser_youtube_uploader.BrowserUploadNeedsReview as exc:
        db.update_video_production_state(
            video_id,
            publish_status="needs_review",
            current_stage="needs_review",
            production_progress="Upload YouTube cần kiểm tra thủ công",
            blocking_reason=str(exc),
        )
        raise
    except browser_youtube_uploader.BrowserUploadError as exc:
        latest = db.get_youtube_publish_workflow(workflow_id) or {}
        if str(latest.get("status") or "") not in {"needs_review", "error"}:
            db.update_youtube_publish_workflow(
                workflow_id,
                status="error",
                error=str(exc),
            )
        db.update_video_production_state(
            video_id,
            publish_status="error",
            current_stage=str(latest.get("stage") or "browser_upload"),
            production_progress="Upload YouTube qua trình duyệt thất bại",
            blocking_reason=str(exc),
        )
        raise

    youtube_video_id = str(browser_result.get("youtube_video_id") or "").strip()
    persist_browser_video_id(youtube_video_id)
    if context["schedule_enabled"] and not bool(browser_result.get("schedule_verified")):
        raise browser_youtube_uploader.BrowserUploadNeedsReview(
            "YouTube Studio chưa xác minh lịch đăng; không cập nhật local thành scheduled."
        )
    latest_workflow = db.get_youtube_publish_workflow(workflow_id) or {}
    latest_browser_state = dict(
        (latest_workflow.get("snapshot") or {}).get("browser_upload_state") or {}
    )
    caption_locator = str(
        browser_result.get("caption_locator")
        or latest_browser_state.get("caption_locator")
        or latest_workflow.get("caption_id")
        or ""
    ).strip()
    if context.get("caption_path") and bool(context["publishing_settings"].get("upload_captions", True)) and not caption_locator:
        raise browser_youtube_uploader.BrowserUploadNeedsReview(
            "Chưa xác minh được phụ đề SRT trên YouTube Studio."
        )
    if caption_locator:
        db.update_youtube_publish_workflow(
            workflow_id,
            caption_id=caption_locator,
            stage="caption_verified",
        )

    workflow = db.get_youtube_publish_workflow(workflow_id)
    publication_id = int(workflow.get("publication_id") or 0)
    if not publication_id:
        publication = db.get_video_publication_by_youtube_id(youtube_video_id)
        publication_id = int((publication or {}).get("id") or 0)

    if context.get("publish_mode") == "public":
        now_iso = db.utc_now()
        if publication_id:
            db.update_video_publication(
                publication_id,
                privacy_status="public",
                processing_status="succeeded",
                scheduled_at="",
                published_at=now_iso,
            )
        db.update_youtube_publish_workflow(
            workflow_id,
            status="published",
            stage="published",
            upload_session_encrypted="",
            error="",
            scheduled_at="",
        )
        final_status = "published"
        final_stage = "published"
        scheduled_at = ""
    elif context["schedule_enabled"]:
        if publication_id:
            try:
                db.complete_youtube_schedule(
                    workflow_id, publication_id, target_schedule_at
                )
            except Exception as s_exc:
                logger.warning(
                    "Không thể cập nhật reservation slot qua complete_youtube_schedule: %s; cập nhật trực tiếp publication...",
                    s_exc,
                )
                try:
                    db.update_video_publication(
                        publication_id,
                        privacy_status="private",
                        processing_status="succeeded",
                        scheduled_at=target_schedule_at,
                    )
                    db.update_youtube_publish_workflow(
                        workflow_id,
                        status="scheduled",
                        stage="scheduled",
                        scheduled_at=target_schedule_at,
                    )
                except Exception as upd_exc:
                    logger.warning("Lỗi cập nhật publication fallback: %s", upd_exc)
        final_status = "scheduled"
        final_stage = "scheduled"
        scheduled_at = target_schedule_at
    else:
        if publication_id:
            db.update_video_publication(
                publication_id,
                privacy_status="private",
                processing_status="processing",
                scheduled_at="",
                published_at="",
            )
        db.update_youtube_publish_workflow(
            workflow_id,
            status="uploaded_private",
            stage="uploaded_private",
            upload_session_encrypted="",
            error="",
        )
        final_status = "uploaded_private"
        final_stage = "uploaded_private"
        scheduled_at = ""

    db.update_video_production_state(
        video_id,
        publish_status=final_status,
        current_stage=final_stage,
        production_progress=(
            "Đã công khai ngay trên YouTube qua Trình duyệt Web"
            if context.get("publish_mode") == "public"
            else (
                "Đã đặt lịch đăng YouTube qua Trình duyệt Web"
                if context["schedule_enabled"]
                else "Đã upload Private qua Trình duyệt Web"
            )
        ),
        blocking_reason="",
    )
    return {
        "workflow_id": workflow_id,
        "youtube_video_id": youtube_video_id,
        "scheduled_at": scheduled_at,
        "stage": final_stage,
        "publish_stage": final_stage,
        "upload_percent": 100,
        "ready": True,
        "missing_configuration": [],
    }


def execute_api_publish_workflow(
    context: dict,
    *,
    token_provider: Callable[[], str],
    progress,
    cancel_check,
) -> dict:
    """Execute end-to-end official YouTube Data API v3 upload & schedule."""
    workflow = context["workflow"]
    workflow_id = str(workflow["id"])
    video_id = int(workflow["video_id"])
    scheduled_at = str(workflow.get("scheduled_at") or "")

    youtube_video_id = str(workflow.get("youtube_video_id") or "")
    if not youtube_video_id:
        session_url = secret_store.decrypt_secret(
            str(workflow.get("upload_session_encrypted") or "")
        )
        resume_session = bool(session_url)
        if not session_url:
            cancel_check()
            session_url = youtube_publisher.start_resumable_upload(
                token=token_provider(),
                video_path=context["video_path"],
                metadata=context["metadata"],
                notify_subscribers=bool(
                    context["publishing_settings"].get("notify_subscribers", True)
                ),
                proxy=context["proxy"],
            )
            db.update_youtube_publish_workflow(
                workflow_id,
                upload_session_encrypted=secret_store.encrypt_secret(session_url),
                stage="session_created",
                status="running",
            )
            progress("Đã tạo phiên upload riêng tư", "session_created", 0)

        total_size = max(1, context["video_path"].stat().st_size)

        def persist_upload_offset(offset: int) -> None:
            percent = min(100, max(0, int((int(offset) / total_size) * 100)))
            db.update_youtube_publish_workflow(
                workflow_id,
                upload_offset=int(offset),
                stage="uploading",
                status="running",
            )
            progress(f"Đang upload YouTube: {percent}%", "uploading", percent)

        def persist_remote_video(payload: dict) -> None:
            remote_id = str(payload.get("id") or "").strip()
            if not remote_id:
                return
            publication = db.save_video_publication(
                video_id=video_id,
                youtube_channel_id=int(workflow["youtube_channel_id"]),
                youtube_video_id=remote_id,
                published_url=f"https://www.youtube.com/watch?v={remote_id}",
                published_title=str(
                    context["metadata"].get("snippet", {}).get("title") or ""
                ),
                published_at="",
                privacy_status="private",
                processing_status=(
                    "succeeded"
                    if str((payload.get("status") or {}).get("uploadStatus") or "")
                    == "processed"
                    else "processing"
                ),
                scheduled_at="",
                artifact_hash=str(
                    context["snapshot"].get("artifacts", {})
                    .get("final_mp4", {})
                    .get("sha256", "")
                ),
            )
            db.update_youtube_publish_workflow(
                workflow_id,
                youtube_video_id=remote_id,
                publication_id=int(publication["id"]),
                upload_offset=total_size,
                stage="uploaded",
                status="running",
            )

        uploaded = youtube_publisher.upload_video_resumable(
            session_url=session_url,
            video_path=context["video_path"],
            token_provider=token_provider,
            start_offset=int(workflow.get("upload_offset") or 0),
            persist_progress=persist_upload_offset,
            persist_remote_video=persist_remote_video,
            cancel_check=cancel_check,
            resume_session=resume_session,
            proxy=context["proxy"],
        )
        youtube_video_id = str(uploaded.get("id") or "").strip()
        if not youtube_video_id:
            raise youtube_publisher.YouTubeUploadReconciliationRequired(
                "Upload đã kết thúc nhưng chưa xác định được YouTube Video ID."
            )
        persist_remote_video(uploaded)
        progress("Đã upload video ở chế độ Private", "uploaded", 100)

    cancel_check()
    workflow = db.get_youtube_publish_workflow(workflow_id)
    completed_stages = {
        "thumbnail_done",
        "caption_done",
        "processing",
        "scheduled",
        "completed",
    }
    if str(workflow.get("stage") or "") not in completed_stages:
        youtube_publisher.upload_thumbnail(
            token_provider(),
            youtube_video_id,
            context["thumbnail_path"],
            proxy=context["proxy"],
        )
        db.update_youtube_publish_workflow(workflow_id, stage="thumbnail_done")
        progress("Đã gắn thumbnail YouTube", "thumbnail_done", 100)

    cancel_check()
    workflow = db.get_youtube_publish_workflow(workflow_id)
    if str(workflow.get("stage") or "") not in {
        "caption_done",
        "processing",
        "scheduled",
        "completed",
    }:
        language = str(context["publishing_settings"].get("language") or "vi")
        caption_id = youtube_publisher.find_caption_track(
            token_provider(),
            youtube_video_id,
            language,
            CAPTION_NAME,
            proxy=context["proxy"],
        )
        if not caption_id:
            caption_result = youtube_publisher.upload_caption(
                token_provider(),
                youtube_video_id,
                context["caption_path"],
                language=language,
                name=CAPTION_NAME,
                proxy=context["proxy"],
            )
            caption_id = str(caption_result.get("id") or "")
        db.update_youtube_publish_workflow(
            workflow_id,
            caption_id=caption_id,
            stage="caption_done",
        )
        progress("Đã gắn phụ đề tiếng Việt", "caption_done", 100)

    workflow = db.get_youtube_publish_workflow(workflow_id)
    publication_id = int(workflow.get("publication_id") or 0)
    if not publication_id:
        publication = db.get_video_publication_by_youtube_id(youtube_video_id)
        publication_id = int((publication or {}).get("id") or 0)

    if context.get("publish_mode") == "public":
        cancel_check()
        preserved_status = dict(context["metadata"].get("status") or {})
        youtube_publisher.publish_video_now(
            token_provider(),
            youtube_video_id,
            preserved_status=preserved_status,
            proxy=context["proxy"],
        )
        now_iso = db.utc_now()
        if publication_id:
            db.update_video_publication(
                publication_id,
                privacy_status="public",
                processing_status="succeeded",
                scheduled_at="",
                published_at=now_iso,
            )
        db.update_youtube_publish_workflow(
            workflow_id,
            status="published",
            stage="published",
            upload_session_encrypted="",
            error="",
            scheduled_at="",
        )
        progress("Đã công khai video trên YouTube", "published", 100)
        final_status = "published"
        final_stage = "published"
        scheduled_at = ""
    elif context["schedule_enabled"]:
        cancel_check()
        db.update_youtube_publish_workflow(
            workflow_id, stage="processing", status="processing"
        )
        if publication_id:
            db.update_video_publication(
                publication_id,
                privacy_status="private",
                processing_status="processing",
            )
        db.update_video_production_state(
            video_id,
            publish_status="processing",
            current_stage="processing",
            production_progress="Đang chờ YouTube xử lý video",
            blocking_reason="",
        )
        progress("Đang chờ YouTube xử lý video", "processing", 100)
        processing_details = youtube_publisher.require_processing_succeeded(
            token_provider(), youtube_video_id, proxy=context["proxy"]
        )
        if publication_id:
            db.update_video_publication(
                publication_id,
                privacy_status="private",
                processing_status="succeeded",
            )
        scheduled_at = db.reserve_youtube_publication_slot(workflow_id)
        progress("Đã giữ khung giờ đăng YouTube", "slot_reserved", 100)
        cancel_check()
        scheduled_at = db.reserve_youtube_publication_slot(workflow_id)
        preserved_status = {
            **dict(processing_details.get("status") or {}),
            **dict(context["metadata"].get("status") or {}),
        }
        youtube_publisher.schedule_video(
            token_provider(),
            youtube_video_id,
            scheduled_at,
            preserved_status=preserved_status,
            proxy=context["proxy"],
        )
        if not publication_id:
            raise youtube_publisher.YouTubeUploadReconciliationRequired(
                "Đã có YouTube Video ID nhưng thiếu publication checkpoint."
            )
        try:
            db.complete_youtube_schedule(
                workflow_id, publication_id, scheduled_at
            )
        except Exception as exc:
            raise youtube_publisher.YouTubeUploadReconciliationRequired(
                "YouTube đã nhận lịch nhưng local checkpoint chưa được xác nhận."
            ) from exc
        progress("Đã đặt lịch đăng YouTube", "scheduled", 100)
        final_status = "scheduled"
        final_stage = "scheduled"
    else:
        if publication_id:
            publication = db.get_video_publication(publication_id) or {}
            db.update_video_publication(
                publication_id,
                privacy_status="private",
                processing_status=str(
                    publication.get("processing_status") or "processing"
                ),
                scheduled_at="",
                published_at="",
            )
        db.update_youtube_publish_workflow(
            workflow_id,
            status="uploaded_private",
            stage="uploaded_private",
            upload_session_encrypted="",
            error="",
        )
        final_status = "uploaded_private"
        final_stage = "uploaded_private"
    db.update_video_production_state(
        video_id,
        publish_status=final_status,
        current_stage=final_stage,
        production_progress=(
            "Đã công khai video trên YouTube"
            if context.get("publish_mode") == "public"
            else (
                "Đã đặt lịch đăng YouTube"
                if context["schedule_enabled"]
                else "Đã upload Private cùng thumbnail và phụ đề"
            )
        ),
        blocking_reason="",
    )
    return {
        "workflow_id": workflow_id,
        "youtube_video_id": youtube_video_id,
        "scheduled_at": scheduled_at,
        "stage": final_stage,
        "publish_stage": final_stage,
        "upload_percent": 100,
        "ready": True,
        "missing_configuration": [],
    }


def execute_publish_job(
    job: dict,
    *,
    progress,
    cancel_check,
    resolve_default_channel_id,
    resolve_publishing_settings=None,
    thumbnails_dir: Path,
) -> dict:
    context = _build_preflight_context(
        job,
        resolve_default_channel_id=resolve_default_channel_id,
        resolve_publishing_settings=resolve_publishing_settings,
        thumbnails_dir=thumbnails_dir,
    )
    workflow = context["workflow"]
    workflow_id = str(workflow["id"])
    video_id = int(workflow["video_id"])
    if workflow.get("status") in {
        "completed",
        "uploaded_private",
        "scheduled",
        "published",
    }:
        return {
            "workflow_id": workflow_id,
            "youtube_video_id": workflow.get("youtube_video_id") or "",
            "scheduled_at": workflow.get("scheduled_at") or "",
            "stage": str(workflow.get("stage") or workflow.get("status") or "completed"),
            "publish_stage": str(
                workflow.get("stage") or workflow.get("status") or "completed"
            ),
            "ready": True,
            "missing_configuration": [],
        }

    db.update_youtube_publish_workflow(
        workflow_id, status="running", error=""
    )
    workflow = db.get_youtube_publish_workflow(workflow_id) or workflow
    context["workflow"] = workflow
    db.update_video_production_state(
        video_id,
        publish_status="running",
        current_stage="preflight",
        production_progress="Đã kiểm tra cấu hình đăng YouTube",
        blocking_reason="",
    )
    progress(
        "Đã kiểm tra cấu hình đăng YouTube",
        str(workflow.get("stage") or "preflight"),
        None,
    )
    cancel_check()

    upload_method = str(context.get("upload_method") or "browser").strip().lower()

    if upload_method == "browser":
        return execute_browser_publish_workflow(
            context,
            progress=progress,
            cancel_check=cancel_check,
        )

    def token_provider() -> str:
        latest_channel = db.get_youtube_channel(
            int(workflow["youtube_channel_id"]), include_tokens=True
        )
        if latest_channel is None:
            raise PublishConfigurationRequired(
                ["default_youtube_channel_id"], "Kênh YouTube không còn tồn tại."
            )
        latest_proxy = _validate_dynamic_channel(
            latest_channel,
            upload_method="api",
            schedule_enabled=context["schedule_enabled"],
        )
        if parse_proxy_url(latest_proxy) != parse_proxy_url(context["proxy"]):
            raise PublishConfigurationRequired(
                ["gpm_proxy_info"],
                "Proxy kênh đã thay đổi; hãy resume để chạy preflight lại.",
            )
        return youtube_comments.access_token_for_channel(
            latest_channel,
            _persist_refreshed_token,
            proxy=context["proxy"],
        )

    return execute_api_publish_workflow(
        context,
        token_provider=token_provider,
        progress=progress,
        cancel_check=cancel_check,
    )
