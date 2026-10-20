import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.services import omnivoice_client


class OmniVoiceClientLifecycleTests(unittest.TestCase):
    def test_connection_failure_never_starts_a_detached_worker(self):
        with patch.object(
            omnivoice_client,
            "_request_json",
            side_effect=omnivoice_client.OmniVoiceConnectionError("timed out"),
        ) as request:
            with self.assertRaisesRegex(
                omnivoice_client.OmniVoiceConnectionError,
                "run_autoyt",
            ):
                omnivoice_client.ensure_worker_running()

        request.assert_called_once_with(
            "/health",
            timeout=5,
            base_url=omnivoice_client.DEFAULT_BASE_URL,
        )
        self.assertFalse(hasattr(omnivoice_client, "_worker_process"))

    def test_api_error_is_not_misclassified_as_a_stopped_worker(self):
        expected = omnivoice_client.OmniVoiceAPIError(
            "OmniVoice API error: 401 - Invalid internal token."
        )
        with patch.object(
            omnivoice_client,
            "_request_json",
            side_effect=expected,
        ):
            with self.assertRaises(omnivoice_client.OmniVoiceAPIError) as context:
                omnivoice_client.ensure_worker_running()

        self.assertIs(context.exception, expected)

    def test_loading_and_busy_workers_are_available(self):
        for state in ("loading", "busy", "ready", "sleeping"):
            with self.subTest(state=state):
                with patch.object(
                    omnivoice_client,
                    "_request_json",
                    return_value={"ok": True, "state": state},
                ):
                    omnivoice_client.ensure_worker_running()

    def test_runtime_error_is_reported_without_starting_another_worker(self):
        with patch.object(
            omnivoice_client,
            "_request_json",
            return_value={
                "ok": False,
                "state": "error",
                "error": "model load failed",
            },
        ):
            with self.assertRaisesRegex(
                omnivoice_client.OmniVoiceAPIError,
                "model load failed",
            ):
                omnivoice_client.ensure_worker_running()


if __name__ == "__main__":
    unittest.main()
