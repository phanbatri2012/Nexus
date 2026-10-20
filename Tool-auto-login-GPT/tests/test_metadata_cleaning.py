import copy

import pytest
from auto_yt.services.chatgpt_worker import (
    build_video_script,
    clean_text,
    sanitize_and_repair_metadata,
)
from auto_yt.dialogue_parser import clean_turn_text_for_tts, sanitize_dialogue_script
from auto_yt.services.browser_youtube_uploader import _sanitize_youtube_metadata_field
from auto_yt.services.metadata_validation import (
    metadata_component_error,
    repair_swapped_script_metadata,
    validate_required_metadata,
)


def test_clean_text_decodes_html_entities():
    raw_title = "Cô Gái Người Dao Bắt Cua Cứu Tỷ Phú Bị Bắt Cóc, Sững Người: Anh Ruột! &#124; MC Văn Sâm"
    cleaned = clean_text(raw_title)
    assert "&#124;" not in cleaned
    assert " | MC Văn Sâm" in cleaned

    raw_quote = "&quot;Sâm Audio&quot; &amp; &apos;Truyện Tâm Lý&#39; &#124; Kênh Chính Thức"
    cleaned_quote = clean_text(raw_quote)
    assert cleaned_quote == '"Sâm Audio" & \'Truyện Tâm Lý\' | Kênh Chính Thức'


def test_clean_text_strips_canvas_writing_directives():
    raw_desc = (
        ':::writing{variant="document" id="58341"}\n'
        'Khi vợ mang thai năm tháng bất ngờ đòi ly hôn để cưới chính bố chồng, Trường sững người.\n'
        'Hãy xem đến cuối để cảm nhận trọn vẹn hành trình.\n'
        ':::'
    )
    cleaned = clean_text(raw_desc)
    assert ":::writing" not in cleaned
    assert ":::" not in cleaned
    assert "Khi vợ mang thai năm tháng" in cleaned
    assert "Hãy xem đến cuối để cảm nhận trọn vẹn hành trình." in cleaned


def test_clean_text_normalizes_unicode_spaces():
    raw = "Tiêu đề\u00a0có\u00a0khoảng\u00a0trắng\ufeff và\u200b ký tự ẩn"
    cleaned = clean_text(raw)
    assert "\u00a0" not in cleaned
    assert "\u200b" not in cleaned
    assert "\ufeff" not in cleaned
    assert cleaned == "Tiêu đề có khoảng trắng và ký tự ẩn"


def test_sanitize_and_repair_metadata():
    state = {
        "title": "Mẹ 52 Tuổi Mang Thai, Con Gái Khóc Nghẹn &#124; Sâm Audio",
        "slug": "me-52-tuoi-mang-thai",
        "description": ':::writing{variant="document" id="123"}\nMô tả video tâm lý.\n:::',
        "hashtags": "#SamAudio #TamLy",
    }
    sanitize_and_repair_metadata(state)
    assert state["title"] == "Mẹ 52 Tuổi Mang Thai, Con Gái Khóc Nghẹn | Sâm Audio"
    assert ":::writing" not in state["description"]
    assert ":::" not in state["description"]
    assert state["description"] == "Mô tả video tâm lý."


@pytest.mark.parametrize("content_mode", ["monologue", "dialogue"])
def test_build_video_script_does_not_mutate_generation_state(content_mode):
    state = {
        "content_mode": content_mode,
        "intro": "Mở đầu đủ dài cho nội dung thử nghiệm của video.",
        "body_parts": ["Nội dung chính đủ dài cho thử nghiệm."],
        "outro": "Kết thúc đủ dài cho nội dung thử nghiệm của video.",
        "title": "Một " + "tiêu đề rất dài " * 20,
        "slug": "slug-hop-le",
        "description": "Đây là phần mô tả đủ dài để kiểm tra việc dựng script không thay đổi state gốc của tiến trình tạo video.",
    }
    before = copy.deepcopy(state)

    build_video_script(state)

    assert state == before


def test_metadata_validator_rejects_swapped_and_duplicated_fields():
    long_description = (
        "Câu chuyện kể về một gia đình trải qua nhiều biến cố và những lựa chọn "
        "khó khăn, từ đó làm rõ giá trị của lòng tin, trách nhiệm và sự bao dung."
    )
    title = "Đại Úy Thay Vợ Bầu Ăn Cơm Thừa Trong Miếu Hoang | MC Văn Sâm"

    assert metadata_component_error("title", long_description)
    assert metadata_component_error("description", title)
    assert metadata_component_error("slug", "Đây không phải slug")
    assert validate_required_metadata(
        {"title": title, "slug": "dai-uy-trong-mieu-hoang", "description": title},
        {"title": True, "slug": True, "description": True},
    )


