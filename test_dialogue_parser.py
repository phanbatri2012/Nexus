# test_dialogue_parser.py
import unittest
from auto_yt.dialogue_parser import (
    parse_dialogue_turns,
    is_dialogue_script,
    clean_turn_text_for_tts,
    split_turn_into_sentences,
    normalize_role_tag,
    extract_dialogue_speakers
)


class TestDialogueParser(unittest.TestCase):
    def test_normalize_role_tag(self):
        self.assertEqual(normalize_role_tag("MC"), "MC")
        self.assertEqual(normalize_role_tag("mc"), "MC")
        self.assertEqual(normalize_role_tag("Host"), "MC")
        self.assertEqual(normalize_role_tag("Dẫn chuyện"), "MC")
        self.assertEqual(normalize_role_tag("KHACH_1"), "KHACH_1")
        self.assertEqual(normalize_role_tag("khach 1"), "KHACH_1")
        self.assertEqual(normalize_role_tag("Khách"), "KHACH_1")
        self.assertEqual(normalize_role_tag("Người tâm sự"), "KHACH_1")
        self.assertEqual(normalize_role_tag("Guest 2"), "KHACH_2")

    def test_is_dialogue_script(self):
        self.assertFalse(is_dialogue_script("Đây là đoạn văn đơn thoại thông thường."))
        self.assertTrue(is_dialogue_script("[MC]: Xin chào quý vị.\n[KHACH_1]: Chào tiến sĩ."))

    def test_clean_turn_text_for_tts(self):
        text = "- Chào bạn (cười), câu chuyện này thật là kỳ lạ—tôi không ngờ tới."
        cleaned = clean_turn_text_for_tts(text)
        self.assertNotIn("(cười)", cleaned)
        self.assertNotIn("—", cleaned)
        self.assertFalse(cleaned.startswith("-"))

    def test_split_turn_into_sentences(self):
        text = "Câu thứ nhất rất hay! Câu thứ hai cũng thế? Cuối cùng là câu ba."
        sentences = split_turn_into_sentences(text)
        self.assertEqual(len(sentences), 3)
        self.assertEqual(sentences[0], "Câu thứ nhất rất hay!")
        self.assertEqual(sentences[1], "Câu thứ hai cũng thế?")
        self.assertEqual(sentences[2], "Cuối cùng là câu ba.")

    def test_parse_dialogue_turns(self):
        script = """
[MC]: Xin chào quý vị khán giả. Hôm nay chúng ta cùng gặp gỡ khách mời Lan.
[KHACH_1]: Em xin chào tiến sĩ Đinh Đoàn. Câu chuyện của em bắt đầu từ 5 năm trước. Lúc đó em mới sinh con đầu lòng.
[MC]: Bạn cứ từ từ chia sẻ nhé.
[KHACH_2]: Tôi xin phép bổ sung thêm một góc nhìn pháp lý.
"""
        turns = parse_dialogue_turns(script)
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[0]["role"], "MC")
        self.assertEqual(len(turns[0]["sentences"]), 2)

        self.assertEqual(turns[1]["role"], "KHACH_1")
        self.assertEqual(len(turns[1]["sentences"]), 3)

        self.assertEqual(turns[2]["role"], "MC")
        self.assertEqual(turns[3]["role"], "KHACH_2")

        speakers = extract_dialogue_speakers(script)
        self.assertEqual(speakers, ["MC", "KHACH_1", "KHACH_2"])

    def test_monologue_fallback(self):
        script = "Ngày xửa ngày xưa, ở một ngôi làng nọ có hai vợ chồng nghèo sống rất hạnh phúc."
        turns = parse_dialogue_turns(script)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["role"], "MC")
        self.assertEqual(turns[0]["text"], script)


if __name__ == "__main__":
    unittest.main()
