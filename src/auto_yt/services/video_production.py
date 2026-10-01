"""Checkpointed scene generation, captions and MP4 rendering."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import statistics
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import imageio_ffmpeg

from auto_yt.services import process_registry
from auto_yt.paths import (
    AUDIO_DIR,
    CAPTIONS_DIR,
    RENDERS_DIR,
    SCENES_DIR,
    SEGMENTS_DIR,
    THUMBNAILS_DIR,
    VISUAL_PLANS_DIR,
)
from auto_yt.services import database as db
from auto_yt.services.visual_action_translator import (
    normalize_visual_identity,
    remove_vietnamese_accents,
    translate_transcript_to_visual_action,
)

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 2 * 1024 * 1024 * 1024
TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
TARGET_FPS = 30
SCENE_IMAGE_WIDTH = 1024
SCENE_IMAGE_HEIGHT = 576
PERCEPTUAL_HASH_IMAGE_SIZE = 32
PERCEPTUAL_HASH_SIZE = 8
PERCEPTUAL_DUPLICATE_DISTANCE = 6
LOW_VARIANCE_IMAGE_THRESHOLD = 2.0
LOW_VARIANCE_COLOR_DISTANCE = 8.0
MAX_PERCEPTUAL_DUPLICATE_RETRIES = 2
PERCEPTUAL_RETRY_DIRECTIONS = (
    "Use a substantially different camera distance, viewpoint, and subject arrangement",
    "Change the foreground, background, direction of movement, and visual hierarchy completely",
)
_WHISPER_LOCK = threading.Lock()
_WHISPER_MODEL = None
_WHISPER_DEVICE = ""
_ENCODER_LOCK = threading.Lock()
_ENCODER = ""


class VideoProductionError(RuntimeError):
    pass


class SceneVisualDuplicateError(VideoProductionError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


ImagePerceptualFingerprint = tuple[int, tuple[float, float, float], float]


def _image_perceptual_fingerprint(path: Path) -> ImagePerceptualFingerprint:
    """Return a structure-sensitive image hash plus low-variance safeguards."""
    from PIL import Image, ImageStat

    with Image.open(path) as source:
        rgb = source.convert("RGB")
        color_sample = rgb.resize((64, 64), Image.Resampling.LANCZOS)
        color_statistics = ImageStat.Stat(color_sample)
        mean_rgb = tuple(float(value) for value in color_statistics.mean[:3])
        grayscale_deviation = float(
            ImageStat.Stat(color_sample.convert("L")).stddev[0]
        )
        grayscale = rgb.convert("L").resize(
            (PERCEPTUAL_HASH_IMAGE_SIZE, PERCEPTUAL_HASH_IMAGE_SIZE),
            Image.Resampling.LANCZOS,
        )
        pixels = list(grayscale.tobytes())

    cosine_table = [
        [
            math.cos(
                (2 * coordinate + 1)
                * frequency
                * math.pi
                / (2 * PERCEPTUAL_HASH_IMAGE_SIZE)
            )
            for coordinate in range(PERCEPTUAL_HASH_IMAGE_SIZE)
        ]
        for frequency in range(PERCEPTUAL_HASH_SIZE)
    ]
    coefficients: list[float] = []
    for vertical_frequency in range(PERCEPTUAL_HASH_SIZE):
        vertical_scale = (
            1 / math.sqrt(2) if vertical_frequency == 0 else 1.0
        )
        for horizontal_frequency in range(PERCEPTUAL_HASH_SIZE):
            horizontal_scale = (
                1 / math.sqrt(2) if horizontal_frequency == 0 else 1.0
            )
            total = 0.0
            for y_coordinate in range(PERCEPTUAL_HASH_IMAGE_SIZE):
                row_offset = y_coordinate * PERCEPTUAL_HASH_IMAGE_SIZE
                vertical_weight = cosine_table[vertical_frequency][y_coordinate]
                for x_coordinate in range(PERCEPTUAL_HASH_IMAGE_SIZE):
                    total += (
                        pixels[row_offset + x_coordinate]
                        * cosine_table[horizontal_frequency][x_coordinate]
                        * vertical_weight
                    )
            coefficients.append(
                0.25 * horizontal_scale * vertical_scale * total
            )

    median_value = statistics.median(coefficients[1:])
    perceptual_hash = 0
    for coefficient in coefficients[1:]:
        perceptual_hash = (perceptual_hash << 1) | int(
            coefficient > median_value
        )
    return perceptual_hash, mean_rgb, grayscale_deviation


def _perceptual_fingerprints_match(
    first: ImagePerceptualFingerprint,
    second: ImagePerceptualFingerprint,
) -> bool:
    hash_distance = (first[0] ^ second[0]).bit_count()
    if hash_distance > PERCEPTUAL_DUPLICATE_DISTANCE:
        return False
    if (
        first[2] <= LOW_VARIANCE_IMAGE_THRESHOLD
        and second[2] <= LOW_VARIANCE_IMAGE_THRESHOLD
    ):
        color_distance = math.dist(first[1], second[1])
        return color_distance <= LOW_VARIANCE_COLOR_DISTANCE
    return True


def _matches_existing_perceptual_fingerprint(
    candidate: ImagePerceptualFingerprint,
    existing: list[ImagePerceptualFingerprint] | None,
) -> bool:
    return any(
        _perceptual_fingerprints_match(candidate, fingerprint)
        for fingerprint in existing or []
    )


def _build_perceptual_retry_prompt(
    base_prompt: str,
    scene: dict,
    retry_index: int,
) -> str:
    direction = PERCEPTUAL_RETRY_DIRECTIONS[
        (retry_index - 1) % len(PERCEPTUAL_RETRY_DIRECTIONS)
    ]
    action = re.sub(r"\s+", " ", str(scene.get("action") or "")).strip()
    action_instruction = f" Keep the narrative event: {action}." if action else ""
    return (
        f"{base_prompt} Visual uniqueness requirement: {direction}."
        f"{action_instruction} Do not reuse any earlier composition or image layout."
    )


def _parse_timestamp(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours * 3600 + minutes * 60 + seconds)
    raise ValueError("Invalid timestamp")


def extract_chapters(generated_script: str, duration_seconds: float) -> list[dict]:
    match = re.search(
        r"### \[CHAPTERS\]\s*(.*?)(?=\n### \[|\Z)",
        generated_script or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    chapter_text = match.group(1) if match else generated_script or ""
    candidates = []
    pattern = re.compile(
        r"(?m)^\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—:]\s*(?P<title>.+?)\s*$"
    )
    for item in pattern.finditer(chapter_text):
        try:
            start = _parse_timestamp(item.group("time"))
        except ValueError:
            continue
        if start < 0 or start >= duration_seconds:
            continue
        title = re.sub(r"\s+", " ", item.group("title")).strip()
        if title:
            candidates.append({"start": start, "title": title})
    candidates.sort(key=lambda item: item["start"])
    deduplicated = []
    for chapter in candidates:
        if deduplicated and chapter["start"] <= deduplicated[-1]["start"]:
            continue
        deduplicated.append(chapter)
    if not deduplicated:
        raise VideoProductionError("Chapters không có timestamp hợp lệ để dựng video.")
    if deduplicated[0]["start"] > 1:
        deduplicated.insert(0, {"start": 0.0, "title": "Mở đầu"})
    for index, chapter in enumerate(deduplicated):
        end = (
            deduplicated[index + 1]["start"]
            if index + 1 < len(deduplicated)
            else duration_seconds
        )
        chapter["end"] = max(chapter["start"] + 0.1, end)
        chapter["duration"] = chapter["end"] - chapter["start"]
    return deduplicated


def _parse_srt_time(value: str) -> float:
    hours, minutes, remainder = value.strip().replace(".", ",").split(":")
    seconds, milliseconds = remainder.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds.ljust(3, "0")[:3]) / 1000
    )


def parse_srt_segments(srt_path: Path) -> list[dict]:
    blocks = re.split(r"\r?\n\s*\r?\n", srt_path.read_text(encoding="utf-8-sig"))
    segments: list[dict] = []
    timestamp_pattern = re.compile(
        r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
    )
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_index = next(
            (index for index, line in enumerate(lines) if timestamp_pattern.search(line)),
            None,
        )
        if timestamp_index is None:
            continue
        match = timestamp_pattern.search(lines[timestamp_index])
        text = re.sub(r"\s+", " ", " ".join(lines[timestamp_index + 1 :])).strip()
        if not text:
            continue
        segments.append(
            {
                "start": _parse_srt_time(match.group("start")),
                "end": _parse_srt_time(match.group("end")),
                "text": text,
            }
        )
    if not segments:
        raise VideoProductionError("SRT không có lời thoại hợp lệ để lập cảnh.")
    return segments


def _extract_normalized_words(text: str) -> list[str]:
    """Extract lowercase words with Vietnamese diacritics stripped."""
    clean = remove_vietnamese_accents(str(text or "").lower())
    return [w for w in re.findall(r"\w+", clean) if len(w) > 1]


def extract_intro_boundary(
    generated_script: str,
    captions: list[dict],
    duration_seconds: float,
) -> float:
    """Extract the ending timestamp (in seconds) of the intro/hook section from script and captions.
    
    Uses a robust 3-tier matching engine:
    1. Tail N-gram phrase matching on ### [INTRO].
    2. Head N-gram phrase matching on ### [BODY] (cross-verification).
    3. Word-density ratio anchor and chapter fallbacks.
    """
    if not generated_script or not captions:
        return 0.0

    intro_match = re.search(
        r"### \[(?:INTRO|MỞ ĐẦU|PHẦN 1: MỞ ĐẦU|MO DAU)\]\s*\n(.*?)(?=\n### \[|\Z)",
        generated_script,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body_match = re.search(
        r"### \[(?:BODY|THÂN BÀI|PHẦN 2|NOI DUNG)\]\s*\n(.*?)(?=\n### \[|\Z)",
        generated_script,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if not intro_match:
        return min(24.0, max(8.0, duration_seconds * 0.15))

    intro_words = _extract_normalized_words(intro_match.group(1))
    if len(intro_words) < 5:
        return min(24.0, max(8.0, duration_seconds * 0.15))

    all_script_words = _extract_normalized_words(generated_script)
    intro_ratio = len(intro_words) / max(1, len(all_script_words))
    expected_time = max(8.0, intro_ratio * duration_seconds)
    max_search_time = min(
        duration_seconds * 0.8,
        max(expected_time * 2.0, expected_time + 40.0, 180.0),
    )

    # 1. Match tail of Intro (last 6-10 words)
    tail_words = intro_words[-10:] if len(intro_words) >= 10 else intro_words
    tail_candidates: list[tuple[float, float]] = []

    for i, cap in enumerate(captions):
        start_time = float(cap.get("start") or 0.0)
        end_time = float(cap.get("end") or 0.0)
        if start_time > max_search_time:
            break

        single_words = set(_extract_normalized_words(cap.get("text", "")))
        match_count = sum(1 for w in tail_words if w in single_words)
        score = match_count / len(tail_words)

        if score < 0.5 and i + 1 < len(captions):
            combined_words = single_words | set(
                _extract_normalized_words(captions[i + 1].get("text", ""))
            )
            comb_count = sum(1 for w in tail_words if w in combined_words)
            comb_score = comb_count / len(tail_words)
            if comb_score >= 0.5:
                score = comb_score
                end_time = float(captions[i + 1].get("end") or end_time)

        if score >= 0.4:
            prox_penalty = abs(end_time - expected_time) / max(1.0, duration_seconds)
            adj_score = score - (prox_penalty * 0.2)
            tail_candidates.append((adj_score, end_time))

    # 2. Match head of Body (first 6-10 words) as dual verification
    body_head_time = 0.0
    if body_match:
        body_words = _extract_normalized_words(body_match.group(1))
        if len(body_words) >= 5:
            head_words = body_words[:10]
            best_head_score = 0.0
            for i, cap in enumerate(captions):
                start_time = float(cap.get("start") or 0.0)
                if start_time > max_search_time:
                    break
                single_words = set(_extract_normalized_words(cap.get("text", "")))
                head_score = sum(1 for w in head_words if w in single_words) / len(head_words)
                if head_score < 0.5 and i + 1 < len(captions):
                    comb_words = single_words | set(
                        _extract_normalized_words(captions[i + 1].get("text", ""))
                    )
                    head_score = sum(1 for w in head_words if w in comb_words) / len(head_words)
                if head_score >= 0.4 and head_score > best_head_score:
                    best_head_score = head_score
                    body_head_time = start_time

    # Best decision
    if tail_candidates:
        tail_candidates.sort(key=lambda x: x[0], reverse=True)
        best_tail_score, best_tail_time = tail_candidates[0]
        if body_head_time > 0 and abs(best_tail_time - body_head_time) <= 5.0:
            return max(best_tail_time, body_head_time)
        if best_tail_score >= 0.4:
            return best_tail_time

    if body_head_time > 0:
        return body_head_time

    # 3. Chapter fallback
    try:
        chapters = extract_chapters(generated_script, duration_seconds)
        if len(chapters) >= 2:
            ch1_start = float(chapters[1]["start"])
            if 6.0 <= ch1_start <= max_search_time:
                return ch1_start
    except Exception:
        pass

    return min(max_search_time, max(8.0, expected_time))


def build_scene_windows(
    captions: list[dict],
    duration_seconds: float,
    *,
    minimum_seconds: float = 25,
    target_seconds: float = 30,
    maximum_seconds: float = 35,
    intro_end_seconds: float = 0.0,
    intro_target_seconds: float = 8.0,
    enable_intro_video: bool = True,
) -> list[dict]:
    """Group real caption timestamps into bounded visual-planning windows,
    allocating ~8s video scenes for the intro section and ~30s image scenes for the body.
    """
    if not 1 <= minimum_seconds <= target_seconds <= maximum_seconds:
        raise ValueError("Scene duration settings are inconsistent.")
    windows: list[dict] = []
    cursor = 0
    use_intro = enable_intro_video and intro_end_seconds >= 5.0

    while cursor < len(captions):
        start = max(0.0, float(captions[cursor]["start"]))
        in_intro = use_intro and (
            start < (intro_end_seconds - 0.5)
            or (start + max(4.0, intro_target_seconds * 0.75) * 0.5) < intro_end_seconds
        )
        
        cur_target = intro_target_seconds if in_intro else target_seconds
        cur_min = max(4.0, intro_target_seconds * 0.75) if in_intro else minimum_seconds
        cur_max = min(14.0, intro_target_seconds * 1.35) if in_intro else maximum_seconds

        best_end_index = cursor
        best_distance = float("inf")
        scan = cursor
        while scan < len(captions):
            candidate_end = min(duration_seconds, float(captions[scan]["end"]))
            candidate_duration = candidate_end - start
            if candidate_duration > cur_max and scan > cursor:
                break
            if candidate_duration >= cur_min:
                distance = abs(candidate_duration - cur_target)
                sentence_end = bool(re.search(r"[.!?…][\"')\]]?$", captions[scan]["text"]))
                score = distance - (2.0 if sentence_end else 0.0)
                if score < best_distance:
                    best_distance = score
                    best_end_index = scan
                if candidate_duration >= cur_target and sentence_end:
                    break
            elif scan == len(captions) - 1:
                best_end_index = scan
            scan += 1
        if best_end_index == cursor and scan > cursor:
            best_end_index = max(cursor, scan - 1)
        end = min(duration_seconds, float(captions[best_end_index]["end"]))
        if best_end_index == len(captions) - 1:
            end = max(end, duration_seconds)
        transcript = re.sub(
            r"\s+",
            " ",
            " ".join(item["text"] for item in captions[cursor : best_end_index + 1]),
        ).strip()
        windows.append(
            {
                "index": len(windows),
                "start": start,
                "end": end,
                "duration": max(0.1, end - start),
                "transcript": transcript,
                "is_video": in_intro,
                "media_type": "video" if in_intro else "image",
            }
        )
        cursor = best_end_index + 1
    for index, window in enumerate(windows):
        start = 0.0 if index == 0 else float(windows[index - 1]["end"])
        end = duration_seconds if index == len(windows) - 1 else float(window["end"])
        window["start"] = start
        window["end"] = max(start + 0.1, end)
        window["duration"] = window["end"] - start
    return windows


def validate_visual_scene_plan(payload: dict, windows: list[dict]) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("visual_bible"), dict):
        raise VideoProductionError("ChatGPT trả về visual bible không hợp lệ.")
    supplied = payload.get("scenes")
    if not isinstance(supplied, list) or len(supplied) != len(windows):
        raise VideoProductionError("ChatGPT trả về thiếu hoặc thừa cảnh.")
    normalized = []
    seen_prompts: set[str] = set()
    seen_actions: set[str] = set()
    for window, scene in zip(windows, supplied):
        if not isinstance(scene, dict) or int(scene.get("index", -1)) != window["index"]:
            raise VideoProductionError("ChatGPT trả về sai thứ tự scene.")
        prompt = re.sub(r"\s+", " ", str(scene.get("prompt") or "")).strip()
        lowered = prompt.casefold()
        if len(prompt) < 80 or "youtube documentary scene about" in lowered:
            raise VideoProductionError("Prompt cảnh còn chung chung, không đủ chi tiết hình ảnh.")
        prompt_key = normalize_visual_identity(prompt)
        if prompt_key in seen_prompts:
            raise VideoProductionError("Hai cảnh dùng cùng prompt; kế hoạch ảnh bị từ chối.")
        seen_prompts.add(prompt_key)
        action = re.sub(r"\s+", " ", str(scene.get("action") or "")).strip()
        action_key = normalize_visual_identity(action)
        if action_key and action_key in seen_actions:
            raise VideoProductionError(
                "Hai cảnh mô tả cùng một hành động hình ảnh; kế hoạch ảnh bị từ chối."
            )
        if action_key:
            seen_actions.add(action_key)
        normalized.append(
            {
                **window,
                "is_video": window.get("is_video", False),
                "media_type": window.get("media_type", "image"),
                "subject": str(scene.get("subject") or "").strip(),
                "action": action,
                "setting": str(scene.get("setting") or "").strip(),
                "era": str(scene.get("era") or "").strip(),
                "composition": str(scene.get("composition") or "").strip(),
                "lighting": str(scene.get("lighting") or "").strip(),
                "color": str(scene.get("color") or "").strip(),
                "prompt": prompt,
                "primary_reference_id": str(
                    scene.get("primary_reference_id") or ""
                ).strip(),
                "reference_path": str(
                    scene.get("reference_path") or ""
                ).strip(),
            }
        )
    return {"visual_bible": payload["visual_bible"], "scenes": normalized}


def materialize_audio(audio_url: str, video_id: int, request_hash: str) -> Path:
    parsed = urllib.parse.urlparse(str(audio_url or ""))
    filename = Path(parsed.path).name
    if parsed.hostname in {"127.0.0.1", "localhost"} and filename:
        local_path = AUDIO_DIR / filename
        if local_path.is_file():
            return local_path
    target = AUDIO_DIR / f"video_{video_id}_{request_hash[:16]}.mp3"
    if target.is_file() and target.stat().st_size > 0:
        return target
    if parsed.scheme not in {"http", "https"}:
        raise VideoProductionError("Audio URL không hợp lệ để dựng video.")
    temporary = target.with_suffix(".mp3.tmp")
    request = urllib.request.Request(audio_url, headers={"Accept": "audio/mpeg,*/*"})
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    raise VideoProductionError("Audio vượt giới hạn dung lượng dựng video.")
                output.write(chunk)
        if total == 0:
            raise VideoProductionError("Audio tải về bị rỗng.")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _scene_hash(
    video_id: int,
    scene: dict,
    profile: dict | None = None,
    negative_prompt: str = "",
    reference_hash: str = "",
) -> str:
    profile_dict = profile if isinstance(profile, dict) else {}
    payload = json.dumps(
        {
            "video_id": video_id,
            "scene": scene,
            "profile_id": profile_dict.get("id", ""),
            "negative_prompt": negative_prompt,
            "reference_hash": reference_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return _sha256_bytes(payload)


def cleanup_duplicate_scene_artifacts(video_id: int) -> int:
    """Find and remove any cached scene artifacts that have duplicate files or duplicate hashes.
    Ensures that retrying video generation will self-heal and re-generate unique scenes instead of getting stuck."""
    artifacts = list(db.list_video_artifacts(video_id, "scene:")) + list(db.list_video_artifacts(video_id, "scene_video:"))
    seen_hashes: dict[str, str] = {}
    removed_count = 0
    for art in artifacts:
        art_path = art.get("path")
        p = Path(art_path) if art_path else None
        if not p or not p.is_file() or p.stat().st_size == 0:
            if art.get("id"):
                db.delete_video_artifact(art["id"])
                removed_count += 1
            continue
        try:
            h = _sha256_file(p)
        except Exception:
            continue
        if h in seen_hashes:
            logger.warning(
                "Found duplicate scene artifact (id=%s, type=%s, hash=%s, matches earlier %s). Purging corrupted cache to force regeneration.",
                art.get("id"),
                art.get("artifact_type"),
                h,
                seen_hashes[h],
            )
            if art.get("id"):
                db.delete_video_artifact(art["id"])
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
            removed_count += 1
        else:
            seen_hashes[h] = art.get("artifact_type")
    return removed_count


def purge_all_scene_artifacts(video_id: int) -> int:
    """Purge all scene artifacts from DB and disk when recreating from scratch."""
    artifacts = list(db.list_video_artifacts(video_id, "scene:")) + list(db.list_video_artifacts(video_id, "scene_video:"))
    removed_count = 0
    for art in artifacts:
        art_path = art.get("path")
        if art_path:
            try:
                Path(art_path).unlink(missing_ok=True)
            except Exception:
                pass
        if art.get("id"):
            db.delete_video_artifact(art["id"])
            removed_count += 1
    logger.info("Purged %d scene artifacts for video %s", removed_count, video_id)
    return removed_count


def purge_scene_artifacts_from_index(video_id: int, from_index: int) -> int:
    """Purge scene artifacts from DB and disk starting from a specific scene index."""
    artifacts = list(db.list_video_artifacts(video_id, "scene:")) + list(db.list_video_artifacts(video_id, "scene_video:"))
    removed_count = 0
    for art in artifacts:
        art_type = str(art.get("artifact_type") or "")
        try:
            idx_str = art_type.split(":", 1)[1]
            scene_idx = int(idx_str)
        except (IndexError, ValueError):
            continue

        if scene_idx >= from_index:
            art_path = art.get("path")
            if art_path:
                try:
                    Path(art_path).unlink(missing_ok=True)
                except Exception:
                    pass
            if art.get("id"):
                db.delete_video_artifact(art["id"])
                removed_count += 1

    # Also purge final MP4 if any, so it will be re-rendered cleanly
    final_mp4 = db.get_latest_video_artifact(video_id, "final_mp4")
    if final_mp4:
        mp4_path = final_mp4.get("path")
        if mp4_path:
            try:
                Path(mp4_path).unlink(missing_ok=True)
            except Exception:
                pass
        if final_mp4.get("id"):
            db.delete_video_artifact(final_mp4["id"])

    # Also purge visual_scene_plan artifact so it will rebuild clean title-free prompts
    scene_plan_art = db.get_latest_video_artifact(video_id, "visual_scene_plan")
    if scene_plan_art:
        plan_path = scene_plan_art.get("path")
        if plan_path:
            try:
                Path(plan_path).unlink(missing_ok=True)
            except Exception:
                pass
        if scene_plan_art.get("id"):
            db.delete_video_artifact(scene_plan_art["id"])

    logger.info(
        "Purged %d scene artifacts for video %s starting from index %d",
        removed_count,
        video_id,
        from_index,
    )
    return removed_count


def _sanitize_scene_prompt_for_generation(
    raw_prompt: str,
    *,
    video_title: str = "",
    scene_action: str = "",
    style_prompt: str = "",
) -> str:
    """Sanitize and ensure scene prompt is 100% free of video titles and legacy anchoring."""
    clean = (raw_prompt or "").strip()

    # 1. Strip legacy title-injecting prefixes and patterns
    legacy_patterns = [
        r"(?i)\b(?:scene depiction|cinematic visual illustrating|story visual core|story theme|dramatic opening scene hook for)\s*[:：\-–—]?\s*",
        r"(?i)\bdramatic opening scene hook for\s+[\'\"“«][^\'\"”»]+[\'\"”»]\.?",
        r"(?i)\bopening scene hook for\s+[\'\"“«][^\'\"”»]+[\'\"”»]\.?",
    ]
    for pat in legacy_patterns:
        clean = re.sub(pat, "", clean)

    # 2. If video_title is given and appears in the prompt, strip it completely
    if video_title and len(video_title.strip()) >= 4:
        clean = re.sub(re.escape(video_title.strip()), "", clean, flags=re.IGNORECASE)
        sanitized_title = _sanitize_scene_prompt_context(video_title.strip())
        if sanitized_title and len(sanitized_title) >= 4:
            clean = re.sub(re.escape(sanitized_title), "", clean, flags=re.IGNORECASE)

    # 3. Clean up punctuation artifacts, dangling quotes, double periods, multiple spaces
    clean = re.sub(r"[\'\"“”«»]", "", clean)
    clean = re.sub(r"\s*,\s*,\s*", ", ", clean)
    clean = re.sub(r"\s*\.\s*\.\s*", ". ", clean)
    clean = re.sub(r"\s*:\s*:", ":", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"^\s*[,.:;\-–—]\s*", "", clean).strip()

    # 4. If prompt became too short or empty, reconstruct from style + scene action
    if len(clean) < 30:
        style = (style_prompt or "").split("\n")[0][:200].strip() or "Cinematic documentary visual style, photorealistic, 8k resolution"
        action = _sanitize_scene_prompt_context(scene_action, max_chars=120)
        action_part = f"Narrative action: {action}. " if action else ""
        clean = (
            f"A cinematic photograph: {style}. "
            f"{action_part}"
            f"16:9 widescreen still photograph, authentic realism, dramatic lighting, clean visual without text."
        )

    # 5. Ensure clean visual without text directive is present
    if "clean visual without text" not in clean.lower() and "without text" not in clean.lower():
        clean = f"{clean.rstrip('. ')}. Clean visual without text."

    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _format_scene_video_prompt(
    raw_prompt: str = "",
    *,
    scene_action: str = "",
    style_prompt: str = "",
    video_style_prompt: str = "",
    video_prompt_template: str = "",
    video_motion_prompt: str = "",
    video_title: str = "",
    has_start_frame: bool = True,
    has_end_frame: bool = False,
) -> str:
    """Format an explicit, generic video generation prompt for Google Flow Agent / Veo."""
    # 1. Determine frame interpolation / animation directive based on attached images
    if has_start_frame and has_end_frame:
        frame_directive = (
            "Generate exactly one 16:9 video, not a still image. "
            "Using the first attached image as the starting frame and "
            "the second attached image as the ending frame, generate a seamless "
            "cinematic video transition from the first frame to the second frame."
        )
    elif has_start_frame:
        frame_directive = (
            "Generate exactly one 16:9 video, not a still image. Using the attached "
            "image as the starting frame, animate it into a cinematic video clip."
        )
    else:
        frame_directive = "Generate exactly one 16:9 high-quality cinematic video clip, not a still image."

    # 2. Extract and sanitize action context (completely topic-agnostic)
    raw_action = scene_action or raw_prompt
    raw_action = re.sub(r"(?i)depict a concrete.*?narration\s*[:：]?\s*", "", raw_action)
    raw_action = re.sub(r"(?i)grounded only in this narration\s*[:：]?\s*", "", raw_action)
    action_clean = _sanitize_scene_prompt_context(raw_action, max_chars=140)
    action_directive = f"Scene action: {action_clean}." if action_clean else ""

    # 3. Clean style prompt without still photo keywords
    effective_style = video_style_prompt.strip() if video_style_prompt and video_style_prompt.strip() else (style_prompt or "")
    style_clean = effective_style.split("\n")[0][:180].strip()
    style_clean = re.sub(
        r"(?i)\b(?:still photograph|photography|35mm photography|photo|film still|raw photo|movie still|still photo)\b",
        "cinematic video",
        style_clean,
    )
    style_clean = re.sub(r"\s+", " ", style_clean).strip()
    style_directive = f"Visual style: {style_clean}." if style_clean else "Visual style: cinematic documentary film, atmospheric natural lighting."

    # 4. Cinematic motion and technical specs (generic 100%)


    # 5. Anti-text instruction
    if video_motion_prompt and video_motion_prompt.strip():
        motion_clean = video_motion_prompt.strip()
        motion_clean = motion_clean.replace("{style}", style_clean).replace("{action}", action_clean)
        motion_directive = motion_clean
    else:
        motion_directive = "Motion: smooth cinematic camera movement, natural realistic motion, 4k 24fps high-fidelity video."

    if "without text" in motion_directive.lower() or "without any text" in motion_directive.lower():
        anti_text_directive = ""
    else:
        anti_text_directive = "Clean video without any text, letters, watermark, or subtitles."

    if video_prompt_template and video_prompt_template.strip():
        custom_video = video_prompt_template.strip()
        custom_video = (
            custom_video
            .replace("{frame_directive}", frame_directive)
            .replace("{action}", action_clean)
            .replace("{style}", style_clean)
            .replace("{motion}", motion_directive)
        )
        full_video_prompt = re.sub(r"\s+", " ", custom_video).strip()
    else:
        full_video_prompt = f"{frame_directive} {action_directive} {style_directive} {motion_directive} {anti_text_directive}"
    full_video_prompt = re.sub(r"\s+", " ", full_video_prompt).strip()
    return full_video_prompt


def _flow_mock_enabled() -> bool:
    return (
        os.environ.get("YOUTUBE_UPLOAD", "true") == "false"
        or os.environ.get("FLOW_MOCK_GENERATION", "false") == "true"
    )


@asynccontextmanager
async def _flow_worker_session(video_id: int, *, force_new_project: bool = False):
    """Open one Playwright/CDP connection for a complete scene generation batch."""
    if _flow_mock_enabled():
        yield None
        return

    from playwright.async_api import async_playwright
    from auto_yt.services import google_flow_browser_service
    from auto_yt.services.google_flow_worker import GoogleFlowWorker

    endpoint = google_flow_browser_service.get_browser_service_endpoint()
    if not endpoint:
        google_flow_browser_service.start_browser_service()
        await asyncio.sleep(5)
        endpoint = google_flow_browser_service.get_browser_service_endpoint()
    if not endpoint:
        raise VideoProductionError("Browser Google Flow chưa khởi động được.")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(endpoint)
        if not browser.contexts:
            raise VideoProductionError("Google Flow browser không có context đang hoạt động.")
        context = browser.contexts[0]
        page = next(
            (
                candidate for candidate in context.pages
                if "flow.google.com" in str(candidate.url or "").lower()
                or "labs.google" in str(candidate.url or "").lower()
            ),
            None,
        )
        if page is None:
            page = context.pages[0] if context.pages else await context.new_page()

        worker = GoogleFlowWorker(page)
        await worker.ensure_project(
            f"auto_yt_{video_id}",
            force_new=force_new_project,
        )
        yield worker


def _log_scene_generation(
    *,
    video_id: int,
    scene_index: int,
    media_type: str,
    state: str,
    attempt: int,
    started_at: float,
    detail: str = "",
) -> None:
    logger.info(
        "flow_scene video_id=%s scene=%s media=%s state=%s attempt=%s elapsed=%.2f detail=%s",
        video_id,
        scene_index,
        media_type,
        state,
        attempt,
        max(0.0, time.monotonic() - started_at),
        detail,
    )


async def _generate_scene_image_async(
    *,
    video_id: int,
    scene: dict,
    scene_count: int,
    profile: dict,
    settings: dict,
    reference_path: Path | None,
    reference_id: str,
    progress,
    cancel_check,
    worker,
    existing_hashes: set[str] | None = None,
    existing_perceptual_hashes: list[ImagePerceptualFingerprint] | None = None,
    force_new_project: bool = False,
) -> Path:
    negative_prompt = str(settings.get("avoid_prompt") or settings.get("negative_prompt") or "")
    fixed_seed = int(_sha256_bytes(str(video_id).encode("utf-8"))[:15], 16)
    cancel_check()
    reference_hash = _sha256_file(reference_path) if reference_path else ""
    content_hash = _scene_hash(
        video_id, scene, profile, negative_prompt, reference_hash
    )
    artifact_type = f"scene:{scene['index']}"
    existing = db.get_latest_video_artifact(video_id, artifact_type)

    cache_matches_plan = bool(
        existing and existing.get("content_hash") == content_hash
    )
    if existing and not cache_matches_plan:
        logger.info(
            "Ignoring stale scene cache for video=%s scene=%s: expected=%s actual=%s",
            video_id,
            scene.get("index"),
            content_hash,
            existing.get("content_hash"),
        )
    if (
        not force_new_project
        and cache_matches_plan
        and existing.get("status") == "completed"
    ):
        artifact_path = Path(existing["path"])
        if artifact_path.exists():
            art_hash = _sha256_file(artifact_path)
            art_fingerprint = _image_perceptual_fingerprint(artifact_path)
            is_exact_duplicate = bool(
                existing_hashes is not None and art_hash in existing_hashes
            )
            is_visual_duplicate = _matches_existing_perceptual_fingerprint(
                art_fingerprint,
                existing_perceptual_hashes,
            )
            if is_exact_duplicate or is_visual_duplicate:
                logger.warning(
                    "Cached artifact %s for scene %s duplicates an earlier scene (%s). Purging cache and regenerating.",
                    existing.get("id"),
                    scene.get("index"),
                    "exact" if is_exact_duplicate else "perceptual",
                )
                if existing.get("id"):
                    db.delete_video_artifact(existing["id"])
                try:
                    artifact_path.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                if worker is not None and hasattr(worker, "register_video_frame_metadata"):
                    worker.register_video_frame_metadata(
                        artifact_path,
                        existing.get("metadata") or {},
                    )
                return artifact_path

    target = SCENES_DIR / f"{video_id}_{scene['index']}_{content_hash[:8]}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    generation_attempt = existing.get("metadata", {}).get("generation_attempt", 0) + 1 if existing else 1
    
    started_at = time.monotonic()
    progress(
        f"Đang tạo ảnh {scene['index'] + 1}/{scene_count} bằng Google Flow",
        f"generating_flow_image {scene['index'] + 1}/{scene_count}",
    )

    db.upsert_video_artifact(
        video_id=video_id,
        artifact_type=artifact_type,
        path=str(target),
        content_hash=content_hash,
        status="processing",
        mime_type="image/png",
        metadata={
            **scene,
            "seed": fixed_seed,
            "generation_attempt": generation_attempt,
            "reference_id": reference_id,
            "reference_hash": reference_hash,
        },
    )

    flow_frame_metadata: dict[str, str] = {}
    perceptual_duplicate_retries = 0
    try:
        if _flow_mock_enabled():
            from PIL import Image

            c1 = (int(scene.get("index", 0)) * 37 + 40) % 256
            c2 = (int(scene.get("index", 0)) * 73 + 80) % 256
            c3 = (int(scene.get("index", 0)) * 109 + 120) % 256
            Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), color=(c1, c2, c3)).save(target)
        else:
            if worker is None:
                raise VideoProductionError("Google Flow worker chưa được khởi tạo.")

            from auto_yt.services.google_flow_worker import FlowGenerationError

            video_rec = db.get_video(video_id) or {}
            video_title = str(video_rec.get("title") or video_rec.get("generated_title") or "")
            scene_action = str(scene.get("action") or scene.get("transcript") or "")
            style_str = str(settings.get("style_prompt") or profile.get("style_prompt") or "")
            clean_prompt = _sanitize_scene_prompt_for_generation(
                scene.get("prompt", ""),
                video_title=video_title,
                scene_action=scene_action,
                style_prompt=style_str,
            )
            refs = [reference_id] if reference_id else []
            reference_paths = (
                {reference_id: str(reference_path)}
                if reference_id and reference_path
                else {}
            )

            concise_style = (
                style_str.split("\n")[0][:200].strip()
                if style_str
                else "Cinematic documentary visual style, photorealistic, 8k resolution"
            )
            clean_action = _sanitize_scene_prompt_context(scene_action, max_chars=120)
            context_desc = f"Narrative action: {clean_action}. " if clean_action else ""
            safe_prompt = (
                f"A cinematic still photograph: {concise_style}. "
                f"{context_desc}"
                "16:9 widescreen still photograph, authentic realism, "
                "dramatic atmospheric lighting, clean visual without text."
            ).replace("  ", " ").strip()

            while True:
                generation_prompt = (
                    clean_prompt
                    if perceptual_duplicate_retries == 0
                    else _build_perceptual_retry_prompt(
                        clean_prompt,
                        scene,
                        perceptual_duplicate_retries,
                    )
                )
                _log_scene_generation(
                    video_id=video_id,
                    scene_index=int(scene.get("index", 0)),
                    media_type="image",
                    state=(
                        "submitted"
                        if perceptual_duplicate_retries == 0
                        else "retry_visual_duplicate"
                    ),
                    attempt=perceptual_duplicate_retries + 1,
                    started_at=started_at,
                )
                try:
                    asset_url = await worker.generate_scene(
                        generation_prompt,
                        negative_prompt,
                        refs,
                        reference_paths,
                        scene_index=int(scene.get("index", 0)),
                        scene_count=scene_count,
                    )
                except FlowGenerationError as first_error:
                    logger.warning(
                        "Flow explicitly rejected scene %d (%s). Retrying once with a safe prompt.",
                        scene.get("index", 0),
                        first_error,
                    )
                    fallback_prompt = (
                        safe_prompt
                        if perceptual_duplicate_retries == 0
                        else _build_perceptual_retry_prompt(
                            safe_prompt,
                            scene,
                            perceptual_duplicate_retries,
                        )
                    )
                    _log_scene_generation(
                        video_id=video_id,
                        scene_index=int(scene.get("index", 0)),
                        media_type="image",
                        state="retry_explicit_error",
                        attempt=perceptual_duplicate_retries + 1,
                        started_at=started_at,
                        detail=str(first_error),
                    )
                    asset_url = await worker.generate_scene(
                        fallback_prompt,
                        negative_prompt,
                        refs,
                        reference_paths,
                        scene_index=int(scene.get("index", 0)),
                        scene_count=scene_count,
                    )

                await worker.download_image(asset_url, str(target))
                if not target.exists() or target.stat().st_size == 0:
                    raise RuntimeError(
                        f"Ảnh tạo từ Google Flow không hợp lệ hoặc rỗng: {target}"
                    )

                from PIL import Image

                with Image.open(target) as image_check:
                    image_width, image_height = image_check.size
                    if (
                        image_width < 800
                        or image_height < 400
                        or image_width / max(1, image_height) < 1.15
                    ):
                        raise RuntimeError(
                            "Ảnh tải về từ Google Flow không đạt chuẩn 16:9 widescreen "
                            f"({image_width}x{image_height})."
                        )

                target_hash = _sha256_file(target)
                target_fingerprint = _image_perceptual_fingerprint(target)
                is_exact_duplicate = bool(
                    existing_hashes is not None and target_hash in existing_hashes
                )
                is_visual_duplicate = _matches_existing_perceptual_fingerprint(
                    target_fingerprint,
                    existing_perceptual_hashes,
                )
                if not is_exact_duplicate and not is_visual_duplicate:
                    break
                if perceptual_duplicate_retries >= MAX_PERCEPTUAL_DUPLICATE_RETRIES:
                    raise SceneVisualDuplicateError(
                        f"Ảnh cảnh {scene.get('index', 0) + 1} vẫn giống cảnh trước "
                        f"sau {MAX_PERCEPTUAL_DUPLICATE_RETRIES} lần tạo lại."
                    )

                perceptual_duplicate_retries += 1
                target.unlink(missing_ok=True)
                logger.warning(
                    "Generated scene %s duplicates an earlier scene (%s); retrying with a different composition (%s/%s).",
                    scene.get("index", 0),
                    "exact" if is_exact_duplicate else "perceptual",
                    perceptual_duplicate_retries,
                    MAX_PERCEPTUAL_DUPLICATE_RETRIES,
                )
                progress(
                    f"Ảnh cảnh {scene['index'] + 1} giống cảnh trước; đang đổi bố cục và tạo lại",
                    "retrying_visual_duplicate",
                )

            if hasattr(worker, "register_generated_video_frame"):
                registered_frame = worker.register_generated_video_frame(
                    target,
                    asset_url,
                )
                if isinstance(registered_frame, dict):
                    flow_frame_metadata = registered_frame

        target_hash = _sha256_file(target)
        target_fingerprint = _image_perceptual_fingerprint(target)
        if existing_hashes is not None and target_hash in existing_hashes:
            raise SceneVisualDuplicateError(
                "Tạo ảnh trùng hệt cảnh trước; dừng để tránh video lặp ảnh."
            )
        if _matches_existing_perceptual_fingerprint(
            target_fingerprint,
            existing_perceptual_hashes,
        ):
            raise SceneVisualDuplicateError(
                "Tạo ảnh quá giống cảnh trước; dừng để tránh video lặp ảnh."
            )
    except Exception as exc:
        failure_stage_by_error = {
            "FlowAgentInteractionError": "agent_interaction",
            "FlowSubmissionError": "submit",
            "FlowGenerationStartError": "generation_start",
            "FlowGenerationTimeout": "generation_timeout",
            "FlowAgentStalledError": "generation_timeout",
            "FlowResultMissingError": "result_missing",
            "FlowInvalidOutputError": "invalid_output",
            "ReferenceAttachmentError": "reference",
            "FlowUiStateError": "ui_state",
            "SceneVisualDuplicateError": "visual_duplicate",
        }
        failure_stage = failure_stage_by_error.get(
            type(exc).__name__,
            "image_generation",
        )
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        db.upsert_video_artifact(
            video_id=video_id,
            artifact_type=artifact_type,
            path=str(target),
            content_hash=content_hash,
            status="failed",
            mime_type="image/png",
            metadata={
                **scene,
                "error": str(exc),
                "failure_stage": failure_stage,
                "generation_attempt": generation_attempt,
                "perceptual_duplicate_retries": perceptual_duplicate_retries,
                "reference_id": reference_id,
                "reference_hash": reference_hash,
            },
        )
        _log_scene_generation(
            video_id=video_id,
            scene_index=int(scene.get("index", 0)),
            media_type="image",
            state="failed",
            attempt=generation_attempt,
            started_at=started_at,
            detail=str(exc),
        )
        raise

    progress(f"Đã tải ảnh {scene['index'] + 1}/{scene_count}", "downloading_flow_image")
    db.upsert_video_artifact(
        video_id=video_id,
        artifact_type=artifact_type,
        path=str(target),
        content_hash=content_hash,
        status="completed",
        mime_type="image/png",
        metadata={
            **scene,
            "generation_attempt": generation_attempt,
            "perceptual_duplicate_retries": perceptual_duplicate_retries,
            "reference_id": reference_id,
            **flow_frame_metadata,
        },
    )
    _log_scene_generation(
        video_id=video_id,
        scene_index=int(scene.get("index", 0)),
        media_type="image",
        state="completed",
        attempt=generation_attempt,
        started_at=started_at,
    )
    return target


def _generate_scene_image(
    *,
    video_id: int,
    scene: dict,
    scene_count: int,
    profile: dict,
    settings: dict,
    reference_path: Path | None,
    reference_id: str,
    progress,
    cancel_check,
    existing_hashes: set[str] | None = None,
    existing_perceptual_hashes: list[ImagePerceptualFingerprint] | None = None,
    force_new_project: bool = False,
) -> Path:
    async def _run() -> Path:
        async with _flow_worker_session(
            video_id,
            force_new_project=force_new_project,
        ) as worker:
            return await _generate_scene_image_async(
                video_id=video_id,
                scene=scene,
                scene_count=scene_count,
                profile=profile,
                settings=settings,
                reference_path=reference_path,
                reference_id=reference_id,
                progress=progress,
                cancel_check=cancel_check,
                worker=worker,
                existing_hashes=existing_hashes,
                existing_perceptual_hashes=existing_perceptual_hashes,
                force_new_project=force_new_project,
            )

    return asyncio.run(_run())


async def _generate_scene_images_with_worker(
    *,
    video_id: int,
    scenes: list[dict],
    profile: dict | None = None,
    reference_profile: dict | None = None,
    settings: dict | None = None,
    progress,
    cancel_check,
    worker,
    force_new_project: bool = False,
) -> list[Path]:
    settings = settings or {}
    references: dict[str, Path] = {}
    image_hashes: set[str] = set()
    image_perceptual_hashes: list[ImagePerceptualFingerprint] = []
    completed_paths: list[Path] = []
    need_force_new = force_new_project
    for scene in scenes:
        cancel_check()
        reference_id = str(scene.get("primary_reference_id") or "").strip()
        scene_ref_path = scene.get("reference_path")
        if scene_ref_path and Path(scene_ref_path).is_file():
            reference_path = Path(scene_ref_path)
        else:
            reference_path = references.get(reference_id) if reference_id else None
        image_path = await _generate_scene_image_async(
            video_id=video_id,
            scene=scene,
            scene_count=len(scenes),
            profile=profile or {},
            settings=settings,
            reference_path=reference_path,
            reference_id=reference_id,
            progress=progress,
            cancel_check=cancel_check,
            worker=worker,
            existing_hashes=image_hashes,
            existing_perceptual_hashes=image_perceptual_hashes,
            force_new_project=need_force_new,
        )
        need_force_new = False
        image_hash = _sha256_file(image_path)
        if image_hash in image_hashes:
            raise SceneVisualDuplicateError(
                "Tạo ảnh trùng hệt cảnh trước; dừng để tránh video lặp ảnh."
            )
        image_fingerprint = _image_perceptual_fingerprint(image_path)
        if _matches_existing_perceptual_fingerprint(
            image_fingerprint,
            image_perceptual_hashes,
        ):
            raise SceneVisualDuplicateError(
                "Tạo ảnh quá giống cảnh trước; dừng để tránh video lặp ảnh."
            )
        image_hashes.add(image_hash)
        image_perceptual_hashes.append(image_fingerprint)
        completed_paths.append(image_path)
        if reference_id and reference_id not in references:
            references[reference_id] = image_path
    return completed_paths


def generate_scene_images(
    *,
    video_id: int,
    scenes: list[dict],
    profile: dict | None = None,
    reference_profile: dict | None = None,
    settings: dict | None = None,
    progress,
    cancel_check,
    force_new_project: bool = False,
) -> list[Path]:
    if force_new_project:
        purge_all_scene_artifacts(video_id)
    cleanup_duplicate_scene_artifacts(video_id)

    async def _run() -> list[Path]:
        async with _flow_worker_session(
            video_id,
            force_new_project=force_new_project,
        ) as worker:
            return await _generate_scene_images_with_worker(
                video_id=video_id,
                scenes=scenes,
                profile=profile,
                reference_profile=reference_profile,
                settings=settings,
                progress=progress,
                cancel_check=cancel_check,
                worker=worker,
                force_new_project=force_new_project,
            )

    return asyncio.run(_run())


async def _generate_scene_video_async(
    *,
    video_id: int,
    scene: dict,
    scene_count: int,
    start_frame_path: Path,
    end_frame_path: Path | None = None,
    profile: dict | None = None,
    settings: dict | None = None,
    progress,
    cancel_check,
    worker,
    force_new_project: bool = False,
) -> Path:
    settings = settings or {}
    video_neg = str(settings.get("video_negative_prompt") or "").strip()
    negative_prompt = video_neg if video_neg else str(settings.get("avoid_prompt") or settings.get("negative_prompt") or "")
    fixed_seed = int(_sha256_bytes(str(video_id).encode("utf-8"))[:15], 16)
    cancel_check()
    start_hash = _sha256_file(start_frame_path) if start_frame_path and start_frame_path.is_file() else ""
    end_hash = _sha256_file(end_frame_path) if end_frame_path and end_frame_path.is_file() else ""
    
    content_hash = _scene_hash(
        video_id, scene, profile, negative_prompt, f"{start_hash}:{end_hash}"
    )
    artifact_type = f"scene_video:{scene['index']}"
    existing = db.get_latest_video_artifact(video_id, artifact_type)

    if (
        not force_new_project
        and existing
        and existing.get("content_hash") == content_hash
        and existing.get("status") == "completed"
    ):
        artifact_path = Path(existing["path"])
        if artifact_path.exists() and artifact_path.stat().st_size > 1000:
            return artifact_path
    elif existing and existing.get("content_hash") != content_hash:
        logger.info(
            "Ignoring stale scene video cache for video=%s scene=%s: expected=%s actual=%s",
            video_id,
            scene.get("index"),
            content_hash,
            existing.get("content_hash"),
        )

    target = SCENES_DIR / f"{video_id}_{scene['index']}_video_{content_hash[:8]}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    generation_attempt = existing.get("metadata", {}).get("generation_attempt", 0) + 1 if existing else 1
    
    started_at = time.monotonic()
    progress(
        f"Đang tạo video intro {scene['index'] + 1}/{scene_count} bằng Google Flow Veo",
        f"generating_flow_video {scene['index'] + 1}/{scene_count}",
    )
    
    db.upsert_video_artifact(
        video_id=video_id,
        artifact_type=artifact_type,
        path=str(target),
        content_hash=content_hash,
        status="processing",
        mime_type="video/mp4",
        metadata={
            **scene,
            "seed": fixed_seed,
            "generation_attempt": generation_attempt,
            "start_hash": start_hash,
            "end_hash": end_hash,
            "start_frame": start_frame_path.name if start_frame_path else "",
            "end_frame": end_frame_path.name if end_frame_path else "",
        },
    )

    flow_timing: dict[str, object] = {}
    try:
        from auto_yt.services.google_flow_worker import FlowFrameAttachmentError

        if not start_frame_path or not start_frame_path.is_file():
            raise FlowFrameAttachmentError(
                f"Thiếu start frame cho video scene {scene['index']}."
            )
        if not end_frame_path or not end_frame_path.is_file():
            raise FlowFrameAttachmentError(
                f"Thiếu end frame cho video scene {scene['index']}; cần đủ cặp ảnh gối đầu."
            )
        _log_scene_generation(
            video_id=video_id,
            scene_index=int(scene.get("index", 0)),
            media_type="video",
            state="submitted",
            attempt=generation_attempt,
            started_at=started_at,
        )
        if _flow_mock_enabled():
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            mock_duration = float(scene.get("duration", 8.0))
            subprocess.run(
                [
                    ffmpeg_exe, "-hide_banner", "-y",
                    "-loop", "1", "-i", str(start_frame_path),
                    "-t", f"{mock_duration:.3f}",
                    "-vf", f"scale={TARGET_WIDTH}:{TARGET_HEIGHT},fps={TARGET_FPS}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(target),
                ],
                capture_output=True,
                check=True,
            )
        else:
            if worker is None:
                raise VideoProductionError("Google Flow worker chưa được khởi tạo.")
            video_rec = db.get_video(video_id) or {}
            video_title = str(video_rec.get("title") or video_rec.get("generated_title") or "")
            scene_action = str(scene.get("action") or scene.get("transcript") or "")
            style_str = str(settings.get("style_prompt") or (profile or {}).get("style_prompt") or "")
            video_prompt = _format_scene_video_prompt(
                scene.get("prompt", ""),
                scene_action=scene_action,
                style_prompt=style_str,
                video_style_prompt=str(settings.get("video_style_prompt") or ""),
                video_prompt_template=str(settings.get("video_prompt_template") or ""),
                video_motion_prompt=str(settings.get("video_motion_prompt") or ""),
                video_title=video_title,
                has_start_frame=bool(start_frame_path and start_frame_path.is_file()),
                has_end_frame=bool(end_frame_path and end_frame_path.is_file()),
            )
            ref_ids = []
            if scene.get("primary_reference_id"):
                ref_ids.append(str(scene["primary_reference_id"]))

            video_url = await worker.generate_scene_video(
                prompt=video_prompt,
                avoid_prompt=negative_prompt,
                start_frame_path=start_frame_path,
                end_frame_path=end_frame_path,
                reference_ids=ref_ids,
                video_settings={
                    "video_model": str(settings.get("video_model") or "veo_3_1_lite"),
                    "video_aspect_ratio": str(settings.get("video_aspect_ratio") or "16:9"),
                    "video_output_count": int(settings.get("video_output_count") or 1),
                },
                scene_index=int(scene.get("index", 0)),
            )
            await worker.download_video(video_url, str(target))
            timing_snapshot = worker.get_last_video_timing()
            flow_timing = timing_snapshot if isinstance(timing_snapshot, dict) else {}

        if not target.exists() or target.stat().st_size < 1000:
            raise RuntimeError(f"Video tạo từ Google Flow Veo không hợp lệ hoặc quá nhỏ: {target}")
    except Exception as exc:
        if worker is not None and hasattr(worker, "get_last_video_timing"):
            timing_snapshot = worker.get_last_video_timing()
            flow_timing = timing_snapshot if isinstance(timing_snapshot, dict) else {}
        failure_stage_by_error = {
            "FlowAgentInteractionError": "agent_interaction",
            "FlowAgentSettingsError": "agent_settings",
            "FlowFrameAttachmentError": "frame_sync",
            "FlowModeError": "legacy_video_mode",
            "FlowSubmissionError": "submit",
            "FlowGenerationStartError": "generation_start",
            "FlowGenerationTimeout": "generation_timeout",
            "FlowAgentStalledError": "generation_timeout",
            "FlowResultMissingError": "result_missing",
            "FlowUiStateError": "ui_state",
        }
        failure_stage = failure_stage_by_error.get(type(exc).__name__, "video_generation")
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        db.upsert_video_artifact(
            video_id=video_id,
            artifact_type=artifact_type,
            path=str(target),
            content_hash=content_hash,
            status="failed",
            mime_type="video/mp4",
            metadata={
                **scene,
                "generation_attempt": generation_attempt,
                "start_hash": start_hash,
                "end_hash": end_hash,
                "start_frame": start_frame_path.name if start_frame_path else "",
                "end_frame": end_frame_path.name if end_frame_path else "",
                "error": str(exc),
                "failure_stage": failure_stage,
                "fallback_reason": str(exc),
                "flow_timing": flow_timing,
            },
        )
        _log_scene_generation(
            video_id=video_id,
            scene_index=int(scene.get("index", 0)),
            media_type="video",
            state="video_checkpoint_failed",
            attempt=generation_attempt,
            started_at=started_at,
            detail=f"{failure_stage}:{exc}",
        )
        raise

    progress(f"Đã tải video {scene['index'] + 1}/{scene_count}", "downloading_flow_video")
    db.upsert_video_artifact(
        video_id=video_id,
        artifact_type=artifact_type,
        path=str(target),
        content_hash=content_hash,
        status="completed",
        mime_type="video/mp4",
        metadata={
            **scene,
            "generation_attempt": generation_attempt,
            "start_hash": start_hash,
            "end_hash": end_hash,
            "start_frame": start_frame_path.name if start_frame_path else "",
            "end_frame": end_frame_path.name if end_frame_path else "",
            "flow_timing": flow_timing,
        },
    )
    _log_scene_generation(
        video_id=video_id,
        scene_index=int(scene.get("index", 0)),
        media_type="video",
        state="completed",
        attempt=generation_attempt,
        started_at=started_at,
    )
    return target


def _generate_scene_video(
    *,
    video_id: int,
    scene: dict,
    scene_count: int,
    start_frame_path: Path,
    end_frame_path: Path | None = None,
    profile: dict | None = None,
    settings: dict | None = None,
    progress,
    cancel_check,
    force_new_project: bool = False,
) -> Path:
    async def _run() -> Path:
        async with _flow_worker_session(
            video_id,
            force_new_project=force_new_project,
        ) as worker:
            return await _generate_scene_video_async(
                video_id=video_id,
                scene=scene,
                scene_count=scene_count,
                start_frame_path=start_frame_path,
                end_frame_path=end_frame_path,
                profile=profile,
                settings=settings,
                progress=progress,
                cancel_check=cancel_check,
                worker=worker,
                force_new_project=force_new_project,
            )

    return asyncio.run(_run())


def generate_scene_media(
    *,
    video_id: int,
    scenes: list[dict],
    profile: dict | None = None,
    reference_profile: dict | None = None,
    settings: dict | None = None,
    progress,
    cancel_check,
    force_new_project: bool = False,
) -> list[Path]:
    """Generate media assets for all scenes.
    
    1. Phase 1: Generates base images for all scenes (Image 0..N).
    2. Phase 2: For intro scenes (is_video=True), animates Image[i] -> Image[i+1] into video clips.
       Any video failure stops at that scene so a later run can resume from the checkpoint.
    """
    settings = settings or {}
    enable_intro_video = bool(settings.get("enable_intro_video", True))
    has_video_scenes = enable_intro_video and any(
        bool(scene.get("is_video") or scene.get("media_type") == "video")
        for scene in scenes
    )

    # Keep the public image-only facade intact while using one shared session for
    # jobs that include both image and Veo phases.
    if not has_video_scenes:
        return generate_scene_images(
            video_id=video_id,
            scenes=scenes,
            profile=profile,
            reference_profile=reference_profile,
            settings=settings,
            progress=progress,
            cancel_check=cancel_check,
            force_new_project=force_new_project,
        )

    if force_new_project:
        purge_all_scene_artifacts(video_id)
    cleanup_duplicate_scene_artifacts(video_id)

    async def _run_batch() -> list[Path]:
        async with _flow_worker_session(
            video_id,
            force_new_project=force_new_project,
        ) as worker:
            base_image_paths = await _generate_scene_images_with_worker(
                video_id=video_id,
                scenes=scenes,
                profile=profile,
                reference_profile=reference_profile,
                settings=settings,
                progress=progress,
                cancel_check=cancel_check,
                worker=worker,
                force_new_project=force_new_project,
            )

            media_paths: list[Path] = []
            flow_video_unsupported = False
            for index, scene in enumerate(scenes):
                cancel_check()
                is_video = bool(
                    enable_intro_video
                    and not flow_video_unsupported
                    and (scene.get("is_video") or scene.get("media_type") == "video")
                )
                if not is_video:
                    media_paths.append(base_image_paths[index])
                    continue

                start_frame = base_image_paths[index]
                end_frame = (
                    base_image_paths[index + 1]
                    if index + 1 < len(base_image_paths)
                    else None
                )
                try:
                    video_path = await _generate_scene_video_async(
                        video_id=video_id,
                        scene=scene,
                        scene_count=len(scenes),
                        start_frame_path=start_frame,
                        end_frame_path=end_frame,
                        profile=profile or {},
                        settings=settings,
                        progress=progress,
                        cancel_check=cancel_check,
                        worker=worker,
                        force_new_project=False,
                    )
                    media_paths.append(video_path)
                except Exception as exc:
                    from auto_yt.services.google_flow_worker import (
                        FlowModeError,
                        FlowAgentSettingsError,
                    )
                    is_system_unsupported = isinstance(
                        exc, (FlowModeError, FlowAgentSettingsError)
                    ) or "không hỗ trợ" in str(exc).lower()

                    if is_system_unsupported:
                        logger.warning(
                            "flow_video is completely unsupported by account/interface (%s); fast-falling back remaining scenes to base image.",
                            exc,
                        )
                        flow_video_unsupported = True
                    else:
                        logger.warning(
                            "flow_video generation failed for scene %s (%s); falling back this scene to base image while continuing remaining scenes.",
                            scene.get("index"),
                            exc,
                        )
                    media_paths.append(base_image_paths[index])
            return media_paths

    return asyncio.run(_run_batch())


def _format_srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def create_srt(audio_path: Path, video_id: int, progress) -> tuple[Path, str]:
    global _WHISPER_DEVICE, _WHISPER_MODEL
    audio_hash = _sha256_file(audio_path)
    existing = db.get_latest_video_artifact(video_id, "captions")
    if (
        existing
        and existing.get("content_hash") == audio_hash
        and existing.get("status") == "ready"
        and Path(existing.get("path") or "").is_file()
    ):
        return Path(existing["path"]), audio_hash
    progress("Đang tạo phụ đề bằng Whisper local", "captions")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VideoProductionError(
            "Thiếu faster-whisper; hãy cài dependencies trước khi dựng video."
        ) from exc

    import time

    def transcribe(model, device_name="CPU"):
        segments, info = model.transcribe(
            str(audio_path), language="vi", vad_filter=True
        )
        results = []
        total_duration = getattr(info, "duration", None) or 0
        last_progress_time = 0.0
        for seg in segments:
            results.append(seg)
            now = time.monotonic()
            if now - last_progress_time >= 5.0:
                last_progress_time = now
                if total_duration > 0:
                    pct = min(99, int((seg.end / total_duration) * 100))
                    progress(
                        f"Đang phiên âm Whisper {device_name}: {pct}% ({int(seg.end)}s/{int(total_duration)}s)",
                        "captions",
                    )
                else:
                    progress(
                        f"Đang phiên âm Whisper {device_name} ({int(seg.end)}s)",
                        "captions",
                    )
        return results

    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            try:
                _WHISPER_MODEL = WhisperModel("small", device="cuda", compute_type="float16")
                _WHISPER_DEVICE = "cuda"
            except Exception:
                try:
                    _WHISPER_MODEL = WhisperModel(
                        "small", device="cpu", compute_type="int8"
                    )
                    _WHISPER_DEVICE = "cpu"
                except Exception as exc:
                    raise VideoProductionError(
                        "Không khởi tạo được Whisper trên GPU hoặc CPU."
                    ) from exc
        try:
            segments = transcribe(_WHISPER_MODEL, _WHISPER_DEVICE.upper())
        except Exception as exc:
            if _WHISPER_DEVICE != "cuda":
                raise VideoProductionError("Whisper CPU tạo phụ đề thất bại.") from exc
            progress(
                "Whisper GPU không khả dụng; đang chuyển sang CPU int8",
                "captions",
            )
            try:
                _WHISPER_MODEL = WhisperModel(
                    "small", device="cpu", compute_type="int8"
                )
                _WHISPER_DEVICE = "cpu"
                segments = transcribe(_WHISPER_MODEL, "CPU")
            except Exception as cpu_exc:
                raise VideoProductionError(
                    "Whisper tạo phụ đề thất bại trên cả GPU và CPU."
                ) from cpu_exc
        rendered = []
        for index, segment in enumerate(segments, start=1):
            text = re.sub(r"\s+", " ", str(segment.text or "")).strip()
            if not text:
                continue
            rendered.append(
                f"{index}\n{_format_srt_time(segment.start)} --> "
                f"{_format_srt_time(segment.end)}\n{text}\n"
            )
    if not rendered:
        raise VideoProductionError("Whisper không tạo được phụ đề có nội dung.")
    target = CAPTIONS_DIR / f"video_{video_id}_{audio_hash[:16]}.srt"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".srt.tmp")
    temporary.write_text("\n".join(rendered), encoding="utf-8")
    temporary.replace(target)
    db.upsert_video_artifact(
        video_id=video_id,
        artifact_type="captions",
        path=str(target),
        content_hash=audio_hash,
        status="ready",
        mime_type="application/x-subrip",
        size_bytes=target.stat().st_size,
        metadata={"language": "vi", "model": "small"},
    )
    return target, audio_hash


def _select_encoder(ffmpeg_exe: str) -> str:
    global _ENCODER
    with _ENCODER_LOCK:
        if _ENCODER:
            return _ENCODER
        command = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=128x72:rate=1:duration=1",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "NUL" if shutil.which("cmd") else "/dev/null",
        ]
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        _ENCODER = "h264_nvenc" if result.returncode == 0 else "libx264"
        return _ENCODER


def _disable_hardware_encoder() -> None:
    global _ENCODER
    with _ENCODER_LOCK:
        _ENCODER = "libx264"


def _escape_subtitle_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'")
    return value


def _find_ffprobe(ffmpeg_exe: str) -> str | None:
    sibling = Path(ffmpeg_exe).with_name(
        "ffprobe.exe" if Path(ffmpeg_exe).suffix.casefold() == ".exe" else "ffprobe"
    )
    if sibling.is_file():
        return str(sibling)
    located = shutil.which("ffprobe")
    if located:
        return located
    return None


def _probe_media_with_av(path: Path) -> dict:
    try:
        import av
    except ImportError as exc:
        raise VideoProductionError(
            "Không tìm thấy ffprobe hoặc PyAV để kiểm tra media."
        ) from exc
    try:
        with av.open(str(path)) as container:
            streams = list(container.streams)
            duration = (
                float(container.duration / av.time_base)
                if container.duration is not None
                else 0.0
            )
            if duration <= 0:
                stream_durations = [
                    float(stream.duration * stream.time_base)
                    for stream in streams
                    if stream.duration is not None and stream.time_base is not None
                ]
                duration = max(stream_durations, default=0.0)
            return {
                "duration_seconds": duration,
                "streams": [
                    {
                        "codec_type": stream.type,
                        "codec_name": stream.codec_context.name,
                        "width": getattr(stream.codec_context, "width", 0),
                        "height": getattr(stream.codec_context, "height", 0),
                    }
                    for stream in streams
                ],
            }
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise VideoProductionError("PyAV không đọc được media vừa tạo.") from exc


def probe_media_duration(path: Path, ffmpeg_exe: str | None = None) -> float:
    ffprobe = _find_ffprobe(ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe())
    if ffprobe is None:
        duration = float(_probe_media_with_av(path)["duration_seconds"] or 0)
        if duration <= 0:
            raise VideoProductionError("Audio không có thời lượng hợp lệ để dựng video.")
        return duration
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise VideoProductionError("ffprobe không đọc được thời lượng audio.")
    try:
        duration = float((json.loads(result.stdout or "{}").get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoProductionError("ffprobe trả về thời lượng audio không hợp lệ.") from exc
    if duration <= 0:
        raise VideoProductionError("Audio không có thời lượng hợp lệ để dựng video.")
    return duration


def validate_render(path: Path, expected_duration: float, ffmpeg_exe: str) -> dict:
    ffprobe = _find_ffprobe(ffmpeg_exe)
    if ffprobe is None:
        probe = _probe_media_with_av(path)
        duration = float(probe["duration_seconds"] or 0)
        streams = probe["streams"]
    else:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise VideoProductionError("ffprobe không đọc được MP4 vừa render.")
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
        duration = float((payload.get("format") or {}).get("duration") or 0)
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video_stream or not audio_stream:
        raise VideoProductionError("MP4 thiếu stream hình hoặc tiếng.")
    if int(video_stream.get("width") or 0) != TARGET_WIDTH or int(
        video_stream.get("height") or 0
    ) != TARGET_HEIGHT:
        raise VideoProductionError("MP4 không đúng độ phân giải 1920x1080.")
    if duration <= 0 or abs(duration - expected_duration) > max(2.0, expected_duration * 0.01):
        raise VideoProductionError("Thời lượng MP4 lệch quá giới hạn cho phép.")
    return {
        "duration_seconds": duration,
        "video_codec": video_stream.get("codec_name") or "",
        "audio_codec": audio_stream.get("codec_name") or "",
    }


def extract_ffmpeg_error(stderr: str) -> str:
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    markers = (
        "cannot allocate memory",
        "out of memory",
        "no space left",
        "invalid argument",
        "error while",
        "conversion failed",
        "failed",
        "error",
    )
    causes = [line for line in lines if any(marker in line.casefold() for marker in markers)]
    selected = causes[-6:] if causes else lines[-6:]
    return "\n".join(selected)[-1600:] or "FFmpeg không cung cấp chi tiết lỗi."


def _write_segment_srt(
    source_segments: list[dict], scene: dict, target: Path
) -> bool:
    scene_start = float(scene["start"])
    scene_end = float(scene["end"])
    rendered = []
    for caption in source_segments:
        start = max(scene_start, float(caption["start"]))
        end = min(scene_end, float(caption["end"]))
        if end <= start:
            continue
        rendered.append(
            f"{len(rendered) + 1}\n{_format_srt_time(start - scene_start)} --> "
            f"{_format_srt_time(end - scene_start)}\n{caption['text']}\n"
        )
    if not rendered:
        target.unlink(missing_ok=True)
        return False
    target.write_text("\n".join(rendered), encoding="utf-8")
    return True


def _segment_filter(
    *,
    scene: dict,
    scene_index: int,
    subtitle_path: Path | None,
    has_previous_image: bool = False,
) -> tuple[str, str]:
    duration = float(scene["duration"])
    frames = max(1, round(duration * TARGET_FPS))
    fade_duration = min(0.35, max(0.1, duration / 4))
    fade_out_start = max(0.0, duration - fade_duration)

    # 5-way dynamic camera motion engine (distinct zoom and panning)
    mode = scene_index % 5
    zoom_in_factor = 0.18
    zoom_out_factor = 0.18

    if mode == 0:
        # Mode 0: Zoom In Center
        z_expr = f"1.0+{zoom_in_factor}*(on/{frames})"
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"
    elif mode == 1:
        # Mode 1: Pan Left -> Right with gentle Zoom In
        z_expr = f"1.0+{zoom_in_factor * 0.8:.3f}*(on/{frames})"
        x_expr = f"(iw-iw/zoom)*(on/{frames})"
        y_expr = "(ih-ih/zoom)/2"
    elif mode == 2:
        # Mode 2: Zoom Out Center (reveal to wide shot)
        z_expr = f"{1.0 + zoom_out_factor:.2f}-{zoom_out_factor}*(on/{frames})"
        x_expr = "(iw-iw/zoom)/2"
        y_expr = "(ih-ih/zoom)/2"
    elif mode == 3:
        # Mode 3: Pan Right -> Left with gentle Zoom In
        z_expr = f"1.0+{zoom_in_factor * 0.8:.3f}*(on/{frames})"
        x_expr = f"(iw-iw/zoom)*(1.0-on/{frames})"
        y_expr = "(ih-ih/zoom)/2"
    else:
        # Mode 4: Diagonal Pan (Bottom-Left to Top-Right)
        z_expr = f"1.0+{zoom_in_factor * 0.8:.3f}*(on/{frames})"
        x_expr = f"(iw-iw/zoom)*(on/{frames})"
        y_expr = f"(ih-ih/zoom)*(on/{frames})"

    filters = [
        f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:"
        f"s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps={TARGET_FPS},"
        f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
        f"fade=t=in:st=0:d={fade_duration:.3f},"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}[current]"
    ]
    output_label = "current"
    if subtitle_path is not None:
        filters.append(
            f"[{output_label}]subtitles=filename='{_escape_subtitle_path(subtitle_path)}'[out]"
        )
        output_label = "out"
    return ";".join(filters), output_label


def _video_segment_filter(
    *,
    scene: dict,
    subtitle_path: Path | None,
    crop_watermark: bool = True,
) -> tuple[str, str]:
    duration = float(scene["duration"])
    fade_duration = min(0.35, max(0.1, duration / 4))
    fade_out_start = max(0.0, duration - fade_duration)

    filters = []
    if crop_watermark:
        # Smart Edge Crop: Crop 5% from bottom-right where Veo watermark appears, then scale back with Lanczos
        filters.append(
            f"[0:v]crop=iw*0.95:ih*0.95:0:0,"
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},"
            f"fps={TARGET_FPS},"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"fade=t=in:st=0:d={fade_duration:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}[v_clean]"
        )
    else:
        filters.append(
            f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},"
            f"fps={TARGET_FPS},"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"fade=t=in:st=0:d={fade_duration:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}[v_clean]"
        )
    output_label = "v_clean"
    if subtitle_path is not None:
        filters.append(
            f"[{output_label}]subtitles=filename='{_escape_subtitle_path(subtitle_path)}'[out]"
        )
        output_label = "out"
    return ";".join(filters), output_label


def _render_segments(
    *,
    ffmpeg_exe: str,
    encoder: str,
    video_id: int,
    scenes: list[dict],
    media_paths: list[Path] | None = None,
    image_paths: list[Path] | None = None,
    srt_path: Path,
    input_hash: str,
    progress,
    cancel_check,
    crop_watermark: bool = True,
) -> list[Path]:
    paths = media_paths if media_paths is not None else (image_paths or [])
    segment_root = SEGMENTS_DIR / str(video_id) / input_hash[:16]
    segment_root.mkdir(parents=True, exist_ok=True)
    captions = parse_srt_segments(srt_path)
    results: list[Path] = []
    for index, (scene, media_path) in enumerate(zip(scenes, paths)):
        cancel_check()
        segment_path = segment_root / f"segment_{index:04d}_{encoder}.mp4"
        if segment_path.is_file() and segment_path.stat().st_size > 0:
            results.append(segment_path)
            continue
        subtitle_path = segment_root / f"segment_{index:04d}.srt"
        has_subtitles = _write_segment_srt(captions, scene, subtitle_path)
        
        is_video_input = media_path.suffix.lower() == ".mp4"
        command = [ffmpeg_exe, "-hide_banner", "-y"]
        
        if is_video_input:
            # Video input: stream loop to match exact scene duration
            command.extend(["-stream_loop", "-1", "-i", str(media_path)])
            filter_graph, output_label = _video_segment_filter(
                scene=scene,
                subtitle_path=subtitle_path if has_subtitles else None,
                crop_watermark=crop_watermark,
            )
        else:
            # Image input: loop image with zoompan
            command.extend(["-loop", "1", "-i", str(media_path)])
            filter_graph, output_label = _segment_filter(
                scene=scene,
                scene_index=index,
                subtitle_path=subtitle_path if has_subtitles else None,
                has_previous_image=False,
            )
            
        temporary = segment_path.with_suffix(".mp4.tmp")
        command.extend(
            [
                "-filter_complex",
                filter_graph,
                "-map",
                f"[{output_label}]",
                "-t",
                f"{float(scene['duration']):.3f}",
                "-an",
                "-c:v",
                encoder,
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(TARGET_FPS),
                "-f",
                "mp4",
                str(temporary),
            ]
        )
        progress(
            f"Đang dựng segment {index + 1}/{len(scenes)}" + (" (Video Intro)" if is_video_input else ""),
            "video_render_segment",
        )
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process_registry.register_process(f'video:{video_id}', proc)
        try:
            stdout, stderr = proc.communicate()
            result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
        finally:
            process_registry.unregister_process(f'video:{video_id}', proc)
        if result.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise VideoProductionError(
                f"FFmpeg render segment {index + 1} thất bại: "
                + extract_ffmpeg_error(result.stderr)
            )
        temporary.replace(segment_path)
        results.append(segment_path)
    return results


def _concat_segments(
    *,
    ffmpeg_exe: str,
    segments: list[Path],
    audio_path: Path,
    temporary: Path,
    progress,
) -> None:
    concat_path = temporary.with_suffix(".concat.txt")
    video_only = temporary.with_suffix(".video.mp4")
    concat_path.write_text(
        "\n".join(
            "file '" + str(path.resolve()).replace("'", "'\\''") + "'"
            for path in segments
        ),
        encoding="utf-8",
    )
    progress("Đang ghép các segment MP4", "video_concat")
    concat_result = subprocess.run(
        [
            ffmpeg_exe,
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(video_only),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if concat_result.returncode != 0:
        raise VideoProductionError(
            "FFmpeg ghép segment thất bại: " + extract_ffmpeg_error(concat_result.stderr)
        )
    mux_result = subprocess.run(
        [
            ffmpeg_exe,
            "-hide_banner",
            "-y",
            "-i",
            str(video_only),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            "-f",
            "mp4",
            str(temporary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    concat_path.unlink(missing_ok=True)
    video_only.unlink(missing_ok=True)
    if mux_result.returncode != 0:
        raise VideoProductionError(
            "FFmpeg mux audio thất bại: " + extract_ffmpeg_error(mux_result.stderr)
        )


def render_video(
    *,
    video_id: int,
    audio_path: Path,
    srt_path: Path,
    scenes: list[dict],
    media_paths: list[Path] | None = None,
    image_paths: list[Path] | None = None,
    input_hash: str,
    progress,
    cancel_check=lambda: None,
    crop_watermark: bool = True,
) -> dict:
    paths = media_paths if media_paths is not None else (image_paths or [])
    existing = db.get_latest_video_artifact(video_id, "final_mp4")
    if (
        existing
        and existing.get("content_hash") == input_hash
        and existing.get("status") == "ready"
        and Path(existing.get("path") or "").is_file()
    ):
        return existing
    if len(scenes) != len(paths) or not scenes:
        raise VideoProductionError("Scene plan và media không đồng bộ.")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    encoder = _select_encoder(ffmpeg_exe)
    video = db.get_video(video_id)
    generated_script = video.get("generated_script", "") if video else ""
    slug = db.extract_generated_video_slug(
        generated_script,
        default_title=video.get("title", "") if video else "",
    )
    resolved_name = db.resolve_render_filename(video_id, slug, renders_dir=RENDERS_DIR)
    target = RENDERS_DIR / f"{resolved_name}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".mp4.tmp")
    try:
        segments = _render_segments(
            ffmpeg_exe=ffmpeg_exe,
            encoder=encoder,
            video_id=video_id,
            scenes=scenes,
            media_paths=paths,
            srt_path=srt_path,
            input_hash=input_hash,
            progress=progress,
            cancel_check=cancel_check,
            crop_watermark=crop_watermark,
        )
    except VideoProductionError:
        if encoder == "libx264":
            raise
        progress(
            "Encoder phần cứng không render được; đang fallback libx264 cho toàn job",
            "video_render_segment",
        )
        _disable_hardware_encoder()
        segments = _render_segments(
            ffmpeg_exe=ffmpeg_exe,
            encoder="libx264",
            video_id=video_id,
            scenes=scenes,
            media_paths=paths,
            srt_path=srt_path,
            input_hash=input_hash,
            progress=progress,
            cancel_check=cancel_check,
            crop_watermark=crop_watermark,
        )
    cancel_check()
    _concat_segments(
        ffmpeg_exe=ffmpeg_exe,
        segments=segments,
        audio_path=audio_path,
        temporary=temporary,
        progress=progress,
    )
    expected_duration = sum(float(scene["duration"]) for scene in scenes)
    details = validate_render(temporary, expected_duration, ffmpeg_exe)
    temporary.replace(target)
    return db.upsert_video_artifact(
        video_id=video_id,
        artifact_type="final_mp4",
        path=str(target),
        content_hash=input_hash,
        status="ready",
        mime_type="video/mp4",
        size_bytes=target.stat().st_size,
        duration_seconds=details["duration_seconds"],
        width=TARGET_WIDTH,
        height=TARGET_HEIGHT,
        codecs={"video": details["video_codec"], "audio": details["audio_codec"]},
        metadata={
            "rendered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "segment_count": len(segments),
        },
    )


def prepare_visual_plan_inputs(video_id: int, snapshot: dict, progress) -> dict:
    video = db.get_video(video_id)
    audio_task = db.get_audio_task(video_id)
    if not video or video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise VideoProductionError("Video không còn ở trạng thái có thể dựng.")
    if not audio_task or audio_task.get("status") != "completed" or not audio_task.get("audio_url"):
        raise VideoProductionError("Audio chưa hoàn tất nên chưa thể dựng video.")
    audio_path = materialize_audio(
        audio_task["audio_url"], video_id, audio_task["request_hash"]
    )
    duration = float(video.get("audio_duration_seconds") or 0)
    if duration <= 0:
        duration = probe_media_duration(audio_path)
        db.update_audio_duration(video_id, duration)
    srt_path, caption_hash = create_srt(audio_path, video_id, progress)
    settings = snapshot.get("image_generation_settings") or {}
    enable_intro_video = bool(settings.get("enable_intro_video", True))
    intro_target_seconds = float(settings.get("intro_scene_target_seconds", 8.0) or 8.0)

    captions = parse_srt_segments(srt_path)
    intro_end_seconds = (
        extract_intro_boundary(video.get("generated_script", ""), captions, duration)
        if enable_intro_video
        else 0.0
    )

    windows = build_scene_windows(
        captions,
        duration,
        minimum_seconds=float(settings.get("scene_duration_min_seconds") or 25),
        target_seconds=float(settings.get("scene_duration_target_seconds") or 30),
        maximum_seconds=float(settings.get("scene_duration_max_seconds") or 35),
        intro_end_seconds=intro_end_seconds,
        intro_target_seconds=intro_target_seconds,
        enable_intro_video=enable_intro_video,
    )
    plan_hash = _sha256_bytes(
        json.dumps(
            {
                "captions": caption_hash,
                "windows": windows,
                "title": video.get("generated_title") or video.get("title") or "",
                "style": settings.get("style_prompt") or "",
                "intro_end_seconds": intro_end_seconds,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    return {
        "video": video,
        "audio_path": audio_path,
        "srt_path": srt_path,
        "caption_hash": caption_hash,
        "windows": windows,
        "plan_hash": plan_hash,
        "intro_end_seconds": intro_end_seconds,
    }


def save_visual_scene_plan(video_id: int, plan_hash: str, plan: dict) -> dict:
    target = VISUAL_PLANS_DIR / f"video_{video_id}_{plan_hash[:16]}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return db.upsert_video_artifact(
        video_id=video_id,
        artifact_type="visual_scene_plan",
        path=str(target),
        content_hash=plan_hash,
        status="ready",
        mime_type="application/json",
        size_bytes=target.stat().st_size,
        metadata={"scene_count": len(plan.get("scenes") or [])},
    )


def load_visual_scene_plan(video_id: int) -> tuple[dict, dict]:
    artifact = db.get_latest_video_artifact(video_id, "visual_scene_plan", "ready")
    if not artifact or not Path(artifact.get("path") or "").is_file():
        raise VideoProductionError("Chưa có kế hoạch cảnh đã kiểm tra cho video.")
    try:
        payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoProductionError("Artifact kế hoạch cảnh không đọc được.") from exc
    if not isinstance(payload, dict):
        raise VideoProductionError("Artifact kế hoạch cảnh sai định dạng.")
    return artifact, payload


_SENSITIVE_WORDS_RE = re.compile(
    r"\b(nguy hiểm|bi kịch|nóng tính|bạo lực|cái chết|chết|giết|tự tử|tai nạn|đánh đập|máu|trả giá|xung đột gay gắt|hận thù|vũ khí|bẫy)\b",
    re.IGNORECASE,
)
_SENSITIVE_REPLACEMENTS = {
    "nguy hiểm": "thử thách",
    "bi kịch": "khó khăn",
    "nóng tính": "căng thẳng",
    "bạo lực": "mâu thuẫn",
    "cái chết": "mất mát",
    "chết": "ra đi",
    "giết": "loại bỏ",
    "tự tử": "bế tắc",
    "tai nạn": "sự cố",
    "đánh đập": "tranh cãi",
    "máu": "",
    "trả giá": "bài học",
    "xung đột gay gắt": "bất đồng",
    "hận thù": "khoảng cách",
    "vũ khí": "",
    "bẫy": "trở ngại",
}


def _sanitize_scene_prompt_context(text: str, max_chars: int = 140) -> str:
    clean = (text or "").strip()

    def _sub(m):
        w = m.group(0).lower()
        return _SENSITIVE_REPLACEMENTS.get(w, "")

    clean = _SENSITIVE_WORDS_RE.sub(_sub, clean)
    # Remove question marks, exclamation marks, and quotes that evoke typography / title banners
    clean = re.sub(r'[\?\!\"“”«»]', '', clean)
    # If text is in ALL CAPS or mostly uppercase (like a video title headline), convert to lowercase
    letters = [c for c in clean if c.isalpha()]
    if letters and (sum(1 for c in letters if c.isupper()) / len(letters)) > 0.5:
        clean = clean.lower()
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars].rsplit(" ", 1)[0]
    return clean


def _sanitize_thumbnail_concept_for_scene0(raw_text: str, max_chars: int = 300) -> str:
    if not raw_text:
        return ""
    # Strip any image URL tags or URLs first
    cleaned = re.sub(r"\[IMAGE_URL:[^\]]*\]", "", raw_text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"/api/thumbnails/\S+", "", cleaned)
    cleaned = re.sub(
        r"(?i)^(?:prompt(?:\s*(?:tiếng anh|chi tiết|hình ảnh))?|ý tưởng(?: thiết kế)?|mô tả)\s*[:：\-–—]\s*",
        "",
        cleaned.strip(),
    )
    cleaned = re.sub(
        r"(?i)\b(?:thumbnail|font chữ|màu chữ|clickbait|tiêu đề|chữ to|dòng chữ|không có chữ|có chữ|chữ nổi bật|tỷ lệ khung hình 16:9)\b",
        "",
        cleaned,
    )
    return _sanitize_scene_prompt_context(cleaned, max_chars=max_chars)


def build_default_visual_scene_plan(
    windows: list[dict],
    title: str,
    style_prompt: str = "",
    prompt_version: str = "",
    generated_script: str = "",
    scene_0_source: str = "from_thumbnail_without_text",
    scene_0_prompt_template: str = "",
    scene_body_prompt_template: str = "",
) -> dict:
    from auto_yt.services import prompt_assets
    assets = []
    if prompt_version:
        assets = prompt_assets.list_prompt_assets(prompt_version)
    if not assets:
        try:
            from auto_yt.paths import PROMPTS_PATH
            import json
            if PROMPTS_PATH.exists():
                p_data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
                active_v = p_data.get("active_version", "default")
                assets = prompt_assets.list_prompt_assets(active_v)
        except Exception:
            pass

    # Extract concise visual style (first paragraph only, max 250 chars) to prevent oversized prompts
    raw_style = (style_prompt or "").strip() or "Cinematic documentary visual style, photorealistic, 8k resolution"
    first_para = raw_style.split("\n\n")[0].split("\n")[0].strip()
    if len(first_para) > 250:
        first_para = first_para[:250].rsplit(" ", 1)[0]
    style = first_para or "Cinematic documentary visual style, photorealistic, 8k resolution"

    # Extract thumbnail reference file and concept for Scene 0
    clean_thumb_concept = ""
    thumb_ref_path = ""
    thumb_ref_id = ""
    if generated_script and scene_0_source != "from_intro_transcript":
        if scene_0_source == "from_thumbnail_with_text":
            thumb_img_match = re.search(
                r"### \[(?:THUMBNAIL CÓ CHỮ|THUMBNAIL)\]\s*\n(.*?)(?=\n### \[|\Z)",
                generated_script,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if not thumb_img_match:
                thumb_img_match = re.search(
                    r"### \[(?:THUMBNAIL KHÔNG CHỮ|THUMBNAIL_WITHOUT_TEXT|THUMBNAIL NOTEXT)\]\s*\n(.*?)(?=\n### \[|\Z)",
                    generated_script,
                    flags=re.DOTALL | re.IGNORECASE,
                )
        else:
            # Priority 1: Thumbnail KHÔNG CHỮ (clean visual without text overlay)
            thumb_img_match = re.search(
                r"### \[(?:THUMBNAIL KHÔNG CHỮ|THUMBNAIL_WITHOUT_TEXT|THUMBNAIL NOTEXT)\]\s*\n(.*?)(?=\n### \[|\Z)",
                generated_script,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if not thumb_img_match:
                # Priority 2: Fallback to other thumbnail section if notext not found
                thumb_img_match = re.search(
                    r"### \[(?:THUMBNAIL CÓ CHỮ|THUMBNAIL)\]\s*\n(.*?)(?=\n### \[|\Z)",
                    generated_script,
                    flags=re.DOTALL | re.IGNORECASE,
                )
        if thumb_img_match:
            sec_content = thumb_img_match.group(1)
            url_match = re.search(r"\[IMAGE_URL:(?:/api/thumbnails/)?([a-zA-Z0-9_\-\.]+)\]", sec_content)
            if url_match:
                img_name = Path(url_match.group(1)).name
                candidate_path = THUMBNAILS_DIR / img_name
                if candidate_path.is_file() and candidate_path.stat().st_size > 0:
                    thumb_ref_path = str(candidate_path)
                    thumb_ref_id = "thumb_anchor"
            clean_thumb_concept = _sanitize_thumbnail_concept_for_scene0(sec_content)

    scenes = []
    prev_visual_actions: list[str] = []
    for w in windows:
        transcript_snippet = w.get("transcript", "").strip()
        matched = prompt_assets.match_scene_reference(transcript_snippet, assets)

        ref_id = ""
        ref_path = ""
        ref_note = ""
        if matched:
            ref_id = matched["name"]
            ref_path = matched["path"]
            ref_note = f"Depicting {matched['display_name']}. "

        # Transform raw Vietnamese transcript into rich, concrete English visual action
        visual_action = translate_transcript_to_visual_action(
            transcript_snippet,
            video_title=title,
            reference_name=matched.get("display_name") if matched else "",
            scene_index=w["index"],
            total_scenes=len(windows),
            prev_actions=prev_visual_actions,
        )
        prev_visual_actions.append(visual_action)
        context_part = f"Narrative scene: {visual_action}. " if visual_action else ""

        is_video = bool(w.get("is_video") or w.get("media_type") == "video")

        if w["index"] == 0:
            # Scene 0: Prioritize matched character/asset reference; fallback to thumbnail reference if no character matched
            if not matched and thumb_ref_path:
                ref_id = thumb_ref_id
                ref_path = thumb_ref_path
                ref_note = "Visual anchor from story thumbnail. "

            # Scene 0: Use Clean Thumbnail concept text if available and not focusing on a specific character, otherwise story hook
            if scene_0_prompt_template and scene_0_prompt_template.strip():
                custom_p = scene_0_prompt_template.strip()
                custom_p = (
                    custom_p
                    .replace("{style}", style)
                    .replace("{reference}", ref_note.strip())
                    .replace("{thumbnail_concept}", clean_thumb_concept or visual_action)
                    .replace("{action}", visual_action)
                    .replace("{scene_index}", "1")
                )
                prompt = re.sub(r"\s+", " ", custom_p).strip()
            elif not matched and clean_thumb_concept and len(clean_thumb_concept) >= 20:
                prompt = (
                    f"A cinematic movie still: {style}, opening scene hook. "
                    f"{ref_note}"
                    f"Story visual core: {clean_thumb_concept}. "
                    f"16:9 widescreen, photorealistic 8k, authentic documentary realism, clean framing without text."
                ).replace("  ", " ").strip()
            else:
                prompt = (
                    f"A cinematic movie still: {style}, dramatic opening scene hook. "
                    f"{ref_note}"
                    f"{context_part}"
                    f"16:9 widescreen, photorealistic 8k, authentic documentary realism, clean visual without text."
                ).replace("  ", " ").strip()
        elif is_video:
            # Subsequent intro video scenes: Story-aware dramatic continuation
            if scene_body_prompt_template and scene_body_prompt_template.strip():
                custom_p = scene_body_prompt_template.strip()
                custom_p = (
                    custom_p
                    .replace("{style}", style)
                    .replace("{reference}", ref_note.strip())
                    .replace("{action}", visual_action)
                    .replace("{scene_index}", str(w["index"] + 1))
                )
                prompt = re.sub(r"\s+", " ", custom_p).strip()
            else:
                prompt = (
                f"A cinematic documentary photograph: {style}, scene {w['index'] + 1} dramatic storytelling. "
                f"{ref_note}"
                f"{context_part}"
                f"16:9 widescreen still photograph, authentic realism, dramatic lighting, clean visual without text."
            ).replace("  ", " ").strip()
        else:
            # Standard body/outro scene
            if scene_body_prompt_template and scene_body_prompt_template.strip():
                custom_p = scene_body_prompt_template.strip()
                custom_p = (
                    custom_p
                    .replace("{style}", style)
                    .replace("{reference}", ref_note.strip())
                    .replace("{action}", visual_action)
                    .replace("{scene_index}", str(w["index"] + 1))
                )
                prompt = re.sub(r"\s+", " ", custom_p).strip()
            else:
                prompt = (
                f"A still photograph: {style}, scene {w['index'] + 1}. "
                f"{ref_note}"
                f"{context_part}"
                f"16:9 widescreen still photograph, authentic documentary realism, natural lighting, clean visual without text."
            ).replace("  ", " ").strip()

        scenes.append(
            {
                "index": w["index"],
                "start": w["start"],
                "end": w["end"],
                "duration": w["duration"],
                "transcript": transcript_snippet,
                "is_video": is_video,
                "media_type": "video" if is_video else "image",
                "subject": matched["display_name"] if matched else (visual_action[:60] if visual_action else f"Scene {w['index'] + 1}"),
                "action": visual_action,
                "setting": "cinematic scene",
                "era": "contemporary",
                "composition": "wide shot, 16:9 cinematic framing",
                "lighting": "cinematic lighting",
                "color": "natural tones",
                "prompt": prompt,
                "primary_reference_id": ref_id,
                "reference_path": ref_path,
            }
        )
    return {
        "visual_bible": {"title": title, "style": style},
        "scenes": scenes,
    }


def produce_video(
    video_id: int,
    snapshot: dict,
    progress,
    cancel_check,
    force_new_project: bool = False,
) -> dict:
    render_mode = (
        snapshot.get("render_mode")
        or (snapshot.get("image_generation_settings") or {}).get("render_mode")
        or (snapshot.get("pipeline") or {}).get("render_mode")
        or "google_flow"
    )
    if str(render_mode).strip().lower() == "stock_video":
        from auto_yt.services import stock_video_renderer
        return stock_video_renderer.produce_stock_video(
            video_id=video_id,
            snapshot=snapshot,
            progress=progress,
            cancel_check=cancel_check,
            force_new_project=force_new_project,
        )

    prepared = prepare_visual_plan_inputs(video_id, snapshot, progress)
    video = prepared["video"]
    audio_path = prepared["audio_path"]
    srt_path = prepared["srt_path"]
    caption_hash = prepared["caption_hash"]
    
    if force_new_project:
        logger.info("force_new_project is True: Purging all scene artifacts for video %s", video_id)
        purge_all_scene_artifacts(video_id)

    plan_payload = None
    if not force_new_project:
        try:
            plan_artifact, loaded_payload = load_visual_scene_plan(video_id)
            if plan_artifact.get("content_hash") == prepared["plan_hash"]:
                plan_payload = loaded_payload
        except VideoProductionError:
            plan_payload = None

    if plan_payload is None:
        title = video.get("generated_title") or video.get("title") or ""
        img_settings = snapshot.get("image_generation_settings") or {}
        style = img_settings.get("style_prompt") or ""
        scene_0_source = img_settings.get("scene_0_source") or "from_thumbnail_without_text"
        prompt_version = snapshot.get("prompt_version") or snapshot.get("version") or video.get("prompt_version") or ""
        generated_script = video.get("generated_script") or ""
        plan_payload = build_default_visual_scene_plan(
            prepared["windows"],
            title,
            style,
            prompt_version=prompt_version,
            generated_script=generated_script,
            scene_0_source=scene_0_source,
            scene_0_prompt_template=str(img_settings.get("scene_0_prompt_template") or ""),
            scene_body_prompt_template=str(img_settings.get("scene_body_prompt_template") or ""),
        )
        save_visual_scene_plan(video_id, prepared["plan_hash"], plan_payload)

    validated_plan = validate_visual_scene_plan(plan_payload, prepared["windows"])
    scenes = validated_plan["scenes"]
    settings = snapshot.get("image_generation_settings") or {}
    
    media_paths = generate_scene_media(
        video_id=video_id,
        scenes=scenes,
        settings=settings,
        progress=progress,
        cancel_check=cancel_check,
        force_new_project=force_new_project,
    )
    cancel_check()
    input_hash = _sha256_bytes(
        json.dumps(
            {
                "audio": _sha256_file(audio_path),
                "captions": caption_hash,
                "scenes": [_sha256_file(path) for path in media_paths],
                "plan": scenes,
                "visual_bible": validated_plan["visual_bible"],
                "render": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT, "fps": TARGET_FPS},
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    cancel_check()
    crop_watermark = bool(settings.get("intro_crop_watermark", True))
    artifact = render_video(
        video_id=video_id,
        audio_path=audio_path,
        srt_path=srt_path,
        scenes=scenes,
        media_paths=media_paths,
        input_hash=input_hash,
        progress=progress,
        cancel_check=cancel_check,
        crop_watermark=crop_watermark,
    )
    return {
        "artifact": artifact,
        "captions_path": str(srt_path),
        "scenes": scenes,
        "visual_bible": validated_plan["visual_bible"],
    }
