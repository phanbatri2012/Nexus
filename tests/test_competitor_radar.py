import datetime as dt
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from nexus_engine import main
from nexus_engine.services import competitor_radar
from nexus_engine.services import database as db


class TestCompetitorRadarPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "radar.db"
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
                ("UC_TARGET_RADAR_0000000001", "Target", now, now),
            )
            self.target_channel_id = int(cursor.lastrowid)
        self.competitor = db.upsert_competitor_channel(
            channel_id="UC_COMPETITOR_000000001",
            canonical_url="https://www.youtube.com/channel/UC_COMPETITOR_000000001",
            title="Competitor",
        )

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _rule(self, prompt_version: str) -> dict:
        return db.create_competitor_watch_rule(
            competitor_channel_id=self.competitor["id"],
            prompt_version=prompt_version,
            target_youtube_channel_id=self.target_channel_id,
            status="enabled",
            publication_mode="schedule",
            guest_voice_pool="kenh-suc-khoe",
        )

    def test_migration_creates_all_radar_casting_and_qc_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            rule_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(competitor_watch_rules)").fetchall()
            }
            source_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(competitor_source_videos)").fetchall()
            }
            assignment_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(video_cast_assignments)").fetchall()
            }
        self.assertTrue(
            {
                "competitor_channels",
                "competitor_watch_rules",
                "competitor_source_videos",
                "competitor_dispatches",
                "video_cast_assignments",
                "speaker_voice_registry",
                "video_quality_reviews",
            }.issubset(tables)
        )
        self.assertIn("guest_voice_pool", rule_columns)
        self.assertTrue(
            {
                "scan_strategy",
                "last_scan_source",
                "rss_failure_count",
                "rss_retry_after",
                "fallback_failure_count",
                "last_fallback_at",
            }.issubset(rule_columns)
        )
        self.assertTrue(
            {
                "content_type",
                "duration_seconds",
                "eligibility_status",
                "exclusion_reason",
                "discovery_source",
                "published_at_confidence",
                "metadata_checked_at",
                "voice_profile_status",
                "voice_profile_json",
                "voice_profile_version",
                "voice_profile_updated_at",
            }.issubset(source_columns)
        )
        self.assertTrue(
            {"casting_version", "manifest_hash", "approval_status"}.issubset(
                assignment_columns
            )
        )

    def test_rule_persists_guest_voice_pool(self):
        rule = self._rule("prompt-with-pool")
        self.assertEqual(rule["guest_voice_pool"], "kenh-suc-khoe")
        updated = db.update_competitor_watch_rule(
            rule["id"], guest_voice_pool="kenh-tai-chinh"
        )
        self.assertEqual(updated["guest_voice_pool"], "kenh-tai-chinh")

    def test_manual_casting_approval_persists_overrides_and_resumes_same_job(self):
        rule = self._rule("prompt-casting-v2")
        source, _created = db.upsert_competitor_source_video(
            competitor_channel_id=self.competitor["id"],
            youtube_video_id="castsource1",
            url="https://www.youtube.com/watch?v=castsource1",
            title="Source",
            published_at="2026-10-20T00:00:00+00:00",
            is_baseline=False,
            content_type="long_form",
            duration_seconds=600,
            eligibility_status="eligible",
        )
        dispatch, _reserved = db.reserve_competitor_dispatch(
            source_video_id=source["id"],
            watch_rule_id=rule["id"],
            prompt_version=rule["prompt_version"],
        )
        snapshot = {
            "automation": {
                "source": "competitor_radar",
                "competitor_source_video_id": source["id"],
            },
            "casting_manifest": {
                "speakers": [
                    {"speaker_key": "MC", "name": "MC", "gender": "male", "age_group": "adult", "language": "vi", "styles": [], "confidence": 1},
                    {"speaker_key": "KHACH_1", "name": "Khách", "gender": "any", "age_group": "any", "language": "vi", "styles": [], "confidence": .3},
                ]
            },
        }
        video_id = db.save_video(
            url=source["url"],
            title="Generated",
            transcript="Transcript",
            generated_script="[MC]: Chào.\n[KHACH_1]: Xin chào.",
            prompt_version=rule["prompt_version"],
            voice_id="mc-fixed",
            production_snapshot_json=json.dumps(snapshot),
        )
        job = db.create_system_job(
            job_id="video-casting-approval",
            job_type="video_generation",
            title="Casting",
            payload={"automation": snapshot["automation"]},
            prompt_version=rule["prompt_version"],
            voice_id="mc-fixed",
            status="paused",
            video_id=video_id,
        )
        db.update_system_job(
            job["id"],
            result_json={"attention_required": "auto_cast", "video_id": video_id},
            error="Cần duyệt",
        )
        db.update_competitor_dispatch(
            dispatch["id"],
            system_job_id=job["id"],
            video_id=video_id,
            status="needs_attention",
        )
        snapshot["cast_voice_overrides"] = {
            "MC": {"voice_id": "mc-fixed"},
            "KHACH_1": {"voice_id": "guest-manual"},
        }
        assignments = [
            {"role_key": "MC", "speaker_key": "MC", "speaker_name": "MC", "voice_id": "mc-fixed", "source": "fixed_mc", "casting_version": 2, "manifest_hash": "hash", "approval_status": "auto"},
            {"role_key": "KHACH_1", "speaker_key": "KHACH_1", "speaker_name": "Khách", "voice_id": "guest-manual", "source": "manual_override", "casting_version": 2, "manifest_hash": "hash", "approval_status": "approved"},
        ]

        resumed = db.approve_radar_casting(
            video_id=video_id,
            system_job_id=job["id"],
            assignments=assignments,
            production_snapshot=snapshot,
        )

        self.assertEqual(resumed["id"], job["id"])
        self.assertEqual(resumed["status"], "queued")
        stored = db.list_video_cast_assignments(video_id)
        self.assertEqual(stored[1]["approval_status"], "approved")
        saved_snapshot = json.loads(db.get_video(video_id)["production_snapshot_json"])
        self.assertEqual(
            saved_snapshot["cast_voice_overrides"]["KHACH_1"]["voice_id"],
            "guest-manual",
        )
        self.assertEqual(
            db.get_competitor_dispatch_for_video(video_id)["status"],
            "queued",
        )

    def test_recast_safety_keeps_video_after_any_tts_submission(self):
        video_id = db.save_video(
            url="https://www.youtube.com/watch?v=ttssource01",
            title="TTS started",
            transcript="Transcript",
            generated_script="[MC]: Chào.\n[KHACH_1]: Xin chào.",
        )
        now = db.utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO audio_tasks (
                    video_id, request_hash, task_id, status, segments_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    "request-hash",
                    "",
                    "processing",
                    json.dumps([{"task_id": "provider-task-1", "status": "processing"}]),
                    now,
                    now,
                ),
            )
        safe, reason = main._radar_recast_safety(video_id)
        self.assertFalse(safe)
        self.assertEqual(reason, "tts_started")

    def test_shared_competitor_dispatches_once_for_each_prompt(self):
        first_rule = self._rule("prompt-alpha")
        second_rule = self._rule("prompt-beta")
        for rule in (first_rule, second_rule):
            db.update_competitor_watch_rule(rule["id"], baseline_complete=True)
        discovery = {
            "scan_source": "proxy_html",
            "rss_error": "",
            "items": [
                {
                    "youtube_video_id": "sharedvid01",
                    "url": "https://www.youtube.com/watch?v=sharedvid01",
                    "title": "Shared long-form upload",
                    "published_at": "2026-10-17T08:00:00+00:00",
                    "updated_at": "2026-10-17T08:00:00+00:00",
                    "published_at_confidence": "exact",
                    "content_type": "long_form",
                    "duration_seconds": 600,
                    "eligibility_status": "eligible",
                    "exclusion_reason": "",
                    "discovery_source": "proxy_html",
                    "metadata_checked_at": "2026-10-17T09:00:00+00:00",
                }
            ],
        }

        def execute(rule: dict, suffix: str) -> None:
            job = db.create_system_job(
                job_id=f"scan-{rule['id']}-{suffix}",
                job_type="competitor_scan",
                title="Shared competitor scan",
                payload={"rule_id": rule["id"], "automatic": True},
                prompt_version=str(rule["prompt_version"]),
            )
            main._execute_competitor_scan_job(job)

        with patch.object(
            main.db,
            "get_youtube_channel",
            return_value={
                "id": self.target_channel_id,
                "gpm_profile_id": "profile-1",
                "gpm_proxy_info": "http://127.0.0.1:8080",
            },
        ), patch.object(main, "_competitor_rule_preflight"), patch.object(
            main.competitor_radar,
            "discover_long_form_videos",
            return_value=discovery,
        ), patch.object(
            main.competitor_radar,
            "next_scan_at",
            return_value="2026-10-17T09:20:00+00:00",
        ), patch.object(
            main,
            "_enqueue_video_request",
            side_effect=lambda request, **_kwargs: {
                "status": "queued",
                "job_id": f"video-{request.prompt_version}",
            },
        ) as enqueue:
            execute(first_rule, "initial")
            execute(second_rule, "initial")
            execute(first_rule, "repeat")
            execute(second_rule, "repeat")

        self.assertEqual(enqueue.call_count, 2)
        dispatches = db.list_competitor_dispatches()
        self.assertEqual(len(dispatches), 2)
        self.assertEqual(
            {item["prompt_version"] for item in dispatches},
            {"prompt-alpha", "prompt-beta"},
        )
        self.assertEqual(
            {item["system_job_id"] for item in dispatches},
            {"video-prompt-alpha", "video-prompt-beta"},
        )

    def test_source_video_upsert_is_idempotent(self):
        payload = {
            "competitor_channel_id": self.competitor["id"],
            "youtube_video_id": "baseline-video",
            "url": "https://www.youtube.com/watch?v=baseline-video",
            "title": "Baseline",
            "published_at": "2026-10-16T08:00:00+00:00",
            "is_baseline": True,
        }
        first, first_created = db.upsert_competitor_source_video(**payload)
        second, second_created = db.upsert_competitor_source_video(**payload)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])

    def test_failed_dispatch_is_not_revived_by_later_scans(self):
        rule = self._rule("prompt-retry")
        db.update_competitor_watch_rule(
            rule["id"],
            baseline_complete=True,
            last_success_at="2026-10-17T09:00:00+00:00",
        )
        source, _created = db.upsert_competitor_source_video(
            competitor_channel_id=self.competitor["id"],
            youtube_video_id="retry-video",
            url="https://www.youtube.com/watch?v=retry-video",
            title="Retry upload",
            published_at="2026-10-17T08:00:00+00:00",
            is_baseline=False,
        )
        dispatch, _reserved = db.reserve_competitor_dispatch(
            source_video_id=source["id"],
            watch_rule_id=rule["id"],
            prompt_version="prompt-retry",
        )
        db.update_competitor_dispatch(dispatch["id"], status="error", error="temporary")
        job = db.create_system_job(
            job_id="scan-retry",
            job_type="competitor_scan",
            title="Retry scan",
            payload={"rule_id": rule["id"], "automatic": True},
            prompt_version="prompt-retry",
        )
        discovery = {
            "scan_source": "rss",
            "rss_error": "",
            "items": [
                {
                    "youtube_video_id": "retry-video",
                    "url": "https://www.youtube.com/watch?v=retry-video",
                    "title": "Retry upload",
                    "published_at": "2026-10-17T08:00:00+00:00",
                    "updated_at": "2026-10-17T08:00:00+00:00",
                    "published_at_confidence": "exact",
                    "content_type": "long_form",
                    "duration_seconds": 600,
                    "eligibility_status": "eligible",
                    "exclusion_reason": "",
                    "discovery_source": "rss",
                    "metadata_checked_at": "2026-10-17T09:00:00+00:00",
                }
            ],
        }
        with patch.object(
            main.db,
            "get_youtube_channel",
            return_value={
                "id": self.target_channel_id,
                "gpm_profile_id": "profile-1",
                "gpm_proxy_info": "http://127.0.0.1:8080",
            },
        ), patch.object(
            main, "_competitor_rule_preflight"
        ), patch.object(
            main.competitor_radar, "discover_long_form_videos", return_value=discovery
        ), patch.object(
            main.competitor_radar,
            "next_scan_at",
            return_value="2026-10-17T09:20:00+00:00",
        ), patch.object(
            main,
            "_enqueue_video_request",
            return_value={"status": "queued", "job_id": "video-retry"},
        ) as enqueue:
            main._execute_competitor_scan_job(job)

        enqueue.assert_not_called()
        terminal = db.get_competitor_dispatch(source["id"], "prompt-retry")
        self.assertEqual(terminal["status"], "error")
        self.assertEqual(terminal["system_job_id"], "")

    def test_baseline_counts_only_eligible_long_form_and_never_dispatches(self):
        rule = self._rule("prompt-baseline-long-form")
        job = db.create_system_job(
            job_id="scan-baseline-long-form",
            job_type="competitor_scan",
            title="Baseline scan",
            payload={"rule_id": rule["id"], "automatic": True},
            prompt_version="prompt-baseline-long-form",
        )
        common = {
            "published_at": "2026-10-17T08:00:00+00:00",
            "updated_at": "",
            "published_at_confidence": "exact",
            "discovery_source": "proxy_html",
            "metadata_checked_at": "2026-10-17T09:00:00+00:00",
        }
        discovery = {
            "scan_source": "proxy_html",
            "rss_error": "RSS unavailable",
            "items": [
                {
                    **common,
                    "youtube_video_id": "longvideo01",
                    "url": "https://www.youtube.com/watch?v=longvideo01",
                    "title": "Long video",
                    "content_type": "long_form",
                    "duration_seconds": 600,
                    "eligibility_status": "eligible",
                    "exclusion_reason": "",
                },
                {
                    **common,
                    "youtube_video_id": "shortvideo1",
                    "url": "https://www.youtube.com/watch?v=shortvideo1",
                    "title": "Short video",
                    "content_type": "short",
                    "duration_seconds": 120,
                    "eligibility_status": "excluded",
                    "exclusion_reason": "duration_below_180_seconds",
                },
                {
                    **common,
                    "youtube_video_id": "pendingvid1",
                    "url": "https://www.youtube.com/watch?v=pendingvid1",
                    "title": "Pending video",
                    "content_type": "unknown",
                    "duration_seconds": 0,
                    "eligibility_status": "metadata_pending",
                    "exclusion_reason": "metadata_unavailable",
                },
            ],
        }
        with patch.object(
            main.db,
            "get_youtube_channel",
            return_value={
                "id": self.target_channel_id,
                "gpm_profile_id": "profile-1",
                "gpm_proxy_info": "http://127.0.0.1:8080",
            },
        ), patch.object(main, "_competitor_rule_preflight"), patch.object(
            main.competitor_radar,
            "discover_long_form_videos",
            return_value=discovery,
        ), patch.object(
            main.competitor_radar,
            "next_scan_at",
            return_value="2026-10-17T09:20:00+00:00",
        ), patch.object(main, "_enqueue_video_request") as enqueue:
            main._execute_competitor_scan_job(job)

        enqueue.assert_not_called()
        updated_rule = db.get_competitor_watch_rule(rule["id"])
        self.assertTrue(updated_rule["baseline_complete"])
        self.assertEqual(updated_rule["baseline_video_count"], 1)
        self.assertEqual(updated_rule["last_scan_source"], "proxy_html")
        sources = db.list_competitor_source_videos(self.competitor["id"])
        self.assertEqual(len(sources), 3)
        self.assertEqual(
            {item["eligibility_status"] for item in sources},
            {"eligible", "excluded", "metadata_pending"},
        )

    def test_deleting_rule_cascades_its_dispatches(self):
        rule = self._rule("prompt-delete")
        source, _created = db.upsert_competitor_source_video(
            competitor_channel_id=self.competitor["id"],
            youtube_video_id="delete-video",
            url="https://www.youtube.com/watch?v=delete-video",
            title="Delete upload",
            published_at="2026-10-17T08:00:00+00:00",
            is_baseline=False,
        )
        db.reserve_competitor_dispatch(
            source_video_id=source["id"],
            watch_rule_id=rule["id"],
            prompt_version="prompt-delete",
        )

        self.assertTrue(db.delete_competitor_watch_rule(rule["id"]))
        self.assertIsNone(db.get_competitor_dispatch(source["id"], "prompt-delete"))


