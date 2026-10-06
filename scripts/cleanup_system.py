#!/usr/bin/env python3
"""CLI utility to clean up temporary files, caches, orphan media, and optimize databases."""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add src to sys.path
_HERE = Path(__file__).resolve().parent
_SRC_DIR = _HERE.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from auto_yt.services.system_cleaner import cleaner


def main():
    parser = argparse.ArgumentParser(description="Auto_YT & OmniVoice System Cleaner CLI")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and calculate cleanable storage without deleting any files",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Perform standard safe cleanup (OmniVoice jobs, segments, logs, cache, ghost DBs)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Perform exhaustive cleanup across all categories",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run SQLite VACUUM to compact database.db",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )

    args = parser.parse_args()

    # Default to safe mode if neither --safe nor --all is specified
    is_dry_run = args.dry_run
    run_vacuum = args.vacuum or args.all or args.safe

    if not args.json:
        mode_str = "DRY RUN (Scan Only)" if is_dry_run else "EXECUTE CLEANUP"
        print(f"============================================================")
        print(f"  Auto_YT & OmniVoice System Cleaner: {mode_str}")
        print(f"============================================================")

    summary = cleaner.run_full_cleanup(
        dry_run=is_dry_run,
        clean_omni=True,
        clean_segments=True,
        clean_renders=True,
        clean_audio=True,
        clean_cache=True,
        clean_logs=True,
        clean_ghost_dbs=True,
        vacuum_db=run_vacuum,
    )

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return

    print(f"\n[+] Cleanup Category Breakdown:")
    print(f"{'Category':<25} | {'Files':<10} | {'Dirs':<8} | {'Space Freed'}")
    print("-" * 65)

    for key, cat in summary.categories.items():
        size_str = f"{cat.mb_freed:,.2f} MB" if cat.mb_freed < 1024 else f"{cat.gb_freed:,.2f} GB"
        print(f"{cat.name:<25} | {cat.files_removed:<10} | {cat.dirs_removed:<8} | {size_str}")

    print("-" * 65)
    total_size_str = (
        f"{summary.total_mb_freed:,.2f} MB"
        if summary.total_mb_freed < 1024
        else f"{summary.total_gb_freed:,.2f} GB"
    )
    print(f"{'TOTAL':<25} | {summary.total_files_removed:<10} | {summary.total_dirs_removed:<8} | {total_size_str}")

    if summary.database_stats:
        db_s = summary.database_stats
        init_mb = round(db_s.get("initial_db_bytes", 0) / (1024 * 1024), 2)
        fin_mb = round(db_s.get("final_db_bytes", 0) / (1024 * 1024), 2)
        pruned = db_s.get("system_jobs_pruned", 0)
        vac = "Yes" if db_s.get("vacuum_executed") else "No"
        print(f"\n[+] Database Optimization:")
        print(f"    - Old system_jobs pruned: {pruned}")
        print(f"    - VACUUM executed: {vac}")
        print(f"    - DB Size: {init_mb} MB -> {fin_mb} MB")

    print("\n[+] Finished successfully.")


if __name__ == "__main__":
    main()
