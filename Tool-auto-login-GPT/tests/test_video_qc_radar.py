import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from auto_yt import main
from auto_yt.services import video_qc


class TestRadarVideoQualityChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.audio_dir = self.root / "audio"
        self.thumbnail_dir = self.root / "thumbnails"
        self.audio_dir.mkdir()
        self.thumbnail_dir.mkdir()
        self.audio_path = self.audio_dir / "audio.mp3"
        self.audio_path.write_bytes(b"audio")
        self.video_path = self.root / "video.mp4"
        self.video_path.write_bytes(b"video")
        self.caption_path = self.root / "caption.srt"
        self.caption_path.write_text(
            "1\n00:00:00,000 --> 00:00:03,000\nXin chào.\n",
            encoding="utf-8",
        )
        self.thumbnail_path = self.thumbnail_dir / "thumbnail.png"
        Image.new("RGB", (1280, 720), "white").save(self.thumbnail_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(
        self,
        *,
        script: str,
        cast_assignments: list[dict],
        voice_policy: dict | None = None,
    ) -> dict:
        video = {
            "id": 77,
            "generated_script": script,
            "generated_title": "Một tiêu đề hợp lệ",
            "title": "Source",
            "voice_id": "mc-fixed",
            "source_type": "generated",
        }
        with patch.object(
            video_qc.youtube_publish_workflow,
            "resolve_thumbnail_path_for_qc",
            return_value=self.thumbnail_path,
        ), patch.object(
            video_qc.video_production,
            "probe_media_duration",
            return_value=60.0,
        ), patch.object(
            video_qc.video_production,
            "validate_render",
            return_value={"duration_seconds": 60.0, "video_codec": "h264", "audio_codec": "aac"},
        ), patch.object(
            video_qc,
            "_decode_and_detect",
            return_value={"full_decode": True, "longest_black_seconds": 0.0, "longest_silence_seconds": 0.0},
        ), patch.object(
            video_qc.youtube_publisher,
            "build_upload_metadata",
            return_value={"snippet": {"title": "Title"}},
        ), patch.object(
            video_qc.youtube_publisher,
            "enforce_upload_metadata_quality",
            return_value=({"snippet": {"title": "Title"}}, {"decision": "review"}),
        ):
            return video_qc.run_video_quality_checks(
                video=video,
                artifact={"status": "ready", "path": str(self.video_path)},
                snapshot={"publishing_settings": {"upload_captions": True}},
                audio_task={
                    "video_id": 77,
                    "status": "completed",
                    "audio_url": "/audio/audio.mp3",
                },
                audio_review={"status": "approved"},
                metadata_review={"status": "approved"},
                cast_assignments=cast_assignments,
                active_voice_ids={"mc-fixed", "guest-one", "guest-two"},
                caption_artifact={"path": str(self.caption_path)},
                audio_dir=self.audio_dir,
                thumbnails_dir=self.thumbnail_dir,
                voice_policy=voice_policy,
            )

    def test_full_qc_passes_approved_metadata_review_decision(self):
        report = self._run(
            script="Nội dung đơn thoại hợp lệ.",
            cast_assignments=[],
        )
        self.assertEqual(report["status"], "passed")
        self.assertTrue(all(item["passed"] for item in report["checks"].values()))
        self.assertNotIn("topic", report["checks"])
        self.assertNotIn("relevance", report["checks"])

    def test_dialogue_with_duplicate_voice_fails_at_casting(self):
        report = self._run(
            script="[MC]: Xin chào.\n[KHACH_1]: Chào anh.",
            cast_assignments=[
                {"role_key": "MC", "voice_id": "mc-fixed"},
                {"role_key": "KHACH_1", "voice_id": "mc-fixed"},
            ],
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_stage"], "casting")

    def test_dialogue_voice_outside_selected_pool_fails_at_casting(self):
        report = self._run(
            script="[MC]: Xin chào.\n[KHACH_1]: Chào anh.",
            cast_assignments=[
                {"role_key": "MC", "voice_id": "mc-fixed"},
                {"role_key": "KHACH_1", "voice_id": "guest-one"},
            ],
            voice_policy={
                "guest_voice_pool": "kenh-suc-khoe",
                "mc_voice_id": "mc-fixed",
                "allowed_host_voice_ids": ["mc-fixed"],
                "allowed_guest_voice_ids": ["guest-two"],
                "allowed_single_voice_ids": [],
            },
        )
        self.assertEqual(report["failed_stage"], "casting")
        self.assertIn("sai quyền/pool", report["checks"]["casting"]["detail"])

    def test_mono_voice_without_single_permission_fails_at_casting(self):
        report = self._run(
            script="Nội dung đơn thoại hợp lệ.",
            cast_assignments=[],
            voice_policy={
                "guest_voice_pool": "kenh-suc-khoe",
                "mc_voice_id": "mc-fixed",
                "allowed_host_voice_ids": ["mc-fixed"],
                "allowed_guest_voice_ids": [],
                "allowed_single_voice_ids": ["guest-one"],
            },
        )
        self.assertEqual(report["failed_stage"], "casting")


class TestRadarPublishGateAndRetry(unittest.TestCase):
    def _snapshot(self, mode: str) -> dict:
        return {
            "prompt_version": "prompt-radar",
            "pipeline": {"youtube_upload": True},
            "automation": {
                "source": "competitor_radar",
                "requires_video_qc": True,
                "publication_mode": mode,
            },
        }

    def test_schedule_and_public_never_enqueue_before_qc_pass(self):
        for mode in ("schedule", "public"):
            with self.subTest(mode=mode), patch.object(
                main.db, "get_latest_video_quality_review", return_value=None
            ), patch.object(main.db, "create_system_job") as create_job:
                result = main._enqueue_youtube_publish_if_enabled(
                    video_id=91,
                    snapshot=self._snapshot(mode),
                    artifact={"id": 5},
                )
                self.assertIsNone(result)
                create_job.assert_not_called()

    def test_schedule_and_public_enqueue_after_qc_pass(self):
        for mode in ("schedule", "public"):
            created = {"id": f"publish-{mode}"}
            with self.subTest(mode=mode), patch.object(
                main.db,
                "get_latest_video_quality_review",
                return_value={"status": "passed"},
            ), patch.object(
                main.db, "get_youtube_publish_workflow_for_video", return_value=None
            ), patch.object(
                main.db, "list_system_jobs", return_value=[]
            ), patch.object(
                main.db, "get_video", return_value={"title": "Radar video"}
            ), patch.object(
                main.db, "create_system_job", return_value=created
            ) as create_job, patch.object(
                main.db, "update_system_job", return_value=created
            ), patch.object(
                main.db, "update_video_production_state"
            ), patch.object(main, "_kick_publish_queue"):
                result = main._enqueue_youtube_publish_if_enabled(
                    video_id=91,
                    snapshot=self._snapshot(mode),
                    artifact={"id": 5},
                )
                self.assertEqual(result, created)
                create_job.assert_called_once()

    def _qc_patches(self, report: dict):
        return (
            patch.object(main.db, "get_video", return_value={"id": 91, "generated_script": "Script"}),
            patch.object(main.db, "get_video_artifact", return_value={"id": 5, "path": "video.mp4"}),
            patch.object(main.db, "update_system_job"),
            patch.object(main.voice_config, "load_voice_config", return_value={"voices": []}),
            patch.object(main.db, "get_audio_task", return_value={}),
            patch.object(main.db, "get_audio_review", return_value={}),
            patch.object(main.db, "get_metadata_review", return_value={}),
            patch.object(main.db, "list_video_cast_assignments", return_value=[]),
            patch.object(main.db, "get_latest_video_artifact", return_value=None),
            patch.object(main.video_qc, "run_video_quality_checks", return_value=report),
        )

    def test_first_failure_repairs_once_and_enqueues_second_qc(self):
        report = {"status": "failed", "failed_stage": "captions", "checks": {}}
        patches = self._qc_patches(report)
        started = [item.start() for item in patches]
        del started
        try:
            with patch.object(main.db, "create_video_quality_review"), patch.object(
                main, "_repair_video_qc_stage", return_value=""
            ) as repair, patch.object(main, "_enqueue_video_qc_job") as enqueue:
                main._execute_video_qc_job(
                    {
                        "id": "qc-1",
                        "video_id": 91,
                        "prompt_version": "prompt-radar",
                        "payload": {"video_id": 91, "artifact_id": 5, "attempt": 1, "snapshot": self._snapshot("schedule")},
                    }
                )
                repair.assert_called_once()
                enqueue.assert_called_once()
                self.assertEqual(enqueue.call_args.kwargs["attempt"], 2)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_second_failure_needs_attention_without_another_repair(self):
        report = {"status": "failed", "failed_stage": "render", "checks": {}}
        patches = self._qc_patches(report)
        started = [item.start() for item in patches]
        del started
        try:
            with patch.object(main, "_mark_video_qc_needs_attention") as attention, patch.object(
                main, "_repair_video_qc_stage"
            ) as repair:
                main._execute_video_qc_job(
                    {
                        "id": "qc-2",
                        "video_id": 91,
                        "prompt_version": "prompt-radar",
                        "payload": {"video_id": 91, "artifact_id": 5, "attempt": 2, "snapshot": self._snapshot("public")},
                    }
                )
                attention.assert_called_once()
                repair.assert_not_called()
        finally:
            for item in reversed(patches):
                item.stop()

    def test_repair_error_records_each_attempt_once_and_pauses(self):
        report = {"status": "failed", "failed_stage": "captions", "checks": {}}
        patches = self._qc_patches(report)
        started = [item.start() for item in patches]
        del started
        try:
            with patch.object(
                main.db, "create_video_quality_review"
            ) as create_review, patch.object(
                main, "_repair_video_qc_stage", side_effect=RuntimeError("repair failed")
            ), patch.object(
                main.db, "update_video_production_state"
            ), patch.object(
                main.db, "update_competitor_dispatch"
            ):
                main._execute_video_qc_job(
                    {
                        "id": "qc-repair-error",
                        "video_id": 91,
                        "prompt_version": "prompt-radar",
                        "payload": {
                            "video_id": 91,
                            "artifact_id": 5,
                            "attempt": 1,
                            "snapshot": self._snapshot("schedule"),
                        },
                    }
                )
                self.assertEqual(
                    [call.kwargs["attempt"] for call in create_review.call_args_list],
                    [1, 2],
                )
                self.assertEqual(
                    [call.kwargs["status"] for call in create_review.call_args_list],
                    ["repairing", "needs_attention"],
                )
        finally:
            for item in reversed(patches):
                item.stop()

    def test_async_render_repair_failure_becomes_qc_attention(self):
        job = {
            "id": "render-repair",
            "job_type": "video_render",
            "video_id": 91,
            "payload": {
                "snapshot": {
                    **self._snapshot("schedule"),
                    "automation": {
                        **self._snapshot("schedule")["automation"],
                        "qc_repair_attempted": True,
                    },
                }
            },
        }
        with patch.object(
            main.db, "get_youtube_publish_workflow_by_job", return_value=None
        ), patch.object(
            main.db,
            "get_latest_video_quality_review",
            return_value={"checks": {"render": {"passed": False}}},
        ), patch.object(
            main, "_mark_video_qc_needs_attention"
        ) as attention, patch.object(
            main.db, "update_video_production_state"
        ) as update_state:
            main._handle_production_job_error(job, RuntimeError("render failed"))
            attention.assert_called_once()
            self.assertEqual(attention.call_args.kwargs["attempt"], 2)
            self.assertEqual(attention.call_args.kwargs["report"]["failed_stage"], "render")
            update_state.assert_called_once_with(91, render_status="error")


if __name__ == "__main__":
    unittest.main()
