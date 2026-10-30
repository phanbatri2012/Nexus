"""On-demand VieNeu audio preview jobs for Short Video projects."""

from __future__ import annotations

import re
from pathlib import Path

from nexus_engine.paths import SHORT_VIDEO_DIR
from nexus_engine.services import (
    database as db,
    security_logging,
    short_video_audio,
    short_video_renderer,
    vieneu_tts,
)


class ShortVideoAudioPreviewError(RuntimeError):
    pass


def _preview_directory(preview_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{32,36}", str(preview_id or "").casefold()):
        raise ShortVideoAudioPreviewError("Preview ID không hợp lệ.")
    root = (SHORT_VIDEO_DIR / "_audio_previews").resolve()
    target = (root / preview_id).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ShortVideoAudioPreviewError("Namespace preview không hợp lệ.") from exc
    target.mkdir(parents=True, exist_ok=True)
    return target


def audio_path(preview: dict) -> Path:
    filename = str(preview.get("audio_filename") or "")
    if filename != "preview.wav":
        raise FileNotFoundError("Bản nghe thử chưa sẵn sàng.")
    path = (_preview_directory(str(preview["id"])) / filename).resolve()
    if not path.is_file():
        raise FileNotFoundError("Artifact nghe thử không còn tồn tại.")
    return path


def _check_canceled(job_id: str) -> None:
    latest = db.get_system_job(job_id)
    if latest and latest.get("cancel_requested"):
        raise ShortVideoAudioPreviewError("Bản nghe thử đã bị hủy.")


def execute(job: dict) -> None:
    payload = job.get("payload") or {}
    preview_id = str(payload.get("preview_id") or "")
    preview = db.get_short_video_audio_preview(preview_id)
    if preview is None:
        raise ShortVideoAudioPreviewError("Không tìm thấy bản nghe thử.")
    directory = _preview_directory(preview_id)
    final_path = directory / "preview.wav"
    if preview.get("status") == "completed" and final_path.is_file():
        db.update_system_job(
            str(job["id"]),
            status="completed",
            progress="Bản nghe thử đã sẵn sàng",
            result_json={"preview_id": preview_id},
            error="",
            finished_at=db.utc_now(),
        )
        return

    profile = payload.get("audio_profile") or {}
    project_key = str(preview.get("project_key") or "")
    profile_version = int(profile.get("profile_version") or 1)
    reference_path, status = vieneu_tts.profile_source_reference(
        project_key,
        profile_version,
    )
    runtime = status.get("runtime") or {}
    if str(runtime.get("lock_sha256") or "") != str(
        profile.get("runtime_lock_sha256") or ""
    ):
        raise ShortVideoAudioPreviewError("Runtime VieNeu đã đổi so với request preview.")
    if str(status.get("reference_sha256") or "") != str(
        profile.get("reference_sha256") or ""
    ):
        raise ShortVideoAudioPreviewError("Reference VieNeu đã đổi so với request preview.")
    lines = short_video_audio.prepare_poem_lines(str(preview.get("poem_excerpt") or ""))
    if not lines or len(lines) > 4:
        raise ShortVideoAudioPreviewError("Preview phải có từ một đến bốn dòng thơ.")
    db.update_short_video_audio_preview(preview_id, status="processing", error="")
    db.update_system_job(
        str(job["id"]),
        progress="Đang tạo bản nghe thử VieNeu",
        resume_from_step="audio_preview",
    )
    selected: list[tuple[Path, int]] = []
    with vieneu_tts.VieNeuGenerationSession(
        job_id=str(job["id"]),
        reference_path=reference_path,
        work_dir=directory,
    ) as session:
        for line in lines:
            _check_canceled(str(job["id"]))
            line_path = directory / f"line_{int(line['index']):03d}.wav"
            if not line_path.is_file():
                seed = short_video_audio.deterministic_seed(
                    item_id=preview_id,
                    audio_revision=0,
                    line_index=int(line["index"]),
                    variant=1,
                )
                session.generate(
                    text=str(line["synthesis_text"]),
                    seed=seed,
                    output_path=line_path,
                )
            metrics = short_video_audio.analyze_wav(line_path)
            if not metrics.get("technical_ok"):
                raise ShortVideoAudioPreviewError(
                    f"Candidate preview dòng {int(line['index']) + 1} không đạt QC kỹ thuật."
                )
            selected.append((line_path, int(line["gap_ms"])))
            db.update_system_job(
                str(job["id"]),
                progress=f"Đã tạo dòng {int(line['index']) + 1}/{len(lines)} của bản nghe thử",
                resume_from_step=f"audio_preview_line_{line['index']}",
            )
    source_path = directory / "preview.source.wav"
    short_video_renderer.assemble_poem_audio(
        lines=selected,
        source_destination=source_path,
        final_destination=final_path,
        job_id=str(job["id"]),
    )
    duration = short_video_renderer.probe_duration(final_path)
    db.update_short_video_audio_preview(
        preview_id,
        status="completed",
        audio_filename="preview.wav",
        duration_seconds=round(duration, 3),
        error="",
    )
    db.update_system_job(
        str(job["id"]),
        status="completed",
        progress="Bản nghe thử VieNeu đã sẵn sàng",
        result_json={"preview_id": preview_id},
        error="",
        cancel_requested=0,
        finished_at=db.utc_now(),
    )


def handle_error(job: dict, exc: Exception) -> None:
    preview_id = str((job.get("payload") or {}).get("preview_id") or "")
    safe_error = security_logging.safe_user_error(
        "short_video_audio_preview",
        exc,
        "Không thể tạo bản nghe thử VieNeu.",
    )
    latest = db.get_system_job(str(job.get("id") or "")) or {}
    canceled = bool(latest.get("cancel_requested"))
    status = "canceled" if canceled else "failed"
    if preview_id:
        db.update_short_video_audio_preview(
            preview_id,
            status=status,
            error=safe_error,
        )
    db.update_system_job(
        str(job.get("id") or ""),
        status=status,
        progress="Đã hủy bản nghe thử" if canceled else "Tạo bản nghe thử thất bại",
        error=safe_error,
        finished_at=db.utc_now(),
    )
