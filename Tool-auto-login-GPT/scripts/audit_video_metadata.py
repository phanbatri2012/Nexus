#!/usr/bin/env python3
"""Audit generated metadata and conservatively repair unambiguous swaps."""

from __future__ import annotations

import argparse
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

from auto_yt.services import database as db
from auto_yt.services.metadata_validation import (
    metadata_component_error,
    repair_swapped_script_metadata,
)


def _load_candidates(video_id: int | None) -> list[dict]:
    query = """
        SELECT
            videos.id,
            videos.generated_script,
            COALESCE(videos.is_published, 0) AS is_published,
            EXISTS(
                SELECT 1 FROM video_publications
                WHERE video_publications.video_id = videos.id
            ) AS has_publication
        FROM videos
        WHERE COALESCE(videos.source_type, ?) = ?
          AND TRIM(COALESCE(videos.generated_script, '')) != ''
    """
    params: list[object] = [db.VIDEO_SOURCE_GENERATED, db.VIDEO_SOURCE_GENERATED]
    if video_id is not None:
        query += " AND videos.id = ?"
        params.append(video_id)
    query += " ORDER BY videos.id"

    connection = sqlite3.connect(str(db.DB_PATH))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def audit_video_metadata(video_id: int | None = None, apply: bool = False) -> dict:
    candidates = _load_candidates(video_id)
    findings: list[dict] = []
    repaired_count = 0
    for row in candidates:
        script = str(row.get("generated_script") or "")
        title_match = db.TITLE_SECTION_PATTERN.search(script)
        description_match = db.DESCRIPTION_SECTION_PATTERN.search(script)
        title = title_match.group(1).strip() if title_match else ""
        description = description_match.group(1).strip() if description_match else ""
        reasons = []
        title_error = metadata_component_error("title", title)
        description_error = metadata_component_error("description", description)
        if title_error:
            reasons.append(f"title: {title_error}")
        if description_error:
            reasons.append(f"description: {description_error}")
        if title and description and title.casefold() == description.casefold():
            reasons.append("title and description are duplicated")

        repaired_script, is_swapped = repair_swapped_script_metadata(script)
        if is_swapped:
            reasons.append("title and description are unambiguously swapped")
        if not reasons:
            continue

        published = bool(row.get("is_published") or row.get("has_publication"))
        status = "reported"
        if apply and is_swapped and not published:
            if not db.update_script(int(row["id"]), repaired_script):
                raise RuntimeError(f"Could not update video {row['id']}")
            repaired_count += 1
            status = "repaired"
        elif apply and published:
            status = "skipped_published"
        elif apply and not is_swapped:
            status = "skipped_ambiguous"

        findings.append(
            {
                "video_id": int(row["id"]),
                "published": published,
                "status": status,
                "reasons": reasons,
            }
        )

    return {
        "mode": "apply" if apply else "dry_run",
        "scanned": len(candidates),
        "flagged": len(findings),
        "repaired": repaired_count,
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit generated video metadata; repairs are opt-in and unpublished-only."
    )
    parser.add_argument("--video-id", type=int, help="Limit the audit to one video ID.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Repair only unambiguous title/description swaps on unpublished drafts.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            audit_video_metadata(video_id=args.video_id, apply=args.apply),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
