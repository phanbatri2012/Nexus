import asyncio
import datetime as dt
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from nexus_engine.services import database as db
from nexus_engine.services import gpm_youtube_automation
from nexus_engine.services import gpm_service
from nexus_engine.services import maintenance_guard
from nexus_engine.services import youtube_comments
from nexus_engine.services import youtube_pinned_comment


UTC = dt.timezone.utc


class TestYouTubePinnedCommentWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "pinned-comment.db"
        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.gpm_detail_patch = patch.object(
            gpm_service,
            "get_gpm_profile_detail",
            return_value={
                "id": "gpm-profile-pinned-comment",
                "raw_proxy": "http://proxy-user:proxy-pass@127.0.0.1:8080",
            },
        )
        self.gpm_detail_patch.start()
        db.init_db()
        self.now = dt.datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
        now_iso = self.now.isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO youtube_channels (
                    channel_id, title, gpm_profile_id, gpm_proxy_info,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "UC_PINNED_COMMENT_TEST",
                    "Pinned comment channel",
                    "gpm-profile-pinned-comment",
                    "http://proxy-user:proxy-pass@127.0.0.1:8080",
                    now_iso,
                    now_iso,
                ),
            )
            self.channel_db_id = int(cursor.lastrowid)

    def tearDown(self):
        self.gpm_detail_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _publication(
        self,
        *,
        script: str = "### [BÌNH LUẬN GHIM]\nNội dung cần ghim",
        privacy_status: str = "public",
        published_at: str | None = None,
        scheduled_at: str = "",
    ) -> dict:
        video_id = db.save_video(
            url="https://example.test/source",
            title="Pinned comment test",
            transcript="",
            generated_script=script,
            prompt_version="test-prompt",
        )
        return db.save_video_publication(
            video_id=video_id,
            youtube_channel_id=self.channel_db_id,
            youtube_video_id="youtube-video-test",
            published_url="https://www.youtube.com/watch?v=youtube-video-test",
            published_title="Pinned comment test",
            published_at=(
                (self.now - dt.timedelta(hours=2)).isoformat()
                if published_at is None
                else published_at
            ),
            privacy_status=privacy_status,
            processing_status="succeeded",
            scheduled_at=scheduled_at,
        )

    def _provider(self, channel_db_id: int) -> tuple[dict, str]:
        self.assertEqual(channel_db_id, self.channel_db_id)
        return db.get_youtube_channel(channel_db_id), "access-token"

    def _public_remote(self) -> dict:
        return {
            "youtube_video_id": "youtube-video-test",
            "channel_id": "UC_PINNED_COMMENT_TEST",
            "privacy_status": "public",
            "published_at": (self.now - dt.timedelta(hours=2)).isoformat(),
            "scheduled_publish_at": "",
        }

    def test_enqueue_is_unique_and_not_claimable_before_public_plus_one_hour(self):
        publication = self._publication()
        public_at = (self.now + dt.timedelta(hours=2)).isoformat()

        first, created = youtube_pinned_comment.enqueue_for_publication(
            publication["id"], public_at=public_at
        )
        second, created_again = youtube_pinned_comment.enqueue_for_publication(
            publication["id"], public_at=public_at
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], f"youtube-pinned-comment-{publication['id']}")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(first["status"], "retry_wait")
        self.assertEqual(
            first["next_retry_at"],
            (self.now + dt.timedelta(hours=3)).isoformat(),
        )
        self.assertIsNone(db.claim_next_system_job(youtube_pinned_comment.JOB_TYPE))

    def test_unscheduled_private_to_public_transition_anchors_at_observation(self):
        publication = {
            "privacy_status": "private",
            "published_at": (self.now - dt.timedelta(days=3)).isoformat(),
            "scheduled_at": "",
        }
        remote = {
            "privacy_status": "public",
            "published_at": (self.now - dt.timedelta(days=3)).isoformat(),
            "scheduled_publish_at": "",
        }

        public_at = youtube_pinned_comment.resolve_public_at(
            publication,
            remote,
            observed_at=self.now.isoformat(),
        )

        self.assertEqual(public_at, self.now.isoformat())

    def test_maintenance_ignores_future_job_but_blocks_due_job(self):
        publication = self._publication()
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(dt.datetime.now(UTC) + dt.timedelta(hours=2)).isoformat(),
        )
        with patch(
            "nexus_engine.services.drain_mode.is_drain_mode_active",
            return_value=False,
        ):
            self.assertNotIn(
                job["id"],
                {item["id"] for item in maintenance_guard.list_system_job_blockers()},
            )
            db.update_system_job(
                job["id"],
                next_retry_at=(dt.datetime.now(UTC) - dt.timedelta(minutes=1)).isoformat(),
            )
            self.assertIn(
                job["id"],
                {item["id"] for item in maintenance_guard.list_system_job_blockers()},
            )

    def test_publish_now_endpoint_creates_delayed_job(self):
        from nexus_engine import main

        publication = self._publication(
            privacy_status="private",
            published_at="",
        )
        with patch(
            "nexus_engine.services.browser_youtube_uploader.make_video_public_via_browser",
            new=AsyncMock(),
        ) as make_public:
            response = asyncio.run(
                main.publish_video_now_endpoint(int(publication["video_id"]))
            )

        self.assertEqual(response["status"], "published")
        make_public.assert_awaited_once()
        pinned_job = db.get_system_job(
            db.youtube_pinned_comment_job_id(publication["id"])
        )
        self.assertEqual(pinned_job["status"], "retry_wait")
        public_at = dt.datetime.fromisoformat(pinned_job["payload"]["public_at"])
        run_at = dt.datetime.fromisoformat(pinned_job["next_retry_at"])
        self.assertEqual(run_at - public_at, dt.timedelta(hours=1))

    def test_publish_workflow_scheduled_result_enqueues_once(self):
        from nexus_engine import main

        scheduled_at = "2026-10-27T12:00:00+00:00"
        job = {"id": "youtube-publish-test", "video_id": 17, "payload": {}}
        result = {
            "workflow_id": "workflow-test",
            "publish_stage": "scheduled",
            "scheduled_at": scheduled_at,
        }
        with (
            patch.object(
                main.youtube_publish_workflow,
                "execute_publish_job",
                return_value=result,
            ),
            patch.object(
                main.db,
                "get_youtube_publish_workflow",
                return_value={"publication_id": 23},
            ),
            patch.object(
                main.youtube_pinned_comment,
                "enqueue_for_publication",
                return_value=({}, True),
            ) as enqueue,
            patch.object(main.db, "update_system_job"),
        ):
            main._execute_youtube_publish_job(job)

        enqueue.assert_called_once_with(
            23,
            scheduled_at=scheduled_at,
            reschedule_existing=True,
        )

    def test_existing_matching_comment_is_reused_and_verified_as_pinned(self):
        publication = self._publication()
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(self.now - dt.timedelta(hours=2)).isoformat(),
        )
        existing = {
            "comment_id": "comment-existing",
            "published_at": (self.now - dt.timedelta(minutes=10)).isoformat(),
            "existing": True,
        }
        with (
            patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
            patch.object(
                youtube_comments, "get_video_details", return_value=self._public_remote()
            ),
            patch.object(
                youtube_comments,
                "find_channel_top_level_comment",
                return_value=existing,
            ) as find_comment,
            patch.object(youtube_comments, "publish_top_level_comment") as publish_comment,
            patch.object(
                gpm_youtube_automation,
                "pin_top_level_comment_via_gpm",
                new=AsyncMock(return_value={"pinned": True, "already_pinned": False}),
            ) as pin_comment,
        ):
            completed = youtube_pinned_comment.execute_job(
                job, access_token_provider=self._provider
            )

        self.assertEqual(completed["status"], "done")
        find_comment.assert_called_once()
        publish_comment.assert_not_called()
        pin_comment.assert_awaited_once()
        saved = db.get_video_publication(publication["id"])
        self.assertEqual(saved["pinned_comment_id"], "comment-existing")
        self.assertEqual(saved["pinned_comment_status"], "pinned")

    def test_retry_after_post_skips_reconcile_and_insert(self):
        publication = self._publication()
        db.update_video_publication(
            publication["id"],
            pinned_comment_status="posted",
            pinned_comment_id="comment-saved-after-insert",
            pinned_comment_posted_at=(self.now - dt.timedelta(minutes=5)).isoformat(),
        )
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(self.now - dt.timedelta(hours=2)).isoformat(),
        )
        with (
            patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
            patch.object(
                youtube_comments, "get_video_details", return_value=self._public_remote()
            ),
            patch.object(youtube_comments, "find_channel_top_level_comment") as find_comment,
            patch.object(youtube_comments, "publish_top_level_comment") as publish_comment,
            patch.object(
                gpm_youtube_automation,
                "pin_top_level_comment_via_gpm",
                new=AsyncMock(return_value={"pinned": True, "already_pinned": True}),
            ),
        ):
            completed = youtube_pinned_comment.execute_job(
                job, access_token_provider=self._provider
            )

        self.assertEqual(completed["status"], "done")
        find_comment.assert_not_called()
        publish_comment.assert_not_called()

    def test_missing_metadata_pauses_without_insert_or_gpm(self):
        publication = self._publication(script="### [TITLE]\nKhông có bình luận ghim")
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(self.now - dt.timedelta(hours=2)).isoformat(),
        )
        with (
            patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
            patch.object(
                youtube_comments, "get_video_details", return_value=self._public_remote()
            ),
            patch.object(youtube_comments, "find_channel_top_level_comment") as find_comment,
            patch.object(youtube_comments, "publish_top_level_comment") as publish_comment,
            patch.object(
                gpm_youtube_automation,
                "pin_top_level_comment_via_gpm",
                new=AsyncMock(),
            ) as pin_comment,
        ):
            with self.assertRaises(
                youtube_pinned_comment.PinnedCommentAttentionRequired
            ) as raised:
                youtube_pinned_comment.execute_job(
                    job, access_token_provider=self._provider
                )
            paused = youtube_pinned_comment.handle_job_failure(job, raised.exception)

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["next_retry_at"], "")
        self.assertEqual(
            paused["result"]["attention_required"],
            "pinned_comment_metadata_missing",
        )
        find_comment.assert_not_called()
        publish_comment.assert_not_called()
        pin_comment.assert_not_awaited()

    def test_gpm_profile_without_proxy_fails_closed_before_browser(self):
        publication = self._publication()
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(self.now - dt.timedelta(hours=2)).isoformat(),
        )
        with (
            patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
            patch.object(gpm_service, "get_gpm_profile_detail", return_value={"raw_proxy": ""}),
            patch.object(youtube_comments, "get_video_details") as get_video,
            patch.object(
                gpm_youtube_automation,
                "pin_top_level_comment_via_gpm",
                new=AsyncMock(),
            ) as pin_comment,
        ):
            with self.assertRaises(
                youtube_pinned_comment.PinnedCommentAttentionRequired
            ) as raised:
                youtube_pinned_comment.execute_job(
                    job, access_token_provider=self._provider
                )
            paused = youtube_pinned_comment.handle_job_failure(job, raised.exception)

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(
            paused["result"]["attention_required"],
            "pinned_comment_gpm_proxy_missing",
        )
        get_video.assert_not_called()
        pin_comment.assert_not_awaited()

    def test_future_remote_schedule_rebases_without_comment_or_gpm(self):
        scheduled_at = (self.now + dt.timedelta(hours=2)).isoformat()
        publication = self._publication(
            privacy_status="private",
            published_at="",
            scheduled_at=scheduled_at,
        )
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"], scheduled_at=scheduled_at
        )
        moved_to = self.now + dt.timedelta(hours=4)
        remote = {
            "youtube_video_id": "youtube-video-test",
            "channel_id": "UC_PINNED_COMMENT_TEST",
            "privacy_status": "private",
            "published_at": "",
            "scheduled_publish_at": moved_to.isoformat(),
        }
        with (
            patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
            patch.object(youtube_comments, "get_video_details", return_value=remote),
            patch.object(youtube_comments, "find_channel_top_level_comment") as find_comment,
            patch.object(youtube_comments, "publish_top_level_comment") as publish_comment,
            patch.object(
                gpm_youtube_automation,
                "pin_top_level_comment_via_gpm",
                new=AsyncMock(),
            ) as pin_comment,
        ):
            rescheduled = youtube_pinned_comment.execute_job(
                job, access_token_provider=self._provider
            )

        self.assertEqual(rescheduled["status"], "retry_wait")
        self.assertEqual(
            rescheduled["next_retry_at"],
            (moved_to + dt.timedelta(hours=1)).isoformat(),
        )
        self.assertEqual(rescheduled["payload"]["schedule_rebase_count"], 1)
        self.assertNotIn("execution_attempts", rescheduled["result"])
        find_comment.assert_not_called()
        publish_comment.assert_not_called()
        pin_comment.assert_not_awaited()

    def test_fourth_schedule_rebase_stops_for_attention(self):
        scheduled_at = (self.now + dt.timedelta(hours=2)).isoformat()
        publication = self._publication(
            privacy_status="private",
            published_at="",
            scheduled_at=scheduled_at,
        )
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"], scheduled_at=scheduled_at
        )
        payload = dict(job["payload"])
        payload["schedule_rebase_count"] = 3
        job = db.update_system_job(job["id"], payload_json=payload)
        remote = {
            "youtube_video_id": "youtube-video-test",
            "channel_id": "UC_PINNED_COMMENT_TEST",
            "privacy_status": "private",
            "published_at": "",
            "scheduled_publish_at": (self.now + dt.timedelta(hours=4)).isoformat(),
        }
        with (
            patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
            patch.object(youtube_comments, "get_video_details", return_value=remote),
        ):
            with self.assertRaises(
                youtube_pinned_comment.PinnedCommentAttentionRequired
            ) as raised:
                youtube_pinned_comment.execute_job(
                    job, access_token_provider=self._provider
                )
            paused = youtube_pinned_comment.handle_job_failure(job, raised.exception)

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["next_retry_at"], "")
        self.assertEqual(
            paused["result"]["attention_required"],
            "pinned_comment_schedule_changed",
        )

    def test_exhausted_retry_budget_is_terminal_until_manual_retry(self):
        from nexus_engine import main

        publication = self._publication()
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(self.now - dt.timedelta(hours=2)).isoformat(),
        )
        job = db.update_system_job(
            job["id"],
            status="running",
            result_json={
                "stage": "posted",
                "execution_attempts": 6,
                "first_attempt_at": self.now.isoformat(),
                "retry_deadline_at": (self.now + dt.timedelta(hours=24)).isoformat(),
            },
        )
        with patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now):
            paused = youtube_pinned_comment.handle_job_failure(
                job,
                youtube_pinned_comment.PinnedCommentTransientError("temporary"),
            )

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["next_retry_at"], "")
        self.assertEqual(
            paused["result"]["attention_required"],
            "pinned_comment_retry_exhausted",
        )
        center_item = main._system_job_center_item(paused, None)
        self.assertTrue(center_item["can_retry"])
        self.assertFalse(center_item["can_resume"])
        restarted_item = main._run_system_job_center_action(
            paused["id"],
            "retry",
            kick_queues=False,
        )
        restarted = db.get_system_job(paused["id"])
        self.assertEqual(restarted_item["status"], "queued")
        self.assertEqual(restarted["status"], "queued")
        self.assertNotIn("execution_attempts", restarted["result"])
        self.assertNotIn("retry_deadline_at", restarted["result"])

    def test_transient_retry_uses_finite_fixed_backoff_table(self):
        publication = self._publication()
        job, _ = youtube_pinned_comment.enqueue_for_publication(
            publication["id"],
            public_at=(self.now - dt.timedelta(hours=2)).isoformat(),
        )
        expected_delays = (300, 900, 3_600, 14_400, 43_200)
        self.assertEqual(
            youtube_pinned_comment.RETRY_DELAYS_SECONDS,
            expected_delays,
        )
        for attempts, delay_seconds in enumerate(expected_delays, start=1):
            with self.subTest(attempts=attempts):
                current = db.update_system_job(
                    job["id"],
                    status="running",
                    result_json={
                        "stage": "pinning",
                        "execution_attempts": attempts,
                        "first_attempt_at": self.now.isoformat(),
                        "retry_deadline_at": (
                            self.now + dt.timedelta(hours=24)
                        ).isoformat(),
                    },
                )
                with (
                    patch.object(youtube_pinned_comment, "_utc_now", return_value=self.now),
                    patch.object(
                        db,
                        "schedule_system_job_recovery",
                        return_value=current,
                    ) as schedule_recovery,
                ):
                    youtube_pinned_comment.handle_job_failure(
                        current,
                        youtube_pinned_comment.PinnedCommentTransientError(
                            "temporary"
                        ),
                    )

                self.assertEqual(
                    schedule_recovery.call_args.kwargs["delay_seconds"],
                    delay_seconds,
                )


