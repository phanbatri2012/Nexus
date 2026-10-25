import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from nexus_engine.services import chatgpt_runtime
from nexus_engine.services import database as db
from nexus_engine.services import trust_builder_actions as actions
from nexus_engine.services import trust_builder_comments as comments
from nexus_engine.services import trust_builder_service as service
from nexus_engine.services.youtube_comments import extract_youtube_video_id


VIDEO_ID = "AbC_123-xyz"
PROMPT_VERSION = "channel-prompt"
CHAT_URL = "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"


class TestTrustBuilderCommentResolver(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "channel_db_id": 7,
            "youtube_channel_ucid": "UC_TRUST_TEST",
            "publication_timezone": "UTC",
        }
        self.prompt_versions = {
            PROMPT_VERSION: {
                "default_youtube_channel_id": "UC_TRUST_TEST",
                "chatgpt_project_url": "https://chatgpt.com/g/g-p-test/project",
            }
        }

    def test_extracts_supported_youtube_url_shapes(self):
        urls = (
            f"https://www.youtube.com/watch?v={VIDEO_ID}&t=10",
            f"https://youtu.be/{VIDEO_ID}?si=test",
            f"https://youtube.com/shorts/{VIDEO_ID}",
            f"https://m.youtube.com/live/{VIDEO_ID}?feature=share",
            f"https://youtube.com/embed/{VIDEO_ID}",
        )
        self.assertEqual(
            [extract_youtube_video_id(url) for url in urls],
            [VIDEO_ID] * len(urls),
        )

    def test_older_same_channel_record_with_chat_beats_newer_without_chat(self):
        publications = [
            {
                "id": 20,
                "video_id": 200,
                "youtube_channel_id": 7,
                "youtube_video_id": VIDEO_ID,
                "chat_url": "",
                "prompt_version": PROMPT_VERSION,
                "video_status": db.VIDEO_STATUS_ACTIVE,
            },
            {
                "id": 10,
                "video_id": 100,
                "youtube_channel_id": 7,
                "youtube_video_id": VIDEO_ID,
                "chat_url": CHAT_URL,
                "prompt_version": PROMPT_VERSION,
                "video_status": db.VIDEO_STATUS_ACTIVE,
            },
        ]
        with patch.object(
            comments.db, "list_video_publication_identities", return_value=publications
        ), patch.object(
            comments.db, "list_video_identity_candidates", return_value=[]
        ), patch.object(comments.db, "list_trust_video_chats", return_value=[]):
            result = comments.resolve_video_chat(
                VIDEO_ID,
                self.plan,
                prompt_versions=self.prompt_versions,
            )

        self.assertEqual(result["status"], "existing_chat")
        self.assertEqual(result["chat_url"], CHAT_URL)
        self.assertEqual(result["linked_video_id"], 100)

    def test_does_not_match_title_or_inactive_video(self):
        sources = [
            {
                "id": 1,
                "url": "https://youtube.com/watch?v=another-id",
                "title": "Same title",
                "chat_url": CHAT_URL,
                "prompt_version": PROMPT_VERSION,
                "video_status": db.VIDEO_STATUS_ACTIVE,
            },
            {
                "id": 2,
                "url": f"https://youtube.com/watch?v={VIDEO_ID}",
                "title": "Same title",
                "chat_url": CHAT_URL,
                "prompt_version": PROMPT_VERSION,
                "video_status": "error",
            },
        ]
        with patch.object(
            comments.db, "list_video_publication_identities", return_value=[]
        ), patch.object(
            comments.db, "list_video_identity_candidates", return_value=sources
        ), patch.object(comments.db, "list_trust_video_chats", return_value=[]):
            result = comments.resolve_video_chat(
                VIDEO_ID,
                self.plan,
                prompt_versions=self.prompt_versions,
            )

        self.assertEqual(result["status"], "new_chat")
        self.assertFalse(result["chat_reused"])

    def test_wrong_channel_publication_cannot_reenter_as_source_candidate(self):
        publications = [
            {
                "id": 8,
                "video_id": 88,
                "youtube_channel_id": 999,
                "youtube_video_id": VIDEO_ID,
                "chat_url": CHAT_URL,
                "prompt_version": PROMPT_VERSION,
                "video_status": db.VIDEO_STATUS_ACTIVE,
            }
        ]
        sources = [
            {
                "id": 88,
                "url": f"https://youtube.com/watch?v={VIDEO_ID}",
                "chat_url": CHAT_URL,
                "prompt_version": PROMPT_VERSION,
                "video_status": db.VIDEO_STATUS_ACTIVE,
            }
        ]
        with patch.object(
            comments.db, "list_video_publication_identities", return_value=publications
        ), patch.object(
            comments.db, "list_video_identity_candidates", return_value=sources
        ), patch.object(comments.db, "list_trust_video_chats", return_value=[]):
            result = comments.resolve_video_chat(
                VIDEO_ID,
                self.plan,
                prompt_versions=self.prompt_versions,
            )

        self.assertEqual(result["status"], "new_chat")
        self.assertFalse(result["chat_reused"])

    def test_missing_or_multiple_channel_prompts_fail_closed(self):
        with patch.object(
            comments.db, "list_video_publication_identities", return_value=[]
        ), patch.object(
            comments.db, "list_video_identity_candidates", return_value=[]
        ), patch.object(comments.db, "list_trust_video_chats", return_value=[]):
            missing = comments.resolve_video_chat(
                VIDEO_ID, self.plan, prompt_versions={}
            )
            ambiguous = comments.resolve_video_chat(
                VIDEO_ID,
                self.plan,
                prompt_versions={
                    "one": {"default_youtube_channel_id": "UC_TRUST_TEST"},
                    "two": {"default_youtube_channel_id": "UC_TRUST_TEST"},
                },
            )

        self.assertEqual(missing["skip_reason"], "prompt_version_missing")
        self.assertEqual(ambiguous["skip_reason"], "prompt_version_ambiguous")


