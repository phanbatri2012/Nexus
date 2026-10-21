"""Durable two-phase media pipeline for Muse AI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
from PIL import Image

import auto_yt.services.database as db
from auto_yt.paths import MUSE_ARTIFACTS_DIR
from auto_yt.services import process_registry, prompt_assets, video_production
from auto_yt.services.muse_provider import (
    MUSE_PROVIDER_NAME,
    MuseAmbiguousSubmissionError,
    MuseError,
    MuseServiceBusy,
    MusePromptBuilder,
    MuseReceipt,
    MuseStudioApiProvider,
    MuseTransientError,
)


logger = logging.getLogger(__name__)
SCENE_SECONDS = 10.0
MAX_VIDEO_SECONDS = 90 * 60
MAX_REQUEST_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (60.0, 300.0)


class MuseRetryScheduled(MuseTransientError):
    def __init__(self, message: str, *, delay_seconds: float, consume_attempt: bool = True):
        super().__init__(message)
        self.delay_seconds = max(1.0, float(delay_seconds))
        self.consume_attempt = consume_attempt


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json_hash(value: object) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def build_fixed_scene_windows(
    captions: list[dict],
    duration_seconds: float,
    *,
    intro_end_seconds: float,
    coverage_mode: str,
    coverage_minutes: int,
) -> list[dict]:
    duration = float(duration_seconds)
    if duration <= 0:
        raise video_production.VideoProductionError("Audio không có thời lượng hợp lệ.")
    if duration > MAX_VIDEO_SECONDS:
        raise video_production.VideoProductionError(
            "Muse chỉ hỗ trợ video tối đa 90 phút."
        )
    mode = str(coverage_mode or "intro_only").strip().lower()
    if mode not in {"intro_only", "first_n_minutes", "full"}:
        mode = "intro_only"
    coverage_limit = max(1, min(90, int(coverage_minutes or 5))) * 60.0
    windows: list[dict] = []
    start = 0.0
    while start < duration:
        end = min(duration, start + SCENE_SECONDS)
        transcript = " ".join(
            str(item.get("text") or "").strip()
            for item in captions
            if float(item.get("end") or 0) > start
            and float(item.get("start") or 0) < end
            and str(item.get("text") or "").strip()
        )
        if mode == "full":
            is_video = True
        elif mode == "first_n_minutes":
            is_video = start < coverage_limit
        else:
            is_video = start < max(0.0, float(intro_end_seconds or 0.0))
        if not windows:
            is_video = True
        windows.append(
            {
                "index": len(windows),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "transcript": re.sub(r"\s+", " ", transcript).strip(),
                "is_video": is_video,
                "media_type": "video" if is_video else "image",
            }
        )
        start = end
    return windows


def snapshot_reference_assets(video_id: int, snapshot: dict) -> list[dict]:
    """Copy configured references to immutable hash-addressed files for this job."""
    settings = snapshot.get("image_generation_settings") or {}
    muse_config = settings.get("muse_settings") or {}
    prompt_version = str(
        snapshot.get("prompt_version") or snapshot.get("version") or "default"
    )
    asset_dir = prompt_assets.get_prompt_asset_dir(prompt_version).resolve()
    target_dir = MUSE_ARTIFACTS_DIR / str(video_id) / "references"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    for set_index, reference_set in enumerate(muse_config.get("reference_sets") or []):
        if not isinstance(reference_set, dict):
            continue
        aliases = [
            str(value).strip()
            for value in reference_set.get("aliases") or []
            if str(value).strip()
        ]
        set_name = str(reference_set.get("name") or f"Reference {set_index + 1}").strip()
        set_id = str(reference_set.get("id") or set_name or f"reference-{set_index + 1}").strip()
        for image_index, filename in enumerate((reference_set.get("images") or [])[:6]):
            safe_name = Path(str(filename or "")).name
            source = (asset_dir / safe_name).resolve()
            try:
                source.relative_to(asset_dir)
            except ValueError as exc:
                raise video_production.VideoProductionError(
                    "Reference Muse nằm ngoài kho prompt assets."
                ) from exc
            if not source.is_file() or source.suffix.lower() not in prompt_assets.SUPPORTED_IMAGE_EXTENSIONS:
                raise video_production.VideoProductionError(
                    f"Không tìm thấy reference Muse: {safe_name}"
                )
            checksum = _sha256_file(source)
            target = target_dir / f"{checksum}{source.suffix.lower()}"
            if not target.is_file() or _sha256_file(target) != checksum:
                temporary = target.with_suffix(target.suffix + ".tmp")
                shutil.copy2(source, temporary)
                temporary.replace(target)
            copied.append(
                {
                    "set_id": set_id,
                    "set_name": set_name,
                    "set_index": set_index,
                    "image_index": image_index,
                    "aliases": aliases,
                    "source_filename": safe_name,
                    "path": str(target),
                    "sha256": checksum,
                    "primary": image_index == 0,
                }
            )
    return copied


def select_scene_references(
    scene: dict,
    reference_snapshot: list[dict],
    *,
    limit: int,
) -> tuple[list[dict], list[dict]]:
    haystack = prompt_assets.strip_accents(
        " ".join(
            str(scene.get(key) or "")
            for key in ("transcript", "subject", "action", "prompt")
        )
    )
    scored: list[tuple[int, int, int, dict]] = []
    for item in reference_snapshot:
        aliases = [str(item.get("set_name") or ""), *(item.get("aliases") or [])]
        alias_score = max(
            (
                len(normalized)
                for alias in aliases
                if (normalized := prompt_assets.strip_accents(alias).strip())
                and normalized in haystack
            ),
            default=0,
        )
        scored.append(
            (
                alias_score,
                1 if item.get("primary") else 0,
                -int(item.get("set_index") or 0) * 100 - int(item.get("image_index") or 0),
                item,
            )
        )
    relevant = [entry for entry in scored if entry[0] > 0]
    relevant.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
    selected = [dict(entry[3]) for entry in relevant[: max(0, int(limit))]]
    selected_hashes = {item["sha256"] for item in selected}
    omitted = []
    relevant_hashes = {entry[3].get("sha256") for entry in relevant}
    for item in reference_snapshot:
        if item.get("sha256") in selected_hashes:
            continue
        omitted.append(
            {
                **dict(item),
                "omission_reason": (
                    "reference_limit" if item.get("sha256") in relevant_hashes else "alias_not_matched"
                ),
            }
        )
    return selected, omitted


def _prepare_inputs(video_id: int, snapshot: dict, progress) -> dict:
    prepared = video_production.prepare_visual_plan_inputs(video_id, snapshot, progress)
    duration = float(prepared["windows"][-1]["end"])
    muse_config = (snapshot.get("image_generation_settings") or {}).get("muse_settings") or {}
    windows = build_fixed_scene_windows(
        video_production.parse_srt_segments(prepared["srt_path"]),
        duration,
        intro_end_seconds=float(prepared.get("intro_end_seconds") or 0),
        coverage_mode=str(muse_config.get("video_coverage_mode") or "intro_only"),
        coverage_minutes=int(muse_config.get("video_coverage_minutes") or 5),
    )
    prepared["windows"] = windows
    prepared["plan_hash"] = _json_hash(
        {
            "version": 1,
            "caption_hash": prepared["caption_hash"],
            "windows": windows,
            "style": (snapshot.get("image_generation_settings") or {}).get("style_prompt") or "",
            "muse_settings": muse_config,
        }
    )
    return prepared


def _bind_reference_hashes(prepared: dict, reference_snapshot: list[dict]) -> dict:
    base_plan_hash = str(prepared["plan_hash"])
    prepared["base_plan_hash"] = base_plan_hash
    prepared["plan_hash"] = _json_hash(
        {
            "base_plan_hash": base_plan_hash,
            "reference_hashes": [
                str(item.get("sha256") or "") for item in reference_snapshot
            ],
        }
    )
    return prepared


def _save_json_artifact(
    video_id: int,
    artifact_type: str,
    target: Path,
    payload: dict,
    *,
    content_hash: str | None = None,
    metadata: dict | None = None,
) -> dict:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    checksum = _sha256_bytes(serialized)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(serialized)
    temporary.replace(target)
    return db.upsert_video_artifact(
        video_id=video_id,
        artifact_type=artifact_type,
        path=str(target),
        content_hash=content_hash or checksum,
        status="ready",
        mime_type="application/json",
        size_bytes=target.stat().st_size,
        metadata={**(metadata or {}), "checksum": checksum},
    )


def _load_json_artifact(video_id: int, artifact_type: str) -> tuple[dict, dict]:
    artifact = db.get_latest_video_artifact(video_id, artifact_type, "ready")
    path = Path(str((artifact or {}).get("path") or ""))
    if not artifact or not path.is_file():
        raise video_production.VideoProductionError(
            f"Artifact Muse chưa sẵn sàng: {artifact_type}."
        )
    try:
        raw = path.read_bytes()
        expected_checksum = str((artifact.get("metadata") or {}).get("checksum") or "")
        if expected_checksum and _sha256_bytes(raw) != expected_checksum:
            raise video_production.VideoProductionError(
                f"Artifact Muse sai checksum: {artifact_type}."
            )
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise video_production.VideoProductionError(
            f"Artifact Muse không đọc được: {artifact_type}."
        ) from exc
    return artifact, payload


def _load_or_build_plan(
    video_id: int,
    snapshot: dict,
    prepared: dict,
    reference_snapshot: list[dict],
    *,
    force_new_project: bool,
) -> dict:
    if not force_new_project:
        try:
            artifact, payload = _load_json_artifact(video_id, "muse_scene_plan")
            if artifact.get("content_hash") == prepared["plan_hash"]:
                return payload
        except video_production.VideoProductionError:
            pass
    settings = snapshot.get("image_generation_settings") or {}
    video = prepared["video"]
    plan = video_production.build_default_visual_scene_plan(
        prepared["windows"],
        video.get("generated_title") or video.get("title") or "",
        style_prompt=str(settings.get("style_prompt") or ""),
        prompt_version=str(snapshot.get("prompt_version") or snapshot.get("version") or ""),
        generated_script=str(video.get("generated_script") or ""),
        scene_0_source="from_intro_transcript",
        scene_0_prompt_template=str(settings.get("scene_0_prompt_template") or ""),
        scene_body_prompt_template=str(settings.get("scene_body_prompt_template") or ""),
    )
    plan = video_production.validate_visual_scene_plan(plan, prepared["windows"])
    plan["reference_snapshot"] = reference_snapshot
    _save_json_artifact(
        video_id,
        "muse_scene_plan",
        MUSE_ARTIFACTS_DIR / str(video_id) / f"plan_{prepared['plan_hash'][:16]}.json",
        plan,
        content_hash=prepared["plan_hash"],
        metadata={
            "base_plan_hash": prepared.get("base_plan_hash") or prepared["plan_hash"],
            "reference_hashes": [
                str(item.get("sha256") or "") for item in reference_snapshot
            ],
            "scene_count": len(plan["scenes"]),
            "reference_count": len(reference_snapshot),
        },
    )
    return plan


def _valid_cached_artifact(artifact: dict | None, request_hash: str) -> bool:
    if not artifact or artifact.get("status") != "ready" or artifact.get("content_hash") != request_hash:
        return False
    path = Path(str(artifact.get("path") or ""))
    expected = str((artifact.get("metadata") or {}).get("checksum") or "")
    return path.is_file() and path.stat().st_size > 0 and bool(expected) and _sha256_file(path) == expected


def _record_request_state(
    video_id: int,
    artifact_type: str,
    request_hash: str,
    *,
    status: str,
    path: str = "",
    mime_type: str = "",
    size_bytes: int = 0,
    duration_seconds: float | None = None,
    width: int | None = None,
    height: int | None = None,
    metadata: dict,
) -> dict:
    return db.upsert_video_artifact(
        video_id=video_id,
        artifact_type=artifact_type,
        path=path,
        content_hash=request_hash,
        status=status,
        mime_type=mime_type,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        codecs={"video": "h264"} if artifact_type.startswith("muse_scene_video:") and status == "ready" else {},
        metadata=metadata,
    )


def _validate_image(path: Path) -> dict:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        raise MuseError("Ảnh Muse bị hỏng hoặc sai định dạng.") from exc
    if width < 640 or height < 360:
        raise MuseError("Ảnh Muse có độ phân giải quá thấp.")
    return {"width": width, "height": height}


def _normalize_video(video_id: int, source: Path, target: Path) -> dict:
    try:
        source_details = video_production.probe_scene_video(source)
    except video_production.VideoProductionError as exc:
        raise MuseError("Video Muse bị hỏng hoặc không đọc được.") from exc
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".mp4.tmp")
    command = [
        ffmpeg_exe,
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(temporary),
    ]
    creationflags = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    process_registry.register_process(f"video:{video_id}", process)
    try:
        _, stderr = process.communicate()
    finally:
        process_registry.unregister_process(f"video:{video_id}", process)
    if process.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise MuseError(
            "Không thể chuẩn hóa video Muse: " + video_production.extract_ffmpeg_error(stderr or "")
        )
    try:
        normalized = video_production.probe_scene_video(temporary)
    except video_production.VideoProductionError as exc:
        temporary.unlink(missing_ok=True)
        raise MuseError("Video Muse sau chuẩn hóa không hợp lệ.") from exc
    temporary.replace(target)
    return {
        "duration_seconds": float(normalized["duration_seconds"]),
        "width": int(normalized.get("width") or 1920),
        "height": int(normalized.get("height") or 1080),
        "source_duration_seconds": float(source_details["duration_seconds"]),
    }


async def _generate_request(
    *,
    adapter,
    video_id: int,
    artifact_type: str,
    request_hash: str,
    prompt: str,
    references: list[dict],
    omitted: list[dict],
    destination: Path,
    expected: str,
    scene: dict,
    cancel_check: Callable[[], None],
) -> dict:
    existing = db.get_latest_video_artifact(video_id, artifact_type)
    if _valid_cached_artifact(existing, request_hash):
        return existing
    metadata = dict((existing or {}).get("metadata") or {})
    if existing and existing.get("content_hash") == request_hash:
        if existing.get("status") == "ready":
            raise MuseError(
                f"Artifact Muse scene {int(scene['index']) + 1} sai checksum hoặc bị thiếu; hãy Recreate."
            )
        if existing.get("status") == "error" and (
            metadata.get("terminal_generation") or metadata.get("invalid_media")
        ):
            raise MuseError(
                f"Generation Muse scene {int(scene['index']) + 1} đã fail; hãy Recreate để gửi request mới."
            )
    if existing and existing.get("content_hash") == request_hash and existing.get("status") == "submitting":
        if metadata.get("thread_url") or metadata.get("turn_id"):
            raise MuseAmbiguousSubmissionError(
                f"Scene {int(scene['index']) + 1} còn receipt Muse browser cũ; hãy Recreate để tạo lại."
            )
        provider = str(metadata.get("provider") or "")
        provider_task_id = str(metadata.get("provider_task_id") or "")
        endpoint = str(metadata.get("endpoint") or "")
        submitted_at = str(metadata.get("submitted_at") or "")
        if (
            provider != MUSE_PROVIDER_NAME
            or not provider_task_id
            or not endpoint
            or not submitted_at
        ):
            raise MuseAmbiguousSubmissionError(
                f"Scene {int(scene['index']) + 1} có submission không receipt; không tự gửi lại."
            )
        reconcile_count = int(metadata.get("reconcile_count") or 0)
        if reconcile_count >= 10:
            raise MuseAmbiguousSubmissionError(
                f"Không thể đối soát submission Muse của scene {int(scene['index']) + 1}."
            )
        reconciled = await adapter.reconcile(
            receipt=MuseReceipt(
                provider=provider,
                provider_task_id=provider_task_id,
                endpoint=endpoint,
                submitted_at=submitted_at,
            ),
            destination=destination,
            expected=expected,
        )
        if reconciled:
            result = reconciled
        else:
            metadata["reconcile_count"] = reconcile_count + 1
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="submitting",
                metadata=metadata,
            )
            raise MuseRetryScheduled(
                "Submission Muse đang xử lý; sẽ đối soát lại trước khi gửi mới.",
                delay_seconds=60,
                consume_attempt=False,
            )
    else:
        previous_attempt = (
            int(metadata.get("attempt") or 0)
            if existing and existing.get("content_hash") == request_hash
            else 0
        )
        attempt = previous_attempt + 1
        if attempt > MAX_REQUEST_ATTEMPTS:
            raise MuseError(
                f"Scene {int(scene['index']) + 1} đã hết {MAX_REQUEST_ATTEMPTS} lần thử Muse."
            )
        base_metadata = {
            "request_hash": request_hash,
            "reference_hashes": [item["sha256"] for item in references],
            "selected_references": references,
            "omitted_references": omitted,
            "attempt": attempt,
            "scene_index": int(scene["index"]),
            "phase": "videos" if expected == "video" else "images",
            "provider": MUSE_PROVIDER_NAME,
        }
        _record_request_state(
            video_id,
            artifact_type,
            request_hash,
            status="submitting",
            metadata=base_metadata,
        )

        def on_submitted(receipt: MuseReceipt) -> None:
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="submitting",
                metadata={
                    **base_metadata,
                    "provider": receipt.provider,
                    "provider_task_id": receipt.provider_task_id,
                    "endpoint": receipt.endpoint,
                    "submitted_at": receipt.submitted_at,
                },
            )

        try:
            create_media = (
                adapter.create_video if expected == "video" else adapter.create_image
            )
            result = await create_media(
                prompt,
                [Path(item["path"]) for item in references],
                destination,
                request_hash=request_hash,
                on_submitted=on_submitted,
                cancel_check=cancel_check,
            )
        except MuseServiceBusy:
            raise
        except MuseAmbiguousSubmissionError as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="submitting",
                metadata={
                    **current_metadata,
                    "error_class": type(exc).__name__,
                    "ambiguous_submission": True,
                },
            )
            raise
        except MuseTransientError as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or {})
            if (
                current.get("status") == "submitting"
                and current_metadata.get("provider") == MUSE_PROVIDER_NAME
                and current_metadata.get("provider_task_id")
                and current_metadata.get("endpoint")
                and current_metadata.get("submitted_at")
            ):
                _record_request_state(
                    video_id,
                    artifact_type,
                    request_hash,
                    status="submitting",
                    metadata={
                        **current_metadata,
                        "last_error_class": type(exc).__name__,
                    },
                )
                raise MuseRetryScheduled(
                    "Submission Muse có receipt; sẽ đối soát trước khi gửi lại.",
                    delay_seconds=60,
                    consume_attempt=False,
                ) from exc
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error",
                metadata={**base_metadata, "error_class": type(exc).__name__},
            )
            if attempt < MAX_REQUEST_ATTEMPTS:
                raise MuseRetryScheduled(
                    str(exc), delay_seconds=RETRY_DELAYS_SECONDS[attempt - 1]
                ) from exc
            raise
        except MuseError as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error",
                metadata={
                    **current_metadata,
                    "error_class": type(exc).__name__,
                    "terminal_generation": True,
                },
            )
            raise
        except Exception as exc:
            if exc.__class__.__name__ == "VideoJobCanceled":
                current = db.get_latest_video_artifact(video_id, artifact_type) or {}
                current_metadata = dict(current.get("metadata") or base_metadata)
                _record_request_state(
                    video_id,
                    artifact_type,
                    request_hash,
                    status="submitting",
                    metadata={
                        **current_metadata,
                        "last_error_class": type(exc).__name__,
                    },
                )
                raise
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error",
                metadata={**current_metadata, "error_class": type(exc).__name__},
            )
            raise

    result_path = Path(str(result["path"]))
    try:
        if expected == "image":
            details = _validate_image(result_path)
            final_path = result_path
            duration = None
        else:
            final_path = destination.with_suffix(".mp4")
            details = _normalize_video(video_id, result_path, final_path)
            duration = float(details["duration_seconds"])
            if result_path != final_path:
                result_path.unlink(missing_ok=True)
    except MuseError as exc:
        current = db.get_latest_video_artifact(video_id, artifact_type) or {}
        current_metadata = dict(current.get("metadata") or {})
        validation_attempt = int(current_metadata.get("attempt") or 1)
        _record_request_state(
            video_id,
            artifact_type,
            request_hash,
            status="error",
            metadata={
                **current_metadata,
                "attempt": validation_attempt,
                "error_class": type(exc).__name__,
                "invalid_media": True,
            },
        )
        raise
    checksum = _sha256_file(final_path)
    final_metadata = {
        **dict((existing or {}).get("metadata") or {}),
        "request_hash": request_hash,
        "reference_hashes": [item["sha256"] for item in references],
        "selected_references": references,
        "omitted_references": omitted,
        "attempt": int((db.get_latest_video_artifact(video_id, artifact_type) or {}).get("metadata", {}).get("attempt") or 1),
        "scene_index": int(scene["index"]),
        "phase": "videos" if expected == "video" else "images",
        "provider": str(result.get("provider") or MUSE_PROVIDER_NAME),
        "provider_task_id": str(result.get("provider_task_id") or ""),
        "endpoint": str(result.get("endpoint") or ""),
        "submitted_at": str(result.get("submitted_at") or ""),
        "checksum": checksum,
        "download_method": str(result.get("download_method") or ""),
    }
    return _record_request_state(
        video_id,
        artifact_type,
        request_hash,
        status="ready",
        path=str(final_path),
        mime_type="video/mp4" if expected == "video" else str(result.get("mime_type") or "image/png"),
        size_bytes=final_path.stat().st_size,
        duration_seconds=duration,
        width=int(details.get("width") or result.get("width") or 0),
        height=int(details.get("height") or result.get("height") or 0),
        metadata=final_metadata,
    )


async def _prepare_media_async(
    *,
    video_id: int,
    snapshot: dict,
    prepared: dict,
    plan: dict,
    progress,
    cancel_check,
    job_id: str,
) -> list[dict]:
    settings = snapshot.get("image_generation_settings") or {}
    references = plan.get("reference_snapshot") or []
    provider = MuseStudioApiProvider(job_id=job_id, video_id=video_id)
    scene_results: list[dict] = []
    async with provider.open_session() as adapter:
        total = len(plan["scenes"])
        for scene in plan["scenes"]:
            cancel_check()
            index = int(scene["index"])
            selected, omitted = select_scene_references(scene, references, limit=6)
            prompt = MusePromptBuilder.image(
                str(scene["prompt"]), has_references=bool(selected)
            )
            request_hash = _json_hash(
                {
                    "kind": "image",
                    "scene": scene,
                    "prompt": prompt,
                    "references": [item["sha256"] for item in selected],
                    "aspect_ratio": "16:9",
                }
            )
            progress(
                f"Muse images: cảnh {index + 1}/{total}",
                "muse_images",
                media_progress={"phase": "images", "completed": index, "total": total},
            )
            image_artifact = await _generate_request(
                adapter=adapter,
                video_id=video_id,
                artifact_type=f"muse_scene_image:{index}",
                request_hash=request_hash,
                prompt=prompt,
                references=selected,
                omitted=omitted,
                destination=MUSE_ARTIFACTS_DIR / str(video_id) / "images" / f"scene_{index:04d}",
                expected="image",
                scene=scene,
                cancel_check=cancel_check,
            )
            scene_results.append({"scene": scene, "image_artifact": image_artifact})

        requested_videos = [item for item in scene_results if item["scene"].get("is_video")]
        for video_number, item in enumerate(requested_videos, 1):
            cancel_check()
            scene = item["scene"]
            index = int(scene["index"])
            selected, omitted = select_scene_references(scene, references, limit=5)
            scene_image = item["image_artifact"]
            first_frame = {
                "set_id": "scene_first_frame",
                "set_name": "Scene first frame",
                "set_index": -1,
                "image_index": 0,
                "aliases": [],
                "source_filename": Path(scene_image["path"]).name,
                "path": scene_image["path"],
                "sha256": (scene_image.get("metadata") or {}).get("checksum"),
                "primary": True,
            }
            video_references = [first_frame, *selected]
            prompt = MusePromptBuilder.video(
                str(scene.get("action") or scene.get("prompt") or ""),
                reference_count=len(video_references),
                duration_seconds=float(scene["duration"]),
            )
            request_hash = _json_hash(
                {
                    "kind": "video",
                    "scene": scene,
                    "prompt": prompt,
                    "references": [reference["sha256"] for reference in video_references],
                    "aspect_ratio": "16:9",
                }
            )
            progress(
                f"Muse videos: cảnh {video_number}/{len(requested_videos)}",
                "muse_videos",
                media_progress={
                    "phase": "videos",
                    "completed": video_number - 1,
                    "total": len(requested_videos),
                },
            )
            item["video_artifact"] = await _generate_request(
                adapter=adapter,
                video_id=video_id,
                artifact_type=f"muse_scene_video:{index}",
                request_hash=request_hash,
                prompt=prompt,
                references=video_references,
                omitted=omitted,
                destination=MUSE_ARTIFACTS_DIR / str(video_id) / "videos" / f"scene_{index:04d}",
                expected="video",
                scene=scene,
                cancel_check=cancel_check,
            )
    return scene_results


def purge_muse_artifacts(video_id: int) -> int:
    removed = 0
    for artifact in db.list_video_artifacts(video_id):
        artifact_type = str(artifact.get("artifact_type") or "")
        if not (
            artifact_type == "muse_scene_plan"
            or artifact_type == "muse_media_manifest"
            or artifact_type.startswith("muse_scene_image:")
            or artifact_type.startswith("muse_scene_video:")
        ):
            continue
        path = Path(str(artifact.get("path") or ""))
        if path.is_file():
            path.unlink(missing_ok=True)
        if artifact.get("id"):
            db.delete_video_artifact(int(artifact["id"]))
            removed += 1
    return removed


def prepare_muse_media(
    video_id: int,
    snapshot: dict,
    progress,
    cancel_check,
    *,
    job_id: str,
    force_new_project: bool = False,
    reference_snapshot: list[dict] | None = None,
) -> dict:
    if force_new_project:
        purge_muse_artifacts(video_id)
    prepared = _prepare_inputs(video_id, snapshot, progress)
    frozen_references = (
        list(reference_snapshot)
        if isinstance(reference_snapshot, list)
        else snapshot_reference_assets(video_id, snapshot)
    )
    _bind_reference_hashes(prepared, frozen_references)
    plan = _load_or_build_plan(
        video_id,
        snapshot,
        prepared,
        frozen_references,
        force_new_project=force_new_project,
    )
    results = asyncio.run(
        _prepare_media_async(
            video_id=video_id,
            snapshot=snapshot,
            prepared=prepared,
            plan=plan,
            progress=progress,
            cancel_check=cancel_check,
            job_id=job_id,
        )
    )
    media = []
    for item in results:
        scene = item["scene"]
        artifact = item.get("video_artifact") or item["image_artifact"]
        if scene.get("is_video") and not item.get("video_artifact"):
            raise MuseError(
                f"Cảnh {int(scene['index']) + 1} yêu cầu video nhưng không có artifact video."
            )
        media.append(
            {
                "scene_index": int(scene["index"]),
                "path": artifact["path"],
                "sha256": (artifact.get("metadata") or {}).get("checksum"),
                "requested_media_type": "video" if scene.get("is_video") else "image",
                "resolved_media_type": "video" if item.get("video_artifact") else "image",
            }
        )
    manifest = {
        "version": 1,
        "video_id": video_id,
        "plan_hash": prepared["plan_hash"],
        "caption_hash": prepared["caption_hash"],
        "visual_bible": plan["visual_bible"],
        "scenes": plan["scenes"],
        "media": media,
    }
    artifact = _save_json_artifact(
        video_id,
        "muse_media_manifest",
        MUSE_ARTIFACTS_DIR / str(video_id) / f"manifest_{prepared['plan_hash'][:16]}.json",
        manifest,
        metadata={
            "plan_hash": prepared["plan_hash"],
            "scene_count": len(media),
            "video_scene_count": sum(entry["resolved_media_type"] == "video" for entry in media),
        },
    )
    progress("Media Muse đã sẵn sàng để dựng MP4", "muse_media_ready")
    return {
        "manifest_artifact": artifact,
        "plan_hash": prepared["plan_hash"],
        "scene_count": len(media),
        "video_scene_count": sum(entry["resolved_media_type"] == "video" for entry in media),
    }


def render_prepared_muse_video(
    video_id: int,
    snapshot: dict,
    progress,
    cancel_check,
) -> dict:
    prepared = _prepare_inputs(video_id, snapshot, progress)
    plan_artifact, plan = _load_json_artifact(video_id, "muse_scene_plan")
    plan_metadata = plan_artifact.get("metadata") or {}
    if plan_metadata.get("base_plan_hash") != prepared["plan_hash"]:
        raise video_production.VideoProductionError(
            "Kế hoạch Muse đã thay đổi; cần tạo lại media trước khi render."
        )
    prepared["base_plan_hash"] = prepared["plan_hash"]
    prepared["plan_hash"] = str(plan_artifact.get("content_hash") or "")
    _, manifest = _load_json_artifact(video_id, "muse_media_manifest")
    if manifest.get("plan_hash") != prepared["plan_hash"]:
        raise video_production.VideoProductionError("Manifest Muse không khớp scene plan.")
    if manifest.get("caption_hash") != prepared["caption_hash"]:
        raise video_production.VideoProductionError("SRT đã thay đổi; cần tạo lại media Muse.")
    scenes = plan.get("scenes") or []
    entries = manifest.get("media") or []
    if len(scenes) != len(entries) or not scenes:
        raise video_production.VideoProductionError("Manifest Muse thiếu hoặc thừa cảnh.")
    paths: list[Path] = []
    hashes: list[str] = []
    for scene, entry in zip(scenes, entries):
        index = int(scene["index"])
        if int(entry.get("scene_index", -1)) != index:
            raise video_production.VideoProductionError("Manifest Muse sai thứ tự cảnh.")
        if scene.get("is_video") and entry.get("resolved_media_type") != "video":
            raise video_production.VideoProductionError(
                f"Cảnh {index + 1} yêu cầu video; không được fallback sang ảnh."
            )
        path = Path(str(entry.get("path") or ""))
        if not path.is_file() or _sha256_file(path) != str(entry.get("sha256") or ""):
            raise video_production.VideoProductionError(
                f"Media Muse cảnh {index + 1} thiếu hoặc sai checksum."
            )
        paths.append(path)
        hashes.append(str(entry["sha256"]))
    input_hash = _json_hash(
        {
            "audio": _sha256_file(prepared["audio_path"]),
            "captions": prepared["caption_hash"],
            "scenes": hashes,
            "plan": scenes,
            "render": {"width": 1920, "height": 1080, "fps": 30},
        }
    )
    artifact = video_production.render_video(
        video_id=video_id,
        audio_path=prepared["audio_path"],
        srt_path=prepared["srt_path"],
        scenes=scenes,
        media_paths=paths,
        input_hash=input_hash,
        progress=progress,
        cancel_check=cancel_check,
        crop_watermark=False,
    )
    return {
        "artifact": artifact,
        "captions_path": str(prepared["srt_path"]),
        "scenes": scenes,
        "visual_bible": plan.get("visual_bible") or {},
    }
