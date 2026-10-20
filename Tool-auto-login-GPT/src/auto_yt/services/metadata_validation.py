"""Shared validation and conservative repair helpers for generated metadata."""

from __future__ import annotations

import html
import hashlib
import json
import re
import unicodedata

from auto_yt.services.script_quality import get_audio_script, get_audio_script_hash


METADATA_GATE_VERSION = 1
_SYSTEM_ERROR_PATTERNS = (
    re.compile(r"this response couldn['’]?t load", re.IGNORECASE),
    re.compile(r"something went wrong", re.IGNORECASE),
    re.compile(r"an error occurred while generating", re.IGNORECASE),
    re.compile(r"a network error occurred", re.IGNORECASE),
    re.compile(r"failed to fetch", re.IGNORECASE),
)
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|PLACEHOLDER)\b", re.IGNORECASE),
    re.compile(r"\{\{[^{}\n]+\}\}"),
    re.compile(r"\[(?:điền|thêm|insert|replace)[^\]\n]*\]", re.IGNORECASE),
)
_PROCESS_ARTIFACT_PATTERN = re.compile(
    r"\b(?:dưới đây là (?:metadata|tiêu đề|mô tả)|theo yêu cầu(?: của người dùng)?|"
    r"trong (?:bản )?kịch bản(?: này| gốc)?|ở phần (?:trên|dưới)|"
    r"here (?:is|are) the (?:metadata|title|description))\b",
    re.IGNORECASE,
)
_MEANINGFUL_STOPWORDS = {
    "anh", "ban", "cac", "cho", "co", "cua", "da", "day", "de", "den",
    "duoc", "gia", "hay", "khi", "khong", "la", "lai", "mot", "nay", "nhung",
    "noi", "qua", "sau", "the", "thi", "trong", "tu", "va", "ve", "voi",
    "a", "an", "and", "are", "for", "from", "how", "in", "is", "of", "on",
    "the", "this", "to", "what", "why", "with", "you", "your",
}


_SECTION_PATTERN = re.compile(
    r"(?ms)^(?P<header>#{0,6}\s*\[(?P<label>[^\]]+)\]\s*\n)"
    r"(?P<body>.*?)(?=^#{0,6}\s*\[[^\]]+\]\s*$|\Z)"
)


def normalize_metadata_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )
    return unicodedata.normalize("NFC", text).strip()


def is_likely_slug(value: object) -> bool:
    text = normalize_metadata_text(value)
    return bool(
        text
        and len(text) <= 100
        and re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text)
    )


def is_likely_title(value: object) -> bool:
    text = normalize_metadata_text(value)
    if not 10 <= len(text) <= 100 or "\n" in text or is_likely_slug(text):
        return False
    lowered = text.casefold()
    disallowed_markers = (
        "đăng ký kênh",
        "nhấn like",
        "bật chuông",
        "miễn trừ trách nhiệm",
        "hãy xem đến cuối",
    )
    return len(text.split()) >= 3 and not any(marker in lowered for marker in disallowed_markers)


def is_likely_description(value: object) -> bool:
    text = normalize_metadata_text(value)
    if not 80 <= len(text) <= 5000 or is_likely_slug(text):
        return False
    if "\n" not in text and len(text) <= 100 and "|" in text:
        return False
    return True


def metadata_component_error(component: str, value: object) -> str:
    text = normalize_metadata_text(value)
    if component == "title":
        if not text:
            return "missing title"
        if len(text) > 100:
            return "title exceeds 100 characters"
        if not is_likely_title(text):
            return "title has an invalid shape"
        return ""
    if component == "slug":
        return "" if is_likely_slug(text) else "slug is not a canonical URL slug"
    if component == "description":
        if not text:
            return "missing description"
        if len(text) < 80:
            return "description is shorter than 80 characters"
        if len(text) > 5000:
            return "description exceeds 5000 characters"
        if not is_likely_description(text):
            return "description looks like another metadata field"
        return ""
    raise ValueError(f"Unsupported metadata component: {component}")


