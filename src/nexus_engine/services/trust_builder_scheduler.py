"""Timezone-aware scheduler and system-job runner for Trust Builder."""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nexus_engine.services import database as db, security_logging
from nexus_engine.services.trust_builder_service import (
    TRUST_JOB_TYPE,
    get_daily_work_state,
    get_active_plan_job,
    run_warmup_session,
    validate_plan_browser_configuration,
)

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 600
SCHEDULER_POLL_SECONDS = 60
ACTIVE_START_HOUR = 8
ACTIVE_END_HOUR = 23
SESSION_INTERVAL_MINUTES = (90, 150)
RETRY_DELAY_MINUTES = 30

PERSONA_CIRCADIAN_STANDARD = "circadian_standard"
PERSONA_OFFICE_WORKER = "office_worker"
PERSONA_NIGHT_OWL = "night_owl"
PERSONA_LEGACY_UNIFORM = "legacy_uniform"

SUPPORTED_PERSONAS = {
    PERSONA_CIRCADIAN_STANDARD,
    PERSONA_OFFICE_WORKER,
    PERSONA_NIGHT_OWL,
    PERSONA_LEGACY_UNIFORM,
}


def is_persona_active_at(persona: str, local_hour: int, local_minute: int = 0) -> bool:
    """Check if the given local time is within the active waking window for the persona."""
    time_val = local_hour + (local_minute / 60.0)
    if persona == PERSONA_NIGHT_OWL:
        # Active from 14:00 to 02:00 (next day)
        return time_val >= 14.0 or time_val < 2.0
    elif persona == PERSONA_OFFICE_WORKER:
        # Active from 08:00 to 23:00
        return 8.0 <= time_val < 23.0
    elif persona == PERSONA_LEGACY_UNIFORM:
        # Legacy: 08:00 to 23:00
        return float(ACTIVE_START_HOUR) <= time_val < float(ACTIVE_END_HOUR)
    else:  # PERSONA_CIRCADIAN_STANDARD
        # Active from 07:30 to 23:00
        return 7.5 <= time_val < 23.0


def calculate_circadian_interval(
    persona: str,
    local_hour: int,
    local_minute: int = 0,
) -> int:
    """Return the interval in minutes until the next session based on persona and local time."""
    time_val = local_hour + (local_minute / 60.0)

    if persona == PERSONA_NIGHT_OWL:
        if 14.0 <= time_val < 18.0:
            min_m, max_m = 120, 180
        elif 18.0 <= time_val < 21.0:
            min_m, max_m = 60, 90
        elif time_val >= 21.0 or time_val < 2.0:
            min_m, max_m = 40, 70
        else:
            return 120
    elif persona == PERSONA_OFFICE_WORKER:
        if 8.0 <= time_val < 11.5:
            min_m, max_m = 210, 300
        elif 11.5 <= time_val < 13.5:
            min_m, max_m = 40, 65
        elif 13.5 <= time_val < 19.0:
            min_m, max_m = 210, 300
        elif 19.0 <= time_val < 23.0:
            min_m, max_m = 40, 65
        else:
            return 120
    elif persona == PERSONA_LEGACY_UNIFORM:
        min_m, max_m = SESSION_INTERVAL_MINUTES
    else:  # PERSONA_CIRCADIAN_STANDARD
        if 7.5 <= time_val < 11.5:
            min_m, max_m = 150, 240
        elif 11.5 <= time_val < 13.5:
            min_m, max_m = 45, 75
        elif 13.5 <= time_val < 18.5:
            min_m, max_m = 180, 240
        elif 18.5 <= time_val < 23.0:
            min_m, max_m = 40, 70
        else:
            return 120

    base = random.randint(min_m, max_m)
    jitter = random.randint(-max(1, int(base * 0.1)), max(1, int(base * 0.1)))
    return max(30, base + jitter)


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
    persona = str(plan.get("schedule_persona") or PERSONA_CIRCADIAN_STANDARD)

    if persona == PERSONA_NIGHT_OWL:
        if 2 <= local_now.hour < 14:
            target_date = local_now.date()
        else:
            target_date = local_now.date() + datetime.timedelta(days=1)
        base_hour, base_minute = 14, 0
    elif persona == PERSONA_OFFICE_WORKER:
        if local_now.hour < 8:
            target_date = local_now.date()
        else:
            target_date = local_now.date() + datetime.timedelta(days=1)
        base_hour, base_minute = 8, 0
    elif persona == PERSONA_LEGACY_UNIFORM:
        if local_now.hour < ACTIVE_START_HOUR:
            target_date = local_now.date()
        else:
            target_date = local_now.date() + datetime.timedelta(days=1)
        base_hour, base_minute = ACTIVE_START_HOUR, 0
    else:  # PERSONA_CIRCADIAN_STANDARD
        time_val = local_now.hour + (local_now.minute / 60.0)
        if time_val < 7.5:
            target_date = local_now.date()
        else:
            target_date = local_now.date() + datetime.timedelta(days=1)
        base_hour, base_minute = 7, 30

    jitter_minutes = random.randint(5, 30)
    target_dt = datetime.datetime.combine(
        target_date,
        datetime.time(hour=base_hour, minute=base_minute),
        tzinfo=timezone,
    ) + datetime.timedelta(minutes=jitter_minutes)
    return target_dt.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _defer_plan(plan_id: int, minutes: int, plan: dict[str, Any] | None = None) -> None:
    now = _utc_now()
    scheduled_run = now + datetime.timedelta(minutes=minutes)
    if plan:
        persona = str(plan.get("schedule_persona") or PERSONA_CIRCADIAN_STANDARD)
        timezone = _plan_timezone(plan)
        scheduled_local = scheduled_run.astimezone(timezone)
        if not is_persona_active_at(persona, scheduled_local.hour, scheduled_local.minute):
            next_start_iso = _next_active_start(plan, now)
            db.update_channel_trust_plan(plan_id, next_run_at=next_start_iso)
            return

    db.update_channel_trust_plan(
        plan_id,
        next_run_at=scheduled_run.replace(microsecond=0).isoformat(),
    )


