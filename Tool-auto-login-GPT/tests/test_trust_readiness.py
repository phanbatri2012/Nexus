import asyncio
import datetime
import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Response

from auto_yt.services import database as db
from auto_yt.services import trust_builder_router as router
from auto_yt.services import trust_builder_service as service
from auto_yt.services import security_logging
from auto_yt.services import trust_builder_scheduler as scheduler
from auto_yt.services import trust_builder_actions as actions


class TestTrustReadiness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.temp_dir.name) / "readiness.db"
        self.db_patch = patch.object(db, "DB_PATH", self.temp_db_path)
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _channel(self, *, proxy: str = "http://user:password@127.0.0.1:8080") -> dict:
        return db.upsert_youtube_channel(
            channel_id="UC_READINESS_TEST",
            title="Readiness test",
            gpm_profile_id="gpm-profile-test",
            gpm_profile_name="Dedicated test profile",
            gpm_proxy_info=proxy,
        )

    def test_proxy_is_encrypted_at_rest_and_filtered_from_public_shape(self):
        secret_proxy = "http://user:password@127.0.0.1:8080"
        channel = self._channel(proxy=secret_proxy)

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

        public = db.public_youtube_channel(channel)
        self.assertTrue(public["gpm_proxy_configured"])
        self.assertNotIn("gpm_proxy_info", public)
        self.assertNotIn("gpm_proxy_info_encrypted", public)
        self.assertNotIn("access_token_encrypted", public)
        self.assertNotIn("refresh_token_encrypted", public)

    def test_proxy_credentials_are_redacted_from_diagnostics(self):
        redacted_url = security_logging.redact_sensitive(
            "proxy failed: http://user:password@127.0.0.1:8080"
        )
        redacted_field = security_logging.redact_sensitive(
            "proxy_info=socks5://alice:secret@proxy.example:1080"
        )
        for output in (redacted_url, redacted_field):
            self.assertNotIn("password", output)
            self.assertNotIn("alice", output)
            self.assertNotIn("secret", output)

    def test_public_plan_response_is_no_store_and_contains_no_proxy(self):
        channel = self._channel()
        plan = db.create_channel_trust_plan(channel_db_id=channel["id"])
        response = Response()
        with patch.object(db, "list_channel_trust_plans", return_value=[plan]):
            payload = router.list_plans(response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("gpm_proxy_info", payload["plans"][0])

    def test_legacy_automation_entry_points_are_gone(self):
        for endpoint in (router.start_plan, router.resume_plan):
            with self.assertRaises(HTTPException) as context:
                endpoint(123)
            self.assertEqual(context.exception.status_code, 410)

        result = asyncio.run(service.run_warmup_session(123))
        self.assertFalse(result["success"])
        self.assertTrue(result["disabled"])
        with self.assertRaises(ValueError):
            scheduler.enqueue_trust_builder_session(123, source="manual", run_immediately=True)

    def test_first_reminder_waits_for_local_configured_time(self):
        plan = {
            "publication_timezone": "Asia/Ho_Chi_Minh",
            "reminder_time_local": "09:00",
            "next_run_at": "",
        }
        before = datetime.datetime(2026, 10, 9, 1, 30, tzinfo=datetime.timezone.utc)
        after = datetime.datetime(2026, 10, 9, 2, 30, tzinfo=datetime.timezone.utc)
        self.assertFalse(scheduler._is_plan_due(plan, before))
        self.assertTrue(scheduler._is_plan_due(plan, after))

    def test_feature_parser_checks_negative_terms_before_enabled_terms(self):
        source = inspect.getsource(actions.action_audit_feature_eligibility)
        negative_check = source.index("disabledTerms.some(term => text.includes(term))")
        positive_check = source.index("return enabledTerms.some(term => text.includes(term))")
        self.assertLess(negative_check, positive_check)

    def test_passive_readiness_fails_closed_without_proxy_or_identity(self):
        channel = self._channel(proxy="")
        plan = db.create_channel_trust_plan(channel_db_id=channel["id"])
        profile_detail = {
            "id": "gpm-profile-test",
            "name": "Dedicated test profile",
            "os": "win",
            "browser_type": "chromium",
            "browser_version": "1",
        }
        runtime = {
            "state": "closed",
            "busy": False,
            "requires_user_action": False,
        }
        parsed = {"type": "gpm", "id": "gpm-profile-test", "profile_name": "Dedicated test profile"}
        with (
            patch.object(service, "parse_profile_target", return_value=parsed),
            patch.object(service, "get_gpm_profile_detail", return_value=profile_detail),
            patch.object(service, "inspect_profile_browser_readiness", return_value=runtime),
        ):
            result = service.run_passive_readiness_check(plan["id"])

        profile_checks = {item["id"]: item for item in result["profile"]["checks"]}
        channel_checks = {item["id"]: item for item in result["channel"]["checks"]}
        self.assertEqual(result["state"], "needs_attention")
        self.assertEqual(profile_checks["proxy_isolation"]["status"], "fail")
        self.assertEqual(channel_checks["studio_identity"]["status"], "unknown")

    def test_interactive_check_does_not_audit_the_wrong_studio_channel(self):
        channel = self._channel()
        plan = db.create_channel_trust_plan(channel_db_id=channel["id"])
        identity = {
            "logged_in": True,
            "identity_verified": False,
            "message": "Profile đang đăng nhập một kênh khác với kênh được gán.",
        }
        branding_audit = AsyncMock()
        feature_audit = AsyncMock()
        with (
            patch.object(service, "validate_plan_browser_configuration", return_value="gpm-profile-test"),
            patch.object(service, "verify_youtube_login", new=AsyncMock(return_value=identity)),
            patch.object(service, "audit_channel_branding_for_plan", new=branding_audit),
            patch.object(service, "audit_feature_eligibility_for_plan", new=feature_audit),
            patch.object(service, "run_passive_readiness_check", return_value={"state": "needs_attention"}),
        ):
            result = asyncio.run(service.run_interactive_readiness_check(plan["id"]))

        self.assertEqual(result["state"], "needs_attention")
        branding_audit.assert_not_awaited()
        feature_audit.assert_not_awaited()

    def test_guided_session_is_idempotent_and_human_operated(self):
        channel = self._channel()
        plan = db.create_channel_trust_plan(
            channel_db_id=channel["id"],
            niche_keywords=["documentary"],
            approved_sources=["https://example.com/reference"],
        )
        first = service.create_guided_readiness_session(plan["id"])
        second = service.create_guided_readiness_session(plan["id"])

        self.assertEqual(first["id"], second["id"])
        self.assertIn("tự thao tác", first["agenda"]["notice"].lower())
        self.assertEqual(db.list_active_system_jobs(service.TRUST_JOB_TYPE), [])

    def test_migration_preserves_legacy_score_and_cancels_jobs(self):
        channel = self._channel()
        plan = db.create_channel_trust_plan(channel_db_id=channel["id"], status="active")
        db.update_channel_trust_plan(plan["id"], trust_score_estimated=42)
        job = db.create_system_job(
            job_id="legacy-trust-job",
            job_type=service.TRUST_JOB_TYPE,
            title="Legacy",
            payload={"plan_id": plan["id"]},
        )
        conn = sqlite3.connect(str(self.temp_db_path))
        try:
            conn.execute("DELETE FROM schema_migrations WHERE name = ?", (db.TRUST_READINESS_V1_MIGRATION,))
            db._migrate_trust_builder_guided_mode(conn)
            conn.commit()
        finally:
            conn.close()

        migrated = db.get_channel_trust_plan(plan["id"])
        migrated_job = db.get_system_job(job["id"])
        self.assertEqual(migrated["legacy_trust_score_estimated"], 42)
        self.assertEqual(migrated["trust_score_estimated"], 0)
        self.assertEqual(migrated["status"], "paused")
        self.assertTrue(migrated["requires_review"])
        self.assertEqual(migrated_job["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
