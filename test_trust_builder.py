"""Unit tests for Trust Builder actions, scoring heuristics, and API endpoints."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import asyncio
import datetime
import sqlite3
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from fastapi.testclient import TestClient

from auto_yt.services import database as db, api_security
from auto_yt.services.trust_builder_actions import (
    ACTION_ALREADY_DONE,
    ACTION_PERFORMED,
    action_audit_channel_branding,
    action_search_and_pick_video,
    action_watch_video,
    action_like_video,
    action_comment_video,
    action_subscribe_channel,
    dismiss_common_popups,
    handle_ad_skipping,
)
from auto_yt.services.trust_builder_service import (
    calculate_trust_score,
    generate_contextual_comment,
    validate_plan_browser_configuration,
)
from auto_yt.services.trust_builder_router import (
    TrustPlanCreateRequest,
    TrustPlanUpdateRequest,
    _public_plan,
)
from auto_yt.services import trust_builder_scheduler
from auto_yt.main import app

client = TestClient(app)

# Authenticate test client for loopback API
session_res = client.get("/api/security/session", headers={"Origin": "http://127.0.0.1:5173"})
if session_res.status_code == 200:
    csrf_token = session_res.json()["csrf_token"]
    client.headers.update({
        "Origin": "http://127.0.0.1:5173",
        api_security.CSRF_HEADER_NAME: csrf_token,
    })


def test_calculate_trust_score():
    plan = {
        "created_at": db.utc_now(),
        "branding_checklist": {
            "avatar": True,
            "banner": True,
            "about": True,
            "handle": True,
            "contact_email": True,
            "country": True,
            "feature_level": "intermediate",
        }
    }
    stats = {
        "watch_count": 25,
        "search_count": 15,
        "like_count": 10,
        "comment_count": 4,
        "subscribe_count": 4,
        "total_watch_seconds": 2500,
        "active_days": 5,
    }
    score = calculate_trust_score(plan, stats)
    assert 50 <= score <= 100


def test_generate_contextual_comment():
    vn_comment = generate_contextual_comment("Cách xây dựng kênh YouTube từ con số 0")
    assert isinstance(vn_comment, str)
    assert len(vn_comment) > 10

    en_comment = generate_contextual_comment("How to grow your YouTube channel in 2026")
    assert isinstance(en_comment, str)
    assert len(en_comment) > 10


def test_action_search_and_pick_video_success():
    async def _run():
        mock_page = AsyncMock()
        mock_search_elem = AsyncMock()
        mock_page.wait_for_selector.return_value = mock_search_elem
        mock_page.wait_for_function = AsyncMock()
        
        # Mock candidate extraction
        mock_page.evaluate.return_value = [
            {
                "url": "https://www.youtube.com/watch?v=mock123",
                "title": "Video Đối Thủ Hay Nhất",
                "channel": "@samkechuyen01",
            },
            {
                "url": "https://www.youtube.com/watch?v=mock456",
                "title": "Video Đối Thủ Số 2",
                "channel": "Kênh Tổng Hợp",
            }
        ]

        selected = await action_search_and_pick_video(
            mock_page,
            keyword="sâm kể chuyện",
            target_channel="@Samkechuyen01",
            timeout_seconds=5.0,
        )

        assert selected["url"] == "https://www.youtube.com/watch?v=mock123"
        assert selected["channel"] == "@samkechuyen01"
        assert mock_page.wait_for_function.called or mock_page.wait_for_selector.called

    asyncio.run(_run())


def test_action_search_does_not_fallback_when_target_channel_is_missing():
    async def _run():
        mock_page = AsyncMock()
        mock_page.wait_for_selector.return_value = AsyncMock()
        mock_page.wait_for_function = AsyncMock()
        mock_page.evaluate.return_value = [
            {
                "url": "https://www.youtube.com/watch?v=other123",
                "title": "Unrelated result",
                "channel": "@other-channel",
            }
        ]

        with patch("auto_yt.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError, match="kênh mục tiêu"):
                await action_search_and_pick_video(
                    mock_page,
                    keyword="channel topic",
                    target_channel="@required-channel",
                    timeout_seconds=5.0,
                )

    asyncio.run(_run())


def test_action_like_video():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {"found": True, "pressed": False, "selector": "like-button-view-model button"}
        mock_btn = AsyncMock()
        mock_page.query_selector.return_value = mock_btn

        liked = await action_like_video(mock_page)
        assert liked == ACTION_PERFORMED
        assert mock_btn.click.called

    asyncio.run(_run())


def test_action_subscribe_channel():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {"found": True, "subscribed": False, "selector": "ytd-watch-metadata #subscribe-button button"}
        mock_btn = AsyncMock()
        mock_page.query_selector.return_value = mock_btn

        subscribed = await action_subscribe_channel(mock_page)
        assert subscribed == ACTION_PERFORMED
        assert mock_btn.click.called

    asyncio.run(_run())


def test_already_done_engagement_is_not_performed():
    async def _run():
        like_page = AsyncMock()
        like_page.evaluate.return_value = {
            "found": True,
            "pressed": True,
            "selector": "like-button-view-model button",
        }
        assert await action_like_video(like_page) == ACTION_ALREADY_DONE
        like_page.query_selector.assert_not_called()

        subscribe_page = AsyncMock()
        subscribe_page.evaluate.return_value = {
            "found": True,
            "subscribed": True,
            "selector": "ytd-watch-metadata #subscribe-button button",
        }
        assert await action_subscribe_channel(subscribe_page) == ACTION_ALREADY_DONE
        subscribe_page.query_selector.assert_not_called()

    asyncio.run(_run())


def test_action_watch_video_duration_short_video():
    """Short video (< min_watch_seconds, e.g. 240s < 600s) must be watched 100%."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.youtube.com/watch?v=short123"
        mock_page.query_selector.return_value = None
        mock_page.evaluate.side_effect = [
            None,
            {"duration": 4.0, "currentTime": 0.0, "paused": False, "ended": False, "readyState": 4, "adShowing": False, "videoId": "short123"},
            {"duration": 4.0, "currentTime": 4.0, "paused": False, "ended": True, "readyState": 4, "adShowing": False, "videoId": "short123"},
        ]
        with patch("auto_yt.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            res = await action_watch_video(
                mock_page,
                video_url="https://www.youtube.com/watch?v=short123",
                min_watch_seconds=5.0,
                max_watch_seconds=10.0,
            )
        assert res["total_duration"] == 4.0
        assert res["retention_percentage"] == 100.0

    asyncio.run(_run())


