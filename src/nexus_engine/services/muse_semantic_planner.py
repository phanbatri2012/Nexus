"""Semantic scene planning and deterministic Muse prompt compilation."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Iterable


PLANNER_VERSION = "semantic_v3"
MUSE_PLANNER_BATCH_SIZE = 30
MUSE_PLANNER_BATCHES_PER_CHAT = 4
MUSE_PLANNER_EXECUTION_VERSION = "batched_v1"
MUSE_PLANNER_MAX_OVERVIEW_BATCHES = 48

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
_TERMINAL_TEXT_PATTERN = re.compile(
    r"(?i)(?<!\w)(?:text|caption|logo|watermark|chữ|phụ\s*đề|biểu\s*trưng)(?!\w)"
)
_SAFE_MILITARY_PHRASES_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"(?:đơn\s+vị|lực\s+lượng|sẵn\s+sàng|vị\s+trí|tinh\s+thần|ý\s+chí|nhiệm\s+vụ|thời\s+kỳ|tư\s+thế)\s+chiến\s+đấu|"
    r"(?:hướng|mũi|kế\s+hoạch|phương\s+án|chuẩn\s+bị|sa\s+bàn|bản\s+đồ|mục\s+tiêu|sơ\s+đồ|thế\s+trận)\s+(?:tấn\s+công|tiến\s+công)|"
    r"chiến\s+đấu\s+(?:lâu\s+năm|kiên\s+cường|bền\s+bỉ)|"
    r"nổ\s+súng\s+(?:báo\s+hiệu|hiệu\s+lệnh)"
    r")\b"
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
        if key in {
            "hay", "nhung", "va", "tai", "khi", "neu", "day", "do",
            "viet nam", "nam", "bac", "trung",
        }:
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


def _evenly_sample_indices(total: int, limit: int) -> list[int]:
    if total <= 0 or limit <= 0:
        return []
    if total <= limit:
        return list(range(total))
    if limit == 1:
        return [0]
    return sorted(
        {
            round(position * (total - 1) / (limit - 1))
            for position in range(limit)
        }
    )


def build_overview_batch_summaries(
    windows: list[dict],
    *,
    batch_size: int = MUSE_PLANNER_BATCH_SIZE,
    max_batches: int = MUSE_PLANNER_MAX_OVERVIEW_BATCHES,
) -> list[dict]:
    """Build bounded, deterministic summaries while preserving the first/last batch."""

    batches = [windows[index : index + batch_size] for index in range(0, len(windows), batch_size)]
    summaries: list[dict] = []
    for batch_index in _evenly_sample_indices(len(batches), max_batches):
        batch = batches[batch_index]
        if not batch:
            continue
        transcript_start = _clean_text(batch[0].get("transcript"), max_chars=520)
        transcript_end = _clean_text(batch[-1].get("transcript"), max_chars=520)
        anchors = extract_source_anchors(f"{transcript_start}\n{transcript_end}")[:12]
        summaries.append(
            {
                "batch_index": batch_index,
                "scene_start_index": int(batch[0]["index"]),
                "scene_end_index": int(batch[-1]["index"]),
                "start": round(float(batch[0]["start"]), 3),
                "end": round(float(batch[-1]["end"]), 3),
                "transcript_start": transcript_start,
                "transcript_end": transcript_end,
                "source_anchors": anchors,
            }
        )
    return summaries


def build_overview_prompt(*, title: str, style: str, windows: list[dict]) -> str:
    summaries = build_overview_batch_summaries(windows)
    schema = {
        "story_context": "12-900 characters describing the global visual story",
        "continuity_rules": ["3-12 concrete continuity rules, each at most 180 characters"],
        "recurring_anchors": ["up to 24 recurring people, places, periods, or objects"],
    }
    return (
        "Bạn là visual planner. Hãy tạo global overview ngắn để nhiều batch scene độc lập vẫn "
        "giữ đúng mạch chuyện và continuity. Chỉ trả về đúng một JSON object hợp lệ; không "
        "markdown, không giải thích và không thêm nội dung ngoài JSON.\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "- story_context dài 12-900 ký tự, cụ thể theo nguồn.\n"
        "- continuity_rules có 3-12 chuỗi cụ thể, mỗi chuỗi tối đa 180 ký tự.\n"
        "- recurring_anchors có tối đa 24 chuỗi, giữ tên riêng, địa danh, mốc thời gian và vật thể.\n"
        "- Cấm placeholder như unknown, n/a, generic scene hoặc dấu ba chấm thay nội dung.\n"
        "- Không sáng tác sự kiện, nhân vật hoặc mốc thời gian không có trong source summaries.\n\n"
        f"TITLE: {_clean_text(title, max_chars=500)}\n"
        f"STYLE: {_clean_text(style, max_chars=800)}\n"
        f"TOTAL_SCENES: {len(windows)}\n"
        f"TOTAL_BATCHES: {(len(windows) + MUSE_PLANNER_BATCH_SIZE - 1) // MUSE_PLANNER_BATCH_SIZE}\n"
        "SAMPLED_BATCH_SUMMARIES_JSON:\n"
        f"{json.dumps(summaries, ensure_ascii=False)}\n\n"
        "OUTPUT_SCHEMA_EXAMPLE:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def _compact_semantic_scene(scene: dict) -> dict:
    return {
        key: scene.get(key)
        for key in ("index", *REQUIRED_SCENE_FIELDS)
        if key in scene
    }


def build_batch_prompt(
    *,
    title: str,
    style: str,
    overview: dict,
    windows: list[dict],
    batch_index: int,
    batch_count: int,
    previous_scenes: list[dict] | None = None,
    lookahead_windows: list[dict] | None = None,
    recent_visual_anchors: list[str] | None = None,
    needs_terminal_frame: bool = False,
    terminal_index: int | None = None,
) -> str:
    if not windows:
        raise ValueError("Batch planner requires at least one scene window.")
    scene_inputs = [
        {
            "index": int(window["index"]),
            "start": round(float(window["start"]), 3),
            "end": round(float(window["end"]), 3),
            "transcript": _clean_text(window.get("transcript"), max_chars=1800),
        }
        for window in windows
    ]
    previous = [_compact_semantic_scene(scene) for scene in (previous_scenes or [])[-2:]]
    lookahead = [
        {
            "index": int(window["index"]),
            "start": round(float(window["start"]), 3),
            "end": round(float(window["end"]), 3),
            "transcript": _clean_text(window.get("transcript"), max_chars=900),
        }
        for window in (lookahead_windows or [])[:2]
    ]
    expected_indices = [item["index"] for item in scene_inputs]
    terminal_index = (
        int(terminal_index)
        if terminal_index is not None
        else int(scene_inputs[-1]["index"]) + 1
    )
    schema: dict[str, object] = {
        "scenes": [
            {
                "index": expected_indices[0],
                "subject": "specific people/group/object",
                "action": "one filmable action",
                "setting": "specific source-grounded environment",
                "era": "specific source-grounded time context",
                "visual_anchor": "concrete unique image",
                "safe_visual_anchor": "same meaning without active violence, injury, death, or gore",
                "must_show": ["2-5 indispensable visible anchors"],
                "composition": "camera framing and spatial arrangement",
                "lighting": "specific lighting",
                "color": "specific palette",
                "narrative_role": "opening|setup|development|demonstration|turning_point|resolution|conclusion",
            }
        ]
    }
    if needs_terminal_frame:
        schema["terminal_frame"] = {
            **schema["scenes"][0],
            "index": terminal_index,
            "narrative_role": "conclusion",
        }
    start_time = round(float(windows[0]["start"]), 3)
    end_time = round(float(windows[-1]["end"]), 3)
    return (
        "Bạn là visual planner cho một batch trong video dài. Prompt này tự chứa đủ context. "
        "Chỉ trả về đúng một JSON object hợp lệ, không markdown, không giải thích.\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "- Chỉ lập plan cho TARGET_SCENES; PREVIOUS_SCENES và LOOKAHEAD chỉ dùng làm continuity.\n"
        f"- Trả đúng {len(scene_inputs)} scene với chính xác các global index: {expected_indices}.\n"
        "- Bám transcript từng scene; giữ source anchors; setting/era cụ thể; không placeholder.\n"
        "- visual_anchor phải quay/chụp được và khác mọi RECENT_VISUAL_ANCHORS.\n"
        "- safe_visual_anchor giữ chủ thể/địa điểm/thời gian nhưng không có bạo lực, thương tích hay gore.\n"
        "- must_show có 2-5 anchor nhìn thấy được. Bỏ CTA và không đưa chữ/logo vào hình.\n"
        + (
            f"- Đây là batch cuối và scene cuối là video: bắt buộc terminal_frame index={terminal_index}, "
            "khác action/visual_anchor scene cuối, narrative_role=conclusion, không chữ/logo/CTA.\n"
            if needs_terminal_frame
            else "- Không trả terminal_frame trong batch này.\n"
        )
        + f"\nTITLE: {_clean_text(title, max_chars=500)}\n"
        f"STYLE: {_clean_text(style, max_chars=800)}\n"
        f"BATCH_POSITION: {batch_index + 1}/{batch_count}\n"
        f"BATCH_TIME_RANGE: {start_time}-{end_time}\n"
        f"GLOBAL_OVERVIEW_JSON: {json.dumps(overview, ensure_ascii=False)}\n"
        f"PREVIOUS_SCENES_JSON: {json.dumps(previous, ensure_ascii=False)}\n"
        f"LOOKAHEAD_JSON: {json.dumps(lookahead, ensure_ascii=False)}\n"
        f"RECENT_VISUAL_ANCHORS_JSON: {json.dumps((recent_visual_anchors or [])[-12:], ensure_ascii=False)}\n"
        f"TARGET_SCENES_JSON: {json.dumps(scene_inputs, ensure_ascii=False)}\n\n"
        "OUTPUT_SCHEMA_EXAMPLE:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


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
    needs_terminal_frame = bool(windows and windows[-1].get("is_video"))
    if needs_terminal_frame:
        schema["terminal_frame"] = {
            "index": len(scene_inputs),
            "subject": "same grounded subject at the end of the final transition",
            "action": "a visibly different completed end state",
            "setting": "same source-grounded environment and continuity",
            "era": "same specific source-grounded time context",
            "visual_anchor": "distinct final image reached after the last scene image",
            "safe_visual_anchor": "same final meaning without active violence, injury, death, or gore",
            "must_show": ["2-5 indispensable visible anchors"],
            "composition": "camera framing and spatial arrangement",
            "lighting": "specific lighting",
            "color": "specific palette",
            "narrative_role": "conclusion",
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
        + (
            "- Vì scene cuối là video, bắt buộc thêm terminal_frame ở top level. Dùng cùng schema "
            f"với scene, index={len(scene_inputs)}, bám source anchors của scene cuối và mô tả trạng "
            "thái kết thúc ngay sau scene cuối. terminal_frame phải khác visual_anchor/action của scene "
            "cuối, giữ continuity, narrative_role=conclusion và tuyệt đối không CTA, chữ, caption hoặc logo.\n"
            if needs_terminal_frame
            else "- Vì scene cuối là ảnh tĩnh, không trả terminal_frame.\n"
        )
        +
        f"- Trả đúng {len(scene_inputs)} scene, index liên tục từ 0.\n\n"
        f"TITLE: {_clean_text(title, max_chars=500)}\n"
        f"STYLE: {_clean_text(style, max_chars=800)}\n"
        "SCENE_INPUTS_JSON:\n"
        f"{json.dumps(scene_inputs, ensure_ascii=False)}\n\n"
        "OUTPUT_SCHEMA_EXAMPLE:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


def build_repair_prompt(
    errors: Iterable[str],
    *,
    scene_count: int,
    needs_terminal_frame: bool = False,
) -> str:
    concise_errors = [_clean_text(error, max_chars=240) for error in errors]
    return (
        "JSON kế hoạch vừa trả về không đạt validation. Hãy sửa TOÀN BỘ JSON trước đó và chỉ "
        "trả về JSON object hợp lệ, không markdown, không giải thích. "
        f"Phải có đúng {scene_count} scene theo đúng index. "
        + (
            f"Phải có terminal_frame top-level với index={scene_count}. "
            if needs_terminal_frame
            else "Không được có terminal_frame. "
        )
        + "Các lỗi cần sửa: "
        + json.dumps(concise_errors, ensure_ascii=False)
    )


def build_overview_repair_prompt(errors: Iterable[str]) -> str:
    concise_errors = [_clean_text(error, max_chars=240) for error in errors]
    return (
        "Global overview vừa trả về không đạt validation. Hãy sửa TOÀN BỘ JSON trước đó. "
        "Chỉ trả về đúng một JSON object, không markdown, không giải thích hoặc nội dung ngoài JSON. "
        "Object chỉ có story_context, continuity_rules và recurring_anchors. Các lỗi cần sửa: "
        + json.dumps(concise_errors, ensure_ascii=False)
    )


def build_batch_repair_prompt(
    errors: Iterable[str],
    *,
    expected_indices: Iterable[int],
    needs_terminal_frame: bool,
    terminal_index: int,
) -> str:
    indices = [int(index) for index in expected_indices]
    concise_errors = [_clean_text(error, max_chars=240) for error in errors]
    return (
        "Batch JSON vừa trả về không đạt validation. Hãy sửa TOÀN BỘ JSON của batch trước đó "
        "và chỉ trả về đúng một JSON object, không markdown, không giải thích. "
        f"Phải có đúng {len(indices)} scene với global index chính xác {indices}. "
        + (
            f"Phải có terminal_frame top-level với index={int(terminal_index)}. "
            if needs_terminal_frame
            else "Không được có terminal_frame. "
        )
        + "Không trả story_context hay field top-level khác. Các lỗi cần sửa: "
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


def parse_strict_planner_response(response_text: str) -> dict:
    """Parse batched-planner output and reject markdown or prose around the JSON."""

    raw = str(response_text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        raise SemanticPlanValidationError(
            ["Phản hồi planner phải chỉ chứa một JSON object, không có nội dung bên ngoài."]
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SemanticPlanValidationError(
            [f"Phản hồi planner không phải JSON object hợp lệ: {exc.msg}."]
        ) from exc
    if not isinstance(payload, dict):
        raise SemanticPlanValidationError(["Planner payload phải là JSON object."])
    return payload


_OVERVIEW_PLACEHOLDER_PATTERN = re.compile(
    r"(?i)(?:\bunknown\b|\bn/?a\b|\bnone\b|\bgeneric\s+scene\b|"
    r"\bplaceholder\b|\.\.\.|…{2,})"
)


def _validate_overview_list(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
    item_max_chars: int,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if not isinstance(value, list):
        return [], [f"{field} phải là array."]
    if not minimum <= len(value) <= maximum:
        errors.append(f"{field} phải có {minimum}-{maximum} mục.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for position, item in enumerate(value):
        text = _clean_text(item, max_chars=max(item_max_chars * 4, 720))
        if len(text) < 2:
            errors.append(f"{field}[{position}] bị thiếu hoặc quá ngắn.")
        if len(text) > item_max_chars:
            errors.append(f"{field}[{position}] vượt {item_max_chars} ký tự.")
        if _OVERVIEW_PLACEHOLDER_PATTERN.search(text):
            errors.append(f"{field}[{position}] chứa placeholder.")
        key = _identity(text)
        if key and key in seen:
            errors.append(f"{field}[{position}] bị trùng.")
        if key:
            seen.add(key)
        cleaned.append(text)
    return cleaned, errors


def validate_global_overview(payload: dict) -> dict:
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise SemanticPlanValidationError(["Global overview phải là JSON object."])
    allowed_keys = {"story_context", "continuity_rules", "recurring_anchors"}
    extra_keys = sorted(str(key) for key in payload if key not in allowed_keys)
    if extra_keys:
        errors.append("Global overview có field không hợp lệ: " + ", ".join(extra_keys))

    story_context = _clean_text(payload.get("story_context"), max_chars=3600)
    if not 12 <= len(story_context) <= 900:
        errors.append("story_context phải dài 12-900 ký tự.")
    if _OVERVIEW_PLACEHOLDER_PATTERN.search(story_context):
        errors.append("story_context chứa placeholder.")
    continuity_rules, continuity_errors = _validate_overview_list(
        payload.get("continuity_rules"),
        field="continuity_rules",
        minimum=3,
        maximum=12,
        item_max_chars=180,
    )
    recurring_anchors, anchor_errors = _validate_overview_list(
        payload.get("recurring_anchors"),
        field="recurring_anchors",
        minimum=0,
        maximum=24,
        item_max_chars=180,
    )
    errors.extend(continuity_errors)
    errors.extend(anchor_errors)
    if errors:
        raise SemanticPlanValidationError(errors)
    return {
        "story_context": story_context,
        "continuity_rules": continuity_rules,
        "recurring_anchors": recurring_anchors,
    }


def _must_show_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_clean_text(item, max_chars=120) for item in value]
    return [item for item in cleaned if item]


def _anchor_matches(anchor: str, combined_identity: str, compact_combined_identity: str) -> bool:
    key = _identity(anchor)
    if not key:
        return False
    if key in combined_identity:
        return True
    compact_key = key.replace(" ", "")
    if len(compact_key) >= 4 and compact_key in compact_combined_identity:
        return True
    words = [w for w in key.split() if len(w) >= 2]
    if len(words) >= 2:
        matching_words = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", combined_identity))
        if matching_words >= 2 or (matching_words >= 1 and len(words) == 2 and len(compact_key) >= 4):
            return True
    return False


def _normalize_semantic_entry(
    raw_scene: object,
    *,
    window: dict,
    expected_index: int,
    seen_anchors: set[str],
    label: str,
    forbid_text_elements: bool = False,
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw_scene, dict):
        return None, [f"{label} không phải object."]
    try:
        supplied_index = int(raw_scene.get("index", -1))
    except (TypeError, ValueError):
        supplied_index = -1
    if supplied_index != expected_index:
        errors.append(f"{label} sai index ({supplied_index}).")

    scene = {"index": expected_index}
    generic_values = {_identity(item) for item in _GENERIC_VALUES}
    for field in REQUIRED_SCENE_FIELDS:
        if field == "must_show":
            values = _must_show_values(raw_scene.get(field))
            if not 2 <= len(values) <= 5:
                errors.append(f"{label} must_show phải có 2-5 anchor.")
            scene[field] = values
            continue
        value = _clean_text(raw_scene.get(field))
        minimum = 12 if field in {"action", "visual_anchor", "safe_visual_anchor"} else 3
        if len(value) < minimum:
            errors.append(f"{label} thiếu hoặc quá ngắn: {field}.")
        if _identity(value) in generic_values:
            errors.append(f"{label} dùng placeholder chung chung ở {field}.")
        scene[field] = value

    anchor_key = _identity(scene.get("visual_anchor"))
    if anchor_key and anchor_key in seen_anchors:
        errors.append(f"{label} trùng visual_anchor với scene trước.")
    if anchor_key:
        seen_anchors.add(anchor_key)

    safe_anchor = scene.get("safe_visual_anchor", "")
    inspected_safe_anchor = _SAFE_MILITARY_PHRASES_PATTERN.sub(" ", safe_anchor)
    if _UNSAFE_SAFE_ANCHOR_PATTERN.search(inspected_safe_anchor):
        errors.append(f"{label} safe_visual_anchor vẫn chứa hành động rủi ro.")

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
        if _anchor_matches(anchor, combined_identity, compact_combined_identity)
    ]
    if source_anchors and not retained:
        errors.append(
            f"{label} làm mất anchor nguồn; cần giữ ít nhất một trong: "
            + ", ".join(source_anchors[:5])
        )
    scene["source_anchors"] = source_anchors
    scene["retained_source_anchors"] = retained

    visual_fields = " ".join(
        [
            *(str(scene.get(key) or "") for key in REQUIRED_SCENE_FIELDS if key != "must_show"),
            *scene.get("must_show", []),
        ]
    )
    if (_CTA_PATTERN.search(transcript) or forbid_text_elements) and _CTA_PATTERN.search(
        visual_fields
    ):
        errors.append(f"{label} còn CTA trong mô tả hình.")
    if forbid_text_elements and _TERMINAL_TEXT_PATTERN.search(visual_fields):
        errors.append(f"{label} không được chứa chữ, caption hoặc logo.")
    return scene, errors


def validate_semantic_batch(
    payload: dict,
    windows: list[dict],
    *,
    previous_visual_anchors: Iterable[str] = (),
    needs_terminal_frame: bool,
    terminal_index: int,
) -> dict:
    """Validate one global-index batch without inferring whole-video terminal policy."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        raise SemanticPlanValidationError(["Batch planner payload phải là JSON object."])
    allowed_keys = {"scenes"}
    if needs_terminal_frame:
        allowed_keys.add("terminal_frame")
    extra_keys = sorted(str(key) for key in payload if key not in allowed_keys)
    if extra_keys:
        errors.append("Batch planner có field top-level không hợp lệ: " + ", ".join(extra_keys))

    supplied = payload.get("scenes")
    if not isinstance(supplied, list) or len(supplied) != len(windows):
        supplied_count = len(supplied) if isinstance(supplied, list) else 0
        errors.append(f"Số scene batch sai: nhận {supplied_count}, cần {len(windows)}.")
        raise SemanticPlanValidationError(errors)

    seen_anchors = {
        key
        for key in (_identity(anchor) for anchor in previous_visual_anchors)
        if key
    }
    normalized_scenes: list[dict] = []
    for window, raw_scene in zip(windows, supplied):
        expected_index = int(window["index"])
        scene, scene_errors = _normalize_semantic_entry(
            raw_scene,
            window=window,
            expected_index=expected_index,
            seen_anchors=seen_anchors,
            label=f"Scene {expected_index}",
        )
        errors.extend(scene_errors)
        if scene is not None:
            normalized_scenes.append(scene)

    terminal_frame: dict | None = None
    raw_terminal = payload.get("terminal_frame")
    if needs_terminal_frame:
        terminal_window = windows[-1] if windows else {"transcript": ""}
        terminal_frame, terminal_errors = _normalize_semantic_entry(
            raw_terminal,
            window=terminal_window,
            expected_index=int(terminal_index),
            seen_anchors=seen_anchors,
            label="Terminal frame",
            forbid_text_elements=True,
        )
        errors.extend(terminal_errors)
        if terminal_frame is not None:
            if _identity(terminal_frame.get("narrative_role")) != "conclusion":
                errors.append("Terminal frame narrative_role phải là conclusion.")
            if normalized_scenes:
                final_scene = normalized_scenes[-1]
                if _identity(terminal_frame.get("action")) == _identity(final_scene.get("action")):
                    errors.append("Terminal frame action phải là trạng thái kết thúc khác scene cuối.")
    elif raw_terminal not in (None, {}):
        errors.append("Chỉ batch cuối có scene video mới được trả terminal_frame.")

    if errors:
        raise SemanticPlanValidationError(errors)
    result: dict[str, object] = {"scenes": normalized_scenes}
    if terminal_frame is not None:
        result["terminal_frame"] = terminal_frame
    return result


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
        scene, scene_errors = _normalize_semantic_entry(
            raw_scene,
            window=window,
            expected_index=expected_index,
            seen_anchors=seen_anchors,
            label=f"Scene {expected_index}",
        )
        errors.extend(scene_errors)
        if scene is not None:
            normalized_scenes.append(scene)

    needs_terminal_frame = bool(windows and windows[-1].get("is_video"))
    terminal_frame: dict | None = None
    raw_terminal = payload.get("terminal_frame")
    if needs_terminal_frame:
        terminal_frame, terminal_errors = _normalize_semantic_entry(
            raw_terminal,
            window=windows[-1],
            expected_index=len(windows),
            seen_anchors=seen_anchors,
            label="Terminal frame",
            forbid_text_elements=True,
        )
        errors.extend(terminal_errors)
        if terminal_frame is not None:
            if _identity(terminal_frame.get("narrative_role")) != "conclusion":
                errors.append("Terminal frame narrative_role phải là conclusion.")
            if normalized_scenes:
                final_scene = normalized_scenes[-1]
                if _identity(terminal_frame.get("action")) == _identity(final_scene.get("action")):
                    errors.append("Terminal frame action phải là trạng thái kết thúc khác scene cuối.")
    elif raw_terminal not in (None, {}):
        errors.append("Không được có terminal_frame khi scene cuối là ảnh tĩnh.")

    if errors:
        raise SemanticPlanValidationError(errors)
    result = {"story_context": story_context, "scenes": normalized_scenes}
    if terminal_frame is not None:
        result["terminal_frame"] = terminal_frame
    return result


