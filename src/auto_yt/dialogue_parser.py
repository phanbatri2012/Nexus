# src/auto_yt/dialogue_parser.py
"""
Module bóc tách lượt thoại (Dialogue Parser) cho hệ thống Auto_YT.
Hỗ trợ bóc tách các tag nhân vật như [MC]:, [KHACH_1]:, [KHACH_2]:, [GUEST]:, [HOST]:...
"""
from __future__ import annotations
import re
import unicodedata
from typing import TypedDict


class DialogueTurn(TypedDict):
    role: str
    text: str
    sentences: list[str]


# Pattern nhận diện tag vai ở đầu câu/lượt thoại: [MC]:, [KHACH_1]:, [Khách 1]:, [Host]:, etc.
DIALOGUE_TAG_REGEX = re.compile(
    r'\[([A-Za-z0-9_\u00C0-\u024F\u1EA0-\u1EF9\s\-]+)\]\s*:\s*',
    flags=re.IGNORECASE
)

# Pattern ngắt câu chuẩn mực cho OmniVoice (không cắt ngang số từ thô)
SENTENCE_SPLIT_REGEX = re.compile(r'(?<=[.!?\n])\s+')


def _strip_vietnamese_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để so sánh chuỗi không phân biệt dấu."""
    nfkd_form = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


def normalize_role_tag(tag: str) -> str:
    """Chuẩn hóa tag vai về định dạng chuẩn (MC, KHACH_1, KHACH_2, etc.)."""
    raw_upper = tag.strip().upper().replace(" ", "_")
    unaccented = _strip_vietnamese_accents(raw_upper).replace(" ", "_")

    if unaccented in ("MC", "HOST", "DAN_CHUYEN", "TIEN_SI", "DINH_DOAN", "CHUYEN_GIA_TAM_LY"):
        return "MC"
    if unaccented in ("KHACH_1", "KHACH1", "GUEST_1", "GUEST1", "KHACH", "GUEST", "NGUOI_TAM_SU", "NGUOI_VO", "NGUOI_CHONG", "THINH_GIA"):
        return "KHACH_1"
    if unaccented in ("KHACH_2", "KHACH2", "GUEST_2", "GUEST2", "CHUYEN_GIA", "LUAT_SU", "NGUOI_THU_3", "BAC_SI"):
        return "KHACH_2"
    return unaccented


def is_dialogue_script(script_text: str) -> bool:
    """Kiểm tra xem kịch bản có chứa các tag đối thoại hay không."""
    if not script_text or not isinstance(script_text, str):
        return False
    return bool(DIALOGUE_TAG_REGEX.search(script_text))


def clean_turn_text_for_tts(text: str) -> str:
    """Chuẩn hóa văn bản lượt thoại để sẵn sàng đọc TTS:
    - Bỏ các ký tự markdown thừa, dấu gạch ngang vô nghĩa
    - Giữ trọn dấu chấm phẩy, dấu hỏi, cảm thán
    """
    if not text:
        return ""
    cleaned = text.strip()
    # Loại bỏ các chỉ dẫn sân khấu trong ngoặc đơn nếu có: (cười), (khóc nức nở), (thở dài)
    cleaned = re.sub(r'\([^\)]*\)', '', cleaned)
    # Thay thế các loại gạch ngang dài thành dấu phẩy hoặc khoảng trắng
    cleaned = re.sub(r'[—–]', ', ', cleaned)
    # Loại bỏ dấu gạch ngang đầu dòng hoặc thừa
    cleaned = re.sub(r'^\s*[-*•]\s*', '', cleaned, flags=re.MULTILINE)
    # Gộp nhiều khoảng trắng liên tiếp
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # Gộp nhiều dòng trống liên tiếp
    cleaned = re.sub(r'\n\s*\n+', '\n', cleaned)
    return cleaned.strip()


def split_turn_into_sentences(text: str) -> list[str]:
    """Tách đoạn thoại thành từng câu tự nhiên tuân thủ quy tắc OmniVoice."""
    cleaned = clean_turn_text_for_tts(text)
    if not cleaned:
        return []
    parts = SENTENCE_SPLIT_REGEX.split(cleaned)
    sentences = [p.strip() for p in parts if p.strip()]
    return sentences if sentences else [cleaned]


def parse_dialogue_turns(script_text: str, default_role: str = "MC") -> list[DialogueTurn]:
    """Bóc tách toàn bộ kịch bản thành danh sách các lượt thoại có cấu trúc."""
    if not script_text or not isinstance(script_text, str):
        return []

    # Tìm tất cả vị trí xuất hiện của tag vai
    matches = list(DIALOGUE_TAG_REGEX.finditer(script_text))
    if not matches:
        # Nếu là kịch bản đơn thoại thông thường, coi như 1 lượt thoại của default_role
        cleaned = clean_turn_text_for_tts(script_text)
        if not cleaned:
            return []
        return [{
            "role": default_role,
            "text": cleaned,
            "sentences": split_turn_into_sentences(cleaned)
        }]

    turns: list[DialogueTurn] = []
    
    # Xử lý đoạn văn bản mở đầu trước khi có tag đầu tiên (nếu có)
    if matches[0].start() > 0:
        prelude = script_text[:matches[0].start()].strip()
        cleaned_prelude = clean_turn_text_for_tts(prelude)
        if cleaned_prelude:
            turns.append({
                "role": default_role,
                "text": cleaned_prelude,
                "sentences": split_turn_into_sentences(cleaned_prelude)
            })

    for i, match in enumerate(matches):
        raw_role = match.group(1)
        role = normalize_role_tag(raw_role)
        start_pos = match.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(script_text)
        turn_content = script_text[start_pos:end_pos].strip()
        cleaned_content = clean_turn_text_for_tts(turn_content)
        if cleaned_content:
            turns.append({
                "role": role,
                "text": cleaned_content,
                "sentences": split_turn_into_sentences(cleaned_content)
            })

    return turns


def extract_dialogue_speakers(script_text: str) -> list[str]:
    """Trả về danh sách các vai diễn xuất hiện trong kịch bản."""
    turns = parse_dialogue_turns(script_text)
    seen = []
    for turn in turns:
        if turn["role"] not in seen:
            seen.append(turn["role"])
    return seen


def resolve_fallback_guest_voice(
    used_voice_ids: list[str],
    available_voices: list[dict] | None = None,
    default_fallback: str = "",
) -> str:
    """Tự động chọn một giọng phụ khác biệt từ danh mục giọng khả dụng
    nếu trong kịch bản phát sinh Khách Mời 2 mà chưa được gán giọng trước.
    """
    if not available_voices:
        return default_fallback

    clean_used = {str(v).strip() for v in used_voice_ids if v and str(v).strip()}
    
    # 1. Tìm các giọng chưa được gán cho MC hoặc Khách 1
    unused_voices = [
        v for v in available_voices
        if v.get("id") and str(v.get("id")).strip() not in clean_used
        and v.get("status", "active") == "active"
    ]

    if unused_voices:
        # Ưu tiên giọng cùng provider hoặc giọng có sẵn
        return str(unused_voices[0]["id"]).strip()

    # Nếu tất cả giọng đã bị dùng, fallback về giọng đầu tiên hoặc default_fallback
    for v in available_voices:
        if v.get("id") and v.get("status", "active") == "active":
            return str(v["id"]).strip()

    return default_fallback


def resolve_turn_voice(
    role: str,
    cast_settings: dict,
    default_voice_id: str = "",
    available_voices: list[dict] | None = None,
) -> str:
    """Xác định ID giọng đọc cho một vai diễn cụ thể trong kịch bản đối thoại.
    Hỗ trợ Smart Distinct Fallback cho KHACH_2 nếu chưa được cấu hình.
    """
    normalized_role = normalize_role_tag(role)
    cast = cast_settings or {}

    if normalized_role == "MC":
        mc_cfg = cast.get("mc") or {}
        mc_voice = mc_cfg.get("voice_id") or mc_cfg.get("default_voice_id")
        return str(mc_voice or default_voice_id).strip()

    if normalized_role == "KHACH_1":
        guest1_cfg = cast.get("guest_1") or {}
        g1_voice = guest1_cfg.get("voice_id") or guest1_cfg.get("default_voice_id")
        return str(g1_voice or default_voice_id).strip()

    if normalized_role == "KHACH_2":
        guest2_cfg = cast.get("guest_2") or {}
        g2_voice = guest2_cfg.get("voice_id") or guest2_cfg.get("default_voice_id")
        
        # Nếu đã có giọng cụ thể (và khác 'auto'), dùng luôn
        if g2_voice and str(g2_voice).strip() not in ("", "auto", "none"):
            return str(g2_voice).strip()

        # Nếu để auto hoặc chưa có giọng, kích hoạt Smart Fallback
        mc_voice = resolve_turn_voice("MC", cast, default_voice_id, available_voices)
        g1_voice = resolve_turn_voice("KHACH_1", cast, default_voice_id, available_voices)
        return resolve_fallback_guest_voice(
            used_voice_ids=[mc_voice, g1_voice],
            available_voices=available_voices,
            default_fallback=g1_voice or default_voice_id,
        )

    # Các vai khác (fallback an toàn)
    return str(default_voice_id).strip()