def test_action_watch_video_duration_long_video():
    """Long video (e.g. 1800s > 600s) must target at least 600s."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.youtube.com/watch?v=long123"
        mock_page.query_selector.return_value = None
        mock_page.evaluate.side_effect = [
            None,
            {"duration": 20.0, "currentTime": 0.0, "paused": False, "ended": False, "readyState": 4, "adShowing": False, "videoId": "long123"},
            {"duration": 20.0, "currentTime": 5.0, "paused": False, "ended": False, "readyState": 4, "adShowing": False, "videoId": "long123"},
        ]
        with patch(
            "auto_yt.services.trust_builder_actions.asyncio.sleep",
            new=AsyncMock(),
        ), patch("auto_yt.services.trust_builder_actions.random.uniform", return_value=0.25):
            res = await action_watch_video(
                mock_page,
                video_url="https://www.youtube.com/watch?v=long123",
                min_pct=20.0,
                max_pct=30.0,
                min_watch_seconds=5.0,
                max_watch_seconds=5.0,
            )
        assert res["total_duration"] == 20.0
        assert res["watched_seconds"] == 5.0

    asyncio.run(_run())


def test_db_trust_plan_min_watch_minutes():
    """Verify that database stores and returns min_watch_minutes correctly."""
    db.init_db()
    # Check that dummy plan decode has min_watch_minutes default
    decoded = db._decode_trust_plan({"min_watch_minutes": None})
    assert decoded["min_watch_minutes"] == 10

    decoded_custom = db._decode_trust_plan({"min_watch_minutes": 15})
    assert decoded_custom["min_watch_minutes"] == 15


def test_db_trust_plan_preserves_zero_engagement_targets():
    decoded = db._decode_trust_plan({
        "daily_like_target": 0,
        "daily_comment_target": 0,
        "daily_subscribe_target": 0,
    })
    assert decoded["daily_like_target"] == 0
    assert decoded["daily_comment_target"] == 0
    assert decoded["daily_subscribe_target"] == 0


def test_trust_plan_request_validation_and_safe_defaults():
    request = TrustPlanCreateRequest(channel_db_id=1)
    assert request.daily_comment_target == 0
    assert request.daily_subscribe_target == 0

    with pytest.raises(ValueError):
        TrustPlanUpdateRequest(daily_comment_target=6)
    with pytest.raises(ValueError):
        TrustPlanUpdateRequest(min_watch_minutes=0)


def test_validate_plan_browser_configuration_rejects_local_and_direct():
    base_plan = {"channel_db_id": 1, "gpm_profile_id": "local_coccoc_Default"}
    with pytest.raises(ValueError, match="Profile GPM"):
        validate_plan_browser_configuration(base_plan)

    direct_plan = {
        "channel_db_id": 1,
        "gpm_profile_id": "gpm-profile",
        "gpm_proxy_info": "Direct",
    }
    with patch(
        "auto_yt.services.trust_builder_service.parse_profile_target",
        return_value={"type": "gpm", "proxy_info": "Direct"},
    ):
        with pytest.raises(ValueError, match="proxy"):
            validate_plan_browser_configuration(direct_plan)

    invalid_proxy_plan = {
        "channel_db_id": 1,
        "gpm_profile_id": "gpm-profile",
        "gpm_proxy_info": "not-a-proxy",
    }
    with patch(
        "auto_yt.services.trust_builder_service.parse_profile_target",
        return_value={"type": "gpm", "proxy_info": "not-a-proxy"},
    ):
        with pytest.raises(ValueError, match="proxy"):
            validate_plan_browser_configuration(invalid_proxy_plan)


def test_validate_plan_browser_configuration_rejects_shared_profile():
    plan = {
        "channel_db_id": 1,
        "gpm_profile_id": "gpm-profile",
        "gpm_proxy_info": "127.0.0.1:9000:user:password",
    }
    with patch(
        "auto_yt.services.trust_builder_service.parse_profile_target",
        return_value={"type": "gpm"},
    ), patch(
        "auto_yt.services.trust_builder_service.db.list_youtube_channels",
        return_value=[{"id": 2, "gpm_profile_id": "gpm-profile"}],
    ):
        with pytest.raises(ValueError, match="kênh khác"):
            validate_plan_browser_configuration(plan)


def test_public_plan_hides_proxy_credentials():
    plan = _public_plan(
        {
            "id": 7,
            "gpm_proxy_info": "127.0.0.1:9000:proxy-user:proxy-password",
        }
    )

    assert "gpm_proxy_info" not in plan
    assert plan["gpm_proxy_configured"] is True


def test_scheduler_uses_channel_timezone_and_next_run():
    plan = {
        "publication_timezone": "Asia/Bangkok",
        "next_run_at": "2026-10-07T02:00:00+00:00",
    }
    before_start = datetime.datetime(2026, 10, 7, 0, 30, tzinfo=datetime.timezone.utc)
    active_time = datetime.datetime(2026, 10, 7, 3, 0, tzinfo=datetime.timezone.utc)
    assert trust_builder_scheduler._is_plan_due(plan, before_start) is False
    assert trust_builder_scheduler._is_plan_due(plan, active_time) is True


def test_delete_trust_plan_removes_activity_logs_atomically(tmp_path, monkeypatch):
    database_path = tmp_path / "trust.db"
    conn = sqlite3.connect(database_path)
    conn.executescript(
        """
        CREATE TABLE channel_trust_plans (id INTEGER PRIMARY KEY);
        CREATE TABLE trust_activity_log (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL);
        INSERT INTO channel_trust_plans(id) VALUES (7);
        INSERT INTO trust_activity_log(id, plan_id) VALUES (1, 7), (2, 7);
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", database_path)

    assert db.delete_channel_trust_plan(7) is True
    verify_conn = sqlite3.connect(database_path)
    assert verify_conn.execute("SELECT COUNT(*) FROM trust_activity_log").fetchone()[0] == 0
    verify_conn.close()


