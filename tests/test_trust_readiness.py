import asyncio
import datetime
import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import Response

from nexus_engine.services import database as db
from nexus_engine.services import security_logging
from nexus_engine.services import trust_builder_actions as actions
from nexus_engine.services import trust_builder_router as router
from nexus_engine.services import trust_builder_scheduler as scheduler
from nexus_engine.services import trust_builder_service as service


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

    def test_comment_readiness_fails_closed_without_unique_prompt_mapping(self):
        plan = self._plan(daily_comment_target=1)
        with patch.object(
            service, "get_gpm_profile_detail", return_value={
                "name": "Dedicated test profile",
                "os": "Windows",
                "browser_type": "chromium",
                "browser_version": "1",
            }
        ), patch.object(
            service,
            "inspect_profile_browser_readiness",
            return_value={"busy": False, "requires_user_action": False, "state": "closed"},
        ), patch.object(
            service.trust_builder_comments,
            "prompt_versions_for_channel",
            return_value=[],
        ):
            result = service.run_passive_readiness_check(plan["id"])

        checks = {item["id"]: item for item in result["channel"]["checks"]}
        self.assertEqual(checks["comment_prompt_mapping"]["status"], "fail")
        self.assertTrue(checks["comment_prompt_mapping"]["required"])
        self.assertEqual(checks["comment_chatgpt_project"]["status"], "fail")
        self.assertIn("Prompt riêng cho Trust Builder", result["blockers"])

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

    def test_daily_work_state_requires_both_search_and_watch_quotas(self):
        plan = {
            "id": 1,
            "warmup_phase": "phase_1_consumer",
            "daily_search_target": 3,
            "daily_watch_target": 5,
        }
        state = service.get_daily_work_state(
            plan,
            {"search_count": 3, "watch_count": 3},
        )
        self.assertFalse(state["daily_complete"])
        self.assertFalse(state["needs_search"])
        self.assertTrue(state["needs_watch"])

    def test_daily_work_state_bounds_failed_engagement_attempts(self):
        plan = {
            "id": 1,
            "warmup_phase": "phase_2_engage",
            "publication_timezone": "UTC",
            "daily_search_target": 1,
            "daily_watch_target": 1,
            "daily_like_target": 0,
            "daily_comment_target": 1,
            "daily_subscribe_target": 0,
        }
        with patch.object(
            service,
            "_daily_engagement_attempts",
            return_value={"like": 0, "comment": 3, "subscribe": 0},
        ):
            state = service.get_daily_work_state(
                plan,
                {"search_count": 1, "watch_count": 1, "comment_count": 0},
            )
        self.assertTrue(state["daily_complete"])
        self.assertTrue(state["exhausted"]["comment"])
        self.assertFalse(state["engagement_pending"])

    def test_scheduler_keeps_phase_two_due_when_engagement_is_pending(self):
        now = datetime.datetime(2026, 10, 9, 20, 0, tzinfo=datetime.timezone.utc)
        plan = {
            "id": 8,
            "status": "active",
            "warmup_phase": "phase_2_engage",
            "publication_timezone": "UTC",
            "next_run_at": "",
            "daily_search_target": 1,
            "daily_watch_target": 1,
            "daily_like_target": 0,
            "daily_comment_target": 1,
            "daily_subscribe_target": 0,
        }
        enqueue = unittest.mock.Mock()
        with patch.object(scheduler, "_utc_now", return_value=now), patch.object(
            db, "list_channel_trust_plans", return_value=[plan]
        ), patch.object(
            db,
            "get_trust_daily_activity_stats",
            return_value={"search_count": 1, "watch_count": 1, "like_count": 0, "comment_count": 0, "subscribe_count": 0},
        ), patch.object(db, "list_trust_activity_logs", return_value=[]), patch.object(
            scheduler, "enqueue_trust_builder_session", enqueue
        ), patch.object(scheduler, "_drain_job_queue", new=AsyncMock()):
            asyncio.run(scheduler._evaluate_active_plans())

        enqueue.assert_called_once_with(8, source="scheduled", run_immediately=False)

    def test_completed_plan_with_missing_engagement_can_be_reopened(self):
        plan = self._plan(
            status="completed",
            warmup_phase="phase_3_ready",
            daily_comment_target=1,
            daily_subscribe_target=1,
        )
        with patch.object(router, "validate_plan_browser_configuration", return_value="gpm-profile-test"):
            reopened = router.start_plan(plan["id"])["plan"]

        self.assertEqual(reopened["status"], "active")
        self.assertEqual(reopened["warmup_phase"], "phase_2_engage")

    def test_failed_engagement_is_persisted_without_increasing_success_stats(self):
        plan = self._plan(
            status="paused",
            warmup_phase="phase_2_engage",
            daily_watch_target=1,
            daily_search_target=1,
            daily_like_target=0,
            daily_comment_target=1,
            daily_subscribe_target=1,
            min_watch_minutes=1,
        )
        context = unittest.mock.Mock()
        context.new_page = AsyncMock(return_value=object())
        with patch.object(service, "validate_plan_browser_configuration", return_value="gpm-profile-test"), patch.object(
            service, "is_profile_browser_busy", return_value=False
        ), patch.object(service, "channel_browser_session", return_value=_BrowserSession(context)), patch.object(
            service, "cleanup_owned_page", new=AsyncMock()
        ), patch.object(
            service,
            "action_search_and_pick_video",
            new=AsyncMock(return_value={"url": "https://youtube.com/watch?v=safe2", "title": "Safe video", "channel": "Safe channel"}),
        ), patch.object(
            service,
            "action_watch_video",
            new=AsyncMock(return_value={"watched_seconds": 60.0, "total_duration": 100.0, "retention_percentage": 60.0, "youtube_video_id": "safe2"}),
        ), patch.object(
            service,
            "_prepare_grounded_comment",
            new=AsyncMock(return_value={
                "status": "ready",
                "comment_text": "A grounded comment about this specific video topic.",
                "youtube_video_id": "safe2",
                "linked_video_id": None,
                "chat_reused": True,
                "context_source": "stored_video",
                "prompt_version": "test",
                "quality_score": 1.0,
                "quality_checks": {"evidence_exact": True},
                "context_chars": 500,
            }),
        ), patch.object(
            service,
            "action_get_current_youtube_video_id",
            new=AsyncMock(return_value="safe2"),
        ), patch.object(service.safety, "is_safe_for_interaction", return_value=(True, "")), patch.object(
            service,
            "action_comment_video",
            new=AsyncMock(return_value={"status": "failed", "reason_code": "comment_submit_disabled", "message": "Nút gửi chưa bật."}),
        ), patch.object(
            service,
            "action_subscribe_channel",
            new=AsyncMock(return_value={"status": "performed", "reason_code": "subscribe_confirmed", "message": ""}),
        ):
            result = asyncio.run(service.run_warmup_session(plan["id"]))

        self.assertTrue(result["success"])
        self.assertTrue(result["partial_success"])
        logs = db.list_trust_activity_logs(plan["id"], limit=20)
        failed_comment = next(item for item in logs if item["activity_type"] == "comment_attempt")
        self.assertEqual(failed_comment["detail_json"]["reason_code"], "comment_submit_disabled")
        stats = db.get_trust_activity_stats(plan["id"])
        self.assertEqual(stats["comment_count"], 0)
        self.assertEqual(stats["subscribe_count"], 1)

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
            new=AsyncMock(return_value={"watched_seconds": 60.0, "total_duration": 100.0, "retention_percentage": 60.0, "youtube_video_id": "safe1"}),
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

    def test_contextual_comment_pipeline_preserves_safe_action_order(self):
        plan = self._plan(
            status="paused",
            warmup_phase="phase_2_engage",
            daily_watch_target=1,
            daily_search_target=1,
            daily_like_target=0,
            daily_comment_target=1,
            daily_subscribe_target=0,
            min_watch_minutes=1,
        )
        context = unittest.mock.Mock()
        context.new_page = AsyncMock(return_value=object())
        events = []

        async def pick_video(*_args, **_kwargs):
            events.append("select_video")
            return {
                "url": "https://youtube.com/watch?v=ordered1",
                "title": "Battery storage",
                "channel": "Safe channel",
            }

        async def watch_video(*_args, **_kwargs):
            events.append("watch_and_verify_id")
            return {
                "watched_seconds": 60.0,
                "total_duration": 100.0,
                "retention_percentage": 60.0,
                "youtube_video_id": "ordered1",
                "last_position_seconds": 60.0,
                "observed_comments": [],
            }

        async def collect_context(*_args, **_kwargs):
            events.append("collect_context")
            return {
                "youtube_video_id": "ordered1",
                "title": "Battery storage",
                "description": "solar battery storage reduces evening grid demand",
                "chapters": [],
                "transcript_segments": [],
                "top_comments": [],
            }

        def resolve_chat(*_args, **_kwargs):
            events.append("resolve_chat")
            return {
                "status": "existing_chat",
                "youtube_video_id": "ordered1",
                "chat_url": "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
                "chat_reused": True,
                "linked_video_id": None,
                "prompt_version": "test-prompt",
                "context_source": "publication",
            }

        def generate_comment(*_args, **_kwargs):
            events.append("generate_comment")
            return (
                '{"comment":"The point that solar battery storage reduces evening grid demand '
                'makes this tradeoff much clearer.","language":"en",'
                '"evidence":"solar battery storage reduces evening grid demand",'
                '"anchor_terms":["solar battery storage"]}'
            )

        original_validate = service.trust_builder_comments.validate_comment_draft

        def validate_comment(*args, **kwargs):
            events.append("validate_comment")
            return original_validate(*args, **kwargs)

        async def recheck_video_id(*_args, **_kwargs):
            events.append("recheck_id")
            return "ordered1"

        async def post_comment(*_args, **_kwargs):
            events.append("post_comment")
            return {
                "status": "performed",
                "reason_code": "comment_confirmed",
                "message": "",
            }

        with patch.object(service, "validate_plan_browser_configuration", return_value="gpm-profile-test"), patch.object(
            service, "is_profile_browser_busy", return_value=False
        ), patch.object(service, "channel_browser_session", return_value=_BrowserSession(context)), patch.object(
            service, "cleanup_owned_page", new=AsyncMock()
        ), patch.object(service, "action_search_and_pick_video", new=pick_video), patch.object(
            service, "action_watch_video", new=watch_video
        ), patch.object(service.safety, "is_safe_for_interaction", return_value=(True, "")), patch.object(
            service, "action_collect_video_context", new=collect_context
        ), patch.object(
            service, "resolve_market_language_config", return_value={"lang_code": "en"}
        ), patch.object(
            service.trust_builder_comments, "resolve_video_chat", side_effect=resolve_chat
        ), patch.object(
            service.trust_builder_comments, "validate_comment_draft", side_effect=validate_comment
        ), patch.object(
            service.chatgpt_runtime, "try_start_operation", return_value=True
        ), patch.object(
            service.chatgpt_runtime, "finish_operation"
        ), patch.object(
            service.chatgpt_worker, "generate_trust_builder_comment", side_effect=generate_comment
        ), patch.object(
            service, "action_get_current_youtube_video_id", new=recheck_video_id
        ), patch.object(service, "action_comment_video", new=post_comment):
            result = asyncio.run(service.run_warmup_session(plan["id"]))

        self.assertTrue(result["success"])
        self.assertTrue(result["details"]["commented"])
        self.assertEqual(
            events,
            [
                "select_video",
                "watch_and_verify_id",
                "resolve_chat",
                "collect_context",
                "generate_comment",
                "validate_comment",
                "recheck_id",
                "post_comment",
            ],
        )

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
