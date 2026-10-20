#!/usr/bin/env python3
"""Audit and optionally repair video artifact rows whose video no longer exists."""

from __future__ import annotations

import argparse
import datetime
import json
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


ACTIVE_JOB_STATUSES = {
    "queued",
    "running",
    "processing",
    "in_progress",
    "retry_wait",
}


def _timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def _is_within_managed_root(path: Path) -> bool:
    try:
        path.relative_to(VIDEO_ARTIFACTS_DIR.resolve())
        return True
    except ValueError:
        return False


def build_manifest() -> dict:
    connection = _connect(readonly=True)
    try:
        rows = connection.execute(
            """
            SELECT video_artifacts.*
            FROM video_artifacts
            LEFT JOIN videos ON videos.id = video_artifacts.video_id
            WHERE videos.id IS NULL
            ORDER BY video_artifacts.id
            """
        ).fetchall()
        orphan_ids = {int(row["id"]) for row in rows}
        remaining_paths = {
            Path(str(row["path"])).resolve()
            for row in connection.execute(
                "SELECT id, path FROM video_artifacts WHERE TRIM(path) != ''"
            ).fetchall()
            if int(row["id"]) not in orphan_ids
        }
    finally:
        connection.close()

    items = []
    total_existing_bytes = 0
    for row in rows:
        raw_path = str(row["path"] or "").strip()
        resolved_path = Path(raw_path).resolve() if raw_path else None
        file_exists = bool(resolved_path and resolved_path.is_file())
        size_bytes = resolved_path.stat().st_size if file_exists else 0
        safe_managed_path = bool(
            resolved_path and _is_within_managed_root(resolved_path)
        )
        referenced_by_live_row = bool(
            resolved_path and resolved_path in remaining_paths
        )
        total_existing_bytes += size_bytes
        items.append(
            {
                "id": int(row["id"]),
                "video_id": int(row["video_id"]),
                "artifact_type": str(row["artifact_type"] or ""),
                "status": str(row["status"] or ""),
                "path": raw_path,
                "resolved_path": str(resolved_path or ""),
                "file_exists": file_exists,
                "size_bytes": size_bytes,
                "safe_managed_path": safe_managed_path,
                "referenced_by_live_row": referenced_by_live_row,
                "eligible_for_file_delete": (
                    file_exists and safe_managed_path and not referenced_by_live_row
                ),
            }
        )
    return {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "database_path": str(database.DB_PATH.resolve()),
        "artifact_root": str(VIDEO_ARTIFACTS_DIR.resolve()),
        "orphan_rows": len(items),
        "existing_files": sum(bool(item["file_exists"]) for item in items),
        "eligible_files": sum(
            bool(item["eligible_for_file_delete"]) for item in items
        ),
        "total_existing_bytes": total_existing_bytes,
        "items": items,
    }


def _write_manifest(manifest: dict) -> Path:
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    path = DIAGNOSTICS_DIR / f"orphan_video_artifacts_{_timestamp()}.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _assert_idle() -> None:
    connection = _connect(readonly=True)
    try:
        placeholders = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
        active = connection.execute(
            f"SELECT id, job_type, status FROM system_jobs "
            f"WHERE status IN ({placeholders}) LIMIT 1",
            tuple(sorted(ACTIVE_JOB_STATUSES)),
        ).fetchone()
    finally:
        connection.close()
    if active is not None:
        raise RuntimeError(
            "Hệ thống đang có job hoạt động; không áp dụng sửa orphan lúc này."
        )


def _backup_database() -> Path:
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"database.pre_orphan_artifact_repair.{_timestamp()}.db"
    source = sqlite3.connect(str(database.DB_PATH))
    backup = sqlite3.connect(str(backup_path))
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    return backup_path


def apply_manifest(manifest: dict, *, delete_files: bool) -> dict:
    _assert_idle()
    backup_path = _backup_database()
    orphan_ids = [int(item["id"]) for item in manifest["items"]]
    deleted_rows = 0
    if orphan_ids:
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in orphan_ids)
            cursor = connection.execute(
                f"""
                DELETE FROM video_artifacts
                WHERE id IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM videos
                      WHERE videos.id = video_artifacts.video_id
                  )
                """,
                orphan_ids,
            )
            deleted_rows = max(0, int(cursor.rowcount or 0))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    deleted_files = 0
    file_errors = []
    if delete_files:
        for item in manifest["items"]:
            if not item["eligible_for_file_delete"]:
                continue
            path = Path(item["resolved_path"])
            try:
                path.unlink(missing_ok=True)
                deleted_files += 1
            except OSError as exc:
                file_errors.append({"path": str(path), "error": str(exc)})

    return {
        "backup_path": str(backup_path),
        "deleted_rows": deleted_rows,
        "deleted_files": deleted_files,
        "file_errors": file_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit or repair video_artifacts rows with missing parent videos."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up the database and delete orphan database rows.",
    )
    parser.add_argument(
        "--delete-files",
        action="store_true",
        help="With --apply, also delete eligible files inside data/video_artifacts.",
    )
    arguments = parser.parse_args()
    if arguments.delete_files and not arguments.apply:
        parser.error("--delete-files requires --apply")

    manifest = build_manifest()
    manifest_path = _write_manifest(manifest)
    output = {
        "mode": "apply" if arguments.apply else "dry-run",
        "manifest_path": str(manifest_path),
        "summary": {
            key: manifest[key]
            for key in (
                "orphan_rows",
                "existing_files",
                "eligible_files",
                "total_existing_bytes",
            )
        },
    }
    if arguments.apply:
        output["result"] = apply_manifest(
            manifest,
            delete_files=arguments.delete_files,
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
