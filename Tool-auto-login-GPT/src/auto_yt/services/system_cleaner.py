"""System Cleaner & Disk Space Optimization Service for Auto_YT & OmniVoice.

Provides safe, audited cleanup operations:
1. OmniVoice TTS job chunks & expired jobs
2. Intermediate video segments (scene segment mp4s & srts)
3. Orphan video renders (not in DB)
4. Orphan & expired preview audio files
5. Safe Chromium CDP browser caches (preserving login cookies & sessions)
6. Old logs, scratch temp files, FB crossposter temp downloads
7. Ghost 0-byte database files & obsolete DB backups
8. SQLite database pruning and VACUUM compaction
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from auto_yt.paths import (
    AUDIO_DIR,
    CAPTIONS_DIR,
    DATA_DIR,
    PROJECT_ROOT,
    RENDERS_DIR,
    SCENES_DIR,
    SEGMENTS_DIR,
    THUMBNAILS_DIR,
    VISUAL_PLANS_DIR,
)
from auto_yt.services import database as db
from auto_yt.services import process_registry

logger = logging.getLogger(__name__)

# Omnivoice data directory relative to Auto_YT workspace root
OMNIVOICE_DATA_DIR = PROJECT_ROOT.parent.parent / "omnivoice" / "data"
OMNIVOICE_JOBS_DIR = OMNIVOICE_DATA_DIR / "jobs"


@dataclass
class CleanupCategoryResult:
    name: str
    files_removed: int = 0
    dirs_removed: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def mb_freed(self) -> float:
        return round(self.bytes_freed / (1024 * 1024), 2)

    @property
    def gb_freed(self) -> float:
        return round(self.bytes_freed / (1024 * 1024 * 1024), 2)


@dataclass
class CleanupSummary:
    timestamp: str
    dry_run: bool
    total_files_removed: int = 0
    total_dirs_removed: int = 0
    total_bytes_freed: int = 0
    categories: dict[str, CleanupCategoryResult] = field(default_factory=dict)
    database_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def total_mb_freed(self) -> float:
        return round(self.total_bytes_freed / (1024 * 1024), 2)

    @property
    def total_gb_freed(self) -> float:
        return round(self.total_bytes_freed / (1024 * 1024 * 1024), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "dry_run": self.dry_run,
            "total_files_removed": self.total_files_removed,
            "total_dirs_removed": self.total_dirs_removed,
            "total_bytes_freed": self.total_bytes_freed,
            "total_mb_freed": self.total_mb_freed,
            "total_gb_freed": self.total_gb_freed,
            "categories": {
                k: {
                    "name": v.name,
                    "files_removed": v.files_removed,
                    "dirs_removed": v.dirs_removed,
                    "bytes_freed": v.bytes_freed,
                    "mb_freed": v.mb_freed,
                    "gb_freed": v.gb_freed,
                    "error_count": len(v.errors),
                }
                for k, v in self.categories.items()
            },
            "database_stats": self.database_stats,
        }


def _get_dir_size_and_count(dir_path: Path) -> tuple[int, int]:
    """Calculate total byte size and file count for a directory."""
    total_bytes = 0
    count = 0
    if not dir_path.exists():
        return 0, 0
    for p in dir_path.rglob("*"):
        if p.is_file():
            try:
                total_bytes += p.stat().st_size
                count += 1
            except OSError:
                pass
    return total_bytes, count


class SystemCleaner:
    def __init__(self, data_dir: Optional[Path] = None, omnivoice_jobs_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.omnivoice_jobs_dir = omnivoice_jobs_dir or OMNIVOICE_JOBS_DIR
        self.db_path = self.data_dir / "database.db"

    # =========================================================================
    # 1. OmniVoice Job Cleanup
    # =========================================================================
    def clean_omnivoice_jobs(
        self,
        max_age_hours: float = 24.0,
        keep_final_wav_if_recent: bool = True,
        dry_run: bool = False,
    ) -> CleanupCategoryResult:
        result = CleanupCategoryResult(name="omnivoice_jobs")
        if not self.omnivoice_jobs_dir.exists():
            return result

        now = time.time()
        max_age_seconds = max_age_hours * 3600.0

        for job_dir in self.omnivoice_jobs_dir.iterdir():
            if not job_dir.is_dir():
                continue
            try:
                mtime = job_dir.stat().st_mtime
                age_seconds = now - mtime
                is_expired = age_seconds > max_age_seconds

                if is_expired:
                    # Remove the entire expired job folder
                    dir_bytes, file_cnt = _get_dir_size_and_count(job_dir)
                    result.bytes_freed += dir_bytes
                    result.files_removed += file_cnt
                    result.dirs_removed += 1
                    if not dry_run:
                        shutil.rmtree(job_dir, ignore_errors=True)
                else:
                    # For active/recent jobs: remove intermediate chunk-*.wav and .tmp files
                    # if final.wav exists and is completed
                    manifest_file = job_dir / "manifest.json"
                    final_wav = job_dir / "final.wav"
                    if final_wav.exists():
                        for chunk_file in job_dir.glob("chunk-*.wav"):
                            try:
                                sz = chunk_file.stat().st_size
                                result.bytes_freed += sz
                                result.files_removed += 1
                                if not dry_run:
                                    chunk_file.unlink(missing_ok=True)
                            except OSError as e:
                                result.errors.append(str(e))
                        for tmp_file in job_dir.glob("*.tmp"):
                            try:
                                sz = tmp_file.stat().st_size
                                result.bytes_freed += sz
                                result.files_removed += 1
                                if not dry_run:
                                    tmp_file.unlink(missing_ok=True)
                            except OSError as e:
                                result.errors.append(str(e))
            except Exception as exc:
                result.errors.append(f"Job {job_dir.name}: {exc}")

        return result

    # =========================================================================
    # 2. Intermediate Video Segments
    # =========================================================================
    def clean_video_segments(self, dry_run: bool = False) -> CleanupCategoryResult:
        result = CleanupCategoryResult(name="video_segments")
        if not SEGMENTS_DIR.exists():
            return result

        for item in SEGMENTS_DIR.iterdir():
            try:
                if item.is_dir():
                    dir_bytes, file_cnt = _get_dir_size_and_count(item)
                    result.bytes_freed += dir_bytes
                    result.files_removed += file_cnt
                    result.dirs_removed += 1
                    if not dry_run:
                        shutil.rmtree(item, ignore_errors=True)
                elif item.is_file():
                    sz = item.stat().st_size
                    result.bytes_freed += sz
                    result.files_removed += 1
                    if not dry_run:
                        item.unlink(missing_ok=True)
            except Exception as exc:
                result.errors.append(f"Segment {item.name}: {exc}")

        return result

    # =========================================================================
    # 3. Orphan Video Renders
    # =========================================================================
    def clean_orphan_renders(self, dry_run: bool = False) -> CleanupCategoryResult:
        result = CleanupCategoryResult(name="orphan_renders")
        if not RENDERS_DIR.exists() or not self.db_path.exists():
            return result

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM video_artifacts WHERE path IS NOT NULL")
            db_paths = set(Path(r[0]).name.lower() for r in cursor.fetchall() if r[0])
            conn.close()
        except Exception as exc:
            result.errors.append(f"DB Query error: {exc}")
            return result

        for render_file in RENDERS_DIR.glob("*.mp4"):
            try:
                if render_file.name.lower() not in db_paths:
                    sz = render_file.stat().st_size
                    result.bytes_freed += sz
                    result.files_removed += 1
                    if not dry_run:
                        render_file.unlink(missing_ok=True)
            except Exception as exc:
                result.errors.append(f"Render {render_file.name}: {exc}")

        # Clean renders/temp if exists
        temp_renders_dir = RENDERS_DIR / "temp"
        if temp_renders_dir.exists():
            for t_item in temp_renders_dir.iterdir():
                try:
                    if t_item.is_dir():
                        d_bytes, f_cnt = _get_dir_size_and_count(t_item)
                        result.bytes_freed += d_bytes
                        result.files_removed += f_cnt
                        result.dirs_removed += 1
                        if not dry_run:
                            shutil.rmtree(t_item, ignore_errors=True)
                    else:
                        sz = t_item.stat().st_size
                        result.bytes_freed += sz
                        result.files_removed += 1
                        if not dry_run:
                            t_item.unlink(missing_ok=True)
                except Exception as exc:
                    result.errors.append(f"Temp render {t_item.name}: {exc}")

        return result

    # =========================================================================
    # 4. Orphan Audio & Expired Previews
    # =========================================================================
    def clean_orphan_audio(
        self,
        retention_hours: float = 24.0,
        dry_run: bool = False,
    ) -> CleanupCategoryResult:
        result = CleanupCategoryResult(name="orphan_audio")
        if not AUDIO_DIR.exists() or not self.db_path.exists():
            return result

        now = time.time()
        min_age_seconds = retention_hours * 3600.0

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT audio_url FROM audio_tasks WHERE audio_url IS NOT NULL")
            audio_task_urls = set(r[0] for r in cursor.fetchall() if r[0])

            cursor.execute("SELECT audio_filename FROM tts_previews WHERE audio_filename IS NOT NULL")
            tts_preview_files = set(r[0] for r in cursor.fetchall() if r[0])

            cursor.execute("SELECT production_snapshot_json, voice_snapshot_json FROM videos")
            video_rows = cursor.fetchall()
            conn.close()
        except Exception as exc:
            result.errors.append(f"DB Query error: {exc}")
            return result

        for audio_file in AUDIO_DIR.glob("*"):
            if not audio_file.is_file():
                continue
            try:
                name = audio_file.name
                mtime = audio_file.stat().st_mtime
                age_seconds = now - mtime

                # Check if referenced anywhere
                is_referenced = (
                    any(name in u for u in audio_task_urls)
                    or (name in tts_preview_files)
                    or any(
                        name in str(r["production_snapshot_json"] or "")
                        or name in str(r["voice_snapshot_json"] or "")
                        for r in video_rows
                    )
                )

                # Delete if unreferenced and older than retention window, or if it's a temporary .tmp file
                if audio_file.suffix.lower() == ".tmp" or (not is_referenced and age_seconds > min_age_seconds):
                    sz = audio_file.stat().st_size
                    result.bytes_freed += sz
                    result.files_removed += 1
                    if not dry_run:
                        audio_file.unlink(missing_ok=True)
            except Exception as exc:
                result.errors.append(f"Audio {audio_file.name}: {exc}")

        return result

    # =========================================================================
    # 5. Safe Chromium CDP Browser Cache
    # =========================================================================
    def clean_browser_caches(self, dry_run: bool = False) -> CleanupCategoryResult:
        """Safely cleans Chromium cache data while strictly preserving login session & cookies."""
        result = CleanupCategoryResult(name="browser_cache")
        chrome_dirs = [
            self.data_dir / "chrome_user_data",
            self.data_dir / "gpt_profiles",
        ]

        target_cache_names = {
            "cache",
            "cache_data",
            "code cache",
            "gpucache",
            "dawngraphitecache",
            "grshadercache",
            "crashpad",
            "shared dictionary",
        }

        for c_root in chrome_dirs:
            if not c_root.exists():
                continue
            for item in c_root.rglob("*"):
                try:
                    if item.is_dir() and item.name.lower() in target_cache_names:
                        d_bytes, f_cnt = _get_dir_size_and_count(item)
                        result.bytes_freed += d_bytes
                        result.files_removed += f_cnt
                        result.dirs_removed += 1
                        if not dry_run:
                            shutil.rmtree(item, ignore_errors=True)
                except Exception as exc:
                    result.errors.append(f"Cache {item}: {exc}")

        return result

    # =========================================================================
    # 6. Logs, Scratch, FB Crossposter Temp
    # =========================================================================
    def clean_logs_and_temp(
        self,
        max_log_age_days: float = 7.0,
        max_scratch_age_days: float = 7.0,
        dry_run: bool = False,
    ) -> CleanupCategoryResult:
        result = CleanupCategoryResult(name="logs_and_temp")
        now = time.time()
        log_threshold = max_log_age_days * 86400.0
        scratch_threshold = max_scratch_age_days * 86400.0

        # 1. Logs directory
        logs_dir = self.data_dir / "logs"
        if logs_dir.exists():
            for lf in logs_dir.glob("*.log*"):
                try:
                    mtime = lf.stat().st_mtime
                    if now - mtime > log_threshold:
                        sz = lf.stat().st_size
                        result.bytes_freed += sz
                        result.files_removed += 1
                        if not dry_run:
                            lf.unlink(missing_ok=True)
                except Exception as exc:
                    result.errors.append(f"Log {lf.name}: {exc}")

        # 2. FB crossposter temp
        fb_temp_dir = self.data_dir / "fb_crossposter_temp"
        if fb_temp_dir.exists():
            for fb_file in fb_temp_dir.glob("*"):
                try:
                    if fb_file.is_file():
                        sz = fb_file.stat().st_size
                        result.bytes_freed += sz
                        result.files_removed += 1
                        if not dry_run:
                            fb_file.unlink(missing_ok=True)
                except Exception as exc:
                    result.errors.append(f"FB temp {fb_file.name}: {exc}")

        # 3. Scratch directory
        scratch_dir = PROJECT_ROOT / "scratch"
        if scratch_dir.exists():
            for sf in scratch_dir.rglob("*"):
                try:
                    if sf.is_file():
                        mtime = sf.stat().st_mtime
                        if now - mtime > scratch_threshold:
                            sz = sf.stat().st_size
                            result.bytes_freed += sz
                            result.files_removed += 1
                            if not dry_run:
                                sf.unlink(missing_ok=True)
                except Exception as exc:
                    result.errors.append(f"Scratch {sf.name}: {exc}")

        # 4. Python bytecode caches
        for root_p in [PROJECT_ROOT, self.omnivoice_jobs_dir.parent.parent]:
            if not root_p.exists():
                continue
            for pdir in root_p.rglob("__pycache__"):
                try:
                    d_bytes, f_cnt = _get_dir_size_and_count(pdir)
                    result.bytes_freed += d_bytes
                    result.files_removed += f_cnt
                    result.dirs_removed += 1
                    if not dry_run:
                        shutil.rmtree(pdir, ignore_errors=True)
                except Exception as exc:
                    result.errors.append(f"Pycache {pdir}: {exc}")

        return result

    # =========================================================================
    # 7. Ghost DBs & Backup DBs
    # =========================================================================
    def clean_ghost_and_backup_dbs(self, dry_run: bool = False) -> CleanupCategoryResult:
        result = CleanupCategoryResult(name="ghost_and_backup_dbs")

        # 1. Backup DB files (*.bak, *.db.pre_*)
        for bak_file in self.data_dir.glob("*.bak*"):
            try:
                sz = bak_file.stat().st_size
                result.bytes_freed += sz
                result.files_removed += 1
                if not dry_run:
                    bak_file.unlink(missing_ok=True)
            except Exception as exc:
                result.errors.append(f"Bak DB {bak_file.name}: {exc}")

        # 2. Ghost 0-byte DB / sqlite files
        search_roots = [
            PROJECT_ROOT.parent,
            PROJECT_ROOT,
            self.data_dir,
        ]
        for s_root in search_roots:
            if not s_root.exists():
                continue
            for pattern in ["*.db", "*.sqlite", "*.sqlite3"]:
                for db_candidate in s_root.glob(pattern):
                    try:
                        # Never delete the active database.db in data/
                        if db_candidate.resolve() == self.db_path.resolve():
                            continue
                        if db_candidate.is_file() and db_candidate.stat().st_size == 0:
                            result.files_removed += 1
                            if not dry_run:
                                db_candidate.unlink(missing_ok=True)
                    except Exception as exc:
                        result.errors.append(f"Ghost DB {db_candidate.name}: {exc}")

        return result

    # =========================================================================
    # 8. Database Pruning & VACUUM
    # =========================================================================
    def optimize_database(
        self,
        prune_system_jobs_days: float = 30.0,
        run_vacuum: bool = True,
        dry_run: bool = False,
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 1. Prune old system_jobs (done, canceled, error older than N days)
            cutoff_date = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=prune_system_jobs_days)
            ).strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute(
                """
                SELECT count(*) FROM system_jobs
                WHERE status IN ('done', 'completed', 'canceled', 'error', 'failed')
                AND created_at < ?
                """,
                (cutoff_date,),
            )
            count_to_prune = cursor.fetchone()[0]
            stats["system_jobs_pruned"] = count_to_prune

            if not dry_run and count_to_prune > 0:
                cursor.execute(
                    """
                    DELETE FROM system_jobs
                    WHERE status IN ('done', 'completed', 'canceled', 'error', 'failed')
                    AND created_at < ?
                    """,
                    (cutoff_date,),
                )
                conn.commit()

            conn.close()

            # 2. VACUUM
            if not dry_run and run_vacuum:
                conn_vac = sqlite3.connect(self.db_path)
                conn_vac.execute("VACUUM;")
                conn_vac.close()
                stats["vacuum_executed"] = True

            stats["final_db_bytes"] = self.db_path.stat().st_size
        except Exception as exc:
            stats["error"] = str(exc)

        return stats

    # =========================================================================
    # 9. Full Orchestrated Cleanup
    # =========================================================================
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
    ) -> CleanupSummary:
        summary = CleanupSummary(
            timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            dry_run=dry_run,
        )

        if clean_omni:
            summary.categories["omnivoice_jobs"] = self.clean_omnivoice_jobs(dry_run=dry_run)
        if clean_segments:
            summary.categories["video_segments"] = self.clean_video_segments(dry_run=dry_run)
        if clean_renders:
            summary.categories["orphan_renders"] = self.clean_orphan_renders(dry_run=dry_run)
        if clean_audio:
            summary.categories["orphan_audio"] = self.clean_orphan_audio(dry_run=dry_run)
        if clean_cache:
            summary.categories["browser_cache"] = self.clean_browser_caches(dry_run=dry_run)
        if clean_logs:
            summary.categories["logs_and_temp"] = self.clean_logs_and_temp(dry_run=dry_run)
        if clean_ghost_dbs:
            summary.categories["ghost_and_backup_dbs"] = self.clean_ghost_and_backup_dbs(dry_run=dry_run)

        # Database optimization
        summary.database_stats = self.optimize_database(run_vacuum=vacuum_db, dry_run=dry_run)

        # Aggregate totals
        for cat in summary.categories.values():
            summary.total_files_removed += cat.files_removed
            summary.total_dirs_removed += cat.dirs_removed
            summary.total_bytes_freed += cat.bytes_freed

        return summary


# Global singleton instance
cleaner = SystemCleaner()
