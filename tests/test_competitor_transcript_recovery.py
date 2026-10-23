import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus_engine import main
from nexus_engine.services import database as db


class CompetitorTranscriptRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "radar-transcript.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()
        now = db.utc_now()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO youtube_channels (channel_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                ("UC_TARGET_TRANSCRIPT_01", "Target", now, now),
            )
            self.target_channel_id = int(cursor.lastrowid)
        self.competitor = db.upsert_competitor_channel(
            channel_id="UC_COMPETITOR_TRANSCRIPT_01",
            canonical_url=(
                "https://www.youtube.com/channel/UC_COMPETITOR_TRANSCRIPT_01"
            ),
            title="Competitor",
        )
        self.rule = db.create_competitor_watch_rule(
            competitor_channel_id=self.competitor["id"],
            prompt_version="prompt-transcript",
            target_youtube_channel_id=self.target_channel_id,
            status="enabled",
            publication_mode="schedule",
            guest_voice_pool="safe-pool",
        )
        self.source, _created = db.upsert_competitor_source_video(
            competitor_channel_id=self.competitor["id"],
            youtube_video_id="transcript-source",
            url="https://www.youtube.com/watch?v=transcript-source",
            title="Transcript source",
            published_at="2026-10-19T00:00:00+00:00",
            is_baseline=False,
        )
        self.dispatch, _reserved = db.reserve_competitor_dispatch(
            source_video_id=self.source["id"],
            watch_rule_id=self.rule["id"],
            prompt_version="prompt-transcript",
        )

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _video_job(self, job_id="video-transcript"):
        job = db.create_system_job(
            job_id=job_id,
            job_type="video_generation",
            title="Transcript job",
            payload={
                "automation": {
                    "source": "competitor_radar",
                    "competitor_rule_id": self.rule["id"],
                    "competitor_dispatch_id": self.dispatch["id"],
                    "target_youtube_channel_id": self.target_channel_id,
                }
            },
            prompt_version="prompt-transcript",
        )
        db.update_competitor_dispatch(
            self.dispatch["id"],
            system_job_id=job_id,
            status="queued",
            error="",
        )
        return job

    def test_video_job_terminal_status_is_synchronized_to_dispatch(self):
        self._video_job()
        job = db.update_system_job(
            "video-transcript",
            status="error",
            error="Transcript provider failed",
            finished_at=db.utc_now(),
        )

        main._sync_competitor_dispatch_from_video_job(job)

        dispatch = db.get_competitor_dispatch(
            self.source["id"], "prompt-transcript"
        )
        self.assertEqual(dispatch["status"], "error")
        self.assertEqual(dispatch["error"], "Transcript provider failed")

    def test_reconcile_is_one_way_and_idempotent(self):
        self._video_job()
        db.update_system_job(
            "video-transcript",
            status="error",
            error="Terminal transcript error",
            finished_at=db.utc_now(),
        )

        self.assertEqual(db.reconcile_competitor_dispatches_from_jobs(), 1)
        self.assertEqual(db.reconcile_competitor_dispatches_from_jobs(), 0)
        dispatch = db.get_competitor_dispatch(
            self.source["id"], "prompt-transcript"
        )
        self.assertEqual(dispatch["status"], "error")

    def test_radar_network_context_resolves_proxy_without_persisting_it_in_payload(self):
        payload = {
            "automation": {
                "source": "competitor_radar",
                "target_youtube_channel_id": self.target_channel_id,
            }
        }
        with patch.object(
            main.db,
            "get_youtube_channel",
            return_value={
                "id": self.target_channel_id,
                "gpm_proxy_info": "127.0.0.1:8123",
            },
        ):
            proxy, required = main._video_job_youtube_network_context(payload)

        self.assertEqual(proxy, "127.0.0.1:8123")
        self.assertTrue(required)
        self.assertNotIn("proxy", payload["automation"])

    def test_transcript_recovery_stops_after_bounded_delays(self):
        self._video_job()
        with patch.object(main, "_schedule_video_queue_wakeup"):
            scheduled = [
                main._schedule_automatic_video_recovery(
                    "video-transcript",
                    None,
                    "Temporary transcript block",
                    {"transcript_error_code": "transcript_request_blocked"},
                    resume_from_step="youtube_transcript",
                )
                for _ in range(len(main.TRANSCRIPT_RECOVERY_DELAYS_SECONDS) + 1)
            ]

        self.assertEqual(scheduled, [True, True, False])
        job = db.get_system_job("video-transcript")
        self.assertEqual(
            job["recovery_count"], len(main.TRANSCRIPT_RECOVERY_DELAYS_SECONDS)
        )

    def test_startup_finalizes_exhausted_transcript_recovery(self):
        self._video_job()
        db.update_system_job(
            "video-transcript",
            status="queued",
            resume_from_step="youtube_transcript",
            recovery_count=len(main.TRANSCRIPT_RECOVERY_DELAYS_SECONDS),
            error="Last transient transcript error",
        )

        self.assertEqual(main._finalize_exhausted_transcript_recoveries(), 1)
        self.assertEqual(main._finalize_exhausted_transcript_recoveries(), 0)
        job = db.get_system_job("video-transcript")
        dispatch = db.get_competitor_dispatch(
            self.source["id"], "prompt-transcript"
        )
        self.assertEqual(job["status"], "error")
        self.assertEqual(dispatch["status"], "error")


if __name__ == "__main__":
    unittest.main()
