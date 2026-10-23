"""Deterministic, content-preserving quality gate for generated scripts."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Callable


QUALITY_GATE_VERSION = 2
REQUIRED_AUDIO_SECTIONS = ("INTRO", "BODY", "OUTRO")
DUPLICATE_PARAGRAPH_MIN_WORDS = 25
MIN_AUDIO_SCRIPT_WORDS = 15
MIN_SECTION_WORDS = {"INTRO": 3, "BODY": 5, "OUTRO": 3}
WORDS_PER_MINUTE_SLOW = 160
WORDS_PER_MINUTE_FAST = 300

SECTION_PATTERN = re.compile(
    r"(?ms)^### \[([^\]]+)\][ \t]*\r?\n(.*?)(?=^### \[[^\]]+\][ \t]*$|\Z)"
)
REQUIRED_SECTION_PATTERN = re.compile(
    r"(?ms)^(?P<header>### \[(?P<name>INTRO|BODY|OUTRO)\][ \t]*\r?\n)"
    r"(?P<body>.*?)(?=^### \[[^\]]+\][ \t]*$|\Z)"
)

SYSTEM_ERROR_PATTERNS = (
    re.compile(r"this response couldn['’]?t load", re.IGNORECASE),
    re.compile(r"this response could not load", re.IGNORECASE),
    re.compile(r"something went wrong", re.IGNORECASE),
    re.compile(r"an error occurred while generating", re.IGNORECASE),
    re.compile(r"there was an error generating a response", re.IGNORECASE),
    re.compile(r"a network error occurred", re.IGNORECASE),
    re.compile(r"the server had an error while processing", re.IGNORECASE),
    re.compile(r"failed to fetch", re.IGNORECASE),
    re.compile(r"conversation not found", re.IGNORECASE),
)

EDITORIAL_PREFIXES = (
    "bổ sung ",
    "bỏ ",
    "chia đoạn",
    "chuyển ý",
    "dẫn dắt ",
    "đào sâu ",
    "giải thích ",
    "giảm ",
    "giữ nhịp",
    "khai thác ",
    "kết nối ",
    "làm rõ ",
    "mở rộng ",
    "nhấn mạnh ",
    "nêu bật ",
    "rút gọn ",
    "sửa ",
    "tăng nhịp",
    "tách ",
    "thêm ",
    "triển khai ",
    "viết lại ",
    "mở đầu bằng ",
    "giảm tiết lộ ",
    "làm rõ mốc ",
    "refined ",
    "refining ",
    "updated ",
    "updating ",
    "polished ",
    "polishing ",
    "edited ",
    "editing ",
    "revised ",
    "revising ",
    "created ",
    "creating ",
    "rewrote ",
    "rewriting ",
    "added ",
    "adding ",
    "summarized ",
    "summarizing ",
    "expanded ",
    "expanding ",
)
STRUCTURAL_NARRATIVE_LABEL_PATTERN = re.compile(
    r"^(?:intro|body|outro|giới thiệu|dẫn nhập|lời dẫn|"
    r"mở\s+(?:đầu|bài)|nội\s+dung\s+chính|thân\s+bài|"
    r"kết\s+(?:luận|bài|thúc)|lời\s+kết|"
    r"thu\s+gọn|mở\s+rộng|"
    r"(?:mở\s+(?:đầu|bài)|kết\s+(?:thúc|bài|luận)|intro|body|outro|thân\s+bài)\s+(?:video|kịch\s+bản|bài\s+viết)(?:\s+.*)?|"
    r"phần\s+(?:\d+|intro|body|outro|giới thiệu|dẫn nhập|"
    r"mở\s+(?:đầu|bài)|nội\s+dung\s+chính|thân\s+bài|"
    r"kết\s+(?:luận|bài|thúc)))\s*:?\s*$",
    flags=re.IGNORECASE,
)

HIGH_CONFIDENCE_EDITORIAL_PATTERN = re.compile(
    r"^(?:"
    r"(?:dưới đây|sau đây)\s+là\s+(?:kịch\s*bản|nội\s*dung|phiên\s*bản)|"
    r"(?:tôi|mình)\s+đã\s+(?:viết|sửa|chỉnh|hoàn\s+thành|cập\s+nhật)\s+"
    r"(?:kịch\s*bản|nội\s*dung|phần|đoạn)|"
    r"(?:ghi\s*chú|lưu\s*ý|editor(?:'s)?\s+note|note)\s*:|"
    r"(?:hãy\s+)?(?:bổ\s*sung|thêm|xóa|bỏ|sửa|viết\s*lại|rút\s*gọn|"
    r"mở\s*rộng|kết\s*nối|nhấn\s*mạnh|làm\s*rõ|triển\s*khai)\s+"
    r"(?:đoạn\s+(?:văn|chuyển|mở|kết|nội\s*dung)|"
    r"câu\s+(?:văn|chuyển|dẫn|mở|kết)|"
    r"phần\s+(?:mở|kết|nội\s*dung|thân)|nội\s*dung|kịch\s*bản|"
    r"luận\s*điểm|chi\s*tiết|mốc(?:\s+thời\s+gian)?|nhịp\s+kể)\b|"
    r"(?:hãy\s+)?(?:chia|tách)\s+đoạn\b|"
    r"(?:please\s+)?(?:add|remove|rewrite|expand|shorten|edit|revise)\s+"
    r"(?:the\s+)?(?:paragraph|sentence|section|script|content|transition)\b"
    r")",
    flags=re.IGNORECASE,
)

SELF_REFERENCE_PATTERNS = (
    re.compile(
        r"\b(?:theo|trong|ở)\s+(?:nội\s+dung\s+)?(?:bản\s+)?kịch\s*bản(?:\s+(?:này|gốc|trên|dưới))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ở|trong|như)\s+(?:phần|đoạn)\s+(?:trên|dưới|trước|sau)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:theo\s+yêu\s+cầu\s+(?:trong|của)\s+(?:prompt|người\s+dùng|bạn|hệ\s+thống|đề\s+bài)|(?:viết|tạo|sinh)\s+theo\s+yêu\s+cầu|dữ\s+liệu\s+được\s+cung\s+cấp|prompt)\b",
        re.IGNORECASE,
    ),
)
HIGH_CONFIDENCE_NARRATION_REFERENCE_PATTERN = re.compile(
    r"\b(?P<prefix>Theo\s+(?:nội\s+dung|lời\s+kể|lời\s+thuật|ghi\s+chép|thông\s+tin|chi\s+tiết|tài\s+liệu)\s+"
    r"(?:được\s+)?(?:thuật\s+lại|kể\s+lại|ghi\s+lại|nhắc\s+đến|nhắc\s+tới))\s+"
    r"(?:ở|trong)\s+(?:nội\s+dung\s+)?(?:bản\s+)?kịch\s*bản"
    r"(?:\s+(?:này|gốc|trên|dưới))?\b",
    re.IGNORECASE,
)
HIGH_CONFIDENCE_EMBEDDED_SCRIPT_REFERENCE_PATTERN = re.compile(
    r"\b(?P<lead>(?:Một\s+)?(?:lời\s+kể|chi\s+tiết|thông\s+tin|tài\s+liệu|nguồn\s+tin|ghi\s+chép)\s+khác)\s+"
    r"(?:được\s+)?(?:nhắc\s+đến|nhắc\s+tới|đề\s+cập|ghi\s+nhận|nêu|mô\s+tả|thuật\s+lại|kể\s+lại|ghi\s+lại)\s+"
    r"(?:ở|trong)\s+(?:nội\s+dung\s+)?(?:bản\s+)?kịch\s*bản"
    r"(?:\s+(?:này|gốc|trên|dưới))?(?P<punct>,\s*|\s+)",
    re.IGNORECASE,
)
HIGH_CONFIDENCE_SOURCE_REFERENCE_PATTERN = re.compile(
    r"\b(?P<source>Theo\s+(?:một\s+số\s+)?(?:thông\s+tin|số\s+liệu|dữ\s+liệu|"
    r"tài\s+liệu|ghi\s+chép|chi\s+tiết|nội\s+dung|lời\s+kể|nguồn\s+tin)(?:(?![,.;:!?]).){0,120}?)\s+"
    r"(?:được\s+)?(?:đề\s+cập|ghi\s+nhận|trình\s+bày|nêu|mô\s+tả|thuật\s+lại|kể\s+lại|nhắc\s+đến|nhắc\s+tới)\s+"
    r"(?:ở|trong)\s+(?:nội\s+dung\s+)?(?:bản\s+)?kịch\s*bản"
    r"(?:\s+(?:này|gốc|trên|dưới))?\b",
    re.IGNORECASE,
)
HIGH_CONFIDENCE_SECTION_REFERENCE_PATTERN = re.compile(
    r"(?P<boundary>^|(?<=[.!?…])\s+)"
    r"(?:Như\s+)?(?:(?:đã|sẽ)\s+)?(?:được\s+)?"
    r"(?:đề\s+cập|trình\s+bày|nêu|mô\s+tả|phân\s+tích|giải\s+thích|nhắc\s+đến)\s+"
    r"(?:ở|trong)\s+(?:phần|đoạn)\s+(?P<position>trên|dưới|trước|sau)"
    r"\s*[,;:]\s*",
    re.IGNORECASE | re.MULTILINE,
)
HIGH_CONFIDENCE_SCRIPT_REFERENCE_PATTERN = re.compile(
    r"(?P<boundary>^|(?<=[.!?…])\s+)"
    r"(?:Như\s+)?(?:(?:đã|sẽ)\s+)?(?:được\s+)?"
    r"(?:đề\s+cập|trình\s+bày|nêu|mô\s+tả|phân\s+tích|giải\s+thích|nhắc\s+đến)\s+"
    r"(?:ở|trong)\s+(?:nội\s+dung\s+)?(?:bản\s+)?kịch\s*bản"
    r"(?:\s+(?:này|gốc|trên|dưới))?\s*[,;:]\s*"
    r"(?P<next>[^\W\d_])",
    re.IGNORECASE | re.MULTILINE | re.UNICODE,
)
HIGH_CONFIDENCE_REQUIREMENT_REFERENCE_PATTERN = re.compile(
    r"(?P<boundary>^|(?<=[.!?…])\s+)"
    r"(?:Theo\s+(?:yêu\s+cầu(?:\s+(?:trong|của)\s+(?:prompt|người\s+dùng))?|"
    r"prompt(?:\s+này)?))\s*[,;:]\s*(?P<next>[^\W\d_])",
    re.IGNORECASE | re.MULTILINE | re.UNICODE,
)
HIGH_CONFIDENCE_PROVIDED_DATA_PATTERN = re.compile(
    r"\b(?P<label>dữ\s+liệu|thông\s+tin)\s+được\s+cung\s+cấp\b",
    re.IGNORECASE,
)
HIGH_CONFIDENCE_PROMPT_INSTRUCTION_PATTERN = re.compile(
    r"(?P<boundary>^|(?<=[.!?…])\s+)"
    r"(?:Prompt(?:\s+này)?|Yêu\s+cầu\s+trong\s+prompt)\s+"
    r"(?:yêu\s+cầu|đề\s+nghị|chỉ\s+định|hướng\s+dẫn)\b"
    r"[^.!?…\n]*(?:[.!?…]+(?=\s|$)|$)\s*",
    re.IGNORECASE | re.MULTILINE,
)
PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|PLACEHOLDER)\b", re.IGNORECASE),
    re.compile(r"\{\{[^{}\n]+\}\}"),
    re.compile(r"\[(?:điền|thêm|insert|replace)[^\]\n]*\]", re.IGNORECASE),
)


def looks_like_editorial_artifact(value: str) -> bool:
    """Recognize a writing instruction or a standalone structural label."""
    normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return bool(
        HIGH_CONFIDENCE_EDITORIAL_PATTERN.match(normalized)
        or STRUCTURAL_NARRATIVE_LABEL_PATTERN.fullmatch(normalized)
    )


def extract_audio_sections(script_text: str) -> dict[str, str]:
    """Return the first occurrence of each narrative section for compatibility."""
    sections: dict[str, str] = {}
    for match in SECTION_PATTERN.finditer(script_text or ""):
        name = match.group(1).strip().upper()
        if name in REQUIRED_AUDIO_SECTIONS and name not in sections:
            content = re.sub(r"\n[ \t]*\n+", "\n\n", match.group(2)).strip()
            sections[name] = content
    return sections


def get_audio_script(script_text: str) -> str:
    sections = extract_audio_sections(script_text)
    return "\n\n".join(
        sections[name] for name in REQUIRED_AUDIO_SECTIONS if sections.get(name)
    ).strip()


def get_audio_script_hash(script_text: str) -> str:
    audio_script = get_audio_script(script_text)
    normalized = "\n".join(
        line.rstrip() for line in audio_script.replace("\r\n", "\n").splitlines()
    ).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _word_count(value: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value, re.UNICODE))


def _issue(
    code: str,
    message: str,
    *,
    severity: str,
    section: str = "",
    paragraph_index: int | None = None,
    preview: str = "",
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
    if section:
        issue["section"] = section
    if paragraph_index is not None:
        issue["paragraph_index"] = paragraph_index
    if preview:
        issue["preview"] = re.sub(r"\s+", " ", preview).strip()[:160]
    return issue


def _fix(code: str, section: str, count: int, message: str) -> dict:
    return {
        "code": code,
        "section": section,
        "count": count,
        "message": message,
    }


def _normalize_base_text(script_text: str) -> tuple[str, list[dict]]:
    original = str(script_text or "")
    cleaned = unicodedata.normalize("NFC", original)
    cleaned = (
        cleaned.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    cleaned = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", cleaned).strip()
    if cleaned == original:
        return cleaned, []
    return cleaned, [
        _fix(
            "normalize_text",
            "",
            1,
            "Đã chuẩn hóa Unicode, khoảng trắng và ký tự vô hình.",
        )
    ]


def _is_high_confidence_editorial_block(block: str) -> bool:
    lines = [
        re.sub(r"^(?:#{1,6}\s*|[-*•]\s+|\d+[.)]\s+)", "", line).strip()
        for line in block.splitlines()
        if line.strip()
    ]
    if not lines:
        return False
    return all(
        STRUCTURAL_NARRATIVE_LABEL_PATTERN.fullmatch(line)
        or HIGH_CONFIDENCE_EDITORIAL_PATTERN.match(line)
        for line in lines
    )


def _capitalize_first_letter(value: str) -> str:
    for index, character in enumerate(value):
        if character.isalpha():
            return f"{value[:index]}{character.upper()}{value[index + 1:]}"
    return value


def _auto_fix_self_references(value: str) -> tuple[str, int]:
    """Rewrite only editorial references whose removal preserves the claim."""

    cleaned = str(value or "")
    total = 0

    cleaned, count = HIGH_CONFIDENCE_NARRATION_REFERENCE_PATTERN.subn(
        lambda match: match.group("prefix").rstrip(),
        cleaned,
    )
    total += count

    cleaned, count = HIGH_CONFIDENCE_EMBEDDED_SCRIPT_REFERENCE_PATTERN.subn(
        lambda match: f"{match.group('lead')}{match.group('punct') or ' '}",
        cleaned,
    )
    total += count

    cleaned, count = HIGH_CONFIDENCE_SOURCE_REFERENCE_PATTERN.subn(
        lambda match: match.group("source").rstrip(),
        cleaned,
    )
    total += count

    def replace_section_reference(match: re.Match) -> str:
        position = match.group("position").casefold()
        transition = "Trước đó, " if position in {"trên", "trước"} else "Tiếp theo, "
        return f"{match.group('boundary')}{transition}"

    cleaned, count = HIGH_CONFIDENCE_SECTION_REFERENCE_PATTERN.subn(
        replace_section_reference,
        cleaned,
    )
    total += count

    def remove_leading_reference(match: re.Match) -> str:
        next_character = _capitalize_first_letter(match.group("next"))
        return f"{match.group('boundary')}{next_character}"

    cleaned, count = HIGH_CONFIDENCE_SCRIPT_REFERENCE_PATTERN.subn(
        remove_leading_reference,
        cleaned,
    )
    total += count
    cleaned, count = HIGH_CONFIDENCE_REQUIREMENT_REFERENCE_PATTERN.subn(
        remove_leading_reference,
        cleaned,
    )
    total += count

    def replace_provided_data(match: re.Match) -> str:
        return f"{match.group('label')} hiện có"

    cleaned, count = HIGH_CONFIDENCE_PROVIDED_DATA_PATTERN.subn(
        replace_provided_data,
        cleaned,
    )
    total += count
    cleaned, count = HIGH_CONFIDENCE_PROMPT_INSTRUCTION_PATTERN.subn(
        lambda match: match.group("boundary"),
        cleaned,
    )
    total += count
    return cleaned.strip(), total


def _auto_fix_narrative(script_text: str) -> tuple[str, list[dict]]:
    fixes: list[dict] = []
    seen_paragraphs: set[str] = set()

    def replace_section(match: re.Match) -> str:
        section = match.group("name").upper()
        body = match.group("body")
        punctuation_count = len(re.findall(r"(?<!\.)\.(?=[?!])", body))
        spacing_count = len(re.findall(r"[ \t]+(?=[,;:!?])", body))
        body = re.sub(r"(?<!\.)\.(?=[?!])", "", body)
        body = re.sub(r"[ \t]+(?=[,;:!?])", "", body)
        if punctuation_count:
            fixes.append(
                _fix(
                    "mixed_terminal_punctuation",
                    section,
                    punctuation_count,
                    "Đã bỏ dấu chấm thừa đứng ngay trước dấu hỏi/chấm than.",
                )
            )
        if spacing_count:
            fixes.append(
                _fix(
                    "punctuation_spacing",
                    section,
                    spacing_count,
                    "Đã bỏ khoảng trắng thừa trước dấu câu.",
                )
            )

        kept: list[str] = []
        editorial_count = 0
        duplicate_count = 0
        self_reference_count = 0
        for block in re.split(r"\n[ \t]*\n+", body):
            stripped = block.strip()
            if not stripped:
                continue
            stripped, rewrite_count = _auto_fix_self_references(stripped)
            self_reference_count += rewrite_count
            if not stripped:
                continue
            if _is_high_confidence_editorial_block(stripped):
                editorial_count += 1
                continue
            normalized = re.sub(r"\s+", " ", stripped).strip().casefold()
            if (
                len(normalized) >= 160
                and _word_count(normalized) >= DUPLICATE_PARAGRAPH_MIN_WORDS
                and normalized in seen_paragraphs
            ):
                duplicate_count += 1
                continue
            if len(normalized) >= 160 and _word_count(normalized) >= DUPLICATE_PARAGRAPH_MIN_WORDS:
                seen_paragraphs.add(normalized)
            kept.append(stripped)

        if editorial_count:
            fixes.append(
                _fix(
                    "editorial_artifact_removed",
                    section,
                    editorial_count,
                    "Đã xóa block chỉ chứa hướng dẫn biên tập có độ chắc chắn cao.",
                )
            )
        if duplicate_count:
            fixes.append(
                _fix(
                    "exact_duplicate_removed",
                    section,
                    duplicate_count,
                    "Đã xóa đoạn lặp nguyên văn, giữ lần xuất hiện đầu tiên.",
                )
            )
        if self_reference_count:
            fixes.append(
                _fix(
                    "narration_self_reference_rewritten",
                    section,
                    self_reference_count,
                    "Đã viết lại tham chiếu kịch bản/prompt rõ ràng thành lời kể tự nhiên.",
                )
            )
        content = "\n\n".join(kept)
        suffix = "\n" if content else ""
        return f"{match.group('header')}{content}{suffix}"

    return REQUIRED_SECTION_PATTERN.sub(replace_section, script_text), fixes


def _find_duplicate_paragraphs(sections: dict[str, str]) -> list[dict]:
    paragraphs: list[tuple[str, str, str, int]] = []
    for section_name, section_text in sections.items():
        for index, paragraph in enumerate(re.split(r"\n[ \t]*\n+", section_text)):
            cleaned = re.sub(r"\s+", " ", paragraph).strip()
            if len(cleaned) < 160 or _word_count(cleaned) < DUPLICATE_PARAGRAPH_MIN_WORDS:
                continue
            normalized = cleaned.casefold()
            paragraphs.append((normalized, cleaned, section_name, index))

    counts = Counter(item[0] for item in paragraphs)
    duplicates: list[dict] = []
    reported: set[str] = set()
    for normalized, paragraph, section_name, index in paragraphs:
        if counts[normalized] < 2 or normalized in reported:
            continue
        reported.add(normalized)
        duplicates.append(
            _issue(
                "duplicate_paragraph",
                f"Đoạn văn bị lặp {counts[normalized]} lần.",
                severity="block",
                section=section_name,
                paragraph_index=index,
                preview=paragraph,
                auto_fixable=True,
            )
        )
    return duplicates


def _find_content_issues(sections: dict[str, str]) -> tuple[list[dict], list[dict]]:
    review_required: list[dict] = []
    warnings: list[dict] = []
    for section_name, section_text in sections.items():
        for index, block in enumerate(re.split(r"\n[ \t]*\n+", section_text)):
            if not block.strip():
                continue
            for pattern in SELF_REFERENCE_PATTERNS:
                match = pattern.search(block)
                if match:
                    review_required.append(
                        _issue(
                            "narration_self_reference",
                            "Lời đọc đang tham chiếu đến kịch bản, prompt hoặc vị trí biên tập.",
                            severity="review",
                            section=section_name,
                            paragraph_index=index,
                            preview=block,
                            confidence=0.96,
                        )
                    )
                    break
            for pattern in PLACEHOLDER_PATTERNS:
                if pattern.search(block):
                    review_required.append(
                        _issue(
                            "unresolved_placeholder",
                            "Lời đọc còn placeholder chưa được thay thế.",
                            severity="review",
                            section=section_name,
                            paragraph_index=index,
                            preview=block,
                            confidence=1.0,
                        )
                    )
                    break

            sentence_lengths = [
                _word_count(sentence)
                for sentence in re.split(r"(?<=[.!?…])\s+", block)
                if sentence.strip()
            ]
            if sentence_lengths and max(sentence_lengths) > 90:
                warnings.append(
                    _issue(
                        "very_long_sentence",
                        "Có câu rất dài; nên cân nhắc tách nhịp để TTS tự nhiên hơn.",
                        severity="warning",
                        section=section_name,
                        paragraph_index=index,
                        preview=block,
                        confidence=0.9,
                    )
                )
    return review_required, warnings


def _find_unbalanced_delimiters(sections: dict[str, str]) -> list[dict]:
    warnings: list[dict] = []
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    for section_name, text in sections.items():
        if any(text.count(left) != text.count(right) for left, right in pairs):
            warnings.append(
                _issue(
                    "unbalanced_delimiter",
                    "Phần lời đọc có ngoặc không cân bằng.",
                    severity="warning",
                    section=section_name,
                    confidence=0.9,
                )
            )
    return warnings


class ScriptQualityGate:
    """Apply safe fixes, then audit the exact script that downstream will use."""

    def __init__(self, sanitizer: Callable[[str], str] | None = None):
        self.sanitizer = sanitizer

    def run(self, script_text: str, *, auto_fix: bool = True) -> dict:
        original = str(script_text or "")
        cleaned = original
        fixes: list[dict] = []

        if auto_fix:
            cleaned, base_fixes = _normalize_base_text(cleaned)
            fixes.extend(base_fixes)
            cleaned, narrative_fixes = _auto_fix_narrative(cleaned)
            fixes.extend(narrative_fixes)
            if self.sanitizer is not None:
                sanitized = self.sanitizer(cleaned)
                if sanitized != cleaned:
                    cleaned = sanitized
                    fixes.append(
                        _fix(
                            "generated_output_sanitized",
                            "",
                            1,
                            "Đã loại bỏ artifact an toàn từ output sinh nội dung.",
                        )
                    )

        report = self.audit(cleaned)
        report.update(
            {
                "quality_gate_version": QUALITY_GATE_VERSION,
                "original_script_hash": get_audio_script_hash(original),
                "script_hash": get_audio_script_hash(cleaned),
                "changed": cleaned != original,
                "fixes": fixes,
                "cleaned_script": cleaned,
            }
        )
        return report

    def audit(self, script_text: str) -> dict:
        text = str(script_text or "")
        matches = list(SECTION_PATTERN.finditer(text))
        narrative_matches = [
            match
            for match in matches
            if match.group(1).strip().upper() in REQUIRED_AUDIO_SECTIONS
        ]
        names = [match.group(1).strip().upper() for match in narrative_matches]
        sections = extract_audio_sections(text)
        errors: list[dict] = []
        review_required: list[dict] = []
        warnings: list[dict] = []

        for section_name in REQUIRED_AUDIO_SECTIONS:
            occurrences = names.count(section_name)
            if occurrences == 0:
                errors.append(
                    _issue(
                        "missing_section",
                        f"Thiếu phần {section_name} trong kịch bản.",
                        severity="block",
                        section=section_name,
                    )
                )
            elif occurrences > 1:
                errors.append(
                    _issue(
                        "duplicate_section",
                        f"Phần {section_name} xuất hiện {occurrences} lần.",
                        severity="block",
                        section=section_name,
                    )
                )
            elif not sections.get(section_name, "").strip():
                errors.append(
                    _issue(
                        "empty_section",
                        f"Phần {section_name} đang trống.",
                        severity="block",
                        section=section_name,
                    )
                )

        ordered_names = [name for name in names if name in REQUIRED_AUDIO_SECTIONS]
        expected_present_order = [
            name for name in REQUIRED_AUDIO_SECTIONS if name in ordered_names
        ]
        if ordered_names and ordered_names != expected_present_order:
            errors.append(
                _issue(
                    "invalid_section_order",
                    "Các phần lời đọc phải theo thứ tự INTRO, BODY, OUTRO.",
                    severity="block",
                )
            )

        audio_script = get_audio_script(text)
        if audio_script and (
            "\ufffd" in audio_script
            or re.search(
                r"(?:[^\W\d_]\?+[^\W\d_]|(?:\s|^)\?{2,}[^\W\d_])",
                audio_script,
                re.UNICODE,
            )
        ):
            errors.append(
                _issue(
                    "corrupted_unicode",
                    "Phần đọc có ký tự lỗi/mất dấu; cần sửa trước khi tạo audio.",
                    severity="block",
                )
            )

        if re.search(r"```|###\s*\[", audio_script):
            errors.append(
                _issue(
                    "unexpected_markup",
                    "Phần đọc còn chứa khối mã hoặc nhãn hệ thống không hợp lệ.",
                    severity="block",
                )
            )

        for section_name, section_text in sections.items():
            if any(pattern.search(section_text) for pattern in SYSTEM_ERROR_PATTERNS):
                errors.append(
                    _issue(
                        "system_error_artifact",
                        f"Phần {section_name} chứa thông báo lỗi hệ thống/mạng thay vì nội dung đọc.",
                        severity="block",
                        section=section_name,
                    )
                )

        total_words = _word_count(audio_script)
        if total_words < MIN_AUDIO_SCRIPT_WORDS:
            errors.append(
                _issue(
                    "insufficient_content",
                    f"Kịch bản quá ngắn ({total_words} từ, tối thiểu {MIN_AUDIO_SCRIPT_WORDS} từ).",
                    severity="block",
                )
            )
        for section_name, minimum in MIN_SECTION_WORDS.items():
            section_words = _word_count(sections.get(section_name, ""))
            if 0 < section_words < minimum:
                errors.append(
                    _issue(
                        "section_too_short",
                        f"Phần {section_name} quá ngắn ({section_words} từ, tối thiểu {minimum} từ).",
                        severity="block",
                        section=section_name,
                    )
                )

        errors.extend(_find_duplicate_paragraphs(sections))
        content_review, content_warnings = _find_content_issues(sections)
        review_required.extend(content_review)
        warnings.extend(content_warnings)
        warnings.extend(_find_unbalanced_delimiters(sections))

        if errors:
            decision = "block"
        elif review_required:
            decision = "review"
        else:
            decision = "pass"

        character_count = len(audio_script)
        paragraph_count = len(
            [part for part in re.split(r"\n[ \t]*\n+", audio_script) if part.strip()]
        )
        section_metrics = {
            name: {
                "word_count": _word_count(sections.get(name, "")),
                "character_count": len(sections.get(name, "")),
            }
            for name in REQUIRED_AUDIO_SECTIONS
        }
        return {
            "quality_gate_version": QUALITY_GATE_VERSION,
            "script_hash": get_audio_script_hash(text),
            "decision": decision,
            "can_approve": decision == "pass",
            "can_manual_approve": decision in {"pass", "review"},
            "errors": errors,
            "review_required": review_required,
            "warnings": warnings,
            "metrics": {
                "word_count": total_words,
                "character_count": character_count,
                "paragraph_count": paragraph_count,
                "estimated_min_seconds": round(
                    total_words / WORDS_PER_MINUTE_FAST * 60
                ),
                "estimated_max_seconds": round(
                    total_words / WORDS_PER_MINUTE_SLOW * 60
                ),
                "sections": section_metrics,
            },
        }


def audit_script_for_audio(script_text: str) -> dict:
    """Backward-compatible read-only audit without mutating the supplied script."""
    return ScriptQualityGate().audit(script_text)
