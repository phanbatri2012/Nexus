"""Poem line preparation and deterministic VieNeu candidate quality checks."""

from __future__ import annotations

import difflib
import hashlib
import math
import re
import unicodedata
import wave
from collections import Counter
from pathlib import Path

import numpy as np


TERMINAL_PUNCTUATION = frozenset(".,!?;:…")
QC_VERSION = "vieneu-poem-qc-v3"
TRANSCRIPT_ACCEPTANCE_THRESHOLD = 0.75


def prepare_poem_lines(poem: str) -> list[dict]:
    raw_lines = str(poem or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_positions = [index for index, value in enumerate(raw_lines) if value.strip()]
    prepared: list[dict] = []
    for ordinal, raw_index in enumerate(content_positions):
        source = raw_lines[raw_index].strip()
        next_index = (
            content_positions[ordinal + 1]
            if ordinal + 1 < len(content_positions)
            else len(raw_lines)
        )
        stanza_end = ordinal == len(content_positions) - 1 or any(
            not raw_lines[index].strip()
            for index in range(raw_index + 1, next_index)
        )
        synthesis = source
        if source[-1] not in TERMINAL_PUNCTUATION:
            synthesis += "." if stanza_end else ","
        if stanza_end and ordinal < len(content_positions) - 1:
            gap_ms = 2200
        elif synthesis[-1] in ".!?…":
            gap_ms = 1200
        else:
            gap_ms = 900
        prepared.append(
            {
                "index": ordinal,
                "source_text": source,
                "synthesis_text": synthesis,
                "stanza_end": stanza_end,
                "gap_ms": gap_ms,
            }
        )
    return prepared


def deterministic_seed(
    *,
    item_id: str,
    audio_revision: int,
    line_index: int,
    variant: int,
) -> int:
    value = f"{item_id}:{int(audio_revision)}:{int(line_index)}:{int(variant)}"
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:4], "big")


def _read_pcm16(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        sample_width = source.getsampwidth()
        frames = source.readframes(source.getnframes())
    if sample_width != 2:
        raise ValueError("Candidate WAV phải là PCM 16-bit.")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(samples, dtype=np.float32), int(sample_rate)


def _frame_values(samples: np.ndarray, sample_rate: int) -> tuple[list[float], list[float]]:
    frame_size = max(256, int(sample_rate * 0.04))
    hop = max(128, int(sample_rate * 0.02))
    f0_values: list[float] = []
    energies: list[float] = []
    min_lag = max(1, int(sample_rate / 400))
    max_lag = max(min_lag + 1, int(sample_rate / 55))
    window = np.hanning(frame_size).astype(np.float32)
    for start in range(0, max(1, len(samples) - frame_size + 1), hop):
        frame = samples[start : start + frame_size]
        if len(frame) < frame_size:
            break
        rms = float(np.sqrt(np.mean(np.square(frame)) + 1e-12))
        if rms < 0.008:
            continue
        energies.append(rms)
        centered = (frame - float(np.mean(frame))) * window
        correlation = np.correlate(centered, centered, mode="full")[frame_size - 1 :]
        if max_lag >= len(correlation):
            continue
        windowed = correlation[min_lag:max_lag]
        lag = min_lag + int(np.argmax(windowed))
        confidence = float(correlation[lag] / max(correlation[0], 1e-9))
        if confidence >= 0.25:
            f0_values.append(float(sample_rate / lag))
    return f0_values, energies


def analyze_wav(path: Path) -> dict:
    samples, sample_rate = _read_pcm16(path)
    if sample_rate != 48000:
        raise ValueError("Candidate VieNeu phải là 48 kHz.")
    if not len(samples):
        raise ValueError("Candidate VieNeu rỗng.")
    absolute = np.abs(samples)
    peak = float(np.max(absolute))
    clipping_ratio = float(np.mean(absolute >= 0.999))
    rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-12))
    f0_values, energies = _frame_values(samples, sample_rate)
    f0_mean = float(np.mean(f0_values)) if f0_values else 0.0
    f0_variation = (
        float(np.std(f0_values) / max(f0_mean, 1e-9)) if len(f0_values) >= 2 else 0.0
    )
    energy_mean = float(np.mean(energies)) if energies else 0.0
    energy_variation = (
        float(np.std(energies) / max(energy_mean, 1e-9)) if len(energies) >= 2 else 0.0
    )
    tail_size = min(len(samples), int(sample_rate * 0.35))
    tail = samples[-tail_size:]
    tail_rms = float(np.sqrt(np.mean(np.square(tail)) + 1e-12))
    spectrum = np.abs(np.fft.rfft(tail * np.hanning(len(tail)))) + 1e-12
    frequencies = np.fft.rfftfreq(len(tail), 1.0 / sample_rate)
    high_frequency_ratio = float(
        np.sum(spectrum[frequencies >= 4000]) / max(np.sum(spectrum), 1e-9)
    )
    spectral_flatness = float(
        math.exp(float(np.mean(np.log(spectrum)))) / max(float(np.mean(spectrum)), 1e-12)
    )
    tail_rattle = bool(
        tail_rms > 0.003
        and high_frequency_ratio > 0.30
        and spectral_flatness > 0.20
    )
    technical_ok = bool(
        0.2 <= len(samples) / sample_rate <= 45.0
        and peak >= 0.01
        and clipping_ratio <= 0.0005
        and not tail_rattle
    )
    technical_score = max(
        0.0,
        min(
            1.0,
            1.0
            - min(1.0, clipping_ratio * 1500.0) * 0.55
            - min(1.0, high_frequency_ratio / 0.45) * 0.20
            - min(1.0, spectral_flatness / 0.50) * 0.15
            - (0.10 if peak > 0.97 else 0.0),
        ),
    )
    return {
        "duration_seconds": round(len(samples) / sample_rate, 3),
        "sample_rate": sample_rate,
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "clipping_ratio": round(clipping_ratio, 8),
        "f0_mean_hz": round(f0_mean, 3),
        "f0_variation": round(f0_variation, 6),
        "energy_variation": round(energy_variation, 6),
        "tail_rms": round(tail_rms, 6),
        "tail_high_frequency_ratio": round(high_frequency_ratio, 6),
        "tail_spectral_flatness": round(spectral_flatness, 6),
        "tail_rattle": tail_rattle,
        "technical_ok": technical_ok,
        "technical_score": round(technical_score, 6),
    }


