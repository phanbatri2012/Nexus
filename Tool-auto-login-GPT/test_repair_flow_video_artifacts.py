import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from auto_yt.services import database


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "repair_flow_video_artifacts.py"
SPEC = importlib.util.spec_from_file_location("repair_flow_video_artifacts", SCRIPT_PATH)
repair_flow_video_artifacts = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(repair_flow_video_artifacts)


class RepairFlowVideoArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(database, "DB_PATH", self.root / "database.db")
        self.database_patch.start()
        self.path_patches = [
            patch.object(repair_flow_video_artifacts, "DATA_DIR", self.root / "data"),
            patch.object(
                repair_flow_video_artifacts,
                "DIAGNOSTICS_DIR",
                self.root / "data" / "logs" / "diagnostics",
            ),
            patch.object(
                repair_flow_video_artifacts,
                "VIDEO_ARTIFACTS_DIR",
                self.root / "data" / "video_artifacts",
            ),
        ]
        for path_patch in self.path_patches:
            path_patch.start()
        database.init_db()

    def tearDown(self):
        for path_patch in reversed(self.path_patches):
            path_patch.stop()
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def test_apply_quarantines_videos_preserves_images_and_requests_new_project(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=repair-flow-test",
            "Repair Flow test",
            "Transcript",
            "Script",
        )
        artifact_root = repair_flow_video_artifacts.VIDEO_ARTIFACTS_DIR
        artifact_root.mkdir(parents=True, exist_ok=True)
        image_path = artifact_root / "scene.png"
        Image.new("RGB", (1376, 768), color=(10, 20, 30)).save(image_path)
        database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="scene:0",
            path=str(image_path),
            content_hash="image-hash",
            status="completed",
            mime_type="image/png",
        )
        for index in range(2):
            clip_path = artifact_root / f"clip-{index}.mp4"
            clip_path.write_bytes(b"duplicate-video" * 128)
            database.upsert_video_artifact(
                video_id=video_id,
                artifact_type=f"scene_video:{index}",
                path=str(clip_path),
                content_hash=f"clip-plan-{index}",
                status="completed",
                mime_type="video/mp4",
            )
        final_path = artifact_root / "final.mp4"
        final_path.write_bytes(b"rendered-video" * 128)
        database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(final_path),
            content_hash="final-hash",
            status="ready",
            mime_type="video/mp4",
        )

        manifest = repair_flow_video_artifacts.build_manifest(video_id)
        self.assertEqual(manifest["scene_video_count"], 2)
        self.assertEqual(len(manifest["duplicate_groups"]), 1)
        result = repair_flow_video_artifacts.apply_manifest(manifest)

        self.assertTrue(image_path.is_file())
        self.assertEqual(
            len(database.list_video_artifacts(video_id, "scene:")),
            1,
        )
        self.assertEqual(database.list_video_artifacts(video_id, "scene_video:"), [])
        self.assertIsNone(database.get_latest_video_artifact(video_id, "final_mp4"))
        self.assertEqual(len(result["moved_files"]), 3)
        self.assertTrue(Path(result["quarantine_dir"]).is_dir())
        self.assertTrue(database.get_flow_project(video_id)["reset_requested"])

    def test_apply_refuses_published_video(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=repair-published",
            "Published",
            "Transcript",
            "Script",
        )
        database.toggle_published(video_id, 1)
        manifest = repair_flow_video_artifacts.build_manifest(video_id)

        with self.assertRaisesRegex(RuntimeError, "đã có trạng thái"):
            repair_flow_video_artifacts.apply_manifest(manifest)

    def test_apply_rechecks_active_jobs_after_manifest(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=repair-race",
            "Repair race",
            "Transcript",
            "Script",
        )
        manifest = repair_flow_video_artifacts.build_manifest(video_id)
        job = database.create_system_job(
            job_id="repair-race-job",
            job_type="flow_media_generation",
            title="Flow repair race",
            payload={"video_id": video_id},
            video_id=video_id,
        )
        database.update_system_job(job["id"], status="running")

        with self.assertRaisesRegex(RuntimeError, "vừa có job hoạt động"):
            repair_flow_video_artifacts.apply_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
