"""Run the strict three-pass Muse start/end-frame release verification."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nexus_engine.paths import LOGS_DIR  # noqa: E402
from nexus_engine.services import database as db  # noqa: E402
from nexus_engine.services import muse_provider, muse_studio_service  # noqa: E402
from nexus_engine.services.youtube_downloader import ensure_ffmpeg_directory  # noqa: E402


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_ready_scene_image(video_id: int, scene_index: int) -> Path:
    artifact_type = f"muse_scene_image:{int(scene_index)}"
    candidates = [
        artifact
        for artifact in db.list_video_artifacts(video_id, artifact_type=artifact_type)
        if str(artifact.get("status") or "") == "ready"
    ]
    if not candidates:
        raise RuntimeError(f"Missing ready artifact {artifact_type}")
    artifact = max(candidates, key=lambda item: int(item.get("id") or 0))
    path = Path(str(artifact.get("path") or "")).resolve()
    if not path.is_file():
        raise RuntimeError(f"Artifact file is missing for {artifact_type}")
    return path


def _extract_frame(ffmpeg: str, video: Path, destination: Path, *, last: bool) -> None:
    args = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if last:
        args.extend(["-sseof", "-0.08"])
    args.extend(["-i", str(video), "-frames:v", "1", "-y", str(destination)])
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not destination.is_file():
        raise RuntimeError(f"FFmpeg frame extraction failed with code {completed.returncode}")


def _rmse(left_path: Path, right_path: Path) -> float:
    with Image.open(left_path) as left_source, Image.open(right_path) as right_source:
        left = left_source.convert("RGB").resize((320, 180))
        right = right_source.convert("RGB").resize((320, 180))
        stats = ImageStat.Stat(ImageChops.difference(left, right))
    mean_square = sum(value * value for value in stats.rms) / max(1, len(stats.rms))
    return round(math.sqrt(mean_square), 4)


def _frame_order_metrics(
    *,
    start_input: Path,
    end_input: Path,
    first_frame: Path,
    last_frame: Path,
) -> dict:
    first_to_start = _rmse(first_frame, start_input)
    first_to_end = _rmse(first_frame, end_input)
    last_to_start = _rmse(last_frame, start_input)
    last_to_end = _rmse(last_frame, end_input)
    return {
        "first_to_start_rmse": first_to_start,
        "first_to_end_rmse": first_to_end,
        "last_to_start_rmse": last_to_start,
        "last_to_end_rmse": last_to_end,
        "ordered": first_to_start < first_to_end and last_to_end < last_to_start,
    }


async def _run(args: argparse.Namespace) -> int:
    if int(args.runs) != 3:
        raise ValueError("Strict Muse release verification requires exactly 3 runs")
    start_source = _latest_ready_scene_image(args.video_id, args.start_scene)
    end_source = _latest_ready_scene_image(args.video_id, args.end_scene)
    if _sha256_file(start_source) == _sha256_file(end_source):
        raise RuntimeError("Start and end inputs must be different")

    timestamp = db.utc_now().replace(":", "-").replace("+", "_")
    evidence_dir = LOGS_DIR / "diagnostics" / f"muse_start_end_e2e_{timestamp}"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    start_input = evidence_dir / f"start_input{start_source.suffix.lower()}"
    end_input = evidence_dir / f"end_input{end_source.suffix.lower()}"
    shutil.copy2(start_source, start_input)
    shutil.copy2(end_source, end_input)

    ffmpeg_path = Path(ensure_ffmpeg_directory()) / "ffmpeg.exe"
    if not ffmpeg_path.is_file():
        raise RuntimeError("Managed FFmpeg executable is unavailable")
    ffmpeg = str(ffmpeg_path)
    provider = muse_provider.MuseStudioApiProvider(job_id="muse-release-e2e")
    results: list[dict] = []
    prompt = (
        "Create one continuous cinematic motion from @Image1 to @Image2. "
        "Preserve the subject and composition while completing the visible state transition. "
        "No text, logo, title card, cutaway, or unrelated subject."
    )
    try:
        with patch.object(
            muse_studio_service,
            "task_recovery_release_status",
            return_value={"enabled": True, "contract_version": 1, "successful_runs": 3},
        ):
            async with provider.open_session() as session:
                for run_index in range(1, 4):
                    video_path = evidence_dir / f"run_{run_index}.mp4"
                    first_frame = evidence_dir / f"run_{run_index}_first.png"
                    last_frame = evidence_dir / f"run_{run_index}_last.png"
                    receipt: dict[str, str] = {}

                    def on_submitted(value: muse_provider.MuseReceipt) -> None:
                        receipt["provider_task_id"] = value.provider_task_id
                        receipt["runtime_endpoint"] = value.endpoint

                    output = await session.create_video(
                        prompt,
                        start_input,
                        end_input,
                        video_path,
                        request_hash=f"muse-start-end-e2e-{uuid.uuid4().hex}",
                        on_submitted=on_submitted,
                    )
                    _extract_frame(ffmpeg, video_path, first_frame, last=False)
                    _extract_frame(ffmpeg, video_path, last_frame, last=True)
                    metrics = _frame_order_metrics(
                        start_input=start_input,
                        end_input=end_input,
                        first_frame=first_frame,
                        last_frame=last_frame,
                    )
                    results.append(
                        {
                            "run": run_index,
                            "video": video_path.name,
                            "first_frame": first_frame.name,
                            "last_frame": last_frame.name,
                            "video_sha256": _sha256_file(video_path),
                            "first_frame_sha256": _sha256_file(first_frame),
                            "last_frame_sha256": _sha256_file(last_frame),
                            "provider_task_id_sha256": hashlib.sha256(
                                str(receipt.get("provider_task_id") or "").encode("utf-8")
                            ).hexdigest(),
                            "provider_elapsed_seconds": output.get("provider_elapsed_seconds"),
                            "metrics": metrics,
                        }
                    )
                    print(
                        json.dumps(
                            {
                                "run": run_index,
                                "completed": True,
                                "ordered": metrics["ordered"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    finally:
        await asyncio.to_thread(muse_studio_service.shutdown_owned_service)

    evidence = {
        "contract": "start_end_v1",
        "video_id": int(args.video_id),
        "start_scene": int(args.start_scene),
        "end_scene": int(args.end_scene),
        "start_input": start_input.name,
        "end_input": end_input.name,
        "start_input_sha256": _sha256_file(start_input),
        "end_input_sha256": _sha256_file(end_input),
        "runs": results,
        "automatic_order_check_passed": len(results) == 3
        and all(bool(item["metrics"]["ordered"]) for item in results),
        "manual_visual_review_required": True,
    }
    evidence_path = evidence_dir / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "evidence_path": str(evidence_path),
                "automatic_order_check_passed": evidence["automatic_order_check_passed"],
                "runs": len(results),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if evidence["automatic_order_check_passed"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", type=int, required=True)
    parser.add_argument("--start-scene", type=int, default=0)
    parser.add_argument("--end-scene", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
