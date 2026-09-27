import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from auto_yt.services.google_flow_worker import (
    FlowGenerationStartError,
    FlowInvalidOutputError,
    FlowModeError,
    FlowResultMissingError,
    FlowSubmissionError,
    FlowUiStateError,
    GoogleFlowWorker,
    REFERENCE_RESULT_ATTACHED,
    REFERENCE_RESULT_NOT_FOUND,
    REFERENCE_RESULT_UI_ERROR,
    ReferenceAttachmentError,
    _upgrade_google_cdn_image_url,
)


def _image_bytes(size=(1376, 768)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(20, 40, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


class GoogleFlowWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        GoogleFlowWorker._session_uploaded_references.clear()
        GoogleFlowWorker._session_uploaded_image_urls.clear()

    async def test_wait_for_editor_finds_prosemirror(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/123"
        locator = MagicMock()
        locator.first.is_visible = AsyncMock(return_value=True)
        page.locator.return_value = locator

        worker = GoogleFlowWorker(page)
        ed = await worker.wait_for_editor(timeout=5.0)
        self.assertIsNotNone(ed)

    async def test_ensure_project_reuses_current_project(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/abc-xyz"
        locator = MagicMock()
        locator.first.is_visible = AsyncMock(return_value=True)
        page.locator.return_value = locator

        worker = GoogleFlowWorker(page)
        res = await worker.ensure_project("test_project")
        self.assertEqual(res, "https://flow.google.com/project/abc-xyz")

    async def test_ensure_project_clicks_new_project_when_at_home(self):
        page = MagicMock()
        page.url = "https://flow.google.com/"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

        link_loc = MagicMock()
        link_loc.is_visible = AsyncMock(return_value=False)
        page.get_by_role.return_value = link_loc

        # New project button visible
        btn_loc = MagicMock()
        btn_loc.first.is_visible = AsyncMock(return_value=True)
        btn_loc.first.click = AsyncMock()
        page.locator.return_value = btn_loc

        worker = GoogleFlowWorker(page)
        with patch.object(worker, "wait_for_editor", new_callable=AsyncMock) as mock_wait:
            res = await worker.ensure_project("my_proj")
            self.assertEqual(res, "https://flow.google.com/")
            mock_wait.assert_called()

    async def test_generate_scene_inputs_prompt_and_extracts_new_image(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/123"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        page.keyboard.insert_text = AsyncMock()

        editor_loc = MagicMock()
        editor_loc.focus = AsyncMock()
        editor_loc.click = AsyncMock()
        editor_loc.fill = AsyncMock()
        editor_loc.evaluate = AsyncMock(return_value="Prompt test")

        gen_btn_loc = MagicMock()
        gen_btn_loc.first.is_visible = AsyncMock(return_value=True)
        gen_btn_loc.first.is_enabled = AsyncMock(return_value=True)
        gen_btn_loc.first.click = AsyncMock()

        stop_btn_loc = MagicMock()
        # First call: Stop button not visible yet, then visible (started), then not visible (done)
        stop_btn_loc.first.is_visible = AsyncMock(side_effect=[True, False, False, False])

        def locator_router(sel):
            if "generate" in sel:
                return gen_btn_loc
            if "Stop" in sel:
                return stop_btn_loc
            return editor_loc

        page.locator.side_effect = locator_router

        # Mock evaluate for images
        existing_imgs_mock = ["https://flow-content.google/image/old1"]
        new_imgs_mock = [
            {"src": "https://flow-content.google/image/old1", "className": "image", "width": 1024, "height": 768},
            {"src": "https://flow-content.google/image/new2", "className": "image", "width": 1376, "height": 768},
        ]

        page.evaluate = AsyncMock(return_value=new_imgs_mock)

        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "wait_for_editor", AsyncMock(return_value=editor_loc)),
            patch.object(
                worker,
                "_capture_stable_image_baseline",
                AsyncMock(return_value={worker._media_key(existing_imgs_mock[0])}),
            ),
            patch.object(
                worker,
                "_resolve_new_media_source",
                AsyncMock(return_value="https://flow-content.google/image/new2"),
            ),
            patch.object(
                worker,
                "_submit_prompt_and_wait_for_ack",
                AsyncMock(return_value=(0.0, "https://flow-content.google/image/new2")),
            ),
        ):
            img_url = await worker.generate_scene("A sunset", "blur", [])
            self.assertEqual(img_url, "https://flow-content.google/image/new2")

    async def test_download_image_writes_file_successfully(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "output.png"
            page = MagicMock()
            resp = MagicMock()
            resp.status = 200
            resp.body = AsyncMock(return_value=b"fake-png-data")
            page.request.get = AsyncMock(return_value=resp)

            worker = GoogleFlowWorker(page)
            await worker.download_image("https://flow-content.google/image/test", str(save_path))
            self.assertTrue(save_path.exists())
            self.assertEqual(save_path.read_bytes(), b"fake-png-data")


    async def test_dismiss_blocking_dialogs_escapes_backdrop(self):
        page = MagicMock()
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

        backdrop_loc = MagicMock()
        # First check is_visible returns True, second check returns False (dismissed)
        backdrop_loc.first.is_visible = AsyncMock(side_effect=[True, False])
        backdrop_loc.first.click = AsyncMock()

        btn_loc = MagicMock()
        btn_loc.first.is_visible = AsyncMock(return_value=False)

        def loc_router(sel):
            if "cdk-overlay-backdrop" in sel:
                return backdrop_loc
            return btn_loc

        page.locator.side_effect = loc_router

        worker = GoogleFlowWorker(page)
        await worker.dismiss_blocking_dialogs()

        page.keyboard.press.assert_called_with("Escape")

    async def test_generate_scene_recovers_when_editor_click_intercepted(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/123"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        page.keyboard.insert_text = AsyncMock()

        editor_loc = MagicMock()
        editor_loc.focus = AsyncMock()
        # First click throws intercepts pointer events exception, second click succeeds
        editor_loc.click = AsyncMock(side_effect=[Exception("<div class='cdk-overlay-backdrop'> intercepts pointer events"), None])
        editor_loc.fill = AsyncMock()
        editor_loc.evaluate = AsyncMock(return_value="Prompt test")

        gen_btn_loc = MagicMock()
        gen_btn_loc.first.is_visible = AsyncMock(return_value=True)
        gen_btn_loc.first.is_enabled = AsyncMock(return_value=True)
        gen_btn_loc.first.click = AsyncMock()

        stop_btn_loc = MagicMock()
        stop_btn_loc.first.is_visible = AsyncMock(side_effect=[True, False, False, False])

        backdrop_loc = MagicMock()
        backdrop_loc.first.is_visible = AsyncMock(return_value=False)

        def locator_router(sel):
            if "generate" in sel:
                return gen_btn_loc
            if "Stop" in sel:
                return stop_btn_loc
            if "cdk-overlay-backdrop" in sel:
                return backdrop_loc
            return editor_loc

        page.locator.side_effect = locator_router

        # Mock evaluate for images
        existing_imgs_mock = ["https://flow-content.google/image/old1"]
        new_imgs_mock = [
            {"src": "https://flow-content.google/image/old1", "className": "image", "width": 1024, "height": 768},
            {"src": "https://flow-content.google/image/new2", "className": "image", "width": 1376, "height": 768},
        ]
        page.evaluate = AsyncMock(return_value=new_imgs_mock)

        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "wait_for_editor", AsyncMock(return_value=editor_loc)),
            patch.object(
                worker,
                "_capture_stable_image_baseline",
                AsyncMock(return_value={worker._media_key(existing_imgs_mock[0])}),
            ),
            patch.object(
                worker,
                "_resolve_new_media_source",
                AsyncMock(return_value="https://flow-content.google/image/new2"),
            ),
            patch.object(
                worker,
                "_submit_prompt_and_wait_for_ack",
                AsyncMock(return_value=(0.0, "https://flow-content.google/image/new2")),
            ),
        ):
            img_url = await worker.generate_scene("A sunset", "blur", [])
            self.assertEqual(img_url, "https://flow-content.google/image/new2")
            self.assertEqual(editor_loc.click.call_count, 2)

    async def test_ensure_project_force_new_creates_brand_new_project(self):
        page = MagicMock()
        # Even if currently inside a project
        page.url = "https://flow.google.com/project/existing-123"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

        btn_loc = MagicMock()
        btn_loc.first.is_visible = AsyncMock(return_value=True)
        btn_loc.first.click = AsyncMock()
        page.locator.return_value = btn_loc

        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "wait_for_editor", new_callable=AsyncMock) as mock_wait,
            patch.object(worker, "_find_project_link", new_callable=AsyncMock) as mock_find,
        ):
            res = await worker.ensure_project("auto_yt_123", force_new=True)
            # Must navigate to home, not reuse existing URL
            page.goto.assert_called()
            # Must NOT attempt to find existing project link
            mock_find.assert_not_called()
            # Must click new project button
            btn_loc.first.click.assert_called()
            mock_wait.assert_called()

    async def test_clear_ingredient_chips_clicks_clear_button(self):
        page = MagicMock()
        chips_loc = MagicMock()
        chips_loc.count = AsyncMock(return_value=1)
        clear_btn = MagicMock()
        clear_btn.first.is_visible = AsyncMock(return_value=True)
        clear_btn.first.click = AsyncMock()

        def loc_router(sel):
            if "flow-ingredient-chip" in sel:
                return chips_loc
            if "clear" in sel.lower():
                return clear_btn
            return MagicMock()

        page.locator.side_effect = loc_router
        worker = GoogleFlowWorker(page)
        await worker.clear_ingredient_chips()
        clear_btn.first.click.assert_called_once()

    async def test_sync_reference_ingredients_attaches_asset(self):
        page = MagicMock()
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

        chips_loc = MagicMock()
        chips_loc.count = AsyncMock(return_value=0)

        add_btn = MagicMock()
        add_btn.first.is_visible = AsyncMock(return_value=True)
        add_btn.first.click = AsyncMock()

        uploads_tab = MagicMock()
        uploads_tab.first.is_visible = AsyncMock(return_value=True)
        uploads_tab.first.click = AsyncMock()

        asset_btn = MagicMock()
        asset_btn.first.is_visible = AsyncMock(return_value=True)
        asset_btn.first.click = AsyncMock()

        add_to_prompt_btn = MagicMock()
        add_to_prompt_btn.first.is_visible = AsyncMock(return_value=True)
        add_to_prompt_btn.first.click = AsyncMock()

        def loc_router(sel):
            sel_l = sel.lower()
            if "flow-ingredient-chip" in sel_l:
                return chips_loc
            if "add-menu-trigger" in sel_l or "add ingredients" in sel_l:
                return add_btn
            if "uploads" in sel_l:
                return uploads_tab
            if "asset-item" in sel_l:
                return asset_btn
            if "detail-add-to-prompt-btn" in sel_l or "add to prompt" in sel_l:
                return add_to_prompt_btn
            return MagicMock()

        page.locator.side_effect = loc_router
        worker = GoogleFlowWorker(page)
        await worker.sync_reference_ingredients(["dinh_doan"])

        add_btn.first.click.assert_called()
        uploads_tab.first.click.assert_called()
        asset_btn.first.click.assert_called()
        add_to_prompt_btn.first.click.assert_called()

    async def test_required_reference_uploads_once_then_reuses_existing_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "le_trong_tan.jpg"
            reference_path.write_bytes(b"portrait")
            page = MagicMock()
            page.url = "https://flow.google.com/project/project-one"
            worker = GoogleFlowWorker(page)

            async def mark_uploaded(path):
                worker._project_reference_cache().add(Path(path).name.casefold())

            attach_existing = AsyncMock(
                side_effect=[
                    (REFERENCE_RESULT_NOT_FOUND, ""),
                    (REFERENCE_RESULT_ATTACHED, "reused_picker"),
                    (REFERENCE_RESULT_ATTACHED, "reused_picker"),
                ]
            )
            upload = AsyncMock(side_effect=mark_uploaded)
            with (
                patch.object(worker, "_try_attach_existing_reference", attach_existing),
                patch.object(worker, "_upload_reference_file", upload),
                patch.object(worker, "_has_ingredient_chip", AsyncMock(return_value=False)),
            ):
                await worker._sync_required_scene_reference(
                    "le_trong_tan",
                    str(reference_path),
                )
                await worker._sync_required_scene_reference(
                    "le_trong_tan",
                    str(reference_path),
                )

            upload.assert_awaited_once_with(str(reference_path))

    async def test_cold_cache_reuses_flow_library_asset_without_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "le_trong_tan.jpg"
            reference_path.write_bytes(b"portrait")
            page = MagicMock()
            page.url = "https://flow.google.com/project/project-one"
            worker = GoogleFlowWorker(page)

            with (
                patch.object(
                    worker,
                    "_try_attach_existing_reference",
                    AsyncMock(return_value=(REFERENCE_RESULT_ATTACHED, "reused_picker")),
                ),
                patch.object(worker, "_upload_reference_file", AsyncMock()) as upload,
            ):
                await worker._sync_required_scene_reference(
                    "le_trong_tan",
                    str(reference_path),
                )

            upload.assert_not_awaited()
            self.assertIn(
                "le_trong_tan.jpg",
                worker._project_reference_cache(),
            )

    async def test_picker_error_falls_back_to_gallery(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        with (
            patch.object(
                worker,
                "_attach_reference_from_picker",
                AsyncMock(return_value=REFERENCE_RESULT_UI_ERROR),
            ),
            patch.object(
                worker,
                "_attach_reference_from_gallery",
                AsyncMock(return_value=REFERENCE_RESULT_ATTACHED),
            ) as gallery,
        ):
            result = await worker._try_attach_existing_reference("le_trong_tan.jpg")

        self.assertEqual(result, (REFERENCE_RESULT_ATTACHED, "reused_gallery"))
        gallery.assert_awaited_once_with("le_trong_tan.jpg")

    async def test_known_asset_ui_failure_never_uploads_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "le_trong_tan.jpg"
            reference_path.write_bytes(b"portrait")
            page = MagicMock()
            page.url = "https://flow.google.com/project/project-one"
            worker = GoogleFlowWorker(page)
            worker._project_reference_cache().add("le_trong_tan.jpg")

            with (
                patch.object(
                    worker,
                    "_try_attach_existing_reference",
                    AsyncMock(return_value=(REFERENCE_RESULT_UI_ERROR, "")),
                ),
                patch.object(worker, "_upload_reference_file", AsyncMock()) as upload,
                patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
            ):
                with self.assertRaises(ReferenceAttachmentError):
                    await worker._sync_required_scene_reference(
                        "le_trong_tan",
                        str(reference_path),
                    )

            upload.assert_not_awaited()

    async def test_reference_cache_is_isolated_by_flow_project(self):
        page_one = MagicMock()
        page_one.url = "https://flow.google.com/project/project-one"
        page_two = MagicMock()
        page_two.url = "https://flow.google.com/project/project-two"
        worker_one = GoogleFlowWorker(page_one)
        worker_two = GoogleFlowWorker(page_two)

        worker_one._project_reference_cache().add("le_trong_tan.jpg")

        self.assertIn("le_trong_tan.jpg", worker_one._project_reference_cache())
        self.assertNotIn("le_trong_tan.jpg", worker_two._project_reference_cache())

    async def test_same_filename_uploads_once_in_each_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "le_trong_tan.jpg"
            reference_path.write_bytes(b"portrait")
            upload_counts = []

            for project_id in ("project-one", "project-two"):
                page = MagicMock()
                page.url = f"https://flow.google.com/project/{project_id}"
                worker = GoogleFlowWorker(page)

                async def mark_uploaded(path, current_worker=worker):
                    current_worker._project_reference_cache().add(
                        Path(path).name.casefold()
                    )

                upload = AsyncMock(side_effect=mark_uploaded)
                with (
                    patch.object(
                        worker,
                        "_try_attach_existing_reference",
                        AsyncMock(
                            side_effect=[
                                (REFERENCE_RESULT_NOT_FOUND, ""),
                                (REFERENCE_RESULT_ATTACHED, "reused_picker"),
                                (REFERENCE_RESULT_ATTACHED, "reused_picker"),
                            ]
                        ),
                    ),
                    patch.object(worker, "_upload_reference_file", upload),
                    patch.object(worker, "_has_ingredient_chip", AsyncMock(return_value=False)),
                ):
                    await worker._sync_required_scene_reference(
                        "le_trong_tan",
                        str(reference_path),
                    )
                    await worker._sync_required_scene_reference(
                        "le_trong_tan",
                        str(reference_path),
                    )
                upload_counts.append(upload.await_count)

            self.assertEqual(upload_counts, [1, 1])

    async def test_sync_reference_ingredients_routes_required_path(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "clear_ingredient_chips", AsyncMock()),
            patch.object(worker, "dismiss_blocking_dialogs", AsyncMock()),
            patch.object(
                worker,
                "_sync_required_scene_reference",
                AsyncMock(),
            ) as sync_required,
        ):
            await worker.sync_reference_ingredients(
                ["le_trong_tan"],
                {"le_trong_tan": "C:/assets/le_trong_tan.jpg"},
            )

        sync_required.assert_awaited_once_with(
            "le_trong_tan",
            "C:/assets/le_trong_tan.jpg",
        )

    async def test_same_filename_ignores_changed_file_content(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_path = Path(first_dir) / "le_trong_tan.jpg"
            second_path = Path(second_dir) / "le_trong_tan.jpg"
            first_path.write_bytes(b"old portrait")
            second_path.write_bytes(b"new portrait")
            page = MagicMock()
            page.url = "https://flow.google.com/project/project-one"
            worker = GoogleFlowWorker(page)
            worker._project_reference_cache().add(first_path.name.casefold())

            with (
                patch.object(
                    worker,
                    "_try_attach_existing_reference",
                    AsyncMock(return_value=(REFERENCE_RESULT_ATTACHED, "reused_picker")),
                ),
                patch.object(worker, "_upload_reference_file", AsyncMock()) as upload,
            ):
                await worker._sync_required_scene_reference(
                    "le_trong_tan",
                    str(second_path),
                )

            upload.assert_not_awaited()

    async def test_exact_filename_matching_rejects_similar_names(self):
        self.assertTrue(
            GoogleFlowWorker._text_matches_asset_filename(
                "le_trong_tan.jpg\nHình ảnh",
                "LE_TRONG_TAN.JPG",
            )
        )
        self.assertFalse(
            GoogleFlowWorker._text_matches_asset_filename(
                "le_trong_tan_old.jpg\nHình ảnh",
                "le_trong_tan.jpg",
            )
        )

    async def test_exact_picker_match_uses_first_existing_duplicate(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        asset_items = MagicMock()
        asset_items.count = AsyncMock(return_value=3)
        similar = MagicMock()
        similar.inner_text = AsyncMock(return_value="le_trong_tan_old.jpg\nHình ảnh")
        first_exact = MagicMock()
        first_exact.inner_text = AsyncMock(return_value="le_trong_tan.jpg\nHình ảnh")
        second_exact = MagicMock()
        second_exact.inner_text = AsyncMock(return_value="le_trong_tan.jpg\nHình ảnh")
        asset_items.nth.side_effect = [similar, first_exact, second_exact]
        page.locator.return_value = asset_items
        worker = GoogleFlowWorker(page)

        result = await worker._find_exact_picker_asset("le_trong_tan.jpg")

        self.assertIs(result, first_exact)
        second_exact.inner_text.assert_not_awaited()

    async def test_wait_for_image_accepts_result_while_progress_is_stale(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(
                worker,
                "_resolve_new_media_source",
                AsyncMock(return_value="https://flow-content.google/image/new"),
            ),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=True)),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            result = await worker._wait_for_new_media(
                media_type="image",
                baseline_keys=set(),
                initial_error_texts=set(),
                submitted_at=0.0,
                timeout_seconds=240.0,
            )

        self.assertEqual(result, "https://flow-content.google/image/new")

    async def test_wait_for_video_accepts_result_while_progress_is_stale(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(
                worker,
                "_resolve_new_media_source",
                AsyncMock(return_value="blob:https://flow.google/video-new"),
            ),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=True)),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            result = await worker._wait_for_new_media(
                media_type="video",
                baseline_keys=set(),
                initial_error_texts=set(),
                submitted_at=0.0,
                timeout_seconds=180.0,
            )

        self.assertEqual(result, "blob:https://flow.google/video-new")

    async def test_collect_image_candidates_keeps_result_panel_assets(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.evaluate = AsyncMock(
            return_value=[
                {
                    "src": "https://flow-content.google/image/sidebar-result",
                    "assetId": "asset-1",
                    "width": 320,
                    "height": 180,
                    "source": "result_panel",
                }
            ]
        )
        worker = GoogleFlowWorker(page)

        candidates = await worker._collect_image_candidates()

        self.assertEqual(candidates[0]["source"], "result_panel")

    async def test_thumbnail_is_opened_to_resolve_full_size_image(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.evaluate = AsyncMock(return_value=True)
        worker = GoogleFlowWorker(page)
        small = {
            "src": "https://flow-content.google/image/thumb",
            "assetId": "asset-new",
            "width": 320,
            "height": 180,
        }
        large = {
            "src": "https://flow-content.google/image/full",
            "assetId": "asset-new",
            "width": 1376,
            "height": 768,
        }

        with (
            patch.object(
                worker,
                "_collect_image_candidates",
                AsyncMock(side_effect=[[small], [small, large]]),
            ),
            patch.object(
                worker,
                "_validate_image_candidate",
                AsyncMock(side_effect=["", large["src"]]),
            ),
        ):
            result = await worker._resolve_new_media_source("image", set())

        self.assertEqual(result, large["src"])
        page.evaluate.assert_awaited_once()

    async def test_uploaded_reference_is_not_selected_as_generated_result(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        worker._uploaded_image_urls.add("https://flow-content.google/image/reference")
        candidates = [
            {
                "src": "https://flow-content.google/image/reference",
                "width": 1200,
                "height": 800,
            },
            {
                "src": "https://flow-content.google/image/generated",
                "width": 1376,
                "height": 768,
            },
        ]

        result = worker._find_new_media_source(
            candidates,
            set(),
            exclude_uploaded_images=True,
        )

        self.assertEqual(result, "https://flow-content.google/image/generated")

    async def test_generation_start_failure_does_not_wait_for_hard_timeout(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_resolve_new_media_source", AsyncMock(return_value="")),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=False)),
            patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
            patch.object(worker, "_get_snackbar_error", AsyncMock(return_value="")),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch.object(worker, "_save_debug_screenshot", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.GENERATION_START_TIMEOUT_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.GENERATION_POLL_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(FlowGenerationStartError):
                await worker._wait_for_new_media(
                    media_type="image",
                    baseline_keys=set(),
                    initial_error_texts=set(),
                    submitted_at=0.0,
                    timeout_seconds=240.0,
                )

    async def test_submit_uses_composer_button_and_does_not_press_enter_after_ack(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        editor = MagicMock()
        button = MagicMock()
        button.click = AsyncMock()
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="baseline")),
            patch.object(worker, "_find_prompt_submit_button", AsyncMock(return_value=button)),
            patch.object(
                worker,
                "_wait_for_submission_ack",
                AsyncMock(return_value=("", "conversation")),
            ),
            patch.object(worker, "dismiss_blocking_dialogs", AsyncMock()),
        ):
            _, source = await worker._submit_prompt_and_wait_for_ack(
                editor=editor,
                full_prompt="prompt",
                media_type="image",
                baseline_keys=set(),
            )

        self.assertEqual(source, "")
        button.click.assert_awaited_once()
        page.keyboard.press.assert_not_awaited()

    async def test_submit_falls_back_to_enter_only_when_prompt_remains(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        editor = MagicMock()
        editor.focus = AsyncMock()
        button = MagicMock()
        button.click = AsyncMock()
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="baseline")),
            patch.object(worker, "_find_prompt_submit_button", AsyncMock(return_value=button)),
            patch.object(
                worker,
                "_wait_for_submission_ack",
                AsyncMock(side_effect=[("", ""), ("", "activity")]),
            ),
            patch.object(worker, "_read_editor_text", AsyncMock(return_value="prompt")),
            patch.object(worker, "dismiss_blocking_dialogs", AsyncMock()),
        ):
            await worker._submit_prompt_and_wait_for_ack(
                editor=editor,
                full_prompt="prompt",
                media_type="image",
                baseline_keys=set(),
            )

        button.click.assert_awaited_once()
        page.keyboard.press.assert_awaited_once_with("Enter")

    async def test_submit_retries_when_editor_clears_without_strong_ack_or_conversation(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        page.keyboard.insert_text = AsyncMock()
        editor = MagicMock()
        editor.click = AsyncMock()
        editor.fill = AsyncMock()
        editor.focus = AsyncMock()
        button = MagicMock()
        button.click = AsyncMock()
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="baseline")),
            patch.object(worker, "_find_prompt_submit_button", AsyncMock(return_value=button)) as find_button,
            patch.object(worker, "_wait_for_submission_ack", AsyncMock(side_effect=[("", ""), ("", "conversation")])),
            patch.object(worker, "_has_prompt_in_conversation", AsyncMock(side_effect=[False, True])),
            patch.object(worker, "_read_editor_text", AsyncMock(return_value="")),
            patch.object(worker, "dismiss_blocking_dialogs", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            _, source = await worker._submit_prompt_and_wait_for_ack(
                editor=editor,
                full_prompt="prompt",
                media_type="image",
                baseline_keys=set(),
            )

        self.assertEqual(source, "")
        button.click.assert_awaited_once()
        page.keyboard.press.assert_awaited_with("Enter")

    async def test_submit_failure_raises_without_starting_generation_wait(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        editor = MagicMock()
        editor.focus = AsyncMock()
        button = MagicMock()
        button.click = AsyncMock()
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="baseline")),
            patch.object(worker, "_find_prompt_submit_button", AsyncMock(return_value=button)),
            patch.object(worker, "_wait_for_submission_ack", AsyncMock(return_value=("", ""))),
            patch.object(worker, "_read_editor_text", AsyncMock(return_value="prompt")),
            patch.object(worker, "dismiss_blocking_dialogs", AsyncMock()),
            patch.object(worker, "_save_debug_screenshot", AsyncMock()) as screenshot,
        ):
            with self.assertRaises(FlowSubmissionError):
                await worker._submit_prompt_and_wait_for_ack(
                    editor=editor,
                    full_prompt="prompt",
                    media_type="image",
                    baseline_keys=set(),
                )

        button.click.assert_awaited_once()
        page.keyboard.press.assert_awaited_once_with("Enter")
        screenshot.assert_awaited_once_with("image_submit_failed")

    async def test_submission_ack_detects_new_conversation_state_without_progress(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_resolve_new_media_source", AsyncMock(return_value="")),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=False)),
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="new-turn")),
        ):
            source, reason = await worker._wait_for_submission_ack(
                media_type="image",
                baseline_keys={"old-image"},
                baseline_marker="old-turn",
            )

        self.assertEqual(source, "")
        self.assertEqual(reason, "conversation")

    async def test_previous_scene_state_does_not_acknowledge_new_submission(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_resolve_new_media_source", AsyncMock(return_value="")),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=False)),
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="old-turn")),
            patch("auto_yt.services.google_flow_worker.SUBMISSION_ACK_TIMEOUT_SECONDS", 0.0),
        ):
            source, reason = await worker._wait_for_submission_ack(
                media_type="image",
                baseline_keys={"old-image"},
                baseline_marker="old-turn",
            )

        self.assertEqual(source, "")
        self.assertEqual(reason, "")

    async def test_acknowledged_generation_can_start_after_twenty_seconds(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        regular_scans = 0

        async def resolve(_media_type, _baseline_keys, *, recover=False):
            nonlocal regular_scans
            if recover:
                return ""
            regular_scans += 1
            return "https://flow-content.google/image/generated" if regular_scans == 2 else ""

        with (
            patch.object(worker, "_resolve_new_media_source", AsyncMock(side_effect=resolve)),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=False)),
            patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
            patch.object(worker, "_get_snackbar_error", AsyncMock(return_value="")),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
            patch(
                "auto_yt.services.google_flow_worker.time.monotonic",
                side_effect=[21.0, 22.0, 22.0],
            ),
        ):
            result = await worker._wait_for_new_media(
                media_type="image",
                baseline_keys=set(),
                initial_error_texts=set(),
                submitted_at=0.0,
                timeout_seconds=240.0,
            )

        self.assertEqual(result, "https://flow-content.google/image/generated")

    async def test_three_ui_poll_errors_fail_without_waiting_for_timeout(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(
                worker,
                "_resolve_new_media_source",
                AsyncMock(side_effect=RuntimeError("CDP disconnected")),
            ),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch.object(worker, "_save_debug_screenshot", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.GENERATION_POLL_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(FlowUiStateError):
                await worker._wait_for_new_media(
                    media_type="image",
                    baseline_keys=set(),
                    initial_error_texts=set(),
                    submitted_at=0.0,
                    timeout_seconds=240.0,
                )

    async def test_video_mode_failure_stops_before_prompt_submission(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_get_existing_videos", AsyncMock(return_value=set())),
            patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
            patch.object(worker, "sync_reference_ingredients", AsyncMock()),
            patch.object(
                worker,
                "_activate_video_mode",
                AsyncMock(side_effect=FlowModeError("mode unavailable")),
            ),
            patch.object(worker, "wait_for_editor", AsyncMock()) as wait_for_editor,
        ):
            with self.assertRaises(FlowModeError):
                await worker.generate_scene_video("prompt", "avoid")

        wait_for_editor.assert_not_awaited()

    async def test_video_frames_use_slots_and_not_ingredient_references(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        editor = MagicMock()
        editor.click = AsyncMock()
        editor.fill = AsyncMock()
        editor.focus = AsyncMock()
        worker = GoogleFlowWorker(page)

        with tempfile.TemporaryDirectory() as temp_dir:
            start_frame = Path(temp_dir) / "start.png"
            end_frame = Path(temp_dir) / "end.png"
            start_frame.write_bytes(b"start")
            end_frame.write_bytes(b"end")
            with (
                patch.object(worker, "_get_existing_videos", AsyncMock(return_value=set())),
                patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
                patch.object(worker, "sync_reference_ingredients", AsyncMock()) as sync_refs,
                patch.object(worker, "_activate_video_mode", AsyncMock()),
                patch.object(
                    worker,
                    "_attach_video_frame",
                    AsyncMock(side_effect=[True, True]),
                ) as attach_frame,
                patch.object(worker, "wait_for_editor", AsyncMock(return_value=editor)),
                patch.object(
                    worker,
                    "_resolve_new_media_source",
                    AsyncMock(return_value="blob:https://flow.google/video-new"),
                ),
                patch.object(
                    worker,
                    "_wait_for_new_media",
                    AsyncMock(return_value="blob:https://flow.google/video-new"),
                ),
                patch.object(
                    worker,
                    "_submit_prompt_and_wait_for_ack",
                    AsyncMock(return_value=(0.0, "blob:https://flow.google/video-new")),
                ),
                patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
            ):
                result = await worker.generate_scene_video(
                    "prompt",
                    "avoid",
                    start_frame_path=start_frame,
                    end_frame_path=end_frame,
                    reference_ids=["character-one"],
                )

        self.assertEqual(result, "blob:https://flow.google/video-new")
        sync_refs.assert_awaited_once_with(["character-one"])
        self.assertEqual(
            [call.args[1] for call in attach_frame.await_args_list],
            ["start", "end"],
        )

    async def test_wait_for_editor_rejects_asset_edit_route(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one/edit/asset-one"
        worker = GoogleFlowWorker(page)

        with self.assertRaises(FlowUiStateError):
            await worker.wait_for_editor(timeout=0.1)

    async def test_restore_project_canvas_navigates_when_done_is_unavailable(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one/edit/asset-one"
        done = MagicMock()
        done.first.is_visible = AsyncMock(return_value=False)
        page.locator.return_value = done

        async def navigate(url, **_kwargs):
            page.url = url

        page.goto = AsyncMock(side_effect=navigate)
        worker = GoogleFlowWorker(page)
        with patch.object(worker, "wait_for_load", AsyncMock()):
            await worker._restore_project_canvas()

        self.assertEqual(page.url, "https://flow.google.com/project/project-one")
        page.goto.assert_awaited_once()

    async def test_ensure_project_does_not_reuse_asset_edit_route(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one/edit/asset-one"
        page.title = AsyncMock(return_value="auto_yt_236")
        done = MagicMock()
        done.first.is_visible = AsyncMock(return_value=False)
        page.locator.return_value = done

        async def navigate(url, **_kwargs):
            page.url = url

        page.goto = AsyncMock(side_effect=navigate)
        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "wait_for_load", AsyncMock()),
            patch.object(worker, "wait_for_editor", AsyncMock()) as wait_for_editor,
        ):
            result = await worker.ensure_project("auto_yt_236")

        self.assertEqual(result, "https://flow.google.com/project/project-one")
        wait_for_editor.assert_awaited_once()

    async def test_generate_scene_restores_canvas_before_prompt_work(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one/edit/asset-one"
        done = MagicMock()
        done.first.is_visible = AsyncMock(return_value=False)
        page.locator.return_value = done

        async def navigate(url, **_kwargs):
            page.url = url

        page.goto = AsyncMock(side_effect=navigate)
        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "wait_for_load", AsyncMock()),
            patch.object(
                worker,
                "_generate_scene_on_canvas",
                AsyncMock(return_value="https://flow-content.google/image/new"),
            ) as generate,
        ):
            result = await worker.generate_scene("prompt", "avoid", [])

        self.assertEqual(result, "https://flow-content.google/image/new")
        self.assertEqual(page.url, "https://flow.google.com/project/project-one")
        generate.assert_awaited_once()

    async def test_reference_portrait_is_excluded_before_download_or_click(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.request.get = AsyncMock()
        worker = GoogleFlowWorker(page)
        worker._active_reference_filenames = {"le_trong_tan.jpg"}
        reference = {
            "src": "https://flow-content.google/image/reference",
            "assetId": "reference-asset",
            "labelText": "le_trong_tan.jpg\nHình ảnh",
            "width": 433,
            "height": 461,
        }

        with patch.object(
            worker,
            "_collect_image_candidates",
            AsyncMock(return_value=[reference]),
        ):
            result = await worker._resolve_new_media_source("image", set())

        self.assertEqual(result, "")
        page.request.get.assert_not_awaited()

    async def test_reference_asset_key_excludes_late_candidate(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        worker._active_reference_keys = {"asset:reference-asset"}
        candidate = {
            "src": "https://flow-content.google/image/reference-late",
            "assetId": "reference-asset",
            "labelText": "",
        }

        self.assertIsNone(worker._find_new_media_candidate([candidate], set()))

    async def test_similar_reference_filename_is_not_excluded(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        worker._active_reference_filenames = {"le_trong_tan.jpg"}
        candidate = {
            "src": "https://flow-content.google/image/similar",
            "assetId": "similar-asset",
            "labelText": "le_trong_tan_old.jpg\nHình ảnh",
        }

        self.assertIs(
            worker._find_new_media_candidate([candidate], set()),
            candidate,
        )

    async def test_reference_and_generated_image_selects_generated_output(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.evaluate = AsyncMock()
        response = MagicMock()
        response.status = 200
        response.body = AsyncMock(return_value=_image_bytes())
        page.request.get = AsyncMock(return_value=response)
        worker = GoogleFlowWorker(page)
        worker._active_reference_filenames = {"le_trong_tan.jpg"}
        reference = {
            "src": "https://flow-content.google/image/reference",
            "assetId": "reference-asset",
            "labelText": "le_trong_tan.jpg\nHình ảnh",
            "width": 433,
            "height": 461,
        }
        generated = {
            "src": "https://flow-content.google/image/generated",
            "assetId": "generated-asset",
            "labelText": "Generated scene",
            "width": 320,
            "height": 180,
        }

        with patch.object(
            worker,
            "_collect_image_candidates",
            AsyncMock(return_value=[reference, generated]),
        ):
            result = await worker._resolve_new_media_source("image", set())

        self.assertEqual(result, generated["src"])
        page.request.get.assert_awaited_once_with(generated["src"], timeout=30000)
        page.evaluate.assert_not_awaited()

    async def test_reference_sync_happens_before_image_baseline(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        events = []

        async def sync(*_args):
            events.append("reference")

        async def baseline():
            events.append("baseline")
            return set()

        with (
            patch.object(worker, "sync_reference_ingredients", AsyncMock(side_effect=sync)),
            patch.object(worker, "_capture_stable_image_baseline", AsyncMock(side_effect=baseline)),
            patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
            patch.object(worker, "wait_for_editor", AsyncMock(side_effect=RuntimeError("stop"))),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                await worker._generate_scene_on_canvas("prompt", "avoid", [], {})

        self.assertEqual(events, ["reference", "baseline"])

    async def test_validated_payload_is_reused_by_download_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            page = MagicMock()
            page.url = "https://flow.google.com/project/project-one"
            response = MagicMock()
            response.status = 200
            response.body = AsyncMock(return_value=_image_bytes())
            page.request.get = AsyncMock(return_value=response)
            worker = GoogleFlowWorker(page)
            asset_url = "https://flow-content.google/image/generated?token=one"

            self.assertTrue(await worker._validate_image_url(asset_url))
            target = Path(temp_dir) / "scene.png"
            await worker.download_image(asset_url, str(target))

            self.assertTrue(target.is_file())
            self.assertEqual(page.request.get.await_count, 1)

    async def test_invalid_candidate_does_not_hide_later_valid_candidate(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"

        async def request_image(url, **_kwargs):
            response = MagicMock()
            response.status = 200
            response.body = AsyncMock(
                return_value=_image_bytes((433, 461))
                if url.endswith("portrait")
                else _image_bytes()
            )
            return response

        page.request.get = AsyncMock(side_effect=request_image)
        page.evaluate = AsyncMock()
        worker = GoogleFlowWorker(page)
        portrait = {
            "src": "https://flow-content.google/image/portrait",
            "assetId": "portrait",
            "width": 433,
            "height": 461,
        }
        generated = {
            "src": "https://flow-content.google/image/generated",
            "assetId": "generated",
            "width": 320,
            "height": 180,
        }

        with patch.object(
            worker,
            "_collect_image_candidates",
            AsyncMock(return_value=[portrait, generated]),
        ):
            result = await worker._resolve_new_media_source("image", set())

        self.assertEqual(result, generated["src"])
        self.assertEqual(worker._last_invalid_image_size, (433, 461))
        page.evaluate.assert_not_awaited()

    async def test_thumbnail_and_original_query_variants_are_validated_separately(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"

        async def request_image(url, **_kwargs):
            response = MagicMock()
            response.status = 200
            response.body = AsyncMock(
                return_value=_image_bytes((320, 180))
                if "size=thumb" in url
                else _image_bytes()
            )
            return response

        page.request.get = AsyncMock(side_effect=request_image)
        worker = GoogleFlowWorker(page)
        thumbnail = "https://flow-content.google/image/asset?size=thumb"
        original = "https://flow-content.google/image/asset?size=original"
        candidate = {
            "src": thumbnail,
            "urls": [thumbnail, original],
            "assetId": "asset-one",
        }

        result = await worker._validate_image_candidate(candidate)

        self.assertEqual(result, original)
        self.assertEqual(page.request.get.await_count, 2)

    async def test_invalid_output_raises_after_flow_is_idle(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        worker._last_invalid_image_size = (433, 461)

        with (
            patch.object(worker, "_resolve_new_media_source", AsyncMock(return_value="")),
            patch.object(worker, "_read_generation_activity", AsyncMock(return_value=False)),
            patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
            patch.object(worker, "_get_snackbar_error", AsyncMock(return_value="")),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch.object(worker, "_save_debug_screenshot", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.GENERATION_START_TIMEOUT_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.GENERATION_POLL_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(FlowInvalidOutputError):
                await worker._wait_for_new_media(
                    media_type="image",
                    baseline_keys=set(),
                    initial_error_texts=set(),
                    submitted_at=0.0,
                    timeout_seconds=240.0,
                )

    async def test_only_reference_after_generation_returns_result_missing(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)

        with (
            patch.object(worker, "_resolve_new_media_source", AsyncMock(return_value="")),
            patch.object(
                worker,
                "_read_generation_activity",
                AsyncMock(side_effect=[True, False, False]),
            ),
            patch.object(worker, "_get_existing_error_texts", AsyncMock(return_value=set())),
            patch.object(worker, "_get_snackbar_error", AsyncMock(return_value="")),
            patch.object(worker, "handle_confirmation_prompts", AsyncMock()),
            patch.object(worker, "_save_debug_screenshot", AsyncMock()),
            patch("auto_yt.services.google_flow_worker.GENERATION_IDLE_GRACE_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.GENERATION_POLL_SECONDS", 0.0),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaises(FlowResultMissingError):
                await worker._wait_for_new_media(
                    media_type="image",
                    baseline_keys=set(),
                    initial_error_texts=set(),
                    submitted_at=asyncio.get_event_loop().time(),
                    timeout_seconds=240.0,
                )

    async def test_detail_fallback_restores_canvas_before_returning(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        done = MagicMock()
        done.first.is_visible = AsyncMock(return_value=True)

        async def close_detail(**_kwargs):
            page.url = "https://flow.google.com/project/project-one"

        done.first.click = AsyncMock(side_effect=close_detail)
        page.locator.return_value = done

        async def open_detail(*_args):
            page.url = "https://flow.google.com/project/project-one/edit/generated"
            return True

        page.evaluate = AsyncMock(side_effect=open_detail)
        worker = GoogleFlowWorker(page)
        candidate = {
            "src": "https://flow-content.google/image/thumb",
            "assetId": "generated",
        }
        detail = {
            "src": "https://flow-content.google/image/full",
            "assetId": "generated",
        }

        with (
            patch.object(
                worker,
                "_collect_image_candidates",
                AsyncMock(return_value=[detail]),
            ),
            patch.object(
                worker,
                "_validate_image_candidate",
                AsyncMock(return_value=detail["src"]),
            ),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            result = await worker._inspect_image_candidate_detail(candidate, set())

        self.assertEqual(result, detail["src"])
        self.assertEqual(page.url, "https://flow.google.com/project/project-one")
        done.first.click.assert_awaited_once()

    async def test_detail_fallback_restores_canvas_when_validation_fails(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        done = MagicMock()
        done.first.is_visible = AsyncMock(return_value=True)

        async def close_detail(**_kwargs):
            page.url = "https://flow.google.com/project/project-one"

        done.first.click = AsyncMock(side_effect=close_detail)
        page.locator.return_value = done

        async def open_detail(*_args):
            page.url = "https://flow.google.com/project/project-one/edit/generated"
            return True

        page.evaluate = AsyncMock(side_effect=open_detail)
        worker = GoogleFlowWorker(page)
        candidate = {
            "src": "https://flow-content.google/image/thumb",
            "assetId": "generated",
        }

        with (
            patch.object(
                worker,
                "_collect_image_candidates",
                AsyncMock(return_value=[candidate]),
            ),
            patch.object(
                worker,
                "_validate_image_candidate",
                AsyncMock(side_effect=RuntimeError("validation failed")),
            ),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaisesRegex(RuntimeError, "validation failed"):
                await worker._inspect_image_candidate_detail(candidate, set())

        self.assertEqual(page.url, "https://flow.google.com/project/project-one")
        done.first.click.assert_awaited_once()

    def test_upgrade_google_cdn_image_url(self):
        self.assertEqual(
            _upgrade_google_cdn_image_url("https://lh3.googleusercontent.com/abc123xyz=s512"),
            "https://lh3.googleusercontent.com/abc123xyz=s0",
        )
        self.assertEqual(
            _upgrade_google_cdn_image_url("https://lh3.googleusercontent.com/abc123xyz=w1376-h768-no"),
            "https://lh3.googleusercontent.com/abc123xyz=s0",
        )
        self.assertEqual(
            _upgrade_google_cdn_image_url("https://flow-content.google/image/xyz=s256"),
            "https://flow-content.google/image/xyz=s0",
        )
        self.assertEqual(
            _upgrade_google_cdn_image_url("blob:https://flow.google.com/123"),
            "blob:https://flow.google.com/123",
        )
        self.assertEqual(
            _upgrade_google_cdn_image_url("https://example.com/image.png"),
            "https://example.com/image.png",
        )

    def test_media_key_normalizes_google_cdn_thumbnail_params(self):
        k1 = GoogleFlowWorker._media_key("https://lh3.googleusercontent.com/abc123xyz=s512")
        k2 = GoogleFlowWorker._media_key("https://lh3.googleusercontent.com/abc123xyz=s0")
        k3 = GoogleFlowWorker._media_key("https://lh3.googleusercontent.com/abc123xyz=w1376-h768")
        self.assertEqual(k1, "https://lh3.googleusercontent.com/abc123xyz")
        self.assertEqual(k2, "https://lh3.googleusercontent.com/abc123xyz")
        self.assertEqual(k3, "https://lh3.googleusercontent.com/abc123xyz")

    async def test_submit_prompt_retries_on_silent_drop_when_editor_empty_and_unacknowledged(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        page.keyboard.insert_text = AsyncMock()

        editor_loc = MagicMock()
        editor_loc.focus = AsyncMock()
        editor_loc.click = AsyncMock()
        editor_loc.fill = AsyncMock()
        editor_loc.evaluate = AsyncMock(return_value="")

        btn_loc = MagicMock()
        btn_loc.first = MagicMock()
        btn_loc.first.is_visible = AsyncMock(return_value=True)
        btn_loc.first.is_enabled = AsyncMock(return_value=True)
        btn_loc.first.click = AsyncMock()

        worker = GoogleFlowWorker(page)
        with (
            patch.object(worker, "dismiss_blocking_dialogs", AsyncMock()),
            patch.object(worker, "_find_prompt_submit_button", AsyncMock(return_value=btn_loc.first)),
            patch.object(worker, "_capture_submission_marker", AsyncMock(return_value="marker1")),
            patch.object(worker, "_wait_for_submission_ack", AsyncMock(side_effect=[("", ""), ("", "conversation")])),
            patch.object(worker, "_has_prompt_in_conversation", AsyncMock(side_effect=[False, True])),
            patch("auto_yt.services.google_flow_worker.asyncio.sleep", AsyncMock()),
        ):
            attempted_at, source = await worker._submit_prompt_and_wait_for_ack(
                editor=editor_loc,
                full_prompt="Scene 1 soldiers",
                media_type="image",
                baseline_keys=set(),
            )
            self.assertGreater(attempted_at, 0)
            self.assertEqual(btn_loc.first.click.await_count, 1)
            page.keyboard.press.assert_awaited_with("Enter")
            editor_loc.fill.assert_called()

    async def test_validate_image_url_fetches_blob_payload(self):
        page = MagicMock()
        page.url = "https://flow.google.com/project/project-one"
        worker = GoogleFlowWorker(page)
        img_bytes = _image_bytes((1376, 768))

        with patch.object(worker, "_fetch_blob_payload", AsyncMock(return_value=img_bytes)):
            valid = await worker._validate_image_url("blob:https://flow.google.com/asset-123")
            self.assertTrue(valid)
            self.assertIsNotNone(worker._validated_image_payload)
            self.assertEqual(worker._validated_image_payload[1], img_bytes)

    async def test_has_ingredient_chip_finds_prompt_attachment_image(self):
        page = MagicMock()
        worker = GoogleFlowWorker(page)
        page.locator = MagicMock()
        img_loc = MagicMock()
        img_loc.first = MagicMock()
        img_loc.first.is_visible = AsyncMock(return_value=True)

        def loc_router(sel):
            if "flow-prompt-box" in sel:
                return img_loc
            mock_none = MagicMock()
            mock_none.first.is_visible = AsyncMock(return_value=False)
            return mock_none

        page.locator.side_effect = loc_router
        has_chip = await worker._has_ingredient_chip()
        self.assertTrue(has_chip)



if __name__ == "__main__":
    unittest.main()
