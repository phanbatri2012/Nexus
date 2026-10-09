import pytest
from auto_yt.services.chatgpt_worker import clean_text, sanitize_and_repair_metadata
from auto_yt.dialogue_parser import clean_turn_text_for_tts, sanitize_dialogue_script
from auto_yt.services.browser_youtube_uploader import _sanitize_youtube_metadata_field


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
