"""Drain mode / Queue freeze management for graceful maintenance and safe restarts."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any

from nexus_engine import paths

logger = logging.getLogger(__name__)

DRAIN_STATE_PATH = paths.DATA_DIR / "system_drain_mode.json"
_drain_lock = threading.Lock()
_cached_state: dict[str, Any] | None = None
_last_mtime: float = -1.0


def _read_state_from_disk() -> dict[str, Any]:
    global _last_mtime
    if not DRAIN_STATE_PATH.exists():
        _last_mtime = -1.0
        return {"enabled": False}
    try:
        stat = DRAIN_STATE_PATH.stat()
        _last_mtime = stat.st_mtime
        content = DRAIN_STATE_PATH.read_text(encoding="utf-8").strip()
        if not content:
            return {"enabled": False}
        return json.loads(content)
    except Exception as exc:
        logger.debug("Failed reading drain mode state file: %s", exc)
        return {"enabled": False}


def _write_state_to_disk(payload: dict[str, Any]) -> None:
    global _last_mtime
    try:
        DRAIN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = DRAIN_STATE_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(DRAIN_STATE_PATH)
        _last_mtime = DRAIN_STATE_PATH.stat().st_mtime
    except Exception as exc:
        logger.error("Failed writing drain mode state to disk: %s", exc)


def is_drain_mode_active() -> bool:
    """Return whether drain mode (queue freeze) is currently active."""
    global _cached_state, _last_mtime
    with _drain_lock:
        if DRAIN_STATE_PATH.exists():
            try:
                current_mtime = DRAIN_STATE_PATH.stat().st_mtime
                if _cached_state is not None and current_mtime == _last_mtime:
                    return bool(_cached_state.get("enabled", False))
            except OSError:
                pass
        state = _read_state_from_disk()
        _cached_state = state
        return bool(state.get("enabled", False))


def get_drain_status() -> dict[str, Any]:
    """Return comprehensive drain mode status, including counts of running and queued jobs."""
    from nexus_engine.services import database as db

    enabled = is_drain_mode_active()
    running_jobs: list[dict[str, Any]] = []
    queued_count = 0

    try:
        conn = sqlite3.connect(str(db.DB_PATH), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows_running = conn.execute(
                """
                SELECT system_jobs.id, system_jobs.job_type, system_jobs.status,
                       system_jobs.progress, system_jobs.video_id, system_jobs.started_at,
                       system_jobs.title, videos.title AS video_title
                FROM system_jobs
                LEFT JOIN videos ON videos.id = system_jobs.video_id
                WHERE system_jobs.status IN ('running', 'processing', 'in_progress')
                ORDER BY system_jobs.started_at ASC
                """
            ).fetchall()
            for r in rows_running:
                display_title = str(r["video_title"] or r["title"] or f"Job #{r['id']}")
                running_jobs.append(
                    {
                        "id": str(r["id"]),
                        "job_type": str(r["job_type"]),
                        "status": str(r["status"]),
                        "progress": str(r["progress"] or ""),
                        "video_id": r["video_id"],
                        "title": display_title,
                        "started_at": str(r["started_at"] or ""),
                    }
                )

            row_q = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM system_jobs
                WHERE status IN ('queued', 'retry_wait')
                """
            ).fetchone()
            queued_count = int(row_q["cnt"]) if row_q else 0
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Could not inspect system_jobs for drain status: %s", exc)

    with _drain_lock:
        state = _cached_state or _read_state_from_disk()

    safe_to_restart = enabled and len(running_jobs) == 0

    return {
        "enabled": enabled,
        "activated_at": str(state.get("activated_at") or ""),
        "reason": str(state.get("reason") or ""),
        "running_jobs_count": len(running_jobs),
        "queued_jobs_count": queued_count,
        "safe_to_restart": safe_to_restart,
        "running_jobs": running_jobs,
    }


def set_drain_mode(enabled: bool, reason: str = "") -> dict[str, Any]:
    """Enable or disable drain mode."""
    global _cached_state
    from nexus_engine.services import database as db

    now = db.utc_now()
    with _drain_lock:
        payload = {
            "enabled": bool(enabled),
            "activated_at": now if enabled else "",
            "reason": reason or ("User requested drain mode for restart" if enabled else "Normal mode resumed"),
            "updated_at": now,
        }
        _write_state_to_disk(payload)
        _cached_state = payload

    logger.info("Drain mode updated: enabled=%s, reason='%s'", enabled, payload["reason"])
    return get_drain_status()


def reset_drain_mode() -> dict[str, Any]:
    """Reset drain mode to disabled (used on server startup)."""
    return set_drain_mode(False, reason="System service startup")