def validate_required_metadata(state: dict, pipeline: dict) -> list[str]:
    errors: list[str] = []
    for component in ("title", "slug", "description"):
        if not pipeline.get(component, True):
            continue
        error = metadata_component_error(component, state.get(component, ""))
        if error:
            errors.append(f"{component}: {error}")
    title = normalize_metadata_text(state.get("title"))
    description = normalize_metadata_text(state.get("description"))
    if title and description and title.casefold() == description.casefold():
        errors.append("title and description are duplicated")
    return errors


def metadata_is_unambiguously_swapped(title: object, description: object) -> bool:
    title_text = normalize_metadata_text(title)
    description_text = normalize_metadata_text(description)
    return bool(
        metadata_component_error("title", title_text)
        and not metadata_component_error("description", title_text)
        and not metadata_component_error("title", description_text)
    )


def repair_swapped_script_metadata(script: str) -> tuple[str, bool]:
    """Swap title/description sections only when their shapes are unambiguous."""
    matches = list(_SECTION_PATTERN.finditer(str(script or "")))
    title_match = next(
        (m for m in matches if _normalize_label(m.group("label")) in {"TIEU DE", "TIEU DE VIDEO"}),
        None,
    )
    description_match = next(
        (m for m in matches if _normalize_label(m.group("label")) in {"MO TA", "MO TA VIDEO"}),
        None,
    )
    if not title_match or not description_match:
        return script, False

    title = title_match.group("body").strip()
    description = description_match.group("body").strip()
    if not metadata_is_unambiguously_swapped(title, description):
        return script, False

    replacements = [
        (title_match.start("body"), title_match.end("body"), f"{description}\n\n"),
        (description_match.start("body"), description_match.end("body"), f"{title}\n"),
    ]
    repaired = script
    for start, end, value in sorted(replacements, reverse=True):
        repaired = repaired[:start] + value + repaired[end:]
    return repaired, True


def _normalize_label(value: str) -> str:
    text = str(value or "").replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().upper()


def _issue(
    code: str,
    message: str,
    *,
    severity: str,
    component: str = "",
    confidence: float = 1.0,
    auto_fixable: bool = False,
) -> dict:
    issue: dict = {
        "code": code,
        "message": message,
        "severity": severity,
        "confidence": confidence,
        "auto_fixable": auto_fixable,
    }
    if component:
        issue["component"] = component
    return issue


def _normalize_for_match(value: object) -> str:
    text = normalize_metadata_text(value).replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _meaningful_tokens(value: object) -> set[str]:
    return {
        token
        for token in _normalize_for_match(value).split()
        if len(token) >= 4 and token not in _MEANINGFUL_STOPWORDS
    }