def test_action_audit_channel_branding_studio():
    """Verify that action_audit_channel_branding correctly evaluates branding elements via Studio."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://studio.youtube.com/channel/UC12345/editing/images"
        mock_page.content = AsyncMock(return_value="<html>YouTube Studio</html>")
        
        # 1st evaluate: branding (avatar, banner)
        # 2nd evaluate: basic info (handle, desc, email)
        mock_page.evaluate.side_effect = [
            {"hasAvatar": True, "hasBanner": True},
            {"hasHandle": True, "hasDesc": True, "hasEmail": True},
        ]

        res = await action_audit_channel_branding(mock_page, channel_id="UC12345", timeout_seconds=5.0)
        assert res["avatar"] is True
        assert res["banner"] is True
        assert res["handle"] is True
        assert res["about"] is True
        assert res["contact_email"] is True

    asyncio.run(_run())


def test_action_audit_channel_branding_fallback():
    """Verify fallback to public channel page when Studio is inaccessible."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://studio.youtube.com/channel/UC12345/editing/images"
        # Return permission error in content
        mock_page.content = AsyncMock(return_value="<html>Rất tiếc, bạn không có quyền xem trang này</html>")
        
        # Public channel evaluate
        mock_page.evaluate.return_value = {
            "hasAvatar": True,
            "hasBanner": True,
            "hasHandle": True,
            "hasDesc": True,
            "hasEmail": False,
        }

        res = await action_audit_channel_branding(mock_page, channel_id="UC12345", timeout_seconds=5.0)
        assert res["avatar"] is True
        assert res["banner"] is True
        assert res["handle"] is True
        assert res["about"] is True

    asyncio.run(_run())