class TestCompetitorRadarScheduling(unittest.TestCase):
    @staticmethod
    def _watch_html(
        *,
        duration: int = 600,
        live: bool = False,
        live_replay: bool = False,
        short: bool = False,
    ) -> bytes:
        player = {
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {
                "videoId": "abcdefghijk",
                "channelId": "UC_COMPETITOR_000000001",
                "title": "Long form candidate",
                "lengthSeconds": str(duration),
                "isLiveContent": live,
                "isShort": short,
            },
            "microformat": {
                "playerMicroformatRenderer": {
                    "publishDate": "2026-10-17T08:00:00+00:00",
                    **({"liveBroadcastDetails": {"startTimestamp": "2026-10-17T08:00:00Z"}} if live_replay else {}),
                }
            },
        }
        return f"<script>var ytInitialPlayerResponse = {json.dumps(player)};</script>".encode()

    def test_long_form_classifier_requires_standard_video_at_least_180_seconds(self):
        eligible = competitor_radar.parse_watch_video_metadata(
            self._watch_html(duration=180),
            video_id="abcdefghijk",
            expected_channel_id="UC_COMPETITOR_000000001",
            discovery_source="proxy_html",
        )
        too_short = competitor_radar.parse_watch_video_metadata(
            self._watch_html(duration=179),
            video_id="abcdefghijk",
            expected_channel_id="UC_COMPETITOR_000000001",
            discovery_source="proxy_html",
        )
        flagged_short = competitor_radar.parse_watch_video_metadata(
            self._watch_html(duration=600, short=True),
            video_id="abcdefghijk",
            expected_channel_id="UC_COMPETITOR_000000001",
            discovery_source="proxy_html",
        )

        self.assertEqual(eligible["eligibility_status"], "eligible")
        self.assertEqual(eligible["content_type"], "long_form")
        self.assertEqual(too_short["eligibility_status"], "excluded")
        self.assertEqual(flagged_short["eligibility_status"], "excluded")

    def test_live_and_live_replay_are_excluded(self):
        live = competitor_radar.parse_watch_video_metadata(
            self._watch_html(live=True),
            video_id="abcdefghijk",
            expected_channel_id="UC_COMPETITOR_000000001",
            discovery_source="proxy_html",
        )
        replay = competitor_radar.parse_watch_video_metadata(
            self._watch_html(live_replay=True),
            video_id="abcdefghijk",
            expected_channel_id="UC_COMPETITOR_000000001",
            discovery_source="proxy_html",
        )
        self.assertEqual(live["content_type"], "live")
        self.assertEqual(replay["content_type"], "live_replay")
        self.assertEqual(live["eligibility_status"], "excluded")
        self.assertEqual(replay["eligibility_status"], "excluded")

    def test_rss_failure_falls_back_to_proxied_videos_page(self):
        channel_html = (
            '<html>UC_COMPETITOR_000000001 '
            '"videoId":"abcdefghijk"</html>'
        ).encode()

        def page_response(_opener, url, **_kwargs):
            return self._watch_html() if "watch?v=" in url else channel_html

        with patch.object(
            competitor_radar,
            "fetch_channel_feed",
            side_effect=competitor_radar.CompetitorSourceUnavailable(
                "rss", "RSS unavailable", status_code=404
            ),
        ), patch.object(
            competitor_radar, "_read_proxied_page", side_effect=page_response
        ):
            result = competitor_radar.discover_long_form_videos(
                "UC_COMPETITOR_000000001",
                "http://127.0.0.1:8080",
                "profile-1",
            )

        self.assertEqual(result["scan_source"], "proxy_html")
        self.assertEqual(result["items"][0]["eligibility_status"], "eligible")
        self.assertIn("RSS unavailable", result["rss_error"])

    def test_empty_proxied_videos_page_falls_back_to_gpm(self):
        gpm_items = [
            {
                "youtube_video_id": "abcdefghijk",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
                "title": "Long video",
                "published_at": "2026-10-17T08:00:00+00:00",
                "updated_at": "",
                "published_at_confidence": "exact",
                "content_type": "long_form",
                "duration_seconds": 600,
                "eligibility_status": "eligible",
                "exclusion_reason": "",
                "discovery_source": "gpm_browser",
                "metadata_checked_at": "2026-10-17T09:00:00+00:00",
            }
        ]
        with patch.object(
            competitor_radar, "fetch_channel_feed", return_value=[]
        ), patch.object(
            competitor_radar,
            "_read_proxied_page",
            return_value=b"<html>UC_COMPETITOR_000000001</html>",
        ), patch.object(
            competitor_radar, "_discover_with_gpm", return_value=gpm_items
        ) as gpm_discovery:
            result = competitor_radar.discover_long_form_videos(
                "UC_COMPETITOR_000000001",
                "http://127.0.0.1:8080",
                "profile-1",
            )

        gpm_discovery.assert_awaited_once()
        self.assertEqual(result["scan_source"], "gpm_browser")
        self.assertEqual(result["items"], gpm_items)
        self.assertIn("không tìm thấy video", result["fallback_error"])

    def test_unverified_gpm_metadata_fails_closed_for_retry(self):
        pending_items = [
            {
                "youtube_video_id": "abcdefghijk",
                "eligibility_status": "metadata_pending",
            }
        ]
        with patch.object(
            competitor_radar, "fetch_channel_feed", return_value=[]
        ), patch.object(
            competitor_radar,
            "_read_proxied_page",
            return_value=b"<html>UC_COMPETITOR_000000001</html>",
        ), patch.object(
            competitor_radar, "_discover_with_gpm", return_value=pending_items
        ):
            with self.assertRaises(
                competitor_radar.CompetitorSourceUnavailable
            ) as caught:
                competitor_radar.discover_long_form_videos(
                    "UC_COMPETITOR_000000001",
                    "http://127.0.0.1:8080",
                    "profile-1",
                )

        self.assertEqual(caught.exception.source, "gpm_browser")
        self.assertIn("tự thử lại", str(caught.exception))

    def test_scan_cadence_for_cold_predicted_and_off_window(self):
        now = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(
            competitor_radar.next_scan_delay_seconds(
                published_times=[now.isoformat()] * 4,
                now=now,
            ),
            5 * 60,
        )
        weekly_samples = [now - dt.timedelta(days=7 * index) for index in range(5)]
        self.assertEqual(
            competitor_radar.next_scan_delay_seconds(
                published_times=weekly_samples,
                now=now,
            ),
            2 * 60,
        )
        off_window_samples = [
            now.replace(hour=3) - dt.timedelta(days=7 * index) for index in range(5)
        ]
        self.assertEqual(
            competitor_radar.next_scan_delay_seconds(
                published_times=off_window_samples,
                now=now,
            ),
            20 * 60,
        )

    def test_manual_window_overrides_learned_schedule(self):
        now = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)
        windows = [{"days": [5], "start": "11:30", "end": "12:30"}]
        self.assertEqual(
            competitor_radar.next_scan_delay_seconds(
                published_times=[],
                now=now,
                window_mode="manual",
                manual_windows=windows,
                timezone_name="UTC",
            ),
            2 * 60,
        )

    def test_manual_window_can_cross_midnight(self):
        tuesday_after_midnight = dt.datetime(
            2026, 10, 20, 0, 30, tzinfo=dt.timezone.utc
        )
        self.assertTrue(
            competitor_radar.is_in_manual_window(
                tuesday_after_midnight,
                [{"days": [0], "start": "23:00", "end": "01:00"}],
                timezone_name="UTC",
            )
        )

    @patch.object(competitor_radar, "create_proxy_opener")
    def test_feed_fetch_fails_closed_before_network_without_proxy(self, opener):
        with self.assertRaises(competitor_radar.CompetitorRadarError):
            competitor_radar.fetch_channel_feed("UC_COMPETITOR_000000001", "")
        opener.assert_not_called()

    @patch.object(competitor_radar.time, "sleep")
    @patch.object(competitor_radar, "create_proxy_opener")
    def test_feed_fetch_retries_transient_404(self, create_opener, sleep):
        class Response:
            headers = {"Content-Type": "text/xml; charset=UTF-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return (
                    '<feed xmlns="http://www.w3.org/2005/Atom" '
                    'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
                    '<entry><yt:videoId>abcdefghijk</yt:videoId>'
                    '<title>Video</title><published>2026-10-17T08:00:00Z</published>'
                    '</entry></feed>'
                ).encode()

        http_error = urllib.error.HTTPError(
            "https://www.youtube.com/feeds/videos.xml",
            404,
            "Not Found",
            {},
            io.BytesIO(b"not found"),
        )
        opener = MagicMock()
        opener.open.side_effect = [http_error, Response()]
        create_opener.return_value = opener

        videos = competitor_radar.fetch_channel_feed(
            "UC_COMPETITOR_000000001",
            "http://127.0.0.1:8080",
            retry_delays=(0,),
        )

        self.assertEqual(videos[0]["youtube_video_id"], "abcdefghijk")
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_feed_parser_keeps_only_baseline_count(self):
        entries = "".join(
            f"""
            <entry>
              <yt:videoId>video-{index}</yt:videoId>
              <title>Video {index}</title>
              <published>2026-10-17T{index % 10:02d}:00:00+00:00</published>
              <link rel="alternate" href="https://www.youtube.com/watch?v=video-{index}" />
            </entry>
            """
            for index in range(18)
        )
        xml = (
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            + entries
            + "</feed>"
        ).encode()
        videos = competitor_radar.parse_channel_feed(xml)
        self.assertEqual(len(videos), competitor_radar.BASELINE_VIDEO_COUNT)


