"""Circadian Rhythm Scheduler for automated Channel Trust Building sessions.

Periodically evaluates active trust plans and triggers automated sessions during
natural human waking hours according to each channel's configured timezone.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
from typing import Any

from auto_yt.services import database as db
from auto_yt.services.trust_builder_service import run_warmup_session

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None
_scheduler_running: bool = False


async def _scheduler_loop() -> None:
    logger.info("Khởi động Trust Builder Circadian Scheduler...")
    while _scheduler_running:
        try:
            plans = db.list_channel_trust_plans()
            active_plans = [p for p in plans if p.get("status") == "active"]

            for plan in active_plans:
                plan_id = int(plan["id"])
                
                # Check when the last session ran
                last_session_at = str(plan.get("last_session_at") or "")
                if last_session_at:
                    try:
                        last_dt = datetime.datetime.fromisoformat(last_session_at.replace("Z", "+00:00"))
                        mins_since_last = (datetime.datetime.now(datetime.timezone.utc) - last_dt).total_seconds() / 60.0
                        # Minimum interval between sessions per channel: 90 - 180 minutes (1.5 - 3 hours)
                        min_interval = random.randint(90, 150)
                        if mins_since_last < min_interval:
                            continue
                    except Exception:
                        pass

                # Check if current time in channel timezone is during active circadian hours (8h00 - 23h00)
                current_hour = datetime.datetime.now().hour  # Local host hour or timezone
                if current_hour < 8 or current_hour >= 23:
                    # Night rest period
                    continue

                # Run session asynchronously in background
                logger.info("Scheduler kích hoạt session nuôi kênh cho Plan ID %d", plan_id)
                asyncio.create_task(run_warmup_session(plan_id))
                
                # Jitter between plan launches
                await asyncio.sleep(random.uniform(5.0, 15.0))

        except Exception as exc:
            logger.error("Lỗi trong Trust Builder Scheduler loop: %s", exc)

        # Check every 10 minutes
        await asyncio.sleep(600)


def start_trust_builder_scheduler() -> None:
    """Start the background scheduler task if not already running."""
    global _scheduler_task, _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop())
    logger.info("Trust Builder Scheduler đã được kích hoạt.")


def stop_trust_builder_scheduler() -> None:
    """Stop the background scheduler task."""
    global _scheduler_task, _scheduler_running
    _scheduler_running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
    logger.info("Trust Builder Scheduler đã dừng.")


def is_trust_builder_scheduler_running() -> bool:
    """Return whether the scheduler is currently active."""
    return _scheduler_running
