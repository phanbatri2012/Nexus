import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from nexus_engine.services import source_voice_analyzer as analyzer
from nexus_engine.services.proxy_utils import ProxyConfigurationError


class _FakeYoutubeDL:
    last_options = None

    def __init__(self, options):
        self.options = options
        _FakeYoutubeDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def download(self, _urls):
        target = Path(self.options["outtmpl"].replace("%(ext)s", "webm"))
        target.write_bytes(b"audio")


def _write_wav(path: Path):
    samples = (np.zeros(analyzer.SAMPLE_RATE, dtype=np.int16)).tobytes()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(analyzer.SAMPLE_RATE)
        handle.writeframes(samples)


class TestSourceVoiceAnalyzer(unittest.TestCase):
    def test_malformed_benchmark_never_enables_auto_mode(self):
        with tempfile.TemporaryDirectory() as temp_name:
            benchmark = Path(temp_name) / "benchmark.json"
            benchmark.write_text(
                json.dumps({"male_count": 10, "female_count": 10, "error_rate": -1}),
                encoding="utf-8",
            )
            with patch.object(analyzer, "BENCHMARK_PATH", benchmark):
                status = analyzer.benchmark_gate_status()
        self.assertFalse(status["enabled"])
        self.assertEqual(status["mode"], "advisory")

    def test_proxy_is_required_before_model_or_network_access(self):
        with patch.object(analyzer, "ensure_model") as ensure_model:
            with self.assertRaises(ProxyConfigurationError):
                analyzer.analyze_source_voice(
                    "https://www.youtube.com/watch?v=abcdefghijk",
                    duration_seconds=600,
                    proxy="",
                )
        ensure_model.assert_not_called()

    def test_ytdlp_window_always_receives_resolved_proxy(self):
        with tempfile.TemporaryDirectory() as temp_name, patch.object(
            analyzer, "YoutubeDL", _FakeYoutubeDL
        ):
            output = analyzer._download_window(
                "https://www.youtube.com/watch?v=abcdefghijk",
                proxy_url="http://user:pass@127.0.0.1:8080",
                start=10,
                end=22,
                output_stem=Path(temp_name) / "clip",
            )
        self.assertTrue(output.name.endswith(".webm"))
        self.assertEqual(
            _FakeYoutubeDL.last_options["proxy"],
            "http://user:pass@127.0.0.1:8080",
        )

    def test_invalid_cached_model_checksum_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_name:
            model_path = Path(temp_name) / "model.onnx"
            model_path.write_bytes(b"invalid")
            with patch.object(analyzer, "MODEL_PATH", model_path), self.assertRaises(
                analyzer.SourceVoiceModelError
            ):
                analyzer.ensure_model()

    def _run_with_windows(self, voiced_results, classifier_results, benchmark_enabled=True):
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        temp_root = Path(temp_dir.name) / "source-temp"
        model_path = Path(temp_dir.name) / "model.onnx"
        model_path.write_bytes(b"model")

        def download(_url, *, output_stem, **_kwargs):
            source = output_stem.with_suffix(".webm")
            source.write_bytes(b"audio")
            return source

        def convert(_source, target):
            _write_wav(target)

        patches = [
            patch.object(analyzer, "TEMP_ROOT", temp_root),
            patch.object(analyzer, "ensure_model", return_value=model_path),
            patch.object(analyzer.ort, "InferenceSession", return_value=MagicMock()),
            patch.object(analyzer, "_download_window", side_effect=download),
            patch.object(analyzer, "_convert_to_mono_wav", side_effect=convert),
            patch.object(analyzer, "_voiced_audio", side_effect=voiced_results),
            patch.object(analyzer, "_classify", side_effect=classifier_results),
            patch.object(
                analyzer,
                "benchmark_gate_status",
                return_value={"enabled": benchmark_enabled, "mode": "auto" if benchmark_enabled else "advisory"},
            ),
        ]
        for active_patch in patches:
            active_patch.start()
        try:
            result = analyzer.analyze_source_voice(
                "https://www.youtube.com/watch?v=abcdefghijk",
                duration_seconds=600,
                proxy="http://127.0.0.1:8080",
            )
            remaining = list(temp_root.iterdir()) if temp_root.exists() else []
            return result, remaining
        finally:
            for active_patch in reversed(patches):
                active_patch.stop()
            temp_dir.cleanup()

    def test_vad_insufficient_speech_returns_any(self):
        empty = np.empty(0, dtype=np.float32)
        result, remaining = self._run_with_windows(
            [(empty, 0.0), (empty, 0.0), (empty, 0.0)],
            [],
        )
        self.assertEqual(result["gender"], "any")
        self.assertEqual(result["status"], "insufficient_speech")
        self.assertEqual(remaining, [])

    def test_disagreeing_windows_do_not_auto_classify(self):
        voiced = np.ones(analyzer.SAMPLE_RATE * 4, dtype=np.float32)
        result, _remaining = self._run_with_windows(
            [(voiced, 4.0), (voiced, 4.0), (voiced, 4.0)],
            [(0.99, 0.01), (0.01, 0.99), (0.99, 0.01)],
        )
        self.assertEqual(result["gender"], "any")
        self.assertFalse(result["threshold_passed"])
        self.assertLess(result["agreement"], analyzer.MIN_DURATION_AGREEMENT)

    def test_benchmark_gate_keeps_high_confidence_result_advisory(self):
        voiced = np.ones(analyzer.SAMPLE_RATE * 4, dtype=np.float32)
        result, _remaining = self._run_with_windows(
            [(voiced, 4.0), (voiced, 4.0), (voiced, 4.0)],
            [(0.97, 0.03), (0.96, 0.04), (0.98, 0.02)],
            benchmark_enabled=False,
        )
        self.assertEqual(result["gender"], "any")
        self.assertEqual(result["predicted_timbre"], "male")
        self.assertEqual(result["status"], "advisory")
        self.assertTrue(result["threshold_passed"])

    def test_temporary_audio_is_removed_after_success(self):
        voiced = np.ones(analyzer.SAMPLE_RATE * 4, dtype=np.float32)
        result, remaining = self._run_with_windows(
            [(voiced, 4.0), (voiced, 4.0), (voiced, 4.0)],
            [(0.97, 0.03), (0.96, 0.04), (0.98, 0.02)],
        )
        self.assertEqual(result["gender"], "male")
        self.assertEqual(remaining, [])

    def test_network_error_does_not_expose_proxy_credentials(self):
        secret_proxy = "http://private-user:private-pass@127.0.0.1:8080"
        with tempfile.TemporaryDirectory() as temp_name, patch.object(
            analyzer, "TEMP_ROOT", Path(temp_name) / "temp"
        ), patch.object(
            analyzer, "ensure_model", return_value=Path(temp_name) / "model.onnx"
        ), patch.object(
            analyzer.ort, "InferenceSession", return_value=MagicMock()
        ), patch.object(
            analyzer, "_download_window", side_effect=RuntimeError(secret_proxy)
        ):
            with self.assertRaises(analyzer.SourceVoiceAnalysisError) as error:
                analyzer.analyze_source_voice(
                    "https://www.youtube.com/watch?v=abcdefghijk",
                    duration_seconds=600,
                    proxy=secret_proxy,
                )
        self.assertNotIn("private-user", str(error.exception))
        self.assertNotIn("private-pass", str(error.exception))


if __name__ == "__main__":
    unittest.main()
