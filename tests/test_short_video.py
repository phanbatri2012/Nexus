import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from nexus_engine.services import database as db
from nexus_engine.services import muse_provider
from nexus_engine.services import short_video_api
from nexus_engine.services import short_video_pipeline
from nexus_engine.services.google_flow_worker import FlowVideoPending, GoogleFlowWorker
from nexus_engine.services.short_video_api import CreateShortVideoRequest
from nexus_engine.services.short_video_registry import ShortVideoProjectRegistry


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "short_video_projects" / "bac-ho-ngam-tho.v1.json"


def manifest_data():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def voice_record():
    return {
        "id": "66cb0b63-125b-4615-8600-e918c56a9c7d",
        "name": "bac_ho_doc_tho_1",
        "provider_id": "omnivoice",
        "provider_voice_id": "voice-profile",
        "revision": 1,
        "status": "active",
        "config": {},
    }


def profile_snapshot():
    data = manifest_data()
    data["resolved_voice_snapshot"] = {
        "voice_id": data["voice_profile"]["voice_id"],
        "voice_name": data["voice_profile"]["voice_name"],
        "provider_id": "omnivoice",
        "provider_voice_id": "voice-profile",
        "voice_revision": 1,
        "config": {"target_words_per_minute": 145},
    }
    return data


class ShortVideoRegistryTests(unittest.TestCase):
    def _registry(self, *documents):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        directory = Path(temp.name)
        for index, document in enumerate(documents):
            (directory / f"project-{index}.json").write_text(
                json.dumps(document, ensure_ascii=False), encoding="utf-8"
            )
        return temp, ShortVideoProjectRegistry(directory)

    def test_valid_manifest_resolves_voice_dependency(self):
        temp, registry = self._registry(manifest_data())
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                project = registry.get("bac-ho-ngam-tho")
            self.assertTrue(project["available"])
            self.assertEqual(project["pipeline_profile"]["id"], "poem_to_short_v1")
            self.assertEqual(project["visual_profile"]["aspect_ratio"], "9:16")
        finally:
            temp.cleanup()

    def test_create_snapshot_can_select_google_flow(self):
        temp, registry = self._registry(manifest_data())
        try:
            with (
                patch(
                    "nexus_engine.services.short_video_registry.voice_config.get_voice",
                    return_value=voice_record(),
                ),
                patch(
                    "nexus_engine.services.short_video_registry.voice_config.build_voice_snapshot",
                    return_value={
                        "voice_id": voice_record()["id"],
                        "voice_name": voice_record()["name"],
                        "provider_id": "omnivoice",
                        "config": {},
                    },
                ),
            ):
                snapshot = registry.snapshot_for_create(
                    "bac-ho-ngam-tho",
                    visual_provider="google_flow",
                )
            self.assertEqual(snapshot["visual_profile"]["provider"], "google_flow")
            self.assertIn("muse", snapshot["visual_profile"]["available_providers"])
        finally:
            temp.cleanup()

    def test_default_visual_provider_must_be_available(self):
        document = manifest_data()
        document["visual_profile"]["available_providers"] = ["google_flow"]
        temp, registry = self._registry(document)
        try:
            project = registry.list_projects()[0]
            self.assertFalse(project["available"])
            self.assertIn("available_providers", " ".join(project["errors"]))
        finally:
            temp.cleanup()

    def test_duplicate_key_marks_every_copy_unavailable(self):
        first = manifest_data()
        second = deepcopy(first)
        second["profile_version"] = "2.0.0"
        temp, registry = self._registry(first, second)
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                projects = registry.list_projects()
            self.assertEqual(len(projects), 2)
            self.assertTrue(all(not project["available"] for project in projects))
            self.assertTrue(all("Trùng project key" in " ".join(project["errors"]) for project in projects))
        finally:
            temp.cleanup()

    def test_secret_field_is_rejected_without_crashing_registry(self):
        document = manifest_data()
        document["visual_profile"]["api_key"] = "must-not-be-stored"
        temp, registry = self._registry(document)
        try:
            project = registry.list_projects()[0]
            self.assertFalse(project["available"])
            self.assertIn("không được chứa trường bí mật", " ".join(project["errors"]))
        finally:
            temp.cleanup()

    def test_secret_suffix_is_rejected(self):
        document = manifest_data()
        document["visual_profile"]["provider_access_token"] = "must-not-be-stored"
        temp, registry = self._registry(document)
        try:
            project = registry.list_projects()[0]
            self.assertFalse(project["available"])
            self.assertIn("provider_access_token", " ".join(project["errors"]))
        finally:
            temp.cleanup()

    def test_dependency_exception_marks_project_unavailable(self):
        temp, registry = self._registry(manifest_data())
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                side_effect=RuntimeError("dependency offline"),
            ):
                project = registry.get("bac-ho-ngam-tho")
            self.assertIsNotNone(project)
            self.assertFalse(project["available"])
            self.assertIn("dependency", project["dependencies"][1]["detail"].casefold())
        finally:
            temp.cleanup()

    def test_archived_projects_are_hidden_from_menu_query(self):
        document = manifest_data()
        document["status"] = "archived"
        temp, registry = self._registry(document)
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                self.assertEqual(registry.list_projects(include_archived=False), [])
                self.assertEqual(len(registry.list_projects(include_archived=True)), 1)
        finally:
            temp.cleanup()


class ShortVideoStoreAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "short-video.db")
        self.artifact_patch = patch.object(short_video_pipeline, "SHORT_VIDEO_DIR", self.root / "artifacts")
        self.db_patch.start()
        self.artifact_patch.start()
        db.init_db()

    def tearDown(self):
        self.artifact_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_double_click_is_idempotent_and_kicks_once(self):
        snapshot = profile_snapshot()
        request = CreateShortVideoRequest(
            title="Nguyên tiêu",
            poem="Rằm xuân lồng lộng trăng soi",
            client_request_id="request-123456",
        )
        with (
            patch.object(short_video_api.registry, "snapshot_for_create", return_value=snapshot),
            patch.object(short_video_api.short_video_worker, "kick") as kick,
        ):
            first = short_video_api.create_short_video_item("bac-ho-ngam-tho", request)
            second = short_video_api.create_short_video_item("bac-ho-ngam-tho", request)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["item"]["id"], second["item"]["id"])
        self.assertEqual(kick.call_count, 1)
        jobs = db.list_system_jobs(limit=None, job_type="short_video_generation")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["payload"]["action"], "generate")

    def test_public_item_does_not_expose_filesystem_path_or_receipts(self):
        snapshot = profile_snapshot()
        item, _job, _created = db.create_short_video_item_with_job(
            item_id="11111111-1111-4111-8111-111111111111",
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Cảnh khuya",
            source_text="Tiếng suối trong như tiếng hát xa",
            manifest=snapshot,
            job_id="short-video-public",
            payload={"short_video_id": "11111111-1111-4111-8111-111111111111"},
            voice_snapshot=snapshot["resolved_voice_snapshot"],
        )
        public = short_video_api._public_item(item, detail=True)
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("provider_task_id", serialized)
        self.assertIn("/api/short-video/items/", public["artifact_urls"]["draft"])

    def test_restart_pauses_short_job_without_starting_worker(self):
        snapshot = profile_snapshot()
        item_id = "33333333-3333-4333-8333-333333333333"
        _item, job, _created = db.create_short_video_item_with_job(
            item_id=item_id,
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Rằm tháng giêng",
            source_text="Rằm xuân lồng lộng trăng soi",
            manifest=snapshot,
            job_id="short-video-restart-paused",
            payload={"short_video_id": item_id, "action": "generate"},
            voice_snapshot=snapshot["resolved_voice_snapshot"],
        )
        self.assertEqual(job["status"], "queued")
        worker = short_video_api.short_video_worker
        with patch.object(worker, "_COORDINATOR", None):
            self.assertEqual(worker.pause_after_restart(), 1)
            self.assertFalse(worker.status()["running"])
        paused = db.get_system_job("short-video-restart-paused")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(
            db.get_short_video_item(item_id)["current_stage"],
            "short_video_resume_after_restart",
        )


