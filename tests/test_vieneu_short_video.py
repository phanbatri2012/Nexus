import hashlib
import io
import json
import os
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from nexus_engine.services import database as db
from nexus_engine.services import short_video_api
from nexus_engine.services import short_video_audio
from nexus_engine.services import short_video_pipeline
from nexus_engine.services import short_video_renderer
from nexus_engine.services import short_video_worker
from nexus_engine.services import vieneu_tts
from nexus_engine.services.short_video_api import AudioPreviewRequest
from nexus_engine.services.short_video_registry import ShortVideoProjectRegistry


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "short_video_projects" / "bac-ho-ngam-tho.v1.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VieNeuPoemAudioTests(unittest.TestCase):
    def test_title_is_not_part_of_vieneu_audio_input(self):
        item = {
            "title": "TÊN BÀI THƠ KHÔNG ĐƯỢC ĐỌC",
            "source_text": "Sáng ra bờ suối, tối vào hang\nCháo bẹ, rau măng vẫn sẵn sàng",
        }
        lines = short_video_pipeline._vieneu_audio_input_lines(item)
        payload = "\n".join(line["synthesis_text"] for line in lines)
        self.assertNotIn(item["title"], payload)
        self.assertIn("Sáng ra bờ suối", payload)

    def test_line_punctuation_and_pause_policy(self):
        lines = short_video_audio.prepare_poem_lines(
            "Dòng thường\nDòng cuối khổ\n\nDòng hỏi?\nDòng cuối"
        )
        self.assertEqual(
            [line["gap_ms"] for line in lines],
            [900, 2200, 1200, 1200],
        )
        self.assertEqual(lines[0]["synthesis_text"], "Dòng thường,")
        self.assertEqual(lines[1]["synthesis_text"], "Dòng cuối khổ.")
        self.assertEqual(lines[2]["source_text"], "Dòng hỏi?")
        self.assertEqual(lines[3]["synthesis_text"], "Dòng cuối.")

    def test_seed_is_deterministic_per_variant(self):
        first = short_video_audio.deterministic_seed(
            item_id="item", audio_revision=2, line_index=3, variant=1
        )
        repeated = short_video_audio.deterministic_seed(
            item_id="item", audio_revision=2, line_index=3, variant=1
        )
        second_variant = short_video_audio.deterministic_seed(
            item_id="item", audio_revision=2, line_index=3, variant=2
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second_variant)

    def test_transcript_qc_rejects_repeated_words(self):
        result = short_video_audio.transcript_score(
            "Sáng ra bờ suối tối vào hang",
            "Sáng sáng sáng sáng ra bờ suối tối vào hang",
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["repeated_words"])

    def test_transcript_qc_tolerates_near_phonetic_asr_spelling(self):
        result = short_video_audio.transcript_score(
            "Sáng ra bờ suối tối vào hang",
            "Sáng ra bờ xuối tối vào hàng",
        )
        self.assertTrue(result["accepted"])
        self.assertGreaterEqual(result["score"], 0.8)

    def test_transcript_qc_rejects_missing_word(self):
        result = short_video_audio.transcript_score(
            "Sáng ra bờ suối tối vào hang",
            "Sáng ra bờ suối vào hang",
        )
        self.assertFalse(result["accepted"])
        self.assertFalse(result["same_word_count"])

    def test_transcript_qc_rejects_unrelated_equal_length_words(self):
        result = short_video_audio.transcript_score(
            "Bàn đá chông chênh dịch sử Đảng",
            "mây trắng bay ngang trời xanh xa",
        )
        self.assertFalse(result["accepted"])
        self.assertLess(result["score"], short_video_audio.TRANSCRIPT_ACCEPTANCE_THRESHOLD)

    def test_wav_qc_rejects_clipping(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = Path(temp_dir) / "clipped.wav"
            samples = np.full(48000, 32767, dtype="<i2")
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(48000)
                target.writeframes(samples.tobytes())
            metrics = short_video_audio.analyze_wav(path)
            self.assertFalse(metrics["technical_ok"])
            self.assertGreater(metrics["clipping_ratio"], 0.9)

    def test_renderer_probe_falls_back_to_ffmpeg_metadata(self):
        ffmpeg_output = """
Duration: 00:00:12.34, start: 0.000000, bitrate: 1500 kb/s
Stream #0:0: Video: h264 (High), yuv420p, 1080x1920, 30 fps
Stream #0:1: Audio: aac (LC), 48000 Hz, stereo
Stream mapping:
Stream #0:0 -> #0:0 (h264 (native) -> wrapped_avframe (native))
Stream #0:1 -> #0:1 (aac (native) -> pcm_s16le (native))
"""
        with (
            patch.object(short_video_renderer, "_ffprobe_exe", return_value=None),
            patch.object(short_video_renderer, "_ffmpeg_exe", return_value="ffmpeg"),
            patch.object(
                short_video_renderer.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=ffmpeg_output),
            ),
        ):
            metadata = short_video_renderer.probe(Path("draft.mp4"))
        self.assertAlmostEqual(metadata["duration_seconds"], 12.34)
        self.assertEqual(metadata["streams"][0]["codec_name"], "h264")
        self.assertEqual(metadata["streams"][0]["width"], 1080)
        self.assertEqual(metadata["streams"][0]["height"], 1920)
        self.assertEqual(metadata["streams"][0]["r_frame_rate"], "30/1")
        self.assertEqual(metadata["streams"][1]["codec_name"], "aac")
        self.assertEqual(len(metadata["streams"]), 2)


