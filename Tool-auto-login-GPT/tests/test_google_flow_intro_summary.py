import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from auto_yt import main
from auto_yt.services.browser_diagnostics import _redact_diagnostic_dom
from auto_yt.services import video_production
from auto_yt.services.google_flow_worker import (
    FlowFrameAttachmentError,
    FlowModeError,
)


@asynccontextmanager
async def _fake_flow_session(*args, **kwargs):
    del args, kwargs
    yield object()


class TestIntroVideoSummary(unittest.TestCase):
    def _generate(self, scenes, side_effect, *, enabled=True):
        base_paths = [Path(f"scene-{index}.png") for index in range(len(scenes))]
        video_mock = AsyncMock(side_effect=side_effect)
        with (
            patch.object(video_production, "_flow_worker_session", _fake_flow_session),
            patch.object(
                video_production,
                "_generate_scene_images_with_worker",
                new=AsyncMock(return_value=base_paths),
            ),
            patch.object(
                video_production,
                "_generate_scene_video_async",
                new=video_mock,
            ),
            patch.object(video_production, "cleanup_duplicate_scene_artifacts"),
        ):
            result = video_production.generate_scene_media(
                video_id=305,
                scenes=scenes,
                settings={"enable_intro_video": enabled},
                progress=lambda *args: None,
                cancel_check=lambda: None,
            )
        return result, video_mock

    def test_all_requested_intro_clips_succeed(self):
        scenes = [
            {"index": 0, "is_video": True},
            {"index": 1, "is_video": True},
            {"index": 2, "is_video": False},
        ]
        result, video_mock = self._generate(
            scenes,
            [Path("scene-0.mp4"), Path("scene-1.mp4")],
        )
        self.assertEqual(video_mock.await_count, 2)
        self.assertEqual(
            result["intro_video_summary"],
            {
                "enabled": True,
                "requested_count": 2,
                "generated_count": 2,
                "fallback_count": 0,
                "fallback_scenes": [],
            },
        )

    def test_partial_and_total_fallbacks_are_counted(self):
        scenes = [
            {"index": 0, "is_video": True},
            {"index": 1, "is_video": True},
        ]
        frame_error = FlowFrameAttachmentError(
            "private DOM detail",
            reason_code="filechooser_timeout",
        )
        partial, _ = self._generate(
            scenes,
            [Path("scene-0.mp4"), frame_error],
        )
        self.assertEqual(partial["intro_video_summary"]["generated_count"], 1)
        self.assertEqual(partial["intro_video_summary"]["fallback_count"], 1)
        self.assertEqual(
            partial["intro_video_summary"]["fallback_scenes"],
            [
                {
                    "scene_index": 1,
                    "failure_stage": "frame_sync",
                    "reason_code": "filechooser_timeout",
                }
            ],
        )

        total, _ = self._generate(
            scenes,
            [
                FlowFrameAttachmentError("first", reason_code="frame_clear_failed"),
                FlowFrameAttachmentError("second", reason_code="frame_count_mismatch"),
            ],
        )
        self.assertEqual(total["intro_video_summary"]["generated_count"], 0)
        self.assertEqual(total["intro_video_summary"]["fallback_count"], 2)

    def test_flow_unsupported_counts_all_remaining_requested_scenes(self):
        scenes = [
            {"index": 0, "is_video": True},
            {"index": 1, "is_video": True},
            {"index": 2, "is_video": True},
        ]
        result, video_mock = self._generate(
            scenes,
            [FlowModeError("Flow không hỗ trợ model")],
        )
        summary = result["intro_video_summary"]
        self.assertEqual(video_mock.await_count, 1)
        self.assertEqual(summary["requested_count"], 3)
        self.assertEqual(summary["fallback_count"], 3)
        self.assertEqual(summary["fallback_scenes"][1]["reason_code"], "flow_video_unsupported")
        self.assertEqual(summary["fallback_scenes"][2]["reason_code"], "flow_video_unsupported")

    def test_disabled_intro_returns_image_only_summary(self):
        scenes = [{"index": 0, "is_video": True}]
        with patch.object(
            video_production,
            "generate_scene_images",
            return_value=[Path("scene-0.png")],
        ):
            result = video_production.generate_scene_media(
                video_id=305,
                scenes=scenes,
                settings={"enable_intro_video": False},
                progress=lambda *args: None,
                cancel_check=lambda: None,
            )
        self.assertEqual(
            result["intro_video_summary"],
            video_production._empty_intro_video_summary(enabled=False),
        )

    def test_frame_attachment_error_is_classified_as_frame_sync(self):
        details = video_production._video_failure_details(
            FlowFrameAttachmentError("timeout", reason_code="filechooser_timeout")
        )
        self.assertEqual(details["failure_stage"], "frame_sync")
        self.assertEqual(details["reason_code"], "filechooser_timeout")