class TestYouTubeTopLevelCommentApi(unittest.TestCase):
    def test_access_token_refresh_preserves_transient_failure(self):
        channel = {
            "id": 1,
            "token_expiry": "",
            "refresh_token_encrypted": "encrypted-refresh",
            "oauth_client_id": "client-id",
            "gpm_proxy_info": "http://127.0.0.1:8080",
        }
        transient = youtube_comments.YouTubeCommentsError(
            "temporary",
            http_status=503,
            transient=True,
        )
        with (
            patch.object(youtube_comments, "decrypt_secret", return_value="refresh-token"),
            patch.object(
                youtube_comments,
                "load_oauth_config",
                return_value={"client_id": "client-id", "client_secret": "secret"},
            ),
            patch.object(
                youtube_comments,
                "refresh_access_token",
                side_effect=transient,
            ),
        ):
            with self.assertRaises(youtube_comments.YouTubeCommentsError) as raised:
                youtube_comments.access_token_for_channel(
                    channel,
                    MagicMock(),
                    proxy="http://127.0.0.1:8080",
                )

        self.assertTrue(raised.exception.transient)
        self.assertEqual(raised.exception.http_status, 503)

    def test_find_exact_own_comment_and_stop_on_repeated_page_token(self):
        wrong_author = {
            "items": [
                {
                    "id": "thread-wrong",
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment-wrong",
                            "snippet": {
                                "authorChannelId": {"value": "UC_OTHER"},
                                "textOriginal": "Nội dung cần ghim",
                            },
                        }
                    },
                }
            ],
            "nextPageToken": "same-token",
        }
        exact = {
            "items": [
                {
                    "id": "thread-exact",
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment-exact",
                            "snippet": {
                                "authorChannelId": {"value": "UC_OWNER"},
                                "textOriginal": "Nội dung   cần ghim",
                                "publishedAt": "2026-10-26T10:00:00Z",
                            },
                        }
                    },
                }
            ],
            "nextPageToken": "same-token",
        }
        with patch.object(
            youtube_comments, "_request_json", side_effect=[wrong_author, exact]
        ) as request:
            found = youtube_comments.find_channel_top_level_comment(
                "token",
                "video-id",
                "UC_OWNER",
                "Nội dung cần ghim",
                proxy="http://127.0.0.1:8080",
            )

        self.assertEqual(found["comment_id"], "comment-exact")
        self.assertTrue(found["existing"])
        self.assertEqual(request.call_count, 2)

    def test_insert_uses_top_level_comment_endpoint_and_returns_comment_id(self):
        response = {
            "id": "thread-new",
            "snippet": {
                "topLevelComment": {
                    "id": "comment-new",
                    "snippet": {
                        "textOriginal": "Nội dung cần ghim",
                        "publishedAt": "2026-10-26T10:00:00Z",
                    },
                }
            },
        }
        with patch.object(youtube_comments, "_request_json", return_value=response) as request:
            created = youtube_comments.publish_top_level_comment(
                "token",
                "video-id",
                "Nội dung cần ghim",
                proxy="http://127.0.0.1:8080",
            )

        self.assertEqual(created["comment_id"], "comment-new")
        _url, kwargs = request.call_args
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["proxy"], "http://127.0.0.1:8080")
        self.assertEqual(kwargs["data"]["snippet"]["videoId"], "video-id")