def _metadata_hash(metadata: dict) -> str:
    payload = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip_generated_container(value: object) -> str:
    text = normalize_metadata_text(value)
    text = re.sub(
        r"(?im)^\s*:::[a-zA-Z0-9_-]*(?:\{[^}]*\})?\s*$",
        "",
        text,
    )
    text = re.sub(r"(?m)^\s*```(?:\w+)?\s*$", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _normalize_title(value: object) -> str:
    title = _strip_generated_container(value)
    title = re.sub(
        r"^(?:#{1,6}\s*)?(?:tiêu đề(?:\s+video)?|title)\s*:\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip().strip('"“”\'`*# ')
    return re.sub(r"[ \t]*\|[ \t]*", " | ", title)


def _normalize_slug(value: object) -> str:
    slug = _strip_generated_container(value).strip("`*#\"' ")
    slug = re.sub(
        r"^(?:url\s*slug|slug)\s*:\s*",
        "",
        slug,
        flags=re.IGNORECASE,
    ).strip()
    return slug


def _normalize_tags(value: object) -> list[str]:
    raw_values = value if isinstance(value, (list, tuple)) else re.split(
        r"[,;\n]+", str(value or "")
    )
    tags: list[str] = []
    seen: set[str] = set()
    total_length = 0
    for raw in raw_values:
        tag = normalize_metadata_text(raw).strip().lstrip("#").strip('"\'` ')
        tag = re.sub(r"\s+", " ", tag)
        key = tag.casefold()
        if not tag or len(tag) > 100 or key in seen:
            continue
        projected = total_length + len(tag) + (1 if tags else 0)
        if len(tags) >= 30 or projected > 480:
            break
        tags.append(tag)
        seen.add(key)
        total_length = projected
    return tags


def _normalize_hashtags(value: object) -> str:
    text = _strip_generated_container(value)
    tokens = re.findall(r"(?<!\w)#[\w-]+", text, flags=re.UNICODE)
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.casefold()
        if key in seen:
            continue
        unique.append(token)
        seen.add(key)
        if len(unique) >= 15:
            break
    return " ".join(unique)


class MetadataQualityGate:
    """Validate upload metadata against the approved narrative deterministically."""

    def run(
        self,
        metadata: dict,
        *,
        script_text: str = "",
        require_description: bool = True,
        require_slug: bool = False,
        check_content_consistency: bool = True,
        strict_title_shape: bool = True,
    ) -> dict:
        source = dict(metadata or {})
        if isinstance(source.get("snippet"), dict):
            snippet = dict(source["snippet"])
            source = {
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "tags": snippet.get("tags", []),
                "hashtags": source.get("hashtags", ""),
                "slug": source.get("slug", ""),
            }

        original = {
            "title": source.get("title", ""),
            "slug": source.get("slug", ""),
            "description": source.get("description", ""),
            "hashtags": source.get("hashtags", ""),
            "tags": source.get("tags", []),
        }
        cleaned = {
            "title": _normalize_title(original["title"]),
            "slug": _normalize_slug(original["slug"]),
            "description": _strip_generated_container(original["description"]),
            "hashtags": _normalize_hashtags(original["hashtags"]),
            "tags": _normalize_tags(original["tags"]),
        }
        fixes: list[dict] = []
        for component in cleaned:
            before = original[component]
            after = cleaned[component]
            comparable_before = (
                list(before) if isinstance(before, tuple) else before
            )
            if comparable_before != after:
                fixes.append(
                    {
                        "code": f"normalize_{component}",
                        "component": component,
                        "message": f"Đã chuẩn hóa {component} và loại dữ liệu trùng/không hợp lệ rõ ràng.",
                    }
                )

        errors: list[dict] = []
        review_required: list[dict] = []
        warnings: list[dict] = []

        if strict_title_shape:
            title_error = metadata_component_error("title", cleaned["title"])
        elif not cleaned["title"]:
            title_error = "missing title"
        elif len(cleaned["title"]) > 100:
            title_error = "title exceeds 100 characters"
        elif "\n" in cleaned["title"]:
            title_error = "title contains a newline"
        else:
            title_error = ""
        if title_error:
            errors.append(
                _issue(
                    "invalid_title",
                    f"Tiêu đề chưa hợp lệ: {title_error}.",
                    severity="block",
                    component="title",
                )
            )
        if require_description:
            description_error = metadata_component_error(
                "description", cleaned["description"]
            )
            if description_error:
                errors.append(
                    _issue(
                        "invalid_description",
                        f"Mô tả chưa hợp lệ: {description_error}.",
                        severity="block",
                        component="description",
                    )
                )
        elif len(cleaned["description"]) > 5000:
            errors.append(
                _issue(
                    "description_too_long",
                    "Mô tả vượt quá 5000 ký tự.",
                    severity="block",
                    component="description",
                )
            )

        if require_slug:
            slug_error = metadata_component_error("slug", cleaned["slug"])
            if slug_error:
                errors.append(
                    _issue(
                        "invalid_slug",
                        f"Slug chưa hợp lệ: {slug_error}.",
                        severity="block",
                        component="slug",
                    )
                )

        if (
            cleaned["title"]
            and cleaned["description"]
            and cleaned["title"].casefold() == cleaned["description"].casefold()
        ):
            errors.append(
                _issue(
                    "duplicated_title_description",
                    "Tiêu đề và mô tả đang trùng nhau.",
                    severity="block",
                )
            )

        for component, value in cleaned.items():
            value_text = " ".join(value) if isinstance(value, list) else str(value)
            if any(pattern.search(value_text) for pattern in _SYSTEM_ERROR_PATTERNS):
                errors.append(
                    _issue(
                        "system_error_artifact",
                        f"{component} chứa thông báo lỗi hệ thống thay vì metadata.",
                        severity="block",
                        component=component,
                    )
                )
            if any(pattern.search(value_text) for pattern in _PLACEHOLDER_PATTERNS):
                errors.append(
                    _issue(
                        "unresolved_placeholder",
                        f"{component} còn placeholder chưa được thay thế.",
                        severity="block",
                        component=component,
                    )
                )
            if _PROCESS_ARTIFACT_PATTERN.search(value_text):
                review_required.append(
                    _issue(
                        "metadata_process_artifact",
                        f"{component} có nội dung tham chiếu quy trình/kịch bản.",
                        severity="review",
                        component=component,
                        confidence=0.96,
                    )
                )

        raw_hashtags = normalize_metadata_text(original["hashtags"])
        if raw_hashtags and not cleaned["hashtags"]:
            errors.append(
                _issue(
                    "invalid_hashtags",
                    "Hashtag có dữ liệu nhưng không có token bắt đầu bằng # hợp lệ.",
                    severity="block",
                    component="hashtags",
                )
            )

        narrative = get_audio_script(script_text)
        narrative_tokens = _meaningful_tokens(narrative)
        if check_content_consistency and narrative_tokens:
            title_core = cleaned["title"].split("|", 1)[0]
            title_tokens = _meaningful_tokens(title_core)
            if len(title_tokens) >= 2 and not title_tokens.intersection(narrative_tokens):
                review_required.append(
                    _issue(
                        "title_content_mismatch",
                        "Không tìm thấy từ khóa có nghĩa của tiêu đề trong phần lời đọc.",
                        severity="review",
                        component="title",
                        confidence=0.9,
                    )
                )

            description_tokens = _meaningful_tokens(cleaned["description"])
            if (
                require_description
                and len(description_tokens) >= 5
                and not description_tokens.intersection(narrative_tokens)
            ):
                review_required.append(
                    _issue(
                        "description_content_mismatch",
                        "Mô tả không có từ khóa có nghĩa trùng với phần lời đọc.",
                        severity="review",
                        component="description",
                        confidence=0.88,
                    )
                )

            tag_tokens = _meaningful_tokens(" ".join(cleaned["tags"]))
            if len(tag_tokens) >= 3 and not tag_tokens.intersection(narrative_tokens):
                warnings.append(
                    _issue(
                        "tags_content_mismatch",
                        "Các tag có vẻ ít liên quan đến phần lời đọc.",
                        severity="warning",
                        component="tags",
                        confidence=0.8,
                    )
                )

        if errors:
            decision = "block"
        elif review_required:
            decision = "review"
        else:
            decision = "pass"

        return {
            "quality_gate_version": METADATA_GATE_VERSION,
            "script_hash": get_audio_script_hash(script_text),
            "metadata_hash": _metadata_hash(cleaned),
            "decision": decision,
            "can_approve": decision == "pass",
            "can_manual_approve": decision in {"pass", "review"},
            "changed": cleaned != original,
            "fixes": fixes,
            "errors": errors,
            "review_required": review_required,
            "warnings": warnings,
            "metrics": {
                "title_characters": len(cleaned["title"]),
                "description_characters": len(cleaned["description"]),
                "tag_count": len(cleaned["tags"]),
                "hashtag_count": len(cleaned["hashtags"].split()),
            },
            "cleaned_metadata": cleaned,
        }