def _normalized_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", str(value or "")).casefold()
    return re.findall(r"[0-9a-zà-ỹđ]+", normalized, flags=re.IGNORECASE)


def _fold_vietnamese_token(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def transcript_score(expected: str, actual: str) -> dict:
    expected_tokens = _normalized_tokens(expected)
    actual_tokens = _normalized_tokens(actual)
    same_word_count = len(actual_tokens) == len(expected_tokens)
    folded_expected = [_fold_vietnamese_token(token) for token in expected_tokens]
    folded_actual = [_fold_vietnamese_token(token) for token in actual_tokens]
    token_similarities = (
        [
            difflib.SequenceMatcher(None, left, right).ratio()
            for left, right in zip(folded_expected, folded_actual)
        ]
        if same_word_count
        else []
    )
    token_score = (
        float(sum(token_similarities) / len(token_similarities))
        if token_similarities
        else 0.0
    )
    character_score = difflib.SequenceMatcher(
        None,
        " ".join(folded_expected),
        " ".join(folded_actual),
    ).ratio()
    ratio = 0.70 * token_score + 0.30 * character_score
    expected_counts = Counter(expected_tokens)
    actual_counts = Counter(actual_tokens)
    repeated = any(
        actual_counts[token] > max(2, expected_counts[token] + 1)
        for token in actual_counts
    )
    word_count_ratio = len(actual_tokens) / max(1, len(expected_tokens))
    accepted = bool(
        expected_tokens
        and actual_tokens
        and same_word_count
        and ratio >= TRANSCRIPT_ACCEPTANCE_THRESHOLD
        and not repeated
    )
    return {
        "score": round(float(ratio), 6),
        "accepted": accepted,
        "expected_word_count": len(expected_tokens),
        "actual_word_count": len(actual_tokens),
        "word_count_ratio": round(word_count_ratio, 6),
        "same_word_count": same_word_count,
        "token_similarity": round(token_score, 6),
        "character_similarity": round(float(character_score), 6),
        "repeated_words": repeated,
    }


def _similarity(value: float, reference: float, *, floor: float = 0.01) -> float:
    left = max(abs(float(value)), floor)
    right = max(abs(float(reference)), floor)
    return max(0.0, min(1.0, min(left, right) / max(left, right)))


def score_candidate(
    *,
    expected_text: str,
    transcript: str,
    metrics: dict,
    reference_metrics: dict,
) -> dict:
    text = transcript_score(expected_text, transcript)
    prosody = (
        0.45
        * _similarity(
            float(metrics.get("f0_variation") or 0),
            float(reference_metrics.get("f0_variation") or 0),
        )
        + 0.35
        * _similarity(
            float(metrics.get("energy_variation") or 0),
            float(reference_metrics.get("energy_variation") or 0),
        )
        + 0.20
        * _similarity(
            float(metrics.get("tail_rms") or 0),
            float(reference_metrics.get("tail_rms") or 0),
            floor=0.001,
        )
    )
    technical = float(metrics.get("technical_score") or 0)
    accepted = bool(text["accepted"] and metrics.get("technical_ok"))
    total = 0.60 * float(text["score"]) + 0.25 * prosody + 0.15 * technical
    return {
        "accepted": accepted,
        "score": round(total, 6),
        "transcript": text,
        "prosody_score": round(prosody, 6),
        "technical_score": round(technical, 6),
        "rejection_reason": (
            ""
            if accepted
            else "transcript_mismatch"
            if not text["accepted"]
            else "technical_quality_failed"
        ),
    }
