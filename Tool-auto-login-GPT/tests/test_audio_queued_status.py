import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from auto_yt.services import database as db
from auto_yt.main import _audio_job_center_item


class AudioQueuedStatusTests(unittest.TestCase):
    def test_audio_job_center_item_queued(self):
        task = {
            "video_id": 999,
            "title": "Test Video",
            "status": "queued",
            "tts_provider_id": "omnivoice",
            "voice_revision": 1,
            "created_at": "2026-10-09T08:00:00Z",
            "updated_at": "2026-10-09T08:00:00Z",
        }
        item = _audio_job_center_item(task)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["progress"], "Đang chờ OmniVoice")

    def test_audio_job_center_item_processing(self):
        task = {
            "video_id": 999,
            "title": "Test Video",
            "status": "processing",
            "tts_provider_id": "omnivoice",
            "voice_revision": 1,
            "created_at": "2026-10-09T08:00:00Z",
            "updated_at": "2026-10-09T08:00:00Z",
        }
        item = _audio_job_center_item(task)
        self.assertEqual(item["status"], "running")
        self.assertEqual(item["progress"], "OmniVoice đang tạo audio")

    def test_get_active_audio_tasks_includes_queued(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_db = Path(tmp.name)
        try:
            with patch.object(db, "DB_PATH", tmp_db):
                conn = sqlite3.connect(str(tmp_db))
                conn.execute(
                    "CREATE TABLE videos (id INTEGER PRIMARY KEY, video_status TEXT)"
                )
                conn.execute(
                    "CREATE TABLE audio_tasks (video_id INTEGER, status TEXT, request_hash TEXT)"
                )
                conn.execute("INSERT INTO videos VALUES (1, 'active')")
                conn.execute("INSERT INTO videos VALUES (2, 'active')")
                conn.execute("INSERT INTO videos VALUES (3, 'active')")
                conn.execute("INSERT INTO audio_tasks VALUES (1, 'pending', 'hash1')")
                conn.execute("INSERT INTO audio_tasks VALUES (2, 'processing', 'hash2')")
                conn.execute("INSERT INTO audio_tasks VALUES (3, 'queued', 'hash3')")
                conn.commit()
                conn.close()

                active = db.get_active_audio_tasks()
                statuses = {t["status"] for t in active}
                self.assertEqual(statuses, {"pending", "processing", "queued"})
        finally:
            tmp_db.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
