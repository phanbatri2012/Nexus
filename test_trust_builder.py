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

from nexus_engine.services import database as db, api_security
from nexus_engine.services.trust_builder_actions import (
    ACTION_ALREADY_DONE,
    ACTION_BLOCKED,
    ACTION_PERFORMED,
    ACTION_UNCERTAIN,
    action_audit_channel_branding,
    action_browse_and_pick_video,
    action_browse_and_read_comments,
    action_refocus_video_player,
    action_search_and_pick_video,
    action_watch_video,
    action_like_video,
    action_comment_video,
    action_subscribe_channel,
    dismiss_common_popups,
    handle_ad_skipping,
    is_sponsored_candidate,
)
from nexus_engine.services.trust_builder_service import (
    calculate_trust_score,
    engagement_requirements_met,
    generate_contextual_comment,
    validate_plan_browser_configuration,
)
from nexus_engine.services.trust_builder_router import (
    TrustPlanCreateRequest,
    TrustPlanUpdateRequest,
    _public_plan,
)
from nexus_engine.services import trust_builder_scheduler
from nexus_engine.main import app

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


def test_enabled_engagement_must_have_verified_success_before_completion():
    plan = {"daily_comment_target": 3, "daily_subscribe_target": 1}
    assert engagement_requirements_met(plan, {"comment_count": 0, "subscribe_count": 0}) is False
    assert engagement_requirements_met(plan, {"comment_count": 1, "subscribe_count": 0}) is False
    assert engagement_requirements_met(plan, {"comment_count": 1, "subscribe_count": 1}) is True
    assert engagement_requirements_met(
        {"daily_comment_target": 0, "daily_subscribe_target": 0},
        {"comment_count": 0, "subscribe_count": 0},
    ) is True


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


def test_is_sponsored_candidate_detection():
    # Vietnamese 'Được tài trợ'
    assert is_sponsored_candidate({
        "url": "https://www.youtube.com/watch?v=ad1",
        "title": "Lenovo Ideapad Slim 3",
        "channel": "Được tài trợ · CellphoneS",
    }) is True

    # Vietnamese 'Quảng cáo'
    assert is_sponsored_candidate({
        "url": "https://www.youtube.com/watch?v=ad2",
        "title": "Khoá học lập trình",
        "channel": "Trung tâm ABC (Quảng cáo)",
    }) is True

    # English 'Sponsored'
    assert is_sponsored_candidate({
        "url": "https://www.youtube.com/watch?v=ad3",
        "title": "Best VPN 2026",
        "channel": "Sponsored · Nord Brand",
    }) is True

    # Title prefix [Ad]
    assert is_sponsored_candidate({
        "url": "https://www.youtube.com/watch?v=ad4",
        "title": "[Ad] Smart watch discount",
        "channel": "Tech Store",
    }) is True

    # Ad redirect / tracking URL
    assert is_sponsored_candidate({
        "url": "https://www.googleadservices.com/pagead/aclk?sa=L&ai=mock",
        "title": "Sale off",
        "channel": "Shop",
    }) is True

    # Explicit flag
    assert is_sponsored_candidate({
        "url": "https://www.youtube.com/watch?v=ad5",
        "title": "Promo video",
        "channel": "Brand",
        "is_ad": True,
    }) is True

    # Organic video candidate must NOT be flagged
    assert is_sponsored_candidate({
        "url": "https://www.youtube.com/watch?v=organic123",
        "title": "Vợ Mất đột ngột Trước Ngày Chồng Về Phép",
        "channel": "Trò Chuyện Với Sầm",
    }) is False