class VieNeuRuntimeTests(unittest.TestCase):
    def setUp(self):
        vieneu_tts._STATUS_CACHE.clear()

    def tearDown(self):
        vieneu_tts._STATUS_CACHE.clear()

    def test_unicode_home_and_model_hash_fail_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            home = Path(temp_dir) / "VieNeu‑TTS v3_Turbo"
            python = home / ".venv" / "Scripts" / "python.exe"
            model = home / "models" / "tiny.bin"
            python.parent.mkdir(parents=True)
            model.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            model.write_bytes(b"model")
            lock = {
                "engine": "vieneu_v3_turbo",
                "backend": "onnx",
                "precision": "fp32",
                "python": ".venv/Scripts/python.exe",
                "runtime_revision": "runtime-revision",
                "package_version": "3.8.3",
                "model_revision": {"backbone": "model-revision"},
                "watermark_available": True,
                "artifacts": [
                    {
                        "path": "models/tiny.bin",
                        "size": model.stat().st_size,
                        "sha256": sha256(model),
                    }
                ],
            }
            (home / "runtime.lock.json").write_text(json.dumps(lock), encoding="utf-8")
            with patch.dict(os.environ, {vieneu_tts.ENV_HOME: str(home)}):
                ready = vieneu_tts.runtime_status()
                self.assertTrue(ready["available"])
                model.write_bytes(b"tampered")
                invalid = vieneu_tts.runtime_status()
                self.assertFalse(invalid["available"])
                self.assertEqual(invalid["code"], "runtime_invalid")

    def test_session_ignores_engine_logs_before_json_protocol(self):
        stream = io.StringIO('Loading ONNX model...\n{"status":"ready"}\n')
        process = SimpleNamespace(stdout=stream)
        session = vieneu_tts.VieNeuGenerationSession(
            job_id="test-session",
            reference_path=Path("reference.wav"),
            work_dir=Path("work"),
        )
        session.process = process
        session._executor = ThreadPoolExecutor(max_workers=1)
        try:
            self.assertEqual(session._read_response(timeout=1), {"status": "ready"})
        finally:
            session._executor.shutdown(wait=True)


class VieNeuRegistryAndPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "preview.db")
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_draft_profile_allows_preview_but_blocks_create(self):
        config = self.root / "projects"
        config.mkdir()
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest.pop("character_profile", None)
        (config / "project.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        technical = {
            "available": True,
            "reference_ready": True,
            "reference_sha256": "a" * 64,
            "detail": "ready",
            "runtime": {
                "available": True,
                "runtime_revision": "runtime",
                "model_revision": {"backbone": "model"},
                "lock_sha256": "b" * 64,
            },
        }
        with patch.object(vieneu_tts, "managed_profile_status", return_value=technical):
            registry = ShortVideoProjectRegistry(config)
            project = registry.get("bac-ho-ngam-tho")
            preview_profile = registry.audio_profile_for_preview("bac-ho-ngam-tho")
        self.assertFalse(project["available"])
        self.assertFalse(project["audio_profile_summary"]["create_ready"])
        self.assertEqual(preview_profile["profile_id"], "bac-ho-ngam-tho-v1")
        with self.assertRaisesRegex(ValueError, "Dependency"):
            registry.snapshot_for_create("bac-ho-ngam-tho")

    def test_preview_double_click_is_idempotent_and_uses_only_poem(self):
        profile = {
            "provider": "vieneu_v3_turbo",
            "profile_id": "bac-ho-ngam-tho-v1",
            "display_name": "Bác Hồ ngâm thơ · VieNeu v1",
            "profile_version": 1,
            "status": "draft",
            "backend": "onnx",
            "precision": "fp32",
            "sample_rate": 48000,
            "runtime_lock_sha256": "b" * 64,
            "reference_sha256": "a" * 64,
        }
        request = AudioPreviewRequest(
            poem="Dòng một\nDòng hai\nDòng ba\nDòng bốn\nDòng năm",
            audio_profile_id=profile["profile_id"],
            client_request_id="preview-request-1",
        )
        with (
            patch.object(
                short_video_api.registry,
                "audio_profile_for_preview",
                return_value=profile,
            ),
            patch.object(short_video_api.short_video_worker, "kick") as kick,
        ):
            first = short_video_api.create_short_video_audio_preview(
                "bac-ho-ngam-tho", request
            )
            second = short_video_api.create_short_video_audio_preview(
                "bac-ho-ngam-tho", request
            )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["preview"]["id"], second["preview"]["id"])
        self.assertEqual(kick.call_count, 2)
        jobs = db.list_system_jobs(limit=None, job_type="short_video_audio_preview")
        self.assertEqual(len(jobs), 1)
        preview = db.get_short_video_audio_preview(first["preview"]["id"])
        self.assertEqual(preview["poem_excerpt"].splitlines(), [
            "Dòng một", "Dòng hai", "Dòng ba", "Dòng bốn"
        ])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("runtime_lock_sha256", serialized)

        db.update_system_job(first["job"]["id"], status="paused")
        db.update_short_video_audio_preview(
            first["preview"]["id"], status="paused"
        )
        with (
            patch.object(
                short_video_api.registry,
                "audio_profile_for_preview",
                return_value=profile,
            ),
            patch.object(short_video_api.short_video_worker, "kick") as resume_kick,
        ):
            resumed = short_video_api.create_short_video_audio_preview(
                "bac-ho-ngam-tho", request
            )
        self.assertFalse(resumed["created"])
        self.assertEqual(resumed["preview"]["id"], first["preview"]["id"])
        self.assertEqual(resumed["preview"]["status"], "queued")
        self.assertEqual(db.get_system_job(first["job"]["id"])["status"], "queued")
        resume_kick.assert_called_once_with()

    def test_restart_pauses_preview_without_starting_worker(self):
        preview_id = "11111111-1111-4111-8111-111111111111"
        job_id = f"short-video-audio-preview-{preview_id}"
        _preview, _job, created = db.create_short_video_audio_preview_with_job(
            preview_id=preview_id,
            project_key="bac-ho-ngam-tho",
            profile_id="bac-ho-ngam-tho-v1",
            request_hash="c" * 64,
            poem_excerpt="Dòng thơ",
            job_id=job_id,
            payload={"preview_id": preview_id, "audio_profile": {}},
            expires_at="2999-01-01T00:00:00+00:00",
            voice_snapshot={
                "voice_id": "bac-ho-ngam-tho-v1",
                "voice_name": "Bác Hồ ngâm thơ · VieNeu v1",
                "provider_id": "vieneu_v3_turbo",
                "voice_revision": 1,
            },
        )
        self.assertTrue(created)
        with patch.object(short_video_worker, "_COORDINATOR", None):
            self.assertEqual(short_video_worker.pause_after_restart(), 1)
            self.assertFalse(short_video_worker.status()["running"])
        self.assertEqual(db.get_system_job(job_id)["status"], "paused")
        self.assertEqual(
            db.get_short_video_audio_preview(preview_id)["status"], "paused"
        )

    def test_public_item_hides_audio_snapshot_path(self):
        manifest = {
            "resolved_audio_profile": {
                "profile_id": "bac-ho-ngam-tho-v1",
                "runtime_lock_sha256": "b" * 64,
                "reference": {
                    "path": "references/audio_profile/reference.wav",
                    "sha256": "a" * 64,
                },
            }
        }
        item = {
            "id": "11111111-1111-4111-8111-111111111111",
            "project_key": "bac-ho-ngam-tho",
            "profile_version": "1.1.0",
            "title": "Cảnh khuya",
            "status": "queued",
            "manifest": manifest,
        }
        public = short_video_api._public_item(item, detail=True)
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("references/audio_profile", serialized)
        self.assertNotIn("runtime_lock_sha256", serialized)


if __name__ == "__main__":
    unittest.main()
