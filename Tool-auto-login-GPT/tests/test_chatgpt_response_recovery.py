import pytest
from unittest.mock import MagicMock
from auto_yt.services.chatgpt_worker import (
    _is_pure_thinking_indicator,
    looks_like_chatgpt_error,
    is_chatgpt_render_error_present,
    _get_payload_content_text,
    _get_payload_assistant_text,
    extract_assistant_response_from_conversation_payload,
    recover_assistant_response_from_backend,
    _accept_or_recover_assistant_response,
    split_outline_parts,
)


def test_is_pure_thinking_indicator():
    # Single line indicators
    assert _is_pure_thinking_indicator("Worked for 1m 47s") is True
    assert _is_pure_thinking_indicator("Checked historical claims") is True
    assert _is_pure_thinking_indicator("Thought for 32 seconds") is True
    assert _is_pure_thinking_indicator("Searching for Vietnamese history") is True
    assert _is_pure_thinking_indicator("Analyzed historical documents") is True
    assert _is_pure_thinking_indicator("Đã kiểm tra tài liệu lịch sử") is True
    assert _is_pure_thinking_indicator("Đang suy nghĩ...") is True
    assert _is_pure_thinking_indicator("Stopped thinking") is True

    # Multi-line indicators
    multiline = "Worked for 1m 47s\nChecked historical claims"
    assert _is_pure_thinking_indicator(multiline) is True

    # Real narrative or outline responses must NOT match
    real_outline = "[PHAN] Trong cuốn sổ tay năm 1978 của cố Tổng Bí thư Lê Duẩn..."
    assert _is_pure_thinking_indicator(real_outline) is False

    real_body = "Năm 1979, trước những diễn biến phức tạp của tình hình biên giới..."
    assert _is_pure_thinking_indicator(real_body) is False


def test_looks_like_chatgpt_error():
    assert looks_like_chatgpt_error("This response couldn't load") is True
    assert looks_like_chatgpt_error("This response couldn’t load") is True
    assert looks_like_chatgpt_error("Something went wrong") is True
    assert looks_like_chatgpt_error("There was an error generating a response") is True
    assert looks_like_chatgpt_error("[PHAN 1] Nội dung phần 1") is False


def test_payload_content_text_filtering():
    # Filter out thoughts
    msg_thoughts = {
        "author": {"role": "assistant"},
        "content": {"content_type": "thoughts", "parts": ["Reasoning steps..."]},
    }
    assert _get_payload_content_text(msg_thoughts) == ""

    # Filter out browser recipient
    msg_browser = {
        "author": {"role": "assistant"},
        "recipient": "browser",
        "content": {"content_type": "text", "parts": ["Searching web..."]},
    }
    assert _get_payload_content_text(msg_browser) == ""

    # Valid string parts
    msg_valid = {
        "author": {"role": "assistant"},
        "content": {"content_type": "text", "parts": ["Line 1", "Line 2"]},
    }
    assert _get_payload_content_text(msg_valid) == "Line 1\nLine 2"

    # Valid structured dict parts
    msg_structured = {
        "author": {"role": "assistant"},
        "content": {"content_type": "text", "parts": [{"text": "Part 1"}, {"text": "Part 2"}]},
    }
    assert _get_payload_content_text(msg_structured) == "Part 1\nPart 2"


def test_extract_assistant_response_from_conversation_payload():
    payload = {
        "current_node": "node_4",
        "mapping": {
            "node_1": {
                "id": "node_1",
                "parent": None,
                "message": {
                    "id": "node_1",
                    "author": {"role": "system"},
                    "content": {"content_type": "text", "parts": ["You are a helpful assistant"]},
                    "status": "finished_successfully",
                },
            },
            "node_2": {
                "id": "node_2",
                "parent": "node_1",
                "message": {
                    "id": "node_2",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Tạo dàn ý chi tiết cho chủ đề lịch sử"]},
                    "status": "finished_successfully",
                },
            },
            "node_3": {
                "id": "node_3",
                "parent": "node_2",
                "message": {
                    "id": "node_3",
                    "author": {"role": "assistant"},
                    "content": {"content_type": "thoughts", "parts": ["Checking historical claims..."]},
                    "status": "finished_successfully",
                },
            },
            "node_4": {
                "id": "node_4",
                "parent": "node_3",
                "message": {
                    "id": "node_4",
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "text",
                        "parts": [
                            "[PHAN 1] Bối cảnh lịch sử năm 1978\nNội dung chi tiết phần 1",
                            "[PHAN 2] Quyết sách chiến lược\nNội dung chi tiết phần 2",
                        ],
                    },
                    "status": "finished_successfully",
                    "end_turn": True,
                },
            },
        },
    }

    extracted = extract_assistant_response_from_conversation_payload(
        payload,
        expected_user_text="Tạo dàn ý chi tiết cho chủ đề lịch sử",
    )
    assert "[PHAN 1] Bối cảnh lịch sử năm 1978" in extracted
    assert "[PHAN 2] Quyết sách chiến lược" in extracted


def test_recover_assistant_response_from_backend(monkeypatch):
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/g/g-p-123/c/6ac76eef-3990-83ec-a218-0f3c09bf20a5"

    fake_payload = {
        "current_node": "node_2",
        "mapping": {
            "node_1": {
                "id": "node_1",
                "parent": None,
                "message": {
                    "id": "node_1",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Prompt 2: Tạo dàn ý..."]},
                    "status": "finished_successfully",
                },
            },
            "node_2": {
                "id": "node_2",
                "parent": "node_1",
                "message": {
                    "id": "node_2",
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "text",
                        "parts": ["[PHAN 1] Mở đầu lịch sử...", "[PHAN 2] Diễn biến trận đánh..."],
                    },
                    "status": "finished_successfully",
                    "end_turn": True,
                },
            },
        },
    }

    def fake_evaluate(script, arg=None):
        return fake_payload

    mock_page.evaluate = fake_evaluate

    recovered = recover_assistant_response_from_backend(mock_page, "Prompt 2: Tạo dàn ý...")
    assert "[PHAN 1] Mở đầu lịch sử..." in recovered
    assert "[PHAN 2] Diễn biến trận đánh..." in recovered


def test_accept_or_recover_on_thinking_indicator(monkeypatch):
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/g/g-p-123/c/6ac76eef-3990-83ec-a218-0f3c09bf20a5"

    fake_payload = {
        "current_node": "node_2",
        "mapping": {
            "node_1": {
                "id": "node_1",
                "parent": None,
                "message": {
                    "id": "node_1",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Prompt 2: Tạo dàn ý..."]},
                    "status": "finished_successfully",
                },
            },
            "node_2": {
                "id": "node_2",
                "parent": "node_1",
                "message": {
                    "id": "node_2",
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "text",
                        "parts": ["[PHAN 1] Dàn ý chuẩn xác từ API"],
                    },
                    "status": "finished_successfully",
                    "end_turn": True,
                },
            },
        },
    }

    mock_page.evaluate = lambda script, arg=None: fake_payload

    # When DOM returned thinking indicator 'Checked historical claims', _accept_or_recover_assistant_response must recover via backend
    result = _accept_or_recover_assistant_response(
        mock_page,
        "Checked historical claims",
        "Prompt 2: Tạo dàn ý...",
    )
    assert result == "[PHAN 1] Dàn ý chuẩn xác từ API"
