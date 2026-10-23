"""Analyze dominant source-video vocal timbre for Radar casting fallback."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import onnxruntime as ort
from faster_whisper.vad import get_speech_timestamps
from yt_dlp import YoutubeDL

from nexus_engine.paths import DATA_DIR
from nexus_engine.services.proxy_utils import ProxyConfigurationError, parse_proxy_url
from nexus_engine.services.youtube_service import validate_youtube_video_url


MODEL_REPOSITORY = "Alice-Sabrina-Ivy/voice-gender-classifier-onnx-q8"
MODEL_REVISION = "27dcf77b4a1028590ee14db10c2e57edd02e4c61"
MODEL_FILENAME = "model_quantized.onnx"
MODEL_SHA256 = "33a7f84f17d5ceee61aae8b23488e06fc1e3295a44eaf7eb5744f3e01e380784"
MODEL_URL = (
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/"
    f"onnx/{MODEL_FILENAME}"
)
MODEL_CACHE_DIR = DATA_DIR / "model_cache" / "voice_gender_ecapa_q8"
MODEL_PATH = MODEL_CACHE_DIR / MODEL_FILENAME
BENCHMARK_PATH = DATA_DIR / "source_voice_benchmark.json"
TEMP_ROOT = DATA_DIR / "tmp" / "source_voice"
SAMPLE_RATE = 16_000
WINDOW_SECONDS = 12.0
WINDOW_RATIOS = (0.2, 0.5, 0.8)
MIN_TOTAL_SPEECH_SECONDS = 9.0
MIN_AGGREGATE_PROBABILITY = 0.85
MIN_DURATION_AGREEMENT = 0.80
MAX_MODEL_BYTES = 20 * 1024 * 1024


class SourceVoiceAnalysisError(RuntimeError):
    """Raised when source analysis cannot safely produce evidence."""


class SourceVoiceModelError(SourceVoiceAnalysisError):
    """Raised when the pinned model is missing, corrupt, or cannot run."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def benchmark_gate_status() -> dict:
    result = {
        "enabled": False,
        "mode": "advisory",
        "reason": "Chưa có benchmark tiếng Việt đạt điều kiện 10 nam + 10 nữ.",
    }
    try:
        payload = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return result
    try:
        male_count = int(payload.get("male_count") or 0)
        female_count = int(payload.get("female_count") or 0)
        error_rate = float(payload.get("error_rate") or 1.0)
    except (TypeError, ValueError, OverflowError):
        return result
    if (
        male_count < 0
        or female_count < 0
        or not math.isfinite(error_rate)
        or not 0.0 <= error_rate <= 1.0
    ):
        return result
    enabled = male_count >= 10 and female_count >= 10 and error_rate <= 0.05
    return {
        "enabled": enabled,
        "mode": "auto" if enabled else "advisory",
        "reason": (
            "Benchmark tiếng Việt đạt ngưỡng."
            if enabled
            else "Benchmark tiếng Việt chưa đạt ngưỡng sai số tối đa 5%."
        ),
        "male_count": male_count,
        "female_count": female_count,
        "error_rate": error_rate,
    }


def model_status() -> dict:
    status = "missing"
    actual_checksum = ""
    if MODEL_PATH.is_file():
        try:
            actual_checksum = _sha256(MODEL_PATH)
            status = "ready" if actual_checksum == MODEL_SHA256 else "invalid"
        except OSError:
            status = "invalid"
    return {
        "status": status,
        "revision": MODEL_REVISION,
        "checksum": MODEL_SHA256,
        "actual_checksum": actual_checksum,
        "size_bytes": MODEL_PATH.stat().st_size if MODEL_PATH.is_file() else 0,
        "benchmark": benchmark_gate_status(),
        "classifier_scope": "binary_vocal_timbre",
    }


