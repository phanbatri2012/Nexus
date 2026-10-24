import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from nexus_engine.services import database as db
from nexus_engine.services.google_flow_worker import FlowImagePending
from nexus_engine import main


class TestSystemJobProgressNotification(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.patcher = patch.object(db, "DB_PATH", self.db_path)
        self.patcher.start()

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, video_status TEXT, production_progress TEXT)")
        conn.execute("CREATE TABLE youtube_publish_workflows (id INTEGER PRIMARY KEY, system_job_id TEXT, status TEXT, error TEXT, created_at TEXT)")
        conn.execute(
            """
            CREATE TABLE system_jobs (
                id TEXT PRIMARY KEY, job_type TEXT, status TEXT,
                title TEXT, progress TEXT, payload_json TEXT,
                result_json TEXT, error TEXT, video_id INTEGER,
                prompt_version TEXT, voice_id TEXT, voice_name TEXT,
                tts_provider_id TEXT, voice_revision INTEGER,
                voice_snapshot_json TEXT, attempt INTEGER DEFAULT 0,
                recovery_count INTEGER DEFAULT 0,
                resume_from_step TEXT DEFAULT '', next_retry_at TEXT DEFAULT '',
                cancel_requested INTEGER DEFAULT 0, created_at TEXT,
                updated_at TEXT, started_at TEXT DEFAULT '', finished_at TEXT DEFAULT ''
            )
            """
        )
        conn.commit()
        conn.close()
        self.job_id = "test-job-progress-123"

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_schedule_system_job_recovery_custom_progress(self):
        db.create_system_job(
            job_id=self.job_id,
            job_type="flow_media_generation",
            title="Test Job",
            payload={"video_id": 9999},
        )
        custom_msg = "Đang chờ Google Flow tạo 4 ảnh còn lại (sẽ tự đối soát sau 45s)"
        recovered = db.schedule_system_job_recovery(
            job_id=self.job_id,
            resume_from_step="flow_image_reconcile",
            delay_seconds=45,
            error="",
            progress=custom_msg,
        )
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["progress"], custom_msg)
        self.assertEqual(recovered["status"], "retry_wait")
        self.assertEqual(recovered["resume_from_step"], "flow_image_reconcile")

    def test_schedule_system_job_recovery_human_mapping_fallback(self):
        db.create_system_job(
            job_id=self.job_id,
            job_type="flow_media_generation",
            title="Test Job",
            payload={"video_id": 9999},
        )
        recovered = db.schedule_system_job_recovery(
            job_id=self.job_id,
            resume_from_step="flow_image_reconcile",
            delay_seconds=60,
            error="",
        )
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["progress"], "Đang chờ Google Flow tạo ảnh; sẽ tự tiếp tục")
        self.assertNotIn("Chờ tự phục hồi", recovered["progress"])
        self.assertNotIn("flow_image_reconcile", recovered["progress"])

    def test_handle_production_job_error_flow_image_pending(self):
        job = db.create_system_job(
            job_id=self.job_id,
            job_type="flow_media_generation",
            title="Test Job",
            payload={"video_id": 9999},
        )
        exc = FlowImagePending(
            "Waiting for images",
            pending_scene_indices=[1, 2, 3],
            pending_image_scene_indices=[1, 2, 3],
            retry_delay_seconds=30.0,
        )
        with patch.object(main.db, "update_video_production_state"):
            main._handle_production_job_error(job, exc)

        updated = db.get_system_job(self.job_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "retry_wait")
        self.assertIn("Đang chờ Google Flow tạo 3 ảnh còn lại", updated["progress"])
        self.assertIn("sẽ tự đối soát sau 30s", updated["progress"])
        self.assertNotIn("Chờ tự phục hồi", updated["progress"])


if __name__ == "__main__":
    unittest.main()
