"""Shared on-demand lifecycle control for project-owned browser services."""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from collections.abc import Callable


StatusProvider = Callable[[], dict]
ServiceAction = Callable[[], dict]
BusyPredicate = Callable[[dict], bool]


def _utc_after(seconds: float) -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        + dt.timedelta(seconds=max(0.0, seconds))
    ).isoformat()


class BrowserServiceUsageLease:
    """Idempotent usage token that never owns or closes the shared browser."""

    def __init__(
        self,
        controller: "OnDemandBrowserLifecycle",
        token: str,
        status: dict,
    ) -> None:
        self._controller = controller
        self._token = token
        self.status = status
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._controller.release(self._token)

    def __enter__(self) -> "BrowserServiceUsageLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class OnDemandBrowserLifecycle:
    """Start on first lease and stop after a bounded truly-idle interval."""

    def __init__(
        self,
        *,
        name: str,
        start_service: ServiceAction,
        stop_service: ServiceAction,
        status_provider: StatusProvider,
        external_busy: BusyPredicate | None = None,
        idle_timeout_seconds: float = 300.0,
        idle_poll_seconds: float = 5.0,
    ) -> None:
        self._name = name
        self._start_service = start_service
        self._stop_service = stop_service
        self._status_provider = status_provider
        self._external_busy = external_busy or (lambda _status: False)
        self.idle_timeout_seconds = max(1.0, float(idle_timeout_seconds))
        self._idle_poll_seconds = max(0.1, float(idle_poll_seconds))
        self._lock = threading.RLock()
        self._tokens: set[str] = set()
        self._timer: threading.Timer | None = None
        self._idle_since: float | None = None
        self._idle_shutdown_at = ""

    def _cancel_timer_locked(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.cancel()

    def _clear_idle_locked(self) -> None:
        self._idle_since = None
        self._idle_shutdown_at = ""

    def _schedule_locked(self, delay: float = 0.0) -> None:
        self._cancel_timer_locked()
        timer = threading.Timer(max(0.0, delay), self._watchdog_tick)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def acquire(self) -> BrowserServiceUsageLease:
        token = uuid.uuid4().hex
        with self._lock:
            self._cancel_timer_locked()
            self._clear_idle_locked()
            self._tokens.add(token)
            try:
                status = self._start_service()
                if not status.get("connected"):
                    raise RuntimeError(
                        status.get("message")
                        or f"Không thể khởi động trình duyệt {self._name}."
                    )
            except Exception:
                self._tokens.discard(token)
                if not self._tokens:
                    self._schedule_locked()
                raise
            return BrowserServiceUsageLease(self, token, status)

    def release(self, token: str) -> None:
        with self._lock:
            self._tokens.discard(token)
            if self._tokens:
                return
            self._clear_idle_locked()
            self._schedule_locked()

    def service_started(self) -> None:
        """Arm idle handling for a manual/background start without a lease."""
        with self._lock:
            if self._tokens:
                return
            self._schedule_locked()

    def visibility_changed(self, visible: bool) -> None:
        with self._lock:
            if visible:
                self._cancel_timer_locked()
                self._clear_idle_locked()
                return
            if not self._tokens:
                self._clear_idle_locked()
                self._schedule_locked()

    def arm_existing_service(self) -> None:
        """Adopt an already-running hidden service without launching one."""
        status = self._status_provider()
        if not status.get("process_alive"):
            return
        with self._lock:
            if self._tokens or status.get("window_visible"):
                return
            self._clear_idle_locked()
            self._schedule_locked()

    def stop_now(self, stop_action: ServiceAction | None = None) -> dict:
        """Stop only when no worker lease is active."""
        with self._lock:
            status = self._status_provider()
            externally_busy = self._external_busy(status)
            if self._tokens or externally_busy:
                status.update(self.snapshot())
                status["stop_blocked"] = True
                status["message"] = (
                    f"Trình duyệt {self._name} đang được tác vụ sử dụng; "
                    "không thể dừng lúc này."
                )
                return status
            self._cancel_timer_locked()
            self._clear_idle_locked()
            return (stop_action or self._stop_service)()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "in_use": bool(self._tokens),
                "active_lease_count": len(self._tokens),
                "idle_shutdown_at": self._idle_shutdown_at,
                "idle_timeout_seconds": int(self.idle_timeout_seconds),
            }

    def _watchdog_tick(self) -> None:
        with self._lock:
            self._timer = None
            if self._tokens:
                self._clear_idle_locked()
                return

        status = self._status_provider()

        with self._lock:
            if self._tokens:
                self._clear_idle_locked()
                return
            if not status.get("process_alive"):
                self._clear_idle_locked()
                return
            if status.get("window_visible"):
                self._clear_idle_locked()
                return
            if self._external_busy(status):
                self._clear_idle_locked()
                self._schedule_locked(self._idle_poll_seconds)
                return

            now = time.monotonic()
            if self._idle_since is None:
                self._idle_since = now
                self._idle_shutdown_at = _utc_after(self.idle_timeout_seconds)
            idle_for = now - self._idle_since
            remaining = self.idle_timeout_seconds - idle_for
            if remaining > 0:
                self._schedule_locked(min(self._idle_poll_seconds, remaining))
                return

            self._cancel_timer_locked()
            self._clear_idle_locked()
            self._stop_service()
