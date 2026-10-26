"""Audited, fail-closed disk cleanup for Nexus Studio Engine."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from nexus_engine.paths import DATA_DIR, PROJECT_ROOT
from nexus_engine.services import database as db

logger = logging.getLogger(__name__)

DEFAULT_RENDER_RETENTION_DAYS = 7.0
DEFAULT_AUDIO_RETENTION_HOURS = 7.0 * 24.0
DEFAULT_TEMP_RETENTION_DAYS = 7.0
DEFAULT_FB_TEMP_RETENTION_HOURS = 24.0
DEFAULT_OMNIVOICE_RETENTION_HOURS = 24.0
DEFAULT_BACKUP_RETENTION_DAYS = 30.0
DEFAULT_MANIFEST_RETENTION_DAYS = 90.0

TERMINAL_WORKFLOW_STATUSES = {
    "canceled", "cancelled", "completed", "done", "error", "failed", "published",
}
NON_TERMINAL_WORKFLOW_STATUSES = {
    "browser_upload", "in_progress", "processing", "queued", "ready_to_schedule",
    "retry_wait", "running", "scheduled", "uploading",
}


@dataclass(frozen=True)
class CleanupCandidate:
    category: str
    path: str
    reason: str
    size_bytes: int
    mtime_ns: int
    fingerprint_sha256: str
    artifact_id: int | None = None
    video_id: int | None = None
    content_hash: str = ""
    expected_db_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "path": self.path,
            "reason": self.reason,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "fingerprint_sha256": self.fingerprint_sha256,
            "artifact_id": self.artifact_id,
            "video_id": self.video_id,
            "content_hash": self.content_hash,
            "expected_db_path": self.expected_db_path,
        }


@dataclass
class CleanupCategoryResult:
    name: str
    files_planned: int = 0
    bytes_planned: int = 0
    files_removed: int = 0
    dirs_removed: int = 0
    bytes_freed: int = 0
    files_protected: int = 0
    bytes_protected: int = 0
    protected_reasons: dict[str, int] = field(default_factory=dict)
    protected_bytes_by_reason: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def mb_freed(self) -> float:
        return round(self.bytes_freed / (1024 * 1024), 2)

    @property
    def gb_freed(self) -> float:
        return round(self.bytes_freed / (1024 * 1024 * 1024), 2)

    @property
    def planned_gb(self) -> float:
        return round(self.bytes_planned / (1024 * 1024 * 1024), 2)

    @property
    def protected_gb(self) -> float:
        return round(self.bytes_protected / (1024 * 1024 * 1024), 2)


@dataclass
class CleanupSummary:
    timestamp: str
    dry_run: bool
    total_files_removed: int = 0
    total_dirs_removed: int = 0
    total_bytes_freed: int = 0
    categories: dict[str, CleanupCategoryResult] = field(default_factory=dict)
    database_stats: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    blocked: bool = False
    blockers: list[dict[str, Any]] = field(default_factory=list)
    verification_errors: list[str] = field(default_factory=list)
    manifest_id: str = ""
    manifest_path: str = ""

    @property
    def total_mb_freed(self) -> float:
        return round(self.total_bytes_freed / (1024 * 1024), 2)

    @property
    def total_gb_freed(self) -> float:
        return round(self.total_bytes_freed / (1024 * 1024 * 1024), 2)

    @property
    def total_files_planned(self) -> int:
        return sum(item.files_planned for item in self.categories.values())

    @property
    def total_bytes_planned(self) -> int:
        return sum(item.bytes_planned for item in self.categories.values())

    @property
    def total_gb_planned(self) -> float:
        return round(self.total_bytes_planned / (1024 * 1024 * 1024), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "dry_run": self.dry_run,
            "blocked": self.blocked,
            "blockers": self.blockers,
            "verification_errors": self.verification_errors,
            "manifest_id": self.manifest_id,
            "manifest_path": self.manifest_path,
            "policy": self.policy,
            "total_files_planned": self.total_files_planned,
            "total_bytes_planned": self.total_bytes_planned,
            "total_gb_planned": self.total_gb_planned,
            "total_files_removed": self.total_files_removed,
            "total_dirs_removed": self.total_dirs_removed,
            "total_bytes_freed": self.total_bytes_freed,
            "total_mb_freed": self.total_mb_freed,
            "total_gb_freed": self.total_gb_freed,
            "categories": {
                key: {
                    "name": value.name,
                    "files_planned": value.files_planned,
                    "bytes_planned": value.bytes_planned,
                    "planned_gb": value.planned_gb,
                    "files_removed": value.files_removed,
                    "dirs_removed": value.dirs_removed,
                    "bytes_freed": value.bytes_freed,
                    "mb_freed": value.mb_freed,
                    "gb_freed": value.gb_freed,
                    "files_protected": value.files_protected,
                    "bytes_protected": value.bytes_protected,
                    "protected_gb": value.protected_gb,
                    "protected_reasons": dict(value.protected_reasons),
                    "protected_bytes_by_reason": dict(
                        value.protected_bytes_by_reason
                    ),
                    "protected_gb_by_reason": {
                        reason: round(byte_count / (1024 ** 3), 2)
                        for reason, byte_count in value.protected_bytes_by_reason.items()
                    },
                    "error_count": len(value.errors),
                }
                for key, value in self.categories.items()
            },
            "database_stats": self.database_stats,
        }


class SystemCleaner:
    _run_lock = threading.Lock()

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        omnivoice_jobs_dir: Optional[Path] = None,
        project_root: Optional[Path] = None,
        cleanup_scope_root: Optional[Path] = None,
    ):
        self.project_root = Path(project_root or PROJECT_ROOT).resolve()
        self.cleanup_scope_root = Path(
            cleanup_scope_root or self.project_root.parent
        ).resolve()
        self.data_dir = Path(data_dir or DATA_DIR).resolve()
        self.omnivoice_jobs_dir = Path(
            omnivoice_jobs_dir
            or self.project_root.parent / "omnivoice" / "data" / "jobs"
        ).resolve()
        self.omnivoice_root = self.omnivoice_jobs_dir.parent.parent
        self.db_path = self.data_dir / "database.db"
        self.video_artifacts_dir = self.data_dir / "video_artifacts"
        self.renders_dir = self.video_artifacts_dir / "renders"
        self.segments_dir = self.video_artifacts_dir / "segments"
        self.audio_dir = self.data_dir / "audio"
        self.manifests_dir = self.data_dir / "cleanup_manifests"
        self.quarantine_dir = self.project_root / ".nexus-quarantine"
        self.process_lock_path = self.data_dir / ".cleanup.lock"
        self.scope_configuration_valid = all(
            self._path_within_scope(configured_root)
            for configured_root in (
                self.project_root,
                self.data_dir,
                self.omnivoice_jobs_dir,
                self.omnivoice_root,
            )
        )

    def _acquire_process_lock(self) -> Any | None:
        """Acquire a non-blocking OS lock shared by CLI and service processes."""

        self.process_lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.process_lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except (OSError, BlockingIOError):
            handle.close()
            return None

    @staticmethod
    def _release_process_lock(handle: Any | None) -> None:
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return (
            self.project_root,
            self.data_dir,
            self.omnivoice_jobs_dir,
            self.omnivoice_root,
        )

    def _path_within_scope(self, path: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(self.cleanup_scope_root)
            return True
        except (OSError, ValueError):
            return False

    def _category(
        self, summary: CleanupSummary, key: str, name: str | None = None
    ) -> CleanupCategoryResult:
        if key not in summary.categories:
            summary.categories[key] = CleanupCategoryResult(name=name or key)
        return summary.categories[key]

    @staticmethod
    def _add_verification_error(summary: CleanupSummary, error: str) -> None:
        if error not in summary.verification_errors:
            summary.verification_errors.append(error)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _is_allowed_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            return False
        if not self._path_within_scope(resolved):
            return False
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root.resolve(strict=False))
                return True
            except ValueError:
                continue
        return False

    def _add_candidate(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        category: str,
        path: Path,
        reason: str,
        artifact_id: int | None = None,
        video_id: int | None = None,
        content_hash: str = "",
        expected_db_path: str = "",
    ) -> None:
        try:
            if path.is_symlink() or not path.is_file():
                return
            resolved = path.resolve(strict=True)
            if not self._is_allowed_path(resolved):
                self._category(summary, category).errors.append("path_outside_allowlist")
                return
            stat_before = resolved.stat()
            fingerprint_sha256 = self._sha256(resolved)
            stat = resolved.stat()
            if (
                stat.st_size != stat_before.st_size
                or stat.st_mtime_ns != stat_before.st_mtime_ns
            ):
                self._category(summary, category).errors.append(
                    "fingerprint_changed_during_scan"
                )
                return
        except OSError as exc:
            self._category(summary, category).errors.append(
                f"candidate_stat_failed:{type(exc).__name__}"
            )
            return
        key = os.path.normcase(str(resolved))
        if key in candidates:
            return
        candidate = CleanupCandidate(
            category=category,
            path=str(resolved),
            reason=reason,
            size_bytes=max(0, int(stat.st_size)),
            mtime_ns=int(stat.st_mtime_ns),
            fingerprint_sha256=fingerprint_sha256,
            artifact_id=artifact_id,
            video_id=video_id,
            content_hash=str(content_hash or ""),
            expected_db_path=str(expected_db_path or ""),
        )
        candidates[key] = candidate
        result = self._category(summary, category)
        result.files_planned += 1
        result.bytes_planned += candidate.size_bytes

    def _protect(
        self,
        summary: CleanupSummary,
        *,
        category: str,
        size_bytes: int,
        reason: str,
    ) -> None:
        result = self._category(summary, category)
        result.files_protected += 1
        result.bytes_protected += max(0, int(size_bytes or 0))
        result.protected_reasons[reason] = result.protected_reasons.get(reason, 0) + 1
        result.protected_bytes_by_reason[reason] = (
            result.protected_bytes_by_reason.get(reason, 0)
            + max(0, int(size_bytes or 0))
        )

    @staticmethod
    def _parse_timestamp(value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _iter_files(root: Path) -> Iterable[Path]:
        if not root.exists():
            return ()
        try:
            return tuple(path for path in root.rglob("*") if path.is_file())
        except OSError:
            return ()

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _collect_expired_renders(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_days: float,
    ) -> None:
        category = "expired_renders"
        self._category(summary, category)
        if not self.db_path.exists():
            self._category(summary, category).errors.append("database_missing")
            self._add_verification_error(summary, "database_missing_during_scan")
            return
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        query = """
            WITH latest_workflow AS (
                SELECT video_id, status, scheduled_at, updated_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY video_id
                           ORDER BY COALESCE(updated_at, created_at) DESC, rowid DESC
                       ) AS rn
                FROM youtube_publish_workflows
            )
            SELECT va.id, va.video_id, va.path, va.content_hash, va.size_bytes,
                   va.created_at, va.updated_at, videos.id AS parent_video_id,
                   latest_workflow.status AS workflow_status,
                   latest_workflow.scheduled_at,
                   EXISTS (
                       SELECT 1 FROM system_jobs sj
                       WHERE sj.video_id = va.video_id
                         AND sj.status IN (
                             'queued', 'running', 'processing', 'in_progress',
                             'retry_wait'
                         )
                   ) AS has_active_job
            FROM video_artifacts va
            LEFT JOIN videos ON videos.id = va.video_id
            LEFT JOIN latest_workflow
              ON latest_workflow.video_id = va.video_id
             AND latest_workflow.rn = 1
            WHERE va.artifact_type = 'final_mp4' AND va.status = 'ready'
        """
        try:
            conn = self._connect_read_only()
            rows = conn.execute(query).fetchall()
            conn.close()
        except Exception as exc:
            self._category(summary, category).errors.append(
                f"render_query_failed:{type(exc).__name__}"
            )
            self._add_verification_error(
                summary, f"render_query_failed:{type(exc).__name__}"
            )
            return

        now = time.time()
        for row in rows:
            raw_path = str(row["path"] or "").strip()
            if not raw_path:
                self._protect(
                    summary, category=category,
                    size_bytes=int(row["size_bytes"] or 0), reason="missing_path",
                )
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = self.project_root / path
            try:
                stat = path.stat()
            except OSError:
                self._protect(
                    summary, category=category,
                    size_bytes=int(row["size_bytes"] or 0), reason="file_missing",
                )
                continue
            effective_time = max(
                stat.st_mtime,
                self._parse_timestamp(row["updated_at"]),
                self._parse_timestamp(row["created_at"]),
            )
            if effective_time >= cutoff:
                self._protect(
                    summary, category=category,
                    size_bytes=stat.st_size, reason="within_retention",
                )
                continue
            workflow_status = str(row["workflow_status"] or "").strip().lower()
            if bool(row["has_active_job"]):
                reason = "active_job"
            elif workflow_status in NON_TERMINAL_WORKFLOW_STATUSES:
                reason = "non_terminal_workflow"
                if workflow_status == "scheduled":
                    scheduled_at = self._parse_timestamp(row["scheduled_at"])
                    if scheduled_at and scheduled_at < now:
                        reason = "scheduled_past_due_requires_reconciliation"
            elif workflow_status and workflow_status not in TERMINAL_WORKFLOW_STATUSES:
                reason = "unknown_workflow_status"
            else:
                reason = ""
            if reason:
                self._protect(
                    summary, category=category,
                    size_bytes=stat.st_size, reason=reason,
                )
                continue
            self._add_candidate(
                candidates,
                summary,
                category=category,
                path=path,
                reason="final_mp4_older_than_retention",
                artifact_id=int(row["id"]),
                video_id=int(row["video_id"]),
                content_hash=str(row["content_hash"] or ""),
                expected_db_path=raw_path,
            )

    def _collect_orphan_audio(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_hours: float,
    ) -> None:
        category = "orphan_audio"
        self._category(summary, category)
        if not self.audio_dir.exists():
            return
        if not self.db_path.exists():
            self._category(summary, category).errors.append("database_missing")
            self._add_verification_error(summary, "database_missing_during_scan")
            return
        try:
            conn = self._connect_read_only()
            audio_urls = {
                str(row[0]) for row in conn.execute(
                    "SELECT audio_url FROM audio_tasks WHERE audio_url IS NOT NULL"
                ).fetchall() if row[0]
            }
            preview_files = {
                str(row[0]) for row in conn.execute(
                    "SELECT audio_filename FROM tts_previews WHERE audio_filename IS NOT NULL"
                ).fetchall() if row[0]
            }
            snapshots = [
                f"{row[0] or ''} {row[1] or ''}"
                for row in conn.execute(
                    "SELECT production_snapshot_json, voice_snapshot_json FROM videos"
                ).fetchall()
            ]
            conn.close()
        except Exception as exc:
            self._category(summary, category).errors.append(
                f"audio_reference_query_failed:{type(exc).__name__}"
            )
            self._add_verification_error(
                summary, f"audio_reference_query_failed:{type(exc).__name__}"
            )
            return
        cutoff = time.time() - max(0.0, retention_hours) * 3600.0
        for path in self.audio_dir.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            name = path.name
            referenced = (
                any(name in value for value in audio_urls)
                or name in preview_files
                or any(name in value for value in snapshots)
            )
            if referenced:
                self._protect(
                    summary, category=category,
                    size_bytes=stat.st_size, reason="database_reference",
                )
            elif stat.st_mtime < cutoff:
                self._add_candidate(
                    candidates, summary, category=category, path=path,
                    reason="unreferenced_audio_older_than_retention",
                )
            else:
                self._protect(
                    summary, category=category,
                    size_bytes=stat.st_size, reason="within_retention",
                )

    def _collect_omnivoice_jobs(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_hours: float,
    ) -> None:
        category = "omnivoice_jobs"
        self._category(summary, category)
        if not self.omnivoice_jobs_dir.exists():
            return
        cutoff = time.time() - max(0.0, retention_hours) * 3600.0
        terminal = {"completed", "done", "failed", "error", "canceled", "cancelled"}
        for job_dir in self.omnivoice_jobs_dir.iterdir():
            if not job_dir.is_dir():
                continue
            manifest_path = job_dir / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                status = str(manifest.get("status") or "").strip().lower()
                updated_at = self._parse_timestamp(manifest.get("updated_at"))
                effective_time = max(job_dir.stat().st_mtime, updated_at)
            except Exception as exc:
                self._category(summary, category).errors.append(
                    f"omnivoice_manifest_skipped:{type(exc).__name__}"
                )
                continue
            if status not in terminal:
                self._protect(
                    summary, category=category,
                    size_bytes=0, reason="non_terminal_job",
                )
                continue
            if effective_time >= cutoff:
                self._protect(
                    summary, category=category,
                    size_bytes=0, reason="within_retention",
                )
                continue
            for path in self._iter_files(job_dir):
                self._add_candidate(
                    candidates, summary, category=category, path=path,
                    reason="terminal_omnivoice_job_older_than_retention",
                )

    def _collect_segments(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_days: float,
    ) -> None:
        category = "video_segments"
        self._category(summary, category)
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        for path in self._iter_files(self.segments_dir):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            self._add_candidate(
                candidates, summary, category=category, path=path,
                reason="intermediate_segment_older_than_retention",
            )

    def _collect_browser_cache(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
    ) -> None:
        category = "browser_cache"
        self._category(summary, category)
        cache_names = {
            "cache", "cache_data", "code cache", "crashpad", "dawngraphitecache",
            "gpucache", "grshadercache", "shared dictionary",
        }
        for browser_root in (
            self.data_dir / "chrome_user_data", self.data_dir / "gpt_profiles",
        ):
            if not browser_root.exists():
                continue
            for directory in browser_root.rglob("*"):
                if not directory.is_dir() or directory.name.lower() not in cache_names:
                    continue
                for path in self._iter_files(directory):
                    self._add_candidate(
                        candidates, summary, category=category, path=path,
                        reason="closed_project_browser_cache",
                    )

    def _collect_logs_and_temp(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_days: float,
        fb_retention_hours: float,
    ) -> None:
        category = "logs_and_temp"
        self._category(summary, category)
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        fb_cutoff = time.time() - max(0.0, fb_retention_hours) * 3600.0

        logs_dir = self.data_dir / "logs"
        if logs_dir.exists():
            for path in logs_dir.glob("*.log*"):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        self._add_candidate(
                            candidates, summary, category=category, path=path,
                            reason="log_older_than_retention",
                        )
                except OSError:
                    continue

        fb_temp_dir = self.data_dir / "fb_crossposter_temp"
        if fb_temp_dir.exists():
            try:
                conn = self._connect_read_only()
                fb_rows = conn.execute(
                    "SELECT id, youtube_id FROM fb_crossposter_queue"
                ).fetchall()
                conn.close()
                fb_prefixes = {
                    f"{str(row['youtube_id'] or '').strip()}_{int(row['id'])}"
                    for row in fb_rows
                    if str(row["youtube_id"] or "").strip()
                }
                fb_prefixes.update(
                    f"repair_{str(row['youtube_id'] or '').strip()}_{int(row['id'])}"
                    for row in fb_rows
                    if str(row["youtube_id"] or "").strip()
                )
            except Exception as exc:
                error = f"fb_reference_query_failed:{type(exc).__name__}"
                self._category(summary, category).errors.append(error)
                self._add_verification_error(summary, error)
                fb_prefixes = set()
            for path in fb_temp_dir.iterdir():
                try:
                    if not path.is_file():
                        continue
                    referenced = any(
                        path.name == prefix
                        or path.name.startswith(prefix + ".")
                        or path.name.startswith(prefix + "_")
                        for prefix in fb_prefixes
                    )
                    if referenced:
                        self._protect(
                            summary,
                            category=category,
                            size_bytes=path.stat().st_size,
                            reason="fb_crosspost_reference",
                        )
                    elif path.stat().st_mtime < fb_cutoff:
                        self._add_candidate(
                            candidates, summary, category=category, path=path,
                            reason="fb_temp_older_than_retention",
                        )
                except OSError:
                    continue

        for path in self._iter_files(self.project_root / "scratch"):
            try:
                if path.stat().st_mtime < cutoff:
                    self._add_candidate(
                        candidates, summary, category=category, path=path,
                        reason="scratch_older_than_retention",
                    )
            except OSError:
                continue

        source_roots = (
            self.project_root / "src",
            self.project_root / "scripts",
            self.project_root / "tests",
            self.omnivoice_root / "src",
            self.omnivoice_root / "scripts",
            self.omnivoice_root / "tests",
        )
        pycache_dirs = {
            root / "__pycache__"
            for root in (self.project_root, self.omnivoice_root)
            if (root / "__pycache__").is_dir()
        }
        for source_root in source_roots:
            if source_root.exists():
                pycache_dirs.update(
                    path for path in source_root.rglob("__pycache__") if path.is_dir()
                )
        for pycache in pycache_dirs:
            if not pycache.is_dir():
                continue
            for path in self._iter_files(pycache):
                try:
                    if path.stat().st_mtime < cutoff:
                        self._add_candidate(
                            candidates, summary, category=category, path=path,
                            reason="python_bytecode_older_than_retention",
                        )
                except OSError:
                    continue

    def _collect_backups_and_ghost_dbs(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_days: float,
        minimum_backups: int = 2,
    ) -> None:
        category = "ghost_and_backup_dbs"
        self._category(summary, category)
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        backup_paths: dict[str, Path] = {}
        for pattern in ("*.bak*", "*.db.pre_*", "*.sqlite.pre_*"):
            for path in self.data_dir.glob(pattern):
                if path.is_file():
                    backup_paths[os.path.normcase(str(path.resolve()))] = path
        backups = sorted(
            backup_paths.values(), key=lambda item: item.stat().st_mtime, reverse=True,
        )
        for index, path in enumerate(backups):
            try:
                stat = path.stat()
            except OSError:
                continue
            if index < max(0, minimum_backups) or stat.st_mtime >= cutoff:
                self._protect(
                    summary, category=category,
                    size_bytes=stat.st_size, reason="backup_retained",
                )
                continue
            self._add_candidate(
                candidates, summary, category=category, path=path,
                reason="backup_older_than_retention_and_not_latest_two",
            )

        for root in (self.project_root, self.data_dir):
            for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
                for path in root.glob(pattern):
                    try:
                        if path.resolve() == self.db_path.resolve():
                            continue
                        if path.is_file() and path.stat().st_size == 0:
                            self._add_candidate(
                                candidates, summary, category=category, path=path,
                                reason="zero_byte_ghost_database",
                            )
                    except OSError:
                        continue

    def _collect_verified_quarantine(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_days: float,
    ) -> None:
        category = "verified_quarantine"
        self._category(summary, category)
        if not self.quarantine_dir.exists():
            return
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        now = time.time()
        for directory in self.quarantine_dir.iterdir():
            if not directory.is_dir():
                continue
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists():
                self._protect(
                    summary, category=category,
                    size_bytes=0, reason="missing_migration_manifest",
                )
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                verified = bool(manifest.get("verified_start_stop_restart"))
                verified_at = self._parse_timestamp(manifest.get("verified_at"))
                delete_after = self._parse_timestamp(manifest.get("delete_after"))
                effective_time = max(directory.stat().st_mtime, verified_at)
            except Exception as exc:
                self._category(summary, category).errors.append(
                    f"migration_manifest_invalid:{type(exc).__name__}"
                )
                continue
            if not verified:
                reason = "migration_not_verified"
            elif effective_time >= cutoff:
                reason = "within_retention"
            elif delete_after and delete_after > now:
                reason = "delete_after_not_reached"
            else:
                reason = ""
            if reason:
                self._protect(
                    summary, category=category, size_bytes=0, reason=reason,
                )
                continue
            for path in self._iter_files(directory):
                self._add_candidate(
                    candidates, summary, category=category, path=path,
                    reason="verified_quarantine_older_than_retention",
                )

    def _collect_old_cleanup_manifests(
        self,
        candidates: dict[str, CleanupCandidate],
        summary: CleanupSummary,
        *,
        retention_days: float,
    ) -> None:
        category = "cleanup_manifests"
        self._category(summary, category)
        if not self.manifests_dir.exists():
            return
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        for path in self.manifests_dir.glob("cleanup-*.json"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    self._add_candidate(
                        candidates, summary, category=category, path=path,
                        reason="cleanup_manifest_older_than_retention",
                    )
            except OSError:
                continue

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(temp_path, path)

    def _manifest_payload(
        self,
        summary: CleanupSummary,
        candidates: list[CleanupCandidate],
        *,
        state: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "manifest_id": summary.manifest_id,
            "created_at": summary.timestamp,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "state": state,
            "policy": summary.policy,
            "candidate_count": len(candidates),
            "candidate_bytes": sum(item.size_bytes for item in candidates),
            "items": [
                {**item.to_dict(), "result": "planned", "error": ""}
                for item in candidates
            ],
        }

    def _render_candidate_still_safe(
        self,
        candidate: CleanupCandidate,
        *,
        retention_days: float,
    ) -> bool:
        if candidate.artifact_id is None:
            return True
        cutoff = time.time() - max(0.0, retention_days) * 86400.0
        query = """
            SELECT va.path, va.status, va.content_hash, va.created_at, va.updated_at,
                   (
                       SELECT status FROM youtube_publish_workflows ypw
                       WHERE ypw.video_id = va.video_id
                       ORDER BY COALESCE(ypw.updated_at, ypw.created_at) DESC,
                                ypw.rowid DESC LIMIT 1
                   ) AS workflow_status,
                   EXISTS (
                       SELECT 1 FROM system_jobs sj
                       WHERE sj.video_id = va.video_id
                         AND sj.status IN (
                             'queued', 'running', 'processing', 'in_progress',
                             'retry_wait'
                         )
                   ) AS has_active_job
            FROM video_artifacts va WHERE va.id = ?
        """
        try:
            conn = self._connect_read_only()
            row = conn.execute(query, (candidate.artifact_id,)).fetchone()
            conn.close()
            stat = Path(candidate.path).stat()
        except Exception:
            return False
        if row is None:
            return False
        workflow_status = str(row["workflow_status"] or "").strip().lower()
        effective_time = max(
            stat.st_mtime,
            self._parse_timestamp(row["updated_at"]),
            self._parse_timestamp(row["created_at"]),
        )
        return bool(
            str(row["status"] or "").strip().lower() == "ready"
            and str(row["path"] or "") == candidate.expected_db_path
            and str(row["content_hash"] or "") == candidate.content_hash
            and not bool(row["has_active_job"])
            and workflow_status not in NON_TERMINAL_WORKFLOW_STATUSES
            and (not workflow_status or workflow_status in TERMINAL_WORKFLOW_STATUSES)
            and effective_time < cutoff
        )

    def _candidate_fingerprint_matches(self, candidate: CleanupCandidate) -> bool:
        path = Path(candidate.path)
        try:
            if path.is_symlink() or not path.is_file() or not self._is_allowed_path(path):
                return False
            stat_before = path.stat()
            if (
                int(stat_before.st_size) != candidate.size_bytes
                or int(stat_before.st_mtime_ns) != candidate.mtime_ns
            ):
                return False
            fingerprint_sha256 = self._sha256(path)
            stat = path.stat()
        except OSError:
            return False
        return bool(
            int(stat.st_size) == candidate.size_bytes
            and int(stat.st_mtime_ns) == candidate.mtime_ns
            and fingerprint_sha256 == candidate.fingerprint_sha256
        )

    def _remove_empty_parents(self, candidates: list[CleanupCandidate]) -> int:
        roots = {root.resolve(strict=False) for root in self.allowed_roots}
        directories: set[Path] = set()
        for candidate in candidates:
            parent = Path(candidate.path).parent
            while self._is_allowed_path(parent) and parent not in roots:
                directories.add(parent)
                parent = parent.parent
        removed = 0
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
                removed += 1
            except OSError:
                continue
        return removed

    def _execute_candidates(
        self,
        candidates: list[CleanupCandidate],
        summary: CleanupSummary,
        manifest_path: Path,
        manifest: dict[str, Any],
        *,
        retention_days: float,
    ) -> None:
        executed: list[CleanupCandidate] = []
        for index, candidate in enumerate(candidates):
            record = manifest["items"][index]
            result = self._category(summary, candidate.category)
            if not self._candidate_fingerprint_matches(candidate):
                record["result"] = "skipped_fingerprint_changed"
                continue
            if not self._render_candidate_still_safe(
                candidate, retention_days=retention_days,
            ):
                record["result"] = "skipped_state_changed"
                continue
            try:
                Path(candidate.path).unlink()
                record["result"] = "deleted"
                result.files_removed += 1
                result.bytes_freed += candidate.size_bytes
                executed.append(candidate)
                if candidate.artifact_id is not None:
                    marked = db.mark_video_artifact_purged(
                        candidate.artifact_id,
                        expected_path=candidate.expected_db_path,
                        reason=candidate.reason,
                        manifest_id=summary.manifest_id,
                        db_path=self.db_path,
                    )
                    if not marked:
                        record["result"] = "deleted_db_state_not_updated"
                        result.errors.append("artifact_purge_state_update_failed")
            except Exception as exc:
                record["result"] = "delete_failed"
                record["error"] = type(exc).__name__
                result.errors.append(f"delete_failed:{type(exc).__name__}")
            if index % 100 == 99:
                manifest["updated_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                self._write_json_atomic(manifest_path, manifest)

        dirs_removed = self._remove_empty_parents(executed)
        if dirs_removed:
            self._category(summary, "empty_directories").dirs_removed = dirs_removed
        manifest["state"] = "completed"
        manifest["updated_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        manifest["summary"] = summary.to_dict()
        self._write_json_atomic(manifest_path, manifest)

    def optimize_database(
        self,
        *,
        prune_system_jobs_days: float = 30.0,
        run_vacuum: bool = True,
    ) -> dict[str, Any]:
        stats = {
            "initial_db_bytes": 0,
            "final_db_bytes": 0,
            "system_jobs_pruned": 0,
            "vacuum_executed": False,
            "error": "",
        }
        if not self.db_path.exists():
            return stats
        try:
            stats["initial_db_bytes"] = self.db_path.stat().st_size
            cutoff = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=max(0.0, prune_system_jobs_days))
            ).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            cursor = conn.execute(
                """
                DELETE FROM system_jobs
                WHERE status IN ('done', 'completed', 'canceled', 'error', 'failed')
                  AND created_at < ?
                """,
                (cutoff,),
            )
            stats["system_jobs_pruned"] = cursor.rowcount
            conn.commit()
            if run_vacuum:
                conn.execute("VACUUM")
                stats["vacuum_executed"] = True
            conn.close()
            stats["final_db_bytes"] = self.db_path.stat().st_size
        except Exception as exc:
            stats["error"] = type(exc).__name__
        return stats

    def run_full_cleanup(
        self,
        dry_run: bool = False,
        clean_omni: bool = True,
        clean_segments: bool = True,
        clean_renders: bool = True,
        clean_audio: bool = True,
        clean_cache: bool = True,
        clean_logs: bool = True,
        clean_ghost_dbs: bool = True,
        vacuum_db: bool = True,
        render_retention_days: float = DEFAULT_RENDER_RETENTION_DAYS,
        audio_retention_hours: float = DEFAULT_AUDIO_RETENTION_HOURS,
        manifest_id: str = "",
    ) -> CleanupSummary:
        summary = CleanupSummary(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            dry_run=bool(dry_run),
            policy={
                "cleanup_scope_root": str(self.cleanup_scope_root),
                "render_retention_days": float(render_retention_days),
                "audio_retention_hours": float(audio_retention_hours),
                "temp_retention_days": DEFAULT_TEMP_RETENTION_DAYS,
                "fb_temp_retention_hours": DEFAULT_FB_TEMP_RETENTION_HOURS,
                "omnivoice_retention_hours": DEFAULT_OMNIVOICE_RETENTION_HOURS,
                "backup_retention_days": DEFAULT_BACKUP_RETENTION_DAYS,
                "backup_minimum_count": 2,
                "manifest_retention_days": DEFAULT_MANIFEST_RETENTION_DAYS,
            },
        )
        if not self.scope_configuration_valid:
            summary.blocked = True
            summary.verification_errors = ["cleanup_scope_configuration_invalid"]
            return summary
        acquired = self._run_lock.acquire(blocking=False)
        if not acquired:
            summary.blocked = True
            summary.verification_errors = ["cleanup_already_running"]
            return summary
        process_lock = None
        try:
            from nexus_engine.services import maintenance_guard

            cleanup_status = maintenance_guard.get_cleanup_status()
            summary.blockers = list(cleanup_status.get("blocking_jobs") or [])
            summary.verification_errors = list(
                cleanup_status.get("verification_errors") or []
            )
            summary.blocked = not bool(cleanup_status.get("safe_to_cleanup"))
            if not dry_run and not cleanup_status.get("safe_to_cleanup"):
                return summary
            if not dry_run:
                process_lock = self._acquire_process_lock()
                if process_lock is None:
                    summary.blocked = True
                    summary.verification_errors = ["cleanup_already_running"]
                    return summary

            candidates: dict[str, CleanupCandidate] = {}
            if clean_renders:
                self._collect_expired_renders(
                    candidates, summary, retention_days=render_retention_days,
                )
            if clean_audio:
                self._collect_orphan_audio(
                    candidates, summary, retention_hours=audio_retention_hours,
                )
            if clean_omni:
                self._collect_omnivoice_jobs(
                    candidates, summary,
                    retention_hours=DEFAULT_OMNIVOICE_RETENTION_HOURS,
                )
            if clean_segments:
                self._collect_segments(
                    candidates, summary, retention_days=DEFAULT_TEMP_RETENTION_DAYS,
                )
            if clean_cache:
                self._collect_browser_cache(candidates, summary)
            if clean_logs:
                self._collect_logs_and_temp(
                    candidates,
                    summary,
                    retention_days=DEFAULT_TEMP_RETENTION_DAYS,
                    fb_retention_hours=DEFAULT_FB_TEMP_RETENTION_HOURS,
                )
            if clean_ghost_dbs:
                self._collect_backups_and_ghost_dbs(
                    candidates, summary, retention_days=DEFAULT_BACKUP_RETENTION_DAYS,
                )
                self._collect_verified_quarantine(
                    candidates, summary, retention_days=DEFAULT_BACKUP_RETENTION_DAYS,
                )
                self._collect_old_cleanup_manifests(
                    candidates, summary, retention_days=DEFAULT_MANIFEST_RETENTION_DAYS,
                )

            if summary.verification_errors:
                summary.blocked = True
                if not dry_run:
                    return summary

            ordered_candidates = sorted(
                candidates.values(), key=lambda item: (item.category, item.path.lower()),
            )
            if dry_run:
                return summary

            summary.manifest_id = str(manifest_id or "").strip() or (
                datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                + "-" + uuid.uuid4().hex[:8]
            )
            manifest_path = self.manifests_dir / f"cleanup-{summary.manifest_id}.json"
            summary.manifest_path = str(manifest_path)
            manifest = self._manifest_payload(summary, ordered_candidates, state="planned")
            self._write_json_atomic(manifest_path, manifest)

            final_status = maintenance_guard.get_cleanup_status()
            if not final_status.get("safe_to_cleanup"):
                summary.blocked = True
                summary.blockers = list(final_status.get("blocking_jobs") or [])
                summary.verification_errors = list(
                    final_status.get("verification_errors") or []
                )
                manifest["state"] = "aborted_busy"
                manifest["blocker_count"] = len(summary.blockers)
                manifest["verification_errors"] = summary.verification_errors
                self._write_json_atomic(manifest_path, manifest)
                return summary

            self._execute_candidates(
                ordered_candidates,
                summary,
                manifest_path,
                manifest,
                retention_days=render_retention_days,
            )
            summary.total_files_removed = sum(
                item.files_removed for item in summary.categories.values()
            )
            summary.total_dirs_removed = sum(
                item.dirs_removed for item in summary.categories.values()
            )
            summary.total_bytes_freed = sum(
                item.bytes_freed for item in summary.categories.values()
            )
            summary.database_stats = self.optimize_database(run_vacuum=vacuum_db)
            manifest["summary"] = summary.to_dict()
            self._write_json_atomic(manifest_path, manifest)
            return summary
        finally:
            self._release_process_lock(process_lock)
            self._run_lock.release()


cleaner = SystemCleaner()
