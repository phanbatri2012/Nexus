"""Durable two-phase media pipeline for Muse AI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
from PIL import Image

import nexus_engine.services.database as db
from nexus_engine.paths import MUSE_ARTIFACTS_DIR
from nexus_engine.services import (
    muse_semantic_planner,
    process_registry,
    prompt_assets,
    video_production,
)
from nexus_engine.services.muse_provider import (
    MUSE_FRAME_CONTRACT_VERSION,
    MUSE_PROVIDER_NAME,
    MuseAmbiguousSubmissionError,
    MuseError,
    MuseRefusalError,
    MuseServiceBusy,
    MusePromptBuilder,
    MuseQuotaUnavailable,
    MuseReceipt,
    MuseReferenceError,
    MuseStudioApiProvider,
    MuseTaskTransientFailure,
    MuseTaskWaitLimitExceeded,
    MuseTransientError,
)


logger = logging.getLogger(__name__)
SCENE_SECONDS = 10.0
MAX_REQUEST_ATTEMPTS = 3
MAX_RECONCILE_ATTEMPTS = 10
RETRY_DELAYS_SECONDS = (60.0, 300.0)
MUSE_PROMPT_POLICY_VERSION = "balanced_v2"
MUSE_RECOVERY_STATE_VERSION = 2
MUSE_RECOVERY_STATES = {
    "submitting",
    "reconciling",
    "retry_wait",
    "terminal_error",
}

_SEVERE_RISK_TERMS = (
    "đẫm máu",
    "máu",
    "thi thể",
    "xác chết",
    "vết thương",
    "thương vong",
    "tổn thất nặng",
    "chết",
    "giết",
    "hy sinh",
    "tử vong",
    "blood",
    "bloody",
    "gore",
    "corpse",
    "corpses",
    "wound",
    "wounds",
    "injury",
    "injuries",
    "casualty",
    "casualties",
    "death",
    "deaths",
    "dead",
    "kill",
    "killed",
    "killing",
)
_ACTION_RISK_GROUPS = {
    "weapons": (
        "súng máy",
        "súng",
        "vũ khí",
        "bom",
        "đạn",
        "tên lửa",
        "chống tăng",
        "machine gun",
        "gun",
        "rifle",
        "weapon",
        "bomb",
        "ammunition",
        "missile",
        "anti-tank",
    ),
    "attack": (
        "bắn",
        "tấn công",
        "giao tranh",
        "chiến đấu",
        "đánh tan",
        "đánh đập",
        "shoot",
        "attack",
        "combat",
        "battle",
        "assault",
    ),
    "explosion": (
        "nổ vang",
        "phát nổ",
        "vụ nổ",
        "explosion",
        "blast",
        "detonation",
    ),
    "destruction": (
        "phá hủy",
        "hủy diệt",
        "destroy",
        "destroyed",
        "destruction",
        "ruins",
    ),
}
_RISK_REPLACEMENTS = (
    ("tổn thất nặng nề", "một giai đoạn lịch sử đầy khó khăn"),
    ("tổn thất nặng", "khó khăn lịch sử"),
    ("thương vong", "mất mát lịch sử"),
    ("vết thương", "dấu vết thời gian"),
    ("thi thể", "dấu tích lịch sử"),
    ("xác chết", "dấu tích lịch sử"),
    ("hy sinh", "được tưởng niệm"),
    ("tử vong", "mất mát"),
    ("đẫm máu", "căng thẳng"),
    ("máu", "bùn đất"),
    ("chết", "mất mát"),
    ("giết", "áp đảo"),
    ("súng máy", "thiết bị thời kỳ"),
    ("chống tăng", "thiết bị phòng thủ thời kỳ"),
    ("tên lửa", "thiết bị thời kỳ"),
    ("vũ khí", "thiết bị thời kỳ"),
    ("súng", "thiết bị thời kỳ"),
    ("bom", "thiết bị thời kỳ"),
    ("đạn", "vật tư thời kỳ"),
    ("giao tranh", "thời khắc căng thẳng"),
    ("chiến đấu", "di chuyển chiến thuật"),
    ("tấn công", "tiếp cận"),
    ("đánh tan", "làm thay đổi cục diện"),
    ("đánh đập", "đối đầu"),
    ("bắn", "phản ứng"),
    ("phá hủy", "bị thời gian bào mòn"),
    ("hủy diệt", "thay đổi hoàn toàn"),
    ("nổ vang", "vang động"),
    ("phát nổ", "biến động"),
    ("vụ nổ", "biến động"),
    ("casualties", "somber context"),
    ("casualty", "somber context"),
    ("killing", "overcoming"),
    ("killed", "overcome"),
    ("kill", "overcome"),
    ("death", "somber absence"),
    ("deaths", "somber absence"),
    ("dead", "somber absence"),
    ("bloody", "tense"),
    ("blood", "mud"),
    ("gore", "weathered detail"),
    ("corpse", "weathered trace"),
    ("corpses", "weathered traces"),
    ("wound", "weathered detail"),
    ("wounds", "weathered details"),
    ("injury", "visible hardship"),
    ("injuries", "visible hardship"),
    ("machine gun", "period equipment"),
    ("anti-tank", "period defensive equipment"),
    ("ammunition", "period supplies"),
    ("missile", "period equipment"),
    ("weapon", "period equipment"),
    ("rifle", "period equipment"),
    ("gun", "period equipment"),
    ("bomb", "period equipment"),
    ("combat", "tense movement"),
    ("battle", "tense turning point"),
    ("assault", "approach"),
    ("attack", "approach"),
    ("shoot", "react"),
    ("detonation", "atmospheric disturbance"),
    ("explosion", "atmospheric disturbance"),
    ("blast", "atmospheric disturbance"),
    ("destroyed", "weathered"),
    ("destruction", "weathering"),
    ("destroy", "weather"),
    ("ruins", "aged surroundings"),
)


class MuseRetryScheduled(MuseTransientError):
    def __init__(self, message: str, *, delay_seconds: float, consume_attempt: bool = True):
        super().__init__(message)
        self.delay_seconds = max(1.0, float(delay_seconds))
        self.consume_attempt = consume_attempt


class MusePlannerBusy(MuseRetryScheduled):
    """ChatGPT profile is busy; retry without consuming a Muse attempt."""


class MuseRecoveryExhausted(MuseError):
    """Muse infrastructure retries are exhausted and the job must pause."""


class MuseReconcileExhausted(MuseRecoveryExhausted):
    """A submitted Muse task could not be reconciled within the poll budget."""


class MuseSemanticPlanNeedsAttention(video_production.VideoProductionError):
    """Semantic planning failed closed before any Muse submission."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _has_provider_receipt(metadata: dict) -> bool:
    return bool(
        str(metadata.get("provider") or "") == MUSE_PROVIDER_NAME
        and str(metadata.get("provider_task_id") or "")
        and str(metadata.get("endpoint") or "")
        and str(metadata.get("submitted_at") or "")
    )


def _metadata_for_recovery_state(
    metadata: dict,
    state: str,
    **updates,
) -> dict:
    if state not in MUSE_RECOVERY_STATES:
        raise ValueError(f"Unsupported Muse recovery state: {state}")
    normalized = {
        **metadata,
        **updates,
        "recovery_state_version": MUSE_RECOVERY_STATE_VERSION,
        "recovery_state": state,
    }
    if state in {"submitting", "reconciling"}:
        for key in (
            "terminal_generation",
            "retryable_generation",
            "infrastructure_failure",
            "recovery_exhausted",
            "reconcile_exhausted",
            "error_class",
        ):
            normalized.pop(key, None)
    elif state == "retry_wait":
        normalized["retryable_generation"] = True
        for key in (
            "terminal_generation",
            "invalid_media",
            "error_class",
        ):
            normalized.pop(key, None)
    else:
        normalized["terminal_generation"] = True
        for key in (
            "retryable_generation",
            "infrastructure_failure",
            "recovery_exhausted",
            "reconcile_exhausted",
        ):
            normalized.pop(key, None)
    return normalized


