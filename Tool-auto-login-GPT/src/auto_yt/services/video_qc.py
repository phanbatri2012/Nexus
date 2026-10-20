"""Automatic technical and completeness quality gate for Radar videos."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import imageio_ffmpeg
from PIL import Image

from auto_yt.dialogue_parser import is_dialogue_script
from auto_yt.services import video_production, youtube_publish_workflow, youtube_publisher


QC_STAGE_ORDER = (
    "script",
    "casting",
    "audio",
    "render",
    "thumbnail",
    "captions",
    "metadata",
)


def _result(passed: bool, detail: str = "", **metadata) -> dict:
    return {"passed": bool(passed), "detail": detail, **metadata}


def _local_audio_path(audio_task: dict | None, audio_dir: Path) -> Path | None:
    if not audio_task:
        return None
    audio_url = str(audio_task.get("audio_url") or "")
    filename = Path(unquote(urlsplit(audio_url).path)).name if audio_url else ""
    if filename:
        candidate = (audio_dir.resolve() / filename).resolve()
        try:
            candidate.relative_to(audio_dir.resolve())
        except ValueError:
            return None
        if candidate.is_file():
            return candidate
    request_hash = str(audio_task.get("request_hash") or "")
    video_id = int(audio_task.get("video_id") or 0)
    if request_hash and video_id:
        candidate = audio_dir / f"video_{video_id}_{request_hash[:16]}.mp3"
        if candidate.is_file():
            return candidate
    return None


def _decode_and_detect(path: Path, duration: float) -> dict:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=2:pix_th=0.98",
            "-af",
            "silencedetect=noise=-50dB:d=3",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=max(180, min(1800, int(duration * 3) + 60)),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("FFmpeg không decode được toàn bộ MP4.")
    diagnostics = str(result.stderr or "")
    black_durations = [
        float(value)
        for value in re.findall(r"black_duration:([0-9.]+)", diagnostics)
    ]
    silence_durations = [
        float(value)
        for value in re.findall(r"silence_duration:\s*([0-9.]+)", diagnostics)
    ]
    longest_black = max(black_durations, default=0.0)
    longest_silence = max(silence_durations, default=0.0)
    if longest_black >= max(2.0, duration * 0.2):
        raise RuntimeError("MP4 có đoạn hình đen kéo dài vượt ngưỡng.")
    if longest_silence >= max(10.0, duration * 0.2):
        raise RuntimeError("MP4 có đoạn im lặng kéo dài vượt ngưỡng.")
    return {
        "full_decode": True,
        "longest_black_seconds": round(longest_black, 3),
        "longest_silence_seconds": round(longest_silence, 3),
    }


def _validate_caption(path: Path) -> dict:
    if not path.is_file() or path.suffix.casefold() != ".srt" or path.stat().st_size <= 0:
        raise RuntimeError("Caption SRT không tồn tại hoặc rỗng.")
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    if not re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}", content):
        raise RuntimeError("Caption SRT không có timestamp hợp lệ.")
    spoken_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().isdigit() and "-->" not in line
    ]
    if not spoken_lines:
        raise RuntimeError("Caption SRT không có nội dung thoại.")
    return {"path": str(path), "lines": len(spoken_lines)}


def run_video_quality_checks(
    *,
    video: dict,
    artifact: dict,
    snapshot: dict,
    audio_task: dict | None,
    audio_review: dict | None,
    metadata_review: dict | None,
    cast_assignments: list[dict],
    active_voice_ids: set[str],
    caption_artifact: dict | None,
    audio_dir: Path,
    thumbnails_dir: Path,
    voice_policy: dict | None = None,
) -> dict:
    """Run QC without any source-topic or editorial relevance decision."""
    checks: dict[str, dict] = {}
    script = str(video.get("generated_script") or "")
    script_ok = bool(script.strip()) and (audio_review or {}).get("status") == "approved"
    checks["script"] = _result(
        script_ok,
        "Kịch bản và quality gate audio hợp lệ" if script_ok else "Kịch bản chưa đạt quality gate",
    )

    policy = voice_policy if isinstance(voice_policy, dict) else {}
    if is_dialogue_script(script):
        voice_ids = [str(item.get("voice_id") or "") for item in cast_assignments]
        mc_assignments = [
            item
            for item in cast_assignments
            if str(item.get("role_key") or "").upper() == "MC"
        ]
        has_mc = bool(mc_assignments)
        policy_ok = True
        if policy:
            expected_mc = str(policy.get("mc_voice_id") or "")
            allowed_hosts = set(policy.get("allowed_host_voice_ids") or [])
            allowed_guests = set(policy.get("allowed_guest_voice_ids") or [])
            policy_ok = (
                len(mc_assignments) == 1
                and str(mc_assignments[0].get("voice_id") or "") == expected_mc
                and expected_mc in allowed_hosts
                and all(
                    str(item.get("voice_id") or "") in allowed_guests
                    for item in cast_assignments
                    if str(item.get("role_key") or "").upper() != "MC"
                )
            )
        casting_ok = (
            bool(cast_assignments)
            and has_mc
            and len(voice_ids) == len(set(voice_ids))
            and all(voice_id in active_voice_ids for voice_id in voice_ids)
            and policy_ok
        )
    else:
        voice_ids = [str(video.get("voice_id") or "")]
        casting_ok = not policy or voice_ids[0] in set(
            policy.get("allowed_single_voice_ids") or []
        )
    checks["casting"] = _result(
        casting_ok,
        (
            "Cast đúng quyền, đúng pool và còn hoạt động"
            if casting_ok and policy
            else "Cast khác biệt và còn hoạt động"
            if casting_ok
            else "Cast thiếu, trùng, sai quyền/pool hoặc có voice không hoạt động"
        ),
        voice_ids=voice_ids,
        voice_pool=str(policy.get("guest_voice_pool") or ""),
    )

    audio_path = _local_audio_path(audio_task, audio_dir)
    audio_ok = bool(
        audio_task
        and audio_task.get("status") == "completed"
        and audio_path
        and audio_path.stat().st_size > 0
    )
    audio_duration = 0.0
    if audio_ok and audio_path:
        try:
            audio_duration = video_production.probe_media_duration(audio_path)
            audio_ok = audio_duration > 0
        except Exception:
            audio_ok = False
    checks["audio"] = _result(
        audio_ok,
        "Audio hoàn tất và đọc được" if audio_ok else "Audio chưa hoàn tất hoặc không đọc được",
        duration_seconds=round(audio_duration, 3),
    )

    video_path = Path(str(artifact.get("path") or ""))
    render_ok = bool(
        artifact
        and artifact.get("status") == "ready"
        and video_path.is_file()
        and video_path.suffix.casefold() == ".mp4"
        and video_path.stat().st_size > 0
        and audio_duration > 0
    )
    render_metadata: dict = {}
    if render_ok:
        try:
            render_metadata = video_production.validate_render(
                video_path,
                audio_duration,
                imageio_ffmpeg.get_ffmpeg_exe(),
            )
            render_metadata.update(
                _decode_and_detect(
                    video_path, float(render_metadata.get("duration_seconds") or audio_duration)
                )
            )
        except Exception as exc:
            render_ok = False
            render_metadata = {"error": str(exc)}
    checks["render"] = _result(
        render_ok,
        "MP4 đạt stream/duration/decode/black/silence" if render_ok else "MP4 không đạt kiểm tra kỹ thuật",
        **render_metadata,
    )

    thumbnail_path = youtube_publish_workflow.resolve_thumbnail_path_for_qc(
        script, snapshot, thumbnails_dir
    )
    thumbnail_ok = bool(thumbnail_path)
    thumbnail_metadata = {}
    if thumbnail_path:
        try:
            with Image.open(thumbnail_path) as image:
                image.verify()
            with Image.open(thumbnail_path) as image:
                width, height = image.size
            thumbnail_ok = width > 0 and height > 0
            thumbnail_metadata = {"path": str(thumbnail_path), "width": width, "height": height}
        except Exception:
            thumbnail_ok = False
    checks["thumbnail"] = _result(
        thumbnail_ok,
        "Thumbnail local đọc được" if thumbnail_ok else "Thumbnail thiếu hoặc hỏng",
        **thumbnail_metadata,
    )

    publishing_settings = snapshot.get("publishing_settings") or {}
    captions_required = bool(publishing_settings.get("upload_captions", True))
    caption_ok = not captions_required
    caption_metadata = {}
    if captions_required:
        try:
            caption_metadata = _validate_caption(
                Path(str((caption_artifact or {}).get("path") or ""))
            )
            caption_ok = True
        except Exception:
            caption_ok = False
    checks["captions"] = _result(
        caption_ok,
        "Caption hợp lệ" if caption_ok else "Caption thiếu hoặc không hợp lệ",
        required=captions_required,
        **caption_metadata,
    )

    metadata_ok = (metadata_review or {}).get("status") == "approved"
    metadata_detail = "Metadata đạt quality gate" if metadata_ok else "Metadata chưa đạt quality gate"
    if metadata_ok:
        try:
            metadata = youtube_publisher.build_upload_metadata(video, publishing_settings)
            _cleaned, quality = youtube_publisher.enforce_upload_metadata_quality(metadata, video)
            metadata_ok = True
            metadata_detail = (
                "Metadata upload đạt quality gate "
                f"({quality.get('decision') or 'pass'})"
            )
        except Exception:
            metadata_ok = False
            metadata_detail = "Metadata upload thiếu hoặc không hợp lệ"
    checks["metadata"] = _result(metadata_ok, metadata_detail)

    failed_stage = next(
        (stage for stage in QC_STAGE_ORDER if not checks.get(stage, {}).get("passed")),
        "",
    )
    return {
        "status": "passed" if not failed_stage else "failed",
        "failed_stage": failed_stage,
        "checks": checks,
    }
