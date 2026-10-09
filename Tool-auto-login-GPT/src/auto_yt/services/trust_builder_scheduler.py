"""Timezone-aware reminder scheduler for human-operated Guided Readiness."""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from auto_yt.services import database as db, security_logging
from auto_yt.services.trust_builder_service import (
    TRUST_JOB_TYPE,
    create_guided_readiness_session,
    run_warmup_session,
)

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 600
SCHEDULER_POLL_SECONDS = 60
ACTIVE_START_HOUR = 8
ACTIVE_END_HOUR = 23
SESSION_INTERVAL_MINUTES = (90, 150)
RETRY_DELAY_MINUTES = 30

_scheduler_task: asyncio.Task | None = None
_scheduler_running = False
_job_tasks: set[asyncio.Task] = set()
_drain_lock = asyncio.Lock()


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_utc(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _plan_timezone(plan: dict[str, Any]) -> ZoneInfo:
    timezone_name = str(plan.get("publication_timezone") or "Asia/Ho_Chi_Minh")
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Ho_Chi_Minh")


def _next_active_start(plan: dict[str, Any], now_utc: datetime.datetime) -> str:
    timezone = _plan_timezone(plan)
    local_now = now_utc.astimezone(timezone)
    next_day = local_now.date() + datetime.timedelta(days=1)
    local_start = datetime.datetime.combine(
        next_day,
        datetime.time(hour=ACTIVE_START_HOUR),
        tzinfo=timezone,
    )
    return local_start.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _defer_plan(plan_id: int, minutes: int) -> None:
    next_run = (_utc_now() + datetime.timedelta(minutes=minutes)).replace(
        microsecond=0
    ).isoformat()
    db.update_channel_trust_plan(plan_id, next_run_at=next_run)


def enqueue_trust_builder_session(
    plan_id: int,
    *,
    source: str,
    run_immediately: bool,
) -> tuple[dict[str, Any], bool]:
    raise ValueError("Automated warm-up đã bị vô hiệu hóa; hãy dùng Guided Readiness.")


async def _execute_claimed_job(job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    payload = job.get("payload") or {}
    plan_id = int(payload.get("plan_id") or 0)
    source = str(payload.get("source") or "scheduled")
    try:
        db.update_system_job(job_id, progress="Đang chạy Trust Builder session")
        result = await run_warmup_session(
            plan_id,
            job_id=job_id,
            require_active=source == "scheduled",
        )
        plan = db.get_channel_trust_plan(plan_id)
        if result.get("canceled"):
            status = "canceled"
            progress = "Đã dừng tại checkpoint an toàn"
            error = ""
        elif result.get("success"):
            status = "completed"
            progress = (
                "Đã đạt quota hôm nay"
                if result.get("quota_reached")
                else "Trust Builder session hoàn thành"
            )
            error = ""
        elif result.get("skipped"):
            status = "completed"
            progress = str(result.get("message") or "Đã bỏ qua session")
            error = ""
        else:
            status = "failed"
            progress = "Trust Builder session thất bại"
            error = security_logging.redact_sensitive(result.get("message") or "")

        db.update_system_job(
            job_id,
            status=status,
            progress=progress,
            result_json=result,
            error=error,
            finished_at=db.utc_now(),
        )
        if plan and plan.get("status") == "active":
            if result.get("quota_reached"):
                db.update_channel_trust_plan(
                    plan_id,
                    next_run_at=_next_active_start(plan, _utc_now()),
                )
            elif result.get("success") and not result.get("skipped"):
                _defer_plan(plan_id, random.randint(*SESSION_INTERVAL_MINUTES))
            else:
                _defer_plan(plan_id, RETRY_DELAY_MINUTES)
    except asyncio.CancelledError:
        db.update_system_job(
            job_id,
            status="canceled",
            progress="Đã dừng khi backend shutdown",
            error="",
            finished_at=db.utc_now(),
        )
        raise
    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Trust Builder job %s thất bại: %s", job_id, safe_error)
        db.update_system_job(
            job_id,
            status="failed",
            progress="Trust Builder session thất bại",
            error=safe_error,
            finished_at=db.utc_now(),
        )
        if db.get_channel_trust_plan(plan_id):
            _defer_plan(plan_id, RETRY_DELAY_MINUTES)


async def _drain_job_queue() -> None:
    # Legacy jobs are never claimed. The migration cancels any pre-existing rows.
    return


def trigger_queue_drain() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_drain_job_queue())


def _is_plan_due(plan: dict[str, Any], now_utc: datetime.datetime) -> bool:
    next_run_at = _parse_utc(str(plan.get("next_run_at") or ""))
    if next_run_at is not None:
        return next_run_at <= now_utc
    local_now = now_utc.astimezone(_plan_timezone(plan))
    try:
        hour, minute = (int(part) for part in str(plan.get("reminder_time_local") or "09:00").split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 9, 0
    return (local_now.hour, local_now.minute) >= (
        max(0, min(hour, 23)),
        max(0, min(minute, 59)),
    )


def _next_guided_reminder(plan: dict[str, Any], now_utc: datetime.datetime) -> str:
    timezone = _plan_timezone(plan)
    local_now = now_utc.astimezone(timezone)
    try:
        hour, minute = (int(part) for part in str(plan.get("reminder_time_local") or "09:00").split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 9, 0
    next_local = datetime.datetime.combine(
        local_now.date() + datetime.timedelta(days=1),
        datetime.time(hour=max(0, min(hour, 23)), minute=max(0, min(minute, 59))),
        tzinfo=timezone,
    )
    return next_local.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat()


async def _evaluate_active_plans() -> None:
    now_utc = _utc_now()
    for plan in db.list_channel_trust_plans():
        if not plan.get("reminder_enabled") or not _is_plan_due(plan, now_utc):
            continue
        plan_id = int(plan["id"])
        try:
            create_guided_readiness_session(plan_id)
            db.update_channel_trust_plan(
                plan_id,
                next_run_at=_next_guided_reminder(plan, now_utc),
                error_message="",
            )
        except Exception as exc:
            safe_error = security_logging.redact_sensitive(exc)
            db.update_channel_trust_plan(
                plan_id,
                error_message=safe_error,
            )


async def _scheduler_loop() -> None:
    logger.info(
        "Trust Builder scheduler chờ %d giây để backend sẵn sàng.",
        STARTUP_DELAY_SECONDS,
    )
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while _scheduler_running:
        try:
            await _evaluate_active_plans()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Lỗi trong Trust Builder scheduler: %s",
                security_logging.redact_sensitive(exc),
            )
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


def start_trust_builder_scheduler() -> None:
    global _scheduler_task, _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _scheduler_running = False
        logger.warning("Không có event loop đang chạy; chưa khởi động Trust Builder scheduler.")
        return
    _scheduler_task = loop.create_task(_scheduler_loop())
    logger.info("Trust Builder scheduler đã được kích hoạt.")


def stop_trust_builder_scheduler() -> None:
    global _scheduler_task, _scheduler_running
    _scheduler_running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
    for task in list(_job_tasks):
        if not task.done():
            task.cancel()
    logger.info("Trust Builder scheduler đã dừng.")


def get_trust_builder_scheduler_status() -> dict[str, Any]:
    return {
        "running": _scheduler_running,
        "ready": bool(_scheduler_task and not _scheduler_task.done()),
        "startup_delay_seconds": STARTUP_DELAY_SECONDS,
        "active_jobs": sum(not task.done() for task in _job_tasks),
        "mode": "guided_reminders_only",
    }


def is_trust_builder_scheduler_running() -> bool:
    return _scheduler_running
