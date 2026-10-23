import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from scripts import migrate_nexus_runtime_paths as migration
from scripts.migrate_nexus_runtime_paths import migrate_runtime_paths


def _create_database(path: Path, artifact_path: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE video_artifacts "
            "(path TEXT NOT NULL, artifact_type TEXT NOT NULL, metadata_json TEXT)"
        )
        connection.execute(
            "INSERT INTO video_artifacts(path, artifact_type, metadata_json) "
            "VALUES (?, 'render', ?)",
            (artifact_path, json.dumps({"source_path": artifact_path})),
        )
        connection.commit()
    finally:
        connection.close()


def test_runtime_path_migration_is_transactional_and_idempotent(tmp_path):
    old_root = tmp_path / "Tool-auto-login-GPT"
    new_root = tmp_path / "Nexus_Studio_Engine"
    data_root = new_root / "data"
    artifact = data_root / "video_artifacts" / "renders" / "video.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")
    database_path = data_root / "database.db"
    old_artifact = old_root / "data" / "video_artifacts" / "renders" / "video.mp4"
    _create_database(database_path, str(old_artifact))

    account_path = data_root / "account.json"
    account_path.write_text(
        json.dumps(
            {
                "gpt_account1": {
                    "folder_user_data": str(old_root / "data" / "chrome_user_data"),
                    "encrypted_secret": "DPAPI:unchanged",
                },
                "trace": f'File "{old_root / "data" / "worker.py"}"',
                "note": f"do not replace non-prefix pathX{old_root / 'data'}",
            }
        ),
        encoding="utf-8",
    )
    unrelated = data_root / "video_artifacts" / "visual_plans" / "unrelated.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(json.dumps({"label": "auto_yt is not a path"}), encoding="utf-8")
    binary_log = data_root / "chrome_user_data" / "Default" / "000003.log"
    binary_log.parent.mkdir(parents=True)
    binary_log.write_bytes(b"\xc8\x00leveldb")

    dry_run = migrate_runtime_paths(
        old_root=old_root,
        new_root=new_root,
        data_root=data_root,
        apply=False,
    )
    assert dry_run.database_rows == 2
    assert dry_run.text_files == 1
    assert not (data_root / migration.MARKER_NAME).exists()

    applied = migrate_runtime_paths(
        old_root=old_root,
        new_root=new_root,
        data_root=data_root,
        apply=True,
    )
    assert applied.database_rows == 2
    payload = json.loads(account_path.read_text(encoding="utf-8"))
    assert payload["gpt_account1"]["folder_user_data"].startswith(str(new_root))
    assert payload["gpt_account1"]["encrypted_secret"] == "DPAPI:unchanged"
    assert str(new_root) in payload["trace"]
    assert payload["note"] == f"do not replace non-prefix pathX{old_root / 'data'}"
    assert json.loads(unrelated.read_text(encoding="utf-8"))["label"] == "auto_yt is not a path"

    connection = sqlite3.connect(database_path)
    try:
        stored_path, metadata_json = connection.execute(
            "SELECT path, metadata_json FROM video_artifacts"
        ).fetchone()
    finally:
        connection.close()
    assert stored_path == str(artifact)
    assert json.loads(metadata_json)["source_path"] == str(artifact)
    assert str(old_root).encode("utf-8") not in database_path.read_bytes()

    repeated = migrate_runtime_paths(
        old_root=old_root,
        new_root=new_root,
        data_root=data_root,
        apply=True,
    )
    assert repeated.already_complete is True


def test_runtime_path_migration_rolls_back_database_and_text_when_marker_write_fails(tmp_path):
    old_root = tmp_path / "legacy"
    new_root = tmp_path / "Nexus_Studio_Engine"
    data_root = new_root / "data"
    artifact = data_root / "video_artifacts" / "renders" / "video.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")
    database_path = data_root / "database.db"
    old_artifact = old_root / "data" / "video_artifacts" / "renders" / "video.mp4"
    _create_database(database_path, str(old_artifact))

    account_path = data_root / "account.json"
    original_account = json.dumps({"folder_user_data": str(old_root / "data" / "profile")})
    account_path.write_text(original_account, encoding="utf-8")

    real_atomic_write = migration._atomic_write

    def fail_marker(path: Path, content: bytes) -> None:
        if path.name == migration.MARKER_NAME:
            raise OSError("simulated marker failure")
        real_atomic_write(path, content)

    with patch.object(migration, "_atomic_write", side_effect=fail_marker):
        try:
            migrate_runtime_paths(
                old_root=old_root,
                new_root=new_root,
                data_root=data_root,
                apply=True,
            )
        except OSError as exc:
            assert "simulated marker failure" in str(exc)
        else:
            raise AssertionError("Migration should have failed")

    assert account_path.read_text(encoding="utf-8") == original_account
    assert not (data_root / migration.MARKER_NAME).exists()
    connection = sqlite3.connect(database_path)
    try:
        stored_path = connection.execute("SELECT path FROM video_artifacts").fetchone()[0]
    finally:
        connection.close()
    assert stored_path == str(old_artifact)


def test_historic_windows_root_detection_respects_path_boundaries(tmp_path):
    new_root = tmp_path / "Nexus_Studio_Engine"
    pattern = migration._historic_windows_root_pattern()
    old_root = r"E:\yt\Tool\Auto_YT\Tool-auto-login-GPT"

    transformed, count = migration._replace_legacy_roots(
        f'File "{old_root}\\data\\artifact.png"', (pattern,), new_root
    )
    assert count == 1
    assert str(new_root) in transformed

    untouched, count = migration._replace_legacy_roots(
        f"prefix{old_root}\\data\\artifact.png", (pattern,), new_root
    )
    assert count == 0
    assert untouched.startswith("prefixE:")
