import unittest
from unittest.mock import patch

from auto_yt.services.on_demand_browser import OnDemandBrowserLifecycle


class FakeTimer:
    instances = []

    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.cancelled = False
        self.started = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


class OnDemandBrowserLifecycleTests(unittest.TestCase):
    def setUp(self):
        FakeTimer.instances = []
        self.now = 0.0
        self.state = {
            "connected": False,
            "process_alive": False,
            "window_visible": False,
            "generation_active": False,
        }
        self.start_count = 0
        self.stop_count = 0

    def status(self):
        return dict(self.state)

    def start(self):
        if not self.state["connected"]:
            self.start_count += 1
            self.state["connected"] = True
            self.state["process_alive"] = True
        return self.status()

    def stop(self):
        self.stop_count += 1
        self.state["connected"] = False
        self.state["process_alive"] = False
        return self.status()

    def controller(self, *, flow=False):
        return OnDemandBrowserLifecycle(
            name="Flow" if flow else "ChatGPT",
            start_service=self.start,
            stop_service=self.stop,
            status_provider=self.status,
            external_busy=(
                (lambda status: bool(status.get("generation_active")))
                if flow
                else None
            ),
            idle_timeout_seconds=300,
            idle_poll_seconds=5,
        )

    def latest_timer(self):
        return FakeTimer.instances[-1]

    def test_multiple_leases_share_one_owner_and_stop_after_300_idle_seconds(self):
        with (
            patch(
                "auto_yt.services.on_demand_browser.threading.Timer",
                FakeTimer,
            ),
            patch(
                "auto_yt.services.on_demand_browser.time.monotonic",
                side_effect=lambda: self.now,
            ),
        ):
            lifecycle = self.controller()
            first = lifecycle.acquire()
            second = lifecycle.acquire()

            self.assertEqual(self.start_count, 1)
            self.assertEqual(lifecycle.snapshot()["active_lease_count"], 2)

            first.close()
            self.assertFalse(FakeTimer.instances)
            second.close()
            self.latest_timer().fire()
            self.assertTrue(lifecycle.snapshot()["idle_shutdown_at"])

            self.now = 299
            self.latest_timer().fire()
            self.assertEqual(self.stop_count, 0)

            self.now = 300
            self.latest_timer().fire()
            self.assertEqual(self.stop_count, 1)
            self.assertFalse(self.state["process_alive"])

    def test_new_lease_cancels_pending_idle_shutdown(self):
        with (
            patch(
                "auto_yt.services.on_demand_browser.threading.Timer",
                FakeTimer,
            ),
            patch(
                "auto_yt.services.on_demand_browser.time.monotonic",
                side_effect=lambda: self.now,
            ),
        ):
            lifecycle = self.controller()
            first = lifecycle.acquire()
            first.close()
            pending = self.latest_timer()

            second = lifecycle.acquire()

            self.assertTrue(pending.cancelled)
            pending.fire()
            self.assertEqual(self.stop_count, 0)
            second.close()

    def test_visible_window_and_flow_generation_suspend_idle_countdown(self):
        with (
            patch(
                "auto_yt.services.on_demand_browser.threading.Timer",
                FakeTimer,
            ),
            patch(
                "auto_yt.services.on_demand_browser.time.monotonic",
                side_effect=lambda: self.now,
            ),
        ):
            lifecycle = self.controller(flow=True)
            lease = lifecycle.acquire()
            self.state["generation_active"] = True
            lease.close()
            self.latest_timer().fire()

            self.assertFalse(lifecycle.snapshot()["idle_shutdown_at"])
            self.state["generation_active"] = False
            self.now = 10
            self.latest_timer().fire()
            self.assertTrue(lifecycle.snapshot()["idle_shutdown_at"])

            self.state["window_visible"] = True
            lifecycle.visibility_changed(True)
            self.assertFalse(lifecycle.snapshot()["idle_shutdown_at"])
            timer_count = len(FakeTimer.instances)
            self.now = 600
            self.assertEqual(len(FakeTimer.instances), timer_count)
            self.assertEqual(self.stop_count, 0)

            self.state["window_visible"] = False
            lifecycle.visibility_changed(False)
            self.latest_timer().fire()
            self.assertTrue(lifecycle.snapshot()["idle_shutdown_at"])

    def test_manual_stop_is_blocked_by_lease_or_flow_generation(self):
        with patch(
            "auto_yt.services.on_demand_browser.threading.Timer",
            FakeTimer,
        ):
            lifecycle = self.controller(flow=True)
            lease = lifecycle.acquire()

            blocked = lifecycle.stop_now()
            self.assertTrue(blocked["stop_blocked"])
            self.assertEqual(self.stop_count, 0)

            lease.close()
            self.state["generation_active"] = True
            blocked = lifecycle.stop_now()
            self.assertTrue(blocked["stop_blocked"])
            self.assertEqual(self.stop_count, 0)


if __name__ == "__main__":
    unittest.main()
