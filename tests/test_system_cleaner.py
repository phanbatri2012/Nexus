from __future__ import annotations

import asyncio
import datetime
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException

from nexus_engine.services import maintenance_guard
from nexus_engine.services.system_cleaner import SystemCleaner


UTC = datetime.timezone.utc


def _safe_cleanup_status() -> dict:
    return {
        "safe_to_cleanup": True,
        "status_verified": True,
        "total_blocking_jobs": 0,
        "blocking_jobs": [],
        "verification_errors": [],
        "reasons": [],
    }


def _create_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE videos (id INTEGER PRIMARY KEY);
        CREATE TABLE video_artifacts (
            id INTEGER PRIMARY KEY,
            video_id INTEGER,
            artifact_type TEXT,
            path TEXT,
            content_hash TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            duration_seconds REAL,
            width INTEGER,
            height INTEGER,
            codecs_json TEXT,
            status TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE youtube_publish_workflows (
            id TEXT PRIMARY KEY,
            video_id INTEGER,
            system_job_id TEXT,
            status TEXT,
            scheduled_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE system_jobs (
            id TEXT PRIMARY KEY,
            video_id INTEGER,
            job_type TEXT,
            status TEXT,
            next_retry_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE audio_tasks (
            video_id INTEGER,
            status TEXT,
            audio_url TEXT,
            updated_at TEXT
        );
        CREATE TABLE tts_previews (audio_filename TEXT);
        CREATE TABLE fb_crossposter_queue (
            id INTEGER PRIMARY KEY,
            youtube_id TEXT,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE youtube_channels (
            id INTEGER PRIMARY KEY,
            gpm_profile_id TEXT
        );
        CREATE TABLE fb_crossposter_settings (
            source_gpm_profile_id TEXT,
            target_gpm_profile_id TEXT
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def cleaner_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path / "project"
    data_dir = project_root / "data"
    jobs_dir = project_root / "omnivoice" / "data" / "jobs"
    renders_dir = data_dir / "video_artifacts" / "renders"
    renders_dir.mkdir(parents=True)
    jobs_dir.mkdir(parents=True)
    _create_database(data_dir / "database.db")
    monkeypatch.setattr(
        maintenance_guard,
        "get_cleanup_status",
        _safe_cleanup_status,
    )
    cleaner = SystemCleaner(
        data_dir=data_dir,
        omnivoice_jobs_dir=jobs_dir,
        project_root=project_root,
    )
    return cleaner, data_dir, renders_dir


def _add_render(
    data_dir: Path,
    renders_dir: Path,
    *,
    artifact_id: int,
    video_id: int,
    age_days: float,
    workflow_status: str = "",
    with_active_job: bool = False,
    path: Path | None = None,
) -> Path:
    render_path = path or renders_dir / f"video-{video_id}.mp4"
    render_path.parent.mkdir(parents=True, exist_ok=True)
    render_path.write_bytes((f"video-{video_id}" * 64).encode("utf-8"))
    timestamp = time.time() - age_days * 86400
    os.utime(render_path, (timestamp, timestamp))
    iso_time = datetime.datetime.fromtimestamp(timestamp, UTC).isoformat()
    conn = sqlite3.connect(data_dir / "database.db")
    conn.execute("INSERT OR IGNORE INTO videos(id) VALUES (?)", (video_id,))
    conn.execute(
        """
        INSERT INTO video_artifacts(
            id, video_id, artifact_type, path, content_hash, mime_type,
            size_bytes, codecs_json, status, metadata_json, created_at, updated_at
        ) VALUES (?, ?, 'final_mp4', ?, ?, 'video/mp4', ?, '{}', 'ready', '{}', ?, ?)
        """,
        (
            artifact_id,
            video_id,
            str(render_path),
            f"hash-{artifact_id}",
            render_path.stat().st_size,
            iso_time,
            iso_time,
        ),
    )
    if workflow_status:
        conn.execute(
            """
            INSERT INTO youtube_publish_workflows(
                id, video_id, status, scheduled_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"workflow-{artifact_id}",
                video_id,
                workflow_status,
                iso_time,
                iso_time,
                iso_time,
            ),
        )
    if with_active_job:
        conn.execute(
            """
            INSERT INTO system_jobs(
                id, video_id, job_type, status, next_retry_at, created_at, updated_at
            ) VALUES (?, ?, 'video_generation', 'retry_wait', '', ?, ?)
            """,
            (f"job-{artifact_id}", video_id, iso_time, iso_time),
        )
    conn.commit()
    conn.close()
    return render_path


def _render_only(cleaner: SystemCleaner, *, dry_run: bool):
    return cleaner.run_full_cleanup(
        dry_run=dry_run,
        clean_omni=False,
        clean_segments=False,
        clean_renders=True,
        clean_audio=False,
        clean_cache=False,
        clean_logs=False,
        clean_ghost_dbs=False,
        vacuum_db=False,
        render_retention_days=30,
    )


def test_default_render_retention_is_seven_days(cleaner_env):
    cleaner, _, _ = cleaner_env

    summary = cleaner.run_full_cleanup(
        dry_run=True,
        clean_omni=False,
        clean_segments=False,
        clean_renders=False,
        clean_audio=False,
        clean_cache=False,
        clean_logs=False,
        clean_ghost_dbs=False,
        vacuum_db=False,
    )

    assert summary.policy["render_retention_days"] == 7.0


def test_render_retention_protects_recent_and_non_terminal(cleaner_env):
    cleaner, data_dir, renders_dir = cleaner_env
    _add_render(
        data_dir, renders_dir, artifact_id=1, video_id=101, age_days=40
    )
    _add_render(
        data_dir, renders_dir, artifact_id=2, video_id=102, age_days=10
    )
    _add_render(
        data_dir,
        renders_dir,
        artifact_id=3,
        video_id=103,
        age_days=40,
        workflow_status="scheduled",
    )
    _add_render(
        data_dir,
        renders_dir,
        artifact_id=4,
        video_id=104,
        age_days=40,
        with_active_job=True,
    )

    summary = _render_only(cleaner, dry_run=True)
    category = summary.categories["expired_renders"]

    assert category.files_planned == 1
    assert category.files_protected == 3
    assert category.protected_reasons["within_retention"] == 1
    assert category.protected_reasons[
        "scheduled_past_due_requires_reconciliation"
    ] == 1
    assert category.protected_bytes_by_reason[
        "scheduled_past_due_requires_reconciliation"
    ] > 0
    assert category.protected_reasons["active_job"] == 1
    assert summary.manifest_id == ""
    assert not cleaner.manifests_dir.exists()


def test_render_retention_boundary_and_terminal_workflow(cleaner_env):
    cleaner, data_dir, renders_dir = cleaner_env
    _add_render(
        data_dir, renders_dir, artifact_id=5, video_id=105, age_days=29.99
    )
    _add_render(
        data_dir,
        renders_dir,
        artifact_id=6,
        video_id=106,
        age_days=30.01,
        workflow_status="completed",
    )

    summary = _render_only(cleaner, dry_run=True)
    category = summary.categories["expired_renders"]

    assert category.files_planned == 1
    assert category.protected_reasons["within_retention"] == 1


def test_orphan_render_artifact_is_eligible_after_retention(cleaner_env):
    cleaner, data_dir, renders_dir = cleaner_env
    _add_render(
        data_dir, renders_dir, artifact_id=7, video_id=107, age_days=40
    )
    conn = sqlite3.connect(data_dir / "database.db")
    conn.execute("DELETE FROM videos WHERE id = 107")
    conn.commit()
    conn.close()

    summary = _render_only(cleaner, dry_run=True)

    assert summary.categories["expired_renders"].files_planned == 1


def test_execute_writes_manifest_deletes_and_marks_artifact_purged(cleaner_env):
    cleaner, data_dir, renders_dir = cleaner_env
    render_path = _add_render(
        data_dir, renders_dir, artifact_id=10, video_id=110, age_days=40
    )

    summary = _render_only(cleaner, dry_run=False)

    assert summary.blocked is False
    assert summary.manifest_id
    assert not render_path.exists()
    manifest = json.loads(Path(summary.manifest_path).read_text(encoding="utf-8"))
    assert manifest["state"] == "completed"
    assert manifest["items"][0]["result"] == "deleted"
    conn = sqlite3.connect(data_dir / "database.db")
    status, metadata_json = conn.execute(
        "SELECT status, metadata_json FROM video_artifacts WHERE id = 10"
    ).fetchone()
    conn.close()
    assert status == "purged"
    metadata = json.loads(metadata_json)
    assert metadata["cleanup"]["manifest_id"] == summary.manifest_id


def test_artifact_is_not_marked_purged_when_delete_fails(
    cleaner_env,
    monkeypatch: pytest.MonkeyPatch,
):
    cleaner, data_dir, renders_dir = cleaner_env
    render_path = _add_render(
        data_dir, renders_dir, artifact_id=11, video_id=111, age_days=40
    )
    original_unlink = Path.unlink

    def fail_render_unlink(path: Path, *args, **kwargs):
        if path.resolve() == render_path.resolve():
            raise PermissionError("simulated locked render")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_render_unlink)

    summary = _render_only(cleaner, dry_run=False)

    assert render_path.exists()
    conn = sqlite3.connect(data_dir / "database.db")
    status = conn.execute(
        "SELECT status FROM video_artifacts WHERE id = 11"
    ).fetchone()[0]
    conn.close()
    assert status == "ready"
    manifest = json.loads(Path(summary.manifest_path).read_text(encoding="utf-8"))
    assert manifest["items"][0]["result"] == "delete_failed"


def test_changed_fingerprint_is_skipped(cleaner_env, monkeypatch: pytest.MonkeyPatch):
    cleaner, data_dir, renders_dir = cleaner_env
    render_path = _add_render(
        data_dir, renders_dir, artifact_id=20, video_id=120, age_days=40
    )
    original_stat = render_path.stat()
    original_write = cleaner._write_json_atomic
    changed = False

    def write_then_change(path: Path, payload: dict) -> None:
        nonlocal changed
        original_write(path, payload)
        if payload.get("state") == "planned" and not changed:
            content = render_path.read_bytes()
            render_path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
            os.utime(
                render_path,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            changed = True

    monkeypatch.setattr(cleaner, "_write_json_atomic", write_then_change)

    summary = _render_only(cleaner, dry_run=False)

    assert render_path.exists()
    manifest = json.loads(Path(summary.manifest_path).read_text(encoding="utf-8"))
    assert manifest["items"][0]["result"] == "skipped_fingerprint_changed"
    assert manifest["items"][0]["fingerprint_sha256"]


def test_fb_temp_requires_no_crosspost_reference(cleaner_env):
    cleaner, data_dir, _ = cleaner_env
    temp_dir = data_dir / "fb_crossposter_temp"
    temp_dir.mkdir()
    referenced = temp_dir / "youtube-abc_7.mp4"
    orphan = temp_dir / "orphan_8.mp4"
    referenced.write_bytes(b"referenced")
    orphan.write_bytes(b"orphan")
    old = time.time() - 2 * 86400
    os.utime(referenced, (old, old))
    os.utime(orphan, (old, old))
    conn = sqlite3.connect(data_dir / "database.db")
    conn.execute(
        "INSERT INTO fb_crossposter_queue(id, youtube_id, status, updated_at) "
        "VALUES (7, 'youtube-abc', 'completed', ?)",
        (datetime.datetime.fromtimestamp(old, UTC).isoformat(),),
    )
    conn.commit()
    conn.close()

    summary = cleaner.run_full_cleanup(
        dry_run=True,
        clean_omni=False,
        clean_segments=False,
        clean_renders=False,
        clean_audio=False,
        clean_cache=False,
        clean_logs=True,
        clean_ghost_dbs=False,
        vacuum_db=False,
    )

    category = summary.categories["logs_and_temp"]
    assert category.files_planned == 1
    assert category.protected_reasons["fb_crosspost_reference"] == 1


def test_bytecode_cleanup_excludes_venv_and_quarantine(cleaner_env):
    cleaner, _, _ = cleaner_env
    source_cache = cleaner.project_root / "src" / "pkg" / "__pycache__" / "a.pyc"
    venv_cache = (
        cleaner.project_root
        / ".venv"
        / "Lib"
        / "site-packages"
        / "pkg"
        / "__pycache__"
        / "b.pyc"
    )
    quarantine_cache = (
        cleaner.quarantine_dir / "legacy" / "__pycache__" / "c.pyc"
    )
    for path in (source_cache, venv_cache, quarantine_cache):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bytecode")
        old = time.time() - 8 * 86400
        os.utime(path, (old, old))

    summary = cleaner.run_full_cleanup(
        dry_run=True,
        clean_omni=False,
        clean_segments=False,
        clean_renders=False,
        clean_audio=False,
        clean_cache=False,
        clean_logs=True,
        clean_ghost_dbs=False,
        vacuum_db=False,
    )

    assert summary.categories["logs_and_temp"].files_planned == 1
    assert venv_cache.exists()
    assert quarantine_cache.exists()


def test_path_outside_allowlist_is_never_planned(cleaner_env, tmp_path: Path):
    cleaner, data_dir, renders_dir = cleaner_env
    outside = tmp_path / "outside.mp4"
    _add_render(
        data_dir,
        renders_dir,
        artifact_id=30,
        video_id=130,
        age_days=40,
        path=outside,
    )

    summary = _render_only(cleaner, dry_run=True)

    assert summary.total_files_planned == 0
    assert outside.exists()
    assert "path_outside_allowlist" in summary.categories["expired_renders"].errors


def test_cleanup_scope_rejects_path_even_when_broad_allowlist_includes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    scope_root = tmp_path / "Tool"
    project_root = scope_root / "Nexus_Studio_Engine"
    cleaner = SystemCleaner(
        data_dir=project_root / "data",
        omnivoice_jobs_dir=scope_root / "omnivoice" / "data" / "jobs",
        project_root=project_root,
        cleanup_scope_root=scope_root,
    )
    outside_scope = tmp_path / "outside-scope.mp4"
    outside_scope.write_bytes(b"must remain")
    monkeypatch.setattr(
        SystemCleaner,
        "allowed_roots",
        property(lambda self: (tmp_path,)),
    )

    assert cleaner._is_allowed_path(outside_scope) is False
    assert outside_scope.exists()


def test_cleanup_configuration_outside_scope_is_blocked(tmp_path: Path):
    scope_root = tmp_path / "Tool"
    project_root = scope_root / "Nexus_Studio_Engine"
    data_dir = project_root / "data"
    jobs_dir = tmp_path / "outside" / "omnivoice" / "data" / "jobs"

    cleaner = SystemCleaner(
        data_dir=data_dir,
        omnivoice_jobs_dir=jobs_dir,
        project_root=project_root,
        cleanup_scope_root=scope_root,
    )

    summary = cleaner.run_full_cleanup(dry_run=False)

    assert summary.blocked is True
    assert summary.verification_errors == ["cleanup_scope_configuration_invalid"]
    assert not data_dir.exists()


def test_cleanup_status_fails_closed_on_probe_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        maintenance_guard,
        "_list_cleanup_database_blockers",
        lambda: ([], ["database_unavailable:OperationalError"]),
    )
    monkeypatch.setattr(
        maintenance_guard,
        "_list_cleanup_runtime_blockers",
        lambda: ([], []),
    )

    status = maintenance_guard.get_cleanup_status()

    assert status["safe_to_cleanup"] is False
    assert status["status_verified"] is False
    assert status["verification_errors"] == [
        "database_unavailable:OperationalError"
    ]


def test_cleanup_database_probe_ignores_stale_secondary_state(
    cleaner_env,
    monkeypatch: pytest.MonkeyPatch,
):
    cleaner, data_dir, _ = cleaner_env
    now = datetime.datetime.now(UTC)
    conn = sqlite3.connect(data_dir / "database.db")
    conn.execute(
        "INSERT INTO fb_crossposter_queue(id, status, updated_at) VALUES (1, 'downloading', ?)",
        ((now - datetime.timedelta(days=2)).isoformat(),),
    )
    conn.execute(
        "INSERT INTO audio_tasks(video_id, status, audio_url, updated_at) VALUES (1, 'processing', '', ?)",
        (now.isoformat(),),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(maintenance_guard.db, "DB_PATH", cleaner.db_path)

    blockers, errors = maintenance_guard._list_cleanup_database_blockers()

    assert errors == []
    assert any(item["type"] == "audio_task" for item in blockers)
    assert not any(item["type"] == "fb_crosspost" for item in blockers)


def test_open_project_gpm_profile_is_a_cleanup_blocker(
    cleaner_env,
    monkeypatch: pytest.MonkeyPatch,
):
    cleaner, data_dir, _ = cleaner_env
    conn = sqlite3.connect(data_dir / "database.db")
    conn.execute(
        "INSERT INTO youtube_channels(id, gpm_profile_id) VALUES (1, 'profile-abc')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(maintenance_guard.db, "DB_PATH", cleaner.db_path)
    monkeypatch.setattr(maintenance_guard.os, "name", "nt")
    monkeypatch.setattr(
        maintenance_guard.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    '[{"ProcessId":321,"CommandLine":'
                    '"chrome.exe --user-data-dir=profile-abc"}]'
                ),
            },
        )(),
    )

    blockers, errors = maintenance_guard._list_open_project_gpm_profiles()

    assert errors == []
    assert blockers == [
        {
            "type": "gpm_profile",
            "id": "profile-abc",
            "status": "open",
            "job_type": "gpm_browser",
            "process_id": 321,
        }
    ]


def test_gpm_profile_probe_fails_closed(cleaner_env, monkeypatch: pytest.MonkeyPatch):
    cleaner, data_dir, _ = cleaner_env
    conn = sqlite3.connect(data_dir / "database.db")
    conn.execute(
        "INSERT INTO youtube_channels(id, gpm_profile_id) VALUES (1, 'profile-abc')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(maintenance_guard.db, "DB_PATH", cleaner.db_path)
    monkeypatch.setattr(maintenance_guard.os, "name", "nt")
    monkeypatch.setattr(
        maintenance_guard.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 1, "stdout": ""}
        )(),
    )

    blockers, errors = maintenance_guard._list_open_project_gpm_profiles()

    assert blockers == []
    assert errors == ["gpm_profile_process_probe_failed"]


def test_runtime_probe_fails_closed_for_playwright_and_muse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from nexus_engine.services import (
        chatgpt_browser_service,
        chatgpt_runtime,
        google_flow_browser_service,
        muse_studio_service,
    )

    invalid_state = tmp_path / "chatgpt-browser.json"
    invalid_state.write_text("not-json", encoding="utf-8")
    missing_state = tmp_path / "google-flow-browser.json"
    monkeypatch.setattr(chatgpt_runtime, "is_busy", lambda: False)
    monkeypatch.setattr(chatgpt_browser_service, "SERVICE_STATE_PATH", invalid_state)
    monkeypatch.setattr(google_flow_browser_service, "SERVICE_STATE_PATH", missing_state)
    monkeypatch.setattr(
        google_flow_browser_service,
        "get_browser_service_status",
        lambda: {"process_alive": False, "connected": False},
    )
    monkeypatch.setattr(
        muse_studio_service,
        "status",
        lambda **kwargs: {
            "running": True,
            "owned": True,
            "api_ready": False,
            "active_media_tasks": 0,
            "browser_running": False,
        },
    )
    monkeypatch.setattr(maintenance_guard, "list_gpm_restart_blockers", lambda: [])
    monkeypatch.setattr(
        maintenance_guard, "_list_open_project_gpm_profiles", lambda: ([], [])
    )
    monkeypatch.setattr(
        maintenance_guard, "_list_omnivoice_cleanup_blockers", lambda: ([], [])
    )

    blockers, errors = maintenance_guard._list_cleanup_runtime_blockers()

    assert blockers == []
    assert any(error.startswith("chatgpt_browser_service_probe_failed") for error in errors)
    assert "muse_runtime_probe_unverified" in errors


def test_execute_is_blocked_without_writing_manifest(
    cleaner_env,
    monkeypatch: pytest.MonkeyPatch,
):
    cleaner, data_dir, renders_dir = cleaner_env
    render_path = _add_render(
        data_dir, renders_dir, artifact_id=40, video_id=140, age_days=40
    )
    monkeypatch.setattr(
        maintenance_guard,
        "get_cleanup_status",
        lambda: {
            "safe_to_cleanup": False,
            "blocking_jobs": [{"type": "system_job", "id": "busy", "status": "running"}],
            "verification_errors": [],
        },
    )

    summary = _render_only(cleaner, dry_run=False)

    assert summary.blocked is True
    assert render_path.exists()
    assert summary.manifest_id == ""
    assert not cleaner.manifests_dir.exists()


def test_execute_is_blocked_by_process_lock_without_manifest(
    cleaner_env,
    monkeypatch: pytest.MonkeyPatch,
):
    cleaner, data_dir, renders_dir = cleaner_env
    render_path = _add_render(
        data_dir, renders_dir, artifact_id=41, video_id=141, age_days=40
    )
    monkeypatch.setattr(cleaner, "_acquire_process_lock", lambda: None)

    summary = _render_only(cleaner, dry_run=False)

    assert summary.blocked is True
    assert summary.verification_errors == ["cleanup_already_running"]
    assert render_path.exists()
    assert not cleaner.manifests_dir.exists()


def test_cleanup_api_returns_sanitized_409(monkeypatch: pytest.MonkeyPatch):
    from nexus_engine import main

    monkeypatch.setattr(
        main.maintenance_guard,
        "get_cleanup_status",
        lambda: {
            "safe_to_cleanup": False,
            "reasons": ["1 active task"],
            "blocking_jobs": [
                {
                    "type": "system_job",
                    "id": "job-1",
                    "status": "running",
                    "payload": {"token": "must-not-leak"},
                }
            ],
            "verification_errors": [],
        },
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(main.run_system_cleanup_endpoint(BackgroundTasks()))

    assert captured.value.status_code == 409
    blocker = captured.value.detail["blockers"][0]
    assert blocker == {"type": "system_job", "id": "job-1", "status": "running"}
    assert "must-not-leak" not in json.dumps(captured.value.detail)


def test_cleanup_api_returns_preallocated_manifest_id(monkeypatch: pytest.MonkeyPatch):
    from nexus_engine import main

    monkeypatch.setattr(
        main.maintenance_guard,
        "get_cleanup_status",
        _safe_cleanup_status,
    )
    background_tasks = BackgroundTasks()

    response = asyncio.run(main.run_system_cleanup_endpoint(background_tasks))

    assert response["status"] == "ok"
    assert response["manifest_id"]
    assert len(background_tasks.tasks) == 1
    assert main.SYSTEM_CLEANUP_INTERVAL_SECONDS == 24 * 60 * 60
