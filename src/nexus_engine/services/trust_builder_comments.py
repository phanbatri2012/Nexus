"""Contextual Trust Builder comments grounded in one exact YouTube video."""

from __future__ import annotations

import copy
import datetime
import json
import logging
import re
import unicodedata
from typing import Any

from nexus_engine.default_prompts import DEFAULT_PROMPTS_DATA
from nexus_engine.paths import PROMPTS_PATH
from nexus_engine.services import database as db
from nexus_engine.services.youtube_comments import extract_youtube_video_id

logger = logging.getLogger(__name__)


MAX_CONTEXT_CHARS = 6000
MAX_DESCRIPTION_CHARS = 1200
MAX_TRANSCRIPT_CHARS = 3000
MAX_CHAPTERS_CHARS = 800
MAX_TOP_COMMENTS_CHARS = 1000
MAX_TRANSCRIPT_SEGMENTS = 8
MAX_TOP_COMMENTS = 5
COMMENT_MIN_WORDS = 12
COMMENT_MAX_WORDS = 60
COMMENT_MIN_CJK_CHARS = 20
COMMENT_MAX_CHARS = 240
NEAR_DUPLICATE_THRESHOLD = 0.75
LATIN_LANGUAGE_HINTS = {
    "en": {"the", "this", "that", "with", "from", "makes", "point", "clearer", "about"},
    "es": {"el", "la", "los", "que", "con", "este", "esta", "muy", "para", "gracias"},
    "de": {"der", "die", "das", "mit", "dieser", "diese", "sehr", "für", "danke"},
    "fr": {"le", "la", "les", "que", "avec", "cette", "très", "pour", "merci"},
    "pt": {"o", "a", "que", "com", "este", "esta", "muito", "para", "obrigado"},
    "it": {"il", "lo", "la", "che", "con", "questo", "questa", "molto", "per", "grazie"},
}


def _read_prompt_versions() -> dict[str, dict[str, Any]]:
    data = copy.deepcopy(DEFAULT_PROMPTS_DATA)
    try:
        if PROMPTS_PATH.exists():
            loaded = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read Trust Builder prompt mappings: %s", exc)
        return {}
    versions = data.get("versions") or {}
    return versions if isinstance(versions, dict) else {}


