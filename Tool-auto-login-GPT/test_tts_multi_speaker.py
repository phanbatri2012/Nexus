# test_tts_multi_speaker.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import unittest
from unittest.mock import patch, MagicMock
import json
from auto_yt import main
from auto_yt.services import database as db
from auto_yt.services import voice_config
from auto_yt.dialogue_parser import is_dialogue_script, build_dialogue_tts_segments


class TestMultiSpeakerAudioPipeline(unittest.TestCase):
    def setUp(self):
        self.mock_db = {}

    @patch("auto_yt.main.db.get_video")
    @patch("auto_yt.main.db.get_audio_task")
    @patch("auto_yt.main.db.upsert_audio_task")
    @patch("auto_yt.main.tts.find_matching_task")
    @patch("auto_yt.main.tts.submit_tts_task")
    @patch("auto_yt.main._require_audio_review_approval")
    @patch("auto_yt.main.voice_config.load_voice_config")
    @patch("auto_yt.main._start_audio_watcher")
    def test_ensure_audio_task_multi_speaker_dialogue(
        self,
        mock_watcher,
        mock_load_voice_config,
        mock_review_approval,
        mock_submit_tts,
        mock_find_matching,
        mock_upsert_audio,
        mock_get_audio,
        mock_get_video,
    ):
        mock_load_voice_config.return_value = {
            "voices": [
                {"id": "voice_mc_sam", "name": "MC Văn Sâm", "provider_id": "genmax", "status": "active"},
                {"id": "voice_guest_lan", "name": "Khách Mời Lan", "provider_id": "genmax", "status": "active"},
            ]
        }
        mock_get_audio.return_value = None
        mock_find_matching.return_value = None
        mock_submit_tts.side_effect = lambda text, v_id, *a, **kw: {
            "id": f"task_{v_id}_{hash(text)}",
            "status": "pending",
            "result": {"audio_url": ""},
            "error": "",
        }
        mock_upsert_audio.side_effect = lambda **kw: kw

        script = """### [INTRO]
[MC]: Xin chào quý vị khán giả đang theo dõi kênh Sâm Audio, Tôi là MC Văn Sâm.
[MC]: Hôm nay trường quay của chúng ta đón nhận câu chuyện từ một bạn gái trẻ.
[KHACH_1]: (nghẹn ngào) Con chào bác Sâm, con chào tất cả quý vị.
[KHACH_1]: Con đã phải chịu đựng rất nhiều tổn thương trong những năm qua.
[MC]: Con cứ bình tĩnh kể lại cho bác và khán giả cùng nghe nhé.
### [OUTRO]
[MC]: Cảm ơn quý vị đã lắng nghe.
"""
        mock_get_video.return_value = {
            "id": 999,
            "title": "Video Multi-Speaker Test",
            "generated_script": script,
            "voice_id": "voice_mc_sam",
            "voice_name": "MC Văn Sâm",
            "video_status": "active",
            "production_snapshot_json": json.dumps({
                "cast_voice_overrides": {
                    "MC": "voice_mc_sam",
                    "KHACH_1": "voice_guest_lan",
                }
            }),
        }

        task = main._ensure_audio_task(999, requested_voice_id="voice_mc_sam")

        # Verify segments were stored
        self.assertIsNotNone(task)
        segments = json.loads(task["segments_json"])
        self.assertEqual(len(segments), 3)

        # Segment 0: MC
        self.assertEqual(segments[0]["role"], "MC")
        self.assertEqual(segments[0]["voice_id"], "voice_mc_sam")
        self.assertEqual(segments[0]["voice_name"], "MC Văn Sâm")

        # Segment 1: KHACH_1 (clean emotion notes)
        self.assertEqual(segments[1]["role"], "KHACH_1")
        self.assertEqual(segments[1]["voice_id"], "voice_guest_lan")
        self.assertEqual(segments[1]["voice_name"], "Khách Mời Lan")

        # Ensure (nghẹn ngào) was stripped from submitted text
        submitted_guest_call = [
            call for call in mock_submit_tts.call_args_list
            if call.args[1] == "voice_guest_lan"
        ]
        self.assertTrue(len(submitted_guest_call) >= 1)
        self.assertNotIn("(nghẹn ngào)", submitted_guest_call[0].args[0])
        self.assertNotIn("[KHACH_1]:", submitted_guest_call[0].args[0])

        # Segment 2: MC Outro
        self.assertEqual(segments[2]["role"], "MC")
        self.assertEqual(segments[2]["voice_id"], "voice_mc_sam")

    @patch("auto_yt.main._require_actionable_video")
    @patch("auto_yt.main._automatically_approve_audio_review")
    @patch("auto_yt.main.voice_config.get_voice")
    @patch("auto_yt.main.voice_config.get_provider")
    @patch("auto_yt.main.db.get_audio_task")
    @patch("auto_yt.main.db.update_video_production")
    @patch("auto_yt.main._ensure_audio_task")
    def test_regenerate_audio_with_cast_voice_overrides(
        self,
        mock_ensure_audio,
        mock_update_prod,
        mock_get_audio_task,
        mock_get_provider,
        mock_get_voice,
        mock_auto_approve,
        mock_require_video,
    ):
        mock_require_video.return_value = {
            "id": 999,
            "generated_script": "### [INTRO]\n[MC]: Alo\n[KHACH_1]: Vang",
            "production_snapshot_json": "{}",
        }
        mock_auto_approve.return_value = {"status": "approved"}
        mock_get_voice.return_value = {
            "id": "voice_mc_new",
            "name": "MC New",
            "provider_id": "genmax",
            "status": "active",
        }
        mock_get_provider.return_value = {"capabilities": {"billable": False}}
        mock_get_audio_task.return_value = None
        mock_ensure_audio.return_value = {
            "id": "new_task",
            "video_id": 999,
            "status": "pending",
            "task_id": "new_task",
            "request_hash": "hash_123",
            "audio_url": "",
            "error": "",
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T00:00:00Z",
        }

        req = main.RegenerateAudioRequest(
            voice_id="voice_mc_new",
            confirm_credit_charge=True,
            cast_voice_overrides={"MC": "voice_mc_new", "KHACH_1": "voice_guest_new"},
        )
        res = main.regenerate_audio_for_video(999, req)

        # Verify snapshot was updated with new cast overrides
        mock_update_prod.assert_called_once()
        saved_kwargs = mock_update_prod.call_args.kwargs
        prod_json = json.loads(saved_kwargs["production_snapshot_json"])
        self.assertEqual(prod_json.get("cast_voice_overrides"), req.cast_voice_overrides)


if __name__ == "__main__":
    unittest.main()