class TestTrustBuilderContextAndGate(unittest.TestCase):
    def test_page_collector_limits_transcript_to_watched_range(self):
        page = Mock()
        page.evaluate = AsyncMock(
            side_effect=[
                False,
                {
                    "youtube_video_id": VIDEO_ID,
                    "title": "Battery storage",
                    "description": "Description",
                    "chapters": [],
                    "top_comments": [f"Comment {index}" for index in range(8)],
                    "transcript": [
                        {
                            "timestamp": f"{index * 10 // 60}:{index * 10 % 60:02d}",
                            "text": f"Segment {index}",
                        }
                        for index in range(16)
                    ],
                },
            ]
        )
        with patch.object(actions.asyncio, "sleep", new=AsyncMock()):
            result = asyncio.run(
                actions.action_collect_video_context(
                    page,
                    watched_from_seconds=30,
                    watched_until_seconds=100,
                )
            )

        self.assertLessEqual(len(result["transcript_segments"]), 8)
        self.assertNotIn("Segment 0", result["transcript_segments"])
        self.assertNotIn("Segment 1", result["transcript_segments"])
        self.assertNotIn("Segment 12", result["transcript_segments"])
        self.assertEqual(len(result["top_comments"]), 5)

    def test_context_budget_and_segment_limits(self):
        transcript = "\n".join(
            f"Segment {index} explains a concrete battery storage detail."
            for index in range(40)
        )
        packet = comments.build_context_packet(
            youtube_video_id=VIDEO_ID,
            page_context={
                "title": "Battery storage overview",
                "description": "Page description " * 300,
                "chapters": [f"Chapter {index}" for index in range(30)],
                "transcript_segments": ["page fallback"],
                "top_comments": [f"Top comment {index}" for index in range(20)],
            },
            stored_video={
                "title": "Stored title",
                "description": "Stored description " * 300,
                "transcript": transcript,
            },
        )

        self.assertLessEqual(packet["context_chars"], comments.MAX_CONTEXT_CHARS)
        self.assertLessEqual(
            len(packet["transcript_segments"]), comments.MAX_TRANSCRIPT_SEGMENTS
        )
        self.assertLessEqual(len(packet["top_comments"]), comments.MAX_TOP_COMMENTS)
        self.assertIn("Segment 39", packet["trusted_evidence_text"])

    def _context(self):
        evidence = "solar battery storage reduces evening grid demand"
        return {
            "trusted_evidence_text": evidence,
            "top_comments": ["This repeats a common top comment exactly"],
        }

    def _valid_payload(self):
        return {
            "comment": (
                "The point that solar battery storage reduces evening grid demand "
                "makes this tradeoff much clearer."
            ),
            "language": "en",
            "evidence": "solar battery storage reduces evening grid demand",
            "anchor_terms": ["solar battery storage"],
        }

    def test_quality_gate_accepts_grounded_unique_comment(self):
        result = comments.validate_comment_draft(
            self._valid_payload(), self._context(), expected_language="en"
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["quality_score"], 1.0)

    def test_quality_gate_rejects_missing_evidence_language_url_and_duplicate(self):
        payload = self._valid_payload()
        payload.update(
            {
                "comment": "https://example.com This repeats a common top comment exactly",
                "language": "vi",
                "evidence": "not in the trusted source",
                "anchor_terms": ["not in the trusted source"],
            }
        )
        context = self._context()
        context["top_comments"] = [payload["comment"]]
        result = comments.validate_comment_draft(payload, context, expected_language="en")
        self.assertFalse(result["valid"])
        self.assertIn("evidence_exact", result["errors"])
        self.assertIn("language_match", result["errors"])
        self.assertIn("safe_style", result["errors"])
        self.assertIn("unique", result["errors"])

    def test_invalid_json_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            comments.parse_comment_response("not json")

    def test_language_gate_checks_comment_text_not_only_declared_code(self):
        payload = self._valid_payload()
        payload["comment"] = (
            "Điểm solar battery storage trong phần này được giải thích rất rõ ràng "
            "và giúp nội dung dễ hiểu hơn nhiều."
        )
        result = comments.validate_comment_draft(
            payload, self._context(), expected_language="en"
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["detected_comment_language"], "vi")
        self.assertIn("language_match", result["errors"])


class TestTrustVideoChatPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.temp_dir.name) / "trust-chat.db"
        self.db_patch = patch.object(db, "DB_PATH", self.temp_db_path)
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_atomic_link_keeps_first_chat_url(self):
        first = db.set_trust_video_chat_if_empty(
            youtube_video_id=VIDEO_ID,
            prompt_version=PROMPT_VERSION,
            chat_url=CHAT_URL,
        )
        second = db.set_trust_video_chat_if_empty(
            youtube_video_id=VIDEO_ID,
            prompt_version=PROMPT_VERSION,
            chat_url="https://chatgpt.com/c/ffffffff-ffff-ffff-ffff-ffffffffffff",
        )

        self.assertEqual(first["chat_url"], CHAT_URL)
        self.assertEqual(second["chat_url"], CHAT_URL)
        self.assertEqual(len(db.list_trust_video_chats(VIDEO_ID)), 1)
        with patch.object(
            comments.db, "list_video_publication_identities", return_value=[]
        ), patch.object(
            comments.db, "list_video_identity_candidates", return_value=[]
        ):
            resolved = comments.resolve_video_chat(
                VIDEO_ID,
                {
                    "channel_db_id": 7,
                    "youtube_channel_ucid": "UC_TRUST_TEST",
                },
                prompt_versions={
                    PROMPT_VERSION: {
                        "default_youtube_channel_id": "UC_TRUST_TEST"
                    }
                },
            )
        self.assertEqual(resolved["status"], "existing_chat")
        self.assertEqual(resolved["chat_url"], CHAT_URL)
        self.assertEqual(resolved["relationship"], "trust_created")


