import asyncio
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from auto_yt import main
from auto_yt.services import (
    chatgpt_projects,
    muse_pipeline,
    muse_provider,
    muse_settings,
    muse_studio_service,
    video_production,
)


def _captions(duration: int) -> list[dict]:
    return [
        {"start": float(start), "end": float(min(duration, start + 5)), "text": f"line {start}"}
        for start in range(0, duration, 5)
    ]


class TestMuseSettings(unittest.TestCase):
    def test_global_setting_uses_studio_path_and_drops_legacy_gpm_keys(self):
        self.assertEqual(
            muse_settings.normalize_muse_settings({"gpm_profile_id": " legacy "}),
            {"muse_studio_path": muse_settings.DEFAULT_MUSE_STUDIO_PATH},
        )
        self.assertEqual(
            muse_settings.normalize_muse_settings({"muse_studio_path": " C:\\Muse_AI_Studio "}),
            {"muse_studio_path": "C:\\Muse_AI_Studio"},
        )
        with patch.object(muse_settings, "write_private_text") as write_private:
            saved = muse_settings.save_muse_settings(
                {"muse_studio_path": "C:\\Muse", "gpm_profile_id": "profile-1"}
            )
        self.assertEqual(saved, {"muse_studio_path": "C:\\Muse"})
        serialized = write_private.call_args.args[1]
        self.assertIn('"muse_studio_path": "C:\\\\Muse"', serialized)
        self.assertNotIn('"gpm_profile_id":', serialized)

    def test_normalize_muse_mode_defaults_and_reference_limit(self):
        normalized = chatgpt_projects.normalize_image_generation_settings(
            {
                "render_mode": "muse_ai",
                "muse_settings": {
                    "video_coverage_mode": "invalid",
                    "video_coverage_minutes": 500,
                    "reference_sets": [
                        {
                            "id": "hero",
                            "name": "Hero",
                            "aliases": ["An", "an", "Anh An"],
                            "images": [f"folder/image-{index}.png" for index in range(8)],
                        }
                    ],
                },
            }
        )
        self.assertEqual(normalized["render_mode"], "muse_ai")
        self.assertEqual(normalized["provider"], "muse_ai")
        self.assertEqual(normalized["muse_settings"]["video_coverage_mode"], "intro_only")
        self.assertEqual(normalized["muse_settings"]["video_coverage_minutes"], 90)
        reference_set = normalized["muse_settings"]["reference_sets"][0]
        self.assertEqual(reference_set["aliases"], ["An", "Anh An"])
        self.assertEqual(len(reference_set["images"]), 6)
        self.assertTrue(all("/" not in value for value in reference_set["images"]))

    def test_legacy_modes_are_unchanged(self):
        self.assertEqual(
            chatgpt_projects.normalize_image_generation_settings({"render_mode": "stock_video"})[
                "render_mode"
            ],
            "stock_video",
        )
        self.assertEqual(
            chatgpt_projects.normalize_image_generation_settings({})["render_mode"],
            "google_flow",
        )

    def test_muse_reference_set_requires_one_to_six_images(self):
        with self.assertRaisesRegex(ValueError, "1 đến 6 ảnh"):
            chatgpt_projects.validate_image_generation_settings(
                {
                    "render_mode": "muse_ai",
                    "muse_settings": {
                        "reference_sets": [
                            {"id": "hero", "name": "Hero", "aliases": [], "images": []}
                        ]
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "1 đến 6 ảnh"):
            chatgpt_projects.validate_image_generation_settings(
                {
                    "render_mode": "muse_ai",
                    "muse_settings": {
                        "reference_sets": [
                            {
                                "id": "hero",
                                "name": "Hero",
                                "aliases": [],
                                "images": [f"hero-{index}.png" for index in range(7)],
                            }
                        ]
                    },
                }
            )


class TestMuseScenePlan(unittest.TestCase):
    def test_fixed_ten_second_windows_and_short_final_scene(self):
        windows = muse_pipeline.build_fixed_scene_windows(
            _captions(25),
            25,
            intro_end_seconds=12,
            coverage_mode="intro_only",
            coverage_minutes=5,
        )
        self.assertEqual([item["duration"] for item in windows], [10.0, 10.0, 5.0])
        self.assertEqual([item["start"] for item in windows], [0.0, 10.0, 20.0])
        self.assertEqual([item["is_video"] for item in windows], [True, True, False])

    def test_all_coverage_modes(self):
        intro = muse_pipeline.build_fixed_scene_windows(
            _captions(70), 70, intro_end_seconds=0, coverage_mode="intro_only", coverage_minutes=5
        )
        first_minute = muse_pipeline.build_fixed_scene_windows(
            _captions(70), 70, intro_end_seconds=0, coverage_mode="first_n_minutes", coverage_minutes=1
        )
        full = muse_pipeline.build_fixed_scene_windows(
            _captions(70), 70, intro_end_seconds=0, coverage_mode="full", coverage_minutes=5
        )
        self.assertEqual(sum(item["is_video"] for item in intro), 1)
        self.assertEqual(sum(item["is_video"] for item in first_minute), 6)
        self.assertTrue(all(item["is_video"] for item in full))

    def test_rejects_video_over_ninety_minutes(self):
        with self.assertRaisesRegex(Exception, "90 phút"):
            muse_pipeline.build_fixed_scene_windows(
                _captions(10),
                muse_pipeline.MAX_VIDEO_SECONDS + 0.1,
                intro_end_seconds=0,
                coverage_mode="full",
                coverage_minutes=5,
            )

    def test_plan_hash_changes_when_reference_content_changes(self):
        first = muse_pipeline._bind_reference_hashes(
            {"plan_hash": "base-plan"}, [{"sha256": "reference-a"}]
        )["plan_hash"]
        second = muse_pipeline._bind_reference_hashes(
            {"plan_hash": "base-plan"}, [{"sha256": "reference-b"}]
        )["plan_hash"]
        self.assertNotEqual(first, second)


class TestMuseReferences(unittest.TestCase):
    def test_alias_primary_and_configuration_order_are_deterministic(self):
        snapshot = []
        for set_index in range(2):
            for image_index in range(4):
                snapshot.append(
                    {
                        "set_id": f"set-{set_index}",
                        "set_name": "Anh An" if set_index == 0 else "Chi Binh",
                        "set_index": set_index,
                        "image_index": image_index,
                        "aliases": ["An"] if set_index == 0 else ["Binh"],
                        "path": f"image-{set_index}-{image_index}.png",
                        "sha256": f"{set_index}-{image_index}",
                        "primary": image_index == 0,
                    }
                )
        selected, omitted = muse_pipeline.select_scene_references(
            {"transcript": "Anh An bước vào phòng cùng Binh", "action": "An greets Binh"},
            snapshot,
            limit=6,
        )
        self.assertEqual(len(selected), 6)
        self.assertTrue(selected[0]["primary"])
        self.assertEqual(selected[0]["set_id"], "set-0")
        self.assertEqual(len(omitted), 2)
        self.assertTrue(all(item["omission_reason"] == "reference_limit" for item in omitted))

    def test_video_prompt_maps_scene_frame_to_image_one(self):
        prompt = muse_provider.MusePromptBuilder.video(
            "A character walks forward.", reference_count=6, duration_seconds=10
        )
        self.assertIn("@Image1 is the required first frame", prompt)
        self.assertIn("@Image2 through @Image6", prompt)
        self.assertIn("16:9", prompt)


class TestMuseStudioApiProvider(unittest.TestCase):
    def test_image_payload_has_all_references_and_idempotency_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            references = []
            for index in range(3):
                path = Path(temp_dir) / f"ref-{index}.png"
                path.write_bytes(f"reference-{index}".encode())
                references.append(path)
            provider = muse_provider.MuseStudioApiProvider()
            with (
                patch.object(provider, "_post_json", return_value={"task_id": "image-1"}) as post,
                patch.object(provider, "_wait_for_task", new=AsyncMock(return_value={"path": "done"})),
            ):
                asyncio.run(
                    provider.create_image(
                        "image prompt",
                        references,
                        Path(temp_dir) / "scene",
                        request_hash="a" * 64,
                    )
                )
            endpoint, payload, idempotency_key = post.call_args.args
            self.assertEqual(endpoint, "/v1/images/tasks")
            self.assertEqual(payload["reference_image"], [str(path.resolve()) for path in references])
            self.assertEqual(idempotency_key, "a" * 64)

    def test_video_payload_keeps_scene_frame_first_and_has_no_idempotency_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            references = []
            for name in ("scene.png", "hero.png", "villain.png"):
                path = Path(temp_dir) / name
                path.write_bytes(name.encode())
                references.append(path)
            provider = muse_provider.MuseStudioApiProvider()
            with (
                patch.object(provider, "_post_json", return_value={"task_id": "video-1"}) as post,
                patch.object(provider, "_wait_for_task", new=AsyncMock(return_value={"path": "done"})),
            ):
                asyncio.run(
                    provider.create_video("video prompt", references, Path(temp_dir) / "clip")
                )
            endpoint, payload, idempotency_key = post.call_args.args
            self.assertEqual(endpoint, "/v1/videos")
            self.assertEqual(payload["reference_image"][0], str(references[0].resolve()))
            self.assertEqual(payload["reference_image"], [str(path.resolve()) for path in references])
            self.assertEqual(idempotency_key, "")

    def test_image_connection_loss_is_retryable_but_video_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.png"
            scene.write_bytes(b"scene")
            provider = muse_provider.MuseStudioApiProvider()
            failure = urllib.error.URLError(ConnectionResetError("lost"))
            with patch.object(provider, "_post_json", side_effect=failure):
                with self.assertRaises(muse_provider.MuseTransientError):
                    asyncio.run(provider.create_image("prompt", [], Path(temp_dir) / "image"))
            with patch.object(provider, "_post_json", side_effect=failure):
                with self.assertRaises(muse_provider.MuseAmbiguousSubmissionError):
                    asyncio.run(provider.create_video("prompt", [scene], Path(temp_dir) / "video"))

    def test_admission_429_is_retryable_before_video_submission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.png"
            scene.write_bytes(b"scene")
            provider = muse_provider.MuseStudioApiProvider()
            with patch.object(
                provider, "_post_json", side_effect=muse_provider.MuseAdmissionRetry("queue full")
            ):
                with self.assertRaises(muse_provider.MuseAdmissionRetry):
                    asyncio.run(provider.create_video("prompt", [scene], Path(temp_dir) / "video"))

    def test_reconcile_polls_existing_task_without_submitting(self):
        provider = muse_provider.MuseStudioApiProvider()
        receipt = muse_provider.MuseReceipt(
            provider_task_id="task-1",
            endpoint="/v1/videos/task-1",
            submitted_at="2026-01-01T00:00:00Z",
        )
        downloaded = {"path": "clip.mp4", "mime_type": "video/mp4"}
        with (
            patch.object(provider, "_get_json", return_value={"status": "completed", "url": "/v1/media/clip.mp4"}) as get,
            patch.object(provider, "_download_completed_task", return_value=downloaded),
            patch.object(provider, "_post_json") as post,
        ):
            result = asyncio.run(
                provider.reconcile(receipt=receipt, destination=Path("clip"), expected="video")
            )
        get.assert_called_once_with("/v1/videos/task-1")
        post.assert_not_called()
        self.assertEqual(result["provider_task_id"], "task-1")

    def test_refusal_and_external_media_url_fail_closed(self):
        with self.assertRaises(muse_provider.MuseRefusalError):
            muse_provider.MuseStudioApiProvider._raise_task_failure(
                {"error": "Generation refused by safety policy"}
            )
        provider = muse_provider.MuseStudioApiProvider()
        with patch.object(
            muse_provider.muse_studio_service,
            "api_credentials",
            return_value=("http://127.0.0.1:18610", "secret"),
        ):
            with self.assertRaisesRegex(muse_provider.MuseError, "ngoài localhost"):
                provider._download_completed_task(
                    {"url": "https://example.com/video.mp4"}, Path("unused"), "video"
                )


class TestMuseRecovery(unittest.TestCase):
    def test_corrupt_cached_artifact_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scene.png"
            path.write_bytes(b"changed")
            artifact = {
                "status": "ready",
                "content_hash": "request",
                "path": str(path),
                "metadata": {"checksum": "not-the-real-checksum"},
            }
            self.assertFalse(muse_pipeline._valid_cached_artifact(artifact, "request"))

            adapter = AsyncMock()

            async def run():
                with patch.object(
                    muse_pipeline.db, "get_latest_video_artifact", return_value=artifact
                ):
                    await muse_pipeline._generate_request(
                        adapter=adapter,
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=Path(temp_dir) / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                    )

            with self.assertRaisesRegex(muse_provider.MuseError, "Recreate"):
                asyncio.run(run())
            adapter.create_image.assert_not_awaited()

    def test_invalid_downloaded_media_is_terminal(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with tempfile.TemporaryDirectory() as temp_dir:
            corrupt = Path(temp_dir) / "result.png"
            corrupt.write_bytes(b"not-an-image")
            adapter = AsyncMock()
            adapter.create_image.return_value = {
                "path": str(corrupt),
                "mime_type": "image/png",
            }

            async def run():
                with (
                    patch.object(
                        muse_pipeline.db,
                        "get_latest_video_artifact",
                        side_effect=get_latest,
                    ),
                    patch.object(
                        muse_pipeline.db,
                        "upsert_video_artifact",
                        side_effect=upsert,
                    ),
                ):
                    await muse_pipeline._generate_request(
                        adapter=adapter,
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=Path(temp_dir) / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                    )

            with self.assertRaises(muse_provider.MuseError):
                asyncio.run(run())

        self.assertEqual(artifact["status"], "error")
        self.assertTrue(artifact["metadata"]["invalid_media"])

    def test_submission_without_receipt_stops_before_resend(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {"attempt": 1},
        }
        adapter = AsyncMock()

        async def run():
            with patch.object(muse_pipeline.db, "get_latest_video_artifact", return_value=artifact):
                await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(muse_provider.MuseAmbiguousSubmissionError):
            asyncio.run(run())
        adapter.create_image.assert_not_awaited()

    def test_transient_error_after_receipt_is_reconciled_without_resubmit(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        class ReceiptThenTimeoutAdapter:
            async def create_image(self, *_args, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id="image-task-1",
                        endpoint="/v1/images/tasks/image-task-1",
                        submitted_at="2026-01-01T00:00:00Z",
                    )
                )
                raise muse_provider.MuseTransientError("timeout")

        async def run():
            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            ):
                await muse_pipeline._generate_request(
                    adapter=ReceiptThenTimeoutAdapter(),
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(muse_pipeline.MuseRetryScheduled) as caught:
            asyncio.run(run())
        self.assertFalse(caught.exception.consume_attempt)
        self.assertEqual(artifact["status"], "submitting")
        self.assertEqual(artifact["metadata"]["provider"], "muse_studio_api")
        self.assertEqual(artifact["metadata"]["provider_task_id"], "image-task-1")

    def test_ambiguous_submission_remains_fail_closed(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        adapter = AsyncMock()
        adapter.create_image.side_effect = muse_provider.MuseAmbiguousSubmissionError("ambiguous")

        async def run():
            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            ):
                await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(muse_provider.MuseAmbiguousSubmissionError):
            asyncio.run(run())
        self.assertEqual(artifact["status"], "submitting")
        self.assertTrue(artifact["metadata"]["ambiguous_submission"])

    def test_cancel_after_receipt_preserves_reconciliation_state(self):
        artifact = None

        class VideoJobCanceled(RuntimeError):
            pass

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        class ReceiptThenCanceledAdapter:
            async def create_image(self, *_args, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id="image-task-cancel",
                        endpoint="/v1/images/tasks/image-task-cancel",
                        submitted_at="2026-01-01T00:00:00Z",
                    )
                )
                raise VideoJobCanceled("canceled")

        async def run():
            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            ):
                await muse_pipeline._generate_request(
                    adapter=ReceiptThenCanceledAdapter(),
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(VideoJobCanceled):
            asyncio.run(run())
        self.assertEqual(artifact["status"], "submitting")
        self.assertEqual(artifact["metadata"]["provider_task_id"], "image-task-cancel")

    def test_legacy_browser_receipt_requires_recreate(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {
                "thread_url": "https://muse.ai/thread/legacy",
                "turn_id": "legacy-turn",
                "submitted_at": "2026-01-01T00:00:00Z",
            },
        }
        adapter = AsyncMock()

        async def run():
            with patch.object(muse_pipeline.db, "get_latest_video_artifact", return_value=artifact):
                await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaisesRegex(muse_provider.MuseAmbiguousSubmissionError, "Recreate"):
            asyncio.run(run())
        adapter.reconcile.assert_not_awaited()


class TestMuseRenderContract(unittest.TestCase):
    def test_video_segments_loop_and_trim_to_each_scene_duration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_paths = []
            for index in range(2):
                path = root / f"scene-{index}.mp4"
                path.write_bytes(b"video")
                media_paths.append(path)
            srt_path = root / "captions.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:13,500\nTest caption\n",
                encoding="utf-8",
            )
            commands = []

            def fake_popen(command, **_kwargs):
                commands.append(command)
                Path(command[-1]).write_bytes(b"segment")
                process = MagicMock()
                process.returncode = 0
                process.communicate.return_value = ("", "")
                return process

            with (
                patch.object(video_production, "SEGMENTS_DIR", root / "segments"),
                patch.object(video_production, "_write_segment_srt", return_value=False),
                patch.object(video_production.subprocess, "Popen", side_effect=fake_popen),
                patch.object(video_production.process_registry, "register_process"),
                patch.object(video_production.process_registry, "unregister_process"),
            ):
                results = video_production._render_segments(
                    ffmpeg_exe="ffmpeg",
                    encoder="libx264",
                    video_id=1,
                    scenes=[
                        {"index": 0, "start": 0.0, "end": 10.0, "duration": 10.0},
                        {"index": 1, "start": 10.0, "end": 13.5, "duration": 3.5},
                    ],
                    media_paths=media_paths,
                    srt_path=srt_path,
                    input_hash="a" * 64,
                    progress=lambda *_args, **_kwargs: None,
                    cancel_check=lambda: None,
                    crop_watermark=False,
                )

            self.assertEqual(len(results), 2)
            self.assertEqual(len(commands), 2)
            for command in commands:
                self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "10.000")
            self.assertEqual(commands[1][commands[1].index("-t") + 1], "3.500")


