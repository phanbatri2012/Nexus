#!/usr/bin/env python3
"""Audit and repair invalid Google Flow scene-video artifacts for one video."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve().parent
_SRC_DIR = _HERE.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from auto_yt.paths import DATA_DIR, DIAGNOSTICS_DIR, VIDEO_ARTIFACTS_DIR
from auto_yt.services import database


ACTIVE_JOB_STATUSES = {"queued", "running", "processing", "retry_wait"}
DERIVED_ARTIFACT_TYPES = {
    "final_mp4",
    "flow_media_manifest",
    "rendered_video",
    "stock_video",
}


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _connect(*, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(
            database.DB_PATH.resolve().as_uri() + "?mode=ro",
            uri=True,
        )
    else:
        connection = sqlite3.connect(str(database.DB_PATH), timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_path(raw_path: object) -> tuple[Path | None, bool]:
    value = str(raw_path or "").strip()
    if not value:
        return None, True
    resolved = Path(value).resolve()
    try:
        resolved.relative_to(VIDEO_ARTIFACTS_DIR.resolve())
    except ValueError:
        return resolved, False
    return resolved, True


def build_manifest(video_id: int) -> dict:
    connection = _connect(readonly=True)
    try:
        video = connection.execute(
            "SELECT id, title, generated_title, is_published FROM videos WHERE id = ?",
            (int(video_id),),
        ).fetchone()
        if video is None:
            raise RuntimeError(f"Không tìm thấy video {video_id}.")
        publication_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM video_publications WHERE video_id = ?",
                (int(video_id),),
            ).fetchone()[0]
        )
        jobs = connection.execute(
            """
            SELECT id, job_type, status
            FROM system_jobs
            WHERE video_id = ? AND status IN ('queued', 'running', 'processing', 'retry_wait')
            ORDER BY created_at
            """,
            (int(video_id),),
        ).fetchall()
        rows = connection.execute(
            """
            SELECT * FROM video_artifacts
            WHERE video_id = ?
              AND (artifact_type LIKE 'scene_video:%'
                   OR artifact_type IN ('final_mp4', 'flow_media_manifest',
                                        'rendered_video', 'stock_video'))
            ORDER BY id
            """,
            (int(video_id),),
        ).fetchall()
        image_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM video_artifacts
                WHERE video_id = ? AND artifact_type LIKE 'scene:%'
                  AND status = 'completed'
                """,
                (int(video_id),),
            ).fetchone()[0]
        )
    finally:
        connection.close()

    items = []
    hashes: dict[str, list[int]] = {}
    unsafe_paths = []
    for row in rows:
        resolved, safe = _managed_path(row["path"])
        exists = bool(resolved and resolved.is_file())
        file_hash = _sha256(resolved) if exists and safe else ""
        if file_hash:
            hashes.setdefault(file_hash, []).append(int(row["id"]))
        if not safe:
            unsafe_paths.append(str(resolved))
        items.append(
            {
                "id": int(row["id"]),
                "artifact_type": str(row["artifact_type"] or ""),
                "status": str(row["status"] or ""),
                "path": str(row["path"] or ""),
                "resolved_path": str(resolved or ""),
                "file_exists": exists,
                "safe_managed_path": safe,
                "sha256": file_hash,
            }
        )
    duplicate_groups = [
        {"sha256": value, "artifact_ids": ids}
        for value, ids in hashes.items()
        if len(ids) > 1
    ]
    return {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "video_id": int(video_id),
        "title": str(video["generated_title"] or video["title"] or ""),
        "is_published": bool(video["is_published"]),
        "publication_count": publication_count,
        "completed_scene_images_preserved": image_count,
        "active_jobs": [dict(row) for row in jobs],
        "artifact_count": len(items),
        "scene_video_count": sum(
            item["artifact_type"].startswith("scene_video:") for item in items
        ),
        "derived_artifact_count": sum(
            item["artifact_type"] in DERIVED_ARTIFACT_TYPES for item in items
        ),
        "duplicate_groups": duplicate_groups,
        "unsafe_paths": unsafe_paths,
        "items": items,
    }


def _write_manifest(manifest: dict, mode: str) -> Path:
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    target = DIAGNOSTICS_DIR / (
        f"flow_video_repair_{manifest['video_id']}_{mode}_{_timestamp()}.json"
    )
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _backup_database(video_id: int) -> Path:
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"database.pre_flow_repair_{video_id}.{_timestamp()}.db"
    source = sqlite3.connect(str(database.DB_PATH))
    backup = sqlite3.connect(str(target))
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    return target


def apply_manifest(manifest: dict) -> dict:
    if manifest["is_published"] or manifest["publication_count"]:
        raise RuntimeError("Từ chối repair vì video đã có trạng thái hoặc bản ghi xuất bản.")
    if manifest["active_jobs"]:
        raise RuntimeError("Từ chối repair vì video vẫn còn job đang hoạt động.")
    if manifest["unsafe_paths"]:
        raise RuntimeError("Từ chối repair vì có artifact nằm ngoài thư mục quản lý.")

    video_id = int(manifest["video_id"])
    backup_path = _backup_database(video_id)
    quarantine_dir = (
        VIDEO_ARTIFACTS_DIR / "quarantine" / f"video_{video_id}_{_timestamp()}"
    )
    quarantine_dir.mkdir(parents=True, exist_ok=False)
    moved_files = []
    for item in manifest["items"]:
        if not item["file_exists"]:
            continue
        source = Path(item["resolved_path"])
        destination = quarantine_dir / f"artifact_{item['id']}_{source.name}"
        shutil.move(str(source), str(destination))
        moved_files.append({"from": str(source), "to": str(destination)})

    artifact_ids = [int(item["id"]) for item in manifest["items"]]
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        deleted_rows = 0
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            cursor = connection.execute(
                f"DELETE FROM video_artifacts WHERE video_id = ? AND id IN ({placeholders})",
                (video_id, *artifact_ids),
            )
            deleted_rows = max(0, int(cursor.rowcount or 0))
        connection.execute(
            """
            INSERT INTO flow_projects (video_id, project_id, project_url, reset_requested)
            VALUES (?, '__reset__', '', 1)
            ON CONFLICT(video_id) DO UPDATE SET reset_requested = 1
            """,
            (video_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "backup_path": str(backup_path),
        "quarantine_dir": str(quarantine_dir),
        "moved_files": moved_files,
        "deleted_artifact_rows": deleted_rows,
        "new_flow_project_requested": True,
        "completed_scene_images_preserved": manifest[
            "completed_scene_images_preserved"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit or repair Flow scene-video artifacts for one unpublished video."
    )
    parser.add_argument("--video-id", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()

    manifest = build_manifest(arguments.video_id)
    selected_mode = "apply" if arguments.apply else "dry-run"
    manifest_path = _write_manifest(manifest, selected_mode)
    output = {
        "mode": selected_mode,
        "manifest_path": str(manifest_path),
        "summary": {
            key: manifest[key]
            for key in (
                "video_id",
                "is_published",
                "publication_count",
                "completed_scene_images_preserved",
                "scene_video_count",
                "derived_artifact_count",
                "duplicate_groups",
                "active_jobs",
                "unsafe_paths",
            )
        },
    }
    if arguments.apply:
        output["result"] = apply_manifest(manifest)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
