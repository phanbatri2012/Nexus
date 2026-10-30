"""Single-process offline VieNeu-TTS v3 Turbo inference runner."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _generate(tts, request: dict, command: dict) -> dict:
    import numpy as np
    import soundfile as sf

    seed = int(command["seed"])
    np.random.seed(seed & 0xFFFFFFFF)
    started = time.monotonic()
    wav = tts.infer(
        str(command["text"]),
        ref_audio=str(request["reference_path"]),
        denoise=bool(request.get("denoise", False)),
        use_ref_codes=bool(request.get("use_ref_codes", True)),
        apply_watermark=bool(request.get("apply_watermark", True)),
        temperature=0.82,
        top_k=28,
        top_p=0.95,
        repetition_penalty=1.2,
        silence_p=0.18,
        crossfade_p=0.0,
    )
    output = Path(command["output_path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    sf.write(str(temporary), np.asarray(wav, dtype=np.float32), 48000, subtype="PCM_16")
    os.replace(temporary, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt = {
        "provider": "vieneu_v3_turbo",
        "backend": "onnx",
        "precision": "fp32",
        "sample_rate": 48000,
        "seed": seed,
        "duration_seconds": round(float(len(wav)) / 48000.0, 3),
        "sha256": digest,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "use_ref_codes": True,
        "denoise": False,
        "apply_watermark": True,
    }
    _write_json_atomic(Path(command["receipt_path"]), receipt)
    return receipt


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--session":
        raise SystemExit("Usage: vieneu_tts_runner.py --session <request.json>")
    request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if request.get("backend") != "onnx" or request.get("precision") != "fp32":
        raise RuntimeError("Only ONNX CPU FP32 is supported by this runner.")
    if int(request.get("sample_rate") or 0) != 48000:
        raise RuntimeError("VieNeu output must use 48 kHz.")

    from vieneu import Vieneu

    tts = Vieneu(
        mode="v3turbo",
        backbone_repo=str(request["backbone_dir"]),
        onnx_dir=str(request["onnx_dir"]),
        codec_dir=str(request["codec_dir"]),
        backend="onnx",
        device="cpu",
        precision="fp32",
    )
    if bool(request.get("apply_watermark")) and tts.watermarker is None:
        raise RuntimeError("Audio watermark was requested but is unavailable.")
    print(json.dumps({"status": "ready"}), flush=True)
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command.get("op") == "close":
                break
            if command.get("op") != "generate":
                raise ValueError("Unsupported runner operation.")
            receipt = _generate(tts, request, command)
            print(json.dumps({"status": "completed", **receipt}), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