def ensure_model() -> Path:
    current = model_status()
    if current["status"] == "ready":
        return MODEL_PATH
    if current["status"] == "invalid":
        raise SourceVoiceModelError(
            "Model phân tích âm sắc có checksum không hợp lệ; không dùng fallback."
        )
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    partial = MODEL_CACHE_DIR / f".{MODEL_FILENAME}.{os.getpid()}.part"
    try:
        request = urllib.request.Request(
        MODEL_URL, headers={"User-Agent": "Nexus-Studio-Engine/source-voice-model"}
        )
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            total = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_MODEL_BYTES:
                    raise SourceVoiceModelError("Artifact model vượt giới hạn 20 MB.")
                output.write(block)
        if _sha256(partial) != MODEL_SHA256:
            raise SourceVoiceModelError("Artifact model tải về sai checksum.")
        os.replace(partial, MODEL_PATH)
    except SourceVoiceModelError:
        raise
    except Exception as exc:
        raise SourceVoiceModelError("Không thể tải model phân tích âm sắc đã pin.") from exc
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
    return MODEL_PATH


def _window_bounds(duration_seconds: float) -> list[tuple[float, float]]:
    if duration_seconds <= 0:
        raise SourceVoiceAnalysisError("Thiếu thời lượng video nguồn.")
    bounds = []
    for ratio in WINDOW_RATIOS:
        center = duration_seconds * ratio
        start = max(0.0, min(center - WINDOW_SECONDS / 2.0, duration_seconds - WINDOW_SECONDS))
        end = min(duration_seconds, start + WINDOW_SECONDS)
        bounds.append((round(start, 3), round(end, 3)))
    return bounds


def _download_window(
    url: str,
    *,
    proxy_url: str,
    start: float,
    end: float,
    output_stem: Path,
) -> Path:
    template = str(output_stem) + ".%(ext)s"
    options = {
        "format": "bestaudio/best",
        "outtmpl": template,
        "proxy": proxy_url,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "download_sections": [f"*{start}-{end}"],
        "force_keyframes_at_cuts": True,
    }
    with YoutubeDL(options) as downloader:
        downloader.download([url])
    matches = [
        path
        for path in output_stem.parent.glob(output_stem.name + ".*")
        if path.suffix not in {".part", ".ytdl", ".wav"} and path.is_file()
    ]
    if not matches:
        raise SourceVoiceAnalysisError("yt-dlp không tạo được audio mẫu qua proxy.")
    return max(matches, key=lambda path: path.stat().st_size)


def _convert_to_mono_wav(source: Path, target: Path) -> None:
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    completed = subprocess.run(command, capture_output=True, check=False, timeout=90)
    if completed.returncode != 0 or not target.is_file():
        raise SourceVoiceAnalysisError("Không thể chuẩn hóa audio mẫu về mono 16 kHz.")


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != SAMPLE_RATE:
            raise SourceVoiceAnalysisError("Audio mẫu không đúng mono 16 kHz.")
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    return samples.astype(np.float32) / 32768.0


def _voiced_audio(audio: np.ndarray) -> tuple[np.ndarray, float]:
    timestamps = get_speech_timestamps(audio, sampling_rate=SAMPLE_RATE)
    chunks = []
    sample_count = 0
    for timestamp in timestamps:
        start = max(0, int(timestamp.get("start") or 0))
        end = min(len(audio), int(timestamp.get("end") or 0))
        if end > start:
            chunks.append(audio[start:end])
            sample_count += end - start
    if not chunks:
        return np.empty(0, dtype=np.float32), 0.0
    return np.concatenate(chunks), sample_count / SAMPLE_RATE


def _softmax(logits: np.ndarray) -> np.ndarray:
    flattened = np.asarray(logits, dtype=np.float64).reshape(-1)
    shifted = flattened - np.max(flattened)
    probabilities = np.exp(shifted)
    return probabilities / np.sum(probabilities)


def _classify(session: ort.InferenceSession, audio: np.ndarray) -> tuple[float, float]:
    input_name = session.get_inputs()[0].name
    logits = session.run(None, {input_name: audio.reshape(1, -1).astype(np.float32)})[0]
    probabilities = _softmax(logits)
    if probabilities.size != 2 or not np.all(np.isfinite(probabilities)):
        raise SourceVoiceModelError("Model trả về logits không hợp lệ.")
    return float(probabilities[0]), float(probabilities[1])