class TestTrustBuilderChatGPTCoordination(unittest.TestCase):
    def tearDown(self):
        if chatgpt_runtime.is_busy():
            chatgpt_runtime.finish_operation()

    def test_shared_lock_is_non_blocking_and_single_owner(self):
        self.assertTrue(chatgpt_runtime.try_start_operation("pipeline", "v1", 1))
        self.assertFalse(chatgpt_runtime.try_start_operation("trust", "v2", 2))
        self.assertEqual(chatgpt_runtime.get_state(), ("pipeline", "v1"))
        chatgpt_runtime.finish_operation()
        self.assertTrue(chatgpt_runtime.try_start_operation("trust", "v2", 2))

    def _page_context(self):
        return {
            "youtube_video_id": VIDEO_ID,
            "title": "Battery storage",
            "description": "solar battery storage reduces evening grid demand",
            "chapters": [],
            "transcript_segments": [],
            "top_comments": [],
        }

    def _resolution(self):
        return {
            "status": "existing_chat",
            "youtube_video_id": VIDEO_ID,
            "chat_url": CHAT_URL,
            "chat_reused": True,
            "linked_video_id": 9,
            "prompt_version": PROMPT_VERSION,
            "context_source": "stored_video",
        }

    def _response(self):
        return (
            '{"comment":"The point that solar battery storage reduces evening grid demand '
            'makes this tradeoff much clearer.","language":"en",'
            '"evidence":"solar battery storage reduces evening grid demand",'
            '"anchor_terms":["solar battery storage"]}'
        )

    def test_existing_chat_uses_short_prompt_and_never_creates_new_chat(self):
        generate = Mock(return_value=self._response())
        create = Mock()
        with patch.object(
            service.trust_builder_comments,
            "resolve_video_chat",
            return_value=self._resolution(),
        ), patch.object(
            service, "action_collect_video_context", new=AsyncMock(return_value=self._page_context())
        ), patch.object(service.db, "get_video", return_value=None), patch.object(
            service.chatgpt_worker, "generate_trust_builder_comment", generate
        ), patch.object(
            service.chatgpt_worker,
            "initialize_trust_video_chat_and_generate_comment",
            create,
        ):
            result = asyncio.run(
                service._prepare_grounded_comment(
                    page=object(),
                    plan={"publication_timezone": "UTC"},
                    youtube_video_id=VIDEO_ID,
                    watch_result={"last_position_seconds": 30},
                    recent_comments=set(),
                )
            )

        self.assertEqual(result["status"], "ready")
        create.assert_not_called()
        self.assertEqual(generate.call_count, 1)
        self.assertNotIn("VIDEO_CONTEXT", generate.call_args.args[1])

    def test_busy_chatgpt_defers_without_opening_worker(self):
        generate = Mock()
        with patch.object(
            service.trust_builder_comments,
            "resolve_video_chat",
            return_value=self._resolution(),
        ), patch.object(
            service, "action_collect_video_context", new=AsyncMock(return_value=self._page_context())
        ), patch.object(service.db, "get_video", return_value=None), patch.object(
            service.chatgpt_runtime, "try_start_operation", return_value=False
        ), patch.object(
            service.chatgpt_worker, "generate_trust_builder_comment", generate
        ):
            result = asyncio.run(
                service._prepare_grounded_comment(
                    page=object(),
                    plan={"publication_timezone": "UTC"},
                    youtube_video_id=VIDEO_ID,
                    watch_result={},
                    recent_comments=set(),
                )
            )

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["skip_reason"], "chatgpt_busy")
        generate.assert_not_called()

    def test_invalid_output_is_repaired_only_once_then_skipped(self):
        generate = Mock(side_effect=["not json", "still not json"])
        with patch.object(
            service.trust_builder_comments,
            "resolve_video_chat",
            return_value=self._resolution(),
        ), patch.object(
            service, "action_collect_video_context", new=AsyncMock(return_value=self._page_context())
        ), patch.object(service.db, "get_video", return_value=None), patch.object(
            service.chatgpt_worker, "generate_trust_builder_comment", generate
        ):
            result = asyncio.run(
                service._prepare_grounded_comment(
                    page=object(),
                    plan={"publication_timezone": "UTC"},
                    youtube_video_id=VIDEO_ID,
                    watch_result={},
                    recent_comments=set(),
                )
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(generate.call_count, 2)
        self.assertIn("invalid_json", result["skip_reason"])


if __name__ == "__main__":
    unittest.main()