class _FakeMenuItem:
    def __init__(self, label: str):
        self.label = label
        self.click = AsyncMock()

    async def is_visible(self):
        return True

    async def inner_text(self):
        return self.label


class _FakeLocatorList:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class TestPinnedCommentBrowserAutomation(unittest.TestCase):
    def test_menu_recognizes_vietnamese_pin_and_unpin(self):
        page = MagicMock()
        page.locator.return_value = _FakeLocatorList([_FakeMenuItem("Ghim")])
        action, _item = asyncio.run(gpm_youtube_automation._visible_pin_menu_action(page))
        self.assertEqual(action, "pin")

        page.locator.return_value = _FakeLocatorList([_FakeMenuItem("Bỏ ghim")])
        action, _item = asyncio.run(gpm_youtube_automation._visible_pin_menu_action(page))
        self.assertEqual(action, "unpin")

    def test_pin_targets_comment_id_confirms_dialog_and_verifies(self):
        comment_id = "comment_target_123"
        menu_item = _FakeMenuItem("Pin")
        confirm = MagicMock()
        confirm.count = AsyncMock(return_value=1)
        confirm.is_visible = AsyncMock(return_value=True)
        confirm.click = AsyncMock()
        confirm.first = confirm
        dialog = MagicMock()
        dialog.is_visible = AsyncMock(return_value=True)
        dialog.inner_text = AsyncMock(return_value="Pin this comment?")
        dialog.locator.return_value = confirm
        dialogs = _FakeLocatorList([dialog])
        page = MagicMock()
        page.bring_to_front = AsyncMock()
        page.goto = AsyncMock()
        page.locator.return_value = dialogs
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        thread = MagicMock()

        @asynccontextmanager
        async def fake_session(_profile_id):
            yield context, MagicMock(), {}

        with (
            patch.object(
                gpm_youtube_automation,
                "verify_youtube_login",
                new=AsyncMock(return_value={"logged_in": True, "identity_verified": True}),
            ),
            patch.object(gpm_youtube_automation, "channel_browser_session", fake_session),
            patch.object(
                gpm_youtube_automation,
                "_wait_for_target_comment_thread",
                new=AsyncMock(return_value=thread),
            ) as wait_for_comment,
            patch.object(
                gpm_youtube_automation,
                "_comment_thread_is_pinned",
                new=AsyncMock(side_effect=[False, True]),
            ),
            patch.object(
                gpm_youtube_automation,
                "_open_comment_action_menu",
                new=AsyncMock(),
            ),
            patch.object(
                gpm_youtube_automation,
                "_visible_pin_menu_action",
                new=AsyncMock(return_value=("pin", menu_item)),
            ),
            patch.object(
                gpm_youtube_automation,
                "cleanup_owned_page",
                new=AsyncMock(),
            ),
            patch.object(gpm_youtube_automation.asyncio, "sleep", new=AsyncMock()),
        ):
            result = asyncio.run(
                gpm_youtube_automation.pin_top_level_comment_via_gpm(
                    "profile-id",
                    video_url="https://www.youtube.com/watch?v=video-id",
                    comment_id=comment_id,
                    expected_channel_id="UC_OWNER",
                )
            )

        self.assertTrue(result["pinned"])
        self.assertFalse(result["already_pinned"])
        self.assertIn(f"lc={comment_id}", page.goto.await_args.args[0])
        self.assertEqual(wait_for_comment.await_args_list[0].args[1], comment_id)
        menu_item.click.assert_awaited_once()
        confirm.click.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
