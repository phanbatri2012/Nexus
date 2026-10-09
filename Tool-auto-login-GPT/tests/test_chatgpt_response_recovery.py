import pytest
import time
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
    wait_for_assistant_response,
    split_outline_parts,
    get_reusable_outline_response,
)


def test_is_pure_thinking_indicator():
    # Single line indicators
    assert _is_pure_thinking_indicator("Worked for 1m 47s") is True
    assert _is_pure_thinking_indicator("Checked historical claims") is True
    assert _is_pure_thinking_indicator("Clarified narrative claims and structured the account") is True
    assert _is_pure_thinking_indicator("Structured the chronological timeline") is True
    assert _is_pure_thinking_indicator("Detailed key historical milestones") is True
    assert _is_pure_thinking_indicator("Formulated outline structure") is True
    assert _is_pure_thinking_indicator("Examined Soviet-Vietnamese treaty context") is True
    assert _is_pure_thinking_indicator("Thought for 32 seconds") is True
    assert _is_pure_thinking_indicator("Searching for Vietnamese history") is True
    assert _is_pure_thinking_indicator("Analyzed historical documents") is True
    assert _is_pure_thinking_indicator("Đã kiểm tra tài liệu lịch sử") is True
    assert _is_pure_thinking_indicator("Làm rõ các luận điểm chính") is True
    assert _is_pure_thinking_indicator("Đang suy nghĩ...") is True
    assert _is_pure_thinking_indicator("Stopped thinking") is True
    # Vietnamese thinking verbs (including Video 281 case)
    assert _is_pure_thinking_indicator("Sắp xếp mốc lịch sử") is True
    assert _is_pure_thinking_indicator("Liệt kê các mốc chính") is True
    assert _is_pure_thinking_indicator("Trình bày bối cảnh lịch sử") is True
    assert _is_pure_thinking_indicator("Khái quát các giai đoạn") is True
    assert _is_pure_thinking_indicator("Xâu chuỗi diễn biến") is True
    assert _is_pure_thinking_indicator("Lược thuật cuộc xung đột") is True
    assert _is_pure_thinking_indicator("Sơ lược nội dung") is True
    assert _is_pure_thinking_indicator("Xây dựng dàn ý chi tiết") is True

    # Multi-line indicators
    multiline = "Worked for 1m 26s\nClarified narrative claims and structured the account"
    assert _is_pure_thinking_indicator(multiline) is True
    multiline_vn = "Sắp xếp mốc lịch sử\nĐã kiểm tra tài liệu tham khảo"
    assert _is_pure_thinking_indicator(multiline_vn) is True

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


def test_wait_for_assistant_response_handles_transient_render_crash(monkeypatch):
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/g/g-p-123/c/6ac76eef-3990-83ec-a218-0f3c09bf20a5"

    call_count = [0]
    
    finished_payload = {
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
                        "parts": ["[PHAN 1] Kịch bản hoàn thành ở phút thứ 3"],
                    },
                    "status": "finished_successfully",
                    "end_turn": True,
                },
            },
        },
    }

    # Simulate: 
    # At first, evaluate calls return in_progress or render crash
    # On 2nd backend poll, evaluate returns finished_payload
    def fake_evaluate(script, arg=None):
        script_str = str(script)
        if "backend-api/conversation" in script_str:
            call_count[0] += 1
            if call_count[0] >= 2:
                return finished_payload
            return {"mapping": {}}
        if "this response couldn't load" in script_str:
            return True # Render error present
        return False

    mock_page.evaluate = fake_evaluate

    # Mock DOM message extractors to simulate crashed DOM
    monkeypatch.setattr("auto_yt.services.chatgpt_worker.get_new_assistant_response", lambda *args, **kwargs: "")
    monkeypatch.setattr("auto_yt.services.chatgpt_worker.get_assistant_response_after_latest_user", lambda *args, **kwargs: "This response couldn't load")
    monkeypatch.setattr("auto_yt.services.chatgpt_worker.is_chatgpt_generation_active", lambda *args: False)
    monkeypatch.setattr("auto_yt.services.chatgpt_worker.accept_external_app_permission_dialog", lambda *args: False)

    response = wait_for_assistant_response(
        mock_page,
        previous_assistant_turn=0,
        submitted_prompt_text="Prompt 2: Tạo dàn ý...",
        timeout=10,
    )

    assert response == "[PHAN 1] Kịch bản hoàn thành ở phút thứ 3"