def enqueue_trust_builder_session(
    plan_id: int,
    *,
    source: str,
    run_immediately: bool,
) -> tuple[dict[str, Any], bool]:
    plan = db.get_channel_trust_plan(plan_id)
    if not plan:
        raise ValueError("Không tìm thấy Trust Plan.")
    if plan.get("status") == "completed":
        raise ValueError("Plan đã hoàn tất; không thể chạy thêm session.")
    validate_plan_browser_configuration(plan)
    existing_job = get_active_plan_job(plan_id)
    if existing_job:
        return existing_job, False
    if source == "scheduled" and plan.get("status") != "active":
        raise ValueError("Plan không ở trạng thái active.")

    job_id = f"trust_builder_{plan_id}_{uuid.uuid4().hex}"
    job = db.create_system_job(
        job_id=job_id,
        job_type=TRUST_JOB_TYPE,
        title=f"Trust Builder · {plan.get('channel_title') or f'Plan #{plan_id}'}",
        payload={"plan_id": plan_id, "source": source},
    )
    db.update_channel_trust_plan(
        plan_id,
        last_attempt_at=db.utc_now(),
        error_message="",
    )
    if run_immediately:
        trigger_queue_drain()
    return job, True


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
                else "Trust Builder session hoàn thành một phần"
                if result.get("partial_success")
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
            elif result.get("retryable_engagement"):
                _defer_plan(plan_id, RETRY_DELAY_MINUTES, plan)
            elif result.get("success") and not result.get("skipped"):
                persona = str(plan.get("schedule_persona") or PERSONA_CIRCADIAN_STANDARD)
                timezone = _plan_timezone(plan)
                local_now = _utc_now().astimezone(timezone)
                interval_minutes = calculate_circadian_interval(persona, local_now.hour, local_now.minute)
                _defer_plan(plan_id, interval_minutes, plan)
            else:
                _defer_plan(plan_id, RETRY_DELAY_MINUTES, plan)
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
    from nexus_engine.services import drain_mode
    if drain_mode.is_drain_mode_active():
        return
    async with _drain_lock:
        while True:
            job = db.claim_next_system_job(TRUST_JOB_TYPE)
            if not job:
                return
            task = asyncio.create_task(_execute_claimed_job(job))
            _job_tasks.add(task)
            task.add_done_callback(_job_tasks.discard)


def trigger_queue_drain() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_drain_job_queue())


def _is_plan_due(plan: dict[str, Any], now_utc: datetime.datetime) -> bool:
    timezone = _plan_timezone(plan)
    local_dt = now_utc.astimezone(timezone)
    persona = str(plan.get("schedule_persona") or PERSONA_CIRCADIAN_STANDARD)
    if not is_persona_active_at(persona, local_dt.hour, local_dt.minute):
        return False
    next_run_at = _parse_utc(str(plan.get("next_run_at") or ""))
    return next_run_at is None or next_run_at <= now_utc


async def _evaluate_active_plans() -> None:
    from nexus_engine.services import drain_mode
    if drain_mode.is_drain_mode_active():
        return
    now_utc = _utc_now()
    for plan in db.list_channel_trust_plans():
        if plan.get("status") != "active" or not _is_plan_due(plan, now_utc):
            continue
        plan_id = int(plan["id"])
        daily_stats = db.get_trust_daily_activity_stats(
            plan_id,
            str(plan.get("publication_timezone") or "Asia/Ho_Chi_Minh"),
            now_utc=now_utc,
        )
        if get_daily_work_state(plan, daily_stats)["daily_complete"]:
            db.update_channel_trust_plan(
                plan_id,
                next_run_at=_next_active_start(plan, now_utc),
            )
            continue
        try:
            enqueue_trust_builder_session(
                plan_id,
                source="scheduled",
                run_immediately=False,
            )
        except ValueError as exc:
            safe_error = security_logging.redact_sensitive(exc)
            db.update_channel_trust_plan(
                plan_id,
                status="error",
                error_message=safe_error,
            )
    await _drain_job_queue()


_last_safety_sync_check: datetime.datetime | None = None


async def _check_periodic_safety_sync() -> None:
    global _last_safety_sync_check
    now = _utc_now()
    if _last_safety_sync_check is not None and (now - _last_safety_sync_check).total_seconds() < 3600:
        return
    _last_safety_sync_check = now
    config = db.get_safety_config()
    if not config.get("shield_enabled", True):
        return
    if not str(config.get("remote_sync_url") or "").strip():
        return
    last_synced_str = str(config.get("last_synced_at") or "")
    interval_hours = int(config.get("auto_sync_interval_hours") or 24)
    last_synced = _parse_utc(last_synced_str)
    if last_synced is None or (now - last_synced).total_seconds() >= (interval_hours * 3600):
        try:
            from nexus_engine.services import trust_builder_safety as safety
            safety.sync_safety_blacklist_from_remote()
        except Exception as exc:
            logger.warning(
                "Lỗi tự động đồng bộ Safety Blacklist định kỳ: %s",
                security_logging.redact_sensitive(exc),
            )


async def _scheduler_loop() -> None:
    logger.info(
        "Trust Builder scheduler chờ %d giây để backend sẵn sàng.",
        STARTUP_DELAY_SECONDS,
    )
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while _scheduler_running:
        try:
            await _check_periodic_safety_sync()
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
    }


def is_trust_builder_scheduler_running() -> bool:
    return _scheduler_running