def test_repair_swapped_script_metadata_only_repairs_unambiguous_swap():
    description = (
        "Câu chuyện kể về một gia đình trải qua nhiều biến cố và những lựa chọn "
        "khó khăn, từ đó làm rõ giá trị của lòng tin, trách nhiệm và sự bao dung."
    )
    title = "Đại Úy Thay Vợ Bầu Ăn Cơm Thừa Trong Miếu Hoang | MC Văn Sâm"
    script = (
        f"### [TIÊU ĐỀ]\n{description}\n\n"
        "### [SLUG]\ndai-uy-trong-mieu-hoang\n\n"
        f"### [MÔ TẢ]\n{title}\n\n"
        "### [HASHTAGS]\n#MCVanSam #TamSu"
    )

    repaired, changed = repair_swapped_script_metadata(script)

    assert changed is True
    assert f"### [TIÊU ĐỀ]\n{title}" in repaired
    assert f"### [MÔ TẢ]\n{description}" in repaired


def test_dialogue_parser_cleaning():
    raw_turn = ':::writing{variant="document" id="999"}\n[MC]: Chào quý vị &#124; Đinh Đoàn.\n:::'
    cleaned = clean_turn_text_for_tts(raw_turn)
    assert ":::writing" not in cleaned
    assert ":::" not in cleaned
    assert "&#124;" not in cleaned
    assert "Chào quý vị | Đinh Đoàn." in cleaned

    raw_script = ':::writing{variant="doc"}\n[MC]: Lời mở đầu.\n\n[KHACH_1]: Lời khách mời &quot;tâm sự&quot;.\n:::'
    sanitized = sanitize_dialogue_script(raw_script)
    assert ":::writing" not in sanitized
    assert ":::" not in sanitized
    assert '&quot;' not in sanitized
    assert '[MC]: Lời mở đầu.' in sanitized
    assert '[KHACH_1]: Lời khách mời "tâm sự".' in sanitized


def test_browser_youtube_uploader_sanitizer():
    raw_title = "Video Hay &#124; MC Văn Sâm"
    sanitized_title = _sanitize_youtube_metadata_field(raw_title)
    assert sanitized_title == "Video Hay | MC Văn Sâm"

    raw_desc = ':::writing{variant="document" id="58341"}\nMô tả &amp; hashtag\n:::'
    sanitized_desc = _sanitize_youtube_metadata_field(raw_desc)
    assert sanitized_desc == "Mô tả & hashtag"


def test_extract_generated_video_title_rejects_subsequent_section_headers():
    from auto_yt.services.database import extract_generated_video_title

    # When [TIÊU ĐỀ] has an invalid long description with CTA and next section is [SLUG],
    # it should not parse [SLUG] as the title.
    malformed_script = (
        "[MC]: Chào quý vị.\n\n"
        "### [TIÊU ĐỀ]\n"
        "Một câu chuyện dài hơn hai trăm ký tự với lời kêu gọi hãy bấm like và đăng ký kênh Sâm Audio để đón xem nhiều câu chuyện hay tiếp theo trên kênh của chúng tôi.\n\n"
        "### [SLUG]\n"
        "cau-chuyen-hay\n\n"
        "### [MÔ TẢ]\n"
        "Mô tả video.\n"
    )
    title = extract_generated_video_title(malformed_script)
    assert title != "[SLUG]"
    assert title != "cau-chuyen-hay"

    # When valid title is present under [TIÊU ĐỀ]
    valid_script = (
        "[MC]: Chào quý vị.\n\n"
        "### [TIÊU ĐỀ]\n"
        "Câu Chuyện Hay Về Lòng Người | Sâm Audio\n\n"
        "### [SLUG]\n"
        "cau-chuyen-hay\n"
    )
    assert extract_generated_video_title(valid_script) == "Câu Chuyện Hay Về Lòng Người | Sâm Audio"


