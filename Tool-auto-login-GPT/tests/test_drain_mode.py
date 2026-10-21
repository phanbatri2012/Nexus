from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from auto_yt.services import api_security
from auto_yt.services import database as db
from auto_yt.services import drain_mode
from auto_yt.services import maintenance_guard


@pytest.fixture(autouse=True)
def clean_drain_mode():
    drain_mode.set_drain_mode(False, reason="test_cleanup_before")
    yield
    drain_mode.set_drain_mode(False, reason="test_cleanup_after")


def test_drain_mode_state_transitions():
    assert drain_mode.is_drain_mode_active() is False
    status = drain_mode.get_drain_status()
    assert status["enabled"] is False

    updated = drain_mode.set_drain_mode(True, reason="Unit test enable")
    assert updated["enabled"] is True
    assert drain_mode.is_drain_mode_active() is True

    reset_status = drain_mode.reset_drain_mode()
    assert reset_status["enabled"] is False
    assert drain_mode.is_drain_mode_active() is False


def test_database_claim_respects_drain_mode():
    db.create_system_job(
        job_id="test-drain-job-1",
        job_type="video_render",
        title="Test Drain Job",
        payload={"video_id": 99999},
        status="queued",
    )
    try:
        # 1. When drain mode is active -> claim returns None
        drain_mode.set_drain_mode(True)
        assert db.claim_next_system_job("video_render") is None
        assert db.has_claimable_system_jobs("video_render") is False
        assert db.get_next_system_job_retry_delay("video_render") is None

        # 2. When drain mode is disabled -> job can be claimed
        drain_mode.set_drain_mode(False)
        assert db.has_claimable_system_jobs("video_render") is True
        claimed = db.claim_next_system_job("video_render")
        assert claimed is not None
        assert claimed["id"] == "test-drain-job-1"
        assert claimed["status"] == "running"
    finally:
        db.update_system_job("test-drain-job-1", status="done")
        db.delete_system_job("test-drain-job-1")


def test_maintenance_guard_ignores_queued_when_drain_active():
    db.create_system_job(
        job_id="test-drain-queued-guard",
        job_type="video_render",
        title="Test Drain Queued Job Guard",
        payload={"video_id": 99998},
        status="queued",
    )
    try:
        # In normal mode, queued job is a blocker
        drain_mode.set_drain_mode(False)
        blockers = maintenance_guard.list_system_job_blockers()
        assert any(b["id"] == "test-drain-queued-guard" for b in blockers)

        # In drain mode, queued job is NOT a blocker
        drain_mode.set_drain_mode(True)
        blockers_drain = maintenance_guard.list_system_job_blockers()
        assert not any(b["id"] == "test-drain-queued-guard" for b in blockers_drain)
    finally:
        db.delete_system_job("test-drain-queued-guard")


def test_drain_mode_api_endpoints():
    from auto_yt.main import app

    client = TestClient(app, base_url="http://127.0.0.1:8080")
    
    # Establish authenticated local API session
    session_res = client.get(
        api_security.SESSION_PATH,
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert session_res.status_code == 200
    csrf_token = session_res.json()["csrf_token"]
    auth_headers = {
        "Origin": "http://127.0.0.1:5173",
        api_security.CSRF_HEADER_NAME: csrf_token,
    }

    # 1. GET /api/system/drain-mode
    get_res = client.get("/api/system/drain-mode", headers=auth_headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert "enabled" in data
    assert "running_jobs_count" in data
    assert "safe_to_restart" in data

    # 2. POST /api/system/drain-mode -> enable
    post_res = client.post(
        "/api/system/drain-mode",
        headers=auth_headers,
        json={"enabled": True, "reason": "Test API"},
    )
    assert post_res.status_code == 200
    assert post_res.json()["enabled"] is True
    assert drain_mode.is_drain_mode_active() is True

    # 3. GET /api/jobs includes drain_mode
    jobs_res = client.get("/api/jobs", headers=auth_headers)
    assert jobs_res.status_code == 200
    assert "drain_mode" in jobs_res.json()
    assert jobs_res.json()["drain_mode"]["enabled"] is True

    # 4. POST /api/system/drain-mode -> disable
    disable_res = client.post(
        "/api/system/drain-mode",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert disable_res.status_code == 200
    assert disable_res.json()["enabled"] is False
    assert drain_mode.is_drain_mode_active() is False