def _normalize_recovery_metadata(metadata: dict) -> dict:
    normalized = dict(metadata or {})
    current_state = str(normalized.get("recovery_state") or "")
    if (
        int(normalized.get("recovery_state_version") or 0)
        >= MUSE_RECOVERY_STATE_VERSION
        and current_state in MUSE_RECOVERY_STATES
    ):
        return _metadata_for_recovery_state(normalized, current_state)

    error_class = " ".join(
        str(normalized.get(key) or "")
        for key in ("error_class", "last_error_class")
    ).casefold()
    if _has_provider_receipt(normalized):
        state = "reconciling"
    elif (
        normalized.get("retryable_generation")
        or normalized.get("quota_retryable")
        or "museserviceinterrupted" in error_class
        or "service_restart_interrupted" in error_class
    ):
        state = "retry_wait"
    elif normalized.get("terminal_generation") or normalized.get("invalid_media"):
        state = "terminal_error"
    else:
        state = "submitting"
    return _metadata_for_recovery_state(normalized, state)


def _json_hash(value: object) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def _normalize_policy_text(value: object) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    )
    return normalized, folded


def _contains_policy_term(normalized: str, folded: str, term: str) -> bool:
    clean_term, folded_term = _normalize_policy_text(term)
    pattern = rf"(?<!\w){re.escape(clean_term)}(?!\w)"
    if re.search(pattern, normalized):
        return True
    if len(folded_term) < 4 or folded_term in {"anh", "dan", "bom", "sung"}:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(folded_term)}(?!\w)", folded))


def classify_scene_risk(scene: dict) -> dict:
    """Return deterministic bilingual risk metadata for one visual scene."""

    source = " ".join(
        str(scene.get(key) or "")
        for key in ("subject", "action", "visual_anchor", "transcript", "prompt")
    )
    normalized, folded = _normalize_policy_text(source)
    severe = sorted(
        {
            term
            for term in _SEVERE_RISK_TERMS
            if _contains_policy_term(normalized, folded, term)
        }
    )
    action_groups = sorted(
        group
        for group, terms in _ACTION_RISK_GROUPS.items()
        if any(_contains_policy_term(normalized, folded, term) for term in terms)
    )
    categories = (["direct_harm"] if severe else []) + action_groups
    return {
        "risk_level": "high" if severe or len(action_groups) >= 2 else "low",
        "risk_categories": categories,
        "risk_match_count": len(severe) + len(action_groups),
    }


def _replace_policy_phrase(value: str, source: str, replacement: str) -> str:
    normalized_source = unicodedata.normalize("NFKC", source)
    return re.sub(
        rf"(?<!\w){re.escape(normalized_source)}(?!\w)",
        replacement,
        value,
        flags=re.IGNORECASE,
    )


def _safe_scene_context(scene: dict) -> str:
    context = str(
        scene.get("safe_visual_anchor")
        or scene.get("visual_anchor")
        or scene.get("transcript")
        or ""
    ).strip()
    for source, replacement in _RISK_REPLACEMENTS:
        context = _replace_policy_phrase(context, source, replacement)
    context = re.sub(r"\s+", " ", context).strip(" .,:;-–—")
    if len(context) > 320:
        shortened = context[:320]
        sentence_end = max(shortened.rfind("."), shortened.rfind("!"), shortened.rfind("?"))
        context = (
            shortened[: sentence_end + 1]
            if sentence_end >= 160
            else shortened.rsplit(" ", 1)[0].rstrip(" ,;:-") + "."
        )
    if classify_scene_risk({"action": context})["risk_level"] == "high":
        raise MuseSemanticPlanNeedsAttention(
            f"Scene {int(scene.get('index') or 0) + 1} không có safe_visual_anchor hợp lệ."
        )
    if not context:
        raise MuseSemanticPlanNeedsAttention(
            f"Scene {int(scene.get('index') or 0) + 1} thiếu semantic anchor."
        )
    return context


def _required_semantic_anchors(scene: dict) -> tuple[str, str, str, list[str]]:
    setting = str(scene.get("setting") or "").strip()
    era = str(scene.get("era") or "").strip()
    safe_context = _safe_scene_context(scene)
    must_show = [
        re.sub(r"\s+", " ", str(item or "")).strip()
        for item in (scene.get("must_show") or [])
        if str(item or "").strip()
    ]
    generic = {"cinematic scene", "contemporary", "historical documentary setting"}
    if not setting or setting.casefold() in generic or not era or era.casefold() in generic:
        raise MuseSemanticPlanNeedsAttention(
            f"Scene {int(scene.get('index') or 0) + 1} thiếu setting/era cụ thể."
        )
    if len(must_show) < 2:
        raise MuseSemanticPlanNeedsAttention(
            f"Scene {int(scene.get('index') or 0) + 1} thiếu must_show anchors."
        )
    return setting, era, safe_context, must_show


def _prepare_balanced_submission(
    scene: dict,
    expected: str,
    prompt: str,
    references: list[dict],
) -> tuple[str, list[dict], dict]:
    risk = classify_scene_risk(scene)
    metadata = {
        "prompt_policy_version": MUSE_PROMPT_POLICY_VERSION,
        **risk,
        "prompt_strategy": "original",
    }
    if risk["risk_level"] != "high":
        return prompt, references, metadata

    metadata["prompt_strategy"] = "risk_rewritten"
    if expected == "video":
        safe_prompt, safe_references = _build_refusal_fallback(scene, expected, references)
        return safe_prompt, safe_references, metadata

    composition = str(scene.get("composition") or "wide 16:9 cinematic framing").strip()
    lighting = str(scene.get("lighting") or "natural atmospheric light").strip()
    color = str(scene.get("color") or "natural tones").strip()
    style = str(scene.get("style") or "cinematic photorealism").strip()
    setting, era, safe_context, must_show = _required_semantic_anchors(scene)
    safe_prompt = MusePromptBuilder.image(
        (
            f"Create a non-graphic visual scene in {setting}, during {era}, matching the source "
            "context and requested genre. "
            f"Scene context: {safe_context}. Required visible anchors: {', '.join(must_show)}. "
            "Focus on preparation, restrained tension, environmental storytelling, or quiet "
            "aftermath. Do not show "
            "active combat, weapons being used, explosions, visible injuries, death, blood, "
            "gore, or suffering. "
            f"Visual treatment: {composition}; {lighting}; {color}; style: {style}."
        ),
        has_references=bool(references),
    )
    return safe_prompt, references, metadata


def _build_refusal_fallback(
    scene: dict,
    expected: str,
    references: list[dict],
) -> tuple[str, list[dict]]:
    """Build one deterministic, non-graphic fallback after a terminal refusal."""

    if expected == "video":
        if len(references) != 2:
            raise MuseReferenceError(
                "Video Muse cần đủ ảnh đầu và ảnh cuối để tạo fallback an toàn."
            )
        prompt = MusePromptBuilder.video(
            (
                "Move continuously from @Image1 to @Image2 with restrained cinematic motion, "
                "gentle parallax, and natural atmospheric movement. Preserve the subject, "
                "setting, era, composition, and visual style of both endpoints. Do not introduce "
                "new people, objects, violence, injuries, graphic content, text, or logos."
            ),
            duration_seconds=float(scene.get("duration") or SCENE_SECONDS),
        )
        return prompt, references[:2]

    composition = str(scene.get("composition") or "wide 16:9 cinematic framing").strip()
    lighting = str(scene.get("lighting") or "natural atmospheric light").strip()
    color = str(scene.get("color") or "natural tones").strip()
    style = str(scene.get("style") or "cinematic photorealism").strip()
    setting, era, safe_context, must_show = _required_semantic_anchors(scene)
    prompt = MusePromptBuilder.image(
        (
            f"Create a neutral, non-graphic visual scene in {setting}, during {era}, matching "
            "the source context and requested genre. "
            f"Scene context: {safe_context}. Required visible anchors: {', '.join(must_show)}. "
            "Use environmental storytelling and restrained, non-violent human activity while "
            "preserving the named place, time context, subject, and context-specific objects. "
            "Do not depict "
            "active combat, weapons being used, explosions, injuries, death, blood, gore, text, "
            "or logos. "
            f"Visual treatment: {composition}; {lighting}; {color}; style: {style}."
        ),
        has_references=False,
    )
    return prompt, []


