import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from auto_yt.services.chatgpt_worker import (
    get_content_mode,
    build_video_script,
    build_monologue_video_script,
    build_dialogue_video_script,
    sanitize_monologue_generated_script,
    sanitize_dialogue_generated_script,
)


def test_get_content_mode_resolution():
    # Test built-in prompt versions
    assert get_content_mode("default") == "dialogue"
    assert get_content_mode("v_1786603848860") == "monologue"
    assert get_content_mode("v_1791168747113") == "dialogue"
    assert get_content_mode("non_existent_version") == "monologue"


def test_monologue_script_preserves_narrative_without_dialogue_mutation():
    state = {
        "content_mode": "monologue",
        "intro": "Đây là mở đầu phim tài liệu lịch sử.",
        "body_parts": ["Trận đánh diễn ra ác liệt tại biên giới phía Bắc."],
        "outro": "Lịch sử mãi khắc ghi chiến công của thế hệ cha anh.",
        "title": "CHIẾN TRANH BIÊN GIỚI 1979",
    }
    script = build_monologue_video_script(state)
    assert "### [INTRO]" in script
    assert "Đây là mở đầu phim tài liệu lịch sử." in script
    assert "### [BODY]" in script
    assert "Trận đánh diễn ra ác liệt tại biên giới phía Bắc." in script
    assert "### [OUTRO]" in script
    assert "### [TIÊU ĐỀ]" in script


def test_dialogue_script_preserves_role_tags():
    state = {
        "content_mode": "dialogue",
        "intro": "[MC]: Chào mừng quý vị đến với chương trình.",
        "body_parts": ["[KHACH_1]: Biến cố ập đến khiến tôi mất hết tài sản."],
        "outro": "[MC]: Cảm ơn anh đã chia sẻ câu chuyện nhân quả này.",
    }
    script = build_dialogue_video_script(state)
    assert "### [INTRO]" in script
    assert "[MC]:" in script
    assert "### [BODY]" in script
    assert "[KHACH_1]:" in script
    assert "### [OUTRO]" in script


def test_build_video_script_dispatcher():
    mono_state = {
        "content_mode": "monologue",
        "intro": "Mở đầu đơn thoại.",
        "body_parts": ["Nội dung đơn thoại."],
        "outro": "Kết bài đơn thoại.",
    }
    mono_script = build_video_script(mono_state)
    assert "Mở đầu đơn thoại." in mono_script

    dial_state = {
        "content_mode": "dialogue",
        "intro": "[MC]: Mở đầu đối thoại.",
        "body_parts": ["[KHACH_1]: Nội dung đối thoại."],
        "outro": "[MC]: Kết bài đối thoại.",
    }
    dial_script = build_video_script(dial_state)
    assert "[MC]: Mở đầu đối thoại." in dial_script
    assert "[KHACH_1]: Nội dung đối thoại." in dial_script