def test_action_search_and_pick_video_skips_sponsored_ad_at_top():
    async def _run():
        mock_page = AsyncMock()
        mock_search_elem = AsyncMock()
        mock_page.wait_for_selector.return_value = mock_search_elem
        mock_page.wait_for_function = AsyncMock()

        # The first item is a sponsored video at top of page, second is organic
        mock_page.evaluate.return_value = [
            {
                "url": "https://www.youtube.com/watch?v=ad_first",
                "title": "Lenovo Ideapad Slim 3 | Trợ Giá Đến 4 Triệu",
                "channel": "Được tài trợ · CellphoneS",
            },
            {
                "url": "https://www.youtube.com/watch?v=organic_win",
                "title": "Vợ Mất đột ngột Trước Ngày Chồng Về Phép",
                "channel": "Trò Chuyện Với Sầm",
            },
        ]

        selected = await action_search_and_pick_video(
            mock_page,
            keyword="truyện tâm sự",
            target_channel="",
            timeout_seconds=5.0,
        )

        # Must skip the sponsored ad and pick the organic video
        assert selected["url"] == "https://www.youtube.com/watch?v=organic_win"
        assert selected["channel"] == "Trò Chuyện Với Sầm"

    asyncio.run(_run())


def test_action_browse_and_pick_video_skips_sponsored_ad_at_top():
    async def _run():
        mock_page = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()

        # The first item is a sponsored ad card on home page, second is organic
        mock_page.evaluate.return_value = [
            {
                "url": "https://www.youtube.com/watch?v=ad_feed",
                "title": "Quảng cáo sản phẩm mới",
                "channel": "Được tài trợ · Brand X",
            },
            {
                "url": "https://www.youtube.com/watch?v=organic_feed",
                "title": "Trung Tá Đạp Cửa Bếp Cứu Mẹ Ruột 70T",
                "channel": "Đêm Nay Sầm Kể",
            },
        ]

        selected = await action_browse_and_pick_video(
            mock_page,
            target_channel="",
            timeout_seconds=5.0,
        )

        assert selected["url"] == "https://www.youtube.com/watch?v=organic_feed"
        assert selected["channel"] == "Đêm Nay Sầm Kể"

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

        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
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
        assert subscribed["status"] == ACTION_PERFORMED
        assert subscribed["reason_code"] == "subscribe_confirmed"
        assert mock_btn.click.called
        confirmation_script = mock_page.wait_for_function.await_args.args[0]
        assert "signature !==" not in confirmation_script

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
        subscribe_result = await action_subscribe_channel(subscribe_page)
        assert subscribe_result["status"] == ACTION_ALREADY_DONE
        subscribe_page.query_selector.assert_not_called()

    asyncio.run(_run())


def test_action_comment_video_records_confirmed_result():
    async def _run():
        mock_page = AsyncMock()
        mock_element = AsyncMock()
        mock_page.query_selector.return_value = mock_element
        mock_page.evaluate.side_effect = [
            {"found": True, "blocked": False},
            {"found": True},
            {"found": True, "enabled": True, "text_ready": True},
        ]
        mock_page.wait_for_function = AsyncMock()

        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            result = await action_comment_video(mock_page, "Bình luận kiểm thử")

        assert result["status"] == ACTION_PERFORMED
        assert result["reason_code"] == "comment_confirmed"
        assert mock_element.click.await_count >= 3

    asyncio.run(_run())


def test_action_comment_video_reports_disabled_comments():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {"found": False, "blocked": True}
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            result = await action_comment_video(mock_page, "Bình luận kiểm thử")
        assert result["status"] == ACTION_BLOCKED
        assert result["reason_code"] == "comments_disabled"

    asyncio.run(_run())


def test_action_subscribe_channel_does_not_repeat_uncertain_click():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {
            "found": True,
            "subscribed": False,
            "selector": '[data-trust-builder-action="subscribe"]',
            "signature": "Subscribe|Subscribe|false",
        }
        mock_page.query_selector.return_value = AsyncMock()
        mock_page.wait_for_function.side_effect = TimeoutError("not confirmed")

        result = await action_subscribe_channel(mock_page)
        assert result["status"] == ACTION_UNCERTAIN
        assert result["reason_code"] == "subscribe_confirmation_timeout"
        assert mock_page.query_selector.return_value.click.await_count == 1

    asyncio.run(_run())


