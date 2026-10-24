import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from nexus_engine.services.google_flow_worker import (
    FlowFrameAttachmentError,
    FlowUploadInteractionError,
    GoogleFlowWorker,
    REFERENCE_RESULT_ATTACHED,
    REFERENCE_RESULT_NOT_FOUND,
    _upgrade_google_cdn_image_url,
    _is_flow_credit_exhausted_error,
    _is_flow_image_credit_exhausted_error,
    _is_flow_video_credit_exhausted_error,
)


class TestGoogleFlowWorkerMedia(unittest.TestCase):
    def test_upgrade_google_cdn_image_url(self):
        # flow.google.com asb URL
        url = "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQwmjl7k34g17zPgVmPwmVauSNF2TcC6A26pkwNRmVRzvHTrOvnEs=s1600-rw"
        upgraded = _upgrade_google_cdn_image_url(url)
        self.assertEqual(
            upgraded,
            "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQwmjl7k34g17zPgVmPwmVauSNF2TcC6A26pkwNRmVRzvHTrOvnEs=s0",
        )

        # googleusercontent URL
        url_lh3 = "https://lh3.googleusercontent.com/abc123xyz=w512-h288-p"
        self.assertEqual(_upgrade_google_cdn_image_url(url_lh3), "https://lh3.googleusercontent.com/abc123xyz=s0")

        # non-google CDN or blob url
        blob = "blob:https://flow.google.com/12345"
        self.assertEqual(_upgrade_google_cdn_image_url(blob), blob)

    def test_media_key_normalization(self):
        url1 = "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s1600-rw?foo=bar"
        url2 = "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s0"
        key1 = GoogleFlowWorker._media_key(url1)
        key2 = GoogleFlowWorker._media_key(url2)
        self.assertEqual(key1, key2)
        self.assertEqual(key1, "https://flow.google.com/asb/anqvlobn17tbpb92bsvfonbq")

    def test_find_new_media_candidate_with_asb_and_references(self):
        mock_page = MagicMock()
        worker = GoogleFlowWorker(mock_page)

        # Baseline contains reference thumbnail
        ref_url = "https://flow-content.google/image/a3c3f570-42cd-4e1b-bf08-3b1ca2df88e7"
        ref_key = worker._media_key(ref_url)
        baseline_keys = {ref_key, "asset:a3c3f570-42cd-4e1b-bf08-3b1ca2df88e7"}

        # Active reference filenames
        worker._active_reference_filenames = {"thumb_4c1bfc9c426b.png"}
        worker._active_reference_keys = baseline_keys

        candidates = [
            {
                "src": ref_url,
                "urls": [ref_url],
                "assetId": "a3c3f570-42cd-4e1b-bf08-3b1ca2df88e7",
                "labelText": "thumb_4c1bfc9c426b.png",
                "turnRole": "",
                "width": 1280,
                "height": 720,
            },
            {
                "src": "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s1600-rw",
                "urls": ["https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s1600-rw"],
                "assetId": "64f26af1-0efc-4c94-9519-724f8cbcaf6e",
                "labelText": "Vietnamese documentary photograph...",
                "turnRole": "agent",
                "width": 1920,
                "height": 1080,
            },
        ]

        selected = worker._find_new_media_candidate(
            candidates,
            baseline_keys,
            exclude_uploaded_images=True,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["assetId"], "64f26af1-0efc-4c94-9519-724f8cbcaf6e")
        self.assertEqual(selected["turnRole"], "agent")