def test_extract_payload_with_turn_exchange_id_and_thought_node():
    payload = {
        "current_node": "node_thought",
        "mapping": {
            "node_user": {
                "id": "node_user",
                "parent": None,
                "message": {
                    "id": "node_user",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Prompt 2: Tạo dàn ý..."]},
                    "status": "finished_successfully",
                    "metadata": {"turn_exchange_id": "exchange_1"},
                },
            },
            "node_thought": {
                "id": "node_thought",
                "parent": "node_user",
                "message": {
                    "id": "node_thought",
                    "author": {"role": "assistant"},
                    "content": {"content_type": "thoughts", "parts": ["Clarified narrative claims and structured the account"]},
                    "status": "finished_successfully",
                    "metadata": {"turn_exchange_id": "exchange_1"},
                },
            },
            "node_assistant": {
                "id": "node_assistant",
                "parent": "node_thought",
                "message": {
                    "id": "node_assistant",
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "text",
                        "parts": ["[PHAN 1] Mở đầu lịch sử...", "[PHAN 2] Diễn biến chi tiết..."],
                    },
                    "status": "finished_successfully",
                    "end_turn": True,
                    "metadata": {"turn_exchange_id": "exchange_1"},
                },
            },
        },
    }

    extracted = extract_assistant_response_from_conversation_payload(
        payload,
        expected_user_text="Prompt 2: Tạo dàn ý...",
    )
    assert "[PHAN 1] Mở đầu lịch sử..." in extracted
    assert "[PHAN 2] Diễn biến chi tiết..." in extracted


def test_split_outline_parts_filters_thought_headers():
    outline_with_thought = (
        "Clarified narrative claims and structured the account\n\n"
        "[PHAN 1] Phần mở đầu lịch sử về cố Tổng Bí thư Lê Duẩn và quan điểm chiến lược.\n\n"
        "[PHAN 2] Diễn biến và các phân tích sâu về mối bang giao và bài học giữ nước."
    )
    parts = split_outline_parts(outline_with_thought)
    assert len(parts) == 2
    assert "Clarified narrative claims" not in parts[0]
    assert "Phần mở đầu lịch sử" in parts[0]
    assert "Diễn biến và các phân tích sâu" in parts[1]


def test_get_reusable_outline_response_ignores_busy_page(monkeypatch):
    page = MagicMock()
    page.url = "https://chatgpt.com/g/g-p-test/c/test-uuid"

    # 1. When generation is actively running, should immediately return ""
    monkeypatch.setattr(
        "auto_yt.services.chatgpt_worker.is_chatgpt_generation_active",
        lambda p: True,
    )
    assert get_reusable_outline_response(page) == ""

    # 2. When generation is idle and DOM has valid completed outline
    monkeypatch.setattr(
        "auto_yt.services.chatgpt_worker.is_chatgpt_generation_active",
        lambda p: False,
    )
    valid_outline_dom = [
        ("user", "Prompt 2: Tạo dàn ý..."),
        (
            "assistant",
            "[PHAN 1] Bối cảnh lịch sử và tư tưởng chiến lược năm 1978.\n\n"
            "[PHAN 2] Diễn biến ngoại giao và hiệp ước phòng thủ chung.\n\n"
            "[PHAN 3] Bài học kinh nghiệm giữ nước và bảo vệ biên cương.",
        ),
    ]
    page.evaluate.return_value = valid_outline_dom
    res = get_reusable_outline_response(page)
    assert "[PHAN 1]" in res
    assert "[PHAN 2]" in res

    # 3. When DOM has only a thinking status (e.g. "Sắp xếp mốc lịch sử"), it should not return it
    thinking_only_dom = [
        ("user", "Prompt 2: Tạo dàn ý..."),
        ("assistant", "Sắp xếp mốc lịch sử"),
    ]
    page.evaluate.return_value = thinking_only_dom
    monkeypatch.setattr(
        "auto_yt.services.chatgpt_worker.recover_assistant_response_from_backend",
        lambda p: "",
    )
    assert get_reusable_outline_response(page) == ""