class TestMuseStudioServiceLifecycle(unittest.TestCase):
    def tearDown(self):
        timer = muse_studio_service._idle_timer
        if timer is not None:
            timer.cancel()
        muse_studio_service._idle_timer = None
        muse_studio_service._process = None
        muse_studio_service._api_key = ""
        muse_studio_service._instance_id = ""
        muse_studio_service._studio_path = None
        muse_studio_service._stdout_handle = None
        muse_studio_service._stderr_handle = None

    def test_validate_path_requires_studio_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(muse_studio_service.MuseStudioConfigurationError):
                muse_studio_service.validate_studio_path(root)
            (root / "muse2api_backend").mkdir()
            (root / "main.py").write_text("", encoding="utf-8")
            (root / "muse2api_backend" / "app.py").write_text("", encoding="utf-8")
            self.assertEqual(muse_studio_service.validate_studio_path(root), str(root.resolve()))

    def test_source_mode_prefers_legacy_backend_data_like_muse_studio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_data = root / "muse2api_data"
            runtime_data.mkdir()
            self.assertEqual(muse_studio_service._studio_data_dir(root), runtime_data.resolve())
            legacy_data = root / "muse2api_backend" / "data"
            legacy_data.mkdir(parents=True)
            self.assertEqual(muse_studio_service._studio_data_dir(root), legacy_data.resolve())

    @unittest.skipUnless(muse_studio_service.sys.platform == "win32", "Windows-only launcher")
    def test_open_studio_uses_nexus_python_and_confirms_process_stays_alive(self):
        process = MagicMock(pid=4321)
        process.wait.side_effect = muse_studio_service.subprocess.TimeoutExpired("Muse", 1.5)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.py").write_text("", encoding="utf-8")
            with (
                patch.object(muse_studio_service, "_validate_studio_path", return_value=root),
                patch.object(muse_studio_service, "_port_in_use", return_value=False),
                patch.object(muse_studio_service, "LOGS_DIR", root / "logs"),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDOUT_PATH",
                    root / "logs" / "muse-ui.stdout.log",
                ),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDERR_PATH",
                    root / "logs" / "muse-ui.stderr.log",
                ),
                patch.object(muse_studio_service.subprocess, "Popen", return_value=process) as popen,
            ):
                result = muse_studio_service.open_studio()

        self.assertTrue(result["success"])
        self.assertEqual(result["pid"], 4321)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(Path(muse_studio_service.sys.executable).resolve()))
        self.assertEqual(Path(command[1]).name, "main.py")
        self.assertNotIn("PYTHONPATH", popen.call_args.kwargs["env"])

    @unittest.skipUnless(muse_studio_service.sys.platform == "win32", "Windows-only launcher")
    def test_open_studio_reports_early_process_exit(self):
        process = MagicMock(pid=4321)
        process.wait.return_value = 1
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.py").write_text("", encoding="utf-8")
            with (
                patch.object(muse_studio_service, "_validate_studio_path", return_value=root),
                patch.object(muse_studio_service, "_port_in_use", return_value=False),
                patch.object(muse_studio_service, "LOGS_DIR", root / "logs"),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDOUT_PATH",
                    root / "logs" / "muse-ui.stdout.log",
                ),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDERR_PATH",
                    root / "logs" / "muse-ui.stderr.log",
                ),
                patch.object(muse_studio_service.subprocess, "Popen", return_value=process),
            ):
                with self.assertRaisesRegex(
                    muse_studio_service.MuseStudioServiceUnavailable,
                    "dừng ngay khi khởi động",
                ):
                    muse_studio_service.open_studio()

    def test_foreign_port_is_never_reused_or_killed(self):
        with (
            patch.object(muse_studio_service, "_validate_studio_path", return_value=Path("C:/Muse")),
            patch.object(muse_studio_service, "_port_in_use", return_value=True),
            patch.object(muse_studio_service.subprocess, "Popen") as popen,
        ):
            with self.assertRaises(muse_studio_service.MuseStudioServiceBusy):
                muse_studio_service.ensure_started()
        popen.assert_not_called()

    def test_start_keeps_api_key_out_of_command_line_and_state(self):
        process = MagicMock()
        process.pid = 4321
        process.poll.return_value = None
        status = {
            "running": True,
            "owned": True,
            "api_ready": True,
            "accounts": 1,
            "browser_running": True,
            "api_url": muse_studio_service.MUSE_API_BASE_URL,
        }
        with (
            patch.object(muse_studio_service, "_validate_studio_path", return_value=Path("C:/Muse")),
            patch.object(muse_studio_service, "_port_in_use", return_value=False),
            patch.object(muse_studio_service.Path, "is_file", return_value=True),
            patch.object(muse_studio_service, "open", mock_open()),
            patch.object(muse_studio_service.subprocess, "Popen", return_value=process) as popen,
            patch.object(muse_studio_service.process_registry, "register_process"),
            patch.object(muse_studio_service, "_write_state") as write_state,
            patch.object(muse_studio_service, "_probe_owned_service", return_value=status),
        ):
            self.assertEqual(muse_studio_service.ensure_started(), status)
        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(command[-1], "--muse-service")
        self.assertNotIn(environment["MUSE2API_KEY"], " ".join(command))
        self.assertEqual(environment["MUSE2API_HOST"], "127.0.0.1")
        self.assertEqual(environment["MUSE2API_PORT"], "18610")
        self.assertEqual(environment["MUSE2API_CDP_PORT"], "19210")
        self.assertEqual(environment["MUSE2API_CONCURRENCY"], "1")
        write_state.assert_called_once_with(process, Path("C:/Muse"))

    def test_state_marker_contains_no_credentials(self):
        process = MagicMock()
        process.pid = 4321
        muse_studio_service._instance_id = "instance-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "muse-service.json"
            with patch.object(muse_studio_service, "MUSE_SERVICE_STATE_PATH", state_path):
                muse_studio_service._write_state(process, Path("C:/Muse"))
            serialized = state_path.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key", "cookie", "token", "proxy", "secret"):
            self.assertNotIn(forbidden, serialized)

    def test_no_account_fails_readiness(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {"status": "no_account", "accounts": 0, "browser_running": False},
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioConfigurationError, "account"
            ):
                muse_studio_service._probe_owned_service()

    def test_probe_rejects_incompatible_model_contract(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {"status": "ready", "accounts": 1, "browser_running": True},
            {"object": "list", "data": [{"id": "muse-image"}]},
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioConfigurationError, "không tương thích"
            ):
                muse_studio_service._probe_owned_service()

    def test_release_schedules_sixty_second_idle_close(self):
        process = MagicMock()
        process.poll.return_value = None
        muse_studio_service._process = process
        timer = MagicMock()
        with patch.object(muse_studio_service.threading, "Timer", return_value=timer) as timer_cls:
            muse_studio_service.release()
        timer_cls.assert_called_once_with(
            muse_studio_service.MUSE_IDLE_CLOSE_SECONDS,
            muse_studio_service._idle_close_callback,
        )
        timer.start.assert_called_once()

    def test_shutdown_uses_the_owned_popen_handle_without_process_lookup(self):
        process = MagicMock()
        process.poll.return_value = None
        muse_studio_service._process = process
        muse_studio_service._api_key = "in-memory-only"
        muse_studio_service._studio_path = Path("C:/Muse")
        with (
            patch.object(muse_studio_service.process_registry, "unregister_process") as unregister,
            patch.object(muse_studio_service, "_remove_state"),
            patch.object(muse_studio_service, "_close_log_handles"),
        ):
            muse_studio_service.shutdown_owned_service()

        if muse_studio_service.os.name == "nt":
            process.send_signal.assert_called_once_with(muse_studio_service.signal.CTRL_BREAK_EVENT)
        else:
            process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        unregister.assert_called_once_with(muse_studio_service.MUSE_SERVICE_PROCESS_KEY, process)
        self.assertIsNone(muse_studio_service._process)
        self.assertEqual(muse_studio_service._api_key, "")


