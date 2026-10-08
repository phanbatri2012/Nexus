import datetime as dt
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from auto_yt import main
from auto_yt.services import database
from auto_yt.services import publication_scheduler
from auto_yt.services import youtube_publish_workflow
from auto_yt.services import youtube_publisher


class YouTubePublishPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(
            database, "DB_PATH", self.root / "database.db"
        )
        self.database_patch.start()
        database.init_db()
        self.browser_readiness_patch = patch.object(
            youtube_publish_workflow.channel_scanner_service,
            "ensure_profile_automation_ready",
            return_value={"state": "running_cdp_ready", "automation_ready": True},
        )
        self.browser_readiness_patch.start()
        self.thumbnails_dir = self.root / "thumbnails"
        self.thumbnails_dir.mkdir()
        (self.thumbnails_dir / "publish.png").write_bytes(b"thumbnail")

    def tearDown(self):
        self.browser_readiness_patch.stop()
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def _create_publish_job(
        self, *, schedule: bool, upload_method: str = "api"
    ) -> tuple[dict, dict]:
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=publish-source",
            "Publish title",
            "Transcript",
            (
                "### [METADATA & QUIZ]\nMÔ TẢ VIDEO: Publish description\n\n"
                "### [THUMBNAIL KHÔNG CHỮ]\n"
                "[IMAGE_URL:/api/thumbnails/publish.png]"
            ),
            prompt_version="publish-prompt",
        )
        channel = database.save_youtube_channel(
            channel_id=f"UC-publish-{int(schedule)}",
            title="Publish channel",
            access_token_encrypted="encrypted-access",
            token_expiry=(
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat(),
            gpm_profile_id="gpm-profile",
            gpm_proxy_info="127.0.0.1:8899:user:password",
        )
        slots = [{"day": day, "time": "23:55"} for day in range(7)]
        database.update_youtube_channel(
            channel["id"],
            public_upload_verified=int(schedule),
            publication_timezone="Asia/Ho_Chi_Minh",
            publication_slots_json=json.dumps(slots),
            publication_daily_limit=1,
            publication_lead_minutes=1,
            publication_paused=0,
        )
        video_path = self.root / "final.mp4"
        video_path.write_bytes(b"video-content")
        artifact = database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(video_path),
            content_hash="render-input-hash",
            status="ready",
            mime_type="video/mp4",
            size_bytes=video_path.stat().st_size,
        )
        caption_path = self.root / "caption.srt"
        caption_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nXin chào",
            encoding="utf-8",
        )
        database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="captions",
            path=str(caption_path),
            content_hash="caption-source-hash",
            status="ready",
            mime_type="application/x-subrip",
            size_bytes=caption_path.stat().st_size,
        )
        snapshot = {
            "prompt_version": "publish-prompt",
            "default_youtube_channel_id": channel["channel_id"],
            "pipeline": {
                "youtube_upload": True,
                "youtube_schedule": schedule,
            },
            "image_generation_settings": {
                "thumbnail_variant": "without_text",
            },
            "publishing_settings": {
                "upload_method": upload_method,
                "category_id": "22",
                "language": "vi",
                "made_for_kids": False,
                "notify_subscribers": False,
            },
        }
        job = database.create_system_job(
            job_id=f"publish-job-{int(schedule)}",
            job_type="youtube_publish",
            title="Publish",
            payload={
                "video_id": video_id,
                "artifact_id": artifact["id"],
                "snapshot": snapshot,
            },
            prompt_version="publish-prompt",
        )
        job = database.update_system_job(job["id"], video_id=video_id)
        return job, channel

    def _execute_with_fake_youtube(self, job: dict, *, video_id: str):
        events = []

        def upload_video(**kwargs):
            kwargs["persist_progress"](4)
            payload = {"id": video_id}
            kwargs["persist_remote_video"](payload)
            return payload

        patches = (
            patch.object(
                youtube_publish_workflow.youtube_comments,
                "access_token_for_channel",
                return_value="access-token",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "encrypt_secret",
                side_effect=lambda value: f"encrypted:{value}",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "decrypt_secret",
                side_effect=lambda value: str(value).removeprefix("encrypted:"),
            ),
            patch.object(
                youtube_publisher,
                "start_resumable_upload",
                return_value="https://www.googleapis.com/upload/session",
            ),
            patch.object(
                youtube_publisher,
                "upload_video_resumable",
                side_effect=upload_video,
            ),
            patch.object(youtube_publisher, "upload_thumbnail"),
            patch.object(youtube_publisher, "find_caption_track", return_value=""),
            patch.object(
                youtube_publisher, "upload_caption", return_value={"id": "caption-id"}
            ),
            patch.object(
                youtube_publisher, "require_processing_succeeded", return_value={}
            ),
            patch.object(youtube_publisher, "schedule_video", return_value={}),
        )
        entered = []
        try:
            for context in patches:
                entered.append(context)
                context.start()
            result = youtube_publish_workflow.execute_publish_job(
                job,
                progress=lambda *values: events.append(values),
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _version: "",
                thumbnails_dir=self.thumbnails_dir,
            )
            return result, events, patches
        finally:
            for context in reversed(entered):
                context.stop()

    def test_channel_schedule_round_trip_and_validation(self):
        channel = database.save_youtube_channel(
            channel_id="UC-settings",
            title="Settings",
        )
        request = main.YouTubeChannelSettingsRequest(
            publication_timezone="Asia/Ho_Chi_Minh",
            publication_slots=[{"day": 0, "time": "09:30"}],
            publication_daily_limit=2,
            publication_lead_minutes=180,
            publication_paused=True,
            public_upload_verified=True,
        )
        updated = main.update_youtube_comment_channel(channel["id"], request)

        self.assertEqual(updated["publication_timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(updated["publication_slots"], [{"day": 0, "time": "09:30"}])
        self.assertEqual(updated["publication_daily_limit"], 2)
        self.assertEqual(updated["publication_lead_minutes"], 180)
        self.assertEqual(updated["publication_paused"], 1)
        self.assertEqual(updated["public_upload_verified"], 1)

        invalid = main.YouTubeChannelSettingsRequest(
            publication_timezone="Invalid/Timezone",
            publication_slots=[{"day": 0, "time": "09:30"}],
        )
        with self.assertRaises(HTTPException) as raised:
            main.update_youtube_comment_channel(channel["id"], invalid)
        self.assertEqual(raised.exception.status_code, 422)

        duplicate = main.YouTubeChannelSettingsRequest(
            publication_slots=[
                {"day": 0, "time": "09:30"},
                {"day": 0, "time": "09:30"},
            ],
        )
        with self.assertRaises(HTTPException) as raised:
            main.update_youtube_comment_channel(channel["id"], duplicate)
        self.assertEqual(raised.exception.status_code, 422)

    def test_private_upload_persists_one_remote_video_and_assets(self):
        job, _channel = self._create_publish_job(schedule=False)
        result, events, _patches = self._execute_with_fake_youtube(
            job, video_id="youtube-private"
        )

        workflow = database.get_youtube_publish_workflow(result["workflow_id"])
        publication = database.get_video_publication_by_youtube_id(
            "youtube-private"
        )
        self.assertEqual(workflow["status"], "uploaded_private")
        self.assertEqual(workflow["stage"], "uploaded_private")
        self.assertEqual(workflow["caption_id"], "caption-id")
        self.assertEqual(publication["video_id"], job["video_id"])
        self.assertEqual(publication["privacy_status"], "private")
        self.assertEqual(publication["processing_status"], "processing")
        self.assertEqual(
            publication["artifact_hash"],
            hashlib.sha256(b"video-content").hexdigest(),
        )
        self.assertEqual(database.get_video(job["video_id"])["is_published"], 0)
        listed = database.get_all_videos(limit=10)["items"][0]
        self.assertEqual(listed["publish_status"], "uploaded_private")
        self.assertEqual(listed["current_stage"], "uploaded_private")
        self.assertEqual(listed["publication_privacy_status"], "private")
        self.assertEqual(listed["publication_processing_status"], "processing")
        self.assertEqual(result["scheduled_at"], "")
        self.assertTrue(any(event[1] == "uploading" for event in events))

    def test_scheduled_upload_waits_for_processing_and_reserves_slot(self):
        job, _channel = self._create_publish_job(schedule=True)
        with (
            patch.object(
                youtube_publish_workflow.youtube_comments,
                "access_token_for_channel",
                return_value="access-token",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "encrypt_secret",
                side_effect=lambda value: f"encrypted:{value}",
            ),
            patch.object(youtube_publisher, "start_resumable_upload", return_value="https://www.googleapis.com/upload/session"),
            patch.object(
                youtube_publisher,
                "upload_video_resumable",
                side_effect=lambda **kwargs: {"id": "youtube-scheduled"},
            ),
            patch.object(youtube_publisher, "upload_thumbnail"),
            patch.object(youtube_publisher, "find_caption_track", return_value="caption-existing"),
            patch.object(youtube_publisher, "upload_caption") as upload_caption,
            patch.object(youtube_publisher, "require_processing_succeeded", return_value={}) as processing,
            patch.object(youtube_publisher, "schedule_video", return_value={}) as schedule_video,
        ):
            result = youtube_publish_workflow.execute_publish_job(
                job,
                progress=lambda *_values: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _version: "",
                thumbnails_dir=self.thumbnails_dir,
            )

        self.assertTrue(result["scheduled_at"])
        processing.assert_called_once()
        schedule_video.assert_called_once()
        upload_caption.assert_not_called()
        publication = database.get_video_publication_by_youtube_id(
            "youtube-scheduled"
        )
        self.assertEqual(publication["published_at"], "")
        self.assertEqual(publication["scheduled_at"], result["scheduled_at"])
        self.assertEqual(publication["privacy_status"], "private")
        self.assertEqual(publication["processing_status"], "succeeded")
        reservation = database.get_channel_schedule_reservation(
            result["workflow_id"]
        )
        self.assertEqual(reservation["status"], "scheduled")

    def test_missing_proxy_pauses_before_any_youtube_request(self):
        job, channel = self._create_publish_job(schedule=False)
        database.update_youtube_channel(channel["id"], gpm_proxy_info="Direct")
        with patch.object(youtube_publisher, "start_resumable_upload") as start:
            with self.assertRaises(
                youtube_publish_workflow.PublishConfigurationRequired
            ) as raised:
                youtube_publish_workflow.execute_publish_job(
                    job,
                    progress=lambda *_values: None,
                    cancel_check=lambda: None,
                    resolve_default_channel_id=lambda _version: "",
                    thumbnails_dir=self.thumbnails_dir,
                )
        self.assertIn("gpm_proxy_info", raised.exception.missing_configuration)
        start.assert_not_called()

    def test_migration_and_workflow_reservation_are_idempotent(self):
        job, channel = self._create_publish_job(schedule=False)
        artifact_id = int((job.get("payload") or {})["artifact_id"])
        first, created = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=artifact_id,
            snapshot=(job.get("payload") or {})["snapshot"],
            system_job_id=job["id"],
        )
        database.init_db()
        second, created_again = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=artifact_id,
            snapshot=(job.get("payload") or {})["snapshot"],
            system_job_id=job["id"],
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        connection = database.sqlite3.connect(str(database.DB_PATH))
        try:
            versions = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name = ?",
                (database.PUBLISH_PIPELINE_V1_MIGRATION,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(versions, 1)

    def test_chunk_resume_reuses_encrypted_session_and_saved_offset(self):
        job, _channel = self._create_publish_job(schedule=False)

        def interrupted_upload(**kwargs):
            kwargs["persist_progress"](4)
            raise youtube_publisher.YouTubeTransientError("network")

        with (
            patch.object(
                youtube_publish_workflow.youtube_comments,
                "access_token_for_channel",
                return_value="access-token",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "encrypt_secret",
                side_effect=lambda value: f"encrypted:{value}",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "decrypt_secret",
                side_effect=lambda value: str(value).removeprefix("encrypted:"),
            ),
            patch.object(
                youtube_publisher,
                "start_resumable_upload",
                return_value="https://www.googleapis.com/upload/session",
            ) as start,
            patch.object(
                youtube_publisher,
                "upload_video_resumable",
                side_effect=interrupted_upload,
            ),
        ):
            with self.assertRaises(youtube_publisher.YouTubeTransientError):
                youtube_publish_workflow.execute_publish_job(
                    job,
                    progress=lambda *_values: None,
                    cancel_check=lambda: None,
                    resolve_default_channel_id=lambda _version: "",
                    thumbnails_dir=self.thumbnails_dir,
                )
        workflow = database.get_youtube_publish_workflow_by_job(job["id"])
        self.assertEqual(workflow["upload_offset"], 4)
        self.assertTrue(workflow["upload_session_encrypted"])
        start.assert_called_once()

        def resumed_upload(**kwargs):
            self.assertEqual(kwargs["start_offset"], 4)
            return {"id": "youtube-resumed"}

        with (
            patch.object(
                youtube_publish_workflow.youtube_comments,
                "access_token_for_channel",
                return_value="access-token",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "decrypt_secret",
                side_effect=lambda value: str(value).removeprefix("encrypted:"),
            ),
            patch.object(youtube_publisher, "start_resumable_upload") as restart,
            patch.object(
                youtube_publisher,
                "upload_video_resumable",
                side_effect=resumed_upload,
            ),
            patch.object(youtube_publisher, "upload_thumbnail"),
            patch.object(youtube_publisher, "find_caption_track", return_value="caption"),
            patch.object(youtube_publisher, "require_processing_succeeded"),
            patch.object(youtube_publisher, "schedule_video"),
        ):
            result = youtube_publish_workflow.execute_publish_job(
                database.get_system_job(job["id"]),
                progress=lambda *_values: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _version: "",
                thumbnails_dir=self.thumbnails_dir,
            )
        restart.assert_not_called()
        self.assertEqual(result["youtube_video_id"], "youtube-resumed")

    def test_post_upload_checkpoint_resume_never_uploads_a_second_video(self):
        job, _channel = self._create_publish_job(schedule=False)
        result, _events, _patches = self._execute_with_fake_youtube(
            job, video_id="youtube-checkpoint"
        )
        workflow_id = result["workflow_id"]

        for stage in ("uploaded", "thumbnail_done", "caption_done", "completed"):
            with self.subTest(stage=stage):
                database.update_youtube_publish_workflow(
                    workflow_id,
                    status="running" if stage != "completed" else "completed",
                    stage=stage,
                )
                with (
                    patch.object(
                        youtube_publish_workflow.youtube_comments,
                        "access_token_for_channel",
                        return_value="access-token",
                    ),
                    patch.object(youtube_publisher, "start_resumable_upload") as start,
                    patch.object(youtube_publisher, "upload_video_resumable") as upload,
                    patch.object(youtube_publisher, "upload_thumbnail"),
                    patch.object(youtube_publisher, "find_caption_track", return_value="caption"),
                    patch.object(youtube_publisher, "upload_caption", return_value={"id": "caption"}),
                ):
                    resumed = youtube_publish_workflow.execute_publish_job(
                        database.get_system_job(job["id"]),
                        progress=lambda *_values: None,
                        cancel_check=lambda: None,
                        resolve_default_channel_id=lambda _version: "",
                        thumbnails_dir=self.thumbnails_dir,
                    )
                start.assert_not_called()
                upload.assert_not_called()
                self.assertEqual(resumed["youtube_video_id"], "youtube-checkpoint")

        publications = database.list_video_publications(job["video_id"])
        self.assertEqual(len(publications), 1)

    def test_resume_rejects_artifact_content_changed_after_snapshot(self):
        job, _channel = self._create_publish_job(schedule=False)
        result, _events, _patches = self._execute_with_fake_youtube(
            job, video_id="youtube-artifact-snapshot"
        )
        workflow = database.get_youtube_publish_workflow(result["workflow_id"])
        artifact = database.get_video_artifact(workflow["artifact_id"])
        Path(artifact["path"]).write_bytes(b"changed-video-content")

        with self.assertRaises(
            youtube_publish_workflow.PublishConfigurationRequired
        ) as raised:
            youtube_publish_workflow.execute_publish_job(
                database.get_system_job(job["id"]),
                progress=lambda *_values: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _version: "",
                thumbnails_dir=self.thumbnails_dir,
            )
        self.assertIn("final_mp4", raised.exception.missing_configuration)

    def test_two_workflows_cannot_reserve_the_same_publication_slot(self):
        first_job, channel = self._create_publish_job(schedule=True)
        first_workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=first_job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(first_job.get("payload") or {})["artifact_id"],
            snapshot=(first_job.get("payload") or {})["snapshot"],
            system_job_id=first_job["id"],
        )
        second_video = database.save_video(
            "https://youtube.com/watch?v=second-slot",
            "Second",
            "Transcript",
            "Script",
        )
        second_path = self.root / "second.mp4"
        second_path.write_bytes(b"second")
        second_artifact = database.upsert_video_artifact(
            video_id=second_video,
            artifact_type="final_mp4",
            path=str(second_path),
            content_hash="second-hash",
            status="ready",
            size_bytes=second_path.stat().st_size,
        )
        second_workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=second_video,
            youtube_channel_id=channel["id"],
            artifact_id=second_artifact["id"],
            snapshot=(first_job.get("payload") or {})["snapshot"],
        )

        first_slot = database.reserve_youtube_publication_slot(first_workflow["id"])
        second_slot = database.reserve_youtube_publication_slot(second_workflow["id"])

        self.assertNotEqual(first_slot, second_slot)

    def test_publication_and_its_workflow_count_once_toward_daily_limit(self):
        first_job, channel = self._create_publish_job(schedule=True)
        database.update_youtube_channel(
            channel["id"],
            publication_timezone="UTC",
            publication_slots_json=json.dumps(
                [{"day": 0, "time": "10:00"}, {"day": 0, "time": "11:00"}]
            ),
            publication_daily_limit=2,
            publication_lead_minutes=1,
        )
        first_workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=first_job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(first_job.get("payload") or {})["artifact_id"],
            snapshot=(first_job.get("payload") or {})["snapshot"],
            system_job_id=first_job["id"],
        )
        fixed_now = dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.timezone.utc)
        first_slot = database.reserve_youtube_publication_slot(
            first_workflow["id"], now_utc=fixed_now
        )
        database.save_video_publication(
            video_id=first_job["video_id"],
            youtube_channel_id=channel["id"],
            youtube_video_id="daily-limit-first",
            published_url="https://www.youtube.com/watch?v=daily-limit-first",
            published_at=first_slot,
        )

        second_video = database.save_video(
            "https://youtube.com/watch?v=daily-limit-second",
            "Second daily slot",
            "Transcript",
            "Script",
        )
        second_path = self.root / "daily-limit-second.mp4"
        second_path.write_bytes(b"second")
        second_artifact = database.upsert_video_artifact(
            video_id=second_video,
            artifact_type="final_mp4",
            path=str(second_path),
            content_hash="daily-limit-second-hash",
            status="ready",
            size_bytes=second_path.stat().st_size,
        )
        second_workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=second_video,
            youtube_channel_id=channel["id"],
            artifact_id=second_artifact["id"],
            snapshot=(first_job.get("payload") or {})["snapshot"],
        )

        second_slot = database.reserve_youtube_publication_slot(
            second_workflow["id"], now_utc=fixed_now
        )

        self.assertEqual(first_slot, "2026-09-21T10:00:00+00:00")
        self.assertEqual(second_slot, "2026-09-21T11:00:00+00:00")

    def test_prompt_readiness_is_generic_for_multiple_channels(self):
        for index, schedule in enumerate((False, True), start=1):
            with self.subTest(channel=index, schedule=schedule):
                channel = database.save_youtube_channel(
                    channel_id=f"UC-ready-{index}",
                    title=f"Ready {index}",
                    access_token_encrypted=f"encrypted-{index}",
                    gpm_profile_id=f"profile-{index}",
                    gpm_proxy_info=f"127.0.0.1:{9000 + index}:user:password",
                )
                database.update_youtube_channel(
                    channel["id"],
                    publication_timezone="UTC",
                    publication_slots_json=json.dumps(
                        [{"day": 0, "time": "10:00"}]
                    ),
                    publication_daily_limit=1,
                    publication_lead_minutes=60,
                    public_upload_verified=int(schedule),
                )
                readiness = (
                    youtube_publish_workflow.evaluate_prompt_publish_readiness(
                        {
                            "default_youtube_channel_id": channel["channel_id"],
                            "pipeline": {
                                "youtube_upload": True,
                                "youtube_schedule": schedule,
                            },
                            "publishing_settings": {"made_for_kids": False},
                        }
                    )
                )
                self.assertTrue(readiness["ready"])
                self.assertEqual(readiness["missing_configuration"], [])

    def test_processing_pending_does_not_reserve_a_schedule_slot(self):
        job, _channel = self._create_publish_job(schedule=True)
        with (
            patch.object(
                youtube_publish_workflow.youtube_comments,
                "access_token_for_channel",
                return_value="access-token",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "encrypt_secret",
                side_effect=lambda value: f"encrypted:{value}",
            ),
            patch.object(
                youtube_publisher,
                "start_resumable_upload",
                return_value="https://www.googleapis.com/upload/session",
            ),
            patch.object(
                youtube_publisher,
                "upload_video_resumable",
                return_value={"id": "youtube-processing"},
            ),
            patch.object(youtube_publisher, "upload_thumbnail"),
            patch.object(
                youtube_publisher, "find_caption_track", return_value="caption"
            ),
            patch.object(
                youtube_publisher,
                "require_processing_succeeded",
                side_effect=youtube_publisher.YouTubeProcessingPending("pending"),
            ),
        ):
            with self.assertRaises(youtube_publisher.YouTubeProcessingPending):
                youtube_publish_workflow.execute_publish_job(
                    job,
                    progress=lambda *_values: None,
                    cancel_check=lambda: None,
                    resolve_default_channel_id=lambda _version: "",
                    thumbnails_dir=self.thumbnails_dir,
                )

        workflow = database.get_youtube_publish_workflow_by_job(job["id"])
        publication = database.get_video_publication_by_youtube_id(
            "youtube-processing"
        )
        self.assertEqual(workflow["stage"], "processing")
        self.assertEqual(publication["processing_status"], "processing")
        self.assertEqual(
            database.list_channel_schedule_reservations(
                workflow["youtube_channel_id"]
            ),
            [],
        )

    def test_readiness_rejects_gpm_profile_shared_by_two_channels(self):
        first = database.save_youtube_channel(
            channel_id="UC-shared-first",
            title="Shared first",
            access_token_encrypted="encrypted-first",
            gpm_profile_id="shared-profile",
            gpm_proxy_info="127.0.0.1:9001:user:password",
        )
        database.save_youtube_channel(
            channel_id="UC-shared-second",
            title="Shared second",
            access_token_encrypted="encrypted-second",
            gpm_profile_id="shared-profile",
            gpm_proxy_info="127.0.0.1:9002:user:password",
        )
        readiness = youtube_publish_workflow.evaluate_prompt_publish_readiness(
            {
                "default_youtube_channel_id": first["channel_id"],
                "pipeline": {"youtube_upload": True, "youtube_schedule": False},
                "publishing_settings": {"made_for_kids": False},
            }
        )
        self.assertFalse(readiness["ready"])
        self.assertIn(
            "gpm_profile_exclusive", readiness["missing_configuration"]
        )

    def test_stale_reservation_is_released_and_reselected(self):
        job, channel = self._create_publish_job(schedule=True)
        database.update_youtube_channel(
            channel["id"],
            publication_timezone="UTC",
            publication_slots_json=json.dumps(
                [{"day": 0, "time": "10:00"}, {"day": 0, "time": "11:00"}]
            ),
            publication_daily_limit=2,
            publication_lead_minutes=60,
        )
        workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(job.get("payload") or {})["artifact_id"],
            snapshot=(job.get("payload") or {})["snapshot"],
            system_job_id=job["id"],
        )
        first = database.reserve_youtube_publication_slot(
            workflow["id"],
            now_utc=dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.timezone.utc),
        )
        second = database.reserve_youtube_publication_slot(
            workflow["id"],
            now_utc=dt.datetime(2026, 9, 21, 9, 30, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(first, "2026-09-21T10:00:00+00:00")
        self.assertEqual(second, "2026-09-21T11:00:00+00:00")

    def test_concurrent_workflows_reserve_distinct_slots_atomically(self):
        first_job, channel = self._create_publish_job(schedule=True)
        database.update_youtube_channel(
            channel["id"],
            publication_timezone="UTC",
            publication_slots_json=json.dumps(
                [{"day": 0, "time": "10:00"}, {"day": 0, "time": "11:00"}]
            ),
            publication_daily_limit=2,
            publication_lead_minutes=1,
        )
        first_workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=first_job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(first_job.get("payload") or {})["artifact_id"],
            snapshot=(first_job.get("payload") or {})["snapshot"],
        )
        second_video = database.save_video(
            "https://youtube.com/watch?v=concurrent-second",
            "Concurrent second",
            "Transcript",
            "Script",
        )
        second_path = self.root / "concurrent-second.mp4"
        second_path.write_bytes(b"second")
        second_artifact = database.upsert_video_artifact(
            video_id=second_video,
            artifact_type="final_mp4",
            path=str(second_path),
            content_hash="concurrent-second-hash",
            status="ready",
            size_bytes=second_path.stat().st_size,
        )
        second_workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=second_video,
            youtube_channel_id=channel["id"],
            artifact_id=second_artifact["id"],
            snapshot=(first_job.get("payload") or {})["snapshot"],
        )
        fixed_now = dt.datetime(2026, 9, 21, 9, 0, tzinfo=dt.timezone.utc)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def reserve(workflow_id):
            try:
                barrier.wait()
                results.append(
                    database.reserve_youtube_publication_slot(
                        workflow_id, now_utc=fixed_now
                    )
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=reserve, args=(workflow["id"],))
            for workflow in (first_workflow, second_workflow)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 2)

    def test_cancel_releases_reserved_slot(self):
        job, channel = self._create_publish_job(schedule=True)
        workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(job.get("payload") or {})["artifact_id"],
            snapshot=(job.get("payload") or {})["snapshot"],
        )
        database.reserve_youtube_publication_slot(workflow["id"])
        database.cancel_youtube_publication_reservation(workflow["id"])
        reservation = database.get_channel_schedule_reservation(workflow["id"])
        self.assertEqual(reservation["status"], "canceled")
        self.assertEqual(
            database.get_youtube_publish_workflow(workflow["id"])["scheduled_at"],
            "",
        )

    def test_schedule_payload_preserves_mutable_status_fields(self):
        publish_at = "2099-09-21T10:00:00+00:00"
        preserved = {
            "selfDeclaredMadeForKids": True,
            "containsSyntheticMedia": True,
            "license": "youtube",
            "embeddable": False,
            "publicStatsViewable": False,
        }

        def fake_api(_url, _token, **kwargs):
            status = kwargs["payload"]["status"]
            self.assertEqual(status["publishAt"], publish_at)
            for key, value in preserved.items():
                self.assertEqual(status[key], value)
            return {
                "id": "youtube-payload",
                "status": {
                    **status,
                    "privacyStatus": "private",
                    "publishAt": "2099-09-21T10:00:00Z",
                },
            }

        with patch.object(youtube_publisher, "_api_json", side_effect=fake_api):
            result = youtube_publisher.schedule_video(
                "token",
                "youtube-payload",
                publish_at,
                preserved_status=preserved,
                proxy="127.0.0.1:8899:user:password",
            )
        self.assertEqual(result["id"], "youtube-payload")

    def test_schedule_rejects_past_publish_at_before_request(self):
        with patch.object(youtube_publisher, "_api_json") as api_call:
            with self.assertRaises(youtube_publisher.YouTubePublishError):
                youtube_publisher.schedule_video(
                    "token",
                    "youtube-past",
                    "2020-01-01T00:00:00+00:00",
                    proxy="127.0.0.1:8899:user:password",
                )
        api_call.assert_not_called()

    def test_dst_nonexistent_slot_is_skipped(self):
        selected = publication_scheduler.find_next_publication_slot(
            timezone_name="Europe/Berlin",
            slots=[{"day": 6, "time": "02:30"}],
            daily_limit=1,
            lead_minutes=1,
            occupied_utc=[],
            now_utc=dt.datetime(2026, 3, 28, 0, 0, tzinfo=dt.timezone.utc),
        )
        selected_utc = dt.datetime.fromisoformat(selected)
        self.assertEqual(selected_utc.date(), dt.date(2026, 4, 5))

    def test_find_next_publication_slot_deduplicates_iso_formats_and_selects_same_day_slot(self):
        slots = [
            {"day": 1, "time": "11:00"},
            {"day": 1, "time": "18:00"},
        ]
        # 2026-10-06 is Tuesday (day 1).
        # Both formats represent 11:00 VN (04:00 UTC).
        occupied = [
            "2026-10-06T04:00:00+00:00",
            "2026-10-06T04:00:00Z",
            "2026-10-06T04:00:15Z",
        ]
        selected = publication_scheduler.find_next_publication_slot(
            timezone_name="Asia/Ho_Chi_Minh",
            slots=slots,
            daily_limit=2,
            lead_minutes=60,
            occupied_utc=occupied,
            now_utc=dt.datetime(2026, 10, 5, 0, 0, tzinfo=dt.timezone.utc),
        )
        # Should correctly pick 18:00 VN on 2026-10-06 (11:00 UTC) instead of skipping to next day
        selected_utc = dt.datetime.fromisoformat(selected)
        self.assertEqual(selected_utc, dt.datetime(2026, 10, 6, 11, 0, tzinfo=dt.timezone.utc))

    def test_processing_poll_is_not_limited_by_network_retry_budget(self):
        job, _channel = self._create_publish_job(schedule=True)
        pending = youtube_publisher.YouTubeProcessingPending(
            "processing", delay_seconds=1
        )
        for _index in range(5):
            self.assertTrue(main._schedule_youtube_processing_poll(job, pending))
        current = database.get_system_job(job["id"])
        self.assertEqual(current["recovery_count"], 5)
        self.assertEqual(current["result"]["processing_poll_count"], 5)
        self.assertEqual(current["result"]["publish_stage"], "processing")
        self.assertTrue(main._schedule_youtube_publish_recovery(job, "network-1"))
        self.assertTrue(main._schedule_youtube_publish_recovery(job, "network-2"))
        self.assertTrue(main._schedule_youtube_publish_recovery(job, "network-3"))
        self.assertFalse(main._schedule_youtube_publish_recovery(job, "network-4"))

    def test_channel_sync_reconciles_scheduled_publication_to_public(self):
        job, channel = self._create_publish_job(schedule=False)
        publication = database.save_video_publication(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            youtube_video_id="youtube-public-now",
            published_url="https://www.youtube.com/watch?v=youtube-public-now",
            privacy_status="private",
            processing_status="succeeded",
            scheduled_at="2026-09-21T10:00:00+00:00",
        )
        with patch.object(
            main.youtube_comments,
            "get_videos_details",
            return_value=[
                {
                    "youtube_video_id": "youtube-public-now",
                    "channel_id": channel["channel_id"],
                    "privacy_status": "public",
                    "scheduled_publish_at": "",
                    "published_at": "2026-09-21T10:00:00Z",
                }
            ],
        ):
            reconciled = main._reconcile_channel_publication_states(
                channel, "access-token"
            )
        updated = database.get_video_publication(publication["id"])
        self.assertEqual(reconciled, 1)
        self.assertEqual(updated["privacy_status"], "public")
        self.assertEqual(database.get_video(job["video_id"])["is_published"], 1)

    def test_execute_publish_job_resolves_dynamic_publishing_settings(self):
        job, _channel = self._create_publish_job(schedule=False)
        payload = dict(job["payload"])
        payload["snapshot"]["publishing_settings"]["made_for_kids"] = None
        database.update_system_job(job["id"], payload_json=payload)
        stale_job = database.get_system_job(job["id"])

        # Without resolve_publishing_settings callback, it raises PublishConfigurationRequired
        with self.assertRaises(youtube_publish_workflow.PublishConfigurationRequired) as ctx:
            youtube_publish_workflow.execute_publish_job(
                stale_job,
                progress=lambda *args: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _ver: "",
                thumbnails_dir=self.thumbnails_dir,
            )
        self.assertIn("made_for_kids", ctx.exception.missing_configuration)

        # With resolve_publishing_settings callback returning updated settings, preflight succeeds
        with patch.object(
            youtube_publish_workflow.youtube_comments,
            "access_token_for_channel",
            return_value="access-token",
        ), patch.object(
            youtube_publish_workflow.secret_store,
            "encrypt_secret",
            side_effect=lambda value: f"encrypted:{value}",
        ), patch.object(
            youtube_publish_workflow.secret_store,
            "decrypt_secret",
            side_effect=lambda value: str(value).removeprefix("encrypted:"),
        ), patch.object(
            youtube_publisher,
            "start_resumable_upload",
            return_value="https://www.googleapis.com/upload/session",
        ), patch.object(
            youtube_publisher,
            "upload_video_resumable",
            return_value={"id": "dyn-yt-id"},
        ), patch.object(
            youtube_publisher,
            "upload_thumbnail",
        ), patch.object(
            youtube_publisher,
            "find_caption_track",
            return_value="",
        ), patch.object(
            youtube_publisher,
            "upload_caption",
            return_value={"id": "caption-id"},
        ):
            result = youtube_publish_workflow.execute_publish_job(
                stale_job,
                progress=lambda *args: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _ver: "",
                resolve_publishing_settings=lambda _ver: {"upload_method": "api", "made_for_kids": False, "category_id": "25"},
                thumbnails_dir=self.thumbnails_dir,
            )
        self.assertEqual(result["youtube_video_id"], "dyn-yt-id")
        self.assertEqual(result["stage"], "uploaded_private")

    def test_build_upload_metadata_includes_audio_and_text_languages(self):
        video = {
            "title": "Tiêu đề video mẫu",
            "description": "Mô tả video mẫu",
            "generated_script": "",
        }
        metadata = youtube_publisher.build_upload_metadata(
            video,
            {"language": "vi", "category_id": "22", "made_for_kids": False},
        )
        self.assertEqual(metadata["snippet"]["defaultLanguage"], "vi")
        self.assertEqual(metadata["snippet"]["defaultAudioLanguage"], "vi")

    def test_browser_upload_private_executes_and_persists_publication(self):
        job, channel = self._create_publish_job(schedule=False, upload_method="browser")
        fake_browser_result = {
            "youtube_video_id": "browser-yt-vid-123",
            "published_url": "https://www.youtube.com/watch?v=browser-yt-vid-123",
            "status": "uploaded_private",
            "scheduled_at": "",
            "title": "Publish title",
            "caption_locator": "browser:vi:captions.srt",
        }
        async def fake_browser_upload(**kwargs):
            kwargs["progress"]("Test progress", "uploading", 50)
            kwargs["persist_video_id"]("browser-yt-vid-123")
            return fake_browser_result

        with patch.object(
            youtube_publish_workflow.browser_youtube_uploader,
            "upload_video_via_browser",
            side_effect=fake_browser_upload,
        ), patch.object(
            youtube_publish_workflow.youtube_comments,
            "access_token_for_channel",
            return_value="access-token",
        ):
            events = []
            result = youtube_publish_workflow.execute_publish_job(
                job,
                progress=lambda *args: events.append(args),
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _ver: "",
                thumbnails_dir=self.thumbnails_dir,
            )

        self.assertEqual(result["youtube_video_id"], "browser-yt-vid-123")
        self.assertEqual(result["stage"], "uploaded_private")
        publication = database.get_video_publication_by_youtube_id("browser-yt-vid-123")
        self.assertIsNotNone(publication)
        self.assertEqual(publication["privacy_status"], "private")

    def test_browser_without_cdp_pauses_as_recoverable_waiting_state(self):
        job, _channel = self._create_publish_job(
            schedule=False,
            upload_method="browser",
        )
        readiness = {
            "profile_id": "gpm-profile",
            "profile_name": "GPM Profile",
            "state": "running_without_cdp",
            "automation_ready": False,
            "requires_user_action": True,
        }
        blocked = youtube_publish_workflow.channel_scanner_service.BrowserAutomationBlocked(
            readiness
        )

        with patch.object(
            youtube_publish_workflow.channel_scanner_service,
            "ensure_profile_automation_ready",
            side_effect=blocked,
        ), patch.object(
            youtube_publish_workflow.browser_youtube_uploader,
            "upload_video_via_browser",
        ) as browser_upload:
            with self.assertRaises(
                youtube_publish_workflow.channel_scanner_service.BrowserAutomationBlocked
            ) as error_context:
                youtube_publish_workflow.execute_publish_job(
                    job,
                    progress=lambda *_values: None,
                    cancel_check=lambda: None,
                    resolve_default_channel_id=lambda _version: "",
                    thumbnails_dir=self.thumbnails_dir,
                )

        browser_upload.assert_not_called()
        main._handle_production_job_error(job, error_context.exception)
        paused_job = database.get_system_job(job["id"])
        workflow = database.get_youtube_publish_workflow_by_job(job["id"])
        video = database.get_video(job["video_id"])
        self.assertEqual(paused_job["status"], "paused")
        self.assertEqual(paused_job["result"]["attention_required"], "youtube_browser_cdp")
        self.assertEqual(workflow["status"], "waiting_for_browser")
        self.assertEqual(workflow["stage"], "browser_preflight")
        self.assertEqual(video["publish_status"], "waiting_for_browser")

        previous_check_at = main._browser_waiting_last_check_at
        try:
            main._browser_waiting_last_check_at = 0.0
            with patch.object(
                main.channel_scanner_service,
                "inspect_profile_browser_readiness",
                return_value={
                    "state": "running_cdp_ready",
                    "automation_ready": True,
                    "busy": False,
                },
            ), patch.object(
                main.channel_scanner_service,
                "ensure_profile_automation_ready",
                return_value={
                    "state": "running_cdp_ready",
                    "automation_ready": True,
                },
            ):
                main._resume_browser_waiting_publish_jobs()
        finally:
            main._browser_waiting_last_check_at = previous_check_at

        resumed_job = database.get_system_job(job["id"])
        resumed_workflow = database.get_youtube_publish_workflow_by_job(job["id"])
        resumed_video = database.get_video(job["video_id"])
        self.assertEqual(resumed_job["status"], "queued")
        self.assertNotIn("attention_required", resumed_job["result"])
        self.assertEqual(resumed_workflow["status"], "reserved")
        self.assertEqual(resumed_workflow["stage"], "preflight")
        self.assertEqual(resumed_video["publish_status"], "queued")
        self.assertFalse(
            main._browser_workflow_is_safe_to_resume(
                {"upload_offset": 1, "youtube_video_id": "", "publication_id": 0}
            )
        )

    def test_legacy_no_cdp_failure_is_migrated_only_when_safe(self):
        job, channel = self._create_publish_job(
            schedule=False,
            upload_method="browser",
        )
        workflow, _created = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=job["payload"]["artifact_id"],
            snapshot=job["payload"]["snapshot"],
            system_job_id=job["id"],
        )
        legacy_error = (
            "Profile GPM đang chạy nhưng không có cổng CDP. "
            "Hãy đóng profile thủ công."
        )
        database.update_youtube_publish_workflow(
            workflow["id"],
            status="failed_permanent",
            error=legacy_error,
        )
        database.update_system_job(
            job["id"],
            status="error",
            error=legacy_error,
        )

        recovered_count = main._recover_legacy_browser_cdp_blocked_jobs()

        recovered_job = database.get_system_job(job["id"])
        recovered_workflow = database.get_youtube_publish_workflow(workflow["id"])
        self.assertEqual(recovered_count, 1)
        self.assertEqual(recovered_job["status"], "paused")
        self.assertEqual(recovered_workflow["status"], "waiting_for_browser")
        self.assertEqual(recovered_workflow["stage"], "browser_preflight")

    def test_browser_upload_schedule_executes_and_reserves_slot(self):
        job, channel = self._create_publish_job(schedule=True, upload_method="browser")
        fake_browser_result = {
            "youtube_video_id": "browser-yt-sched-456",
            "published_url": "https://www.youtube.com/watch?v=browser-yt-sched-456",
            "status": "scheduled",
            "scheduled_at": "2026-09-27T23:55:00+07:00",
            "title": "Publish title",
            "caption_locator": "browser:vi:captions.srt",
            "schedule_verified": True,
        }
        async def fake_browser_upload(**kwargs):
            kwargs["persist_video_id"]("browser-yt-sched-456")
            return fake_browser_result

        with patch.object(
            youtube_publish_workflow.browser_youtube_uploader,
            "upload_video_via_browser",
            side_effect=fake_browser_upload,
        ), patch.object(
            youtube_publish_workflow.youtube_comments,
            "access_token_for_channel",
            return_value="access-token",
        ):
            result = youtube_publish_workflow.execute_publish_job(
                job,
                progress=lambda *args: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _ver: "",
                thumbnails_dir=self.thumbnails_dir,
            )

        self.assertEqual(result["youtube_video_id"], "browser-yt-sched-456")
        self.assertEqual(result["stage"], "scheduled")
        self.assertTrue(bool(result["scheduled_at"]))

    def test_browser_upload_passes_category_id(self):
        job, channel = self._create_publish_job(schedule=False, upload_method="browser")
        # Set category_id in snapshot publishing settings
        job["payload"]["snapshot"]["publishing_settings"]["category_id"] = "25"
        captured_kwargs = {}

        async def fake_browser_upload(**kwargs):
            captured_kwargs.update(kwargs)
            kwargs["persist_video_id"]("browser-yt-cat-25")
            kwargs["persist_checkpoint"](
                "monetization_detected",
                {
                    "monetization": {
                        "capability": "unavailable",
                        "evidence": ["complete_non_monetized_topology"],
                    },
                    "video_monetization_state": "not_applicable",
                    "ad_suitability_state": "not_applicable",
                },
            )
            return {
                "youtube_video_id": "browser-yt-cat-25",
                "published_url": "https://www.youtube.com/watch?v=browser-yt-cat-25",
                "status": "uploaded_private",
                "scheduled_at": "",
                "title": "Publish title",
                "caption_locator": "browser:vi:captions.srt",
            }

        with patch.object(
            youtube_publish_workflow.browser_youtube_uploader,
            "upload_video_via_browser",
            side_effect=fake_browser_upload,
        ), patch.object(
            youtube_publish_workflow.youtube_comments,
            "access_token_for_channel",
            return_value="access-token",
        ):
            youtube_publish_workflow.execute_publish_job(
                job,
                progress=lambda *args: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _ver: "",
                thumbnails_dir=self.thumbnails_dir,
            )

        self.assertEqual(captured_kwargs.get("category_id"), "25")
        self.assertEqual(captured_kwargs.get("publication_timezone"), "Asia/Ho_Chi_Minh")
        self.assertEqual(captured_kwargs.get("expected_channel_id"), channel["channel_id"])
        self.assertEqual(
            captured_kwargs.get("publishing_settings", {}).get("monetization_mode"),
            "auto_enable_if_available",
        )
        workflow = database.get_youtube_publish_workflow_by_job(job["id"])
        snapshot = workflow["snapshot"]
        self.assertEqual(snapshot["monetization_capability"], "unavailable")
        self.assertEqual(
            snapshot["monetization_detection_evidence"],
            ["complete_non_monetized_topology"],
        )
        self.assertEqual(snapshot["video_monetization_state"], "not_applicable")

    def test_browser_resume_passes_existing_video_id_without_new_identity(self):
        job, channel = self._create_publish_job(schedule=True, upload_method="browser")
        workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(job.get("payload") or {})["artifact_id"],
            snapshot=(job.get("payload") or {})["snapshot"],
            system_job_id=job["id"],
        )
        database.update_youtube_publish_workflow(
            workflow["id"],
            youtube_video_id="existing-browser-video",
            stage="draft_created",
            status="error",
        )
        payload = dict(job["payload"])
        payload["workflow_id"] = workflow["id"]
        database.update_system_job(job["id"], payload_json=payload)
        captured_kwargs = {}

        async def fake_browser_upload(**kwargs):
            captured_kwargs.update(kwargs)
            return {
                "youtube_video_id": "existing-browser-video",
                "published_url": "https://www.youtube.com/watch?v=existing-browser-video",
                "status": "scheduled",
                "scheduled_at": kwargs["schedule_at"],
                "title": "Publish title",
                "caption_locator": "browser:vi:captions.srt",
                "schedule_verified": True,
                "resumed": True,
            }

        with patch.object(
            youtube_publish_workflow.browser_youtube_uploader,
            "upload_video_via_browser",
            side_effect=fake_browser_upload,
        ):
            youtube_publish_workflow.execute_publish_job(
                database.get_system_job(job["id"]),
                progress=lambda *args: None,
                cancel_check=lambda: None,
                resolve_default_channel_id=lambda _ver: "",
                thumbnails_dir=self.thumbnails_dir,
            )

        self.assertEqual(
            captured_kwargs.get("existing_video_id"), "existing-browser-video"
        )

    def test_browser_resume_blocks_duplicate_when_remote_identity_is_unknown(self):
        job, channel = self._create_publish_job(schedule=False, upload_method="browser")
        snapshot = dict((job.get("payload") or {})["snapshot"])
        snapshot["browser_upload_state"] = {
            "checkpoint": "needs_review",
            "remote_identity_unknown": True,
            "file_selected": True,
        }
        workflow, _ = database.reserve_youtube_publish_workflow(
            video_id=job["video_id"],
            youtube_channel_id=channel["id"],
            artifact_id=(job.get("payload") or {})["artifact_id"],
            snapshot=snapshot,
            system_job_id=job["id"],
        )
        database.update_youtube_publish_workflow(
            workflow["id"],
            stage="needs_review",
            status="needs_review",
        )
        payload = dict(job["payload"])
        payload["workflow_id"] = workflow["id"]
        database.update_system_job(job["id"], payload_json=payload)

        with patch.object(
            youtube_publish_workflow.browser_youtube_uploader,
            "upload_video_via_browser",
        ) as browser_upload:
            with self.assertRaises(
                youtube_publish_workflow.browser_youtube_uploader.BrowserUploadNeedsReview
            ):
                youtube_publish_workflow.execute_publish_job(
                    database.get_system_job(job["id"]),
                    progress=lambda *_values: None,
                    cancel_check=lambda: None,
                    resolve_default_channel_id=lambda _version: "",
                    thumbnails_dir=self.thumbnails_dir,
                )
        browser_upload.assert_not_called()

    def test_publish_video_schedule_endpoint(self):
        import asyncio

        # 1. Non-existent video raises 404
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.publish_video_schedule_endpoint(999999))
        self.assertEqual(ctx.exception.status_code, 404)

        # 2. Video without ready mp4 artifact raises 400
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=schedule-ep-test",
            "Schedule test title",
            "Transcript",
            "### [METADATA & QUIZ]\nMÔ TẢ VIDEO: Description",
            prompt_version="default",
        )
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main.publish_video_schedule_endpoint(video_id))
        self.assertEqual(ctx.exception.status_code, 400)

        # 3. Video with ready mp4 artifact successfully queues schedule publish job
        artifact = database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(self.root / "output.mp4"),
            content_hash="mp4-hash-test",
            status="ready",
        )
        with patch.object(main, "_kick_production_queue") as kick_mock:
            res = asyncio.run(main.publish_video_schedule_endpoint(video_id))
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "queued")
            kick_mock.assert_called_once()

        job = database.get_system_job(res["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["job_type"], "youtube_publish")
        self.assertEqual(job["payload"]["publish_mode"], "schedule")
        self.assertEqual(job["payload"]["artifact_id"], artifact["id"])
        self.assertEqual(job["payload"]["snapshot"]["publishing_settings"]["publish_mode"], "schedule")
        self.assertTrue(job["payload"]["snapshot"]["pipeline"]["youtube_upload"])
        self.assertTrue(job["payload"]["snapshot"]["pipeline"]["youtube_schedule"])

        video = database.get_video(video_id)
        self.assertEqual(video["publish_status"], "queued")
        self.assertEqual(video["current_stage"], "preflight")

    def test_prepare_publish_context_auto_recovers_missing_captions(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=auto-sub-test",
            "Auto Sub Title",
            "Transcript",
            "### [METADATA & QUIZ]\nMÔ TẢ VIDEO: Description\n\n### [THUMBNAIL CÓ CHỮ]\n[IMAGE_URL:/api/thumbnails/publish.png]\n\n### [THUMBNAIL KHÔNG CHỮ]\n[IMAGE_URL:/api/thumbnails/publish.png]",
            prompt_version="default",
        )
        mp4_path = self.root / "video_auto_sub.mp4"
        mp4_path.write_bytes(b"mp4-content")
        mp4_art = database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(mp4_path),
            content_hash="mp4-hash-sub",
            status="ready",
        )
        channel = database.save_youtube_channel(
            channel_id="UC-auto-sub-chan",
            title="Auto Sub Channel",
            access_token_encrypted="encrypted-access",
            token_expiry=(
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat(),
            gpm_profile_id="gpm-profile",
            gpm_proxy_info="127.0.0.1:8899:user:password",
        )
        audio_file = self.root / f"video_{video_id}_audio.mp3"
        audio_file.write_bytes(b"audio-mp3-bytes")
        database.upsert_audio_task(
            video_id=video_id,
            task_id="task-audio-1",
            status="completed",
            audio_url=f"http://127.0.0.1:8080/api/audio/{audio_file.name}",
            request_hash="audio-hash-1",
        )

        dummy_srt = self.root / f"video_{video_id}_auto.srt"
        dummy_srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nAuto sub\n", encoding="utf-8")

        def mock_create_srt(audio_p, vid_id, prog):
            database.upsert_video_artifact(
                video_id=vid_id,
                artifact_type="captions",
                path=str(dummy_srt),
                content_hash="sub-hash-auto",
                status="ready",
                mime_type="application/x-subrip",
                size_bytes=dummy_srt.stat().st_size,
            )
            return dummy_srt, "sub-hash-auto"

        job = database.create_system_job(
            job_id="job-auto-sub-1",
            job_type="youtube_publish",
            title="Publish Job Auto Sub",
            payload={
                "video_id": video_id,
                "artifact_id": mp4_art["id"],
                "snapshot": {
                    "publishing_settings": {"made_for_kids": False, "upload_captions": True},
                    "default_youtube_channel_id": channel["channel_id"],
                },
            },
        )

        with patch("auto_yt.services.video_production.create_srt", side_effect=mock_create_srt), \
             patch.object(youtube_publish_workflow, "AUDIO_DIR", self.root):
            context = youtube_publish_workflow._build_preflight_context(
                job,
                resolve_default_channel_id=lambda v: channel["channel_id"],
                thumbnails_dir=self.thumbnails_dir,
            )
            self.assertIsNotNone(context["caption_artifact"])
            self.assertEqual(context["caption_artifact"]["status"], "ready")
            self.assertEqual(str(context["caption_path"]), str(dummy_srt))

    def test_prepare_publish_context_allows_missing_captions_if_upload_captions_disabled(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=no-sub-test",
            "No Sub Title",
            "Transcript",
            "### [METADATA & QUIZ]\nMÔ TẢ VIDEO: Description\n\n### [THUMBNAIL CÓ CHỮ]\n[IMAGE_URL:/api/thumbnails/publish.png]\n\n### [THUMBNAIL KHÔNG CHỮ]\n[IMAGE_URL:/api/thumbnails/publish.png]",
            prompt_version="default",
        )
        mp4_path = self.root / "video_no_sub.mp4"
        mp4_path.write_bytes(b"mp4-content")
        mp4_art = database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(mp4_path),
            content_hash="mp4-hash-nosub",
            status="ready",
        )
        channel = database.save_youtube_channel(
            channel_id="UC-no-sub-chan",
            title="No Sub Channel",
            access_token_encrypted="encrypted-access",
            token_expiry=(
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat(),
            gpm_profile_id="gpm-profile",
            gpm_proxy_info="127.0.0.1:8899:user:password",
        )
        job = database.create_system_job(
            job_id="job-no-sub-1",
            job_type="youtube_publish",
            title="Publish Job No Sub",
            payload={
                "video_id": video_id,
                "artifact_id": mp4_art["id"],
                "snapshot": {
                    "publishing_settings": {"made_for_kids": False, "upload_captions": False},
                    "default_youtube_channel_id": channel["channel_id"],
                },
            },
        )

        context = youtube_publish_workflow._build_preflight_context(
            job,
            resolve_default_channel_id=lambda v: channel["channel_id"],
            thumbnails_dir=self.thumbnails_dir,
        )
        self.assertIsNone(context["caption_artifact"])
        self.assertIsNone(context["caption_path"])


if __name__ == "__main__":
    unittest.main()
