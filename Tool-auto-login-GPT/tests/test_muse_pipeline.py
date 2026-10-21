import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

from auto_yt import main
from auto_yt.services import chatgpt_projects, muse_pipeline, muse_provider


def _captions(duration: int) -> list[dict]:
    return [
        {"start": float(start), "end": float(min(duration, start + 5)), "text": f"line {start}"}
        for start in range(0, duration, 5)
    ]


class TestMuseSettings(unittest.TestCase):
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


class TestMuseBrowserAdapter(unittest.TestCase):
    def test_login_page_fails_capability_probe(self):
        body = MagicMock()
        body.inner_text = AsyncMock(return_value="Sign in to continue")
        page = MagicMock()
        page.locator.return_value = body
        adapter = muse_provider.MuseBrowserAdapter(AsyncMock())
        with self.assertRaises(muse_provider.MuseAuthenticationError):
            asyncio.run(adapter.capability_probe(page))

    def test_capability_probe_checks_send_and_clears_draft_without_clicking(self):
        body = MagicMock()
        body.inner_text = AsyncMock(return_value="Muse workspace")
        file_input = MagicMock()
        file_input.first = file_input
        file_input.count = AsyncMock(return_value=1)
        textarea = MagicMock()
        textarea.first = textarea
        textarea.count = AsyncMock(return_value=1)
        textarea.is_visible = AsyncMock(return_value=True)
        textarea.is_enabled = AsyncMock(return_value=True)
        textarea.fill = AsyncMock()
        send = MagicMock()
        send.is_enabled = AsyncMock(return_value=True)
        send.click = AsyncMock()
        composer = MagicMock()
        page = MagicMock()
        page.url = "https://muse.ai/thread/new"
        page.locator.side_effect = lambda selector: (
            body if selector == "body" else textarea if selector == "textarea" else file_input
        )
        page.evaluate = AsyncMock(
            return_value={"result_detection": True, "download_extraction": True}
        )
        page.wait_for_timeout = AsyncMock()
        adapter = muse_provider.MuseBrowserAdapter(AsyncMock())

        with patch.object(
            muse_provider,
            "_first_visible",
            new=AsyncMock(side_effect=[composer, send]),
        ):
            result = asyncio.run(adapter.capability_probe(page))

        self.assertTrue(result["send_detection"])
        self.assertTrue(result["result_detection"])
        self.assertTrue(result["download_extraction"])
        self.assertEqual(
            textarea.fill.await_args_list,
            [call("Auto_YT capability probe - do not send"), call("")],
        )
        send.click.assert_not_awaited()

    def test_profile_without_proxy_fails_closed(self):
        with patch.object(
            muse_provider.gpm_service,
            "get_gpm_profile_detail",
            return_value={"id": "profile-1", "proxy": "direct"},
        ):
            with self.assertRaises(muse_provider.ProxyConfigurationError):
                muse_provider.MuseBrowserProvider.validate_profile("profile-1")

    def test_reference_upload_uses_playwright_file_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "hero.png"
            image_path.write_bytes(b"reference")
            file_input = MagicMock()
            file_input.first = file_input
            file_input.count = AsyncMock(return_value=1)
            file_input.set_input_files = AsyncMock()
            page = MagicMock()
            page.locator.return_value = file_input
            page.evaluate = AsyncMock(return_value=True)
            page.wait_for_timeout = AsyncMock()
            adapter = muse_provider.MuseBrowserAdapter(AsyncMock())
            asyncio.run(adapter._upload_references(page, [image_path]))
            file_input.set_input_files.assert_awaited_once_with([str(image_path.resolve())])

    def test_rate_limit_timeout_refusal_and_cancel_are_classified(self):
        adapter = muse_provider.MuseBrowserAdapter(AsyncMock())
        adapter._attachments = AsyncMock(return_value=[])

        body = MagicMock()
        body.inner_text = AsyncMock(return_value="Too many requests, try again later")
        page = MagicMock()
        page.locator.return_value = body
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock()
        with self.assertRaises(muse_provider.MuseTransientError):
            asyncio.run(
                adapter._wait_for_result(
                    page,
                    expected="image",
                    baseline_sources=set(),
                    timeout_seconds=10,
                    cancel_check=None,
                )
            )

        with self.assertRaisesRegex(RuntimeError, "canceled"):
            asyncio.run(
                adapter._wait_for_result(
                    page,
                    expected="image",
                    baseline_sources=set(),
                    timeout_seconds=10,
                    cancel_check=lambda: (_ for _ in ()).throw(RuntimeError("canceled")),
                )
            )

        with self.assertRaisesRegex(muse_provider.MuseTransientError, "hết thời gian"):
            asyncio.run(
                adapter._wait_for_result(
                    page,
                    expected="image",
                    baseline_sources=set(),
                    timeout_seconds=-1,
                    cancel_check=None,
                )
            )

        body.inner_text = AsyncMock(return_value="ready")
        page.evaluate = AsyncMock(return_value="I cannot create this media.")
        with patch.object(muse_provider, "_first_visible", new=AsyncMock(return_value=None)):
            with self.assertRaises(muse_provider.MuseRefusalError):
                asyncio.run(
                    adapter._wait_for_result(
                        page,
                        expected="image",
                        baseline_sources=set(),
                        timeout_seconds=10,
                        cancel_check=None,
                    )
                )

    def test_wrong_media_type_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "result.png"
            image_path.write_bytes(b"not-a-video")
            extractor = muse_provider.MuseMediaExtractor()
            with self.assertRaisesRegex(muse_provider.MuseError, "yêu cầu video"):
                extractor._validate_result(image_path, "video", "browser_blob", "image/png")


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
        adapter.generate.assert_not_awaited()

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
            async def generate(self, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        thread_url="https://muse.ai/thread/receipt-1",
                        turn_id="turn-1",
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
        self.assertEqual(
            artifact["metadata"]["thread_url"], "https://muse.ai/thread/receipt-1"
        )

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
        adapter.generate.side_effect = muse_provider.MuseAmbiguousSubmissionError("ambiguous")

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
            async def generate(self, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        thread_url="https://muse.ai/thread/receipt-cancel",
                        turn_id="turn-cancel",
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
        self.assertEqual(
            artifact["metadata"]["thread_url"],
            "https://muse.ai/thread/receipt-cancel",
        )


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

    def test_provider_source_has_no_direct_http_downloader(self):
        source = Path(muse_provider.__file__).read_text(encoding="utf-8")
        self.assertNotIn("requests.get", source)
        self.assertNotIn("urllib.request", source)
        self.assertIn("fetch(src", source)

    def test_muse_profile_response_never_exposes_credentials(self):
        public = main._public_gpm_profile(
            {
                "id": "profile-1",
                "name": "Muse",
                "raw_proxy": "host:8080:user:secret",
                "proxy": "http://other:password@host:8080",
                "token": "bearer-secret",
                "cookies": "session-secret",
                "proxy_username": "user",
                "proxy_password": "secret",
            }
        )
        serialized = json.dumps(public)
        for secret in ("bearer-secret", "session-secret", "password", "user:secret"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("raw_proxy", public)
        self.assertNotIn("proxy", public)
        self.assertTrue(public["proxy_configured"])
        self.assertEqual(public["proxy_display"], "host:8080")

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
