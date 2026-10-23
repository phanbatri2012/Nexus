#!/usr/bin/env python3
"""Migrate runtime paths from legacy Auto YT layouts to the Nexus root."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


MIGRATION_ID = "nexus_runtime_paths_v2"
MARKER_NAME = ".nexus-runtime-paths-v2.json"
LEGACY_PRODUCT_DIR = "Tool-auto-login-GPT"
MAX_TEXT_FILE_BYTES = 16 * 1024 * 1024
RUNTIME_TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".log",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass
class MigrationReport:
    migration_id: str = MIGRATION_ID
    database_rows: int = 0
    database_files: int = 0
    text_files: int = 0
    replacements: int = 0
    missing_targets: int = 0
    applied: bool = False
    already_complete: bool = False


@dataclass(frozen=True)
class DatabaseUpdate:
    table: str
    column: str
    rowid: int
    value: str
    replacements: int


def _explicit_root_pattern(old_root: Path) -> re.Pattern[str]:
    variants = {
        str(old_root.resolve()).rstrip("\\/"),
        old_root.resolve().as_posix().rstrip("/"),
    }
    return re.compile(
        r"(?<![A-Za-z0-9])(?:"
        + "|".join(re.escape(item) for item in sorted(variants, key=len, reverse=True))
        + r")(?=$|[\\/\s\"'.,;:)\]}])",
        re.IGNORECASE,
    )


def _historic_windows_root_pattern() -> re.Pattern[str]:
    # Requiring an absolute drive path plus the exact legacy directory avoids
    # changing ordinary prose that merely mentions the old product name.
    return re.compile(
        r"(?<![A-Za-z0-9])"
        r"[A-Z]:[\\/]+"
        r"(?:[^\\/:*?\"<>|\r\n=]+[\\/]+)*"
        + re.escape(LEGACY_PRODUCT_DIR)
        + r"(?=$|[\\/\s\"'.,;:)\]}])",
        re.IGNORECASE,
    )


def _replacement_for_match(match: re.Match[str], new_root: Path) -> str:
    matched = match.group(0)
    canonical = str(new_root.resolve())
    if "\\\\" in matched:
        return canonical.replace("\\", "\\\\")
    if "/" in matched and "\\" not in matched:
        return new_root.resolve().as_posix()
    return canonical


def _replace_legacy_roots(
    value: str,
    patterns: Iterable[re.Pattern[str]],
    new_root: Path,
) -> tuple[str, int]:
    transformed = value
    replacements = 0
    for pattern in patterns:
        transformed, count = pattern.subn(
            lambda match: _replacement_for_match(match, new_root),
            transformed,
        )
        replacements += count
    return transformed, replacements


def _transform_json(
    value: Any,
    patterns: Iterable[re.Pattern[str]],
    new_root: Path,
) -> tuple[Any, int]:
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        count = 0
        for key, child in value.items():
            transformed, child_count = _transform_json(child, patterns, new_root)
            result[key] = transformed
            count += child_count
        return result, count
    if isinstance(value, list):
        result_list = []
        count = 0
        for child in value:
            transformed, child_count = _transform_json(child, patterns, new_root)
            result_list.append(transformed)
            count += child_count
        return result_list, count
    if isinstance(value, str):
        return _replace_legacy_roots(value, patterns, new_root)
    return value, 0


def _runtime_text_files(data_root: Path) -> Iterator[Path]:
    backup_root = (data_root / ".migration-backup").resolve()
    marker_v2 = (data_root / MARKER_NAME).resolve()
    for path in data_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in RUNTIME_TEXT_SUFFIXES:
            continue
        resolved = path.resolve()
        if resolved == marker_v2 or backup_root in resolved.parents:
            continue
        if resolved.stat().st_size <= MAX_TEXT_FILE_BYTES:
            yield resolved


def _prepare_text_updates(
    data_root: Path,
    patterns: tuple[re.Pattern[str], ...],
    new_root: Path,
) -> tuple[list[tuple[Path, bytes, bytes]], int]:
    updates: list[tuple[Path, bytes, bytes]] = []
    total_replacements = 0
    for path in _runtime_text_files(data_root):
        original = path.read_bytes()
        has_bom = original.startswith(b"\xef\xbb\xbf")
        try:
            text = original.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            if LEGACY_PRODUCT_DIR.encode("ascii") in original:
                raise RuntimeError(
                    f"Binary runtime file contains a legacy path: {path.name}"
                ) from exc
            continue

        replacements = 0
        transformed_text = text
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                transformed_text, replacements = _replace_legacy_roots(
                    text, patterns, new_root
                )
            else:
                transformed, replacements = _transform_json(payload, patterns, new_root)
                if path.name == ".nexus-layout-v1.json" and isinstance(transformed, dict):
                    if "legacy_root" in transformed:
                        transformed.pop("legacy_root", None)
                        transformed["legacy_root_redacted"] = True
                transformed_text = json.dumps(
                    transformed, ensure_ascii=False, indent=2
                ) + "\n"
        else:
            transformed_text, replacements = _replace_legacy_roots(
                text, patterns, new_root
            )

        if not replacements:
            continue
        encoding = "utf-8-sig" if has_bom else "utf-8"
        updates.append((path, original, transformed_text.encode(encoding)))
        total_replacements += replacements
    return updates, total_replacements


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _text_columns(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (table,) in tables:
        quoted_table = _quote_identifier(str(table))
        for column in connection.execute(f"PRAGMA table_info({quoted_table})"):
            name = str(column[1])
            declared_type = str(column[2] or "").upper()
            if declared_type and not any(
                affinity in declared_type
                for affinity in ("TEXT", "CHAR", "CLOB", "JSON")
            ):
                continue
            yield str(table), name


def _prepare_database_updates(
    connection: sqlite3.Connection,
    patterns: tuple[re.Pattern[str], ...],
    new_root: Path,
) -> list[DatabaseUpdate]:
    updates: list[DatabaseUpdate] = []
    for table, column in _text_columns(connection):
        quoted_table = _quote_identifier(table)
        quoted_column = _quote_identifier(column)
        try:
            rows = connection.execute(
                f"SELECT rowid, {quoted_column} FROM {quoted_table} "
                f"WHERE typeof({quoted_column}) = 'text'"
            )
            for rowid, stored_value in rows:
                transformed, replacements = _replace_legacy_roots(
                    str(stored_value), patterns, new_root
                )
                if replacements:
                    updates.append(
                        DatabaseUpdate(
                            table=table,
                            column=column,
                            rowid=int(rowid),
                            value=transformed,
                            replacements=replacements,
                        )
                    )
        except sqlite3.OperationalError as exc:
            if "no such column: rowid" not in str(exc).lower():
                raise
            raise RuntimeError(
                f"Cannot safely migrate WITHOUT ROWID table: {table}"
            ) from exc
    return updates


def _database_files(data_root: Path) -> list[Path]:
    active = data_root / "database.db"
    if not active.is_file():
        raise FileNotFoundError("Canonical data/database.db is missing")
    backups_root = data_root / "backups"
    historical = []
    if backups_root.is_dir():
        historical = sorted(
            path
            for path in backups_root.iterdir()
            if path.is_file() and path.suffix.lower() in {".db", ".sqlite"}
        )
    return [active, *historical]


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.nexus-migrate-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _backup_database(database_path: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.unlink(missing_ok=True)
    source = sqlite3.connect(database_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()


def _vacuum_and_verify_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError("SQLite integrity check failed after migration")
    finally:
        connection.close()


def _apply_database_updates(
    connection: sqlite3.Connection,
    updates: Iterable[DatabaseUpdate],
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for update in updates:
            connection.execute(
                f"UPDATE {_quote_identifier(update.table)} "
                f"SET {_quote_identifier(update.column)} = ? WHERE rowid = ?",
                (update.value, update.rowid),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _count_missing_targets(updates: Iterable[DatabaseUpdate], new_root: Path) -> int:
    canonical = str(new_root.resolve()).casefold()
    missing = 0
    for update in updates:
        if update.column.casefold() not in {"path", "profile_dir", "folder_user_data"}:
            continue
        value = update.value
        if value.casefold().startswith(canonical) and not Path(value).exists():
            missing += 1
    return missing


def _backup_path_for(database_path: Path, data_root: Path, backup_root: Path) -> Path:
    relative = database_path.relative_to(data_root).as_posix().replace("/", "__")
    return backup_root / f"{relative}.pre-nexus-runtime-paths-v2"


def migrate_runtime_paths(
    *,
    old_root: Path,
    new_root: Path,
    data_root: Path,
    apply: bool,
) -> MigrationReport:
    old_root = old_root.resolve()
    new_root = new_root.resolve()
    data_root = data_root.resolve()
    if old_root == new_root:
        raise ValueError("Legacy and canonical roots must be different")
    if data_root != new_root / "data":
        raise ValueError("The data root must be the canonical repository data directory")

    marker = data_root / MARKER_NAME
    backup_root = data_root / ".migration-backup"
    patterns = (_explicit_root_pattern(old_root), _historic_windows_root_pattern())
    database_paths = _database_files(data_root)
    text_updates, text_replacements = _prepare_text_updates(
        data_root, patterns, new_root
    )

    database_updates: dict[Path, list[DatabaseUpdate]] = {}
    for database_path in database_paths:
        connection = sqlite3.connect(database_path)
        try:
            updates = _prepare_database_updates(connection, patterns, new_root)
        finally:
            connection.close()
        if updates:
            database_updates[database_path] = updates

    database_cells = sum(len(items) for items in database_updates.values())
    database_replacements = sum(
        update.replacements
        for items in database_updates.values()
        for update in items
    )
    report = MigrationReport(
        database_rows=database_cells,
        database_files=len(database_updates),
        text_files=len(text_updates),
        replacements=text_replacements + database_replacements,
        missing_targets=_count_missing_targets(
            database_updates.get(data_root / "database.db", []), new_root
        ),
        applied=apply,
        already_complete=marker.exists() and not database_updates and not text_updates,
    )
    if not apply or report.already_complete:
        return report

    database_backups: dict[Path, Path] = {}
    written: list[tuple[Path, bytes]] = []
    try:
        for database_path in database_updates:
            backup_path = _backup_path_for(database_path, data_root, backup_root)
            _backup_database(database_path, backup_path)
            database_backups[database_path] = backup_path

        for path, original, updated in text_updates:
            _atomic_write(path, updated)
            written.append((path, original))

        for database_path, updates in database_updates.items():
            connection = sqlite3.connect(database_path)
            try:
                _apply_database_updates(connection, updates)
            finally:
                connection.close()

        for database_path in database_updates:
            _vacuum_and_verify_database(database_path)

        remaining_text, _ = _prepare_text_updates(data_root, patterns, new_root)
        remaining_database = 0
        for database_path in database_paths:
            connection = sqlite3.connect(database_path)
            try:
                remaining_database += len(
                    _prepare_database_updates(connection, patterns, new_root)
                )
            finally:
                connection.close()
        if remaining_text or remaining_database:
            raise RuntimeError("Legacy runtime paths remain after migration")

        marker_payload = {
            **asdict(report),
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "canonical_root": str(new_root),
            "storage_compacted": True,
        }
        _atomic_write(
            marker,
            (json.dumps(marker_payload, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    except Exception:
        for database_path, backup_path in database_backups.items():
            if backup_path.exists():
                os.replace(backup_path, database_path)
        for path, original in reversed(written):
            _atomic_write(path, original)
        marker.unlink(missing_ok=True)
        raise

    for backup_path in database_backups.values():
        backup_path.unlink(missing_ok=True)
    try:
        backup_root.rmdir()
    except OSError:
        pass
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = migrate_runtime_paths(
        old_root=args.old_root,
        new_root=args.new_root,
        data_root=args.data_root,
        apply=args.apply,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
