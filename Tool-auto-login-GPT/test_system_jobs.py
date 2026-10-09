import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from auto_yt import main
from auto_yt.services import database
from auto_yt.services import generation_checkpoint
from auto_yt.services import channel_scanner_service, fb_crossposter_service
from auto_yt.services.chatgpt_runtime import (
    CHATGPT_LOGIN_REQUIRED_MESSAGE,
    ChatGPTAttentionRequiredError,
)


class SystemJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        database.init_db()

    def tearDown(self):
        self.database_patch.stop()
        try:
            self.temp_directory.cleanup()
        except Exception:
            pass

    def create_job(self, job_id: str) -> dict:
        return database.create_system_job(
            job_id=job_id,
            job_type="video_generation",
            title=f"Video {job_id}",
            payload={"url": f"https://youtube.com/watch?v={job_id}"},
            prompt_version="prompt-a",
            voice_id="voice-a",
        )

    def reset_automatic_login_state(self) -> None:
        with main._automatic_login_state_lock:
            main._automatic_login_active = False
            main._automatic_login_job_ids.clear()
            main._automatic_login_last_failure_at = 0.0

    def create_fb_browser_item(self, page_id: str, youtube_id: str) -> int:
        database.save_fb_crossposter_settings(
            {
                "target_fb_page_id": page_id,
                "target_fb_page_name": "Test Facebook Page",
                "target_gpm_profile_id": "test-gpm-profile",
                "upload_mode": "browser",
                "convert_to_vertical": False,
            },
            page_id=page_id,
        )
        database.upsert_fb_crossposter_queue_items(
            [
                {
                    "youtube_id": youtube_id,
                    "youtube_url": f"https://www.youtube.com/watch?v={youtube_id}",
                    "original_title": "Test Facebook video",
                }
            ],
            target_page_id=page_id,
        )
        item = database.get_fb_crossposter_queue(
            target_page_id=page_id,
            page_size=1,
        )["items"][0]
        database.update_fb_crossposter_queue_item(
            int(item["id"]),
            {"scheduled_publish_time": int(main.time.time()) + 3600},
        )
        return int(item["id"])

    def create_fb_job(self, job_id: str, item_id: int) -> dict:
        return database.create_system_job(
            job_id=job_id,
            job_type="fb_crosspost",
            title="Facebook browser upload",
            payload={"item_id": item_id},
        )

    def test_startup_does_not_activate_comment_jobs_that_can_open_gpm(self):
        with (
            patch.object(main.account_store, "load_account"),
            patch.object(main.tts, "migrate_api_key_storage"),
            patch.object(
                main.voice_config,
                "get_provider",
                return_value={"enabled": False},
            ),
            patch.object(
                main.chatgpt_browser_service,
                "start_browser_service",
                return_value={"connected": True},
            ),
            patch.object(
                main.google_flow_browser_service,
                "start_browser_service",
                return_value={"connected": True},
            ),
            patch.object(database, "get_active_audio_tasks", return_value=[]),
            patch.object(database, "recover_interrupted_system_jobs"),
            patch.object(main, "_reconcile_active_comment_draft_job_duplicates"),
            patch.object(database, "pause_queued_attention_jobs"),
            patch.object(main, "_kick_video_queue"),
            patch.object(main, "_kick_comment_queue") as kick_comment,
            patch.object(main, "_kick_flow_media_queue"),
            patch.object(main, "_kick_production_queue"),
            patch.object(main, "_kick_publish_queue"),
            patch.object(main, "_enqueue_due_comment_syncs") as enqueue_comment_syncs,
            patch.object(main, "_cleanup_expired_tts_previews"),
            patch.object(main.threading, "Thread") as thread_class,
        ):
            main.resume_background_jobs()

        kick_comment.assert_not_called()
        enqueue_comment_syncs.assert_not_called()
        thread_names = {
            call.kwargs.get("name")
            for call in thread_class.call_args_list
        }
        self.assertIn("youtube-comment-sync", thread_names)

    def test_maintenance_status_blocks_due_gpm_comment_job(self):
        channel = database.save_youtube_channel(
            channel_id="UC-gpm-maintenance",
            title="GPM maintenance channel",
            gpm_profile_id="gpm-profile-1",
            gpm_profile_name="Profile 1",
            interaction_mode="gpm_browser",
        )
        database.create_system_job(
            job_id="comment-publish-maintenance",
            job_type="comment_publish",
            title="Due GPM comment",
            payload={"channel_id": channel["id"], "comment_ids": ["comment-1"]},
        )

        status = main.get_maintenance_status()

        self.assertFalse(status["safe_to_restart"])
        self.assertEqual(status["summary"]["gpm_browsers"], 1)
        self.assertEqual(
            status["blocking_jobs"][0]["id"],
            "comment-publish-maintenance",
        )

    def test_maintenance_status_ignores_direct_api_comment_job(self):
        channel = database.save_youtube_channel(
            channel_id="UC-direct-maintenance",
            title="Direct API maintenance channel",
            gpm_profile_id="gpm-profile-2",
            interaction_mode="direct_api",
        )
        database.create_system_job(
            job_id="comment-publish-direct",
            job_type="comment_publish",
            title="Direct API comment",
            payload={"channel_id": channel["id"], "comment_ids": ["comment-2"]},
        )

        status = main.get_maintenance_status()

        self.assertTrue(status["safe_to_restart"])
        self.assertEqual(status["summary"]["gpm_browsers"], 0)

    def test_maintenance_status_blocks_active_google_flow_generation(self):
        browser_status = {
            "connected": True,
            "generation_active": True,
            "generation_checked_at": "2026-09-28T01:00:00+00:00",
        }
        with patch.object(
            main.maintenance_guard.google_flow_browser_service,
            "get_browser_service_status",
            return_value=browser_status,
        ):
            status = main.get_maintenance_status()

        self.assertFalse(status["safe_to_restart"])
        self.assertEqual(status["summary"]["google_flow_generations"], 1)
        self.assertEqual(
            status["blocking_jobs"][0]["type"],
            "google_flow_generation",
        )

    def test_claims_video_jobs_in_fifo_order(self):
        self.create_job("first")
        self.create_job("second")

        first = database.claim_next_system_job("video_generation")
        database.update_system_job("first", status="done")
        second = database.claim_next_system_job("video_generation")

        self.assertEqual(first["id"], "first")
        self.assertEqual(second["id"], "second")
        self.assertEqual(first["attempt"], 1)
        self.assertEqual(database.get_system_job_queue_position("second"), None)

    def test_queued_job_can_be_canceled_and_retried(self):
        self.create_job("queued")

        canceled = database.request_cancel_system_job("queued")
        retried = database.retry_system_job("queued")

        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(database.get_system_job_queue_position("queued"), 1)

    def test_queued_job_can_be_paused_and_resumed(self):
        self.create_job("pausable")

        paused = database.pause_system_job("pausable")
        self.assertEqual(paused["status"], "paused")
        self.assertIsNone(database.claim_next_system_job("video_generation"))

        resumed = database.resume_system_job("pausable")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(
            database.claim_next_system_job("video_generation")["id"],
            "pausable",
        )

    def test_fb_cdp_block_pauses_queue_and_system_job(self):
        item_id = self.create_fb_browser_item("page-cdp-pause", "yt-cdp-pause")
        self.create_fb_job("fb-cdp-pause", item_id)
        media_directory = Path(self.temp_directory.name) / "fb-media"
        media_directory.mkdir()
        (media_directory / f"yt-cdp-pause_{item_id}.mp4").write_bytes(b"video")
        (media_directory / f"yt-cdp-pause_{item_id}.jpg").write_bytes(b"x" * 1024)
        blocked = channel_scanner_service.BrowserAutomationBlocked(
            {
                "profile_id": "test-gpm-profile",
                "profile_name": "Test GPM Profile",
            }
        )

        with (
            patch.object(fb_crossposter_service, "TEMP_DOWNLOAD_DIR", media_directory),
            patch.object(
                fb_crossposter_service,
                "is_valid_video_file",
                return_value=True,
            ),
            patch(
                "auto_yt.services.fb_reels_browser_service.schedule_reel_via_gpm",
                new=AsyncMock(side_effect=blocked),
            ),
        ):
            result = fb_crossposter_service.process_queue_item_jit(
                item_id,
                sys_job_id="fb-cdp-pause",
            )
            resumed_result = fb_crossposter_service.resume_fb_crossposter_queue_item(
                item_id
            )

        queue_item = database.get_fb_crossposter_queue_item(item_id)
        system_job = database.get_system_job("fb-cdp-pause")
        self.assertEqual(result["status"], "checkpoint_paused")
        self.assertEqual(resumed_result["status"], "checkpoint_paused")
        self.assertEqual(queue_item["status"], "checkpoint_paused")
        self.assertEqual(queue_item["checkpoint_phase"], "CP3_CDP_READY")
        self.assertEqual(system_job["status"], "paused")
        self.assertEqual(
            system_job["result"]["attention_required"],
            "fb_browser_cdp",
        )
        self.assertEqual(system_job["finished_at"], "")
        matching_jobs = [
            job
            for job in database.list_system_jobs(job_type="fb_crosspost")
            if int((job.get("payload") or {}).get("item_id") or 0) == item_id
        ]
        self.assertEqual([job["id"] for job in matching_jobs], ["fb-cdp-pause"])

    def test_fb_cdp_wait_auto_resumes_only_safe_checkpoint(self):
        item_id = self.create_fb_browser_item("page-cdp-resume", "yt-cdp-resume")
        self.create_fb_job("fb-cdp-resume", item_id)
        blocked = channel_scanner_service.BrowserAutomationBlocked(
            {"profile_id": "test-gpm-profile"}
        )
        fb_crossposter_service.pause_fb_browser_checkpoint(
            item_id,
            blocked,
            sys_job_id="fb-cdp-resume",
        )
        main._browser_waiting_last_check_at = 0.0

        with (
            patch.object(
                main.channel_scanner_service,
                "inspect_profile_browser_readiness",
                return_value={"busy": False, "automation_ready": True},
            ),
            patch.object(
                main.channel_scanner_service,
                "ensure_profile_automation_ready",
                return_value={"automation_ready": True},
            ),
        ):
            main._resume_browser_waiting_publish_jobs()

        resumed = database.get_system_job("fb-cdp-resume")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["error"], "")
        self.assertNotIn("attention_required", resumed["result"])

    def test_legacy_fb_cdp_failure_is_migrated_to_paused(self):
        item_id = self.create_fb_browser_item("page-cdp-legacy", "yt-cdp-legacy")
        self.create_fb_job("fb-cdp-legacy", item_id)
        database.update_fb_checkpoint(
            item_id,
            "CP3_CDP_READY",
            status="checkpoint_paused",
            can_resume=True,
            error_message="Trình duyệt đang mở nhưng không có cổng tự động hóa CDP",
        )
        database.update_system_job(
            "fb-cdp-legacy",
            status="failed",
            error="Trình duyệt đang mở nhưng không có cổng tự động hóa CDP",
            finished_at=database.utc_now(),
        )

        recovered = main._recover_legacy_browser_cdp_blocked_jobs()

        migrated = database.get_system_job("fb-cdp-legacy")
        self.assertEqual(recovered, 1)
        self.assertEqual(migrated["status"], "paused")
        self.assertEqual(
            migrated["result"]["attention_required"],
            "fb_browser_cdp",
        )

    def test_fb_checkpoint_after_cdp_requires_manual_review(self):
        item_id = self.create_fb_browser_item("page-fb-review", "yt-fb-review")
        self.create_fb_job("fb-review", item_id)
        fb_crossposter_service.pause_fb_browser_checkpoint(
            item_id,
            RuntimeError("Facebook composer changed"),
            sys_job_id="fb-review",
            phase="CP5_ASSET_UPLOADED",
        )
        main._browser_waiting_last_check_at = 0.0

        with patch.object(
            main.channel_scanner_service,
            "ensure_profile_automation_ready",
        ) as ensure_ready:
            main._resume_browser_waiting_publish_jobs()

        paused = database.get_system_job("fb-review")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(
            paused["result"]["attention_required"],
            "fb_browser_review",
        )
        ensure_ready.assert_not_called()

    def test_fb_retry_is_owned_only_by_production_queue(self):
        item_id = self.create_fb_browser_item("page-fb-retry", "yt-fb-retry")
        self.create_fb_job("fb-retry", item_id)
        database.update_system_job("fb-retry", status="failed", error="old error")

        with (
            patch.object(main.threading, "Thread") as thread_class,
            patch.object(main, "_kick_publish_queue") as kick_publish,
        ):
            result = main._run_system_job_center_action(
                "fb-retry",
                "retry",
                kick_queues=True,
            )

        self.assertEqual(result["status"], "queued")
        thread_class.assert_not_called()
        kick_publish.assert_called_once_with()

    def test_fb_retry_rejects_duplicate_active_item_job(self):
        item_id = self.create_fb_browser_item("page-fb-dedup", "yt-fb-dedup")
        self.create_fb_job("fb-active", item_id)
        database.update_system_job("fb-active", status="paused")
        self.create_fb_job("fb-duplicate", item_id)
        database.update_system_job("fb-duplicate", status="failed")

        with self.assertRaisesRegex(
            main.JobCenterActionNotAllowedError,
            "đã có một job đang chờ hoặc đang chạy",
        ):
            main._run_system_job_center_action(
                "fb-duplicate",
                "retry",
                kick_queues=False,
            )

        self.assertEqual(database.get_system_job("fb-active")["status"], "paused")
        self.assertEqual(database.get_system_job("fb-duplicate")["status"], "failed")

    def test_error_video_job_resumes_from_checkpoint_without_clearing_it(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=resume-checkpoint",
            "Resume checkpoint",
            "Transcript",
            "Saved partial script",
        )
        self.create_job("resume-checkpoint")
        database.update_system_job(
            "resume-checkpoint",
            video_id=video_id,
            status="error",
            error="slug failed",
            result_json={"preserved": True},
            recovery_count=3,
            finished_at=database.utc_now(),
        )
        checkpoint = {
            "chat_url": "https://chatgpt.com/c/saved-chat",
            "current_step": "slug",
            "pending_prompt": {"step": "slug", "fingerprint": "saved"},
        }

        with (
            patch.object(main, "load_checkpoint", return_value=checkpoint),
            patch.object(main, "clear_checkpoint") as clear_checkpoint,
            patch.object(main, "_kick_video_queue") as kick_video_queue,
        ):
            response = main.resume_job_from_checkpoint("resume-checkpoint")

        resumed = database.get_system_job("resume-checkpoint")
        self.assertTrue(response["success"])
        self.assertEqual(response["job"]["id"], "resume-checkpoint")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["resume_from_step"], "slug")
        self.assertEqual(resumed["recovery_count"], 0)
        self.assertEqual(resumed["result"], {"preserved": True})
        clear_checkpoint.assert_not_called()
        kick_video_queue.assert_called_once_with()

    def test_checkpoint_resume_is_not_available_without_a_saved_chat(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=no-checkpoint",
            "No checkpoint",
            "Transcript",
            "Saved partial script",
        )
        self.create_job("no-checkpoint")
        database.update_system_job(
            "no-checkpoint",
            video_id=video_id,
            status="error",
        )

        with patch.object(main, "load_checkpoint", return_value={}):
            item = main._system_job_center_item(
                database.get_system_job("no-checkpoint"),
                None,
            )
            with self.assertRaisesRegex(main.HTTPException, "checkpoint hợp lệ"):
                main.resume_job_from_checkpoint("no-checkpoint")

        self.assertFalse(item["can_resume_checkpoint"])
        self.assertEqual(database.get_system_job("no-checkpoint")["status"], "error")

    def test_verification_resume_clears_stale_attention_marker(self):
        self.create_job("verification")
        database.update_system_job(
            "verification",
            status="paused",
            error="CAPTCHA",
            result_json={"attention_required": "chatgpt_verification"},
        )

        resumed = database.resume_system_job("verification")

        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["error"], "")
        self.assertEqual(resumed["result"], {})

    def test_expired_login_schedules_one_automatic_login_for_multiple_jobs(self):
        self.reset_automatic_login_state()
        error = ChatGPTAttentionRequiredError(
            f"{CHATGPT_LOGIN_REQUIRED_MESSAGE} Hãy đăng nhập lại."
        )
        thread = unittest.mock.Mock()

        with patch.object(main.threading, "Thread", return_value=thread) as create_thread:
            first = main._schedule_automatic_chatgpt_login("first", error)
            second = main._schedule_automatic_chatgpt_login("second", error)

        self.assertTrue(first)
        self.assertTrue(second)
        create_thread.assert_called_once()
        thread.start.assert_called_once_with()
        self.assertEqual(main._automatic_login_job_ids, {"first", "second"})
        self.reset_automatic_login_state()

    def test_captcha_attention_does_not_start_automatic_login(self):
        self.reset_automatic_login_state()
        error = ChatGPTAttentionRequiredError("ChatGPT yêu cầu CAPTCHA/Cloudflare.")

        with patch.object(main.threading, "Thread") as create_thread:
            scheduled = main._schedule_automatic_chatgpt_login("captcha", error)

        self.assertFalse(scheduled)
        create_thread.assert_not_called()
        self.assertFalse(main._automatic_login_active)

    def test_successful_automatic_login_resumes_only_incident_jobs(self):
        self.reset_automatic_login_state()
        for job_id in ("incident", "older-paused"):
            self.create_job(job_id)
            database.update_system_job(
                job_id,
                status="paused",
                error="Login expired",
                result_json={
                    "attention_required": "chatgpt_verification",
                    "automatic_login": "pending",
                },
            )
        with main._automatic_login_state_lock:
            main._automatic_login_active = True
            main._automatic_login_job_ids.add("incident")

        with (
            patch.object(main, "_run_chatgpt_login_blocking", return_value={"success": True}),
            patch.object(main, "_kick_video_queue") as kick_video,
            patch.object(main, "_kick_comment_queue") as kick_comment,
        ):
            main._run_automatic_chatgpt_login()

        incident = database.get_system_job("incident")
        older = database.get_system_job("older-paused")
        self.assertEqual(incident["status"], "queued")
        self.assertEqual(incident["result"], {})
        self.assertEqual(older["status"], "paused")
        kick_video.assert_called_once_with()
        kick_comment.assert_called_once_with()
        self.assertFalse(main._automatic_login_active)

    def test_failed_automatic_login_keeps_job_paused_and_enforces_cooldown(self):
        self.reset_automatic_login_state()
        self.create_job("failed-login")
        database.update_system_job(
            "failed-login",
            status="paused",
            error="Login expired",
            result_json={
                "attention_required": "chatgpt_verification",
                "automatic_login": "pending",
            },
        )
        with main._automatic_login_state_lock:
            main._automatic_login_active = True
            main._automatic_login_job_ids.add("failed-login")

        with patch.object(
            main,
            "_run_chatgpt_login_blocking",
            side_effect=RuntimeError("Wrong password"),
        ):
            main._run_automatic_chatgpt_login()

        failed = database.get_system_job("failed-login")
        self.assertEqual(failed["status"], "paused")
        self.assertEqual(failed["result"]["automatic_login"], "failed")
        self.assertIn("Auto Login", failed["progress"])
        self.assertGreater(main._automatic_login_last_failure_at, 0)

        error = ChatGPTAttentionRequiredError(CHATGPT_LOGIN_REQUIRED_MESSAGE)
        with patch.object(main.threading, "Thread") as create_thread:
            scheduled = main._schedule_automatic_chatgpt_login("next", error)
        self.assertFalse(scheduled)
        create_thread.assert_not_called()
        self.reset_automatic_login_state()

    def test_run_chatgpt_login_blocking_success(self):
        fake_account = {"email": "user@example.com", "password": "secret_password"}
        fake_result = {"success": True, "cookies": [{"name": "session", "value": "123"}]}
        mock_page = unittest.mock.AsyncMock()
        mock_page.set_default_timeout = unittest.mock.MagicMock()
        mock_context = unittest.mock.AsyncMock()
        mock_context.pages = [mock_page]
        mock_context.new_page.return_value = mock_page

        async def _mock_launch(*args, **kwargs):
            return mock_context

        mock_p = unittest.mock.MagicMock()
        mock_p.chromium.launch_persistent_context = _mock_launch
        mock_playwright_cm = unittest.mock.AsyncMock()
        mock_playwright_cm.__aenter__.return_value = mock_p

        with (
            patch("auto_yt.services.account_store.load_account", return_value=fake_account),
            patch("auto_yt.services.account_store.save_account") as mock_save,
            patch("auto_yt.services.chatgpt_browser_service.stop_browser_service") as mock_stop,
            patch("auto_yt.services.chatgpt_browser_service.start_browser_service") as mock_start,
            patch("playwright.async_api.async_playwright", return_value=mock_playwright_cm),
            patch("auto_yt.services.chatgpt_login.login_gpt_auto", return_value=fake_result) as mock_login,
        ):
            res = main._run_chatgpt_login_blocking()

        self.assertTrue(res.get("success"))
        mock_stop.assert_called_once()
        mock_start.assert_called_once()
        mock_login.assert_called_once_with(fake_account, mock_page)
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args[0][0]["session_cookie"], fake_result["cookies"])

    def test_legacy_queued_verification_job_is_paused_on_startup(self):
        self.create_job("legacy-verification")
        database.update_system_job(
            "legacy-verification",
            result_json={"attention_required": "chatgpt_verification"},
            error="CAPTCHA",
        )

        paused_count = database.pause_queued_attention_jobs()
        job = database.get_system_job("legacy-verification")

        self.assertEqual(paused_count, 1)
        self.assertEqual(job["status"], "paused")
        self.assertIsNone(database.claim_next_system_job("video_generation"))

    def test_interrupted_automatic_login_is_not_left_pending_after_restart(self):
        self.create_job("interrupted-login")
        database.update_system_job(
            "interrupted-login",
            status="paused",
            result_json={
                "attention_required": "chatgpt_verification",
                "automatic_login": "pending",
            },
        )

        database.pause_queued_attention_jobs()

        job = database.get_system_job("interrupted-login")
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["result"]["automatic_login"], "interrupted")
        self.assertIn("gián đoạn", job["progress"])

    def test_running_job_is_requeued_after_restart(self):
        self.create_job("interrupted")
        database.claim_next_system_job("video_generation")

        recovered_count = database.recover_interrupted_system_jobs(
            "video_generation"
        )
        recovered = database.get_system_job("interrupted")

        self.assertEqual(recovered_count, 1)
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["recovery_count"], 1)
        self.assertIn("khôi phục", recovered["progress"].lower())

    def test_delayed_recovery_is_claimed_only_when_due(self):
        self.create_job("recoverable")
        database.claim_next_system_job("video_generation")

        waiting = database.schedule_system_job_recovery(
            "recoverable",
            resume_from_step="body 2/4",
            delay_seconds=3600,
            error="temporary failure",
            result_json={"video_id": 42},
        )

        self.assertEqual(waiting["status"], "retry_wait")
        self.assertEqual(waiting["resume_from_step"], "body 2/4")
        self.assertEqual(waiting["recovery_count"], 1)
        self.assertIsNone(database.claim_next_system_job("video_generation"))

        database.update_system_job("recoverable", next_retry_at=database.utc_now())
        recovered = database.claim_next_system_job("video_generation")

        self.assertEqual(recovered["id"], "recoverable")
        self.assertEqual(recovered["status"], "running")
        self.assertEqual(recovered["attempt"], 2)

    def test_retry_wait_job_can_be_paused_and_resumed(self):
        self.create_job("waiting")
        database.claim_next_system_job("video_generation")
        database.schedule_system_job_recovery(
            "waiting",
            resume_from_step="metadata",
            delay_seconds=3600,
            error="temporary failure",
        )

        paused = database.pause_system_job("waiting")
        resumed = database.resume_system_job("waiting")

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["resume_from_step"], "metadata")

    def test_delayed_recovery_keeps_later_video_jobs_in_fifo_order(self):
        self.create_job("recovering-first")
        database.claim_next_system_job("video_generation")
        database.schedule_system_job_recovery(
            "recovering-first",
            resume_from_step="body 2/4",
            delay_seconds=3600,
            error="temporary failure",
        )
        self.create_job("queued-second")

        self.assertFalse(database.has_claimable_system_jobs("video_generation"))
        self.assertIsNone(database.claim_next_system_job("video_generation"))

        database.update_system_job(
            "recovering-first",
            next_retry_at=database.utc_now(),
        )
        recovered = database.claim_next_system_job("video_generation")

        self.assertEqual(recovered["id"], "recovering-first")

    def test_payload_and_result_are_round_tripped_as_objects(self):
        created = self.create_job("json")
        updated = database.update_system_job(
            "json",
            result_json={"video_id": 42, "success": True},
        )

        self.assertEqual(created["payload"]["url"], "https://youtube.com/watch?v=json")
        self.assertEqual(updated["result"]["video_id"], 42)

    def test_unstarted_video_job_can_be_edited_atomically(self):
        self.create_job("editable")

        updated = database.update_editable_video_job(
            "editable",
            title="https://youtube.com/watch?v=updated",
            payload={
                "url": "https://youtube.com/watch?v=updated",
                "prompt_version": "prompt-b",
                "voice_id": "voice-b",
                "voice_name": "Voice B",
            },
            prompt_version="prompt-b",
            voice_id="voice-b",
        )

        self.assertEqual(updated["status"], "queued")
        self.assertEqual(
            updated["payload"]["url"],
            "https://youtube.com/watch?v=updated",
        )
        self.assertEqual(updated["prompt_version"], "prompt-b")
        self.assertEqual(updated["voice_id"], "voice-b")

    def test_failed_video_job_edit_detaches_but_keeps_saved_draft(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=old",
            "Old title",
            "transcript",
            "partial script",
        )
        self.create_job("failed-edit")
        database.update_system_job(
            "failed-edit",
            status="error",
            video_id=video_id,
            error="old error",
            result_json={"video_id": video_id},
        )

        updated = database.update_editable_video_job(
            "failed-edit",
            title="https://youtube.com/watch?v=new",
            payload={"url": "https://youtube.com/watch?v=new"},
            prompt_version="prompt-b",
            voice_id="voice-b",
        )

        self.assertIsNone(updated["video_id"])
        self.assertEqual(updated["error"], "")
        self.assertEqual(updated["result"], {})
        self.assertIsNotNone(database.get_video(video_id))

    def test_running_video_job_cannot_be_edited_or_deleted(self):
        self.create_job("running")
        database.claim_next_system_job("video_generation")

        with self.assertRaises(ValueError):
            database.update_editable_video_job(
                "running",
                title="updated",
                payload={"url": "updated"},
                prompt_version="prompt-b",
                voice_id="voice-b",
            )
        with self.assertRaises(ValueError):
            database.delete_system_job("running")

    def test_deleting_finished_job_does_not_delete_linked_video(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=kept",
            "Kept title",
            "transcript",
            "script",
        )
        self.create_job("deletable")
        database.update_system_job(
            "deletable",
            status="error",
            video_id=video_id,
        )

        deleted = database.delete_system_job("deletable")

        self.assertEqual(deleted["id"], "deletable")
        self.assertIsNone(database.get_system_job("deletable"))
        self.assertIsNotNone(database.get_video(video_id))

    def test_deleting_video_removes_all_linked_records_atomically(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=delete-all",
            "Delete all",
            "transcript",
            "script",
        )
        self.create_job("linked-finished")
        database.update_system_job(
            "linked-finished",
            status="error",
            video_id=video_id,
        )
        database.upsert_audio_task(
            video_id=video_id,
            request_hash="request-hash",
            task_id="task-id",
            status="failed",
            error="failed",
        )
        database.upsert_audio_review(
            video_id=video_id,
            script_hash="script-hash",
            status="blocked",
            report={"errors": []},
        )

        deleted = database.delete_video_with_dependencies(video_id)

        self.assertEqual(deleted["video"]["id"], video_id)
        self.assertEqual(deleted["system_job_ids"], ["linked-finished"])
        self.assertIsNone(database.get_video(video_id))
        self.assertIsNone(database.get_system_job("linked-finished"))
        self.assertIsNone(database.get_audio_task(video_id))
        self.assertIsNone(database.get_audio_review(video_id))

    def test_deleting_video_is_blocked_while_linked_work_is_active(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=active-delete",
            "Active delete",
            "transcript",
            "script",
        )
        self.create_job("linked-running")
        database.update_system_job(
            "linked-running",
            status="running",
            video_id=video_id,
        )

        with self.assertRaisesRegex(ValueError, "đang chạy"):
            database.delete_video_with_dependencies(video_id)

        self.assertIsNotNone(database.get_video(video_id))
        self.assertIsNotNone(database.get_system_job("linked-running"))

    def test_deleting_video_is_blocked_while_audio_is_active(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=active-audio",
            "Active audio",
            "transcript",
            "script",
        )
        database.upsert_audio_task(
            video_id=video_id,
            request_hash="request-hash",
            task_id="task-id",
            status="processing",
        )

        with self.assertRaisesRegex(ValueError, "audio"):
            database.delete_video_with_dependencies(video_id)

        self.assertIsNotNone(database.get_video(video_id))
        self.assertIsNotNone(database.get_audio_task(video_id))

    def test_reconcile_removes_historical_orphans_but_keeps_unlinked_jobs(self):
        import sqlite3

        connection = sqlite3.connect(str(database.DB_PATH))
        now = database.utc_now()
        connection.execute(
            '''
            INSERT INTO system_jobs (
                id, job_type, status, video_id, created_at, updated_at
            ) VALUES ('orphan-job', 'video_generation', 'error', 999, ?, ?)
            ''',
            (now, now),
        )
        connection.execute(
            '''
            INSERT INTO system_jobs (
                id, job_type, status, video_id, created_at, updated_at
            ) VALUES ('unlinked-job', 'video_generation', 'error', NULL, ?, ?)
            ''',
            (now, now),
        )
        connection.execute(
            '''
            INSERT INTO audio_tasks (
                video_id, request_hash, task_id, status, created_at, updated_at
            ) VALUES (999, 'hash', 'task', 'failed', ?, ?)
            ''',
            (now, now),
        )
        connection.execute(
            '''
            INSERT INTO audio_reviews (
                video_id, script_hash, status, updated_at
            ) VALUES (999, 'hash', 'blocked', ?)
            ''',
            (now,),
        )
        connection.commit()
        connection.close()

        removed = database.remove_orphan_video_dependencies()

        self.assertEqual(removed["system_jobs"], 1)
        self.assertEqual(removed["audio_tasks"], 1)
        self.assertEqual(removed["audio_reviews"], 1)
        self.assertIsNone(database.get_system_job("orphan-job"))
        self.assertIsNotNone(database.get_system_job("unlinked-job"))

    def test_video_delete_endpoint_clears_linked_runtime_and_local_files(self):
        media_root = Path(self.temp_directory.name) / "media"
        thumbnail_dir = media_root / "thumbnails"
        audio_dir = media_root / "audio"
        checkpoint_dir = media_root / "checkpoints"
        thumbnail_dir.mkdir(parents=True)
        audio_dir.mkdir(parents=True)
        thumbnail_path = thumbnail_dir / "thumb_delete.png"
        audio_path = audio_dir / "video_delete.mp3"
        thumbnail_path.write_bytes(b"thumbnail")
        audio_path.write_bytes(b"audio")
        script = (
            "### [BODY]\nscript\n\n"
            "### [THUMBNAIL CÓ CHỮ]\n"
            "[IMAGE_URL:/api/thumbnails/thumb_delete.png]\n\n"
            "### [AUDIO]\nhttp://127.0.0.1:8080/api/audio/video_delete.mp3"
        )
        video_id = database.save_video(
            "https://youtube.com/watch?v=delete-files",
            "Delete files",
            "transcript",
            script,
        )
        self.create_job("linked-runtime")
        database.update_system_job(
            "linked-runtime",
            status="error",
            video_id=video_id,
        )
        main._jobs["linked-runtime"] = {"status": "error"}

        with (
            patch.object(main, "THUMBNAILS_DIR", thumbnail_dir),
            patch.object(main, "AUDIO_DIR", audio_dir),
            patch.object(generation_checkpoint, "CHECKPOINT_DIR", checkpoint_dir),
        ):
            generation_checkpoint.save_checkpoint(
                video_id,
                {"thumb_text": "/api/thumbnails/thumb_delete.png"},
            )
            response = main.delete_video(video_id)

        self.assertTrue(response["success"])
        self.assertEqual(response["deleted_job_ids"], ["linked-runtime"])
        self.assertEqual(response["deleted_media_count"], 2)
        self.assertNotIn("linked-runtime", main._jobs)
        self.assertFalse(thumbnail_path.exists())
        self.assertFalse(audio_path.exists())
        self.assertFalse((checkpoint_dir / f"video_{video_id}.json").exists())

    def test_video_delete_keeps_a_local_file_referenced_by_another_video(self):
        thumbnail_dir = Path(self.temp_directory.name) / "thumbnails"
        thumbnail_dir.mkdir(parents=True)
        shared_path = thumbnail_dir / "shared.png"
        shared_path.write_bytes(b"shared")
        shared_marker = "[IMAGE_URL:/api/thumbnails/shared.png]"
        deleted_video_id = database.save_video(
            "https://youtube.com/watch?v=delete-shared",
            "Delete shared",
            "transcript",
            shared_marker,
        )
        database.save_video(
            "https://youtube.com/watch?v=keep-shared",
            "Keep shared",
            "transcript",
            shared_marker,
        )

        with patch.object(main, "THUMBNAILS_DIR", thumbnail_dir):
            response = main.delete_video(deleted_video_id)

        self.assertEqual(response["deleted_media_count"], 0)
        self.assertTrue(shared_path.exists())

    def test_video_delete_is_blocked_during_direct_chatgpt_operation(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=active-chatgpt",
            "Active ChatGPT",
            "transcript",
            "script",
        )
        with main._chatgpt_state_lock:
            main._chatgpt_video_id = video_id
        try:
            with self.assertRaisesRegex(Exception, "ChatGPT") as raised:
                main.delete_video(video_id)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIsNotNone(database.get_video(video_id))
        finally:
            with main._chatgpt_state_lock:
                main._chatgpt_video_id = None

    def test_checkpoint_cleanup_failure_does_not_hide_successful_delete(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=checkpoint-warning",
            "Checkpoint warning",
            "transcript",
            "script",
        )

        with patch.object(
            main,
            "clear_checkpoint",
            side_effect=OSError("checkpoint is locked"),
        ):
            response = main.delete_video(video_id)

        self.assertTrue(response["success"])
        self.assertIsNone(database.get_video(video_id))
        self.assertEqual(len(response["warnings"]), 1)
        self.assertIn("checkpoint", response["warnings"][0])

    def test_list_jobs_includes_linked_video_search_fields(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=searchable-id",
            "Tieu de goc",
            "transcript",
            "TIÊU ĐỀ VIDEO: Tiêu đề mới",
        )
        self.create_job("search-fields")
        database.update_system_job("search-fields", video_id=video_id)

        job = database.list_system_jobs(job_type="video_generation")[0]

        self.assertEqual(job["video_url"], "https://youtube.com/watch?v=searchable-id")
        self.assertEqual(job["original_title"], "Tieu de goc")
        self.assertEqual(job["generated_title"], "Tiêu đề mới")

        response = main.list_jobs(
            limit=100,
            job_type="video_generation",
            search="tieu de moi",
        )
        self.assertEqual([item["id"] for item in response["items"]], ["search-fields"])

    def test_job_list_filters_before_pagination_and_reports_true_counts(self):
        self.create_job("older-error")
        database.update_system_job("older-error", status="error")
        for index in range(500):
            job_id = f"newer-done-{index}"
            self.create_job(job_id)
            database.update_system_job(job_id, status="done")

        response = main.list_jobs(
            limit=20,
            job_type="video_generation",
            status="error",
        )

        self.assertEqual([item["id"] for item in response["items"]], ["older-error"])
        self.assertEqual(response["counts"]["total"], 501)
        self.assertEqual(response["counts"]["error"], 1)
        self.assertEqual(response["filtered_total"], 1)
        self.assertEqual(response["selectable_total"], 1)
        self.assertEqual(response["action_counts"]["retry"], 1)
        self.assertTrue(response["snapshot_at"])

        search_response = main.list_jobs(
            limit=20,
            job_type="video_generation",
            search="older error",
        )
        self.assertEqual(
            [item["id"] for item in search_response["items"]],
            ["older-error"],
        )

    def test_bulk_retry_is_best_effort_for_mixed_system_jobs(self):
        self.create_job("retry-error")
        database.update_system_job("retry-error", status="error")
        self.create_job("retry-canceled")
        database.update_system_job("retry-canceled", status="canceled")
        self.create_job("retry-incompatible")
        database.create_system_job(
            job_id="publish-error",
            job_type="comment_publish",
            title="Publish error",
            payload={"comment_ids": ["comment-1"]},
        )
        database.update_system_job("publish-error", status="error")

        with (
            patch.object(main, "_kick_video_queue") as kick_video,
            patch.object(main, "_kick_comment_queue") as kick_comment,
        ):
            response = main.bulk_job_action(main.BulkJobActionRequest(
                action="retry",
                mode="explicit",
                job_ids=[
                    "retry-error",
                    "retry-canceled",
                    "retry-incompatible",
                    "publish-error",
                ],
            ))

        self.assertEqual(response["requested"], 4)
        self.assertEqual(response["eligible"], 2)
        self.assertEqual(response["succeeded"], 2)
        self.assertEqual(response["skipped"], 2)
        self.assertEqual(response["failed"], 0)
        self.assertEqual(database.get_system_job("retry-error")["status"], "queued")
        self.assertEqual(
            database.get_system_job("retry-canceled")["status"], "queued"
        )
        self.assertEqual(
            database.get_system_job("retry-incompatible")["status"], "queued"
        )
        self.assertEqual(database.get_system_job("publish-error")["status"], "error")
        kick_video.assert_called_once_with()
        kick_comment.assert_not_called()

    def test_bulk_all_matching_honors_snapshot_filters_and_exclusions(self):
        matching_ids = [f"matching-error-{index}" for index in range(205)]
        for job_id in matching_ids:
            self.create_job(job_id)
            database.update_system_job(job_id, status="error")
        snapshot_at = database.utc_now()
        self.create_job("created-after-snapshot")
        database.update_system_job("created-after-snapshot", status="error")

        with (
            patch.object(main, "_kick_video_queue") as kick_video,
            patch.object(main, "_kick_comment_queue") as kick_comment,
        ):
            response = main.bulk_job_action(main.BulkJobActionRequest(
                action="retry",
                mode="all_matching",
                status="error",
                job_type="video_generation",
                search="error",
                excluded_job_ids=[matching_ids[-1]],
                snapshot_at=snapshot_at,
            ))

        self.assertEqual(response["requested"], 204)
        self.assertEqual(response["succeeded"], 204)
        self.assertEqual(database.get_system_job(matching_ids[0])["status"], "queued")
        self.assertEqual(database.get_system_job(matching_ids[-1])["status"], "error")
        self.assertEqual(
            database.get_system_job("created-after-snapshot")["status"], "error"
        )
        kick_video.assert_called_once_with()
        kick_comment.assert_not_called()

    def test_bulk_action_only_kicks_the_related_comment_queue(self):
        database.create_system_job(
            job_id="comment-error",
            job_type="comment_sync",
            title="Comment sync error",
            payload={"publication_id": 1},
        )
        database.update_system_job("comment-error", status="error")

        with (
            patch.object(main, "_kick_video_queue") as kick_video,
            patch.object(main, "_kick_comment_queue") as kick_comment,
        ):
            response = main.bulk_job_action(main.BulkJobActionRequest(
                action="retry",
                job_ids=["comment-error"],
            ))

        self.assertEqual(response["succeeded"], 1)
        kick_video.assert_not_called()
        kick_comment.assert_called_once_with()

    def test_bulk_retry_does_not_restore_a_job_for_a_video_marked_error(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=video-error",
            "Video error",
            "Transcript",
            "Generated script",
        )
        self.create_job("video-error-job")
        database.update_system_job(
            "video-error-job",
            video_id=video_id,
            status="error",
        )
        database.set_video_status(video_id, database.VIDEO_STATUS_ERROR)

        response = main.bulk_job_action(main.BulkJobActionRequest(
            action="retry",
            job_ids=["video-error-job"],
        ))

        self.assertEqual(response["succeeded"], 0)
        self.assertEqual(response["failed"], 1)
        self.assertEqual(
            database.get_system_job("video-error-job")["status"],
            "error",
        )

    def test_bulk_action_skips_job_that_changes_status_during_execution(self):
        for job_id in ("race-error", "stable-error"):
            self.create_job(job_id)
            database.update_system_job(job_id, status="error")
        original_retry = database.retry_system_job

        def retry_with_race(job_id):
            if job_id == "race-error":
                raise ValueError("Trạng thái job vừa thay đổi.")
            return original_retry(job_id)

        with (
            patch.object(database, "retry_system_job", side_effect=retry_with_race),
            patch.object(main, "_kick_video_queue"),
            patch.object(main, "_kick_comment_queue"),
        ):
            response = main.bulk_job_action(main.BulkJobActionRequest(
                action="retry",
                job_ids=["race-error", "stable-error"],
            ))

        self.assertEqual(response["eligible"], 2)
        self.assertEqual(response["succeeded"], 1)
        self.assertEqual(response["skipped"], 1)
        self.assertEqual(response["failed"], 0)
        self.assertEqual(database.get_system_job("race-error")["status"], "error")
        self.assertEqual(database.get_system_job("stable-error")["status"], "queued")

    def test_bulk_action_dispatches_to_youtube_download_manager(self):
        running_download = {
            "id": "download-a",
            "status": "running",
            "total": 1,
            "completed": 0,
            "failed": 0,
            "items": [],
            "created_at": database.utc_now(),
        }
        paused_download = {**running_download, "status": "paused"}
        with (
            patch.object(main.download_jobs, "list_jobs", return_value=[running_download]),
            patch.object(main.download_jobs, "get", return_value=running_download),
            patch.object(
                main.download_jobs,
                "pause",
                return_value=paused_download,
            ) as pause_download,
            patch.object(main, "_kick_video_queue") as kick_video,
            patch.object(main, "_kick_comment_queue") as kick_comment,
        ):
            response = main.bulk_job_action(main.BulkJobActionRequest(
                action="pause",
                mode="explicit",
                job_ids=["download:download-a"],
            ))

        self.assertEqual(response["succeeded"], 1)
        pause_download.assert_called_once_with("download-a")
        kick_video.assert_not_called()
        kick_comment.assert_not_called()

    def test_audio_and_completed_jobs_are_not_selectable(self):
        audio_item = main._audio_job_center_item({
            "video_id": 9,
            "status": "failed",
            "title": "Audio",
        })
        self.create_job("completed-job")
        completed = database.update_system_job("completed-job", status="done")
        completed_item = main._system_job_center_item(completed, None)

        self.assertFalse(main._job_is_selectable(audio_item))
        self.assertFalse(main._job_is_selectable(completed_item))

    def test_job_search_matches_each_video_inside_download_batch(self):
        item = main._download_job_center_item({
            "id": "batch-a",
            "status": "completed",
            "total": 2,
            "completed": 2,
            "failed": 0,
            "items": [
                {
                    "id": "alpha-id",
                    "title": "Video thông thường",
                    "url": "https://youtube.com/watch?v=alpha-id",
                },
                {
                    "id": "history-id",
                    "title": "Lịch sử Việt Nam",
                    "url": "https://youtube.com/watch?v=history-id",
                },
            ],
        })

        self.assertTrue(main._job_matches_video_search(item, "lich su viet nam"))
        self.assertTrue(main._job_matches_video_search(item, "history-id"))
        self.assertFalse(main._job_matches_video_search(item, "khong ton tai"))

    def test_fail_interrupted_system_jobs(self):
        database.create_system_job(
            job_id="render-running",
            job_type="video_render",
            title="Render Running",
            payload={"video_id": 1},
        )
        database.update_system_job("render-running", status="running")

        database.create_system_job(
            job_id="render-cancel-req",
            job_type="video_render",
            title="Render Cancel Requested",
            payload={"video_id": 1},
        )
        database.update_system_job(
            "render-cancel-req",
            status="running",
            cancel_requested=1,
        )

        database.create_system_job(
            job_id="render-completed",
            job_type="video_render",
            title="Render Completed",
            payload={"video_id": 1},
        )
        database.update_system_job("render-completed", status="completed")

        recovered = database.fail_interrupted_system_jobs(
            "video_render",
            "Tác vụ bị gián đoạn do hệ thống khởi động lại.",
        )
        self.assertEqual(recovered, 2)

        j1 = database.get_system_job("render-running")
        self.assertEqual(j1["status"], "failed")
        self.assertEqual(j1["error"], "Tác vụ bị gián đoạn do hệ thống khởi động lại.")

        j2 = database.get_system_job("render-cancel-req")
        self.assertEqual(j2["status"], "canceled")

        j3 = database.get_system_job("render-completed")
        self.assertEqual(j3["status"], "completed")

    def test_cancel_render_video_endpoint(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=render-test",
            "Render Test",
            "transcript",
            "script",
        )
        database.create_system_job(
            job_id="job-render-active",
            job_type="video_render",
            title="Render Active",
            payload={"video_id": video_id},
        )
        database.update_system_job("job-render-active", video_id=video_id, status="queued")

        res = main.cancel_render_video(video_id)
        self.assertTrue(res["success"])
        job = database.get_system_job("job-render-active")
        self.assertEqual(job["status"], "canceled")

    def test_job_center_includes_and_filters_production_jobs(self):
        database.create_system_job(
            job_id="job-render-1",
            job_type="video_render",
            title="Dựng video MP4 #1",
            payload={"video_id": 10},
        )
        database.create_system_job(
            job_id="job-scene-1",
            job_type="visual_scene_plan",
            title="Kế hoạch cảnh #1",
            payload={"video_id": 10},
        )
        database.create_system_job(
            job_id="job-flow-media-1",
            job_type="flow_media_generation",
            title="Tạo media Google Flow #1",
            payload={"video_id": 10},
        )
        database.create_system_job(
            job_id="job-upload-1",
            job_type="youtube_upload",
            title="Upload YT #1",
            payload={"video_id": 10},
        )
        database.create_system_job(
            job_id="job-fb-1",
            job_type="fb_crosspost",
            title="Đăng chéo Facebook #1",
            payload={"page_id": "page_123"},
        )

        all_res = main.list_jobs(job_type="all")
        all_ids = {item["id"] for item in all_res["items"]}
        self.assertIn("job-render-1", all_ids)
        self.assertIn("job-scene-1", all_ids)
        self.assertIn("job-flow-media-1", all_ids)
        self.assertIn("job-upload-1", all_ids)
        self.assertIn("job-fb-1", all_ids)

        render_res = main.list_jobs(job_type="video_render")
        self.assertEqual(len(render_res["items"]), 1)
        self.assertEqual(render_res["items"][0]["id"], "job-render-1")
        self.assertEqual(render_res["items"][0]["type_label"], "Dựng video MP4")

        scene_res = main.list_jobs(job_type="visual_scene_plan")
        self.assertEqual(len(scene_res["items"]), 1)
        self.assertEqual(scene_res["items"][0]["id"], "job-scene-1")
        self.assertEqual(scene_res["items"][0]["type_label"], "Lập kế hoạch cảnh")

        flow_res = main.list_jobs(job_type="flow_media_generation")
        self.assertEqual(len(flow_res["items"]), 1)
        self.assertEqual(flow_res["items"][0]["id"], "job-flow-media-1")
        self.assertEqual(
            flow_res["items"][0]["type_label"],
            "Tạo media Google Flow",
        )

        yt_res = main.list_jobs(job_type="youtube_publish")
        self.assertEqual(len(yt_res["items"]), 1)
        self.assertEqual(yt_res["items"][0]["id"], "job-upload-1")
        self.assertEqual(
            yt_res["items"][0]["type_label"],
            "Upload YouTube (Trình duyệt GPM)",
        )

        fb_res = main.list_jobs(job_type="fb_crosspost")
        self.assertEqual(len(fb_res["items"]), 1)
        self.assertEqual(fb_res["items"][0]["id"], "job-fb-1")
        self.assertEqual(fb_res["items"][0]["type_label"], "Đăng chéo Facebook")

    def test_legacy_flow_render_jobs_migrate_without_touching_stock(self):
        flow_video_id = database.save_video(
            "https://youtube.com/watch?v=legacy-flow",
            "Legacy Flow",
            "transcript",
            "script",
        )
        stock_video_id = database.save_video(
            "https://youtube.com/watch?v=legacy-stock",
            "Legacy Stock",
            "transcript",
            "script",
        )
        database.create_system_job(
            job_id="legacy-flow-render",
            job_type="video_render",
            title="Legacy Flow Render",
            payload={
                "video_id": flow_video_id,
                "snapshot": {"render_mode": "google_flow"},
            },
            video_id=flow_video_id,
        )
        database.create_system_job(
            job_id="legacy-stock-render",
            job_type="video_render",
            title="Legacy Stock Render",
            payload={
                "video_id": stock_video_id,
                "snapshot": {"render_mode": "stock_video"},
            },
            video_id=stock_video_id,
        )

        migrated = main._migrate_legacy_google_flow_render_jobs()

        self.assertEqual(migrated, 1)
        self.assertEqual(
            database.get_system_job("legacy-flow-render")["job_type"],
            "flow_media_generation",
        )
        self.assertEqual(
            database.get_system_job("legacy-stock-render")["job_type"],
            "video_render",
        )

    def test_flow_and_render_coordinators_run_independently(self):
        flow_started = threading.Event()
        release_flow = threading.Event()
        render_completed = threading.Event()
        errors = []

        def run_flow(job):
            flow_started.set()
            if not release_flow.wait(timeout=3):
                raise RuntimeError("Flow test timed out")
            database.update_system_job(job["id"], status="completed")

        def run_render(job):
            database.update_system_job(job["id"], status="completed")
            render_completed.set()

        def record_error(_job, exc):
            errors.append(exc)

        flow_coordinator = main.production_coordinator_service.ProductionCoordinator(
            {"flow_media_generation": run_flow},
            error_handler=record_error,
        )
        render_coordinator = main.production_coordinator_service.ProductionCoordinator(
            {"video_render": run_render},
            error_handler=record_error,
        )
        database.create_system_job(
            job_id="independent-flow",
            job_type="flow_media_generation",
            title="Flow",
            payload={},
        )
        database.create_system_job(
            job_id="independent-render",
            job_type="video_render",
            title="Render",
            payload={},
        )

        try:
            flow_coordinator.wake()
            self.assertTrue(flow_started.wait(timeout=1))
            render_coordinator.wake()
            self.assertTrue(render_completed.wait(timeout=1))
        finally:
            release_flow.set()
            flow_coordinator.stop(timeout=3)
            render_coordinator.stop(timeout=3)

        self.assertEqual(errors, [])
        self.assertEqual(
            database.get_system_job("independent-render")["status"],
            "completed",
        )

    def test_video_stage_get_or_create_is_atomic_for_concurrent_triggers(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=atomic-flow-trigger",
            "Atomic Flow Trigger",
            "transcript",
            "script",
        )
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def create_stage_job(suffix):
            try:
                barrier.wait(timeout=2)
                results.append(
                    database.create_system_job_if_absent(
                        job_id=f"atomic-flow-{suffix}",
                        job_type="flow_media_generation",
                        title="Atomic Flow",
                        payload={"video_id": video_id},
                        video_id=video_id,
                        dedupe_job_types=("flow_media_generation", "video_render"),
                    )
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=create_stage_job, args=(index,))
            for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for _, created in results if created), 1)
        self.assertEqual(len({job["id"] for job, _ in results}), 1)
        active_jobs = [
            job
            for job in database.list_active_system_jobs()
            if job.get("video_id") == video_id
            and job.get("job_type") in {"flow_media_generation", "video_render"}
        ]
        self.assertEqual(len(active_jobs), 1)

    def test_job_center_production_job_actions(self):
        database.create_system_job(
            job_id="job-render-act",
            job_type="video_render",
            title="Dựng video MP4 Action",
            payload={"video_id": 20},
        )
        with patch.object(main, "_kick_production_queue"):
            # Pause
            paused = main.pause_job("job-render-act")
            self.assertTrue(paused["success"])
            self.assertEqual(database.get_system_job("job-render-act")["status"], "paused")

            # Resume
            resumed = main.resume_job("job-render-act")
            self.assertTrue(resumed["success"])
            self.assertEqual(database.get_system_job("job-render-act")["status"], "queued")

            # Cancel
            canceled = main.cancel_job("job-render-act")
            self.assertTrue(canceled["success"])
            self.assertEqual(database.get_system_job("job-render-act")["status"], "canceled")

            # Retry
            retried = main.retry_job("job-render-act")
            self.assertTrue(retried["success"])
            self.assertEqual(database.get_system_job("job-render-act")["status"], "queued")

    def test_retry_render_stage_rejects_another_active_stage_for_video(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=retry-stage-conflict",
            "Retry Stage Conflict",
            "transcript",
            "script",
        )
        database.create_system_job(
            job_id="flow-stage-error",
            job_type="flow_media_generation",
            title="Flow Error",
            payload={"video_id": video_id},
            video_id=video_id,
        )
        database.update_system_job("flow-stage-error", status="error")
        database.create_system_job(
            job_id="render-stage-active",
            job_type="video_render",
            title="Render Active",
            payload={"video_id": video_id, "media_prepared": True},
            video_id=video_id,
        )

        with self.assertRaises(main.HTTPException) as raised:
            main.retry_job("flow-stage-error")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            database.get_system_job("flow-stage-error")["status"],
            "error",
        )

    def test_job_center_fb_crosspost_cancel_action(self):
        database.create_system_job(
            job_id="fb-crosspost-task_test1",
            job_type="fb_crosspost",
            title="FB Test",
            payload={"task_id": "task_test1", "page_id": "p1"},
        )
        database.update_system_job("fb-crosspost-task_test1", status="running")
        with patch("auto_yt.services.fb_crossposter_service.cancel_schedule_ahead_batch"):
            canceled = main.cancel_job("fb-crosspost-task_test1")
            self.assertTrue(canceled["success"])
            job = database.get_system_job("fb-crosspost-task_test1")
            self.assertEqual(job["cancel_requested"], 1)

    def test_job_center_fb_crosspost_retry_action(self):
        database.create_system_job(
            job_id="fb-crosspost-task_retry1",
            job_type="fb_crosspost",
            title="FB Retry Test",
            payload={"page_id": "page_123", "days_ahead": 2},
        )
        database.update_system_job("fb-crosspost-task_retry1", status="error", error="Meta timeout")
        with patch("auto_yt.services.fb_crossposter_service.start_schedule_ahead_batch") as mock_start:
            retried = main.retry_job("fb-crosspost-task_retry1")
            self.assertTrue(retried["success"])
            job = database.get_system_job("fb-crosspost-task_retry1")
            self.assertEqual(job["status"], "queued")
            mock_start.assert_not_called()
            main._execute_fb_crosspost_job(job)
            mock_start.assert_called_once_with(
                target_page_id="page_123",
                days_ahead=2,
                existing_sys_job_id="fb-crosspost-task_retry1",
            )

    def test_job_center_fb_crosspost_retry_failed_status(self):
        database.create_system_job(
            job_id="fb-crosspost-task_failed1",
            job_type="fb_crosspost",
            title="FB Retry Failed Status Test",
            payload={"page_id": "page_456", "days_ahead": 3},
        )
        database.update_system_job("fb-crosspost-task_failed1", status="failed", error="Fatal batch error")
        with patch("auto_yt.services.fb_crossposter_service.start_schedule_ahead_batch") as mock_start:
            retried = main.retry_job("fb-crosspost-task_failed1")
            self.assertTrue(retried["success"])
            job = database.get_system_job("fb-crosspost-task_failed1")
            self.assertEqual(job["status"], "queued")
            mock_start.assert_not_called()
            main._execute_fb_crosspost_job(job)
            mock_start.assert_called_once_with(
                target_page_id="page_456",
                days_ahead=3,
                existing_sys_job_id="fb-crosspost-task_failed1",
            )

    def test_job_center_supports_all_new_publishing_and_utility_jobs(self):
        database.create_system_job(
            job_id="fb-sync-1",
            job_type="fb_crosspost_sync",
            title="FB Sync Test",
            payload={"channel_url": "https://youtube.com/@test"},
        )
        database.create_system_job(
            job_id="thumb-1",
            job_type="thumbnail_generation",
            title="Thumb Test",
            payload={"video_id": 1},
        )
        database.create_system_job(
            job_id="tiktok-1",
            job_type="tiktok_publish",
            title="TikTok Test",
            payload={"video_path": "test.mp4"},
        )

        all_jobs = main.list_jobs(job_type="all")
        job_types = {j["type"] for j in all_jobs["items"]}
        self.assertIn("fb_crosspost_sync", job_types)
        self.assertIn("thumbnail_generation", job_types)
        self.assertIn("tiktok_publish", job_types)

        # Filter by fb_crosspost_sync
        fb_sync_jobs = main.list_jobs(job_type="fb_crosspost_sync")
        self.assertEqual(len(fb_sync_jobs["items"]), 1)
        self.assertEqual(fb_sync_jobs["items"][0]["type_label"], "Đồng bộ video Facebook")

        # Filter by thumbnail_generation
        thumb_jobs = main.list_jobs(job_type="thumbnail_generation")
        self.assertEqual(len(thumb_jobs["items"]), 1)
        self.assertEqual(thumb_jobs["items"][0]["type_label"], "Sinh ảnh Thumbnail")

        # Filter by tiktok_publish
        tiktok_jobs = main.list_jobs(job_type="tiktok_publish")
        self.assertEqual(len(tiktok_jobs["items"]), 1)
        self.assertEqual(tiktok_jobs["items"][0]["type_label"], "Đăng video TikTok")



    def test_force_stop_running_job(self):
        database.create_system_job(
            job_id="job-force-stop-1",
            job_type="video_generation",
            title="Force Stop Test",
            payload={"video_id": 99},
        )
        database.update_system_job("job-force-stop-1", status="running", cancel_requested=1)
        with patch("auto_yt.services.process_registry.kill_job_processes") as mock_kill:
            result = main.force_stop_job("job-force-stop-1")
            self.assertTrue(result["success"])
            mock_kill.assert_any_call("job-force-stop-1")
            mock_kill.assert_any_call("video:99")
            
            job = database.get_system_job("job-force-stop-1")
            self.assertEqual(job["status"], "canceled")
            self.assertEqual(job["cancel_requested"], 0)
            self.assertIn("dừng cưỡng bức", job["progress"].lower())

    def test_force_stop_bulk_action(self):
        database.create_system_job(
            job_id="job-force-bulk-1",
            job_type="video_generation",
            title="Force Bulk 1",
            payload={"video_id": 101},
        )
        database.update_system_job("job-force-bulk-1", status="running")
        with patch("auto_yt.services.process_registry.kill_job_processes") as mock_kill:
            req = main.BulkJobActionRequest(
                action="force_stop",
                mode="explicit",
                job_ids=["job-force-bulk-1"],
            )
            result = main.bulk_job_action(req)
            self.assertTrue(result["success"])
            self.assertEqual(result["succeeded"], 1)
            job = database.get_system_job("job-force-bulk-1")
            self.assertEqual(job["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
