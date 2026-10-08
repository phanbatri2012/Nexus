import unittest
from auto_yt.services.chatgpt_worker import (
    split_outline_parts,
    strip_outline_preamble,
    clean_text,
    sanitize_narrative_response,
    validate_metadata_response,
)
from auto_yt.services.audio_review import looks_like_editorial_artifact


class TestLatexAndCanvasCleaner(unittest.TestCase):
    def test_split_outline_latex_escaped_brackets(self):
        outline = r"""
\[PHAN\]
1. Bối cảnh lịch sử và quan hệ Việt - Trung
- Những năm tháng kháng chiến chống Pháp và chống Mỹ...

\[PHAN\]
2. Hiệp định Genève 1954 và bài học đắt giá
- TBT Lê Duẩn trực tiếp chứng kiến những toan tính...

\[PHAN\]
3. Cuộc chiến bảo vệ biên giới 1979
- Chi tiết đám cưới con trai và sự bình thản đầy bản lĩnh...
"""
        parts = split_outline_parts(outline)
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertNotIn(r"\[PHAN\]", part)
            self.assertNotIn("[PHAN]", part)

    def test_split_outline_unnumbered_latex(self):
        outline = r"""
\[PHAN\]
Bối cảnh lịch sử và sự phức tạp trong quan hệ Việt - Trung
- Những năm tháng gắn bó thời kỳ kháng chiến...

\[PHAN\]
Hiệp định Genève 1954 và bài học đắt giá
- TBT Lê Duẩn trực tiếp chứng kiến những toan tính...

\[PHAN\]
Cuộc đấu trí ngoại giao và lập trường kiên định
- Những cuộc gặp căng thẳng với Mao Trạch Đông...
"""
        parts = split_outline_parts(outline)
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].startswith("Bối cảnh lịch sử"))
        self.assertTrue(parts[1].startswith("Hiệp định Genève"))
        self.assertTrue(parts[2].startswith("Cuộc đấu trí ngoại giao"))

    def test_clean_text_unescapes_latex_punctuation(self):
        raw = r"BÍ MẬT VỀ MỐI HIỂM HỌA PHƯƠNG BẮC\! \#GocKhuatVietSu \[PHAN\] \: \? \- \*"
        cleaned = clean_text(raw)
        self.assertIn("BÍ MẬT VỀ MỐI HIỂM HỌA PHƯƠNG BẮC!", cleaned)
        self.assertIn("#GocKhuatVietSu", cleaned)
        self.assertNotIn(r"\!", cleaned)
        self.assertNotIn(r"\#", cleaned)
        self.assertNotIn(r"\:", cleaned)
        self.assertNotIn(r"\?", cleaned)
        self.assertNotIn(r"\-", cleaned)

    def test_canvas_editorial_note_rejection(self):
        self.assertTrue(looks_like_editorial_artifact("Refined framing of historical tensions"))
        self.assertTrue(looks_like_editorial_artifact("Updated the script for better narrative flow"))
        self.assertTrue(looks_like_editorial_artifact("Polished section 2"))
        
        # In sanitize_narrative_response, a pure canvas note should return empty string
        res = sanitize_narrative_response("Refined framing of historical tensions")
        self.assertEqual(res, "")

    def test_validate_metadata_clean_escapes(self):
        raw_meta = r"""
### [TIÊU ĐỀ]
LÊ DUẨN ĐÃ NHÌN THẤY ĐIỀU GÌ\? BÍ MẬT VỀ MỐI HIỂM HỌA PHƯƠNG BẮC\!

### [URL SLUG]
le-duan-moi-hiem-hoa-phuong-bac

### [MÔ TẢ]
Mô tả chi tiết câu chuyện lịch sử...

### [HASHTAG]
\#GocKhuatVietSu \#LeDuan

### [TAGS]
Lê Duẩn, chiến tranh biên giới

### [BÌNH LUẬN GHIM]
Bình luận thảo luận\!

### [QUIZ]
Câu hỏi tương tác
A. 1954
B. 1975
C. 1979
D. 1986
Đáp án: C
Giải thích: 1979
"""
        validated = validate_metadata_response(raw_meta)
        self.assertIn("LÊ DUẨN ĐÃ NHÌN THẤY ĐIỀU GÌ? BÍ MẬT VỀ MỐI HIỂM HỌA PHƯƠNG BẮC!", validated)
        self.assertIn("#GocKhuatVietSu #LeDuan", validated)
        self.assertIn("Bình luận thảo luận!", validated)
        self.assertNotIn(r"\?", validated)
        self.assertNotIn(r"\!", validated)
        self.assertNotIn(r"\#", validated)


    def test_thinking_indicator_dynamic_patterns(self):
        from auto_yt.services.chatgpt_worker import _is_pure_thinking_indicator
        self.assertTrue(_is_pure_thinking_indicator("Verifying historical figures"))
        self.assertTrue(_is_pure_thinking_indicator("Checking historical dates"))
        self.assertTrue(_is_pure_thinking_indicator("Searching for references"))
        self.assertTrue(_is_pure_thinking_indicator("Analyzing outline structure"))
        self.assertTrue(_is_pure_thinking_indicator("Worked for 20s"))
        self.assertTrue(_is_pure_thinking_indicator("thought for 5 seconds"))
        self.assertTrue(_is_pure_thinking_indicator("đang suy nghĩ..."))
        # Genuine Vietnamese outline should not be treated as a thinking indicator
        self.assertFalse(_is_pure_thinking_indicator("Bối cảnh lịch sử và sự phức tạp trong quan hệ Việt - Trung những năm 1954-1979"))

    def test_payload_content_ignores_thoughts_and_tools(self):
        from auto_yt.services.chatgpt_worker import _get_payload_content_text, _get_completed_payload_assistant_text
        thought_msg = {
            "author": {"role": "assistant"},
            "content": {"content_type": "thoughts", "parts": ["Verifying historical figures"]},
            "status": "finished_successfully",
            "end_turn": True
        }
        self.assertEqual(_get_payload_content_text(thought_msg), "")
        self.assertEqual(_get_completed_payload_assistant_text(thought_msg), "")

        tool_msg = {
            "author": {"role": "assistant"},
            "recipient": "browser",
            "content": {"content_type": "text", "parts": ["search('Le Duan 1979')"]},
            "status": "finished_successfully",
            "end_turn": True
        }
        self.assertEqual(_get_payload_content_text(tool_msg), "")
        self.assertEqual(_get_completed_payload_assistant_text(tool_msg), "")

    def test_select_reusable_outline_rejects_short_thought_status(self):
        from auto_yt.services.chatgpt_worker import select_reusable_outline_response
        turns = [
            ("user", "Hãy tạo dàn ý cho kịch bản..."),
            ("assistant", "Verifying historical figures")
        ]
        self.assertEqual(select_reusable_outline_response(turns), "")

    def test_is_core_script_complete_requires_substantive_parts(self):
        from auto_yt.services.chatgpt_worker import is_core_script_complete
        state_incomplete = {
            "intro": "Mở đầu kịch bản...",
            "outro": "Kết thúc video...",
            "expected_body_parts": 2,
            "body_parts": ["Refined framing of historical tensions"]
        }
        self.assertFalse(is_core_script_complete("transcript", state_incomplete))

        state_complete = {
            "intro": "Chào mừng quý vị đến với kênh Góc Khuất Việt Sử trong video hôm nay về bài học lịch sử sâu sắc.",
            "outro": "Cảm ơn quý vị đã theo dõi toàn bộ video hôm nay. Hãy like và subscribe kênh để đón xem tiếp.",
            "expected_body_parts": 2,
            "body_parts": [
                "Nội dung phần 1 kể về hoàn cảnh lịch sử đầy biến động của hiệp định Genève năm 1954 và những bài học xương máu.",
                "Nội dung phần 2 tiếp tục với cuộc chiến tranh bảo vệ biên giới phía Bắc năm 1979 và bản lĩnh của Tổng Bí thư Lê Duẩn."
            ]
        }
        self.assertTrue(is_core_script_complete("transcript", state_complete))


if __name__ == "__main__":
    unittest.main()
