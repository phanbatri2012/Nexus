# src/auto_yt/dialogue_parser.py
"""
Module bóc tách lượt thoại (Dialogue Parser) và phân đoạn Audio đa giọng cho hệ thống Auto_YT.
Hỗ trợ bóc tách các tag nhân vật như [MC]:, [KHACH_1]:, [KHACH_2]:, [GUEST]:, [HOST]:...
và gom nhóm phân đoạn thoại thông minh (Consecutive Turn Batching) cho TTS Engine.
"""
from __future__ import annotations
import re
import unicodedata
from typing import TypedDict, Optional


class DialogueTurn(TypedDict):
    role: str
    text: str
    sentences: list[str]


class DialogueSegment(TypedDict):
    index: int
    role: str
    voice_id: str
    voice_name: str
    text: str
    characters: int


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

    if unaccented in ("MC", "HOST", "DAN_CHUYEN", "TIEN_SI", "DINH_DOAN", "CHUYEN_GIA_TAM_LY", "NGUOI_DAN"):
        return "MC"
    if unaccented in ("KHACH_1", "KHACH1", "GUEST_1", "GUEST1", "KHACH", "GUEST", "NGUOI_TAM_SU", "NGUOI_VO", "NGUOI_CHONG", "THINH_GIA", "KHACH_MOI_1", "KHACH_MOI"):
        return "KHACH_1"
    if unaccented in ("KHACH_2", "KHACH2", "GUEST_2", "GUEST2", "CHUYEN_GIA", "LUAT_SU", "NGUOI_THU_3", "BAC_SI", "KHACH_MOI_2"):
        return "KHACH_2"
    if unaccented in ("KHACH_3", "KHACH3", "GUEST_3", "GUEST3", "KHACH_MOI_3"):
        return "KHACH_3"
    return unaccented


def is_dialogue_script(script_text: str) -> bool:
    """Kiểm tra xem kịch bản có chứa các tag đối thoại hay không."""
    if not script_text or not isinstance(script_text, str):
        return False
    return bool(DIALOGUE_TAG_REGEX.search(script_text))


def clean_turn_text_for_tts(text: str) -> str:
    """Chuẩn hóa văn bản lượt thoại để sẵn sàng đọc TTS:
    - Bỏ các nhãn vai: [MC]:, [KHACH_1]:, MC:, v.v.
    - Bỏ các tiêu đề phần: ### [INTRO], ### [BODY], [PHAN 1]
    - Bỏ các chỉ dẫn sân khấu trong ngoặc đơn / ngoặc vuông / dấu sao: (nghẹn ngào), (cười), [thở dài], *khóc nức nở*
    - Bỏ các ký tự markdown thừa, dấu gạch ngang vô nghĩa
    - Giữ trọn dấu chấm phẩy, dấu hỏi, cảm thán
    """
    if not text:
        return ""
    cleaned = text.strip()

    # Loại bỏ các tiêu đề Markdown hoặc nhãn phần: ### [INTRO], ### [BODY], [PHAN 1], etc.
    cleaned = re.sub(r'###\s*\[[^\]]+\]', '', cleaned)
    cleaned = re.sub(r'\[PHAN\s*\d+[^\]]*\]', '', cleaned, flags=re.IGNORECASE)

    # Loại bỏ tag vai nếu còn sót ở đầu chuỗi: [MC]:, [KHACH_1]:, MC:, Khách:
    cleaned = re.sub(
        r'^\s*\[?(?:MC|HOST|KHACH(?:_\d+)?|GUEST(?:_\d+)?|NGUOI_DAN|KHACH_MOI(?:_\d+)?)\]?\s*:\s*',
        '',
        cleaned,
        flags=re.IGNORECASE
    )

    # Loại bỏ các chỉ dẫn sân khấu/cảm xúc trong ngoặc đơn: (cười), (khóc nức nở), (thở dài), (ngập ngừng)
    cleaned = re.sub(r'\([^\)]*\)', '', cleaned)
    # Loại bỏ các chỉ dẫn trong ngoặc vuông không phải tag chính: [thở dài], [im lặng]
    cleaned = re.sub(r'\[[^\]]*\]', '', cleaned)
    # Loại bỏ chỉ dẫn in nghiêng / sao: *bật khóc*, _cười_
    cleaned = re.sub(r'\*[^*]+\*', '', cleaned)
    cleaned = re.sub(r'_[^_]+_', '', cleaned)

    # Thay thế các loại gạch ngang dài thành dấu phẩy hoặc khoảng trắng
    cleaned = re.sub(r'[—–]', ', ', cleaned)
    # Loại bỏ dấu gạch ngang đầu dòng hoặc thừa
    cleaned = re.sub(r'^\s*[-*•]\s*', '', cleaned, flags=re.MULTILINE)
    # Gộp nhiều khoảng trắng liên tiếp
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # Loại bỏ khoảng trắng trước dấu câu và chuẩn hóa khoảng trắng sau dấu câu
    cleaned = re.sub(r'\s+([,.:;!?])', r'\1', cleaned)
    cleaned = re.sub(r'([,.:;!?])(?=[A-Za-z0-9\u00C0-\u024F\u1EA0-\u1EF9])', r'\1 ', cleaned)
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
        return str(unused_voices[0]["id"]).strip()

    # Nếu tất cả giọng đã bị dùng, fallback về giọng đầu tiên hoặc default_fallback
    for v in available_voices:
        if v.get("id") and v.get("status", "active") == "active":
            return str(v["id"]).strip()

    return default_fallback