def test_history_prompt_text_matches_distinct_pipeline_prompts():
    from auto_yt.services.chatgpt_worker import history_prompt_text_matches

    p_title = "Dựa vào toàn bộ nội dung kịch bản lịch sử vừa viết, hãy đặt một tiêu đề video YouTube cực kỳ giật gân"
    p_slug = "Dựa vào nội dung kịch bản lịch sử vừa viết, hãy tạo một URL slug ngắn gọn, chuẩn SEO"
    p_tags = "Dựa vào toàn bộ nội dung video vừa viết, hãy tạo danh sách từ 10 đến 15 thẻ từ khóa (tags)"
    p_pinned = "Với vai trò là chủ kênh 'Góc Khuất Việt Sử', hãy viết một bình luận ghim (Pinned Comment)"
    p_quiz = "Dựa vào nội dung lịch sử vừa viết, hãy tạo 1 câu hỏi trắc nghiệm lịch sử hấp dẫn"
    p_hashtags = "Dựa vào nội dung video lịch sử vừa viết, hãy đề xuất chính xác từ 3 đến 5 hashtag"

    # Self matching
    assert history_prompt_text_matches(p_title, p_title) is True
    assert history_prompt_text_matches(p_slug, p_slug) is True
    assert history_prompt_text_matches(p_tags, p_tags) is True
    assert history_prompt_text_matches(p_pinned, p_pinned) is True
    assert history_prompt_text_matches(p_quiz, p_quiz) is True
    assert history_prompt_text_matches(p_hashtags, p_hashtags) is True

    # Cross matching MUST be False
    assert history_prompt_text_matches(p_title, p_slug) is False
    assert history_prompt_text_matches(p_title, p_tags) is False
    assert history_prompt_text_matches(p_slug, p_tags) is False
    assert history_prompt_text_matches(p_quiz, p_pinned) is False
    assert history_prompt_text_matches(p_hashtags, p_tags) is False


def test_sanitize_and_repair_metadata():
    from auto_yt.services.chatgpt_worker import sanitize_and_repair_metadata, build_video_script

    state = {
        "intro": "Đoạn mở đầu",
        "body_parts": ["Phần thân bài 1"],
        "outro": "Đoạn kết luận lịch sử.",
        "title": "Lịch sử có thể lùi xa, những cuộc chiến có thể khép lại... Hẹn gặp lại quý vị trong những hành trình khám phá lịch sử tiếp theo của Góc Khuất Việt Sử.",
        "slug": "LÊ DUẨN ĐÃ NHÌN THẤU TRUNG QUỐC? Góc Khuất Trước CHIẾN TRANH 1979",
        "description": "le-duan-nhin-thau-trung-quoc-chien-tranh-1979",
        "pinned_comment": "🇻🇳 Bình luận ghim chuẩn.",
        "quiz": "🇻🇳 Bình luận ghim chuẩn.",
    }

    sanitize_and_repair_metadata(state)

    assert state["title"] == "LÊ DUẨN ĐÃ NHÌN THẤU TRUNG QUỐC? Góc Khuất Trước CHIẾN TRANH 1979"
    assert state["slug"] == "le-duan-nhin-thau-trung-quoc-chien-tranh-1979"
    assert state["description"] == ""
    assert state["quiz"] == ""
    assert "Hẹn gặp lại quý vị" in state["outro"]

    script = build_video_script(state)
    assert "### [TIÊU ĐỀ]\nLÊ DUẨN ĐÃ NHÌN THẤU TRUNG QUỐC? Góc Khuất Trước CHIẾN TRANH 1979" in script
    assert "### [SLUG]\nle-duan-nhin-thau-trung-quoc-chien-tranh-1979" in script


