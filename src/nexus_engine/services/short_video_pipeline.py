"""Checkpointed poem-to-Short pipeline using OmniVoice and selectable visual engines."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

from PIL import Image

from nexus_engine.paths import SHORT_VIDEO_DIR
from nexus_engine.services import (
    chatgpt_runtime,
    database as db,
    muse_semantic_planner,
    security_logging,
    short_video_renderer,
    tts_registry as tts,
)
from nexus_engine.services.chatgpt_runtime import ChatGPTAttentionRequiredError
from nexus_engine.services.chatgpt_worker import (
    generate_muse_scene_plan_only,
    resolve_short_video_character_era,
)
from nexus_engine.services.muse_provider import (
    MusePromptBuilder,
    MuseReceipt,
    MuseStudioApiProvider,
)


logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1
POLL_INTERVAL_SECONDS = 2.0
TTS_TIMEOUT_SECONDS = 7200.0
_WHISPER_LOCK = threading.Lock()
_WHISPER_MODEL = None
_WHISPER_DEVICE = ""


class ShortVideoPipelineError(RuntimeError):
    pass


class ShortVideoCanceled(ShortVideoPipelineError):
    pass


class ShortVideoAttentionRequired(ShortVideoPipelineError):
    def __init__(self, message: str, *, reason: str):
        self.reason = reason
        super().__init__(message)


class ShortVideoRetryLater(ShortVideoPipelineError):
    def __init__(self, message: str, *, delay_seconds: float = 30.0):
        self.delay_seconds = max(1.0, float(delay_seconds))
        super().__init__(message)


def _canonical_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_item_dir(project_key: str, item_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", project_key):
        raise ShortVideoPipelineError("Project key Short Video không hợp lệ.")
    if not re.fullmatch(r"[0-9a-f-]{32,36}", item_id.casefold()):
        raise ShortVideoPipelineError("Item ID Short Video không hợp lệ.")
    root = SHORT_VIDEO_DIR.resolve()
    target = (root / project_key / item_id).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ShortVideoPipelineError("Namespace artifact Short Video không hợp lệ.") from exc
    target.mkdir(parents=True, exist_ok=True)
    return target


def prepare_item_directory(project_key: str, item_id: str) -> Path:
    """Return the validated item namespace used for immutable pre-job snapshots."""

    return _safe_item_dir(project_key, item_id)


def _resolve_artifact(item_dir: Path, relative_path: str) -> Path:
    candidate = (item_dir / str(relative_path or "")).resolve()
    try:
        candidate.relative_to(item_dir.resolve())
    except ValueError as exc:
        raise ShortVideoPipelineError("Artifact Short Video nằm ngoài namespace item.") from exc
    return candidate


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_checkpoint(item_dir: Path, *, item_id: str) -> dict:
    path = item_dir / "checkpoint.json"
    if not path.is_file():
        return {
            "version": CHECKPOINT_VERSION,
            "item_id": item_id,
            "stage": "queued",
            "audio_revision": 0,
            "scene_revisions": {},
            "audio": {},
            "timing": {},
            "storyboard": {},
            "scenes": [],
            "artifacts": {},
        }
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShortVideoAttentionRequired(
            "Checkpoint Short Video bị hỏng; cần kiểm tra thủ công.",
            reason="invalid_checkpoint",
        ) from exc
    if checkpoint.get("item_id") != item_id:
        raise ShortVideoAttentionRequired(
            "Checkpoint Short Video không khớp item.",
            reason="invalid_checkpoint",
        )
    return checkpoint


def _save_checkpoint(
    checkpoint: dict,
    *,
    item_dir: Path,
    job: dict,
    item: dict,
    stage: str,
    progress: str,
) -> None:
    checkpoint["version"] = CHECKPOINT_VERSION
    checkpoint["item_id"] = item["id"]
    checkpoint["stage"] = stage
    checkpoint["updated_at"] = db.utc_now()
    _atomic_json(item_dir / "checkpoint.json", checkpoint)
    payload = dict(job.get("payload") or {})
    payload["checkpoint"] = {
        "version": CHECKPOINT_VERSION,
        "stage": stage,
        "updated_at": checkpoint["updated_at"],
        "audio_revision": int(checkpoint.get("audio_revision") or 0),
        "scene_count": len(checkpoint.get("scenes") or []),
    }
    db.update_system_job(
        str(job["id"]),
        progress=progress,
        payload_json=payload,
        resume_from_step=stage,
    )
    db.update_short_video_item(
        str(item["id"]),
        status="processing",
        current_stage=stage,
        error="",
    )
    job["payload"] = payload


def _check_canceled(job_id: str) -> None:
    latest = db.get_system_job(job_id)
    if latest and latest.get("cancel_requested"):
        raise ShortVideoCanceled("Đã dừng Short Video tại checkpoint an toàn.")


def _estimated_duration_seconds(title: str, poem: str, voice_snapshot: dict) -> float:
    text = f"{title} {poem}"
    words = len(re.findall(r"\S+", text))
    settings = voice_snapshot.get("config") if isinstance(voice_snapshot, dict) else {}
    try:
        words_per_minute = float((settings or {}).get("target_words_per_minute") or 145)
    except (TypeError, ValueError):
        words_per_minute = 145.0
    pause_count = max(1, poem.count("\n") + 1)
    try:
        pause_seconds = float((settings or {}).get("pause_ms") or 150) / 1000.0
    except (TypeError, ValueError):
        pause_seconds = 0.15
    return words / max(words_per_minute, 1.0) * 60.0 + pause_count * pause_seconds


def _validate_profile(item: dict) -> tuple[dict, dict, dict]:
    profile = item.get("manifest") if isinstance(item.get("manifest"), dict) else {}
    pipeline = profile.get("pipeline_profile") or {}
    voice_profile = profile.get("voice_profile") or {}
    voice_snapshot = profile.get("resolved_voice_snapshot") or {}
    if pipeline.get("id") != "poem_to_short_v1":
        raise ShortVideoPipelineError("Pipeline profile Short Video không được hỗ trợ.")
    if voice_profile.get("provider") != "omnivoice":
        raise ShortVideoPipelineError("Short Video này bắt buộc dùng OmniVoice.")
    if voice_snapshot.get("provider_id") != "omnivoice":
        raise ShortVideoPipelineError("Voice snapshot không thuộc OmniVoice.")
    if voice_snapshot.get("voice_id") != voice_profile.get("voice_id"):
        raise ShortVideoPipelineError("Voice snapshot không khớp manifest.")
    if voice_snapshot.get("voice_name") != voice_profile.get("voice_name"):
        raise ShortVideoPipelineError("Tên giọng snapshot không khớp manifest.")
    visual = profile.get("visual_profile") or {}
    visual_provider = str(visual.get("provider") or "muse")
    available_providers = visual.get("available_providers") or [visual_provider]
    if visual_provider not in {"muse", "google_flow"}:
        raise ShortVideoPipelineError("Visual provider Short Video không được hỗ trợ.")
    if visual_provider not in available_providers:
        raise ShortVideoPipelineError("Visual provider không thuộc danh sách manifest cho phép.")
    render = profile.get("render_profile") or {}
    forbidden_flags = (
        "subtitles",
        "title_card",
        "credits",
        "music",
        "ambience",
    )
    if any(render.get(flag) is not False for flag in forbidden_flags):
        raise ShortVideoPipelineError("Render profile Short Video có lớp nội dung bị cấm.")
    return profile, pipeline, voice_snapshot


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _character_era(profile: dict, era_key: str) -> dict | None:
    character = profile.get("resolved_character_profile")
    if not isinstance(character, dict):
        return None
    return next(
        (
            era
            for era in character.get("eras") or []
            if str(era.get("key") or "") == str(era_key or "")
        ),
        None,
    )


def _validate_character_references(item_dir: Path, era: dict) -> None:
    references = era.get("references") if isinstance(era, dict) else []
    if not isinstance(references, list) or not 2 <= len(references) <= 3:
        raise ShortVideoAttentionRequired(
            "Thời kỳ hình ảnh chưa có đủ 2–3 ảnh tham chiếu.",
            reason="character_references_unavailable",
        )
    if sum(bool(reference.get("primary")) for reference in references) != 1:
        raise ShortVideoAttentionRequired(
            "Thời kỳ hình ảnh không có đúng một ảnh tham chiếu primary.",
            reason="character_references_unavailable",
        )
    for reference in references:
        relative = str(reference.get("snapshot_path") or "")
        path = _resolve_artifact(item_dir, relative)
        expected_hash = str(reference.get("sha256") or "")
        if not path.is_file() or not expected_hash or _file_sha256(path) != expected_hash:
            raise ShortVideoAttentionRequired(
                "Snapshot ảnh Character Profile không toàn vẹn.",
                reason="character_references_unavailable",
            )


def _ensure_character_era(
    *,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    profile: dict,
) -> dict | None:
    character = profile.get("resolved_character_profile")
    if not isinstance(character, dict):
        return None
    options = item.get("options") if isinstance(item.get("options"), dict) else {}
    requested = str(options.get("character_era") or "auto")
    state = checkpoint.get("character_resolution")
    expected_identity = {
        "profile_key": character.get("key") or "",
        "profile_version": character.get("profile_version") or 0,
        "snapshot_hash": character.get("snapshot_hash") or "",
        "requested_era": requested,
    }
    if isinstance(state, dict) and all(
        state.get(key) == value for key, value in expected_identity.items()
    ):
        era = _character_era(profile, str(state.get("era_key") or ""))
        if era is None:
            raise ShortVideoAttentionRequired(
                "Thời kỳ checkpoint không thuộc Character Profile.",
                reason="character_era_invalid",
            )
        _validate_character_references(item_dir, era)
        if str(state.get("source") or "") == "auto" and float(state.get("confidence") or 0) < 0.70:
            raise ShortVideoAttentionRequired(
                "Không đủ tự tin để tự chọn thời kỳ hình ảnh; hãy chọn thủ công.",
                reason="character_era_low_confidence",
            )
        return state

    era_keys = {
        str(era.get("key") or "") for era in character.get("eras") or []
    }
    if requested != "auto":
        if requested not in era_keys:
            raise ShortVideoAttentionRequired(
                "Thời kỳ được chọn không thuộc Character Profile.",
                reason="character_era_invalid",
            )
        resolved = {
            "era_key": requested,
            "confidence": 1.0,
            "reason": "Người dùng chọn thủ công thời kỳ hình ảnh.",
            "source": "manual",
            "planner_source": "manual_override",
        }
    else:
        if not chatgpt_runtime.try_start_operation("short_video_character_era"):
            raise ShortVideoRetryLater(
                "Profile ChatGPT đang bận; Short Video sẽ thử chọn thời kỳ lại.",
                delay_seconds=20,
            )
        try:
            try:
                result = resolve_short_video_character_era(
                    title=str(item.get("title") or ""),
                    poem=str(item.get("source_text") or ""),
                    eras=character.get("eras") or [],
                    cancel_check=lambda: _check_canceled(str(job["id"])),
                )
            except ChatGPTAttentionRequiredError as exc:
                raise ShortVideoAttentionRequired(
                    str(exc),
                    reason="character_era_attention_required",
                ) from exc
        finally:
            chatgpt_runtime.finish_operation()
        resolved = {**result, "source": "auto"}

    era = _character_era(profile, str(resolved.get("era_key") or ""))
    if era is None:
        raise ShortVideoAttentionRequired(
            "Không xác định được thời kỳ hình ảnh hợp lệ.",
            reason="character_era_invalid",
        )
    _validate_character_references(item_dir, era)
    state = {**expected_identity, **resolved}
    checkpoint["character_resolution"] = state
    updated_options = dict(options)
    updated_options.update(
        {
            "resolved_character_era": state["era_key"],
            "character_era_source": state["source"],
            "character_era_confidence": state["confidence"],
            "character_era_reason": state["reason"],
        }
    )
    item["options"] = updated_options
    db.update_short_video_item(str(item["id"]), options_json=updated_options)
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage="character_era_ready",
        progress=f"Đã chọn thời kỳ hình ảnh: {era.get('label') or state['era_key']}",
    )
    if state["source"] == "auto" and float(state.get("confidence") or 0) < 0.70:
        raise ShortVideoAttentionRequired(
            "Không đủ tự tin để tự chọn thời kỳ hình ảnh; hãy chọn thủ công.",
            reason="character_era_low_confidence",
        )
    return state


def _tts_text(item: dict) -> str:
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    poem = str(item.get("source_text") or "").strip()
    return f"{title}.\n\n{poem}"


def _ensure_audio(
    *,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    voice_snapshot: dict,
    max_duration: float,
) -> float:
    audio_state = checkpoint.setdefault("audio", {})
    normalized_relative = str(audio_state.get("normalized_path") or "")
    if normalized_relative:
        normalized = _resolve_artifact(item_dir, normalized_relative)
        if normalized.is_file():
            duration = short_video_renderer.probe_duration(normalized)
            audio_state["duration_seconds"] = round(duration, 3)
            return duration

    provider = tts.get_provider("omnivoice")
    health = provider.health(test_connection=True)
    if not health.get("ok"):
        raise ShortVideoAttentionRequired(
            "OmniVoice chưa sẵn sàng để tạo giọng đọc.",
            reason="omnivoice_unavailable",
        )
    task_id = str(audio_state.get("provider_task_id") or "")
    text = _tts_text(item)
    if not task_id:
        request_hash = _canonical_hash(
            {
                "item_id": item["id"],
                "audio_revision": int(checkpoint.get("audio_revision") or 0),
                "tts_hash": tts.get_request_hash(text, voice_snapshot["voice_id"], voice_snapshot),
            }
        )
        task = tts.submit_tts_task(
            text,
            voice_snapshot["voice_id"],
            voice_snapshot,
            request_hash=request_hash,
        )
        task_id = str(task.get("id") or "")
        if not task_id:
            raise ShortVideoPipelineError("OmniVoice không trả task ID.")
        audio_state.update(
            {
                "provider": "omnivoice",
                "provider_task_id": task_id,
                "request_hash": request_hash,
                "submitted_at": db.utc_now(),
                "single_task": True,
            }
        )
        _save_checkpoint(
            checkpoint,
            item_dir=item_dir,
            job=job,
            item=item,
            stage="omnivoice_poll",
            progress="OmniVoice đang ngâm toàn bộ bài thơ trong một task",
        )

    started = time.monotonic()
    recovered = bool(audio_state.get("recovered"))
    while True:
        _check_canceled(str(job["id"]))
        task = tts.get_tts_task(task_id, "omnivoice")
        status = str(task.get("status") or "").casefold()
        if status in {"completed", "success", "succeeded", "done"}:
            break
        if status in {"failed", "error", "canceled", "cancelled"}:
            if not recovered:
                task = tts.retry_tts_task(task_id, "omnivoice")
                recovered = True
                audio_state["recovered"] = True
                _save_checkpoint(
                    checkpoint,
                    item_dir=item_dir,
                    job=job,
                    item=item,
                    stage="omnivoice_recover",
                    progress="Đang khôi phục task OmniVoice đã lưu",
                )
                continue
            raise ShortVideoAttentionRequired(
                "Task OmniVoice không thể hoàn thành; không gửi task mới tự động.",
                reason="omnivoice_failed",
            )
        if time.monotonic() - started > TTS_TIMEOUT_SECONDS:
            raise ShortVideoRetryLater(
                "OmniVoice vẫn đang xử lý; sẽ tiếp tục poll task đã lưu.",
                delay_seconds=60,
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    raw_path = item_dir / "audio" / f"narration_r{int(checkpoint.get('audio_revision') or 0)}.source.wav"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    provider.download_audio(task_id, raw_path)
    normalized_path = item_dir / "audio" / f"narration_r{int(checkpoint.get('audio_revision') or 0)}.wav"
    short_video_renderer.normalize_audio(raw_path, normalized_path, job_id=str(job["id"]))
    duration = short_video_renderer.probe_duration(normalized_path)
    audio_state.update(
        {
            "source_path": raw_path.relative_to(item_dir).as_posix(),
            "normalized_path": normalized_path.relative_to(item_dir).as_posix(),
            "duration_seconds": round(duration, 3),
            "completed_at": db.utc_now(),
        }
    )
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage="audio_ready",
        progress=f"Đã tạo giọng đọc {duration:.1f} giây",
    )
    if duration > max_duration + 0.05:
        raise ShortVideoAttentionRequired(
            f"Giọng đọc dài {duration:.1f} giây, vượt giới hạn {max_duration:.0f} giây; hệ thống không cắt hoặc tăng tốc.",
            reason="duration_exceeded",
        )
    return duration


def _transcribe_audio(audio_path: Path) -> list[dict]:
    global _WHISPER_DEVICE, _WHISPER_MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ShortVideoPipelineError("Thiếu faster-whisper để tạo timecode nội bộ.") from exc
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            try:
                _WHISPER_MODEL = WhisperModel("small", device="cuda", compute_type="float16")
                _WHISPER_DEVICE = "cuda"
            except Exception:
                _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
                _WHISPER_DEVICE = "cpu"
        try:
            segments, _info = _WHISPER_MODEL.transcribe(
                str(audio_path),
                language="vi",
                vad_filter=True,
            )
            return [
                {
                    "start": round(float(segment.start), 3),
                    "end": round(float(segment.end), 3),
                    "text": re.sub(r"\s+", " ", str(segment.text or "")).strip(),
                }
                for segment in segments
                if str(segment.text or "").strip()
            ]
        except Exception as exc:
            if _WHISPER_DEVICE != "cuda":
                raise ShortVideoPipelineError("Whisper CPU tạo timecode thất bại.") from exc
            _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
            _WHISPER_DEVICE = "cpu"
            segments, _info = _WHISPER_MODEL.transcribe(
                str(audio_path), language="vi", vad_filter=True
            )
            return [
                {
                    "start": round(float(segment.start), 3),
                    "end": round(float(segment.end), 3),
                    "text": re.sub(r"\s+", " ", str(segment.text or "")).strip(),
                }
                for segment in segments
                if str(segment.text or "").strip()
            ]


def _ensure_timing(
    *, checkpoint: dict, item_dir: Path, job: dict, item: dict
) -> list[dict]:
    timing_state = checkpoint.setdefault("timing", {})
    relative = str(timing_state.get("path") or "")
    if relative:
        path = _resolve_artifact(item_dir, relative)
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("segments") or []
    audio_path = _resolve_artifact(
        item_dir, str((checkpoint.get("audio") or {}).get("normalized_path") or "")
    )
    segments = _transcribe_audio(audio_path)
    target = item_dir / "timing" / "whisper.json"
    _atomic_json(
        target,
        {
            "language": "vi",
            "internal_only": True,
            "render_as_subtitles": False,
            "segments": segments,
        },
    )
    timing_state.update(
        {
            "path": target.relative_to(item_dir).as_posix(),
            "segment_count": len(segments),
            "render_as_subtitles": False,
        }
    )
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage="timing_ready",
        progress="Đã tạo timecode nội bộ; không render phụ đề",
    )
    return segments


def build_scene_windows(
    *,
    poem: str,
    timings: list[dict],
    duration: float,
    pipeline: dict,
    max_video_scenes: int = 3,
) -> list[dict]:
    maximum = max(1.0, float(pipeline.get("scene_max_seconds") or 8))
    max_scenes = max(1, min(24, int(pipeline.get("max_scenes") or 24)))
    count = max(1, min(max_scenes, int(math.ceil(duration / maximum))))
    poem_units = [line.strip() for line in str(poem or "").splitlines() if line.strip()]
    if not poem_units:
        poem_units = [str(poem or "").strip()]
    windows: list[dict] = []
    for index in range(count):
        start = duration * index / count
        end = duration * (index + 1) / count
        overlapping = [
            segment
            for segment in timings
            if float(segment.get("end") or 0) > start
            and float(segment.get("start") or 0) < end
        ]
        line_start = math.floor(index * len(poem_units) / count)
        line_end = max(line_start + 1, math.ceil((index + 1) * len(poem_units) / count))
        source_lines = poem_units[line_start:line_end]
        transcript = " ".join(
            str(segment.get("text") or "").strip() for segment in overlapping
        ).strip()
        if not transcript:
            transcript = " / ".join(source_lines)
        windows.append(
            {
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "transcript": transcript,
                "source_lines": source_lines,
                "is_video": False,
                "media_type": "image",
            }
        )
    hero_limit = max(0, min(3, int(max_video_scenes)))
    hero_count = min(hero_limit, max(0, count - 1))
    hero_indices: list[int] = []
    for ordinal in range(hero_count):
        candidate = min(count - 2, round((ordinal + 1) * (count - 1) / (hero_count + 1)))
        if candidate not in hero_indices:
            hero_indices.append(candidate)
    for index in hero_indices:
        windows[index]["is_video"] = True
        windows[index]["media_type"] = "video"
    return windows


def character_scene_count(scene_count: int, policy: dict | None = None) -> int:
    count = max(0, int(scene_count))
    if count <= 0:
        return 0
    if count <= 4:
        return (1, 1, 2, 3)[count - 1]
    policy = policy or {}
    minimum = float(policy.get("min_scene_ratio") or 0.60)
    target = float(policy.get("target_scene_ratio") or 0.70)
    maximum = float(policy.get("max_scene_ratio") or 0.80)
    lower = math.ceil(count * minimum)
    upper = math.floor(count * maximum)
    return max(lower, min(upper, round(count * target)))


def _character_scene_score(scene: dict, era: dict, position: int, count: int) -> float:
    text = " ".join(
        str(scene.get(field) or "")
        for field in (
            "subject",
            "action",
            "setting",
            "narrative_role",
            "transcript",
        )
    ).casefold()
    score = 0.0
    for keyword in era.get("keywords") or []:
        if str(keyword).strip().casefold() in text:
            score += 5.0
    for cue in ("bác", "hồ chí minh", "người", "poet", "leader", "nhân vật"):
        if cue in text:
            score += 2.0
    center = (count - 1) / 2
    score += max(0.0, 1.0 - abs(position - center) / max(count, 1))
    return score


def _apply_character_profile_to_plan(plan: dict, profile: dict, checkpoint: dict) -> dict:
    character = profile.get("resolved_character_profile")
    resolution = checkpoint.get("character_resolution")
    if not isinstance(character, dict) or not isinstance(resolution, dict):
        return plan
    era = _character_era(profile, str(resolution.get("era_key") or ""))
    if era is None:
        raise ShortVideoPipelineError("Storyboard không có thời kỳ Character Profile hợp lệ.")
    scenes = plan.get("scenes") or []
    policy = character.get("appearance_policy") or {}
    visible_count = character_scene_count(len(scenes), policy)
    ranked = sorted(
        (
            (_character_scene_score(scene, era, position, len(scenes)), int(scene["index"]))
            for position, scene in enumerate(scenes)
        ),
        key=lambda value: (-value[0], value[1]),
    )
    visible_indices = {index for _score, index in ranked[:visible_count]}
    allowed_shots = policy.get("allowed_shots") or [
        "medium",
        "half_body",
        "full_body",
        "three_quarter",
        "rear_view",
    ]
    visible_ordinal = 0
    for scene in scenes:
        index = int(scene["index"])
        base_prompt = str(scene.get("prompt") or "")
        if index in visible_indices:
            shot_type = str(allowed_shots[visible_ordinal % len(allowed_shots)])
            visible_ordinal += 1
            reference_role = (
                "full_body"
                if shot_type == "full_body"
                else "half_body"
                if shot_type in {"medium", "half_body"}
                else "primary_portrait"
            )
            scene.update(
                {
                    "character_presence": True,
                    "shot_type": shot_type,
                    "reference_role": reference_role,
                    "identity_notes": character.get("identity_prompt") or "",
                    "era_context": era.get("label") or era.get("key") or "",
                }
            )
            scene["prompt"] = (
                f"{base_prompt} Character identity: {character.get('identity_prompt') or ''}. "
                f"Historical appearance: {era.get('appearance_prompt') or ''}. "
                f"Historical setting: {era.get('context_prompt') or ''}. "
                f"Use a respectful {shot_type.replace('_', ' ')} shot and preserve the identity from references. "
                "Natural closed or resting mouth; no speech pose, no lip-sync, no tight close-up."
            )
        else:
            scene.update(
                {
                    "character_presence": False,
                    "shot_type": "environment",
                    "reference_role": "",
                    "identity_notes": "",
                    "era_context": era.get("label") or era.get("key") or "",
                }
            )
            scene["prompt"] = (
                f"{base_prompt} Environmental scene only: landscape, architecture, objects, or atmosphere. "
                "Do not show Ho Chi Minh or any identifiable historical person."
            )
        scene["prompt"] += (
            f" Visual style: {character.get('style_prompt') or ''}. "
            "Neutral black-and-white archival documentary image, no colorization, no sepia cast, "
            "no written words, typography, logo, watermark, or subtitles. Vertical 9:16 composition."
        )
    plan["character"] = {
        "profile_key": character.get("key") or "",
        "profile_version": character.get("profile_version") or 0,
        "era_key": era.get("key") or "",
        "era_label": era.get("label") or "",
        "source": resolution.get("source") or "",
        "confidence": resolution.get("confidence"),
        "visible_scene_count": visible_count,
        "scene_count": len(scenes),
    }
    return plan


def _ensure_storyboard(
    *,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    profile: dict,
    pipeline: dict,
    duration: float,
    timings: list[dict],
) -> dict:
    storyboard_state = checkpoint.setdefault("storyboard", {})
    relative = str(storyboard_state.get("path") or "")
    if relative:
        path = _resolve_artifact(item_dir, relative)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    visual = profile.get("visual_profile") or {}
    character = profile.get("resolved_character_profile")
    resolution = checkpoint.get("character_resolution")
    era = (
        _character_era(profile, str((resolution or {}).get("era_key") or ""))
        if isinstance(character, dict) and isinstance(resolution, dict)
        else None
    )
    planner_style = str(visual.get("style") or "")
    if isinstance(character, dict) and isinstance(era, dict):
        planner_style = " ".join(
            value
            for value in (
                str(character.get("style_prompt") or ""),
                str(era.get("appearance_prompt") or ""),
                str(era.get("context_prompt") or ""),
            )
            if value
        )
    windows = build_scene_windows(
        poem=str(item.get("source_text") or ""),
        timings=timings,
        duration=duration,
        pipeline=pipeline,
        max_video_scenes=int(visual.get("max_video_scenes") or 0),
    )
    if not chatgpt_runtime.try_start_operation("short_video_storyboard"):
        raise ShortVideoRetryLater(
            "Profile ChatGPT đang bận; Short Video sẽ thử lập storyboard lại.",
            delay_seconds=20,
        )
    try:
        planner_result = generate_muse_scene_plan_only(
            title=str(item.get("title") or ""),
            style=planner_style,
            windows=windows,
            cancel_check=lambda: _check_canceled(str(job["id"])),
        )
    finally:
        chatgpt_runtime.finish_operation()
    plan = muse_semantic_planner.compile_muse_scene_plan(
        planner_result["semantic_plan"],
        windows,
        title=str(item.get("title") or ""),
        style=planner_style,
        aspect_ratio="9:16",
    )
    if isinstance(character, dict):
        plan = _apply_character_profile_to_plan(plan, profile, checkpoint)
    else:
        prohibition = (
            " Vertical 9:16 composition. No written words, typography, logo, watermark, "
            "subtitles, lip-sync, speaking mouth, or close-up portrait. "
            "Prefer landscapes, objects, architecture, and restrained human figures."
        )
        for scene in plan.get("scenes") or []:
            scene["prompt"] = str(scene.get("prompt") or "") + prohibition
    target = item_dir / "storyboard" / "scene_manifest.json"
    _atomic_json(target, plan)
    storyboard_state.update(
        {
            "path": target.relative_to(item_dir).as_posix(),
            "planner_source": planner_result.get("planner_source"),
            "chat_url": planner_result.get("chat_url") or "",
            "scene_count": len(plan.get("scenes") or []),
        }
    )
    checkpoint["scenes"] = [
        {
            "index": int(scene["index"]),
            "image": {},
            "video": {},
            "fallback_to_image": False,
        }
        for scene in plan.get("scenes") or []
    ]
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage="storyboard_ready",
        progress=f"Đã lập storyboard {len(plan.get('scenes') or [])} cảnh bằng ChatGPT Playwright",
    )
    return plan


def _validate_vertical_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except Exception as exc:
        raise ShortVideoPipelineError("Visual provider trả ảnh cảnh không hợp lệ.") from exc
    if width <= 0 or height <= 0 or abs(width / height - 9 / 16) > 0.03:
        raise ShortVideoPipelineError("Visual provider trả ảnh không đúng tỷ lệ 9:16.")


def _receipt_from_state(state: dict) -> MuseReceipt | None:
    receipt = state.get("receipt") if isinstance(state.get("receipt"), dict) else {}
    if not all(receipt.get(key) for key in ("provider_task_id", "endpoint", "submitted_at")):
        return None
    return MuseReceipt(
        provider_task_id=str(receipt["provider_task_id"]),
        endpoint=str(receipt["endpoint"]),
        submitted_at=str(receipt["submitted_at"]),
    )


def _scene_character_references(
    *,
    scene: dict,
    checkpoint: dict,
    profile: dict,
    item_dir: Path,
    provider: str,
) -> list[dict]:
    if not bool(scene.get("character_presence")):
        return []
    resolution = checkpoint.get("character_resolution") or {}
    era = _character_era(profile, str(resolution.get("era_key") or ""))
    if era is None:
        raise ShortVideoPipelineError("Không tìm thấy reference cho thời kỳ đã resolve.")
    references = list(era.get("references") or [])
    primary = next((reference for reference in references if reference.get("primary")), None)
    desired_role = str(scene.get("reference_role") or "primary_portrait")
    matching = [
        reference for reference in references if reference.get("role") == desired_role
    ]
    if provider == "google_flow":
        selected = (matching or ([primary] if primary else []) or references)[:1]
    else:
        selected = []
        for reference in [primary, *matching, *references]:
            if reference and reference.get("id") not in {
                value.get("id") for value in selected
            }:
                selected.append(reference)
            if len(selected) >= 3:
                break
    if not selected:
        raise ShortVideoAttentionRequired(
            "Scene có Bác Hồ nhưng không có ảnh tham chiếu phù hợp.",
            reason="character_references_unavailable",
        )
    result: list[dict] = []
    for reference in selected:
        path = _resolve_artifact(item_dir, str(reference.get("snapshot_path") or ""))
        if not path.is_file():
            raise ShortVideoAttentionRequired(
                "Ảnh snapshot Character Profile không còn tồn tại.",
                reason="character_references_unavailable",
            )
        result.append(
            {
                "id": str(reference.get("id") or ""),
                "sha256": str(reference.get("sha256") or ""),
                "role": str(reference.get("role") or ""),
                "path": path,
            }
        )
    return result


async def _generate_muse_media_async(
    *,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    profile: dict,
    plan: dict,
) -> None:
    visual = profile.get("visual_profile") or {}
    resolution = str(visual.get("image_resolution") or "1080x1920")
    scene_states = checkpoint.setdefault("scenes", [])
    state_by_index = {int(state["index"]): state for state in scene_states}
    provider = MuseStudioApiProvider(job_id=str(job["id"]))
    async with provider.open_session() as adapter:
        for scene in plan.get("scenes") or []:
            _check_canceled(str(job["id"]))
            index = int(scene["index"])
            state = state_by_index[index]
            image_state = state.setdefault("image", {})
            relative = str(image_state.get("path") or "")
            existing = _resolve_artifact(item_dir, relative) if relative else None
            if existing and existing.is_file():
                _validate_vertical_image(existing)
                continue
            destination = item_dir / "scenes" / f"scene_{index:03d}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            revision = int((checkpoint.get("scene_revisions") or {}).get(str(index)) or 0)
            references = _scene_character_references(
                scene=scene,
                checkpoint=checkpoint,
                profile=profile,
                item_dir=item_dir,
                provider="muse",
            )
            prompt = MusePromptBuilder.image(
                str(scene.get("prompt") or ""),
                has_references=bool(references),
                aspect_ratio="9:16",
                resolution=resolution,
            )
            request_hash = _canonical_hash(
                {
                    "kind": "short_image",
                    "item_id": item["id"],
                    "scene": index,
                    "revision": revision,
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "resolution": resolution,
                    "character_profile_version": (
                        (profile.get("resolved_character_profile") or {}).get("profile_version")
                    ),
                    "character_era": (checkpoint.get("character_resolution") or {}).get("era_key"),
                    "references": [
                        {"id": reference["id"], "sha256": reference["sha256"]}
                        for reference in references
                    ],
                    "storyboard_version": "character-profile-v1",
                }
            )

            def on_submitted(receipt: MuseReceipt, *, target=image_state) -> None:
                target["receipt"] = {
                    "provider_task_id": receipt.provider_task_id,
                    "endpoint": receipt.endpoint,
                    "submitted_at": receipt.submitted_at,
                }
                target["request_hash"] = request_hash
                _save_checkpoint(
                    checkpoint,
                    item_dir=item_dir,
                    job=job,
                    item=item,
                    stage=f"muse_image_{index}",
                    progress=f"Muse đang tạo ảnh dọc cảnh {index + 1}",
                )

            receipt = _receipt_from_state(image_state)
            result = None
            if receipt is not None:
                result = await adapter.reconcile(
                    receipt=receipt,
                    destination=destination,
                    expected="image",
                )
            if result is None:
                result = await adapter.create_image(
                    prompt,
                    [reference["path"] for reference in references],
                    destination,
                    request_hash=request_hash,
                    aspect_ratio="9:16",
                    resolution=resolution,
                    on_submitted=on_submitted,
                    cancel_check=lambda: _check_canceled(str(job["id"])),
                )
            result_path = Path(str(result.get("path") or destination))
            _validate_vertical_image(result_path)
            image_state.update(
                {
                    "path": result_path.relative_to(item_dir).as_posix(),
                    "status": "ready",
                    "request_hash": request_hash,
                    "reference_asset_ids": [reference["id"] for reference in references],
                }
            )
            _save_checkpoint(
                checkpoint,
                item_dir=item_dir,
                job=job,
                item=item,
                stage=f"muse_image_{index}_ready",
                progress=f"Đã tạo ảnh dọc cảnh {index + 1}",
            )

        scenes = plan.get("scenes") or []
        for scene in scenes:
            if not scene.get("is_video"):
                continue
            _check_canceled(str(job["id"]))
            index = int(scene["index"])
            if index + 1 >= len(scenes):
                continue
            state = state_by_index[index]
            video_state = state.setdefault("video", {})
            relative = str(video_state.get("path") or "")
            existing = _resolve_artifact(item_dir, relative) if relative else None
            if existing and existing.is_file():
                continue
            start_path = _resolve_artifact(
                item_dir, str(state_by_index[index]["image"].get("path") or "")
            )
            end_path = _resolve_artifact(
                item_dir, str(state_by_index[index + 1]["image"].get("path") or "")
            )
            destination = item_dir / "scenes" / f"scene_{index:03d}.mp4"
            revision = int((checkpoint.get("scene_revisions") or {}).get(str(index)) or 0)
            prompt = MusePromptBuilder.video(
                str(scene.get("action") or scene.get("prompt") or ""),
                duration_seconds=float(scene.get("duration") or 7),
                aspect_ratio="9:16",
                resolution=resolution,
            )
            request_hash = _canonical_hash(
                {
                    "kind": "short_video",
                    "item_id": item["id"],
                    "scene": index,
                    "revision": revision,
                    "prompt": prompt,
                    "start": state_by_index[index]["image"].get("request_hash"),
                    "end": state_by_index[index + 1]["image"].get("request_hash"),
                    "aspect_ratio": "9:16",
                    "resolution": resolution,
                }
            )

            def on_video_submitted(receipt: MuseReceipt, *, target=video_state) -> None:
                target["receipt"] = {
                    "provider_task_id": receipt.provider_task_id,
                    "endpoint": receipt.endpoint,
                    "submitted_at": receipt.submitted_at,
                }
                target["request_hash"] = request_hash
                _save_checkpoint(
                    checkpoint,
                    item_dir=item_dir,
                    job=job,
                    item=item,
                    stage=f"muse_video_{index}",
                    progress=f"Muse đang tạo chuyển động cảnh {index + 1}",
                )

            try:
                receipt = _receipt_from_state(video_state)
                result = None
                if receipt is not None:
                    result = await adapter.reconcile(
                        receipt=receipt,
                        destination=destination,
                        expected="video",
                    )
                if result is None:
                    result = await adapter.create_video(
                        prompt,
                        start_path,
                        end_path,
                        destination,
                        request_hash=request_hash,
                        aspect_ratio="9:16",
                        resolution=resolution,
                        duration_seconds=float(scene.get("duration") or 7),
                        on_submitted=on_video_submitted,
                        cancel_check=lambda: _check_canceled(str(job["id"])),
                    )
                result_path = Path(str(result.get("path") or destination))
                media = short_video_renderer.probe(result_path)
                stream = next(
                    (entry for entry in media["streams"] if entry.get("codec_type") == "video"),
                    {},
                )
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                if width <= 0 or height <= 0 or abs(width / height - 9 / 16) > 0.03:
                    raise ShortVideoPipelineError("Clip Muse không đúng tỷ lệ 9:16.")
                video_state.update(
                    {
                        "path": result_path.relative_to(item_dir).as_posix(),
                        "status": "ready",
                        "request_hash": request_hash,
                    }
                )
                state["fallback_to_image"] = False
            except ShortVideoCanceled:
                raise
            except Exception as exc:
                logger.warning(
                    "Short Video Muse clip degraded to image for item=%s scene=%s error=%s",
                    item["id"],
                    index,
                    security_logging.redact_sensitive(type(exc).__name__),
                )
                video_state.clear()
                video_state.update(
                    {
                        "status": "degraded",
                        "reason": type(exc).__name__,
                    }
                )
                state["fallback_to_image"] = True
            _save_checkpoint(
                checkpoint,
                item_dir=item_dir,
                job=job,
                item=item,
                stage=f"muse_video_{index}_ready",
                progress=(
                    f"Đã tạo chuyển động cảnh {index + 1}"
                    if not state.get("fallback_to_image")
                    else f"Cảnh {index + 1} dùng pan/zoom từ ảnh do clip Muse lỗi"
                ),
            )


@asynccontextmanager
async def _flow_worker_session(
    *, checkpoint: dict, item_dir: Path, job: dict, item: dict
):
    from playwright.async_api import async_playwright

    from nexus_engine.services import google_flow_browser_service
    from nexus_engine.services.google_flow_worker import GoogleFlowWorker

    try:
        usage_lease = await asyncio.to_thread(
            google_flow_browser_service.acquire_browser_service
        )
    except Exception as exc:
        raise ShortVideoAttentionRequired(
            "Browser Google Flow chưa sẵn sàng cho Short Video.",
            reason="google_flow_unavailable",
        ) from exc

    try:
        endpoint = google_flow_browser_service.get_browser_service_endpoint()
        if not endpoint:
            raise ShortVideoAttentionRequired(
                "Browser Google Flow chưa kết nối được.",
                reason="google_flow_unavailable",
            )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(endpoint)
            if not browser.contexts:
                raise ShortVideoAttentionRequired(
                    "Browser Google Flow không có context đang hoạt động.",
                    reason="google_flow_unavailable",
                )
            context = browser.contexts[0]
            try:
                await google_flow_browser_service.keep_attached_browser_hidden(
                    browser,
                    context,
                )
            except Exception as exc:
                logger.warning(
                    "Could not hide Google Flow Short Video worker window: %s",
                    security_logging.redact_sensitive(exc),
                )
            page = next(
                (
                    candidate
                    for candidate in context.pages
                    if "flow.google.com" in str(candidate.url or "").casefold()
                    or "labs.google" in str(candidate.url or "").casefold()
                ),
                None,
            )
            if page is None:
                page = context.pages[0] if context.pages else await context.new_page()

            flow_state = checkpoint.setdefault("google_flow", {})
            mapped_url = str(flow_state.get("project_url") or "").strip()
            if not re.match(
                r"^https://(?:flow\.google\.com|labs\.google)/project/",
                mapped_url,
                flags=re.IGNORECASE,
            ):
                mapped_url = ""
            if mapped_url:
                try:
                    await page.goto(
                        mapped_url,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not open mapped Flow project for Short Video item=%s error=%s",
                        item["id"],
                        security_logging.redact_sensitive(type(exc).__name__),
                    )

            numeric_id = int(hashlib.sha256(str(item["id"]).encode("utf-8")).hexdigest()[:12], 16)
            project_name = str(flow_state.get("project_name") or f"nexus_{numeric_id}")
            worker = GoogleFlowWorker(page)
            worker.video_id = numeric_id
            project_url = await worker.ensure_project(
                project_name,
                force_new=not bool(mapped_url),
            )
            updated_flow_state = {
                "project_name": project_name,
                "project_id": worker._project_id(),
                "project_url": str(project_url or page.url or ""),
            }
            if flow_state != updated_flow_state:
                flow_state.clear()
                flow_state.update(updated_flow_state)
                _save_checkpoint(
                    checkpoint,
                    item_dir=item_dir,
                    job=job,
                    item=item,
                    stage="google_flow_project_ready",
                    progress="Google Flow đã sẵn sàng cho Short Video",
                )
            yield worker
    finally:
        usage_lease.close()


def _flow_request_key(item_id: str, media_type: str, index: int, revision: int) -> str:
    compact_id = re.sub(r"[^A-Za-z0-9]", "", item_id)[:20]
    return f"sv-{compact_id}-{media_type}-{index}-r{revision}"


def _persist_flow_intent(
    state: dict,
    *,
    request_hash: str,
    request_key: str,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    stage: str,
    progress: str,
) -> None:
    existing_intent = state.get("submission_intent")
    if state.get("request_hash") == request_hash and isinstance(existing_intent, dict):
        return
    state.clear()
    state.update(
        {
            "status": "intent_saved",
            "request_hash": request_hash,
            "submission_intent": {
                "request_key": request_key,
                "request_hash": request_hash,
                "saved_at": db.utc_now(),
            },
        }
    )
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage=stage,
        progress=progress,
    )


def _save_flow_pending(
    state: dict,
    exc: Exception,
    worker,
    *,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    stage: str,
    progress: str,
) -> None:
    receipt = getattr(exc, "receipt", None)
    if not isinstance(receipt, dict) or not receipt:
        receipt = worker.get_last_generation_receipt()
    if isinstance(receipt, dict) and receipt:
        state["receipt"] = receipt
    state["status"] = "pending"
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage=stage,
        progress=progress,
    )


async def _generate_flow_media_async(
    *,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    profile: dict,
    plan: dict,
) -> None:
    from nexus_engine.services.google_flow_worker import FlowMediaPending

    visual = profile.get("visual_profile") or {}
    image_model = str(visual.get("google_flow_image_model") or "nano_banana_2_1")
    video_model = str(visual.get("google_flow_video_model") or "omni_1_1_flash")
    avoid_prompt = ", ".join(str(value) for value in (visual.get("forbid") or []))
    scene_states = checkpoint.setdefault("scenes", [])
    state_by_index = {int(state["index"]): state for state in scene_states}
    scenes = plan.get("scenes") or []

    async with _flow_worker_session(
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
    ) as worker:
        for scene in scenes:
            _check_canceled(str(job["id"]))
            index = int(scene["index"])
            state = state_by_index[index]
            image_state = state.setdefault("image", {})
            relative = str(image_state.get("path") or "")
            existing = _resolve_artifact(item_dir, relative) if relative else None
            if existing and existing.is_file():
                _validate_vertical_image(existing)
                continue

            destination = item_dir / "scenes" / f"scene_{index:03d}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            revision = int((checkpoint.get("scene_revisions") or {}).get(str(index)) or 0)
            prompt = str(scene.get("prompt") or "")
            references = _scene_character_references(
                scene=scene,
                checkpoint=checkpoint,
                profile=profile,
                item_dir=item_dir,
                provider="google_flow",
            )
            reference_ids = [reference["id"] for reference in references]
            reference_paths = {
                reference["id"]: str(reference["path"]) for reference in references
            } or None
            request_hash = _canonical_hash(
                {
                    "kind": "short_flow_image",
                    "item_id": item["id"],
                    "scene": index,
                    "revision": revision,
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "model": image_model,
                    "character_profile_version": (
                        (profile.get("resolved_character_profile") or {}).get("profile_version")
                    ),
                    "character_era": (checkpoint.get("character_resolution") or {}).get("era_key"),
                    "references": [
                        {"id": reference["id"], "sha256": reference["sha256"]}
                        for reference in references
                    ],
                    "storyboard_version": "character-profile-v1",
                }
            )
            request_key = _flow_request_key(str(item["id"]), "image", index, revision)
            _persist_flow_intent(
                image_state,
                request_hash=request_hash,
                request_key=request_key,
                checkpoint=checkpoint,
                item_dir=item_dir,
                job=job,
                item=item,
                stage=f"flow_image_{index}_intent",
                progress=f"Đã lưu intent tạo ảnh Flow cảnh {index + 1}",
            )
            receipt = image_state.get("receipt")
            try:
                asset_url = await worker.generate_scene(
                    prompt=prompt,
                    avoid_prompt=avoid_prompt,
                    reference_ids=reference_ids,
                    reference_paths=reference_paths,
                    scene_index=index,
                    scene_count=len(scenes),
                    image_settings={
                        "model": image_model,
                        "aspect_ratio": "9:16",
                        "output_count": 1,
                    },
                    request_key=request_key,
                    reconcile_only=isinstance(receipt, dict) and bool(receipt),
                    check_late_result=True,
                    submit_if_prompt_missing=not bool(receipt),
                    submit_only=not bool(receipt),
                )
                await worker.download_image(asset_url, str(destination))
            except FlowMediaPending as exc:
                _save_flow_pending(
                    image_state,
                    exc,
                    worker,
                    checkpoint=checkpoint,
                    item_dir=item_dir,
                    job=job,
                    item=item,
                    stage=f"flow_image_{index}_pending",
                    progress=f"Google Flow đang tạo ảnh cảnh {index + 1}",
                )
                raise ShortVideoRetryLater(
                    "Google Flow đang tạo ảnh; Short Video sẽ tiếp tục đối soát.",
                    delay_seconds=float(getattr(exc, "retry_delay_seconds", 30.0)),
                ) from exc

            _validate_vertical_image(destination)
            image_state.update(
                {
                    "path": destination.relative_to(item_dir).as_posix(),
                    "status": "ready",
                    "request_hash": request_hash,
                    "reference_asset_ids": reference_ids,
                }
            )
            _save_checkpoint(
                checkpoint,
                item_dir=item_dir,
                job=job,
                item=item,
                stage=f"flow_image_{index}_ready",
                progress=f"Đã tạo ảnh dọc Flow cảnh {index + 1}",
            )

        for scene in scenes:
            if not scene.get("is_video"):
                continue
            _check_canceled(str(job["id"]))
            index = int(scene["index"])
            if index + 1 >= len(scenes):
                continue
            state = state_by_index[index]
            video_state = state.setdefault("video", {})
            relative = str(video_state.get("path") or "")
            existing = _resolve_artifact(item_dir, relative) if relative else None
            if existing and existing.is_file():
                continue

            start_path = _resolve_artifact(
                item_dir, str(state_by_index[index]["image"].get("path") or "")
            )
            end_path = _resolve_artifact(
                item_dir, str(state_by_index[index + 1]["image"].get("path") or "")
            )
            destination = item_dir / "scenes" / f"scene_{index:03d}.mp4"
            revision = int((checkpoint.get("scene_revisions") or {}).get(str(index)) or 0)
            prompt = str(scene.get("action") or scene.get("prompt") or "")
            request_hash = _canonical_hash(
                {
                    "kind": "short_flow_video",
                    "item_id": item["id"],
                    "scene": index,
                    "revision": revision,
                    "prompt": prompt,
                    "start": state_by_index[index]["image"].get("request_hash"),
                    "end": state_by_index[index + 1]["image"].get("request_hash"),
                    "aspect_ratio": "9:16",
                    "model": video_model,
                }
            )
            request_key = _flow_request_key(str(item["id"]), "video", index, revision)
            _persist_flow_intent(
                video_state,
                request_hash=request_hash,
                request_key=request_key,
                checkpoint=checkpoint,
                item_dir=item_dir,
                job=job,
                item=item,
                stage=f"flow_video_{index}_intent",
                progress=f"Đã lưu intent tạo video Flow cảnh {index + 1}",
            )
            receipt = video_state.get("receipt")
            try:
                asset_url = await worker.generate_scene_video(
                    prompt=prompt,
                    avoid_prompt=avoid_prompt,
                    start_frame_path=start_path,
                    end_frame_path=end_path,
                    reference_ids=[],
                    video_settings={
                        "video_model": video_model,
                        "video_aspect_ratio": "9:16",
                        "video_output_count": 1,
                    },
                    scene_index=index,
                    reconcile_only=True,
                    submit_if_prompt_missing=not bool(receipt),
                    request_key=request_key,
                    submit_only=not bool(receipt),
                )
                await worker.download_video(asset_url, str(destination))
                media = short_video_renderer.probe(destination)
                stream = next(
                    (entry for entry in media["streams"] if entry.get("codec_type") == "video"),
                    {},
                )
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                if width <= 0 or height <= 0 or abs(width / height - 9 / 16) > 0.03:
                    raise ShortVideoPipelineError("Clip Google Flow không đúng tỷ lệ 9:16.")
                video_state.update(
                    {
                        "path": destination.relative_to(item_dir).as_posix(),
                        "status": "ready",
                        "request_hash": request_hash,
                    }
                )
                state["fallback_to_image"] = False
            except FlowMediaPending as exc:
                _save_flow_pending(
                    video_state,
                    exc,
                    worker,
                    checkpoint=checkpoint,
                    item_dir=item_dir,
                    job=job,
                    item=item,
                    stage=f"flow_video_{index}_pending",
                    progress=f"Google Flow đang tạo chuyển động cảnh {index + 1}",
                )
                raise ShortVideoRetryLater(
                    "Google Flow đang tạo video; Short Video sẽ tiếp tục đối soát.",
                    delay_seconds=float(getattr(exc, "retry_delay_seconds", 30.0)),
                ) from exc
            except ShortVideoCanceled:
                raise
            except Exception as exc:
                logger.warning(
                    "Short Video Flow clip degraded to image for item=%s scene=%s error=%s",
                    item["id"],
                    index,
                    security_logging.redact_sensitive(type(exc).__name__),
                )
                video_state.clear()
                video_state.update({"status": "degraded", "reason": type(exc).__name__})
                state["fallback_to_image"] = True

            _save_checkpoint(
                checkpoint,
                item_dir=item_dir,
                job=job,
                item=item,
                stage=f"flow_video_{index}_ready",
                progress=(
                    f"Đã tạo chuyển động Flow cảnh {index + 1}"
                    if not state.get("fallback_to_image")
                    else f"Cảnh {index + 1} dùng pan/zoom từ ảnh do clip Flow lỗi"
                ),
            )


def _ensure_media(
    *, checkpoint: dict, item_dir: Path, job: dict, item: dict, profile: dict, plan: dict
) -> None:
    visual_provider = str((profile.get("visual_profile") or {}).get("provider") or "muse")
    media_generator = (
        _generate_flow_media_async
        if visual_provider == "google_flow"
        else _generate_muse_media_async
    )
    asyncio.run(
        media_generator(
            checkpoint=checkpoint,
            item_dir=item_dir,
            job=job,
            item=item,
            profile=profile,
            plan=plan,
        )
    )
    _atomic_json(item_dir / "scene_manifest.json", {"scenes": checkpoint.get("scenes") or []})


def _render_scenes(checkpoint: dict, plan: dict, item_dir: Path) -> list[dict]:
    states = {int(state["index"]): state for state in checkpoint.get("scenes") or []}
    rendered: list[dict] = []
    for scene in plan.get("scenes") or []:
        index = int(scene["index"])
        state = states[index]
        image_path = _resolve_artifact(item_dir, str(state.get("image", {}).get("path") or ""))
        video_relative = str(state.get("video", {}).get("path") or "")
        video_path = _resolve_artifact(item_dir, video_relative) if video_relative else None
        rendered.append(
            {
                "index": index,
                "duration": float(scene.get("duration") or 0),
                "image_path": str(image_path),
                "video_path": str(video_path) if video_path and video_path.is_file() else "",
            }
        )
    return rendered


def _render_artifact(
    *,
    kind: str,
    checkpoint: dict,
    item_dir: Path,
    job: dict,
    item: dict,
    profile: dict,
    plan: dict,
) -> dict:
    render_profile = profile.get("render_profile") or {}
    target_profile = render_profile.get(kind) or {}
    destination = item_dir / "renders" / f"{kind}.mp4"
    audio_path = _resolve_artifact(
        item_dir, str((checkpoint.get("audio") or {}).get("normalized_path") or "")
    )
    result = short_video_renderer.render(
        scenes=_render_scenes(checkpoint, plan, item_dir),
        audio_path=audio_path,
        destination=destination,
        width=int(target_profile.get("width") or (540 if kind == "draft" else 1080)),
        height=int(target_profile.get("height") or (960 if kind == "draft" else 1920)),
        fps=int(target_profile.get("fps") or 30),
        job_id=str(job["id"]),
        monochrome=isinstance(profile.get("resolved_character_profile"), dict),
    )
    checkpoint.setdefault("artifacts", {})[kind] = {
        "path": destination.relative_to(item_dir).as_posix(),
        "qc": result["qc"],
        "created_at": db.utc_now(),
    }
    _save_checkpoint(
        checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        stage=f"{kind}_ready",
        progress=f"Đã render và QC bản {kind}",
    )
    return result


def _invalidate_for_audio_regeneration(checkpoint: dict) -> None:
    revision = int(checkpoint.get("audio_revision") or 0) + 1
    character_resolution = deepcopy(checkpoint.get("character_resolution") or {})
    checkpoint.clear()
    checkpoint.update(
        {
            "version": CHECKPOINT_VERSION,
            "stage": "regenerate_audio",
            "audio_revision": revision,
            "scene_revisions": {},
            "audio": {},
            "timing": {},
            "storyboard": {},
            "scenes": [],
            "artifacts": {},
            "character_resolution": character_resolution,
        }
    )


def _invalidate_for_visual_regeneration(checkpoint: dict) -> None:
    revisions = dict(checkpoint.get("scene_revisions") or {})
    for state in checkpoint.get("scenes") or []:
        key = str(int(state.get("index") or 0))
        revisions[key] = int(revisions.get(key) or 0) + 1
    preserved = {
        "version": CHECKPOINT_VERSION,
        "stage": "regenerate_visuals",
        "audio_revision": int(checkpoint.get("audio_revision") or 0),
        "scene_revisions": revisions,
        "audio": deepcopy(checkpoint.get("audio") or {}),
        "timing": deepcopy(checkpoint.get("timing") or {}),
        "storyboard": {},
        "scenes": [],
        "artifacts": {},
    }
    if isinstance(checkpoint.get("google_flow"), dict):
        preserved["google_flow"] = deepcopy(checkpoint["google_flow"])
    checkpoint.clear()
    checkpoint.update(preserved)


def _invalidate_scene(checkpoint: dict, scene_index: int) -> None:
    revisions = checkpoint.setdefault("scene_revisions", {})
    revisions[str(scene_index)] = int(revisions.get(str(scene_index)) or 0) + 1
    states = {int(state["index"]): state for state in checkpoint.get("scenes") or []}
    if scene_index not in states:
        raise ShortVideoPipelineError("Scene cần tạo lại không tồn tại.")
    states[scene_index]["image"] = {}
    states[scene_index]["video"] = {}
    states[scene_index]["fallback_to_image"] = False
    if scene_index > 0 and scene_index - 1 in states:
        states[scene_index - 1]["video"] = {}
        states[scene_index - 1]["fallback_to_image"] = False
    checkpoint.setdefault("artifacts", {}).pop("draft", None)
    checkpoint.setdefault("artifacts", {}).pop("final", None)


def execute(job: dict) -> None:
    payload = job.get("payload") or {}
    item_id = str(payload.get("short_video_id") or "")
    item = db.get_short_video_item(item_id)
    if item is None:
        raise ShortVideoPipelineError("Không tìm thấy item Short Video của job.")
    profile, pipeline, voice_snapshot = _validate_profile(item)
    item_dir = _safe_item_dir(str(item["project_key"]), str(item["id"]))
    checkpoint = load_checkpoint(item_dir, item_id=str(item["id"]))
    action = str(payload.get("action") or "generate")
    checkpoint["item_id"] = item["id"]
    if action == "regenerate_audio":
        _invalidate_for_audio_regeneration(checkpoint)
        checkpoint["item_id"] = item["id"]
    elif action == "regenerate_scene":
        _invalidate_scene(checkpoint, int(payload.get("scene_index")))
    elif action == "regenerate_visuals":
        _invalidate_for_visual_regeneration(checkpoint)
        checkpoint["item_id"] = item["id"]

    db.update_short_video_item(
        item_id,
        status="processing",
        current_stage=action,
        error="",
    )
    if action == "final_render":
        storyboard_relative = str((checkpoint.get("storyboard") or {}).get("path") or "")
        draft_relative = str((checkpoint.get("artifacts") or {}).get("draft", {}).get("path") or "")
        if not storyboard_relative or not draft_relative:
            raise ShortVideoPipelineError("Chưa có draft hợp lệ để render final.")
        if not _resolve_artifact(item_dir, draft_relative).is_file():
            raise ShortVideoPipelineError("Artifact draft không còn tồn tại để duyệt.")
        plan = json.loads(_resolve_artifact(item_dir, storyboard_relative).read_text(encoding="utf-8"))
        _render_artifact(
            kind="final",
            checkpoint=checkpoint,
            item_dir=item_dir,
            job=job,
            item=item,
            profile=profile,
            plan=plan,
        )
        db.update_short_video_item(
            item_id,
            status="completed",
            current_stage="completed",
            error="",
            completed_at=db.utc_now(),
        )
        db.update_system_job(
            str(job["id"]),
            status="completed",
            progress="Short Video final đã hoàn thành",
            result_json={"short_video_id": item_id, "artifact": "final"},
            error="",
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
        return

    _ensure_character_era(
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        profile=profile,
    )
    max_duration = float(pipeline.get("max_duration_seconds") or 180)
    estimate = _estimated_duration_seconds(
        str(item.get("title") or ""),
        str(item.get("source_text") or ""),
        voice_snapshot,
    )
    checkpoint["estimated_duration_seconds"] = round(estimate, 3)
    if not (checkpoint.get("audio") or {}).get("provider_task_id") and estimate > max_duration:
        _save_checkpoint(
            checkpoint,
            item_dir=item_dir,
            job=job,
            item=item,
            stage="duration_estimate_exceeded",
            progress="Bài thơ ước tính vượt giới hạn 180 giây",
        )
        raise ShortVideoAttentionRequired(
            f"Bài thơ ước tính dài {estimate:.1f} giây, vượt giới hạn {max_duration:.0f} giây; hệ thống không tự cắt hoặc tăng tốc.",
            reason="duration_estimate_exceeded",
        )
    duration = _ensure_audio(
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        voice_snapshot=voice_snapshot,
        max_duration=max_duration,
    )
    timings = _ensure_timing(
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
    )
    plan = _ensure_storyboard(
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        profile=profile,
        pipeline=pipeline,
        duration=duration,
        timings=timings,
    )
    _ensure_media(
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        profile=profile,
        plan=plan,
    )
    _render_artifact(
        kind="draft",
        checkpoint=checkpoint,
        item_dir=item_dir,
        job=job,
        item=item,
        profile=profile,
        plan=plan,
    )
    db.update_short_video_item(
        item_id,
        status="awaiting_review",
        current_stage="awaiting_review",
        error="",
    )
    db.update_system_job(
        str(job["id"]),
        status="completed",
        progress="Draft Short Video đang chờ duyệt",
        result_json={"short_video_id": item_id, "artifact": "draft"},
        error="",
        cancel_requested=0,
        finished_at=db.utc_now(),
    )


def handle_error(job: dict, exc: Exception) -> None:
    item_id = str((job.get("payload") or {}).get("short_video_id") or "")
    safe_error = security_logging.safe_user_error(
        "short_video_generation",
        exc,
        "Pipeline Short Video thất bại.",
    )
    if isinstance(exc, ShortVideoCanceled):
        db.update_system_job(
            str(job["id"]),
            status="canceled",
            progress="Đã dừng Short Video tại checkpoint an toàn",
            error="",
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
        if item_id:
            db.update_short_video_item(
                item_id,
                status="canceled",
                current_stage="canceled",
                error="",
            )
        return
    if isinstance(exc, ShortVideoRetryLater):
        db.schedule_system_job_recovery(
            str(job["id"]),
            resume_from_step=str(job.get("resume_from_step") or "checkpoint"),
            delay_seconds=exc.delay_seconds,
            error=safe_error,
            progress=str(exc),
        )
        return
    if isinstance(exc, (ShortVideoAttentionRequired, ChatGPTAttentionRequiredError)):
        reason = (
            exc.reason
            if isinstance(exc, ShortVideoAttentionRequired)
            else "chatgpt_verification"
        )
        db.update_system_job(
            str(job["id"]),
            status="paused",
            progress=str(exc),
            result_json={"attention_required": reason, "short_video_id": item_id},
            error=safe_error,
            next_retry_at="",
            cancel_requested=0,
            finished_at="",
        )
        if item_id:
            db.update_short_video_item(
                item_id,
                status="needs_attention",
                current_stage=reason,
                error=safe_error,
            )
        return
    db.update_system_job(
        str(job["id"]),
        status="error",
        progress="Pipeline Short Video thất bại",
        error=safe_error,
        cancel_requested=0,
        finished_at=db.utc_now(),
    )
    if item_id:
        db.update_short_video_item(
            item_id,
            status="error",
            current_stage="error",
            error=safe_error,
        )


def public_scene_manifest(item: dict) -> list[dict]:
    item_dir = _safe_item_dir(str(item["project_key"]), str(item["id"]))
    checkpoint = load_checkpoint(item_dir, item_id=str(item["id"]))
    plan: dict = {}
    relative = str((checkpoint.get("storyboard") or {}).get("path") or "")
    if relative:
        path = _resolve_artifact(item_dir, relative)
        if path.is_file():
            plan = json.loads(path.read_text(encoding="utf-8"))
    states = {int(state["index"]): state for state in checkpoint.get("scenes") or []}
    result: list[dict] = []
    for scene in plan.get("scenes") or []:
        index = int(scene["index"])
        state = states.get(index) or {}
        image_ready = bool((state.get("image") or {}).get("path"))
        video_ready = bool((state.get("video") or {}).get("path"))
        result.append(
            {
                "index": index,
                "start": scene.get("start"),
                "end": scene.get("end"),
                "duration": scene.get("duration"),
                "transcript": scene.get("transcript") or "",
                "source_lines": scene.get("source_lines") or [],
                "media_type": "video" if video_ready else "image",
                "fallback_to_image": bool(state.get("fallback_to_image")),
                "character_presence": bool(scene.get("character_presence")),
                "shot_type": scene.get("shot_type") or "",
                "reference_role": scene.get("reference_role") or "",
                "reference_asset_ids": list(
                    (state.get("image") or {}).get("reference_asset_ids") or []
                ),
                "era_context": scene.get("era_context") or "",
                "image_url": (
                    f"/api/short-video/items/{item['id']}/artifacts/scene-{index}-image"
                    if image_ready
                    else ""
                ),
                "video_url": (
                    f"/api/short-video/items/{item['id']}/artifacts/scene-{index}-video"
                    if video_ready
                    else ""
                ),
            }
        )
    return result


def artifact_path(item: dict, kind: str) -> tuple[Path, str]:
    item_dir = _safe_item_dir(str(item["project_key"]), str(item["id"]))
    checkpoint = load_checkpoint(item_dir, item_id=str(item["id"]))
    mime_type = "application/octet-stream"
    relative = ""
    if kind == "audio":
        relative = str((checkpoint.get("audio") or {}).get("normalized_path") or "")
        mime_type = "audio/wav"
    elif kind in {"draft", "final"}:
        relative = str((checkpoint.get("artifacts") or {}).get(kind, {}).get("path") or "")
        mime_type = "video/mp4"
    else:
        match = re.fullmatch(r"scene-(\d+)-(image|video)", kind)
        if match:
            index = int(match.group(1))
            media_kind = match.group(2)
            state = next(
                (
                    entry
                    for entry in checkpoint.get("scenes") or []
                    if int(entry.get("index") or 0) == index
                ),
                {},
            )
            relative = str((state.get(media_kind) or {}).get("path") or "")
            mime_type = "image/png" if media_kind == "image" else "video/mp4"
    if not relative:
        raise FileNotFoundError("Artifact Short Video chưa sẵn sàng.")
    path = _resolve_artifact(item_dir, relative)
    if not path.is_file():
        raise FileNotFoundError("Artifact Short Video không tồn tại.")
    return path, mime_type
