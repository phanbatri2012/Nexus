"""Explicitly-started in-process coordinator for Short Video jobs."""

from __future__ import annotations

import threading

from nexus_engine.services import database as db
from nexus_engine.services import short_video_audio_preview, short_video_pipeline
from nexus_engine.services.production_coordinator import ProductionCoordinator


JOB_TYPE = "short_video_generation"
PREVIEW_JOB_TYPE = "short_video_audio_preview"
_LOCK = threading.Lock()
_COORDINATOR: ProductionCoordinator | None = None


def _handle_error(job: dict, exc: Exception) -> None:
    if str(job.get("job_type") or "") == PREVIEW_JOB_TYPE:
        short_video_audio_preview.handle_error(job, exc)
        return
    short_video_pipeline.handle_error(job, exc)


def _coordinator() -> ProductionCoordinator:
    global _COORDINATOR
    with _LOCK:
        if _COORDINATOR is None:
            _COORDINATOR = ProductionCoordinator(
                {
                    JOB_TYPE: short_video_pipeline.execute,
                    PREVIEW_JOB_TYPE: short_video_audio_preview.execute,
                },
                error_handler=_handle_error,
            )
        return _COORDINATOR


def kick() -> None:
    """Start/wake the worker only after an explicit user action."""

    _coordinator().wake()


def stop() -> None:
    with _LOCK:
        coordinator = _COORDINATOR
    if coordinator is not None:
        coordinator.stop()


def status() -> dict:
    with _LOCK:
        coordinator = _COORDINATOR
    if coordinator is None:
        return {
            "running": False,
            "ready": False,
            "current_job_id": "",
            "registered_job_types": [JOB_TYPE, PREVIEW_JOB_TYPE],
        }
    return coordinator.status()


def pause_after_restart() -> int:
    """Keep all recoverable Short jobs paused until an explicit resume action."""

    db.recover_interrupted_system_jobs(JOB_TYPE)
    db.recover_interrupted_system_jobs(PREVIEW_JOB_TYPE)
    jobs = []
    for job_type in (JOB_TYPE, PREVIEW_JOB_TYPE):
        jobs.extend(
            db.list_system_jobs(
                limit=None,
                job_type=job_type,
                statuses={"queued", "retry_wait"},
            )
        )
    paused = 0
    for job in jobs:
        result = dict(job.get("result") or {})
        result["attention_required"] = "short_video_resume_after_restart"
        updated = db.update_system_job(
            str(job["id"]),
            status="paused",
            progress=(
                "Short Video đã giữ checkpoint sau khi Nexus khởi động lại; "
                "bấm Tiếp tục để chạy tiếp."
            ),
            result_json=result,
            error="",
            next_retry_at="",
            cancel_requested=0,
            finished_at="",
        )
        if not updated:
            continue
        if str(job.get("job_type") or "") == PREVIEW_JOB_TYPE:
            preview = db.get_short_video_audio_preview_by_job(str(job["id"]))
            if preview:
                db.update_short_video_audio_preview(
                    str(preview["id"]),
                    status="paused",
                    error="",
                )
        else:
            item = db.get_short_video_item_by_job(str(job["id"]))
            if not item:
                paused += 1
                continue
            db.update_short_video_item(
                str(item["id"]),
                status="needs_attention",
                current_stage="short_video_resume_after_restart",
                error="",
            )
        paused += 1
    return paused