def test_extract_generated_video_title_with_outro_fallback():
    from auto_yt.services.database import extract_generated_video_title

    script_corrupted = """### [INTRO]
Intro text

### [BODY]
Body text

### [OUTRO]
Outro text

### [TIÊU ĐỀ]
Lịch sử có thể lùi xa, những cuộc chiến có thể khép lại, nhưng bài học về độc lập, chủ quyền và quyền tự quyết của dân tộc sẽ không bao giờ mất đi giá trị. Hẹn gặp lại quý vị trong những hành trình khám phá lịch sử tiếp theo của Góc Khuất Việt Sử.

### [SLUG]
LÊ DUẨN ĐÃ NHÌN THẤU TRUNG QUỐC? Góc Khuất Trước CHIẾN TRANH 1979

### [MÔ TẢ]
le-duan-nhin-thau-trung-quoc-chien-tranh-1979
"""
    title = extract_generated_video_title(script_corrupted)
    assert title == "LÊ DUẨN ĐÃ NHÌN THẤU TRUNG QUỐC? Góc Khuất Trước CHIẾN TRANH 1979"


def test_cross_metadata_prompt_matching_isolation():
    from auto_yt.services.chatgpt_worker import history_prompt_text_matches

    tag_prompt = "Hãy tạo 15 thẻ từ khóa (tags) chuẩn SEO cho video YouTube này, ngăn cách bằng dấu phẩy."
    hashtag_prompt = "Hãy tạo 5-8 hashtag chuẩn SEO cho video YouTube này, bắt đầu bằng dấu #."
    slug_prompt = "Hãy tạo 1 URL slug ngắn gọn, không dấu, nối bằng gạch ngang cho video này."
    desc_prompt = "Hãy viết phần mô tả video chi tiết khoảng 1000-1500 ký tự tóm tắt câu chuyện."
    pinned_prompt = "Hãy viết 1 bình luận ghim kêu gọi khán giả tương tác cho video này."
    quiz_prompt = "Hãy tạo 1 câu hỏi trắc nghiệm tương tác với 4 lựa chọn A, B, C, D và đáp án giải thích."
    chapter_prompt = "Hãy tạo các mốc thời gian (chapters / dòng thời gian) cho video này từ 00:00."
    thumb_prompt = "Hãy tạo prompt tạo ảnh thumbnail cho video này để vẽ trên DALL-E."

    # All distinct metadata prompt pairs MUST NOT match each other
    assert not history_prompt_text_matches(tag_prompt, hashtag_prompt)
    assert not history_prompt_text_matches(hashtag_prompt, tag_prompt)
    assert not history_prompt_text_matches(slug_prompt, desc_prompt)
    assert not history_prompt_text_matches(desc_prompt, slug_prompt)
    assert not history_prompt_text_matches(pinned_prompt, quiz_prompt)
    assert not history_prompt_text_matches(quiz_prompt, chapter_prompt)
    assert not history_prompt_text_matches(chapter_prompt, thumb_prompt)
    assert not history_prompt_text_matches(thumb_prompt, chapter_prompt)

    # Identical or slightly formatted prompts in same category MUST match
    assert history_prompt_text_matches(tag_prompt, tag_prompt + "\n\nLƯU Ý QUAN TRỌNG: STRICT_NO_FILLER")
    assert history_prompt_text_matches(slug_prompt, "url slug: " + slug_prompt)


def test_self_healing_extractors_on_shifted_metadata():
    from auto_yt.services.database import (
        extract_generated_video_quiz,
        extract_generated_video_chapters,
    )

    # Simulated shifted script where Quiz is inside CHAPTERS section
    shifted_script = """### [INTRO]
Mở đầu video

### [BODY]
Thân bài video

### [OUTRO]
Kết thúc video

### [QUIZ]
Cảm ơn quý vị đã theo dõi video. Hãy để lại bình luận nhé!

### [CHAPTERS]
Câu hỏi: Nhân vật chính giữ cấp bậc gì?
A. Thiếu úy
B. Trung úy
C. Đại úy
D. Thiếu tá
Đáp án: C. Đại úy

### [THUMBNAIL_PROMPT_TEXT]
00:00 - Mở đầu
10:00 - Diễn biến kịch tính
20:00 - Sự thật hé lộ
"""

    quiz = extract_generated_video_quiz(shifted_script)
    assert "Đáp án: C. Đại úy" in quiz
    assert "A. Thiếu úy" in quiz

    chapters = extract_generated_video_chapters(shifted_script)
    assert "00:00 - Mở đầu" in chapters
    assert "10:00 - Diễn biến kịch tính" in chapters
    assert "20:00 - Sự thật hé lộ" in chapters



