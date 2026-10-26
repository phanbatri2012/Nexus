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

from nexus_engine.services.system_cleaner import (
    DEFAULT_RENDER_RETENTION_DAYS,
    cleaner,
)


def main():
    parser = argparse.ArgumentParser(description="Nexus Studio Engine System Cleaner CLI")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and calculate cleanable storage without deleting any files",
    )
    mode.add_argument(
        "--safe",
        action="store_true",
        help="Execute conservative cleanup while preserving DB backups and migration quarantine",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Execute all policy-safe categories, including expired verified quarantine and old DB backups",
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
    parser.add_argument(
        "--render-retention-days",
        type=float,
        default=DEFAULT_RENDER_RETENTION_DAYS,
        help="Rolling retention window for final MP4 renders (default: 7 days)",
    )

    args = parser.parse_args()

    if args.render_retention_days < 0:
        parser.error("--render-retention-days must be zero or greater")

    # No mode is deliberately read-only. Destructive cleanup requires an
    # explicit --safe or --all flag.
    is_dry_run = args.dry_run or not (args.safe or args.all)
    run_vacuum = bool(args.vacuum or args.all or args.safe)

    if not args.json:
        mode_str = "DRY RUN (Scan Only)" if is_dry_run else "EXECUTE CLEANUP"
        print(f"============================================================")
        print(f"  Nexus Studio Engine System Cleaner: {mode_str}")
        print(f"============================================================")

    summary = cleaner.run_full_cleanup(
        dry_run=is_dry_run,
        clean_omni=True,
        clean_segments=True,
        clean_renders=True,
        clean_audio=True,
        clean_cache=True,
        clean_logs=True,
        clean_ghost_dbs=bool(args.all or is_dry_run),
        vacuum_db=run_vacuum,
        render_retention_days=args.render_retention_days,
    )

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return

    print(f"\n[+] Cleanup Category Breakdown:")
    print(f"{'Category':<25} | {'Planned':<10} | {'Removed':<10} | {'Space'}")
    print("-" * 75)

    for key, cat in summary.categories.items():
        byte_count = cat.bytes_planned if is_dry_run else cat.bytes_freed
        size_mb = byte_count / (1024 * 1024)
        size_str = f"{size_mb:,.2f} MB" if size_mb < 1024 else f"{byte_count / (1024 ** 3):,.2f} GB"
        print(
            f"{cat.name:<25} | {cat.files_planned:<10} | "
            f"{cat.files_removed:<10} | {size_str}"
        )

    print("-" * 75)
    total_bytes = summary.total_bytes_planned if is_dry_run else summary.total_bytes_freed
    total_mb = total_bytes / (1024 * 1024)
    total_size_str = (
        f"{total_mb:,.2f} MB"
        if total_mb < 1024
        else f"{total_bytes / (1024 ** 3):,.2f} GB"
    )
    print(
        f"{'TOTAL':<25} | {summary.total_files_planned:<10} | "
        f"{summary.total_files_removed:<10} | {total_size_str}"
    )

    if summary.blocked:
        print("\n[!] Cleanup was blocked by active work or an unverifiable safety probe.")
        for reason in summary.verification_errors:
            print(f"    - {reason}")
        for blocker in summary.blockers[:20]:
            print(
                "    - "
                f"{blocker.get('type', 'task')}:"
                f"{blocker.get('id', '')} [{blocker.get('status', '')}]"
            )

    if summary.manifest_id:
        print(f"\n[+] Manifest: {summary.manifest_path}")

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

    if summary.blocked:
        print("\n[!] Finished without deleting files.")
    else:
        print("\n[+] Finished successfully.")


if __name__ == "__main__":
    main()
