import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from nexus_engine.services.google_flow_worker import (
    FlowImageNeedsAttention,
    FlowMediaPending,
    FlowImagePending,
)
from nexus_engine.services import video_production


def test_scene_max_attempts_exceeded_fails_and_marks_db():
    mock_db = MagicMock()
    mock_scene = {"index": 11, "name": "Scene 12"}
    existing_meta = {"generation_attempt": 5}
    existing_artifact = {
        "content_hash": "hash123",
        "status": "queued",
        "metadata": existing_meta,
    }

    with patch.object(video_production, "db", mock_db), \
         patch.object(video_production, "_cached_reference_hash", return_value="refhash"), \
         patch.object(video_production, "_scene_hash", return_value="hash123"):
        
        with pytest.raises(FlowImageNeedsAttention) as excinfo:
            import asyncio
            asyncio.run(
                video_production._generate_scene_image_async(
                    video_id=999,
                    scene=mock_scene,
                    scene_count=20,
                    profile={},
                    settings={},
                    reference_path=None,
                    reference_id="",
                    progress=MagicMock(),
                    cancel_check=MagicMock(),
                    worker=MagicMock(),
                    artifact_snapshot={"scene:11": existing_artifact},
                )
            )

        assert "vượt quá giới hạn thử tối đa" in str(excinfo.value)
        # Verify db.upsert_video_artifact was called with status="failed" and terminal=True
        mock_db.upsert_video_artifact.assert_called_once()
        _, kwargs = mock_db.upsert_video_artifact.call_args
        assert kwargs["video_id"] == 999
        assert kwargs["artifact_type"] == "scene:11"
        assert kwargs["status"] == "failed"
        assert kwargs["metadata"]["terminal"] is True
        assert kwargs["metadata"]["reason_code"] == "flow_max_attempts_exceeded"


def test_cached_terminal_failed_scene_raises_immediately():
    mock_db = MagicMock()
    mock_scene = {"index": 11, "name": "Scene 12"}
    existing_meta = {
        "generation_attempt": 6,
        "terminal": True,
        "reason_code": "flow_max_attempts_exceeded",
        "error": "Cảnh 12 đã vượt quá giới hạn thử tối đa (5 lần).",
    }
    existing_artifact = {
        "content_hash": "hash123",
        "status": "failed",
        "metadata": existing_meta,
    }

    with patch.object(video_production, "db", mock_db), \
         patch.object(video_production, "_cached_reference_hash", return_value="refhash"), \
         patch.object(video_production, "_scene_hash", return_value="hash123"):
        
        with pytest.raises(FlowImageNeedsAttention) as excinfo:
            import asyncio
            asyncio.run(
                video_production._generate_scene_image_async(
                    video_id=999,
                    scene=mock_scene,
                    scene_count=20,
                    profile={},
                    settings={},
                    reference_path=None,
                    reference_id="",
                    progress=MagicMock(),
                    cancel_check=MagicMock(),
                    worker=MagicMock(),
                    artifact_snapshot={"scene:11": existing_artifact},
                )
            )

        assert "vượt quá giới hạn thử tối đa" in str(excinfo.value)
        # Should not upsert again
        mock_db.upsert_video_artifact.assert_not_called()


def test_circuit_breaker_stagnant_reconciles_pauses_job():
    from nexus_engine.main import _handle_production_job_error

    mock_db = MagicMock()
    mock_db.list_latest_video_artifacts.return_value = {
        "scene:0": {"status": "completed"},
        "scene:1": {"status": "completed"},
    }
    mock_db.get_youtube_publish_workflow_by_job.return_value = None

    job = {
        "id": "flow-media-test-123",
        "job_type": "flow_media_generation",
        "video_id": 379,
        "result": {
            "reconcile_last_completed_count": 2,
            "reconcile_stagnant_count": 14,  # will reach 15
        },
        "payload": {"video_id": 379},
    }

    mock_db.get_system_job.return_value = job

    exc = FlowImagePending(
        "Google Flow còn 2 ảnh đang chờ",
        retry_delay_seconds=30.0,
        pending_image_scene_indices=[11, 13],
    )

    with patch("nexus_engine.main.db", mock_db):
        _handle_production_job_error(job, exc)

    # Verify job was paused
    mock_db.update_system_job.assert_called()
    call_args = mock_db.update_system_job.call_args[1]
    assert call_args["status"] == "paused"
    assert call_args["result_json"]["attention_required"] == "flow_reconcile_stalled"
    assert "15 lần liên tiếp mà không có ảnh/video mới" in call_args["progress"]

    # Verify video state was updated
    mock_db.update_video_production_state.assert_called_with(
        379,
        render_status="paused",
        current_stage="flow_reconcile_stalled",
        production_progress=call_args["progress"],
        blocking_reason=call_args["error"],
    )
