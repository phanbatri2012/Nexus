import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from nexus_engine.services import video_production
from nexus_engine.services.google_flow_worker import FlowGenerationTimeout, FlowImagePending
from nexus_engine.services.video_production import ValidatedImageArtifact, ImagePerceptualFingerprint


@asynccontextmanager
async def _fake_flow_session(*args, **kwargs):
    del args, kwargs
    yield object()


class TestTwoPhaseReconciliation(unittest.TestCase):
    def test_reconciliation_phase_collects_later_scene_even_if_earlier_scene_pending(self):
        scenes = [
            {"index": 0, "action": "scene 0 action", "is_video": False},
            {"index": 1, "action": "scene 1 action", "is_video": False},
        ]
        
        valid_img_1 = ValidatedImageArtifact(
            path=Path("scene-1.png"),
            sha256="sha256-scene-1",
            size_bytes=1000,
            mtime_ns=123456,
            fingerprint=(111, (1.0, 2.0, 3.0), 5.0),
            width=1376,
            height=768,
        )

        async def fake_generate_image(*args, **kwargs):
            scene = kwargs.get("scene", {})
            idx = scene.get("index")
            if idx == 0:
                raise FlowImagePending("Scene 0 still pending", reason_code="flow_image_prompt_pending")
            elif idx == 1:
                return valid_img_1
            raise RuntimeError(f"Unexpected scene {idx}")

        artifact_snapshot = {
            "scene:0": {"status": "queued", "metadata": {"flow_receipt": {"request_key": "k0"}}},
            "scene:1": {"status": "queued", "metadata": {"flow_receipt": {"request_key": "k1"}}},
        }

        with (
            patch.object(video_production, "_flow_worker_session", _fake_flow_session),
            patch.object(video_production, "_generate_scene_image_async", new=AsyncMock(side_effect=fake_generate_image)),
            patch.object(video_production.db, "list_latest_video_artifacts", return_value=artifact_snapshot),
            patch.object(video_production.db, "get_latest_video_artifact", return_value=None),
            patch.object(video_production.db, "upsert_video_artifact"),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "stat", return_value=type("Stat", (), {"st_size": 1000, "st_mtime_ns": 123456})()),
        ):
            try:
                video_production.generate_scene_media(
                    video_id=999,
                    scenes=scenes,
                    settings={"enable_intro_video": False},
                    progress=lambda *args, **kwargs: None,
                    cancel_check=lambda: None,
                )
            except FlowImagePending as pending_exc:
                # Scene 1 was collected, so only scene 0 should remain pending!
                self.assertEqual(pending_exc.pending_image_scene_indices, [0])
                self.assertNotIn(1, pending_exc.pending_image_scene_indices)
            else:
                self.fail("Expected FlowImagePending for scene 0")

    def test_reconciliation_timeout_allows_phase_1b_redispatch(self):
        scenes = [
            {"index": 0, "action": "scene 0 action", "is_video": False},
        ]
        
        call_count = 0

        async def fake_generate_image(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Phase 1A reconciliation timeout
                raise FlowGenerationTimeout("10 min timeout")
            elif call_count == 2:
                # Phase 1B re-dispatch generates a fresh pending receipt
                raise FlowImagePending("Fresh dispatch pending", reason_code="flow_image_prompt_pending")
            raise RuntimeError("Unexpected call")

        artifact_snapshot = {
            "scene:0": {"status": "queued", "metadata": {"flow_receipt": {"request_key": "expired_key"}}},
        }

        with (
            patch.object(video_production, "_flow_worker_session", _fake_flow_session),
            patch.object(video_production, "_generate_scene_image_async", new=AsyncMock(side_effect=fake_generate_image)),
            patch.object(video_production.db, "list_latest_video_artifacts", return_value=artifact_snapshot),
            patch.object(video_production.db, "get_latest_video_artifact", return_value=None),
            patch.object(video_production.db, "upsert_video_artifact"),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "stat", return_value=type("Stat", (), {"st_size": 1000, "st_mtime_ns": 123456})()),
        ):
            try:
                video_production.generate_scene_media(
                    video_id=999,
                    scenes=scenes,
                    settings={"enable_intro_video": False},
                    progress=lambda *args, **kwargs: None,
                    cancel_check=lambda: None,
                )
            except FlowImagePending as pending_exc:
                self.assertEqual(pending_exc.pending_image_scene_indices, [0])
                # Call count MUST be 2: 1 for Phase 1A (timeout), 1 for Phase 1B (fresh dispatch)!
                self.assertEqual(call_count, 2)
            else:
                self.fail("Expected FlowImagePending from fresh dispatch")


if __name__ == "__main__":
    unittest.main()