class ShortVideoPipelineUnitTests(unittest.TestCase):
    def test_scene_windows_are_vertical_pipeline_sized_and_bounded(self):
        windows = short_video_pipeline.build_scene_windows(
            poem="\n".join(f"Dòng thơ {index}" for index in range(60)),
            timings=[],
            duration=179.0,
            pipeline={"scene_max_seconds": 8, "max_scenes": 24},
        )
        self.assertLessEqual(len(windows), 24)
        self.assertTrue(all(float(scene["duration"]) <= 8.01 for scene in windows))
        self.assertLessEqual(sum(bool(scene["is_video"]) for scene in windows), 3)
        self.assertFalse(windows[-1]["is_video"])

    def test_scene_windows_honor_manifest_video_scene_limit(self):
        windows = short_video_pipeline.build_scene_windows(
            poem="Một\nHai\nBa\nBốn",
            timings=[],
            duration=28.0,
            pipeline={"scene_max_seconds": 8, "max_scenes": 24},
            max_video_scenes=0,
        )
        self.assertFalse(any(scene["is_video"] for scene in windows))

    def test_audio_regeneration_invalidates_all_dependent_stages(self):
        checkpoint = {
            "audio_revision": 2,
            "audio": {"normalized_path": "audio.wav"},
            "timing": {"path": "timing.json"},
            "storyboard": {"path": "storyboard.json"},
            "scenes": [{"index": 0}],
            "artifacts": {"draft": {"path": "draft.mp4"}, "final": {"path": "final.mp4"}},
        }
        short_video_pipeline._invalidate_for_audio_regeneration(checkpoint)
        self.assertEqual(checkpoint["audio_revision"], 3)
        self.assertEqual(checkpoint["audio"], {})
        self.assertEqual(checkpoint["timing"], {})
        self.assertEqual(checkpoint["storyboard"], {})
        self.assertEqual(checkpoint["scenes"], [])
        self.assertEqual(checkpoint["artifacts"], {})

    def test_scene_regeneration_preserves_audio_and_other_scenes(self):
        checkpoint = {
            "audio": {"normalized_path": "audio.wav"},
            "scene_revisions": {},
            "scenes": [
                {"index": 0, "image": {"path": "0.png"}, "video": {"path": "0.mp4"}},
                {"index": 1, "image": {"path": "1.png"}, "video": {}},
                {"index": 2, "image": {"path": "2.png"}, "video": {}},
            ],
            "artifacts": {"draft": {"path": "draft.mp4"}},
        }
        short_video_pipeline._invalidate_scene(checkpoint, 1)
        self.assertEqual(checkpoint["audio"]["normalized_path"], "audio.wav")
        self.assertEqual(checkpoint["scenes"][0]["image"]["path"], "0.png")
        self.assertEqual(checkpoint["scenes"][0]["video"], {})
        self.assertEqual(checkpoint["scenes"][1]["image"], {})
        self.assertEqual(checkpoint["scenes"][2]["image"]["path"], "2.png")

    def test_media_dispatches_to_google_flow_from_profile_snapshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            item_dir = Path(temp_dir)
            checkpoint = {"scenes": []}
            with (
                patch.object(
                    short_video_pipeline,
                    "_generate_flow_media_async",
                    new=AsyncMock(),
                ) as flow_generator,
                patch.object(
                    short_video_pipeline,
                    "_generate_muse_media_async",
                    new=AsyncMock(),
                ) as muse_generator,
            ):
                short_video_pipeline._ensure_media(
                    checkpoint=checkpoint,
                    item_dir=item_dir,
                    job={"id": "job-flow"},
                    item={"id": "11111111-1111-4111-8111-111111111111"},
                    profile={"visual_profile": {"provider": "google_flow"}},
                    plan={"scenes": []},
                )
            flow_generator.assert_awaited_once()
            muse_generator.assert_not_awaited()

    def test_flow_vertical_image_validation_accepts_portrait_output(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (900, 1600), (20, 30, 40)).save(image_buffer, format="PNG")
        response = SimpleNamespace(
            status=200,
            body=AsyncMock(return_value=image_buffer.getvalue()),
        )
        page = SimpleNamespace(
            url="https://flow.google.com/project/test",
            request=SimpleNamespace(get=AsyncMock(return_value=response)),
        )
        worker = GoogleFlowWorker(page)
        worker._expected_image_aspect_ratio = "9:16"
        self.assertTrue(
            asyncio.run(worker._validate_image_url("https://flow.google.com/asb/test"))
        )

    def test_flow_video_intent_submits_only_after_confirmed_missing(self):
        async def exercise(submit_if_missing):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                start = Path(temp_dir) / "start.png"
                end = Path(temp_dir) / "end.png"
                start.write_bytes(b"start")
                end.write_bytes(b"end")
                page = SimpleNamespace(url="https://flow.google.com/project/test")
                worker = GoogleFlowWorker(page)
                worker._ensure_project_canvas = AsyncMock()
                worker._find_video_for_prompt = AsyncMock(
                    return_value={"state": "missing", "source": ""}
                )
                worker._ensure_agent_session_ready = AsyncMock(
                    side_effect=RuntimeError("submission path reached")
                )
                with self.assertRaises((RuntimeError, FlowVideoPending)) as raised:
                    await worker.generate_scene_video(
                        prompt="cinematic motion",
                        avoid_prompt="text",
                        start_frame_path=start,
                        end_frame_path=end,
                        request_key="short-video-request",
                        reconcile_only=True,
                        submit_if_prompt_missing=submit_if_missing,
                    )
                return worker, raised.exception

        blocked_worker, blocked_error = asyncio.run(exercise(False))
        self.assertIsInstance(blocked_error, FlowVideoPending)
        blocked_worker._ensure_agent_session_ready.assert_not_awaited()

        resumed_worker, resumed_error = asyncio.run(exercise(True))
        self.assertIsInstance(resumed_error, RuntimeError)
        resumed_worker._ensure_agent_session_ready.assert_awaited_once()