class TestFlowMediaManifest(unittest.TestCase):
    def test_diagnostic_dom_redacts_prompt_and_sensitive_attributes(self):
        sanitized = _redact_diagnostic_dom(
            '<div>secret creative prompt</div>'
            '<input value="access_token=abc123">'
            '<img src="https://example.test/private.png" title="private title">'
            '<span>C:\\private\\frame.png</span>'
        )
        self.assertNotIn("secret creative prompt", sanitized)
        self.assertNotIn("abc123", sanitized)
        self.assertNotIn("private.png", sanitized)
        self.assertNotIn("C:\\private", sanitized)
        self.assertIn("[REDACTED_TEXT]", sanitized)

    def test_manifest_v2_contains_media_resolution_and_no_raw_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "scene-0.png"
            image_path.write_bytes(b"image")
            summary = {
                "enabled": True,
                "requested_count": 1,
                "generated_count": 0,
                "fallback_count": 1,
                "raw_exception": "secret traceback",
                "fallback_scenes": [
                    {
                        "scene_index": 0,
                        "failure_stage": "frame_sync",
                        "reason_code": "filechooser_timeout",
                        "prompt": "secret prompt",
                    }
                ],
            }
            with (
                patch.object(video_production, "VISUAL_PLANS_DIR", root),
                patch.object(
                    video_production.db,
                    "upsert_video_artifact",
                    return_value={"id": 1},
                ),
            ):
                saved = video_production.save_flow_media_manifest(
                    305,
                    plan_hash="plan-hash",
                    caption_hash="caption-hash",
                    scenes=[{"index": 0, "is_video": True}],
                    visual_bible={},
                    media_paths=[image_path],
                    intro_video_summary=summary,
                )
            manifest = saved["manifest"]
            serialized = json.dumps(manifest)
            self.assertEqual(manifest["version"], 2)
            self.assertEqual(manifest["media"][0]["requested_media_type"], "video")
            self.assertEqual(manifest["media"][0]["resolved_media_type"], "image")
            self.assertEqual(manifest["media"][0]["fallback_reason_code"], "filechooser_timeout")
            self.assertNotIn("secret traceback", serialized)
            self.assertNotIn("secret prompt", serialized)

    def test_manifest_v1_remains_readable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "legacy.media.json"
            payload = {
                "version": 1,
                "video_id": 305,
                "plan_hash": "plan-hash",
                "caption_hash": "caption-hash",
                "media": [
                    {"scene_index": 0, "path": "scene-0.png", "sha256": "hash"}
                ],
                "visual_bible": {},
            }
            manifest_bytes = json.dumps(payload).encode("utf-8")
            manifest_path.write_bytes(manifest_bytes)
            artifact = {
                "path": str(manifest_path),
                "content_hash": video_production._sha256_bytes(manifest_bytes),
            }
            with patch.object(
                video_production.db,
                "get_latest_video_artifact",
                return_value=artifact,
            ):
                _, loaded = video_production.load_flow_media_manifest(305)
            self.assertEqual(loaded["media"][0]["requested_media_type"], "image")
            self.assertEqual(loaded["media"][0]["resolved_media_type"], "image")
            self.assertEqual(loaded["intro_video_summary"]["fallback_count"], 0)

    def test_job_center_api_whitelists_intro_summary_fields(self):
        job = {
            "id": "flow-job",
            "job_type": "flow_media_generation",
            "status": "completed",
            "title": "Video 305",
            "payload": {},
            "result": {
                "intro_video_summary": {
                    "enabled": True,
                    "requested_count": 1,
                    "generated_count": 0,
                    "fallback_count": 1,
                    "raw_exception": "secret traceback",
                    "fallback_scenes": [
                        {
                            "scene_index": 0,
                            "failure_stage": "frame_sync",
                            "reason_code": "filechooser_timeout",
                            "prompt": "secret prompt",
                        }
                    ],
                }
            },
        }
        item = main._system_job_center_item(job, None)
        serialized = json.dumps(item["intro_video_summary"])
        self.assertNotIn("secret traceback", serialized)
        self.assertNotIn("secret prompt", serialized)
        self.assertEqual(
            set(item["intro_video_summary"]),
            {
                "enabled",
                "requested_count",
                "generated_count",
                "fallback_count",
                "fallback_scenes",
            },
        )

    def test_final_mp4_guard_skips_flow_media_generation(self):
        import tempfile
        from auto_yt.services import database as db
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            fake_artifact = {
                "id": 99999,
                "video_id": 404,
                "artifact_type": "final_mp4",
                "status": "ready",
                "path": str(tmp_path),
            }
            with patch.object(db, "get_latest_video_artifact", return_value=fake_artifact):
                result = video_production.generate_scene_media(
                    video_id=404,
                    scenes=[{"index": 0, "is_video": True}],
                    progress=lambda *args: None,
                    cancel_check=lambda: None,
                )
                self.assertEqual(result["media_paths"], [])
        finally:
            tmp_path.unlink(missing_ok=True)
