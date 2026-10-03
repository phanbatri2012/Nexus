import datetime as dt
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt.services import database as db


class TestChannelScheduleReservations(unittest.TestCase):
    def setUp(self):
        # Create an in-memory or temp SQLite database with required schema for isolated testing
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE youtube_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                publication_timezone TEXT DEFAULT 'Asia/Ho_Chi_Minh',
                publication_slots_json TEXT DEFAULT '[]',
                publication_daily_limit INTEGER DEFAULT 2,
                publication_lead_minutes INTEGER DEFAULT 120
            );

            CREATE TABLE youtube_publish_workflows (
                id TEXT PRIMARY KEY,
                video_id INTEGER,
                youtube_channel_id INTEGER,
                system_job_id TEXT,
                status TEXT DEFAULT 'pending',
                stage TEXT DEFAULT '',
                scheduled_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE system_jobs (
                id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE channel_schedule_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_channel_id INTEGER NOT NULL,
                workflow_id TEXT UNIQUE NOT NULL,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE video_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_channel_id INTEGER NOT NULL,
                video_id INTEGER,
                scheduled_at TEXT,
                published_at TEXT,
                processing_status TEXT DEFAULT 'succeeded'
            );

            CREATE TABLE videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT
            );
        """)

        # Seed test channel
        self.conn.execute("""
            INSERT INTO youtube_channels (id, channel_id, title, publication_timezone, publication_slots_json, publication_daily_limit, publication_lead_minutes)
            VALUES (1, 'UC_TEST_CHANNEL', 'Test Channel', 'Asia/Ho_Chi_Minh', '[{"day":0,"time":"11:00"},{"day":0,"time":"18:00"},{"day":1,"time":"11:00"},{"day":1,"time":"18:00"},{"day":2,"time":"11:00"},{"day":2,"time":"18:00"},{"day":3,"time":"11:00"},{"day":3,"time":"18:00"},{"day":4,"time":"11:00"},{"day":4,"time":"18:00"},{"day":5,"time":"11:00"},{"day":5,"time":"18:00"},{"day":6,"time":"11:00"},{"day":6,"time":"18:00"}]', 2, 120)
        """)

    def tearDown(self):
        self.conn.close()

    def test_cleanup_stale_reservation_on_errored_workflow(self):
        now_str = "2026-10-03T10:00:00+00:00"
        # Seed an errored workflow with a reservation
        self.conn.execute("""
            INSERT INTO youtube_publish_workflows (id, video_id, youtube_channel_id, system_job_id, status, created_at, updated_at)
            VALUES ('wf-error-1', 101, 1, 'job-error-1', 'error', ?, ?)
        """, (now_str, now_str))

        self.conn.execute("""
            INSERT INTO system_jobs (id, status, created_at, updated_at)
            VALUES ('job-error-1', 'error', ?, ?)
        """, (now_str, now_str))

        self.conn.execute("""
            INSERT INTO channel_schedule_reservations (youtube_channel_id, workflow_id, scheduled_at, status, created_at, updated_at)
            VALUES (1, 'wf-error-1', '2026-10-04T04:00:00+00:00', 'reserved', ?, ?)
        """, (now_str, now_str))

        # Run cleanup
        cleaned = db.cleanup_stale_channel_schedule_reservations(self.conn, youtube_channel_id=1)
        self.assertGreaterEqual(cleaned, 1)

        row = self.conn.execute("SELECT status FROM channel_schedule_reservations WHERE workflow_id = 'wf-error-1'").fetchone()
        self.assertEqual(row["status"], "canceled")

    def test_cleanup_stale_reservation_on_inactive_expired(self):
        # 3 hours ago
        past_str = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).isoformat()
        self.conn.execute("""
            INSERT INTO youtube_publish_workflows (id, video_id, youtube_channel_id, system_job_id, status, created_at, updated_at)
            VALUES ('wf-stale-2', 102, 1, 'job-stale-2', 'needs_review', ?, ?)
        """, (past_str, past_str))

        self.conn.execute("""
            INSERT INTO channel_schedule_reservations (youtube_channel_id, workflow_id, scheduled_at, status, created_at, updated_at)
            VALUES (1, 'wf-stale-2', '2026-10-04T04:00:00+00:00', 'reserved', ?, ?)
        """, (past_str, past_str))

        cleaned = db.cleanup_stale_channel_schedule_reservations(self.conn, youtube_channel_id=1, max_age_seconds=3600)
        self.assertGreaterEqual(cleaned, 1)

        row = self.conn.execute("SELECT status FROM channel_schedule_reservations WHERE workflow_id = 'wf-stale-2'").fetchone()
        self.assertEqual(row["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
