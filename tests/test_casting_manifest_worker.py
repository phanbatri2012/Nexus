import json
import unittest
from unittest.mock import MagicMock, patch

from nexus_engine.services import chatgpt_worker


class TestCastingManifestWorker(unittest.TestCase):
    def setUp(self):
        self.state = {
            "content_mode": "dialogue",
            "intro": "[MC]: Xin chào.\n[KHACH_1]: Em chào anh.",
            "body_parts": ["[MC]: Mời chia sẻ.\n[KHACH_1]: Tôi xin kể."],
            "outro": "[MC]: Cảm ơn.\n[KHACH_1]: Xin cảm ơn.",
            "casting_manifest": {},
            "current_step": "outro",
        }
        self.valid_manifest = json.dumps(
            {
                "speakers": [
                    {"speaker_key": "MC", "name": "MC", "gender": "male", "age_group": "adult", "language": "vi", "styles": ["calm"], "confidence": .99},
                    {"speaker_key": "GUEST1", "name": "Khách", "gender": "female", "age_group": "adult", "language": "vi", "styles": ["warm"], "confidence": .9},
                ]
            },
            ensure_ascii=False,
        )

    def test_invalid_json_retries_exactly_once_then_persists(self):
        with patch.object(
            chatgpt_worker,
            "send_or_recover_generation_prompt",
            side_effect=["not json", self.valid_manifest],
        ) as send, patch.object(
            chatgpt_worker, "clear_pending_generation_prompt"
        ) as clear, patch.object(
            chatgpt_worker, "persist_generation_state"
        ) as persist:
            result = chatgpt_worker.generate_casting_manifest(
                MagicMock(),
                self.state,
                {"casting_manifest": "Analyze:\n{script}"},
                required=True,
            )

        self.assertEqual(send.call_count, 2)
        self.assertEqual(clear.call_count, 2)
        self.assertGreaterEqual(persist.call_count, 2)
        self.assertEqual(result["speakers"][1]["speaker_key"], "KHACH_1")
        self.assertEqual(self.state["casting_manifest"], result)

    def test_missing_runtime_prompt_fails_when_radar_requires_manifest(self):
        with self.assertRaises(RuntimeError):
            chatgpt_worker.generate_casting_manifest(
                MagicMock(), self.state, {}, required=True
            )


if __name__ == "__main__":
    unittest.main()
