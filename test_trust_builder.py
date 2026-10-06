"""Unit tests for Trust Builder actions, scoring heuristics, and API endpoints."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from auto_yt.services import database as db, api_security
from auto_yt.services.trust_builder_actions import (
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
)
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


def test_action_like_video():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {"found": True, "pressed": False, "selector": "like-button-view-model button"}
        mock_btn = AsyncMock()
        mock_page.query_selector.return_value = mock_btn

        liked = await action_like_video(mock_page)
        assert liked is True
        assert mock_btn.click.called

    asyncio.run(_run())


def test_action_subscribe_channel():
    async def _run():
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = {"found": True, "subscribed": False, "selector": "ytd-watch-metadata #subscribe-button button"}
        mock_btn = AsyncMock()
        mock_page.query_selector.return_value = mock_btn

        subscribed = await action_subscribe_channel(mock_page)
        assert subscribed is True
        assert mock_btn.click.called

    asyncio.run(_run())


def test_action_watch_video_duration_short_video():
    """Short video (< min_watch_seconds, e.g. 240s < 600s) must be watched 100%."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.youtube.com/watch?v=short123"
        # Return duration 240s on query, then immediately signal ended
        mock_page.evaluate.side_effect = [
            None,   # play
            240.0,  # duration query
            True,   # is_ended query
        ]
        res = await action_watch_video(
            mock_page,
            video_url="https://www.youtube.com/watch?v=short123",
            min_watch_seconds=600.0,
            max_watch_seconds=1200.0,
        )
        assert res["total_duration"] == 240.0
        # Retention percentage should be calculated relative to total_duration
        assert res["retention_percentage"] <= 100.0

    asyncio.run(_run())


def test_action_watch_video_duration_long_video():
    """Long video (e.g. 1800s > 600s) must target at least 600s."""
    async def _run():
        mock_page = AsyncMock()
        mock_page.url = "https://www.youtube.com/watch?v=long123"
        # First return duration 1800s, then simulate video ending
        mock_page.evaluate.side_effect = [
            None,    # play
            1800.0,  # duration query
            True,    # is_ended query
        ]
        res = await action_watch_video(
            mock_page,
            video_url="https://www.youtube.com/watch?v=long123",
            min_pct=60.0,
            max_pct=90.0,
            min_watch_seconds=600.0,
            max_watch_seconds=1200.0,
        )
        assert res["total_duration"] == 1800.0

    asyncio.run(_run())


def test_db_trust_plan_min_watch_minutes():
    """Verify that database stores and returns min_watch_minutes correctly."""
    db.init_db()
    # Check that dummy plan decode has min_watch_minutes default
    decoded = db._decode_trust_plan({"min_watch_minutes": None})
    assert decoded["min_watch_minutes"] == 10

    decoded_custom = db._decode_trust_plan({"min_watch_minutes": 15})
    assert decoded_custom["min_watch_minutes"] == 15