class _FakeFileChooserContext:
    def __init__(self, chooser):
        self.chooser = chooser
        self.info = None

    async def __aenter__(self):
        future = asyncio.get_running_loop().create_future()
        future.set_result(self.chooser)
        self.info = SimpleNamespace(value=future)
        return self.info

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class TestGoogleFlowWorkerUpload(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page = MagicMock()
        self.page.url = "https://labs.google/fx/tools/flow/project/test-upload"
        self.worker = GoogleFlowWorker(self.page)
        self.worker._close_reference_ui = AsyncMock()

    async def test_active_overlay_upload_wins_over_background_upload(self):
        real_upload = MagicMock(name="real_upload")
        fake_background_upload = MagicMock(name="fake_background_upload")

        active_actions = MagicMock()
        active_actions.count = AsyncMock(return_value=1)
        active_actions.nth.return_value = real_upload
        active_root = MagicMock()
        active_root.locator.return_value = active_actions

        background_actions = MagicMock()
        background_actions.count = AsyncMock(return_value=1)
        background_actions.nth.return_value = fake_background_upload
        background_root = MagicMock()
        background_root.locator.return_value = background_actions

        self.worker._is_locator_actionable = AsyncMock(return_value=True)
        selected = await self.worker._find_upload_action(
            [active_root, background_root]
        )

        self.assertIs(selected, real_upload)
        background_root.locator.assert_not_called()

    async def test_hidden_file_input_is_used_without_file_chooser(self):
        add_button = MagicMock()
        add_button.click = AsyncMock()
        file_input = MagicMock()
        file_input.set_input_files = AsyncMock()
        self.worker._find_add_media_button = AsyncMock(return_value=add_button)
        self.worker._visible_upload_roots = AsyncMock(return_value=[MagicMock()])
        self.worker._find_upload_file_input = AsyncMock(return_value=file_input)
        self.worker._find_upload_action = AsyncMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.png"
            path.write_bytes(b"frame")
            await self.worker._submit_file_from_add_menu(path)

        file_input.set_input_files.assert_awaited_once()
        self.worker._find_upload_action.assert_not_awaited()
        self.page.expect_file_chooser.assert_not_called()

    async def test_file_chooser_is_used_when_overlay_has_no_direct_input(self):
        add_button = MagicMock()
        add_button.click = AsyncMock()
        upload_action = MagicMock()
        upload_action.click = AsyncMock()
        chooser = MagicMock()
        chooser.set_files = AsyncMock()
        self.page.expect_file_chooser = MagicMock(
            return_value=_FakeFileChooserContext(chooser)
        )
        self.worker._find_add_media_button = AsyncMock(return_value=add_button)
        self.worker._visible_upload_roots = AsyncMock(return_value=[MagicMock()])
        self.worker._find_upload_file_input = AsyncMock(return_value=None)
        self.worker._find_upload_action = AsyncMock(return_value=upload_action)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.png"
            path.write_bytes(b"frame")
            await self.worker._submit_file_from_add_menu(path)

        upload_action.click.assert_awaited_once()
        chooser.set_files.assert_awaited_once()

    async def test_timeout_before_set_files_retries_without_submitting(self):
        add_button = MagicMock()
        add_button.click = AsyncMock()
        file_input = MagicMock()
        file_input.set_input_files = AsyncMock()
        self.worker._find_add_media_button = AsyncMock(return_value=add_button)
        self.worker._visible_upload_roots = AsyncMock(return_value=[MagicMock()])
        self.worker._find_upload_file_input = AsyncMock(
            side_effect=[TimeoutError("pre-submit timeout"), file_input]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.png"
            path.write_bytes(b"frame")
            await self.worker._submit_file_from_add_menu(path)

        self.assertEqual(add_button.click.await_count, 2)
        file_input.set_input_files.assert_awaited_once()

    async def test_failed_pre_submit_attempt_does_not_dirty_frame_cache(self):
        self.worker._attach_video_frame_from_picker = AsyncMock(
            return_value=REFERENCE_RESULT_NOT_FOUND
        )
        self.worker._upload_video_frame_file = AsyncMock(
            side_effect=FlowFrameAttachmentError(
                "upload timeout",
                reason_code="upload_timeout",
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.png"
            path.write_bytes(b"frame")
            with self.assertRaises(FlowFrameAttachmentError):
                await self.worker._ensure_agent_video_frame(
                    path,
                    "start",
                    1,
                    scene_index=0,
                )
            self.assertNotIn(
                self.worker._video_frame_key(path),
                self.worker._project_video_frame_cache(),
            )

    async def test_submitted_but_unconfirmed_frame_is_not_uploaded_twice(self):
        self.worker._attach_video_frame_from_picker = AsyncMock(
            side_effect=[REFERENCE_RESULT_NOT_FOUND, REFERENCE_RESULT_ATTACHED]
        )
        self.worker._upload_video_frame_file = AsyncMock(return_value=False)
        self.worker._prompt_attachment_identities = AsyncMock(
            return_value=[{"assetId": "asset-1", "urls": []}]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.png"
            path.write_bytes(b"frame")
            await self.worker._ensure_agent_video_frame(
                path,
                "start",
                1,
                scene_index=0,
            )

        self.worker._upload_video_frame_file.assert_awaited_once()
        self.assertEqual(self.worker._attach_video_frame_from_picker.await_count, 2)

    async def test_upload_timeout_maps_to_frame_attachment_reason(self):
        self.worker._submit_file_from_add_menu = AsyncMock(
            side_effect=FlowUploadInteractionError(
                "chooser timeout",
                reason_code="filechooser_timeout",
            )
        )
        self.worker._capture_frame_failure_diagnostic_once = AsyncMock()
        with self.assertRaises(FlowFrameAttachmentError) as raised:
            await self.worker._upload_video_frame_file(Path("frame.png"), 1)
        self.assertEqual(raised.exception.reason_code, "filechooser_timeout")

    async def test_video_queue_extends_deadline_from_detection_time(self):
        self.worker._wait_for_generation_poll = AsyncMock()
        self.worker.handle_confirmation_prompts = AsyncMock()
        self.worker._resolve_new_media_source = AsyncMock(
            side_effect=["", "", "video-url"]
        )

        async def read_activity():
            self.worker._generation_queue_active = True
            return True

        self.worker._read_generation_activity = read_activity
        self.worker._get_existing_error_texts = AsyncMock(return_value=set())
        self.worker._get_snackbar_error = AsyncMock(return_value="")
        self.worker._log_generation_event = MagicMock()
        with patch(
            "nexus_engine.services.google_flow_worker.time.monotonic",
            side_effect=[170.0, 200.0, 201.0],
        ):
            result = await self.worker._wait_for_new_media(
                media_type="video",
                baseline_keys=set(),
                initial_error_texts=set(),
                submitted_at=0.0,
                timeout_seconds=180.0,
            )

        self.assertEqual(result, "video-url")
        self.assertEqual(self.worker._resolve_new_media_source.await_count, 3)


    async def test_find_video_for_prompt_does_not_reuse_unscoped_canvas_video(self):
        self.worker.page.evaluate = AsyncMock(
            return_value={
                "promptFound": True,
                "queued": True,
                "error": "",
                "isLatestPrompt": True,
                "candidates": [],
            }
        )
        self.worker._collect_video_candidates = AsyncMock(
            return_value=[{"completed": True, "assetId": "canvas_video_1"}]
        )
        self.worker._find_new_video_source = MagicMock(return_value="")
        self.worker._inspect_video_candidate_detail = AsyncMock(
            return_value="https://flow.google.com/video/canvas_video_1.mp4"
        )
        result = await self.worker._find_video_for_prompt("Generate cinematic video")
        self.assertEqual(result.get("state"), "queued")
        self.assertEqual(result.get("source"), "")
        self.worker._inspect_video_candidate_detail.assert_not_awaited()

    async def test_generation_receipt_contains_identity_without_media_url(self):
        self.worker.page.url = "https://flow.google.com/project/project-123"
        receipt = self.worker._remember_generation_receipt(
            request_key="request-abc",
            media_type="video",
            scene_index=3,
            full_prompt="Generate a cinematic clip",
            submitted_at="2026-10-20T00:00:00+00:00",
            deadline_seconds=900,
            attempt=2,
        )

        self.assertEqual(receipt["request_key"], "request-abc")
        self.assertEqual(receipt["project_id"], "project-123")
        self.assertEqual(receipt["scene_index"], 3)
        self.assertEqual(receipt["attempt"], 2)
        self.assertEqual(len(receipt["prompt_hash"]), 64)
        self.assertNotIn("url", " ".join(receipt))

    def test_is_flow_credit_exhausted_error_daily_quota(self):
        # Actual Google Flow error strings
        en_err = "The video generation failed because your daily quota for this model has been reached. You will not be charged any credits for this failed generation."
        vn_err = "Không thành công Bạn đã đạt đến hạn mức sử dụng. Vui lòng thử lại sau."
        self.assertTrue(_is_flow_credit_exhausted_error(en_err))
        self.assertTrue(_is_flow_credit_exhausted_error(vn_err))
        self.assertTrue(_is_flow_credit_exhausted_error(f"{vn_err} {en_err}"))

        # Non-quota error
        normal_err = "Policy violation: prompt contains prohibited terms."
        self.assertFalse(_is_flow_credit_exhausted_error(normal_err))
        self.assertFalse(_is_flow_credit_exhausted_error(""))

    def test_image_and_video_credit_quota_distinction(self):
        video_quota_err = "The video generation failed because your daily quota for this model has been reached."
        veo_err = "I will generate 1 video interpolating between images using Veo 3.1. Bạn đã đạt đến hạn mức sử dụng."
        image_quota_err = "Bạn đã đạt đến hạn mức sử dụng. Vui lòng thử lại sau."

        # Video quota error should be identified as video, NOT image
        self.assertTrue(_is_flow_video_credit_exhausted_error(video_quota_err))
        self.assertFalse(_is_flow_image_credit_exhausted_error(video_quota_err))
        self.assertTrue(_is_flow_video_credit_exhausted_error(veo_err))
        self.assertFalse(_is_flow_image_credit_exhausted_error(veo_err))

        # Generic quota tile without video cues should not be blocked from image classification
        self.assertTrue(_is_flow_credit_exhausted_error(image_quota_err))

    def test_request_marker_format(self):
        marker = GoogleFlowWorker._request_marker("8fedd269baa4985a7960")
        self.assertIn("AYT-8fedd269baa4985a7960", marker)
        self.assertIn("Internal tracking token:", marker)

    async def test_ensure_agent_video_frame_falls_back_to_upload_on_picker_error(self):
        import tempfile
        from nexus_engine.services.google_flow_worker import REFERENCE_RESULT_UI_ERROR
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            self.worker._attach_video_frame_from_picker = AsyncMock(
                return_value=REFERENCE_RESULT_UI_ERROR
            )
            self.worker._upload_video_frame_file = AsyncMock(return_value=True)
            self.worker._prompt_attachment_identities = AsyncMock(return_value=[{"assetId": "a1"}])
            cache = self.worker._project_video_frame_cache()
            key = self.worker._video_frame_key(tmp_path)
            cache[key] = {"source": "picker", "flow_asset_id": "stale_id"}

            await self.worker._ensure_agent_video_frame(tmp_path, "start", 1, scene_index=0)
            self.worker._upload_video_frame_file.assert_awaited_once_with(tmp_path, 1)
            self.assertEqual(cache[key]["source"], "upload")
        finally:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