def _split_long_captions(
    captions: list[dict],
    *,
    maximum_seconds: float,
) -> list[dict]:
    """Split overlong captions proportionally so no spoken unit crosses scene ownership."""

    normalized: list[dict] = []
    for caption in sorted(captions, key=lambda item: float(item.get("start") or 0.0)):
        start = max(0.0, float(caption.get("start") or 0.0))
        end = max(start + 0.001, float(caption.get("end") or start))
        text = re.sub(r"\s+", " ", str(caption.get("text") or "")).strip()
        duration = end - start
        if duration <= maximum_seconds:
            normalized.append({"start": start, "end": end, "text": text})
            continue
        chunk_count = max(2, int(math.ceil(duration / maximum_seconds)))
        words = text.split()
        for chunk_index in range(chunk_count):
            word_start = round(chunk_index * len(words) / chunk_count)
            word_end = round((chunk_index + 1) * len(words) / chunk_count)
            chunk_start = start + duration * chunk_index / chunk_count
            chunk_end = start + duration * (chunk_index + 1) / chunk_count
            normalized.append(
                {
                    "start": chunk_start,
                    "end": chunk_end,
                    "text": " ".join(words[word_start:word_end]).strip(),
                }
            )
    return normalized


def _add_long_silence_segments(
    captions: list[dict],
    *,
    duration_seconds: float,
    maximum_seconds: float,
) -> list[dict]:
    """Represent long silence as bounded planning units without duplicating captions."""

    if not captions:
        captions = []
    timeline: list[dict] = []
    cursor = 0.0
    for caption in captions:
        start = max(cursor, float(caption["start"]))
        caption_duration = max(0.0, float(caption["end"]) - float(caption["start"]))
        allowed_gap = max(0.0, maximum_seconds - caption_duration)
        while start - cursor > allowed_gap + 0.001:
            required_chunk = (start - cursor) - allowed_gap
            silence_end = min(
                start,
                cursor + min(SCENE_SECONDS, maximum_seconds, required_chunk),
            )
            timeline.append({"start": cursor, "end": silence_end, "text": ""})
            cursor = silence_end
        timeline.append(dict(caption))
        cursor = max(cursor, float(caption["end"]))
    duration = float(duration_seconds)
    while duration - cursor > 0.05:
        silence_end = min(duration, cursor + min(SCENE_SECONDS, maximum_seconds))
        timeline.append({"start": cursor, "end": silence_end, "text": ""})
        cursor = silence_end
    if not timeline:
        timeline.append({"start": cursor, "end": duration, "text": ""})
    return timeline


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
    mode = str(coverage_mode or "intro_only").strip().lower()
    if mode not in {"intro_only", "first_n_minutes", "full"}:
        mode = "intro_only"
    coverage_minute_count = max(1, int(coverage_minutes or 5))
    coverage_limit = (
        duration
        if coverage_minute_count >= duration / 60.0
        else coverage_minute_count * 60.0
    )
    split_captions = _split_long_captions(captions, maximum_seconds=12.0)
    split_captions = _add_long_silence_segments(
        split_captions,
        duration_seconds=duration,
        maximum_seconds=12.0,
    )
    windows = video_production.build_scene_windows(
        split_captions,
        duration,
        minimum_seconds=6.0,
        target_seconds=10.0,
        maximum_seconds=12.0,
        intro_end_seconds=0.0,
        enable_intro_video=False,
    )
    if not windows:
        raise video_production.VideoProductionError("Không thể tạo scene windows từ audio/SRT.")
    for index, window in enumerate(windows):
        start = float(window["start"])
        if mode == "full":
            is_video = True
        elif mode == "first_n_minutes":
            is_video = start < coverage_limit
        else:
            is_video = start < max(0.0, float(intro_end_seconds or 0.0))
        if index == 0:
            is_video = True
        window.update(
            {
                "index": index,
                "start": round(float(window["start"]), 3),
                "end": round(float(window["end"]), 3),
                "duration": round(float(window["end"]) - float(window["start"]), 3),
                "transcript": re.sub(r"\s+", " ", str(window.get("transcript") or "")).strip(),
                "is_video": is_video,
                "media_type": "video" if is_video else "image",
            }
        )
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
    video = prepared["video"]
    settings = snapshot.get("image_generation_settings") or {}
    prepared["plan_hash"] = _json_hash(
        {
            "version": 2,
            "planner_version": muse_semantic_planner.PLANNER_VERSION,
            "prompt_policy_version": MUSE_PROMPT_POLICY_VERSION,
            "caption_hash": prepared["caption_hash"],
            "windows": windows,
            "title": video.get("generated_title") or video.get("title") or "",
            "style": settings.get("style_prompt") or "",
            "scene_0_prompt_template": settings.get("scene_0_prompt_template") or "",
            "scene_body_prompt_template": settings.get("scene_body_prompt_template") or "",
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
    semantic_plan_builder: Callable[[dict], dict] | None,
) -> dict:
    if not force_new_project:
        try:
            artifact, payload = _load_json_artifact(video_id, "muse_scene_plan")
            if (
                artifact.get("content_hash") == prepared["plan_hash"]
                and payload.get("planner_version") == muse_semantic_planner.PLANNER_VERSION
            ):
                cached = dict(payload)
                cached["planner_cache_hit"] = True
                cached["planner_elapsed_seconds"] = 0.0
                return cached
        except video_production.VideoProductionError:
            pass
    settings = snapshot.get("image_generation_settings") or {}
    video = prepared["video"]
    if semantic_plan_builder is None:
        raise MuseSemanticPlanNeedsAttention(
            "Không có semantic planner; dừng trước khi gửi request tới Muse."
        )
    title = str(video.get("generated_title") or video.get("title") or "").strip()
    style = str(settings.get("style_prompt") or "").strip()
    planner_request = {
        "planner_version": muse_semantic_planner.PLANNER_VERSION,
        "title": title,
        "style": style,
        "windows": prepared["windows"],
        "chat_url": str(video.get("chat_url") or ""),
        "prompt_version": str(snapshot.get("prompt_version") or snapshot.get("version") or ""),
    }
    try:
        planner_result = semantic_plan_builder(planner_request)
        if not isinstance(planner_result, dict):
            raise muse_semantic_planner.SemanticPlanValidationError(
                ["Semantic planner không trả về object."]
            )
        semantic_payload = planner_result.get("semantic_plan") or planner_result
        plan = muse_semantic_planner.compile_muse_scene_plan(
            semantic_payload,
            prepared["windows"],
            title=title,
            style=style,
        )
    except muse_semantic_planner.SemanticPlanValidationError as exc:
        raise MuseSemanticPlanNeedsAttention(str(exc)) from exc
    plan["planner_source"] = str(
        planner_result.get("planner_source") or "chatgpt_playwright_batch"
    )
    plan["planner_cache_hit"] = False
    plan["planner_elapsed_seconds"] = round(
        max(0.0, float(planner_result.get("planner_elapsed_seconds") or 0.0)),
        3,
    )
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
            "planner_version": muse_semantic_planner.PLANNER_VERSION,
            "planner_source": plan["planner_source"],
            "planner_elapsed_seconds": plan["planner_elapsed_seconds"],
            "anchor_coverage": plan.get("anchor_coverage") or {},
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


def _submission_hash_for_attempt(
    base_submission_hash: str,
    attempt: int,
    submission_variant: str,
    quota_recovery_sequence: int = 0,
    recovery_epoch: int = 0,
) -> str:
    if int(recovery_epoch) > 0:
        return _json_hash(
            {
                "base_submission_hash": base_submission_hash,
                "variant": "infrastructure_recovery_v1",
                "submission_variant": submission_variant,
                "submission_attempt": int(attempt),
                "recovery_epoch": int(recovery_epoch),
                "quota_recovery_sequence": int(quota_recovery_sequence),
            }
        )
    if int(quota_recovery_sequence) > 0:
        return _json_hash(
            {
                "base_submission_hash": base_submission_hash,
                "variant": "quota_recovery_v1",
                "submission_variant": submission_variant,
                "attempt": int(attempt),
                "quota_recovery_sequence": int(quota_recovery_sequence),
            }
        )
    if int(attempt) <= 1:
        return base_submission_hash
    return _json_hash(
        {
            "base_submission_hash": base_submission_hash,
            "variant": "terminal_transient_retry_v1",
            "submission_variant": submission_variant,
            "attempt": int(attempt),
        }
    )


def _checkpoint_quota_failure(
    *,
    video_id: int,
    artifact_type: str,
    request_hash: str,
    metadata: dict,
    error: MuseQuotaUnavailable,
    generation_started: float,
    capacity_preflight: dict | None = None,
) -> None:
    checkpoint = _metadata_for_recovery_state(
        metadata,
        "retry_wait",
        last_error_class=type(error).__name__,
        terminal_provider_task=True,
        quota_retryable=True,
        quota_recovery_sequence=int(
            metadata.get("quota_recovery_sequence") or 0
        ) + 1,
        generation_elapsed_seconds=round(
            time.monotonic() - generation_started, 3
        ),
    )
    if isinstance(capacity_preflight, dict):
        checkpoint["quota_preflight"] = dict(capacity_preflight)
    for current_key, history_key in {
        "provider_task_id": "last_provider_task_id",
        "endpoint": "last_provider_endpoint",
        "submitted_at": "last_submitted_at",
    }.items():
        value = checkpoint.pop(current_key, None)
        if value:
            checkpoint[history_key] = value
    _record_request_state(
        video_id,
        artifact_type,
        request_hash,
        status="error",
        metadata=checkpoint,
    )


def _checkpoint_terminal_transient_failure(
    *,
    video_id: int,
    artifact_type: str,
    request_hash: str,
    scene: dict,
    metadata: dict,
    error: MuseTaskTransientFailure,
    expected: str,
    generation_started: float,
) -> dict | None:
    attempt = max(1, int(metadata.get("attempt") or 1))
    provider_retry_exhausted = isinstance(error, MuseTaskWaitLimitExceeded)
    retryable_generation = attempt < MAX_REQUEST_ATTEMPTS
    checkpoint = _metadata_for_recovery_state(
        metadata,
        "retry_wait",
        last_error_class=type(error).__name__,
        terminal_provider_task=True,
        provider_retry_exhausted=provider_retry_exhausted,
    )
    receipt_fields = {
        "provider_task_id": "last_provider_task_id",
        "endpoint": "last_provider_endpoint",
        "submitted_at": "last_submitted_at",
    }
    for current_key, history_key in receipt_fields.items():
        value = checkpoint.pop(current_key, None)
        if value:
            checkpoint[history_key] = value

    if not retryable_generation:
        checkpoint.update(
            {
                "infrastructure_failure": True,
                "recovery_exhausted": True,
                "generation_elapsed_seconds": round(
                    time.monotonic() - generation_started,
                    3,
                ),
            }
        )
        _record_request_state(
            video_id,
            artifact_type,
            request_hash,
            status="error",
            metadata=checkpoint,
        )
        raise MuseRecoveryExhausted(
            (
                f"Muse đã hết tổng thời gian chờ cho scene {int(scene['index']) + 1}."
                if provider_retry_exhausted
                else f"Scene {int(scene['index']) + 1} đã hết {MAX_REQUEST_ATTEMPTS} lần thử Muse."
            )
        ) from error

    _record_request_state(
        video_id,
        artifact_type,
        request_hash,
        status="error",
        metadata=checkpoint,
    )
    raise MuseRetryScheduled(
        str(error),
        delay_seconds=RETRY_DELAYS_SECONDS[attempt - 1],
        consume_attempt=True,
    ) from error


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
    policy_metadata: dict | None = None,
    recovery_epoch: int = 0,
) -> dict:
    if expected == "video" and len(references) != 2:
        raise MuseReferenceError(
            "Video Muse không được submit khi thiếu ảnh đầu hoặc ảnh cuối."
        )
    generation_started = time.monotonic()
    frame_metadata = (
        {
            "frame_contract_version": MUSE_FRAME_CONTRACT_VERSION,
            "start_frame_hash": str(references[0].get("sha256") or ""),
            "end_frame_hash": str(references[1].get("sha256") or ""),
        }
        if expected == "video"
        else {}
    )
    existing = db.get_latest_video_artifact(video_id, artifact_type)
    if _valid_cached_artifact(existing, request_hash):
        return {**existing, "_muse_cache_hit": True}
    metadata = _normalize_recovery_metadata(
        dict((existing or {}).get("metadata") or {})
    )
    stored_recovery_epoch = int(metadata.get("recovery_epoch") or 0)
    epoch_advanced = bool(
        existing
        and existing.get("content_hash") == request_hash
        and int(recovery_epoch) > stored_recovery_epoch
    )
    if (
        epoch_advanced
        and str(metadata.get("recovery_state") or "")
        in {"retry_wait", "reconciling"}
    ):
        for key in (
            "reconcile_count",
            "reconcile_exhausted",
            "recovery_exhausted",
            "submission_transport_retry_count",
            "infrastructure_failure",
            "provider_retry_exhausted",
            "terminal_provider_task",
            "error_class",
            "last_error_class",
        ):
            metadata.pop(key, None)
        if _has_provider_receipt(metadata):
            metadata = _metadata_for_recovery_state(
                metadata,
                "reconciling",
                recovery_epoch=int(recovery_epoch),
            )
        else:
            for key in ("attempt", "submission_attempt", "submission_hash"):
                metadata.pop(key, None)
            metadata = _metadata_for_recovery_state(
                metadata,
                "retry_wait",
                recovery_epoch=int(recovery_epoch),
            )
    has_recoverable_receipt = _has_provider_receipt(metadata)
    if (
        existing
        and existing.get("content_hash") == request_hash
        and existing.get("status") == "degraded"
        and metadata.get("degraded_to_image")
    ):
        return {**existing, "degraded_to_image": True, "_muse_cache_hit": True}
    if existing and existing.get("content_hash") == request_hash:
        if existing.get("status") == "ready":
            raise MuseError(
                f"Artifact Muse scene {int(scene['index']) + 1} sai checksum hoặc bị thiếu; hãy Recreate."
            )
        if (
            existing.get("status") == "error"
            and metadata.get("recovery_state") == "terminal_error"
            and not metadata.get("quota_retryable")
            and not has_recoverable_receipt
        ):
            raise MuseError(
                f"Generation Muse scene {int(scene['index']) + 1} đã fail; hãy Recreate để gửi request mới."
            )
    needs_submission = True
    resume_without_receipt = False
    if (
        existing
        and existing.get("content_hash") == request_hash
        and (
            existing.get("status") == "submitting"
            or (existing.get("status") == "error" and has_recoverable_receipt)
        )
    ):
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
            if metadata.get("idempotent_submission") and metadata.get("submission_hash"):
                resume_without_receipt = True
            else:
                raise MuseAmbiguousSubmissionError(
                    f"Scene {int(scene['index']) + 1} có submission không receipt; không tự gửi lại."
                )
        else:
            reconcile_count = int(metadata.get("reconcile_count") or 0)
            if reconcile_count >= MAX_RECONCILE_ATTEMPTS:
                exhausted_metadata = _metadata_for_recovery_state(
                    metadata,
                    "reconciling",
                    reconcile_exhausted=True,
                    recovery_epoch=int(recovery_epoch),
                )
                _record_request_state(
                    video_id,
                    artifact_type,
                    request_hash,
                    status="submitting",
                    metadata=exhausted_metadata,
                )
                raise MuseReconcileExhausted(
                    f"Không thể đối soát submission Muse của scene {int(scene['index']) + 1} "
                    f"sau {MAX_RECONCILE_ATTEMPTS} lần; receipt đã được giữ lại."
                )
            try:
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
            except MuseTaskTransientFailure as exc:
                degraded = _checkpoint_terminal_transient_failure(
                    video_id=video_id,
                    artifact_type=artifact_type,
                    request_hash=request_hash,
                    scene=scene,
                    metadata=metadata,
                    error=exc,
                    expected=expected,
                    generation_started=generation_started,
                )
                if degraded:
                    return degraded
            except MuseQuotaUnavailable as exc:
                capacity = getattr(exc, "capacity_preflight", None) or getattr(
                    adapter, "capacity_preflight", None
                )
                _checkpoint_quota_failure(
                    video_id=video_id,
                    artifact_type=artifact_type,
                    request_hash=request_hash,
                    metadata=metadata,
                    error=exc,
                    generation_started=generation_started,
                    capacity_preflight=capacity,
                )
                exc.capacity_preflight = dict(capacity or {})
                raise
            except MuseTransientError:
                # The task may still be alive. Preserve the receipt and reconcile later.
                raise
            except MuseError as exc:
                _record_request_state(
                    video_id,
                    artifact_type,
                    request_hash,
                    status="error",
                    metadata=_metadata_for_recovery_state(
                        metadata,
                        "terminal_error",
                        error_class=type(exc).__name__,
                    ),
                )
                raise
            if reconciled:
                result = reconciled
                needs_submission = False
            else:
                next_reconcile_count = reconcile_count + 1
                metadata = _metadata_for_recovery_state(
                    metadata,
                    "reconciling",
                    reconcile_count=next_reconcile_count,
                    recovery_epoch=int(recovery_epoch),
                )
                _record_request_state(
                    video_id,
                    artifact_type,
                    request_hash,
                    status="submitting",
                    metadata=metadata,
                )
                if next_reconcile_count >= MAX_RECONCILE_ATTEMPTS:
                    raise MuseReconcileExhausted(
                        f"Không thể đối soát submission Muse của scene {int(scene['index']) + 1} "
                        f"sau {MAX_RECONCILE_ATTEMPTS} lần; receipt đã được giữ lại."
                    )
                raise MuseRetryScheduled(
                    "Submission Muse đang xử lý; sẽ đối soát lại trước khi gửi mới.",
                    delay_seconds=60,
                    consume_attempt=False,
                )
    if needs_submission:
        recovering_quota = bool(
            existing
            and existing.get("content_hash") == request_hash
            and existing.get("status") == "error"
            and metadata.get("quota_retryable")
        )
        previous_attempt = (
            int(metadata.get("attempt") or 0)
            if existing and existing.get("content_hash") == request_hash
            else 0
        )
        attempt = (
            previous_attempt
            if (resume_without_receipt or recovering_quota) and previous_attempt
            else previous_attempt + 1
        )
        if attempt > MAX_REQUEST_ATTEMPTS:
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error",
                metadata=_metadata_for_recovery_state(
                    metadata,
                    "retry_wait",
                    infrastructure_failure=True,
                    recovery_exhausted=True,
                    recovery_epoch=int(recovery_epoch),
                ),
            )
            raise MuseRecoveryExhausted(
                f"Scene {int(scene['index']) + 1} đã hết {MAX_REQUEST_ATTEMPTS} lần thử Muse."
            )
        retrying_terminal_failure = bool(
            existing
            and existing.get("content_hash") == request_hash
            and existing.get("status") == "error"
            and metadata.get("retryable_generation")
        )
        submission_variant = str(
            (
                metadata.get("submission_variant")
                if resume_without_receipt or retrying_terminal_failure or recovering_quota
                else (policy_metadata or {}).get("prompt_strategy")
            )
            or "original"
        )
        submission_prompt = prompt
        submission_references = references
        refusal_fallback_used = False
        base_submission_hash = request_hash
        if submission_variant == "refusal_fallback":
            submission_prompt, submission_references = _build_refusal_fallback(
                scene,
                expected,
                references,
            )
            base_submission_hash = _json_hash(
                {
                    "request_hash": request_hash,
                    "variant": "non_graphic_refusal_fallback_v1",
                    "prompt": submission_prompt,
                    "references": [item["sha256"] for item in submission_references],
                }
            )
            refusal_fallback_used = True

        previous_submission_attempt = max(1, previous_attempt)
        quota_recovery_sequence = int(
            metadata.get("quota_recovery_sequence") or 0
        )
        expected_previous_hash = _submission_hash_for_attempt(
            base_submission_hash,
            previous_submission_attempt,
            submission_variant,
            max(0, quota_recovery_sequence - 1) if recovering_quota else 0,
            recovery_epoch,
        )
        if (
            not epoch_advanced
            and (resume_without_receipt or retrying_terminal_failure or recovering_quota)
            and str(metadata.get("submission_hash") or "") != expected_previous_hash
        ):
            raise MuseAmbiguousSubmissionError(
                "Checkpoint Muse không khớp request/attempt hiện tại."
            )
        submission_hash = (
            str(metadata.get("submission_hash") or "")
            if resume_without_receipt
            else _submission_hash_for_attempt(
                base_submission_hash,
                attempt,
                submission_variant,
                quota_recovery_sequence if recovering_quota else 0,
                recovery_epoch,
            )
        )
        base_metadata = _metadata_for_recovery_state({
            **(
                metadata
                if resume_without_receipt or retrying_terminal_failure or recovering_quota
                else {}
            ),
            "request_hash": request_hash,
            "reference_hashes": [item["sha256"] for item in references],
            "selected_references": references,
            "omitted_references": omitted,
            "attempt": attempt,
            "submission_attempt": attempt,
            "recovery_epoch": int(recovery_epoch),
            "scene_index": int(scene["index"]),
            "phase": "videos" if expected == "video" else "images",
            "provider": MUSE_PROVIDER_NAME,
            "submission_hash": submission_hash,
            "submission_variant": submission_variant,
            "quota_recovery_sequence": quota_recovery_sequence,
            "idempotent_submission": True,
            "refusal_count": int(metadata.get("refusal_count") or 0),
            "refusal_elapsed_seconds": float(
                metadata.get("refusal_elapsed_seconds") or 0
            ),
            "fallback_count": int(metadata.get("fallback_count") or 0),
            **frame_metadata,
            **dict(policy_metadata or {}),
        }, "submitting")
        base_metadata.pop("retryable_generation", None)
        base_metadata.pop("terminal_generation", None)
        base_metadata.pop("quota_retryable", None)
        base_metadata.pop("terminal_provider_task", None)
        base_metadata.pop("last_error_class", None)
        base_metadata.pop("error_class", None)
        if not resume_without_receipt:
            base_metadata.pop("submission_transport_retry_count", None)
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
                metadata=_metadata_for_recovery_state(
                    base_metadata,
                    "reconciling",
                    provider=receipt.provider,
                    provider_task_id=receipt.provider_task_id,
                    endpoint=receipt.endpoint,
                    submitted_at=receipt.submitted_at,
                ),
            )

        try:
            while True:
                try:
                    if expected == "video":
                        if len(submission_references) != 2:
                            raise MuseReferenceError(
                                "Video Muse fallback phải giữ đủ ảnh đầu và ảnh cuối."
                            )
                        result = await adapter.create_video(
                            submission_prompt,
                            Path(submission_references[0]["path"]),
                            Path(submission_references[1]["path"]),
                            destination,
                            request_hash=submission_hash,
                            on_submitted=on_submitted,
                            cancel_check=cancel_check,
                        )
                    else:
                        result = await adapter.create_image(
                            submission_prompt,
                            [Path(item["path"]) for item in submission_references],
                            destination,
                            request_hash=submission_hash,
                            on_submitted=on_submitted,
                            cancel_check=cancel_check,
                        )
                    break
                except MuseRefusalError as exc:
                    current = db.get_latest_video_artifact(video_id, artifact_type) or {}
                    current_metadata = dict(current.get("metadata") or base_metadata)
                    refused_task_id = str(current_metadata.get("provider_task_id") or "")
                    refusal_count = int(base_metadata.get("refusal_count") or 0) + 1
                    refusal_elapsed = float(
                        base_metadata.get("refusal_elapsed_seconds") or 0
                    ) + float(getattr(exc, "provider_elapsed_seconds", 0) or 0)
                    base_metadata = {
                        **base_metadata,
                        "refusal_count": refusal_count,
                        "refusal_elapsed_seconds": round(refusal_elapsed, 3),
                        "last_refused_task_id": refused_task_id,
                        "original_error_class": type(exc).__name__,
                    }
                    strategy = str(base_metadata.get("prompt_strategy") or "original")
                    if expected == "video" and (
                        strategy == "risk_rewritten" or refusal_fallback_used
                    ):
                        degraded_metadata = {
                            **current_metadata,
                            **base_metadata,
                            "degraded_to_image": True,
                            "degrade_reason": "muse_refusal",
                            "generation_elapsed_seconds": round(
                                time.monotonic() - generation_started, 3
                            ),
                        }
                        degraded = _record_request_state(
                            video_id,
                            artifact_type,
                            request_hash,
                            status="degraded",
                            metadata=degraded_metadata,
                        )
                        return {
                            **degraded,
                            "degraded_to_image": True,
                            "_muse_cache_hit": False,
                        }
                    if refusal_fallback_used:
                        raise
                    refusal_fallback_used = True
                    submission_prompt, submission_references = _build_refusal_fallback(
                        scene,
                        expected,
                        references,
                    )
                    submission_hash = _json_hash(
                        {
                            "request_hash": request_hash,
                            "variant": "non_graphic_refusal_fallback_v1",
                            "prompt": submission_prompt,
                            "references": [
                                item["sha256"] for item in submission_references
                            ],
                        }
                    )
                    base_metadata = {
                        **base_metadata,
                        "refusal_fallback": True,
                        "fallback_count": int(base_metadata.get("fallback_count") or 0) + 1,
                        "fallback_prompt_hash": _sha256_bytes(
                            submission_prompt.encode("utf-8")
                        ),
                        "fallback_reference_hashes": [
                            item["sha256"] for item in submission_references
                        ],
                        "submission_hash": submission_hash,
                        "submission_variant": "refusal_fallback",
                    }
                    for receipt_key in ("provider_task_id", "endpoint", "submitted_at"):
                        base_metadata.pop(receipt_key, None)
                    _record_request_state(
                        video_id,
                        artifact_type,
                        request_hash,
                        status="submitting",
                        metadata=_metadata_for_recovery_state(
                            base_metadata,
                            "submitting",
                        ),
                    )
        except MuseServiceBusy:
            raise
        except MuseQuotaUnavailable as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            capacity = getattr(exc, "capacity_preflight", None) or getattr(
                adapter, "capacity_preflight", None
            )
            _checkpoint_quota_failure(
                video_id=video_id,
                artifact_type=artifact_type,
                request_hash=request_hash,
                metadata=current_metadata,
                error=exc,
                generation_started=generation_started,
                capacity_preflight=capacity,
            )
            exc.capacity_preflight = dict(capacity or {})
            raise
        except MuseTaskTransientFailure as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            degraded = _checkpoint_terminal_transient_failure(
                video_id=video_id,
                artifact_type=artifact_type,
                request_hash=request_hash,
                scene=scene,
                metadata=current_metadata,
                error=exc,
                expected=expected,
                generation_started=generation_started,
            )
            if degraded:
                return degraded
        except MuseAmbiguousSubmissionError as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="submitting",
                metadata=_metadata_for_recovery_state(
                    current_metadata,
                    "submitting",
                    last_error_class=type(exc).__name__,
                    ambiguous_submission=True,
                ),
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
                    metadata=_metadata_for_recovery_state(
                        current_metadata,
                        "reconciling",
                        last_error_class=type(exc).__name__,
                    ),
                )
                raise MuseRetryScheduled(
                    "Submission Muse có receipt; sẽ đối soát trước khi gửi lại.",
                    delay_seconds=60,
                    consume_attempt=False,
                ) from exc
            transport_retry_count = int(
                current_metadata.get("submission_transport_retry_count")
                or base_metadata.get("submission_transport_retry_count")
                or 0
            ) + 1
            exhausted = transport_retry_count >= MAX_REQUEST_ATTEMPTS
            transport_metadata = _metadata_for_recovery_state(
                base_metadata,
                "retry_wait" if exhausted else "submitting",
                last_error_class=type(exc).__name__,
                submission_transport_retry_count=transport_retry_count,
                infrastructure_failure=exhausted,
                recovery_exhausted=exhausted,
            )
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error" if exhausted else "submitting",
                metadata=transport_metadata,
            )
            if not exhausted:
                raise MuseRetryScheduled(
                    str(exc),
                    delay_seconds=RETRY_DELAYS_SECONDS[transport_retry_count - 1],
                    consume_attempt=False,
                ) from exc
            raise MuseRecoveryExhausted(
                f"Scene {int(scene['index']) + 1} đã hết {MAX_REQUEST_ATTEMPTS} lần retry kết nối Muse."
            ) from exc
        except MuseError as exc:
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error",
                metadata=_metadata_for_recovery_state(
                    current_metadata,
                    "terminal_error",
                    error_class=type(exc).__name__,
                ),
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
                    metadata=_metadata_for_recovery_state(
                        current_metadata,
                        "submitting",
                        last_error_class=type(exc).__name__,
                    ),
                )
                raise
            current = db.get_latest_video_artifact(video_id, artifact_type) or {}
            current_metadata = dict(current.get("metadata") or base_metadata)
            _record_request_state(
                video_id,
                artifact_type,
                request_hash,
                status="error",
                metadata=_metadata_for_recovery_state(
                    current_metadata,
                    "terminal_error",
                    error_class=type(exc).__name__,
                ),
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
            metadata=_metadata_for_recovery_state(
                current_metadata,
                "terminal_error",
                attempt=validation_attempt,
                error_class=type(exc).__name__,
                invalid_media=True,
            ),
        )
        raise
    checksum = _sha256_file(final_path)
    current_artifact = db.get_latest_video_artifact(video_id, artifact_type) or existing or {}
    final_metadata = {
        **dict(current_artifact.get("metadata") or {}),
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
        "provider_elapsed_seconds": result.get("provider_elapsed_seconds"),
        "generation_elapsed_seconds": round(time.monotonic() - generation_started, 3),
        **frame_metadata,
    }
    for key in (
        "recovery_state",
        "recovery_state_version",
        "retryable_generation",
        "terminal_generation",
        "infrastructure_failure",
        "recovery_exhausted",
        "reconcile_exhausted",
        "error_class",
        "last_error_class",
    ):
        final_metadata.pop(key, None)
    ready = _record_request_state(
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
    return {**ready, "_muse_cache_hit": False}


async def _prepare_media_async(
    *,
    video_id: int,
    snapshot: dict,
    prepared: dict,
    plan: dict,
    progress,
    cancel_check,
    job_id: str,
    recovery_epoch: int = 0,
) -> dict:
    pipeline_started = time.monotonic()
    settings = snapshot.get("image_generation_settings") or {}
    references = plan.get("reference_snapshot") or []
    provider = MuseStudioApiProvider(job_id=job_id, video_id=video_id)

    def heartbeat(payload: dict) -> None:
        phase = str(payload.get("phase") or "media")
        scene_index = int(payload.get("scene_index") or 0)
        task_status = str(payload.get("task_status") or "processing")
        task_progress = int(payload.get("progress") or 0)
        progress(
            f"Muse {phase}: cảnh {scene_index + 1}, task {task_status} ({task_progress}%)",
            f"muse_{phase}",
            media_progress={
                "phase": phase,
                "scene_index": scene_index,
                "task_status": task_status,
                "progress": task_progress,
            },
        )

    set_heartbeat_callback = getattr(provider, "set_heartbeat_callback", None)
    if callable(set_heartbeat_callback):
        set_heartbeat_callback(heartbeat)
    scene_results: list[dict] = []
    quota_preflight: dict = {}
    async with provider.open_session() as adapter:
        total = len(plan["scenes"])
        needs_terminal_frame = bool(plan["scenes"] and plan["scenes"][-1].get("is_video"))
        terminal_frame = plan.get("terminal_frame")
        if needs_terminal_frame and not isinstance(terminal_frame, dict):
            raise MuseSemanticPlanNeedsAttention(
                "Plan Muse thiếu terminal_frame cho scene video cuối; hãy Recreate."
            )
        if not needs_terminal_frame and terminal_frame:
            raise MuseSemanticPlanNeedsAttention(
                "Plan Muse có terminal_frame dù scene cuối là ảnh tĩnh; hãy Recreate."
            )
        image_items = [(scene, False) for scene in plan["scenes"]]
        if isinstance(terminal_frame, dict):
            image_items.append((terminal_frame, True))
        terminal_frame_artifact: dict | None = None
        image_phase_started = time.monotonic()
        for image_number, (scene, is_terminal) in enumerate(image_items, 1):
            cancel_check()
            index = int(scene["index"])
            set_heartbeat_context = getattr(adapter, "set_heartbeat_context", None)
            if callable(set_heartbeat_context):
                set_heartbeat_context(phase="images", scene_index=index)
            selected, omitted = select_scene_references(scene, references, limit=6)
            original_prompt = MusePromptBuilder.image(
                str(scene["prompt"]), has_references=bool(selected)
            )
            prompt, submission_references, policy_metadata = _prepare_balanced_submission(
                scene,
                "image",
                original_prompt,
                selected,
            )
            submitted_hashes = {item["sha256"] for item in submission_references}
            policy_omitted = [
                {**item, "omission_reason": "balanced_policy"}
                for item in selected
                if item["sha256"] not in submitted_hashes
            ]
            request_hash = _json_hash(
                {
                    "kind": "image",
                    "scene": scene,
                    "prompt_policy_version": MUSE_PROMPT_POLICY_VERSION,
                    "prompt_strategy": policy_metadata["prompt_strategy"],
                    "prompt": prompt,
                    "references": [item["sha256"] for item in submission_references],
                    "aspect_ratio": "16:9",
                }
            )
            progress(
                (
                    f"Muse images: ảnh kết {image_number}/{len(image_items)}"
                    if is_terminal
                    else f"Muse images: cảnh {image_number}/{len(image_items)}"
                ),
                "muse_images",
                media_progress={
                    "phase": "images",
                    "completed": image_number - 1,
                    "total": len(image_items),
                },
            )
            image_artifact = await _generate_request(
                adapter=adapter,
                video_id=video_id,
                artifact_type=(
                    "muse_terminal_frame"
                    if is_terminal
                    else f"muse_scene_image:{index}"
                ),
                request_hash=request_hash,
                prompt=prompt,
                references=submission_references,
                omitted=[*omitted, *policy_omitted],
                destination=(
                    MUSE_ARTIFACTS_DIR / str(video_id) / "images" / "terminal_frame"
                    if is_terminal
                    else MUSE_ARTIFACTS_DIR
                    / str(video_id)
                    / "images"
                    / f"scene_{index:04d}"
                ),
                expected="image",
                scene=scene,
                cancel_check=cancel_check,
                policy_metadata=policy_metadata,
                recovery_epoch=recovery_epoch,
            )
            if is_terminal:
                terminal_frame_artifact = image_artifact
            else:
                scene_results.append({"scene": scene, "image_artifact": image_artifact})

        image_phase_seconds = round(time.monotonic() - image_phase_started, 3)
        requested_videos = [item for item in scene_results if item["scene"].get("is_video")]
        scene_result_by_index = {
            int(item["scene"]["index"]): item for item in scene_results
        }

        def frame_reference(artifact: dict | None, *, role: str) -> dict:
            path = Path(str((artifact or {}).get("path") or ""))
            checksum = str(((artifact or {}).get("metadata") or {}).get("checksum") or "")
            if not path.is_file() or path.stat().st_size <= 0 or not checksum:
                raise MuseReferenceError(
                    f"Thiếu {role} hợp lệ; Nexus không submit video Muse."
                )
            return {
                "set_id": role,
                "set_name": "Start frame" if role == "start_frame" else "End frame",
                "set_index": -1,
                "image_index": 0,
                "aliases": [],
                "source_filename": path.name,
                "path": str(path),
                "sha256": checksum,
                "primary": role == "start_frame",
            }

        video_phase_started = time.monotonic()
        for video_number, item in enumerate(requested_videos, 1):
            cancel_check()
            scene = item["scene"]
            index = int(scene["index"])
            set_heartbeat_context = getattr(adapter, "set_heartbeat_context", None)
            if callable(set_heartbeat_context):
                set_heartbeat_context(phase="videos", scene_index=index)
            next_scene_result = scene_result_by_index.get(index + 1)
            end_artifact = (
                next_scene_result["image_artifact"]
                if next_scene_result is not None
                else terminal_frame_artifact
            )
            video_references = [
                frame_reference(item.get("image_artifact"), role="start_frame"),
                frame_reference(end_artifact, role="end_frame"),
            ]
            original_prompt = MusePromptBuilder.video(
                str(scene.get("action") or scene.get("prompt") or ""),
                duration_seconds=float(scene["duration"]),
            )
            prompt, submission_references, policy_metadata = _prepare_balanced_submission(
                scene,
                "video",
                original_prompt,
                video_references,
            )
            if len(submission_references) != 2:
                raise MuseReferenceError(
                    "Muse video policy phải giữ nguyên đủ ảnh đầu và ảnh cuối."
                )
            request_hash = _json_hash(
                {
                    "kind": "video",
                    "scene": scene,
                    "frame_contract_version": MUSE_FRAME_CONTRACT_VERSION,
                    "start_frame_hash": submission_references[0]["sha256"],
                    "end_frame_hash": submission_references[1]["sha256"],
                    "prompt_policy_version": MUSE_PROMPT_POLICY_VERSION,
                    "prompt_strategy": policy_metadata["prompt_strategy"],
                    "prompt": prompt,
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
            video_result = await _generate_request(
                adapter=adapter,
                video_id=video_id,
                artifact_type=f"muse_scene_video:{index}",
                request_hash=request_hash,
                prompt=prompt,
                references=submission_references,
                omitted=[],
                destination=MUSE_ARTIFACTS_DIR / str(video_id) / "videos" / f"scene_{index:04d}",
                expected="video",
                scene=scene,
                cancel_check=cancel_check,
                policy_metadata=policy_metadata,
                recovery_epoch=recovery_epoch,
            )
            if video_result.get("degraded_to_image"):
                item["video_degradation"] = video_result
            else:
                item["video_artifact"] = video_result

        quota_preflight = dict(adapter.capacity_preflight or {})
        if quota_preflight.get("status") == "degraded":
            progress(
                "Muse chưa xác nhận được quota; đã dùng account unknown sau cùng",
                "muse_quota_degraded",
            )

    video_phase_seconds = round(time.monotonic() - video_phase_started, 3)
    outcomes = []
    for item in scene_results:
        outcomes.append(item["image_artifact"])
        if item["scene"].get("is_video"):
            outcomes.append(item.get("video_artifact") or item.get("video_degradation") or {})
    outcome_metadata = [dict(item.get("metadata") or {}) for item in outcomes]
    summary = {
        "prompt_policy_version": MUSE_PROMPT_POLICY_VERSION,
        "quota_preflight": quota_preflight,
        "planned_media_count": len(outcomes),
        "original_count": sum(
            meta.get("prompt_strategy") == "original" for meta in outcome_metadata
        ),
        "risk_rewritten_count": sum(
            meta.get("prompt_strategy") == "risk_rewritten" for meta in outcome_metadata
        ),
        "refusal_count": sum(
            int(meta.get("refusal_count") or 0) for meta in outcome_metadata
        ),
        "refusal_elapsed_seconds": round(
            sum(
                float(meta.get("refusal_elapsed_seconds") or 0)
                for meta in outcome_metadata
            ),
            3,
        ),
        "fallback_count": sum(
            int(meta.get("fallback_count") or 0) for meta in outcome_metadata
        ),
        "degraded_to_image_count": sum(
            bool(meta.get("degraded_to_image")) for meta in outcome_metadata
        ),
        "cache_hit_count": sum(bool(item.get("_muse_cache_hit")) for item in outcomes),
        "image_phase_seconds": image_phase_seconds,
        "video_phase_seconds": video_phase_seconds,
        "total_seconds": round(time.monotonic() - pipeline_started, 3),
    }
    return {
        "scene_results": scene_results,
        "terminal_frame_artifact": terminal_frame_artifact,
        "generation_summary": summary,
    }


_MANIFEST_GENERATION_METADATA_KEYS = (
    "frame_contract_version",
    "start_frame_hash",
    "end_frame_hash",
    "prompt_policy_version",
    "prompt_strategy",
    "risk_level",
    "risk_categories",
    "risk_match_count",
    "refusal_count",
    "refusal_elapsed_seconds",
    "fallback_count",
    "degraded_to_image",
    "degrade_reason",
    "provider_retry_exhausted",
    "provider_elapsed_seconds",
    "generation_elapsed_seconds",
)


def _manifest_generation_metadata(artifact: dict | None) -> dict:
    if not artifact:
        return {}
    metadata = dict(artifact.get("metadata") or {})
    filtered = {
        key: metadata[key]
        for key in _MANIFEST_GENERATION_METADATA_KEYS
        if key in metadata
    }
    filtered["cache_hit"] = bool(artifact.get("_muse_cache_hit"))
    return filtered


def purge_muse_artifacts(video_id: int) -> int:
    removed = 0
    for artifact in db.list_video_artifacts(video_id):
        artifact_type = str(artifact.get("artifact_type") or "")
        if not (
            artifact_type == "muse_scene_plan"
            or artifact_type == "muse_media_manifest"
            or artifact_type == "muse_terminal_frame"
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


def inspect_muse_recovery(video_id: int) -> dict:
    latest_by_type: dict[str, dict] = {}
    for artifact in db.list_video_artifacts(video_id):
        artifact_type = str(artifact.get("artifact_type") or "")
        if not (
            artifact_type == "muse_terminal_frame"
            or artifact_type.startswith("muse_scene_image:")
            or artifact_type.startswith("muse_scene_video:")
        ):
            continue
        previous = latest_by_type.get(artifact_type)
        if previous is None or int(artifact.get("id") or 0) > int(previous.get("id") or 0):
            latest_by_type[artifact_type] = artifact

    recoverable: list[str] = []
    terminal: list[str] = []
    for artifact_type, artifact in latest_by_type.items():
        if str(artifact.get("status") or "") not in {"error", "submitting"}:
            continue
        metadata = _normalize_recovery_metadata(
            dict(artifact.get("metadata") or {})
        )
        state = str(metadata.get("recovery_state") or "")
        if state in {"submitting", "reconciling", "retry_wait"}:
            recoverable.append(artifact_type)
        elif state == "terminal_error":
            terminal.append(artifact_type)
    return {
        "recoverable": bool(recoverable),
        "recoverable_artifacts": sorted(recoverable),
        "terminal_artifacts": sorted(terminal),
    }


def purge_muse_artifacts_from_index(video_id: int, from_index: int) -> int:
    if int(from_index) < 0:
        raise ValueError("Muse scene index must be non-negative")
    first_video_index = max(0, int(from_index) - 1)
    removed = 0
    for artifact in db.list_video_artifacts(video_id):
        artifact_type = str(artifact.get("artifact_type") or "")
        should_remove = artifact_type in {
            "muse_terminal_frame",
            "muse_media_manifest",
            "final_mp4",
            "rendered_video",
        }
        if artifact_type.startswith("muse_scene_image:"):
            try:
                should_remove = int(artifact_type.split(":", 1)[1]) >= int(from_index)
            except ValueError:
                should_remove = False
        elif artifact_type.startswith("muse_scene_video:"):
            try:
                should_remove = int(artifact_type.split(":", 1)[1]) >= first_video_index
            except ValueError:
                should_remove = False
        if not should_remove:
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
    semantic_plan_builder: Callable[[dict], dict] | None = None,
    recovery_epoch: int = 0,
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
        semantic_plan_builder=semantic_plan_builder,
    )
    prepared_media = asyncio.run(
        _prepare_media_async(
            video_id=video_id,
            snapshot=snapshot,
            prepared=prepared,
            plan=plan,
            progress=progress,
            cancel_check=cancel_check,
            job_id=job_id,
            recovery_epoch=recovery_epoch,
        )
    )
    results = prepared_media["scene_results"]
    generation_summary = prepared_media["generation_summary"]
    media = []
    for item in results:
        scene = item["scene"]
        artifact = item.get("video_artifact") or item["image_artifact"]
        video_outcome = item.get("video_artifact") or item.get("video_degradation")
        if (
            scene.get("is_video")
            and not item.get("video_artifact")
            and not item.get("video_degradation")
        ):
            raise MuseError(
                f"Cảnh {int(scene['index']) + 1} yêu cầu video nhưng không có artifact video."
            )
        entry = {
            "scene_index": int(scene["index"]),
            "path": artifact["path"],
            "sha256": (artifact.get("metadata") or {}).get("checksum"),
            "requested_media_type": "video" if scene.get("is_video") else "image",
            "resolved_media_type": "video" if item.get("video_artifact") else "image",
            "degraded_to_image": bool(item.get("video_degradation")),
            "generation": {
                "image": _manifest_generation_metadata(item.get("image_artifact")),
                "video": _manifest_generation_metadata(video_outcome),
            },
        }
        if item.get("video_artifact"):
            image_path = Path(str(item["image_artifact"].get("path") or ""))
            if not image_path.is_file() or image_path.stat().st_size <= 0:
                raise MuseError(
                    f"Ảnh nguồn Muse cảnh {int(scene['index']) + 1} không tồn tại để fallback render."
                )
            entry["fallback_image"] = {
                "path": str(image_path),
                "sha256": _sha256_file(image_path),
            }
        media.append(entry)
    planner_summary = {
        "planner_version": str(plan.get("planner_version") or ""),
        "planner_source": str(plan.get("planner_source") or ""),
        "planner_cache_hit": bool(plan.get("planner_cache_hit")),
        "planner_elapsed_seconds": float(plan.get("planner_elapsed_seconds") or 0.0),
        "anchor_coverage": plan.get("anchor_coverage") or {},
    }
    generation_summary.update(planner_summary)
    manifest = {
        "version": 3,
        "video_id": video_id,
        "plan_hash": prepared["plan_hash"],
        "caption_hash": prepared["caption_hash"],
        "visual_bible": plan["visual_bible"],
        "scenes": plan["scenes"],
        "media": media,
        "planner_summary": planner_summary,
        "generation_summary": generation_summary,
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
            "generation_summary": generation_summary,
        },
    )
    degraded_count = int(generation_summary.get("degraded_to_image_count") or 0)
    progress(
        (
            f"Media Muse đã sẵn sàng để dựng MP4; {degraded_count} cảnh video dùng ảnh tĩnh"
            if degraded_count
            else "Media Muse đã sẵn sàng để dựng MP4"
        ),
        "muse_media_ready",
    )
    return {
        "manifest_artifact": artifact,
        "plan_hash": prepared["plan_hash"],
        "scene_count": len(media),
        "video_scene_count": sum(entry["resolved_media_type"] == "video" for entry in media),
        "generation_summary": generation_summary,
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
    fallback_image_paths: list[Path | None] = []
    fallback_image_hashes: list[str] = []
    for scene, entry in zip(scenes, entries):
        index = int(scene["index"])
        if int(entry.get("scene_index", -1)) != index:
            raise video_production.VideoProductionError("Manifest Muse sai thứ tự cảnh.")
        path = Path(str(entry.get("path") or ""))
        if not path.is_file() or _sha256_file(path) != str(entry.get("sha256") or ""):
            raise video_production.VideoProductionError(
                f"Media Muse cảnh {index + 1} thiếu hoặc sai checksum."
            )
        paths.append(path)
        hashes.append(str(entry["sha256"]))
        fallback_path: Path | None = None
        fallback_hash = ""
        fallback_entry = entry.get("fallback_image")
        if isinstance(fallback_entry, dict):
            declared_hash = str(fallback_entry.get("sha256") or "")
            candidate = Path(str(fallback_entry.get("path") or ""))
            fallback_hash = declared_hash
            if (
                candidate.is_file()
                and candidate.stat().st_size > 0
                and declared_hash
                and _sha256_file(candidate) == declared_hash
            ):
                fallback_path = candidate
            else:
                fallback_hash = f"invalid:{declared_hash}"
        fallback_image_paths.append(fallback_path)
        fallback_image_hashes.append(fallback_hash)
    input_hash = _json_hash(
        {
            "audio": _sha256_file(prepared["audio_path"]),
            "captions": prepared["caption_hash"],
            "scenes": hashes,
            "fallback_images": fallback_image_hashes,
            "plan": scenes,
            "render": {
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "video_extension_policy": video_production.VIDEO_EXTENSION_POLICY_VERSION,
            },
        }
    )
    artifact = video_production.render_video(
        video_id=video_id,
        audio_path=prepared["audio_path"],
        srt_path=prepared["srt_path"],
        scenes=scenes,
        media_paths=paths,
        fallback_image_paths=fallback_image_paths,
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
