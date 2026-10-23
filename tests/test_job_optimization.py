import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus_engine import main
from nexus_engine.services import database as db


class JobCenterOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            db,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        db.init_db()

    def tearDown(self):
        self.database_patch.stop()
        try:
            self.temp_directory.cleanup()
        except Exception:
            pass

    def test_summary_counts_and_fast_active_filter(self):
        # Create an active job
        db.create_system_job(
            job_id="job-active-1",
            job_type="video_generation",
            title="Video Đang Xử Lý",
            payload={"url": "https://youtube.com/watch?v=111"},
        )
        # Create a done job
        db.create_system_job(
            job_id="job-done-1",
            job_type="comment_sync",
            title="Sync Comment",
            payload={},
        )
        db.update_system_job("job-done-1", status="done")

        # Create an error job
        db.create_system_job(
            job_id="job-error-1",
            job_type="competitor_scan",
            title="Scan Radar Lỗi",
            payload={},
        )
        db.update_system_job("job-error-1", status="error")

        # Verify summary counts in SQL
        counts = db.get_system_jobs_summary_counts()
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["active"], 1)
        self.assertEqual(counts["error"], 1)

        # Test list_jobs status='active' returns only the active job
        active_res = main.list_jobs(status="active", limit=50)
        self.assertEqual(active_res["filtered_total"], 1)
        self.assertEqual(len(active_res["items"]), 1)
        self.assertEqual(active_res["items"][0]["id"], "job-active-1")
        self.assertEqual(active_res["counts"]["total"], 3)
        self.assertEqual(active_res["counts"]["active"], 1)
        self.assertEqual(active_res["counts"]["error"], 1)

        # Test list_jobs status='error' returns only the error job
        error_res = main.list_jobs(status="error", limit=50)
        self.assertEqual(error_res["filtered_total"], 1)
        self.assertEqual(len(error_res["items"]), 1)
        self.assertEqual(error_res["items"][0]["id"], "job-error-1")

        # Test list_jobs status='all' returns all 3 jobs
        all_res = main.list_jobs(status="all", limit=50)
        self.assertEqual(all_res["filtered_total"], 3)
        self.assertEqual(len(all_res["items"]), 3)
        all_ids = {item["id"] for item in all_res["items"]}
        self.assertEqual(all_ids, {"job-active-1", "job-done-1", "job-error-1"})

    def test_paged_items_hydration(self):
        # Create video_generation job with pipeline
        db.create_system_job(
            job_id="job-pipe-1",
            job_type="video_generation",
            title="Video Pipeline Test",
            payload={"pipeline": {"audio": True, "title": True}},
        )
        res = main.list_jobs(status="all", limit=10)
        item = next(it for it in res["items"] if it["id"] == "job-pipe-1")
        self.assertIsNotNone(item["pipeline"])
        self.assertTrue(item["pipeline"].get("audio"))
        self.assertTrue(item["pipeline"].get("title"))


if __name__ == "__main__":
    unittest.main()