def _extract_voice_id_from_entry(entry: any) -> str:
    """Trích xuất Voice ID linh hoạt từ chuỗi trực tiếp hoặc dictionary cấu hình."""
    if not entry:
        return ""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("voice_id") or entry.get("default_voice_id") or "").strip()
    return ""


def resolve_turn_voice(
    role: str,
    cast_settings: dict,
    default_voice_id: str = "",
    available_voices: list[dict] | None = None,
) -> str:
    """Xác định ID giọng đọc cho một vai diễn cụ thể trong kịch bản đối thoại.
    Hỗ trợ cả dạng dict lồng nhau (mc.voice_id) và dạng key-value trực tiếp (MC: voice_id).
    Hỗ trợ Smart Distinct Fallback cho KHACH_2/KHACH_3 nếu chưa được cấu hình.
    """
    normalized_role = normalize_role_tag(role)
    cast = cast_settings or {}

    if normalized_role == "MC":
        val = cast.get("mc") or cast.get("MC") or cast.get("host") or cast.get("HOST")
        mc_voice = _extract_voice_id_from_entry(val)
        return str(mc_voice or default_voice_id).strip()

    if normalized_role == "KHACH_1":
        val = (
            cast.get("guest_1") or cast.get("GUEST_1") or
            cast.get("khach_1") or cast.get("KHACH_1") or
            cast.get("guest") or cast.get("khach")
        )
        g1_voice = _extract_voice_id_from_entry(val)
        return str(g1_voice or default_voice_id).strip()

    if normalized_role in ("KHACH_2", "KHACH_3"):
        key_lookup = "guest_2" if normalized_role == "KHACH_2" else "guest_3"
        alt_lookup = "KHACH_2" if normalized_role == "KHACH_2" else "KHACH_3"
        val = cast.get(key_lookup) or cast.get(alt_lookup)
        g_voice = _extract_voice_id_from_entry(val)
        
        # Nếu đã có giọng cụ thể (và khác 'auto' / 'none'), dùng luôn
        if g_voice and g_voice.lower() not in ("auto", "none", "distinct"):
            return g_voice

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


def build_dialogue_tts_segments(
    script_text: str,
    cast_settings: dict | None = None,
    default_voice_id: str = "",
    available_voices: list[dict] | None = None,
    max_segment_chars: int = 7500,
) -> list[DialogueSegment]:
    """Gom nhóm các lượt thoại liên tiếp cùng một giọng đọc thành danh sách Segment
    chuẩn hóa, sẵn sàng gửi sang TTS engine (Genmax hoặc OmniVoice).
    
    Quy tắc gom:
    - Nếu 2 hoặc nhiều lượt thoại liên tiếp có cùng voice_id -> gộp lại
    - Nếu gặp lượt thoại khác voice_id (đổi vai) -> tạo segment mới
    - Nếu độ dài của segment đang gom vượt quá max_segment_chars -> tạo segment mới
    """
    if not script_text or not isinstance(script_text, str):
        return []

    turns = parse_dialogue_turns(script_text)
    if not turns:
        return []

    # Map tra cứu tên giọng đọc (voice_name) để lưu snapshot chi tiết
    voice_name_map: dict[str, str] = {}
    if available_voices:
        for v in available_voices:
            if v.get("id"):
                voice_name_map[str(v["id"]).strip()] = str(v.get("name") or "").strip()

    segments: list[DialogueSegment] = []
    current_role: Optional[str] = None
    current_voice_id: Optional[str] = None
    current_texts: list[str] = []
    current_length = 0

    for turn in turns:
        role = turn["role"]
        cleaned_text = clean_turn_text_for_tts(turn["text"])
        if not cleaned_text:
            continue

        turn_voice_id = resolve_turn_voice(
            role=role,
            cast_settings=cast_settings or {},
            default_voice_id=default_voice_id,
            available_voices=available_voices,
        )
        turn_len = len(cleaned_text)

        # Kiểm tra điều kiện có thể gộp vào segment hiện tại hay không:
        can_append = (
            current_voice_id is not None
            and current_voice_id == turn_voice_id
            and (current_length + turn_len + 2 <= max_segment_chars or not current_texts)
        )

        if can_append:
            current_texts.append(cleaned_text)
            current_length += turn_len + 2
        else:
            # Lưu lại segment trước đó nếu có
            if current_texts and current_voice_id is not None:
                combined_text = "\n\n".join(current_texts).strip()
                v_name = voice_name_map.get(current_voice_id, "")
                segments.append({
                    "index": len(segments),
                    "role": current_role or "MC",
                    "voice_id": current_voice_id,
                    "voice_name": v_name,
                    "text": combined_text,
                    "characters": len(combined_text),
                })
            # Khởi tạo segment mới
            current_role = role
            current_voice_id = turn_voice_id
            current_texts = [cleaned_text]
            current_length = turn_len

    # Lưu lại segment cuối cùng
    if current_texts and current_voice_id is not None:
        combined_text = "\n\n".join(current_texts).strip()
        v_name = voice_name_map.get(current_voice_id, "")
        segments.append({
            "index": len(segments),
            "role": current_role or "MC",
            "voice_id": current_voice_id,
            "voice_name": v_name,
            "text": combined_text,
            "characters": len(combined_text),
        })

    return segments
