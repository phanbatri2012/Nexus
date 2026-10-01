import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt import main
from auto_yt.services import chatgpt_projects


PROJECT_URL = chatgpt_projects.DEFAULT_CHATGPT_PROJECT_URL


def make_prompts_data() -> dict:
    return {
        "active_version": "default",
        "versions": {
            "default": {
                "name": "Talkshow Tâm Lý Đinh Đoàn (MC & Khách Mời)",
                "project_url": PROJECT_URL,
                "content_mode": "dialogue",
                "default_voice_id": "",
                "cast_settings": {
                    "mc": {"voice_id": "", "name": "MC Đinh Đoàn", "role_desc": "Người dẫn dắt talkshow"},
                    "guest_1": {"voice_id": "", "name": "Khách Mời 1", "role_desc": "Khách mời chia sẻ"},
                    "guest_2": {"enabled": False, "voice_id": "", "name": "Khách Mời 2", "role_desc": "Khách mời phụ"},
                    "turn_pause_seconds": 0.35,
                },
                "prompts": {
                    key: f"prompt-{key}" for key in main.PROMPT_FIELD_KEYS
                },
            },
        },
    }


class DialogueSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.prompts_path = Path(self.temporary_directory.name) / "prompts.json"
        self.prompts_path.write_text(
            json.dumps(make_prompts_data(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.path_patch = patch.object(main, "PROMPTS_PATH", self.prompts_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temporary_directory.cleanup()

    def read_saved_data(self) -> dict:
        return json.loads(self.prompts_path.read_text(encoding="utf-8"))

    def test_save_prompt_cast_settings_and_normalization(self):
        result = main.save_prompt_cast_settings(
            "default",
            main.PromptCastSettingsData(
                cast_settings={
                    "mc": {"voice_id": "voice-mc-1", "name": "MC Đinh Đoàn", "role_desc": "Host"},
                    "guest_1": {"voice_id": "voice-guest-1", "name": "Chị Hoa", "role_desc": "Khách mời"},
                    "guest_2": {"enabled": True, "voice_id": "voice-guest-2", "name": "Anh Tuấn", "role_desc": "Khách mời 2"},
                    "turn_pause_seconds": 0.4,
                }
            ),
        )
        saved = self.read_saved_data()
        saved_cast = saved["versions"]["default"]["cast_settings"]
        self.assertEqual(saved_cast["mc"]["voice_id"], "voice-mc-1")
        self.assertEqual(saved_cast["guest_1"]["voice_id"], "voice-guest-1")
        self.assertTrue(saved_cast["guest_2"]["enabled"])
        self.assertEqual(saved_cast["turn_pause_seconds"], 0.4)
        self.assertEqual(result["version"]["cast_settings"]["mc"]["name"], "MC Đinh Đoàn")

    def test_save_prompt_content_mode(self):
        result = main.save_prompt_content_mode(
            "default",
            main.PromptContentModeData(content_mode="monologue"),
        )
        saved = self.read_saved_data()
        self.assertEqual(saved["versions"]["default"]["content_mode"], "monologue")
        self.assertEqual(result["version"]["content_mode"], "monologue")


if __name__ == "__main__":
    unittest.main()
