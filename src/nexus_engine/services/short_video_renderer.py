"""FFmpeg renderer and QC for voice-only vertical Short Video artifacts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg

from nexus_engine.services import process_registry, security_logging


class ShortVideoRenderError(RuntimeError):
    pass


def _ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffprobe_exe() -> str:
    ffmpeg = Path(_ffmpeg_exe())
    sibling = ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.casefold() == ".exe" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    located = shutil.which("ffprobe")
    if located:
        return located
    raise ShortVideoRenderError("Không tìm thấy ffprobe để kiểm tra media Short Video.")


def _run_process(
    command: list[str],
    *,
    job_id: str,
    timeout: float = 900,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    process_registry.register_process(job_id, process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ShortVideoRenderError("FFmpeg Short Video vượt thời gian xử lý.") from exc
    finally:
        process_registry.unregister_process(job_id, process)
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if completed.returncode != 0:
        safe_error = security_logging.redact_sensitive(completed.stderr or "")
        lines = [line.strip() for line in safe_error.splitlines() if line.strip()]
        detail = "\n".join(lines[-8:])[-1800:]
        raise ShortVideoRenderError(detail or "FFmpeg không thể tạo media Short Video.")
    return completed


def normalize_audio(source: Path, destination: Path, *, job_id: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".tmp" + destination.suffix)
    temporary.unlink(missing_ok=True)
    _run_process(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        job_id=job_id,
        timeout=300,
    )
    os.replace(temporary, destination)
    return destination


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            _ffprobe_exe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise ShortVideoRenderError("ffprobe không đọc được artifact Short Video.")
    try:
        payload = json.loads(result.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ShortVideoRenderError("Metadata media Short Video không hợp lệ.") from exc
    return {
        "duration_seconds": duration,
        "streams": payload.get("streams") if isinstance(payload.get("streams"), list) else [],
    }


def probe_duration(path: Path) -> float:
    duration = float(probe(path).get("duration_seconds") or 0)
    if duration <= 0:
        raise ShortVideoRenderError("Audio Short Video không có thời lượng hợp lệ.")
    return duration


def _ratio(value: str) -> float:
    try:
        numerator, denominator = str(value or "0/1").split("/", 1)
        return float(numerator) / max(float(denominator), 0.000001)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _black_ratio(path: Path, duration: float, *, job_id: str) -> float:
    result = _run_process(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.5:pix_th=0.98",
            "-an",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        job_id=job_id,
        timeout=300,
    )
    black_seconds = sum(
        float(match)
        for match in re.findall(r"black_duration:([0-9.]+)", result.stderr or "")
    )
    return round(black_seconds / max(duration, 0.001), 6)


def _max_sampled_saturation(path: Path, *, job_id: str) -> float:
    result = _run_process(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            "fps=1,signalstats,metadata=print",
            "-frames:v",
            "24",
            "-an",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        job_id=job_id,
        timeout=300,
    )
    values = [
        float(value)
        for value in re.findall(
            r"lavfi\.signalstats\.SATAVG=([0-9.]+)",
            f"{result.stdout}\n{result.stderr}",
        )
    ]
    if not values:
        raise ShortVideoRenderError("QC không đọc được độ bão hòa của video Short.")
    return round(max(values), 6)


def validate_render(
    path: Path,
    *,
    expected_duration: float,
    width: int,
    height: int,
    fps: int,
    job_id: str,
    monochrome: bool = False,
) -> dict:
    metadata = probe(path)
    streams = metadata["streams"]
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    subtitle = next((stream for stream in streams if stream.get("codec_type") == "subtitle"), None)
    if not video or not audio:
        raise ShortVideoRenderError("Video Short thiếu stream hình hoặc giọng đọc.")
    if subtitle:
        raise ShortVideoRenderError("Video Short không được chứa subtitle stream.")
    if int(video.get("width") or 0) != width or int(video.get("height") or 0) != height:
        raise ShortVideoRenderError(f"Video Short không đúng độ phân giải {width}x{height}.")
    if str(video.get("codec_name") or "").casefold() != "h264":
        raise ShortVideoRenderError("Video Short không dùng codec H.264.")
    if str(audio.get("codec_name") or "").casefold() != "aac":
        raise ShortVideoRenderError("Video Short không dùng codec AAC.")
    actual_fps = _ratio(str(video.get("r_frame_rate") or ""))
    if abs(actual_fps - fps) > 0.05:
        raise ShortVideoRenderError(f"Video Short không đúng {fps} fps.")
    duration = float(metadata.get("duration_seconds") or 0)
    if duration <= 0 or abs(duration - expected_duration) > max(1.0, expected_duration * 0.01):
        raise ShortVideoRenderError("Thời lượng video Short lệch quá giới hạn cho phép.")
    black_ratio = _black_ratio(path, duration, job_id=job_id)
    if black_ratio > 0.02:
        raise ShortVideoRenderError("QC phát hiện quá nhiều khung hình đen trong video Short.")
    max_saturation = _max_sampled_saturation(path, job_id=job_id) if monochrome else None
    if monochrome and float(max_saturation or 0) > 2.0:
        raise ShortVideoRenderError("QC phát hiện video Character Profile chưa hoàn toàn đen trắng.")
    return {
        "duration_seconds": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(actual_fps, 3),
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio.get("codec_name") or ""),
        "black_ratio": black_ratio,
        "has_audio": True,
        "has_subtitle_stream": False,
        "text_overlay_enabled": False,
        "monochrome_required": monochrome,
        "monochrome_verified": bool(monochrome and float(max_saturation or 0) <= 2.0),
        "max_sampled_saturation": max_saturation,
    }


def _scene_filter(
    width: int,
    height: int,
    fps: int,
    duration: float,
    index: int,
    *,
    monochrome: bool = False,
) -> str:
    frames = max(1, round(duration * fps))
    direction = -1 if index % 2 else 1
    zoom_step = 0.00045 * direction
    if direction > 0:
        zoom = f"min(zoom+{zoom_step:.5f},1.08)"
    else:
        zoom = f"if(eq(on,1),1.08,max(1.0,zoom{zoom_step:.5f}))"
    filters = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{zoom}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
        f"d={frames}:s={width}x{height}:fps={fps}"
    )
    if monochrome:
        filters += ",hue=s=0"
    return filters + ",format=yuv420p"


def render(
    *,
    scenes: list[dict],
    audio_path: Path,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    job_id: str,
    monochrome: bool = False,
) -> dict:
    if not scenes:
        raise ShortVideoRenderError("Không có scene để dựng Short Video.")
    expected_duration = probe_duration(audio_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    work_dir = destination.parent / f".{destination.stem}_segments"
    work_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    for index, scene in enumerate(scenes):
        duration = max(0.05, float(scene.get("duration") or 0))
        media_path = Path(str(scene.get("video_path") or scene.get("image_path") or ""))
        if not media_path.is_file():
            raise ShortVideoRenderError(f"Scene {index + 1} chưa có media hợp lệ.")
        segment = work_dir / f"scene_{index:03d}.mp4"
        filter_graph = _scene_filter(
            width,
            height,
            fps,
            duration,
            index,
            monochrome=monochrome,
        )
        if scene.get("video_path"):
            input_args = ["-stream_loop", "-1", "-i", str(media_path)]
        else:
            input_args = ["-loop", "1", "-i", str(media_path)]
        _run_process(
            [
                _ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *input_args,
                "-t",
                f"{duration:.3f}",
                "-vf",
                filter_graph,
                "-an",
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "19",
                "-pix_fmt",
                "yuv420p",
                str(segment),
            ],
            job_id=job_id,
        )
        segments.append(segment)

    concat_file = work_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(
            "file '" + segment.resolve().as_posix().replace("'", "'\\''") + "'"
            for segment in segments
        ),
        encoding="utf-8",
    )
    visual_track = work_dir / "visuals.mp4"
    _run_process(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(visual_track),
        ],
        job_id=job_id,
    )
    temporary = destination.with_name(destination.stem + ".tmp" + destination.suffix)
    temporary.unlink(missing_ok=True)
    _run_process(
        [
            _ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(visual_track),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{expected_duration:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        job_id=job_id,
    )
    os.replace(temporary, destination)
    qc = validate_render(
        destination,
        expected_duration=expected_duration,
        width=width,
        height=height,
        fps=fps,
        job_id=job_id,
        monochrome=monochrome,
    )
    return {"path": str(destination), "qc": qc}