class MuseVerticalAdapterTests(unittest.TestCase):
    def test_short_requests_send_real_vertical_parameters_and_defaults_stay_wide(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            start = root / "start.png"
            end = root / "end.png"
            start.write_bytes(b"image")
            end.write_bytes(b"image")
            provider = muse_provider.MuseStudioApiProvider()
            provider._submit_and_wait = AsyncMock(return_value={"path": str(root / "out.mp4")})
            asyncio.run(
                provider.create_video(
                    "vertical prompt",
                    start,
                    end,
                    root / "out.mp4",
                    aspect_ratio="9:16",
                    resolution="1080x1920",
                    duration_seconds=7,
                )
            )
            payload = provider._submit_and_wait.call_args.kwargs["payload"]
            self.assertEqual(payload["aspect_ratio"], "9:16")
            self.assertEqual(payload["resolution"], "1080x1920")
            self.assertEqual(payload["duration"], 7)

            provider._submit_and_wait.reset_mock()
            asyncio.run(provider.create_video("wide prompt", start, end, root / "wide.mp4"))
            default_payload = provider._submit_and_wait.call_args.kwargs["payload"]
            self.assertEqual(default_payload["aspect_ratio"], "16:9")
            self.assertEqual(default_payload["resolution"], "1920x1080")


class ShortVideoMockE2ETests(ShortVideoStoreAndApiTests):
    def test_mock_pipeline_runs_from_poem_to_review_and_final(self):
        snapshot = profile_snapshot()
        item_id = "22222222-2222-4222-8222-222222222222"
        item, job, _created = db.create_short_video_item_with_job(
            item_id=item_id,
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Cảnh khuya",
            source_text="tiếng suối trong như tiếng hát xa\ntrăng lồng cổ thụ bóng lồng hoa",
            manifest=snapshot,
            job_id="short-video-e2e-generate",
            payload={
                "short_video_id": item_id,
                "project_key": "bac-ho-ngam-tho",
                "action": "generate",
                "checkpoint": {"version": 1, "stage": "queued"},
            },
            voice_snapshot=snapshot["resolved_voice_snapshot"],
        )

        class FakeOmniVoice:
            def health(self, *, test_connection=False):
                return {"ok": True}

            def download_audio(self, _task_id, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"mock-wav")
                return destination

        class FakeMuse:
            def __init__(self, **_kwargs):
                pass

            @contextlib.asynccontextmanager
            async def open_session(self):
                yield self

            async def reconcile(self, **_kwargs):
                return None

            async def create_image(self, _prompt, _references, destination, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id=f"image-{destination.stem}",
                        endpoint=f"/v1/images/tasks/{destination.stem}",
                        submitted_at="2026-10-29T00:00:00+00:00",
                    )
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (108, 192), (70, 90, 110)).save(destination)
                return {"path": str(destination)}

            async def create_video(self, _prompt, _start, _end, destination, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id=f"video-{destination.stem}",
                        endpoint=f"/v1/videos/{destination.stem}",
                        submitted_at="2026-10-29T00:00:00+00:00",
                    )
                )
                destination.write_bytes(b"mock-video")
                return {"path": str(destination)}

        def fake_planner(**kwargs):
            scenes = []
            for window in kwargs["windows"]:
                index = int(window["index"])
                scenes.append(
                    {
                        "index": index,
                        "subject": "khung cảnh thiên nhiên tĩnh lặng",
                        "action": f"ánh trăng lan nhẹ trên cảnh vật số {index}",
                        "setting": "rừng núi Việt Nam về đêm",
                        "era": "thời kháng chiến",
                        "visual_anchor": f"vầng trăng và dòng suối cảnh {index}",
                        "safe_visual_anchor": f"mặt nước phản chiếu ánh trăng cảnh {index}",
                        "must_show": ["vầng trăng", "dòng suối"],
                        "composition": "vertical layered landscape composition",
                        "lighting": "soft moonlight through old trees",
                        "color": "indigo ink and warm paper tones",
                        "narrative_role": "continuation",
                    }
                )
            return {
                "semantic_plan": {
                    "story_context": "Một đêm trăng yên tĩnh giữa núi rừng Việt Nam.",
                    "scenes": scenes,
                },
                "planner_source": "chatgpt_playwright_mock",
                "chat_url": "",
            }

        def fake_normalize(_source, destination, *, job_id):
            del job_id
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"normalized-wav")
            return destination

        def fake_render(*, destination, width, height, fps, **_kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"mock-mp4")
            return {
                "path": str(destination),
                "qc": {
                    "duration_seconds": 12,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "has_audio": True,
                    "has_subtitle_stream": False,
                    "text_overlay_enabled": False,
                },
            }

        with (
            patch.object(short_video_pipeline.tts, "get_provider", return_value=FakeOmniVoice()),
            patch.object(
                short_video_pipeline.tts,
                "submit_tts_task",
                return_value={"id": "omnivoice:one-task"},
            ) as submit_tts,
            patch.object(
                short_video_pipeline.tts,
                "get_tts_task",
                return_value={"id": "omnivoice:one-task", "status": "completed"},
            ),
            patch.object(short_video_pipeline.tts, "retry_tts_task"),
            patch.object(short_video_pipeline.short_video_renderer, "normalize_audio", side_effect=fake_normalize),
            patch.object(short_video_pipeline.short_video_renderer, "probe_duration", return_value=12.0),
            patch.object(
                short_video_pipeline,
                "_transcribe_audio",
                return_value=[
                    {"start": 0.0, "end": 6.0, "text": "tiếng suối trong như tiếng hát xa"},
                    {"start": 6.0, "end": 12.0, "text": "trăng lồng cổ thụ bóng lồng hoa"},
                ],
            ),
            patch.object(short_video_pipeline.chatgpt_runtime, "try_start_operation", return_value=True),
            patch.object(short_video_pipeline.chatgpt_runtime, "finish_operation"),
            patch.object(short_video_pipeline, "generate_muse_scene_plan_only", side_effect=fake_planner),
            patch.object(short_video_pipeline, "MuseStudioApiProvider", FakeMuse),
            patch.object(
                short_video_pipeline.short_video_renderer,
                "probe",
                return_value={
                    "duration_seconds": 7,
                    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
                },
            ),
            patch.object(short_video_pipeline.short_video_renderer, "render", side_effect=fake_render),
        ):
            short_video_pipeline.execute(job)
            submit_tts.assert_called_once()
            submitted_text = submit_tts.call_args.args[0]
            self.assertIn("Cảnh khuya", submitted_text)
            self.assertIn("tiếng suối", submitted_text)

            draft_item = db.get_short_video_item(item_id)
            self.assertEqual(draft_item["status"], "awaiting_review")
            self.assertEqual(db.get_system_job(job["id"])["status"], "completed")

            final_item, final_job = db.create_short_video_job(
                item_id=item_id,
                job_id="short-video-e2e-final",
                title="final: Cảnh khuya",
                payload={
                    "short_video_id": item_id,
                    "project_key": "bac-ho-ngam-tho",
                    "action": "final_render",
                    "checkpoint": {"version": 1, "stage": "final_render"},
                },
                voice_snapshot=snapshot["resolved_voice_snapshot"],
                stage="final_render",
            )
            self.assertEqual(final_item["status"], "queued")
            short_video_pipeline.execute(final_job)

        completed = db.get_short_video_item(item_id)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["completed_at"])
        final_state = db.get_system_job("short-video-e2e-final")
        self.assertEqual(final_state["status"], "completed")


if __name__ == "__main__":
    unittest.main()
