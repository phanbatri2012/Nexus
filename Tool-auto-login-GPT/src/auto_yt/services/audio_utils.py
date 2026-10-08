import io
import os
import tempfile
import time
import uuid
from pathlib import Path

import av
import numpy as np

from auto_yt.services.network_security import (
    GENMAX_AUDIO_HOSTS,
    MAX_AUDIO_DOWNLOAD_BYTES,
    UnsafeRemoteResource,
    download_bounded,
)


DOWNLOAD_TIMEOUT_SECONDS = 90
MAX_SPEECH_WORDS_PER_SECOND = 6.0
FILE_REPLACE_RETRY_ATTEMPTS = 20
FILE_REPLACE_RETRY_DELAY_SECONDS = 0.05

_MPEG1_BITRATES = {
    1: (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    2: (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
}
_MPEG2_BITRATES = {
    1: (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    3: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = (44100, 48000, 32000)


class AudioContentError(RuntimeError):
    """Raised when a returned audio file is invalid or materially incomplete."""


def _decode_synchsafe(value: bytes) -> int:
    return (
        (value[0] << 21)
        | (value[1] << 14)
        | (value[2] << 7)
        | value[3]
    )


def _id3v2_end(data: bytes) -> int:
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    footer_size = 10 if data[5] & 0x10 else 0
    return min(len(data), 10 + _decode_synchsafe(data[6:10]) + footer_size)


def _parse_mp3_header(data: bytes, offset: int) -> dict | None:
    if offset + 4 > len(data):
        return None
    header = int.from_bytes(data[offset:offset + 4], "big")
    if header & 0xFFE00000 != 0xFFE00000:
        return None

    version_bits = (header >> 19) & 0b11
    layer_bits = (header >> 17) & 0b11
    bitrate_index = (header >> 12) & 0b1111
    sample_rate_index = (header >> 10) & 0b11
    padding = (header >> 9) & 1
    if (
        version_bits == 0b01
        or layer_bits == 0
        or bitrate_index in {0, 15}
        or sample_rate_index == 3
    ):
        return None

    version = {0b11: 1.0, 0b10: 2.0, 0b00: 2.5}[version_bits]
    layer = {0b11: 1, 0b10: 2, 0b01: 3}[layer_bits]
    bitrates = _MPEG1_BITRATES if version == 1.0 else _MPEG2_BITRATES
    bitrate_kbps = bitrates[layer][bitrate_index]
    sample_rate = _SAMPLE_RATES[sample_rate_index]
    if version == 2.0:
        sample_rate //= 2
    elif version == 2.5:
        sample_rate //= 4

    if layer == 1:
        frame_length = ((12 * bitrate_kbps * 1000) // sample_rate + padding) * 4
        samples_per_frame = 384
    elif layer == 3 and version != 1.0:
        frame_length = (72 * bitrate_kbps * 1000) // sample_rate + padding
        samples_per_frame = 576
    else:
        frame_length = (144 * bitrate_kbps * 1000) // sample_rate + padding
        samples_per_frame = 1152

    if frame_length <= 4 or offset + frame_length > len(data):
        return None
    return {
        "frame_length": frame_length,
        "sample_rate": sample_rate,
        "samples_per_frame": samples_per_frame,
        "encoding": (version, layer, sample_rate),
    }


def _find_first_frame(data: bytes) -> tuple[int, dict]:
    start = _id3v2_end(data)
    scan_end = min(len(data) - 4, start + 16_384)
    for offset in range(start, scan_end + 1):
        frame = _parse_mp3_header(data, offset)
        if frame:
            return offset, frame
    raise AudioContentError("The Genmax response is not a valid MP3 file.")


def extract_mp3_audio_frames(data: bytes) -> tuple[bytes, float, tuple]:
    """Return playable MP3 frames without container metadata and their duration."""
    offset, first_frame = _find_first_frame(data)
    output = bytearray()
    duration_seconds = 0.0
    encoding = first_frame["encoding"]
    frame_index = 0

    while offset + 4 <= len(data):
        frame = _parse_mp3_header(data, offset)
        if not frame:
            break
        if frame["encoding"] != encoding:
            raise AudioContentError(
                "Genmax returned MP3 segments with incompatible encodings."
            )

        frame_end = offset + frame["frame_length"]
        frame_data = data[offset:frame_end]
        is_metadata_frame = frame_index == 0 and any(
            marker in frame_data for marker in (b"Xing", b"Info", b"VBRI")
        )
        if not is_metadata_frame:
            output.extend(frame_data)
            duration_seconds += frame["samples_per_frame"] / frame["sample_rate"]
        offset = frame_end
        frame_index += 1

    if not output or duration_seconds <= 0:
        raise AudioContentError(
            "The Genmax MP3 file contains no playable audio frames."
        )
    return bytes(output), duration_seconds, encoding


def download_audio(audio_url: str) -> bytes:
    try:
        data = download_bounded(
            audio_url,
            allowed_hosts=GENMAX_AUDIO_HOSTS,
            allowed_content_types=("audio/mpeg", "audio/mp3", "application/octet-stream"),
            max_bytes=MAX_AUDIO_DOWNLOAD_BYTES,
            timeout_seconds=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except UnsafeRemoteResource as exc:
        raise AudioContentError(f"Genmax returned an unsafe audio resource: {exc}") from exc
    # Validate the container before callers persist or merge untrusted bytes.
    _find_first_frame(data)
    return data


def get_remote_mp3_duration(audio_url: str) -> float:
    _, duration_seconds, _ = extract_mp3_audio_frames(download_audio(audio_url))
    return duration_seconds


def validate_spoken_duration(
    text: str,
    duration_seconds: float,
    *,
    minimum_words_per_minute: float | None = None,
    provider_name: str = "Genmax",
) -> None:
    word_count = len(text.split())
    minimum_duration = word_count / MAX_SPEECH_WORDS_PER_SECOND
    if duration_seconds < minimum_duration:
        raise AudioContentError(
            f"{provider_name} returned incomplete audio: "
            f"{duration_seconds / 60:.1f} minutes for {word_count:,} words "
            f"(minimum expected {minimum_duration / 60:.1f} minutes)."
        )
    if minimum_words_per_minute is not None:
        minimum_wpm = float(minimum_words_per_minute)
        if minimum_wpm <= 0:
            raise ValueError("minimum_words_per_minute must be positive.")
        # A small fixed allowance avoids rejecting natural pauses in very
        # short samples while still catching systematic slow/hallucinated TTS.
        maximum_duration = max(
            8.0,
            word_count * 60.0 / minimum_wpm + 4.0,
        )
        if duration_seconds > maximum_duration:
            raise AudioContentError(
                f"{provider_name} returned implausibly long audio: "
                f"{duration_seconds / 60:.1f} minutes for {word_count:,} words "
                f"(maximum expected {maximum_duration / 60:.1f} minutes)."
            )


def _replace_file_with_retry(source_path: Path, output_path: Path) -> None:
    for attempt in range(FILE_REPLACE_RETRY_ATTEMPTS):
        try:
            os.replace(source_path, output_path)
            return
        except PermissionError:
            if attempt == FILE_REPLACE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(FILE_REPLACE_RETRY_DELAY_SECONDS)


def decode_audio_stream(
    source: bytes | Path | str,
    target_sr: int = 44100,
) -> tuple[np.ndarray, float]:
    """Decode audio bytes or a file path into a float32 stereo PCM numpy array (2, N) at target_sr."""
    if isinstance(source, bytes):
        container = av.open(io.BytesIO(source))
    else:
        container = av.open(str(source))

    if not container.streams.audio:
        container.close()
        raise AudioContentError("File âm thanh không chứa audio stream hợp lệ.")

    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=target_sr)

    chunks = []
    total_samples = 0
    for frame in container.decode(stream):
        for resampled_frame in resampler.resample(frame):
            arr = resampled_frame.to_ndarray()
            chunks.append(arr)
            total_samples += arr.shape[1]

    for resampled_frame in resampler.resample(None):
        arr = resampled_frame.to_ndarray()
        chunks.append(arr)
        total_samples += arr.shape[1]

    container.close()

    if not chunks:
        return np.zeros((2, 0), dtype=np.float32), 0.0

    full_audio = np.concatenate(chunks, axis=1)
    duration = total_samples / target_sr
    return full_audio, duration


def _load_and_decode_segment(
    source: dict | str | bytes | Path,
    target_sr: int = 44100,
) -> tuple[np.ndarray, float]:
    """Fetch/download audio from any provider or format and decode to PCM."""
    if isinstance(source, dict):
        url = str(source.get("audio_url") or "").strip()
        task_id = str(source.get("task_id") or "").strip()
    elif isinstance(source, (str, Path)):
        url = str(source).strip()
        task_id = ""
    else:
        # Raw bytes
        return decode_audio_stream(source, target_sr=target_sr)

    if not url:
        raise AudioContentError("Segment thiếu audio_url để tải âm thanh.")

    # Handle OmniVoice local jobs
    if url.startswith("omnivoice://") or task_id.startswith("omnivoice:"):
        from auto_yt.services import omnivoice_client
        raw_job_id = url.removeprefix("omnivoice://") if url.startswith("omnivoice://") else task_id.removeprefix("omnivoice:")
        raw_bytes = omnivoice_client._request(f"/v1/jobs/{raw_job_id}/audio", timeout=30 * 60)
        return decode_audio_stream(raw_bytes, target_sr=target_sr)

    # Handle HTTP/HTTPS remote URLs (e.g. Genmax)
    if url.startswith("http://") or url.startswith("https://"):
        raw_bytes = download_audio(url)
        return decode_audio_stream(raw_bytes, target_sr=target_sr)

    # Handle local file paths
    file_path = Path(url)
    if file_path.exists():
        return decode_audio_stream(file_path, target_sr=target_sr)

    raise AudioContentError(f"Không thể nạp segment âm thanh từ nguồn: {url}")


def merge_audio_segments_to_mp3(
    sources: list[dict | str | bytes | Path],
    output_path: Path,
    expected_texts: list[str] | None = None,
    pause_between_turns: float = 0.25,
    sample_rate: int = 44100,
    bit_rate: int = 192000,
) -> float:
    """
    Decodes audio segments from any provider (OmniVoice local WAVs or Genmax remote MP3s),
    applies a 10ms boundary fade-in/fade-out to eliminate clicks/pops,
    inserts natural pause between speaker transitions, and encodes to a pristine,
    standards-compliant MP3 file with full Xing header & ID3 container.
    """
    if not sources:
        raise ValueError("At least one audio source is required.")
    if expected_texts is not None and len(expected_texts) != len(sources):
        raise ValueError("Each audio source must have matching expected text.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.{uuid.uuid4().hex}.tmp")

    fade_samples = int(0.010 * sample_rate)  # 10ms = 441 samples
    fade_in_envelope = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    fade_out_envelope = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)

    pause_samples = int(pause_between_turns * sample_rate)
    pause_pcm = np.zeros((2, pause_samples), dtype=np.float32) if pause_samples > 0 else None

    total_samples = 0

    try:
        out_container = av.open(str(temporary_path), mode="w", format="mp3")
        out_stream = out_container.add_stream("mp3", rate=sample_rate)
        out_stream.bit_rate = bit_rate
        out_stream.layout = "stereo"

        pts_counter = 0
        frame_chunk_size = 1152  # Standard MP3 MPEG frame size
        buffer_pcm = np.zeros((2, 0), dtype=np.float32)

        for index, source in enumerate(sources):
            pcm, duration_seconds = _load_and_decode_segment(source, target_sr=sample_rate)

            if expected_texts is not None:
                validate_spoken_duration(expected_texts[index], duration_seconds)

            if pcm.shape[1] > 0:
                # Apply 10ms fade-in / fade-out
                if pcm.shape[1] >= fade_samples * 2:
                    pcm[:, :fade_samples] *= fade_in_envelope
                    pcm[:, -fade_samples:] *= fade_out_envelope
                elif pcm.shape[1] > 0:
                    half = pcm.shape[1] // 2
                    if half > 0:
                        pcm[:, :half] *= np.linspace(0.0, 1.0, half, dtype=np.float32)
                        pcm[:, -half:] *= np.linspace(1.0, 0.0, half, dtype=np.float32)

                buffer_pcm = np.concatenate([buffer_pcm, pcm], axis=1)

                # Add pause if not the last segment
                if index < len(sources) - 1 and pause_pcm is not None:
                    buffer_pcm = np.concatenate([buffer_pcm, pause_pcm], axis=1)

            # Encode complete 1152-sample frames from buffer_pcm
            while buffer_pcm.shape[1] >= frame_chunk_size:
                chunk = buffer_pcm[:, :frame_chunk_size]
                buffer_pcm = buffer_pcm[:, frame_chunk_size:]

                frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                frame.rate = sample_rate
                frame.pts = pts_counter
                pts_counter += frame_chunk_size
                total_samples += frame_chunk_size

                for packet in out_stream.encode(frame):
                    out_container.mux(packet)

        # Encode remaining samples in buffer
        if buffer_pcm.shape[1] > 0:
            rem_len = buffer_pcm.shape[1]
            pad_len = frame_chunk_size - rem_len
            padded_chunk = np.pad(buffer_pcm, ((0, 0), (0, pad_len)), mode="constant")
            frame = av.AudioFrame.from_ndarray(padded_chunk, format="fltp", layout="stereo")
            frame.rate = sample_rate
            frame.pts = pts_counter
            total_samples += rem_len
            for packet in out_stream.encode(frame):
                out_container.mux(packet)

        # Flush encoder
        for packet in out_stream.encode(None):
            out_container.mux(packet)

        out_container.close()

        _replace_file_with_retry(temporary_path, output_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
        raise

    final_duration = total_samples / sample_rate
    return final_duration


def merge_remote_mp3_files(
    audio_urls: list[str | dict],
    output_path: Path,
    expected_texts: list[str] | None = None,
) -> float:
    """Wrapper calling merge_audio_segments_to_mp3 for universal MP3 stitching."""
    return merge_audio_segments_to_mp3(
        sources=audio_urls,
        output_path=output_path,
        expected_texts=expected_texts,
        pause_between_turns=0.25,
    )