def _style_summary(style: str) -> str:
    return _clean_text(
        style or "cinematic visual realism, photorealistic, natural detail",
        max_chars=420,
    )


def _compile_still_prompt(
    semantic: dict,
    *,
    style_text: str,
    terminal: bool = False,
    aspect_ratio: str = "16:9",
) -> str:
    must_show = ", ".join(semantic["must_show"])
    opening = (
        "Create the distinct final still reached after the last video transition. "
        if terminal
        else "Create a coherent cinematic still for this video scene. "
    )
    framing = "vertical portrait composition" if aspect_ratio == "9:16" else "widescreen composition"
    return _clean_text(
        opening
        + f"Subject: {semantic['subject']}. Action: {semantic['action']}. "
        f"Setting: {semantic['setting']}. Era or time: {semantic['era']}. "
        f"Visual anchor: {semantic['visual_anchor']}. Must show: {must_show}. "
        f"Composition: {semantic['composition']}. Lighting: {semantic['lighting']}. "
        f"Color palette: {semantic['color']}. Style: {style_text}. "
        f"Photorealistic {aspect_ratio} {framing}, no text, no captions, no logo.",
        max_chars=1800,
    )


def compile_muse_scene_plan(
    semantic_plan: dict,
    windows: list[dict],
    *,
    title: str,
    style: str,
    aspect_ratio: str = "16:9",
) -> dict:
    """Compile validated semantic fields into prompts without another model call."""

    validated = validate_semantic_plan(semantic_plan, windows)
    compiled: list[dict] = []
    style_text = _style_summary(style)
    for window, semantic in zip(windows, validated["scenes"]):
        prompt = _compile_still_prompt(
            semantic,
            style_text=style_text,
            aspect_ratio=aspect_ratio,
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
    result = {
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
    terminal_frame = validated.get("terminal_frame")
    if isinstance(terminal_frame, dict):
        result["terminal_frame"] = {
            **terminal_frame,
            "is_video": False,
            "media_type": "image",
            "prompt": _compile_still_prompt(
                terminal_frame,
                style_text=style_text,
                terminal=True,
                aspect_ratio=aspect_ratio,
            ),
            "style": style_text,
            "primary_reference_id": "",
            "reference_path": "",
        }
    return result