def analyze_source_voice(
    url: str,
    *,
    duration_seconds: float,
    proxy: str,
) -> dict:
    """Return vocal-timbre evidence; never infer a person's gender identity."""
    validated_url = validate_youtube_video_url(url)
    proxy_url = parse_proxy_url(proxy)
    if not proxy_url:
        raise ProxyConfigurationError(
            "Kênh đích thiếu proxy GPM hợp lệ; phân tích giọng nguồn trực tiếp đã bị chặn."
        )
    model_path = ensure_model()
    benchmark = benchmark_gate_status()
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    window_results: list[dict] = []
    try:
        session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
    except Exception as exc:
        raise SourceVoiceModelError("Không thể khởi tạo model phân tích âm sắc.") from exc

    try:
        with tempfile.TemporaryDirectory(prefix="radar-", dir=TEMP_ROOT) as temp_name:
            temp_dir = Path(temp_name)
            for index, (start, end) in enumerate(_window_bounds(float(duration_seconds))):
                source = _download_window(
                    validated_url,
                    proxy_url=proxy_url,
                    start=start,
                    end=end,
                    output_stem=temp_dir / f"window_{index}",
                )
                wav_path = temp_dir / f"window_{index}.wav"
                _convert_to_mono_wav(source, wav_path)
                voiced, speech_seconds = _voiced_audio(_read_wav(wav_path))
                if speech_seconds <= 0:
                    window_results.append(
                        {
                            "index": index,
                            "speech_seconds": 0.0,
                            "male_probability": 0.0,
                            "female_probability": 0.0,
                            "label": "any",
                        }
                    )
                    continue
                male_probability, female_probability = _classify(session, voiced)
                window_results.append(
                    {
                        "index": index,
                        "speech_seconds": round(speech_seconds, 3),
                        "male_probability": male_probability,
                        "female_probability": female_probability,
                        "label": "male" if male_probability >= female_probability else "female",
                    }
                )
    except (ProxyConfigurationError, SourceVoiceAnalysisError, SourceVoiceModelError):
        raise
    except Exception as exc:
        raise SourceVoiceAnalysisError("Phân tích audio nguồn thất bại.") from exc

    total_speech = sum(item["speech_seconds"] for item in window_results)
    if total_speech > 0:
        male_probability = sum(
            item["male_probability"] * item["speech_seconds"] for item in window_results
        ) / total_speech
        female_probability = sum(
            item["female_probability"] * item["speech_seconds"] for item in window_results
        ) / total_speech
    else:
        male_probability = female_probability = 0.0
    predicted = "male" if male_probability >= female_probability else "female"
    confidence = max(male_probability, female_probability)
    agreeing_seconds = sum(
        item["speech_seconds"] for item in window_results if item["label"] == predicted
    )
    agreement = agreeing_seconds / total_speech if total_speech else 0.0
    threshold_passed = (
        total_speech >= MIN_TOTAL_SPEECH_SECONDS
        and confidence >= MIN_AGGREGATE_PROBABILITY
        and agreement >= MIN_DURATION_AGREEMENT
    )
    auto_enabled = bool(benchmark.get("enabled"))
    accepted = threshold_passed and auto_enabled
    status = (
        "ready"
        if accepted
        else "advisory"
        if threshold_passed and not auto_enabled
        else "insufficient_speech"
        if total_speech < MIN_TOTAL_SPEECH_SECONDS
        else "uncertain"
    )
    return {
        "status": status,
        "gender": predicted if accepted else "any",
        "predicted_timbre": predicted if total_speech else "any",
        "confidence": round(confidence, 6),
        "male_probability": round(male_probability, 6),
        "female_probability": round(female_probability, 6),
        "speech_seconds": round(total_speech, 3),
        "agreement": round(agreement, 6),
        "threshold_passed": threshold_passed,
        "benchmark_gate": benchmark,
        "model_revision": MODEL_REVISION,
        "model_checksum": MODEL_SHA256,
        "windows": window_results,
    }
