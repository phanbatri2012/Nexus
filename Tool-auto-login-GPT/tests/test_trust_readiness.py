import asyncio
import datetime
import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import Response

from auto_yt.services import database as db
from auto_yt.services import security_logging
from auto_yt.services import trust_builder_actions as actions
from auto_yt.services import trust_builder_router as router
from auto_yt.services import trust_builder_scheduler as scheduler
from auto_yt.services import trust_builder_service as service


class _BrowserSession:
    def __init__(self, context):
        self.context = context

    async def __aenter__(self):
        return self.context, object(), {}

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class TestTrustBuilderAutomation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.temp_dir.name) / "trust-builder.db"
        self.db_patch = patch.object(db, "DB_PATH", self.temp_db_path)
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _channel(self, *, proxy: str = "http://user:password@127.0.0.1:8080") -> dict:
        return db.upsert_youtube_channel(
            channel_id="UC_TRUST_BUILDER_TEST",
            title="Trust Builder test",
            gpm_profile_id="gpm-profile-test",
            gpm_profile_name="Dedicated test profile",
            gpm_proxy_info=proxy,
        )

    def _plan(self, **changes) -> dict:
        channel = self._channel()
        plan = db.create_channel_trust_plan(channel_db_id=channel["id"], **changes)
        return db.get_channel_trust_plan(plan["id"])

    def test_create_and_update_quota_configuration(self):
        channel = self._channel()
        created = router.create_plan(router.TrustPlanCreateRequest(
            channel_db_id=channel["id"],
            niche_keywords=["tin tức"],
            target_channels=["Kênh mục tiêu"],
            daily_watch_target=15,
            daily_search_target=10,
            daily_like_target=10,
            daily_comment_target=5,
            daily_subscribe_target=5,
            min_watch_minutes=30,
            warmup_phase="phase_2_engage",
        ))["plan"]

        self.assertEqual(created["mode"], "automated")
        self.assertEqual(created["daily_watch_target"], 15)
        self.assertEqual(created["daily_search_target"], 10)
        self.assertEqual(created["min_watch_minutes"], 30)

        updated = router.update_plan(
            created["id"],
            router.TrustPlanUpdateRequest(
                daily_watch_target=1,
                daily_search_target=1,
                daily_like_target=0,
                daily_comment_target=0,
                daily_subscribe_target=0,
                min_watch_minutes=1,
            ),
        )["plan"]
        self.assertEqual(updated["daily_watch_target"], 1)
        self.assertEqual(updated["daily_search_target"], 1)
        self.assertEqual(updated["daily_like_target"], 0)
        self.assertEqual(updated["min_watch_minutes"], 1)

    def test_start_pause_resume_and_manual_session_queue(self):
        plan = self._plan(status="draft", warmup_phase="idle")
        with patch.object(service, "validate_plan_browser_configuration", return_value="gpm-profile-test"), patch.object(
            router, "validate_plan_browser_configuration", return_value="gpm-profile-test"
        ), patch.object(scheduler, "trigger_queue_drain"):
            started = router.start_plan(plan["id"])["plan"]
            self.assertEqual(started["status"], "active")
            self.assertEqual(started["mode"], "automated")
            self.assertEqual(started["warmup_phase"], "phase_1_consumer")

            paused = router.pause_plan(plan["id"])["plan"]
            self.assertEqual(paused["status"], "paused")

            resumed = router.resume_plan(plan["id"])["plan"]
            self.assertEqual(resumed["status"], "active")
            self.assertEqual(resumed["mode"], "automated")

            first = asyncio.run(router.trigger_run_session(plan["id"]))
            second = asyncio.run(router.trigger_run_session(plan["id"]))

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["job"]["status"], "queued")
        self.assertEqual(first["job"]["job_type"], service.TRUST_JOB_TYPE)

    def test_scheduler_claims_only_active_due_plan_with_remaining_quota(self):
        now = datetime.datetime(2026, 10, 9, 20, 0, tzinfo=datetime.timezone.utc)
        plans = [
            {"id": 1, "status": "active", "publication_timezone": "UTC", "next_run_at": "", "daily_search_target": 3, "daily_watch_target": 5},
            {"id": 2, "status": "paused", "publication_timezone": "UTC", "next_run_at": "", "daily_search_target": 3, "daily_watch_target": 5},
            {"id": 3, "status": "active", "publication_timezone": "Asia/Ho_Chi_Minh", "next_run_at": "", "daily_search_target": 3, "daily_watch_target": 5},
            {"id": 4, "status": "active", "publication_timezone": "UTC", "next_run_at": "", "daily_search_target": 3, "daily_watch_target": 5},
        ]

        def daily_stats(plan_id, *_args, **_kwargs):
            if plan_id == 4:
                return {"search_count": 3, "watch_count": 5}
            return {"search_count": 0, "watch_count": 0}

        enqueue = unittest.mock.Mock()
        with patch.object(scheduler, "_utc_now", return_value=now), patch.object(
            db, "list_channel_trust_plans", return_value=plans
        ), patch.object(db, "get_trust_daily_activity_stats", side_effect=daily_stats), patch.object(
            scheduler, "enqueue_trust_builder_session", enqueue
        ), patch.object(scheduler, "_drain_job_queue", new=AsyncMock()):
            asyncio.run(scheduler._evaluate_active_plans())

        enqueue.assert_called_once_with(1, source="scheduled", run_immediately=False)

    def test_safety_shield_blocks_like_comment_and_subscribe(self):
        plan = self._plan(status="paused", warmup_phase="phase_2_engage")
        context = unittest.mock.Mock()
        context.new_page = AsyncMock(return_value=object())
        like = AsyncMock(return_value=actions.ACTION_PERFORMED)
        comment = AsyncMock(return_value=actions.ACTION_PERFORMED)
        subscribe = AsyncMock(return_value=actions.ACTION_PERFORMED)

        with patch.object(service, "validate_plan_browser_configuration", return_value="gpm-profile-test"), patch.object(
            service, "is_profile_browser_busy", return_value=False
        ), patch.object(service, "channel_browser_session", return_value=_BrowserSession(context)), patch.object(
            service, "cleanup_owned_page", new=AsyncMock()
        ), patch.object(
            service,
            "action_search_and_pick_video",
            new=AsyncMock(return_value={"url": "https://youtube.com/watch?v=safe1", "title": "Test video", "channel": "Test channel"}),
        ), patch.object(
            service,
            "action_watch_video",
            new=AsyncMock(return_value={"watched_seconds": 60.0, "total_duration": 100.0, "retention_percentage": 60.0}),
        ), patch.object(service.safety, "is_safe_for_interaction", return_value=(False, "blocked")), patch.object(
            service, "action_like_video", new=like
        ), patch.object(service, "action_comment_video", new=comment), patch.object(
            service, "action_subscribe_channel", new=subscribe
        ):
            result = asyncio.run(service.run_warmup_session(plan["id"]))

        self.assertTrue(result["success"])
        like.assert_not_awaited()
        comment.assert_not_awaited()
        subscribe.assert_not_awaited()
        activity_types = {item["activity_type"] for item in db.list_trust_activity_logs(plan["id"], limit=20)}
        self.assertIn("safety_shield", activity_types)
        self.assertNotIn("like", activity_types)
        self.assertNotIn("comment", activity_types)
        self.assertNotIn("subscribe", activity_types)

    def test_reverse_migration_restores_score_but_keeps_plan_paused_and_jobs_canceled(self):
        plan = self._plan(status="active")
        db.update_channel_trust_plan(
            plan["id"],
            trust_score_estimated=42,
            next_run_at="2026-10-10T01:00:00+00:00",
        )
        db.create_trust_activity_log(plan["id"], "watch", duration_seconds=90)
        job = db.create_system_job(
            job_id="legacy-trust-job",
            job_type=service.TRUST_JOB_TYPE,
            title="Legacy",
            payload={"plan_id": plan["id"]},
        )

        conn = sqlite3.connect(str(self.temp_db_path))
        try:
            conn.execute(
                "DELETE FROM schema_migrations WHERE name IN (?, ?)",
                (db.TRUST_READINESS_V1_MIGRATION, db.TRUST_AUTOMATION_RESTORE_V1_MIGRATION),
            )
            db._migrate_trust_builder_guided_mode(conn)
            db._migrate_trust_builder_automation_restore(conn)
            conn.commit()
        finally:
            conn.close()

        migrated = db.get_channel_trust_plan(plan["id"])
        self.assertEqual(migrated["legacy_trust_score_estimated"], 42)
        self.assertEqual(migrated["trust_score_estimated"], 42)
        self.assertEqual(migrated["mode"], "automated")
        self.assertEqual(migrated["status"], "paused")
        self.assertFalse(migrated["requires_review"])
        self.assertEqual(migrated["next_run_at"], "")
        self.assertEqual(db.get_system_job(job["id"])["status"], "canceled")
        self.assertEqual(db.get_trust_activity_stats(plan["id"])["watch_count"], 1)

    def test_proxy_is_encrypted_filtered_redacted_and_missing_proxy_fails_closed(self):
        secret_proxy = "http://user:password@127.0.0.1:8080"
        channel = self._channel(proxy=secret_proxy)
        plan = db.create_channel_trust_plan(channel_db_id=channel["id"])

        conn = sqlite3.connect(str(self.temp_db_path))
        try:
            plaintext, encrypted = conn.execute(
                "SELECT gpm_proxy_info, gpm_proxy_info_encrypted FROM youtube_channels WHERE id = ?",
                (channel["id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(plaintext, "")
        self.assertTrue(encrypted)
        self.assertNotIn("user", encrypted)
        self.assertNotIn("password", encrypted)

        response = Response()
        payload = router.list_plans(response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("gpm_proxy_info", payload["plans"][0])
        self.assertNotIn("gpm_proxy_info_encrypted", payload["plans"][0])
        self.assertTrue(payload["plans"][0]["gpm_proxy_configured"])

        diagnostic = security_logging.redact_sensitive(
            "proxy failed: http://user:password@127.0.0.1:8080"
        )
        self.assertNotIn("user", diagnostic)
        self.assertNotIn("password", diagnostic)

        db.update_youtube_channel(channel["id"], gpm_proxy_info="")
        without_proxy = db.get_channel_trust_plan(plan["id"])
        with self.assertRaisesRegex(ValueError, "proxy riêng"):
            service.validate_plan_browser_configuration(without_proxy)

    def test_actions_require_ui_confirmation_and_feature_parser_checks_negative_first(self):
        button = unittest.mock.Mock()
        button.scroll_into_view_if_needed = AsyncMock()
        button.click = AsyncMock()
        page = unittest.mock.Mock()
        page.evaluate = AsyncMock(return_value={"found": True, "pressed": False, "selector": "button"})
        page.query_selector = AsyncMock(return_value=button)
        page.wait_for_function = AsyncMock(side_effect=TimeoutError())

        with patch.object(actions.asyncio, "sleep", new=AsyncMock()):
            result = asyncio.run(actions.action_like_video(page))
        self.assertEqual(result, actions.ACTION_FAILED)

        source = inspect.getsource(actions.action_audit_feature_eligibility)
        negative_check = source.index("disabledTerms.some(term => text.includes(term))")
        positive_check = source.index("return enabledTerms.some(term => text.includes(term))")
        self.assertLess(negative_check, positive_check)


if __name__ == "__main__":
    unittest.main()