def test_auto_repair_metadata_helpers():
    from auto_yt.services.chatgpt_worker import (
        auto_repair_hashtags,
        auto_repair_tags,
        auto_repair_pinned_comment,
        auto_repair_quiz,
        sanitize_and_repair_metadata,
    )

    # 1. Hashtags without '#'
    raw_sentence = "Trên cung đường đèo vắng, sĩ quan Trần Hoàng Nam gặp tai nạn"
    repaired_hashtags = auto_repair_hashtags(raw_sentence, "Sĩ Quan Bị Vợ Hãm Hại")
    assert "#" in repaired_hashtags
    assert "#TrênCungĐườngĐèoVắng" in repaired_hashtags or "#SamAudio" in repaired_hashtags

    # 2. Empty hashtags fallback
    empty_hashtags = auto_repair_hashtags("", "Sĩ Quan Bị Vợ Hãm Hại")
    assert "#SamAudio" in empty_hashtags

    # 3. Tags containing hashes
    hash_tags = "#MCVanSam, #TamSuGiaDinh, #TruyenNhanQua"
    repaired_tags = auto_repair_tags(hash_tags)
    assert "#" not in repaired_tags
    assert "MCVanSam" in repaired_tags

    # 4. Empty tags fallback
    empty_tags = auto_repair_tags("", "Sĩ Quan Bị Hãm Hại")
    assert "MC Văn Sâm" in empty_tags
    assert "Sĩ Quan Bị Hãm Hại" in empty_tags

    # 5. Pinned comment & Quiz
    assert "Văn Sâm" in auto_repair_pinned_comment("")
    assert "Đáp án đúng:" in auto_repair_quiz("")

    # 6. sanitize_and_repair_metadata state repair
    state = {
        "title": "Sĩ Quan Bị Vợ Hãm Hại | MC Văn Sâm",
        "hashtags": "tâm sự gia đình, chuyện hôn nhân, ngoại tình",
        "tags": "#MCVanSam, #TamSu, #HonNhan",
        "pinned_comment": "Bình luận ngắn",
        "quiz": "Quiz ngắn",
    }
    sanitize_and_repair_metadata(state)
    assert "#" in state["hashtags"]
    assert "#" not in state["tags"]
    assert len(state["pinned_comment"]) > 10
    assert "Đáp án đúng:" in state["quiz"]


def test_slug_unaccents_vietnamese_characters():
    state = {
        "title": "Nữ Thiếu Tá Giả Chết | MC Văn Sâm",
        "slug": "nữ-thiếu-tá-giả-chết-bật-dậy-giữa-đám-tang-mc-văn-sâm",
    }
    sanitize_and_repair_metadata(state)
    assert "thi-u-t" not in state["slug"]
    assert "thieu-ta" in state["slug"]
    assert "dam-tang" in state["slug"]


def test_auto_repair_hashtags_rejects_sentence_hashtags():
    from auto_yt.services.chatgpt_worker import auto_repair_hashtags
    raw_sentence_hashtags = (
        "#TrongMộtĐêmMưaTầmTã #ThiếuTáTrầnMinhChiếnCứuNữBácSĩNguyễnNgọcAnBấtTỉnhBênVệĐường "
        "#ĐừngBỏEmKhiếnAnhSữngNgười #BởiĐóLàCâuNóiCủaThảo"
    )
    repaired = auto_repair_hashtags(raw_sentence_hashtags, "Cứu Nữ Bác Sĩ")
    # All of the sentence tags were > 32 chars, so it should fall back safely to valid short tags
    for tag in repaired.split():
        assert len(tag) <= 32


def test_audio_review_allows_emphasis_question_marks():
    from auto_yt.services.audio_review import audit_script_for_audio
    test_script = (
        "### [INTRO]\n"
        "[MC]: Chào quý vị.\n\n"
        "[KHACH_1]: Cháu chào chú Sâm. Cháu có một câu chuyện muốn kể.\n\n"
        "### [BODY]\n"
        "[KHACH_1]: " + ("Lời xin lỗi ấy làm sao trả lại được 24 năm?? " * 15) + "\n\n"
        "[MC]: " + ("Bác rất hiểu nỗi lòng của cháu. " * 15) + "\n\n"
        "### [OUTRO]\n"
        "[MC]: " + ("Cảm ơn quý vị đã theo dõi chương trình hôm nay. " * 15) + "\n"
    )
    report = audit_script_for_audio(test_script)
    error_codes = [e["code"] for e in report["errors"]]
    assert "corrupted_unicode" not in error_codes