def test_action_browse_and_pick_video_filters_seen_and_unsafe_candidates():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = [
            {"url": "https://www.youtube.com/watch?v=seen1", "title": "Seen", "channel": "@safe"},
            {"url": "https://www.youtube.com/watch?v=unsafe1", "title": "Unsafe", "channel": "@unsafe"},
            {"url": "https://www.youtube.com/watch?v=fresh1", "title": "Fresh safe video", "channel": "@safe"},
        ]

        def safety_result(*, title, **_kwargs):
            return (title != "Unsafe", "blocked" if title == "Unsafe" else "")

        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()), patch(
            "nexus_engine.services.trust_builder_actions.safety.is_safe_for_interaction",
            side_effect=safety_result,
        ):
            result = await action_browse_and_pick_video(
                mock_page,
                target_channel="@safe",
                excluded_video_ids={"seen1"},
            )

        assert result["url"].endswith("fresh1")
        mock_page.goto.assert_awaited_once()

    asyncio.run(_run())


def test_action_browse_and_read_comments_dwell_and_extraction():
    """Verify that action_browse_and_read_comments performs natural scrolls, dwells, and extracts comments."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = ["Bình luận rất hay!", "Cảm ơn tác giả."]
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            res = await action_browse_and_read_comments(
                mock_page,
                min_dwell_seconds=15.0,
                max_dwell_seconds=20.0,
            )
        assert res["dwell_seconds"] >= 15.0
        assert res["comments"] == ["Bình luận rất hay!", "Cảm ơn tác giả."]
        assert mock_page.mouse.wheel.await_count >= 4

    asyncio.run(_run())


def test_action_browse_and_read_comments_early_cancel():
    """Verify that action_browse_and_read_comments exits early when cancel_check is triggered."""
    async def _run():
        mock_page = AsyncMock()
        cancel_flag = False

        def _check():
            nonlocal cancel_flag
            return cancel_flag

        async def _fake_sleep(duration):
            nonlocal cancel_flag
            cancel_flag = True

        mock_page.evaluate.return_value = ["Comment 1"]
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", side_effect=_fake_sleep):
            res = await action_browse_and_read_comments(
                mock_page,
                min_dwell_seconds=30.0,
                max_dwell_seconds=40.0,
                cancel_check=_check,
            )
        assert res["dwell_seconds"] < 30.0

    asyncio.run(_run())


def test_action_refocus_video_player_success():
    """Verify that action_refocus_video_player calculates player bounds and smoothly moves mouse."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.side_effect = [
            {"left": 120, "top": 50, "width": 854, "height": 480, "scrollY": 0},
            False,
        ]
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            dwell = await action_refocus_video_player(mock_page)

        assert dwell > 0.0
        assert mock_page.mouse.move.await_count >= 1
        first_call_args = mock_page.mouse.move.await_args_list[0]
        x, y = first_call_args.args[0], first_call_args.args[1]
        assert 120 <= x <= 120 + 854
        assert 50 <= y <= 50 + 480

    asyncio.run(_run())


def test_action_refocus_video_player_missing_player():
    """Verify that action_refocus_video_player handles missing player gracefully without raising error."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = None
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            dwell = await action_refocus_video_player(mock_page)

        assert dwell == 0.0
        assert mock_page.mouse.move.await_count == 0

    asyncio.run(_run())


def test_action_refocus_video_player_resumes_paused_playback():
    """Verify that action_refocus_video_player triggers play if video was paused."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.side_effect = [
            {"left": 100, "top": 40, "width": 640, "height": 360, "scrollY": 0},
            True,
            None,
        ]
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
            dwell = await action_refocus_video_player(mock_page)

        assert dwell > 0.0
        assert mock_page.evaluate.await_count >= 3

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
        with patch("nexus_engine.services.trust_builder_actions.asyncio.sleep", new=AsyncMock()):
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
            "nexus_engine.services.trust_builder_actions.asyncio.sleep",
            new=AsyncMock(),
        ), patch("nexus_engine.services.trust_builder_actions.random.uniform", return_value=0.25):
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
        "nexus_engine.services.trust_builder_service.parse_profile_target",
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
        "nexus_engine.services.trust_builder_service.parse_profile_target",
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
        "nexus_engine.services.trust_builder_service.parse_profile_target",
        return_value={"type": "gpm"},
    ), patch(
        "nexus_engine.services.trust_builder_service.db.list_youtube_channels",
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
        CREATE TABLE trust_guided_sessions (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL);
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
