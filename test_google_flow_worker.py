import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from auto_yt.services.google_flow_worker import (
    GoogleFlowWorker,
    REFERENCE_RESULT_ATTACHED,
    REFERENCE_RESULT_NOT_FOUND,
    REFERENCE_RESULT_UI_ERROR,
    ReferenceAttachmentError,
)


class GoogleFlowWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        GoogleFlowWorker._session_uploaded_references.clear()

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
            patch.object(worker, "_get_existing_images", AsyncMock(return_value=set(existing_imgs_mock))),
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
            patch.object(worker, "_get_existing_images", AsyncMock(return_value=set(existing_imgs_mock))),
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


if __name__ == "__main__":
    unittest.main()