class TestMuseRouting(unittest.TestCase):
    def test_muse_routes_to_independent_media_job(self):
        expected = ({"id": "muse-job", "job_type": "muse_media_generation"}, True)
        with patch.object(main, "_enqueue_muse_media_job", return_value=expected) as enqueue:
            job, created, phase = main._route_video_render_job(
                video_id=42,
                snapshot={"image_generation_settings": {"render_mode": "muse_ai"}},
            )
        enqueue.assert_called_once()
        self.assertEqual(job["job_type"], "muse_media_generation")
        self.assertTrue(created)
        self.assertEqual(phase, "media_generation")

    def test_explicit_enqueue_wakes_an_existing_recovered_muse_job(self):
        existing = {"id": "muse-recovered", "job_type": "muse_media_generation"}
        snapshot = {
            "prompt_version": "documentary",
            "image_generation_settings": {"render_mode": "muse_ai"},
        }
        with (
            patch.object(muse_pipeline, "snapshot_reference_assets", return_value=[]),
            patch.object(main.db, "create_system_job_if_absent", return_value=(existing, False)),
            patch.object(main, "_kick_muse_media_queue") as kick,
        ):
            job, created = main._enqueue_muse_media_job(
                video_id=42,
                snapshot=snapshot,
            )

        self.assertEqual(job, existing)
        self.assertFalse(created)
        kick.assert_called_once_with()

    def test_session_transient_retries_after_sixty_then_three_hundred_seconds(self):
        for previous_attempt, expected_delay in ((0, 60.0), (1, 300.0)):
            job = {
                "id": f"muse-session-{previous_attempt}",
                "job_type": "muse_media_generation",
                "video_id": 42,
                "payload": {},
                "result": {"muse_session_attempt": previous_attempt},
            }
            with (
                patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
                patch.object(main.db, "get_system_job", return_value=job),
                patch.object(main.db, "schedule_system_job_recovery") as schedule,
                patch.object(main.db, "update_system_job"),
                patch.object(main.db, "update_video_production_state"),
            ):
                main._handle_production_job_error(
                    job, muse_provider.MuseTransientError("temporary session failure")
                )

            self.assertEqual(schedule.call_args.kwargs["delay_seconds"], expected_delay)
            self.assertEqual(
                schedule.call_args.kwargs["result_json"]["muse_session_attempt"],
                previous_attempt + 1,
            )

    def test_third_session_transient_failure_is_terminal(self):
        job = {
            "id": "muse-session-final",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {"muse_session_attempt": 2},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job, muse_provider.MuseTransientError("temporary session failure")
            )

        schedule.assert_not_called()
        self.assertTrue(
            any(call_item.kwargs.get("status") == "error" for call_item in update_job.call_args_list)
        )
        self.assertTrue(
            any(
                call_item.kwargs.get("result_json", {}).get("muse_session_attempt") == 3
                for call_item in update_job.call_args_list
            )
        )

    def test_service_busy_wait_does_not_consume_session_attempt(self):
        job = {
            "id": "muse-profile-busy",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job"),
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(job, muse_provider.MuseServiceBusy("busy"))

        self.assertEqual(schedule.call_args.kwargs["delay_seconds"], 15.0)
        self.assertNotIn("muse_session_attempt", schedule.call_args.kwargs["result_json"])
        self.assertEqual(schedule.call_args.kwargs["result_json"]["muse_wait_reason"], "service_busy")

    def test_muse_auth_failure_does_not_enter_youtube_pause_flow(self):
        job = {
            "id": "muse-no-proxy",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main, "_pause_youtube_publish_job") as pause_youtube,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job, muse_provider.MuseAuthenticationError("no account")
            )

        pause_youtube.assert_not_called()
        self.assertEqual(update_job.call_args.kwargs["status"], "error")

    def test_provider_source_has_no_playwright_or_gpm_fallback(self):
        source = Path(muse_provider.__file__).read_text(encoding="utf-8")
        self.assertNotIn("playwright", source.lower())
        self.assertNotIn("gpm_service", source)
        self.assertIn("/v1/media/", source)
        self.assertIn("parsed.hostname != muse_studio_service.MUSE_API_HOST", source)

    def test_muse_settings_response_exposes_only_sanitized_service_status(self):
        safe_status = {
            "running": True,
            "owned": True,
            "api_ready": True,
            "accounts": 2,
            "browser_running": True,
            "api_url": "http://127.0.0.1:18610",
        }
        with (
            patch.object(main.muse_settings, "load_muse_settings", return_value={"muse_studio_path": "C:\\Muse"}),
            patch.object(main.muse_studio_service, "status", return_value=safe_status),
        ):
            response = main.get_muse_settings_endpoint()
        serialized = response.body.decode("utf-8")
        self.assertIn('"accounts":2', serialized)
        for secret_field in ("api_key", "cookie", "token", "proxy"):
            self.assertNotIn(secret_field, serialized.lower())

    def test_legacy_muse_profile_routes_are_removed(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertNotIn('/api/muse/profile/open', source)
        self.assertNotIn('/api/muse/profile/verify', source)
        self.assertIn('/api/muse/studio/open', source)
        self.assertIn('/api/muse/service/verify', source)

    def test_muse_api_responses_disable_caching(self):
        response = main._muse_json_response({"success": True})
        self.assertEqual(response.headers["cache-control"], "no-store")
        error = main._muse_http_exception(400, ValueError("bad request"))
        self.assertEqual(error.headers["Cache-Control"], "no-store")

    def test_startup_recovers_but_does_not_kick_muse_queue(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        startup_start = source.index('@app.on_event("startup")')
        startup_end = source.index('@app.on_event("shutdown")', startup_start)
        startup = source[startup_start:startup_end]
        self.assertIn('"muse_media_generation"', startup)
        self.assertNotIn("_kick_muse_media_queue()", startup)


if __name__ == "__main__":
    unittest.main()
