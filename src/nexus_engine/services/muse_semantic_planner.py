"""Semantic scene planning and deterministic Muse prompt compilation."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Iterable


PLANNER_VERSION = "semantic_v2"

REQUIRED_SCENE_FIELDS = (
    "subject",
    "action",
    "setting",
    "era",
    "visual_anchor",
    "safe_visual_anchor",
    "must_show",
    "composition",
    "lighting",
    "color",
    "narrative_role",
)

_GENERIC_VALUES = {
    "cinematic scene",
    "documentary scene",
    "historical scene",
    "contemporary",
    "modern day",
    "modern-day",
    "unknown",
    "n/a",
    "none",
}
_CTA_PATTERN = re.compile(
    r"(?i)(?<!\w)(?:like|subscribe|đăng\s*ký|chia\s*sẻ|nhấn\s*chuông|follow)(?!\w)"
)
_UNSAFE_SAFE_ANCHOR_PATTERN = re.compile(
    r"(?i)(?<!\w)(?:"
    r"máu|đẫm\s*máu|thi\s*thể|xác\s*chết|vết\s*thương|thương\s*vong|"
    r"bắn|nổ|giao\s*tranh|chiến\s*đấu|tấn\s*công|"
    r"blood|bloody|gore|corpses?|wounds?|injur(?:y|ies)|casualt(?:y|ies)|deaths?|"
    r"shoot(?:ing)?|explosion|combat|battle|assault"
    r")(?!\w)"
)
_SOURCE_ANCHOR_PATTERN = re.compile(
    r"\b(?:18|19|20)\d{2}\b|"
    r"\b(?i:quốc\s*lộ|đường\s*số|route|highway)\s*\d+[A-Za-z]?\b|"
    r"\b(?i:trung\s*đoàn|tiểu\s*đoàn|binh\s*đoàn|bình\s*đoàn|đoàn\s*xe|groupement)"
    r"(?:\s+(?i:cơ\s*động|số))*\s+[A-Za-z0-9-]+\b|"
    r"\b[A-Z]{2,}\d+[A-Za-z0-9-]*\b",
    flags=re.UNICODE,
)


class SemanticPlanValidationError(ValueError):
    """Raised when ChatGPT output cannot safely drive Muse generation."""

    def __init__(self, errors: Iterable[str]):
        self.errors = [str(error).strip() for error in errors if str(error).strip()]
        super().__init__("; ".join(self.errors) or "Semantic scene plan is invalid.")


def _clean_text(value: object, *, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars]
    sentence_end = max(shortened.rfind("."), shortened.rfind("!"), shortened.rfind("?"))
    if sentence_end >= max_chars // 2:
        return shortened[: sentence_end + 1].strip()
    return shortened.rsplit(" ", 1)[0].rstrip(" ,;:-") + "."


def _identity(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    folded = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def extract_source_anchors(text: str) -> list[str]:
    """Extract proper-name, year, and route anchors that should survive planning."""

    anchors: list[str] = []
    seen: set[str] = set()
    source = str(text or "")
    def append_anchor(value: str, *, match_start: int) -> None:
        value = re.sub(r"\s+", " ", value).strip(" ,.;:()[]")
        key = _identity(value)
        if len(key) < 2 or key in seen or _CTA_PATTERN.search(value):
            return
        prefix = source[:match_start].rstrip()
        sentence_leading_single_word = (
            len(value.split()) == 1
            and not any(char.isdigit() for char in value)
            and (not prefix or prefix[-1:] in ".!?…\n")
        )
        if sentence_leading_single_word:
            return
        if key in {"hay", "nhung", "va", "tai", "khi", "neu", "day", "do"}:
            return
        anchors.append(value)
        seen.add(key)

    for match in _SOURCE_ANCHOR_PATTERN.finditer(source):
        append_anchor(match.group(0), match_start=match.start())

    tokens = list(re.finditer(r"\b[^\W\d_][\w'’-]*\b", source, flags=re.UNICODE))
    group: list[re.Match] = []

    def flush_group() -> None:
        if not group:
            return
        append_anchor(
            " ".join(item.group(0) for item in group),
            match_start=group[0].start(),
        )
        group.clear()

    for token in tokens:
        value = token.group(0)
        if value[:1].isupper():
            if group and source[group[-1].end() : token.start()].strip():
                flush_group()
            group.append(token)
        else:
            flush_group()
    flush_group()
    return anchors


def build_planner_prompt(*, title: str, style: str, windows: list[dict]) -> str:
    scene_inputs = [
        {
            "index": int(window["index"]),
            "start": round(float(window["start"]), 3),
            "end": round(float(window["end"]), 3),
            "transcript": _clean_text(window.get("transcript"), max_chars=1800),
        }
        for window in windows
    ]
    schema = {
        "story_context": "one concise visual continuity note",
        "scenes": [
            {
                "index": 0,
                "subject": "specific people/group/object",
                "action": "one filmable action",
                "setting": "specific physical or digital environment grounded in the source",
                "era": "specific date, period, or source-grounded present-day time context",
                "visual_anchor": "concrete image that represents this narration window",
                "safe_visual_anchor": "same meaning without active violence, injury, death, or gore",
                "must_show": ["2-5 indispensable visible anchors"],
                "composition": "camera framing and spatial arrangement",
                "lighting": "specific lighting",
                "color": "specific palette",
                "narrative_role": "opening|setup|development|demonstration|turning_point|resolution|conclusion",
            }
        ],
    }
    return (
        "Bạn là visual planner cho video thuộc mọi chủ đề như tài liệu, giáo dục, công nghệ, "
        "đời sống và kể chuyện. Lập kế hoạch ngữ nghĩa cho TOÀN BỘ scene trong một lần. "
        "Chỉ trả về một JSON object hợp lệ, không markdown, không giải thích.\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "- Mỗi scene phải bám đúng transcript của chính scene và mạch chuyện toàn video.\n"
        "- Giữ nguyên tên riêng, địa danh, phe/nhóm, con số, năm và vật thể quan trọng từ nguồn.\n"
        "- setting và era phải cụ thể; cấm placeholder như 'cinematic scene' hoặc 'contemporary'.\n"
        "- visual_anchor mô tả hình ảnh có thể quay/chụp, không chép nguyên lời thoại dài.\n"
        "- safe_visual_anchor giữ nguyên địa điểm, thời gian, chủ thể và vật thể trung tính, nhưng "
        "chỉ mô tả trạng thái chuẩn bị, quan sát, môi trường, căng thẳng hoặc hậu quả phi đồ họa; "
        "không nhắc điều cấm và không đổi thể loại của video.\n"
        "- Các scene phải khác nhau rõ ràng. must_show có 2-5 chuỗi ngắn, nhìn thấy được.\n"
        "- Nếu scene cuối có CTA like/đăng ký/chia sẻ, bỏ CTA khỏi hình và tạo một kết luận "
        "trực quan đúng chủ đề. Chỉ dùng sắc thái tưởng niệm khi chính nội dung nguồn yêu cầu. "
        "Không đưa chữ/logo vào hình.\n"
        f"- Trả đúng {len(scene_inputs)} scene, index liên tục từ 0.\n\n"
        f"TITLE: {_clean_text(title, max_chars=500)}\n"
        f"STYLE: {_clean_text(style, max_chars=800)}\n"
        "SCENE_INPUTS_JSON:\n"
        f"{json.dumps(scene_inputs, ensure_ascii=False)}\n\n"
        "OUTPUT_SCHEMA_EXAMPLE:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def build_repair_prompt(errors: Iterable[str], *, scene_count: int) -> str:
    concise_errors = [_clean_text(error, max_chars=240) for error in errors]
    return (
        "JSON kế hoạch vừa trả về không đạt validation. Hãy sửa TOÀN BỘ JSON trước đó và chỉ "
        "trả về JSON object hợp lệ, không markdown, không giải thích. "
        f"Phải có đúng {scene_count} scene theo đúng index. Các lỗi cần sửa: "
        + json.dumps(concise_errors, ensure_ascii=False)
    )


def parse_planner_response(response_text: str) -> dict:
    raw = str(response_text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise SemanticPlanValidationError(["Phản hồi planner không chứa JSON object hợp lệ."])


def _must_show_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_clean_text(item, max_chars=120) for item in value]
    return [item for item in cleaned if item]


def validate_semantic_plan(payload: dict, windows: list[dict]) -> dict:
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise SemanticPlanValidationError(["Planner payload phải là JSON object."])
    story_context = _clean_text(payload.get("story_context"), max_chars=900)
    if len(story_context) < 12:
        errors.append("story_context quá ngắn hoặc bị thiếu.")
    supplied = payload.get("scenes")
    if not isinstance(supplied, list) or len(supplied) != len(windows):
        supplied_count = len(supplied) if isinstance(supplied, list) else 0
        errors.append(f"Số scene sai: nhận {supplied_count}, cần {len(windows)}.")
        raise SemanticPlanValidationError(errors)

    normalized_scenes: list[dict] = []
    seen_anchors: set[str] = set()
    for window, raw_scene in zip(windows, supplied):
        expected_index = int(window["index"])
        if not isinstance(raw_scene, dict):
            errors.append(f"Scene {expected_index} không phải object.")
            continue
        try:
            supplied_index = int(raw_scene.get("index", -1))
        except (TypeError, ValueError):
            supplied_index = -1
        if supplied_index != expected_index:
            errors.append(f"Scene {expected_index} sai index ({supplied_index}).")

        scene = {"index": expected_index}
        for field in REQUIRED_SCENE_FIELDS:
            if field == "must_show":
                values = _must_show_values(raw_scene.get(field))
                if not 2 <= len(values) <= 5:
                    errors.append(f"Scene {expected_index} must_show phải có 2-5 anchor.")
                scene[field] = values
                continue
            value = _clean_text(raw_scene.get(field))
            minimum = 12 if field in {"action", "visual_anchor", "safe_visual_anchor"} else 3
            if len(value) < minimum:
                errors.append(f"Scene {expected_index} thiếu hoặc quá ngắn: {field}.")
            if _identity(value) in {_identity(item) for item in _GENERIC_VALUES}:
                errors.append(f"Scene {expected_index} dùng placeholder chung chung ở {field}.")
            scene[field] = value

        anchor_key = _identity(scene.get("visual_anchor"))
        if anchor_key and anchor_key in seen_anchors:
            errors.append(f"Scene {expected_index} trùng visual_anchor với scene trước.")
        seen_anchors.add(anchor_key)

        safe_anchor = scene.get("safe_visual_anchor", "")
        if _UNSAFE_SAFE_ANCHOR_PATTERN.search(safe_anchor):
            errors.append(f"Scene {expected_index} safe_visual_anchor vẫn chứa hành động rủi ro.")

        transcript = str(window.get("transcript") or "")
        source_anchors = extract_source_anchors(transcript)
        combined = " ".join(
            [
                scene.get("subject", ""),
                scene.get("setting", ""),
                scene.get("era", ""),
                scene.get("visual_anchor", ""),
                scene.get("safe_visual_anchor", ""),
                *scene.get("must_show", []),
            ]
        )
        combined_identity = _identity(combined)
        compact_combined_identity = combined_identity.replace(" ", "")
        retained = [
            anchor
            for anchor in source_anchors
            if _identity(anchor)
            and (
                _identity(anchor) in combined_identity
                or (
                    len(_identity(anchor).replace(" ", "")) >= 5
                    and _identity(anchor).replace(" ", "") in compact_combined_identity
                )
            )
        ]
        if source_anchors and not retained:
            errors.append(
                f"Scene {expected_index} làm mất anchor nguồn; cần giữ ít nhất một trong: "
                + ", ".join(source_anchors[:5])
            )
        scene["source_anchors"] = source_anchors
        scene["retained_source_anchors"] = retained

        if _CTA_PATTERN.search(transcript):
            visual_fields = " ".join(
                str(scene.get(key) or "")
                for key in ("subject", "action", "visual_anchor", "safe_visual_anchor")
            )
            if _CTA_PATTERN.search(visual_fields):
                errors.append(f"Scene {expected_index} còn CTA trong mô tả hình.")
        normalized_scenes.append(scene)

    if errors:
        raise SemanticPlanValidationError(errors)
    return {"story_context": story_context, "scenes": normalized_scenes}


def _style_summary(style: str) -> str:
    return _clean_text(
        style or "cinematic visual realism, photorealistic, natural detail",
        max_chars=420,
    )


def compile_muse_scene_plan(
    semantic_plan: dict,
    windows: list[dict],
    *,
    title: str,
    style: str,
) -> dict:
    """Compile validated semantic fields into prompts without another model call."""

    validated = validate_semantic_plan(semantic_plan, windows)
    compiled: list[dict] = []
    style_text = _style_summary(style)
    for window, semantic in zip(windows, validated["scenes"]):
        must_show = ", ".join(semantic["must_show"])
        prompt = _clean_text(
            "Create a coherent cinematic still for this video scene. "
            f"Subject: {semantic['subject']}. Action: {semantic['action']}. "
            f"Setting: {semantic['setting']}. Era or time: {semantic['era']}. "
            f"Visual anchor: {semantic['visual_anchor']}. Must show: {must_show}. "
            f"Composition: {semantic['composition']}. Lighting: {semantic['lighting']}. "
            f"Color palette: {semantic['color']}. Style: {style_text}. "
            "Photorealistic 16:9 widescreen image, no text, no captions, no logo.",
            max_chars=1800,
        )
        compiled.append(
            {
                **window,
                **semantic,
                "is_video": bool(window.get("is_video")),
                "media_type": "video" if window.get("is_video") else "image",
                "prompt": prompt,
                "style": style_text,
                "primary_reference_id": "",
                "reference_path": "",
            }
        )

    total = len(compiled)
    retained = sum(bool(scene.get("retained_source_anchors")) for scene in compiled)
    required = sum(bool(scene.get("source_anchors")) for scene in compiled)
    return {
        "planner_version": PLANNER_VERSION,
        "story_context": validated["story_context"],
        "visual_bible": {"title": _clean_text(title, max_chars=500), "style": style_text},
        "anchor_coverage": {
            "scene_count": total,
            "scenes_with_source_anchors": required,
            "scenes_retaining_source_anchors": retained,
            "ratio": round(retained / required, 4) if required else 1.0,
        },
        "scenes": compiled,
    }