def prompt_versions_for_channel(
    youtube_channel_ucid: str,
    versions: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Return prompt versions explicitly mapped to one stable YouTube channel ID."""
    channel_id = str(youtube_channel_ucid or "").strip()
    if not channel_id:
        return []
    source = versions if versions is not None else _read_prompt_versions()
    return sorted(
        version_id
        for version_id, config in source.items()
        if isinstance(config, dict)
        and str(config.get("default_youtube_channel_id") or "").strip() == channel_id
    )


def _safe_video_id(url: str) -> str:
    try:
        return extract_youtube_video_id(str(url or ""))
    except ValueError:
        return ""


def _recency_sort(value: Any, fallback: int = 0) -> float:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return float(fallback)


def resolve_video_chat(
    youtube_video_id: str,
    plan: dict[str, Any],
    *,
    prompt_versions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve one exact video to the safest existing ChatGPT conversation."""
    normalized_id = str(youtube_video_id or "").strip()
    if not normalized_id:
        return {"status": "invalid_video_id", "skip_reason": "invalid_video_id"}

    channel_db_id = int(plan.get("channel_db_id") or 0)
    mapped_prompts = prompt_versions_for_channel(
        str(plan.get("youtube_channel_ucid") or ""),
        prompt_versions,
    )
    mapped_set = set(mapped_prompts)
    candidates: list[dict[str, Any]] = []
    exact_publications = [
        publication
        for publication in db.list_video_publication_identities()
        if str(publication.get("youtube_video_id") or "") == normalized_id
        and publication.get("video_status") == db.VIDEO_STATUS_ACTIVE
    ]
    current_channel_video_ids = {
        int(publication["video_id"])
        for publication in exact_publications
        if publication.get("youtube_channel_id") is not None
        and int(publication["youtube_channel_id"]) == channel_db_id
    }
    wrong_channel_only_video_ids = {
        int(publication["video_id"])
        for publication in exact_publications
        if int(publication["video_id"]) not in current_channel_video_ids
    }

    for publication in exact_publications:
        publication_channel_id = publication.get("youtube_channel_id")
        if publication_channel_id is None or int(publication_channel_id) != channel_db_id:
            continue
        candidates.append(
            {
                "relationship": "publication_same_channel",
                "tier": 4,
                "chat_url": str(publication.get("chat_url") or "").strip(),
                "prompt_version": str(publication.get("prompt_version") or "").strip(),
                "linked_video_id": int(publication["video_id"]),
                "created_sort": _recency_sort(
                    publication.get("publication_created_at")
                    or publication.get("video_created_at"),
                    int(publication.get("id") or 0),
                ),
                "context_source": "publication",
            }
        )

    for video in db.list_video_identity_candidates():
        if video.get("video_status") != db.VIDEO_STATUS_ACTIVE:
            continue
        if int(video.get("id") or 0) in wrong_channel_only_video_ids:
            continue
        if _safe_video_id(str(video.get("url") or "")) != normalized_id:
            continue
        prompt_version = str(video.get("prompt_version") or "").strip()
        candidates.append(
            {
                "relationship": (
                    "source_mapped_prompt"
                    if prompt_version and prompt_version in mapped_set
                    else "source_video"
                ),
                "tier": 3 if prompt_version and prompt_version in mapped_set else 2,
                "chat_url": str(video.get("chat_url") or "").strip(),
                "prompt_version": prompt_version,
                "linked_video_id": int(video["id"]),
                "created_sort": _recency_sort(
                    video.get("created_at"), int(video.get("id") or 0)
                ),
                "context_source": "stored_video",
            }
        )

    for link in db.list_trust_video_chats(normalized_id):
        prompt_version = str(link.get("prompt_version") or "").strip()
        if mapped_set and prompt_version not in mapped_set:
            continue
        candidates.append(
            {
                "relationship": "trust_created",
                "tier": 3 if prompt_version in mapped_set else 2,
                "chat_url": str(link.get("chat_url") or "").strip(),
                "prompt_version": prompt_version,
                "linked_video_id": link.get("linked_video_id"),
                "created_sort": _recency_sort(
                    link.get("last_used_at") or link.get("created_at")
                ),
                "context_source": str(link.get("context_source") or "trust_created"),
            }
        )

    if candidates:
        candidates.sort(
            key=lambda item: (
                int(item["tier"]),
                bool(item.get("chat_url")),
                float(item.get("created_sort") or 0.0),
            ),
            reverse=True,
        )
        best = dict(candidates[0])
        if not best.get("prompt_version"):
            if len(mapped_prompts) == 1:
                best["prompt_version"] = mapped_prompts[0]
            elif not best.get("chat_url"):
                return {
                    "status": "prompt_version_unresolved",
                    "skip_reason": "prompt_version_unresolved",
                    "candidates": len(candidates),
                }
        best["status"] = "existing_chat" if best.get("chat_url") else "new_chat"
        best["chat_reused"] = bool(best.get("chat_url"))
        best["youtube_video_id"] = normalized_id
        return best

    if len(mapped_prompts) != 1:
        return {
            "status": "prompt_version_unresolved",
            "skip_reason": (
                "prompt_version_ambiguous" if len(mapped_prompts) > 1 else "prompt_version_missing"
            ),
            "youtube_video_id": normalized_id,
        }
    return {
        "status": "new_chat",
        "youtube_video_id": normalized_id,
        "prompt_version": mapped_prompts[0],
        "chat_url": "",
        "linked_video_id": None,
        "context_source": "youtube_page",
        "chat_reused": False,
    }


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate_sentence(value: str, limit: int) -> str:
    clean = _normalize_space(value)
    if len(clean) <= limit:
        return clean
    candidate = clean[:limit].rstrip()
    boundary = max(candidate.rfind(". "), candidate.rfind("! "), candidate.rfind("? "))
    if boundary >= max(80, limit // 2):
        candidate = candidate[: boundary + 1]
    return candidate.rstrip(" ,;:-")


def _sample_text_segments(value: str, limit: int, max_segments: int) -> list[str]:
    clean = str(value or "").strip()
    if not clean:
        return []
    parts = [
        _normalize_space(part)
        for part in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+", clean)
        if _normalize_space(part)
    ]
    if not parts:
        return []
    if len(parts) > max_segments:
        indexes = sorted(
            {
                round(index * (len(parts) - 1) / max(1, max_segments - 1))
                for index in range(max_segments)
            }
        )
        parts = [parts[index] for index in indexes]
    result: list[str] = []
    used = 0
    for part in parts:
        remaining = limit - used
        if remaining <= 0:
            break
        clipped = _truncate_sentence(part, remaining)
        if clipped:
            result.append(clipped)
            used += len(clipped) + 1
    return result


def build_context_packet(
    *,
    youtube_video_id: str,
    page_context: dict[str, Any],
    stored_video: dict[str, Any] | None = None,
    observed_comments: list[str] | None = None,
) -> dict[str, Any]:
    """Build a bounded context packet without persisting a full transcript copy."""
    stored = stored_video or {}
    title = _truncate_sentence(
        str(page_context.get("title") or stored.get("title") or ""),
        300,
    )
    channel = _truncate_sentence(str(page_context.get("channel") or ""), 200)
    description_source = str(
        stored.get("description") or page_context.get("description") or ""
    )
    description = _truncate_sentence(description_source, MAX_DESCRIPTION_CHARS)
    raw_chapters = page_context.get("chapters") or []
    if isinstance(raw_chapters, str):
        raw_chapters = [raw_chapters]
    chapters = _sample_text_segments(
        "\n".join(str(item or "") for item in raw_chapters),
        MAX_CHAPTERS_CHARS,
        12,
    )
    page_segments = page_context.get("transcript_segments") or []
    if isinstance(page_segments, str):
        page_segments = [page_segments]
    stored_transcript = str(stored.get("transcript") or "").strip()
    transcript_source = stored_transcript or "\n".join(
        str(item or "") for item in page_segments
    )
    transcript_segments = _sample_text_segments(
        transcript_source,
        MAX_TRANSCRIPT_CHARS,
        MAX_TRANSCRIPT_SEGMENTS,
    )
    combined_comments = [
        *(observed_comments or []),
        *((page_context.get("top_comments") or []) if isinstance(page_context.get("top_comments") or [], list) else []),
    ]
    top_comments: list[str] = []
    seen_comments: set[str] = set()
    for comment in combined_comments:
        clean = _truncate_sentence(str(comment or ""), 300)
        key = clean.casefold()
        if clean and key not in seen_comments:
            seen_comments.add(key)
            top_comments.append(clean)
        if len(top_comments) >= MAX_TOP_COMMENTS:
            break
    while len("\n".join(top_comments)) > MAX_TOP_COMMENTS_CHARS and top_comments:
        top_comments.pop()

    sections = [
        f"YOUTUBE_VIDEO_ID: {youtube_video_id}",
        f"TITLE: {title}",
        f"CHANNEL: {channel or '(unknown)'}",
        f"DESCRIPTION: {description or '(none)'}",
        "CHAPTERS:\n" + ("\n".join(chapters) or "(none)"),
        "TRANSCRIPT_EXCERPTS:\n" + ("\n".join(transcript_segments) or "(none)"),
        "TOP_COMMENTS_FOR_DUPLICATE_AVOIDANCE:\n" + ("\n".join(top_comments) or "(none)"),
    ]
    prompt_context = "\n\n".join(sections)
    if len(prompt_context) > MAX_CONTEXT_CHARS:
        overflow = len(prompt_context) - MAX_CONTEXT_CHARS
        if transcript_segments:
            transcript_segments[-1] = _truncate_sentence(
                transcript_segments[-1],
                max(0, len(transcript_segments[-1]) - overflow),
            )
        sections[5] = "TRANSCRIPT_EXCERPTS:\n" + ("\n".join(transcript_segments) or "(none)")
        prompt_context = "\n\n".join(sections)[:MAX_CONTEXT_CHARS]

    trusted_evidence = "\n".join(
        [
            _normalize_space(description_source),
            *(str(item or "") for item in raw_chapters),
            stored_transcript or "\n".join(page_segments),
        ]
    ).strip()
    return {
        "youtube_video_id": youtube_video_id,
        "title": title,
        "channel": channel,
        "description": description,
        "chapters": chapters,
        "transcript_segments": transcript_segments,
        "top_comments": top_comments,
        "prompt_context": prompt_context,
        "trusted_evidence_text": trusted_evidence,
        "context_chars": len(prompt_context),
        "has_grounding": bool(trusted_evidence),
    }


def _detect_text_language(text: str, fallback: str = "") -> str:
    if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", text, re.IGNORECASE):
        return "vi"
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    tokens = set(re.findall(r"[a-zà-öø-ÿ]+", text.casefold()))
    scores = {
        language: len(tokens & hints)
        for language, hints in LATIN_LANGUAGE_HINTS.items()
    }
    best_language, best_score = max(scores.items(), key=lambda item: item[1])
    competing_scores = [score for score in scores.values() if score == best_score]
    if best_score >= 2 and len(competing_scores) == 1:
        return best_language
    return str(fallback or "").strip().lower()


def detect_context_language(context: dict[str, Any], market_language: str = "en") -> str:
    text = " ".join(
        [
            str(context.get("title") or ""),
            str(context.get("description") or ""),
            " ".join(context.get("transcript_segments") or []),
        ]
    )
    return _detect_text_language(text, fallback=market_language or "en") or "en"


def build_comment_prompt(
    context: dict[str, Any],
    expected_language: str,
    *,
    include_context: bool = True,
) -> str:
    prompt = (
        "Create exactly one natural YouTube comment grounded only in the supplied video context. "
        "Mention one concrete idea, do not invent personal experience, do not copy top comments, "
        "and do not include URLs, hashtags, promotion, or generic praise.\n"
        f"Write in language code: {expected_language}.\n"
        "Return JSON only with this schema: "
        '{"comment":"...","language":"...","evidence":"exact excerpt from description, chapter, or transcript",'
        '"anchor_terms":["exact concrete phrase"]}. '
        "Evidence must be a short exact excerpt from the trusted context, and every anchor term must "
        "appear verbatim in both evidence and comment."
    )
    if include_context:
        return prompt + f"\n\nVIDEO_CONTEXT:\n{context['prompt_context']}"
    return (
        prompt
        + " Use only the source context already present in this video's conversation; "
        "do not ask for or restate the full transcript."
    )


def build_repair_prompt(errors: list[str], expected_language: str) -> str:
    return (
        "Your previous JSON comment failed validation for these reasons: "
        + "; ".join(errors[:8])
        + ". Repair it using only the video context already provided. "
        + f"Keep language code {expected_language}. Return only the same JSON schema; "
        + "use a short exact evidence excerpt and at least one verbatim anchor in both evidence and comment."
    )


def parse_comment_response(response_text: str) -> dict[str, Any]:
    raw = str(response_text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("invalid_json")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_json_object")
    return payload


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", _normalize_space(value)).casefold()
    return normalized


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[\w\u00c0-\u024f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", _fold(value)))


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def validate_comment_draft(
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    expected_language: str,
    recent_comments: set[str] | None = None,
) -> dict[str, Any]:
    comment = _normalize_space(str(payload.get("comment") or ""))
    language = str(payload.get("language") or "").strip().lower()
    evidence = _normalize_space(str(payload.get("evidence") or ""))
    raw_anchors = payload.get("anchor_terms") or []
    anchors = [
        _normalize_space(str(item or ""))
        for item in raw_anchors
        if _normalize_space(str(item or ""))
    ][:5] if isinstance(raw_anchors, list) else []
    trusted = _fold(str(context.get("trusted_evidence_text") or ""))
    folded_comment = _fold(comment)
    folded_evidence = _fold(evidence)
    detected_comment_language = _detect_text_language(comment)

    checks = {
        "comment_present": bool(comment),
        "evidence_exact": bool(evidence and folded_evidence in trusted),
        "anchor_grounded": any(
            _fold(anchor) in folded_evidence and _fold(anchor) in folded_comment
            for anchor in anchors
        ),
        "language_match": bool(
            language
            and language == expected_language
            and detected_comment_language in {"", expected_language}
        ),
        "safe_style": True,
        "natural_length": True,
        "unique": True,
    }

    word_count = len(comment.split())
    contains_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", comment))
    if contains_cjk:
        checks["natural_length"] = COMMENT_MIN_CJK_CHARS <= len(comment) <= COMMENT_MAX_CHARS
    else:
        checks["natural_length"] = COMMENT_MIN_WORDS <= word_count <= COMMENT_MAX_WORDS and len(comment) <= COMMENT_MAX_CHARS

    unsafe_patterns = (
        r"https?://|www\.",
        r"(?:^|\s)#[\w-]+",
        r"\b(?:subscribe to my|check out my|buy now|promo code)\b",
        r"\b(?:tôi đã (?:mua|dùng|thử)|i (?:bought|used|tried) this)\b",
    )
    checks["safe_style"] = bool(comment) and not any(
        re.search(pattern, comment, re.IGNORECASE) for pattern in unsafe_patterns
    )

    comparison_comments = [
        *(context.get("top_comments") or []),
        *(recent_comments or set()),
    ]
    checks["unique"] = not any(
        _fold(comment) == _fold(previous)
        or _similarity(comment, str(previous)) >= NEAR_DUPLICATE_THRESHOLD
        for previous in comparison_comments
        if str(previous or "").strip()
    )

    weights = {
        "comment_present": 0.10,
        "evidence_exact": 0.25,
        "anchor_grounded": 0.20,
        "language_match": 0.10,
        "safe_style": 0.15,
        "natural_length": 0.10,
        "unique": 0.10,
    }
    quality_score = round(sum(weights[name] for name, passed in checks.items() if passed), 2)
    errors = [name for name, passed in checks.items() if not passed]
    return {
        "valid": not errors and quality_score >= 0.85,
        "comment": comment,
        "language": language,
        "detected_comment_language": detected_comment_language,
        "evidence": evidence,
        "anchor_terms": anchors,
        "quality_score": quality_score,
        "quality_checks": checks,
        "errors": errors,
    }
