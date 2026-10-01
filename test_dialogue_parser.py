# test_dialogue_parser.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import unittest
from auto_yt.dialogue_parser import (
    parse_dialogue_turns,
    is_dialogue_script,
    clean_turn_text_for_tts,
    split_turn_into_sentences,
    normalize_role_tag,
    extract_dialogue_speakers,
    resolve_turn_voice,
    resolve_fallback_guest_voice,
    build_dialogue_tts_segments,
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
        text = "### [INTRO]\n[MC]: - Chào bạn (cười), [thở dài] câu chuyện này thật là kỳ lạ *bật khóc*—tôi không ngờ tới."
        cleaned = clean_turn_text_for_tts(text)
        self.assertNotIn("### [INTRO]", cleaned)
        self.assertNotIn("[MC]:", cleaned)
        self.assertNotIn("(cười)", cleaned)
        self.assertNotIn("[thở dài]", cleaned)
        self.assertNotIn("*bật khóc*", cleaned)
        self.assertNotIn("—", cleaned)
        self.assertFalse(cleaned.startswith("-"))
        self.assertIn("Chào bạn, câu chuyện này thật là kỳ lạ, tôi không ngờ tới.", cleaned)

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

    def test_resolve_turn_voice_and_smart_fallback(self):
        available_voices = [
            {"id": "voice_mc_dinh_doan", "name": "Đinh Đoàn", "status": "active"},
            {"id": "voice_guest_lan", "name": "Chị Lan", "status": "active"},
            {"id": "voice_guest_backup", "name": "Bác Sĩ Nam", "status": "active"},
        ]
        # Dict format
        cast_settings = {
            "mc": {"voice_id": "voice_mc_dinh_doan"},
            "guest_1": {"voice_id": "voice_guest_lan"},
            "guest_2": {"voice_id": "auto"} # Auto fallback
        }
        self.assertEqual(resolve_turn_voice("MC", cast_settings), "voice_mc_dinh_doan")
        self.assertEqual(resolve_turn_voice("KHACH_1", cast_settings), "voice_guest_lan")
        g2_voice = resolve_turn_voice("KHACH_2", cast_settings, available_voices=available_voices)
        self.assertEqual(g2_voice, "voice_guest_backup")

        # Flat string format (from API cast_voice_overrides)
        flat_cast = {
            "MC": "voice_mc_dinh_doan",
            "KHACH_1": "voice_guest_lan",
        }
        self.assertEqual(resolve_turn_voice("MC", flat_cast), "voice_mc_dinh_doan")
        self.assertEqual(resolve_turn_voice("KHACH_1", flat_cast), "voice_guest_lan")

    def test_build_dialogue_tts_segments_consecutive_batching(self):
        script = """
[MC]: Chào quý vị khán giả.
[MC]: Hôm nay là một chủ đề rất đặc biệt.
[KHACH_1]: Con chào bác Sâm ạ.
[KHACH_1]: Con muốn tâm sự chuyện gia đình.
[MC]: Bác luôn sẵn sàng lắng nghe con.
"""
        available_voices = [
            {"id": "v_mc", "name": "MC Voice"},
            {"id": "v_guest", "name": "Guest Voice"},
        ]
        cast_settings = {
            "MC": "v_mc",
            "KHACH_1": "v_guest",
        }
        segments = build_dialogue_tts_segments(
            script,
            cast_settings=cast_settings,
            available_voices=available_voices,
            max_segment_chars=5000,
        )
        # Should produce 3 segments: (MC turn 1+2), (Guest turn 1+2), (MC turn 3)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["role"], "MC")
        self.assertEqual(segments[0]["voice_id"], "v_mc")
        self.assertEqual(segments[0]["voice_name"], "MC Voice")
        self.assertIn("Chào quý vị khán giả.", segments[0]["text"])
        self.assertIn("Hôm nay là một chủ đề rất đặc biệt.", segments[0]["text"])

        self.assertEqual(segments[1]["role"], "KHACH_1")
        self.assertEqual(segments[1]["voice_id"], "v_guest")
        self.assertIn("Con chào bác Sâm ạ.", segments[1]["text"])
        self.assertIn("Con muốn tâm sự chuyện gia đình.", segments[1]["text"])

        self.assertEqual(segments[2]["role"], "MC")
        self.assertEqual(segments[2]["voice_id"], "v_mc")


if __name__ == "__main__":
    unittest.main()