class TestCompetitorRadarStartupContract(unittest.TestCase):
    @staticmethod
    def _casting_voice(voice_id, *, status="active", usage="multi_guest", pool="pool-a"):
        return {
            "id": voice_id,
            "name": voice_id,
            "status": status,
            "casting_profile": {
                "gender": "female",
                "age_group": "adult",
                "languages": ["vi"],
                "roles": ["mc" if usage == "multi_host" else "guest"],
                "styles": [],
                "usage_modes": [usage],
                "voice_pools": [] if usage == "multi_host" else [pool],
            },
        }

    def test_casting_approval_rejects_wrong_pool_inactive_and_duplicate_voice(self):
        manifest = [
            {"speaker_key": "MC", "speaker_name": "MC", "role": "mc", "gender": "male", "age_group": "adult", "language": "vi", "styles": [], "confidence": 1},
            {"speaker_key": "KHACH_1", "speaker_name": "Khách 1", "role": "guest", "gender": "any", "age_group": "any", "language": "vi", "styles": [], "confidence": .3},
            {"speaker_key": "KHACH_2", "speaker_name": "Khách 2", "role": "guest", "gender": "any", "age_group": "any", "language": "vi", "styles": [], "confidence": .3},
        ]
        voices = [
            self._casting_voice("mc", usage="multi_host"),
            self._casting_voice("good-1"),
            self._casting_voice("good-2"),
            self._casting_voice("wrong-pool", pool="pool-b"),
            self._casting_voice("inactive", status="archived"),
        ]
        context = {
            "video": {"id": 42},
            "dispatch": {"id": 9},
            "snapshot": {"automation": {"source": "competitor_radar"}},
            "manifest": manifest,
            "voices": voices,
            "job": {"id": "job-42", "status": "paused", "result": {"attention_required": "auto_cast"}},
            "mc_voice_id": "mc",
            "guest_voice_pool": "pool-a",
        }
        invalid_payloads = (
            [{"speaker_key": "KHACH_1", "voice_id": "wrong-pool"}, {"speaker_key": "KHACH_2", "voice_id": "good-2"}],
            [{"speaker_key": "KHACH_1", "voice_id": "inactive"}, {"speaker_key": "KHACH_2", "voice_id": "good-2"}],
            [{"speaker_key": "KHACH_1", "voice_id": "good-1"}, {"speaker_key": "KHACH_2", "voice_id": "good-1"}],
        )
        for payload in invalid_payloads:
            request = main.CastingApprovalRequest(assignments=payload)
            with self.subTest(payload=payload), patch.object(
                main, "_radar_casting_context", return_value=context
            ), patch.object(
                main.db, "list_video_cast_assignments", return_value=[]
            ), patch.object(
                main.db, "approve_radar_casting"
            ) as approve, self.assertRaises(HTTPException) as error:
                main.approve_competitor_casting(42, request)
            self.assertEqual(error.exception.status_code, 422)
            approve.assert_not_called()

    def test_casting_report_resolves_voice_names_from_full_catalog(self):
        assignments = [
            {"id": 1, "speaker_key": "MC", "voice_id": "voice-active"},
            {"id": 2, "speaker_key": "GUEST_1", "voice_id": "voice-archived"},
            {"id": 3, "speaker_key": "GUEST_2", "voice_id": "voice-removed"},
        ]
        voices = [
            {"id": "voice-active", "name": "MC Minh", "status": "active"},
            {
                "id": "voice-archived",
                "name": "Khách Lan",
                "status": "archived",
            },
        ]
        with patch.object(
            main,
            "_radar_casting_context",
            return_value={
                "voices": voices,
                "manifest": [],
                "dispatch": {"voice_profile": {}},
                "job": {},
                "mc_voice_id": "voice-active",
                "guest_voice_pool": "pool",
            },
        ), patch.object(main.db, "list_video_cast_assignments", return_value=assignments):
            report = main.get_competitor_casting_report(42)

        self.assertEqual(
            [item["voice_name"] for item in report["assignments"]],
            ["MC Minh", "Khách Lan", "Giọng không còn trong danh mục"],
        )

    def test_health_reports_radar_source_quality_without_gating_global_readiness(self):
        main_path = Path(__file__).parents[1] / "src" / "nexus_engine" / "main.py"
        source = main_path.read_text(encoding="utf-8")
        health = source[source.index('@app.get("/health")') : source.index('@app.get("/api/system/maintenance-status")')]
        self.assertIn('"competitor_radar_health": radar_health', health)
        self.assertIn('"long_form_min_seconds": competitor_radar.LONG_FORM_MIN_SECONDS', health)
        self.assertIn('"source_voice_model": source_voice_analyzer.model_status()', health)
        self.assertIn('"degraded" if radar_degraded else "ready"', health)
        self.assertNotIn('and bool(radar_health', health)

    def test_channel_isolation_preflight_fails_closed(self):
        invalid_channels = (
            {"gpm_profile_id": "", "gpm_proxy_info": "http://127.0.0.1:8080"},
            {"gpm_profile_id": "profile-1", "gpm_proxy_info": ""},
            {
                "gpm_profile_id": "local_chrome_Default",
                "gpm_proxy_info": "http://127.0.0.1:8080",
            },
        )
        for channel in invalid_channels:
            with self.subTest(channel=channel), self.assertRaises(HTTPException) as error:
                main._require_radar_channel_isolation(channel)
            self.assertEqual(error.exception.status_code, 409)

    def test_startup_recovers_jobs_but_does_not_enqueue_or_scan_directly(self):
        main_path = Path(__file__).parents[1] / "src" / "nexus_engine" / "main.py"
        source = main_path.read_text(encoding="utf-8")
        startup = source[source.index("def resume_background_jobs") : source.index("@app.on_event(\"shutdown\")")]
        self.assertIn('db.recover_interrupted_system_jobs("competitor_scan")', startup)
        self.assertIn("target=_competitor_radar_scheduler", startup)
        self.assertNotIn("_enqueue_competitor_scan(", startup)
        self.assertNotIn("resolve_competitor_channel(", startup)
        shutdown = source[source.index('def stop_video_queue_wakeup_timer') : source.index('class VideoJobCanceled')]
        self.assertIn("_competitor_radar_stop_event.set()", shutdown)
        self.assertIn("_competitor_radar_thread.join(timeout=5)", shutdown)



    def test_resolve_competitor_channel_prioritizes_canonical_over_featured(self):
        from unittest.mock import AsyncMock, MagicMock
        featured_html = '''
        <html>
          <head>
            <title>Radio Văn Sâm - YouTube</title>
            <link rel="canonical" href="https://www.youtube.com/channel/UCb1ubfu9A8I7bAw9VleSR5g">
            <meta property="og:image" content="https://example.com/thumb.jpg">
          </head>
          <body>
            <script>
              var data = {"items": [{"gridChannelRenderer": {"channelId": "UC4JP20XnIBVlAI5GQM58edg"}}]};
            </script>
          </body>
        </html>
        '''
        mock_page = AsyncMock()
        mock_page.title.return_value = "Radio Văn Sâm - YouTube"
        mock_page.content.return_value = featured_html
        mock_page.url = "https://www.youtube.com/@radio-vansam78"
        mock_locator = MagicMock()
        def get_attr(attr):
            if attr == "href":
                return "https://www.youtube.com/channel/UCb1ubfu9A8I7bAw9VleSR5g"
            if attr == "content":
                return "https://example.com/thumb.jpg"
            return ""
        mock_locator.get_attribute = AsyncMock(side_effect=get_attr)
        mock_page.locator = MagicMock(return_value=mock_locator)

        mock_context = AsyncMock()
        mock_context.pages = [mock_page]
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = (mock_context, None, None)
        mock_session.__aexit__.return_value = None

        with patch("nexus_engine.services.channel_scanner_service.channel_browser_session", return_value=mock_session):
            import asyncio
            result = asyncio.run(
                competitor_radar.resolve_competitor_channel(
                    "https://www.youtube.com/@radio-vansam78",
                    "dummy_profile",
                )
            )
            self.assertEqual(result["channel_id"], "UCb1ubfu9A8I7bAw9VleSR5g")
            self.assertEqual(result["canonical_url"], "https://www.youtube.com/channel/UCb1ubfu9A8I7bAw9VleSR5g")


if __name__ == "__main__":
    unittest.main()
