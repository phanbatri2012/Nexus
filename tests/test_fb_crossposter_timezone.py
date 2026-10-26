"""Regression tests for Fanpage-local scheduling and UTC persistence."""

import datetime
import sqlite3
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from nexus_engine.services import database as db
import nexus_engine.main as main


@pytest.fixture
def isolated_fb_database(tmp_path, monkeypatch):
    database_path = tmp_path / "crossposter-timezone.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    conn = sqlite3.connect(database_path)
    conn.executescript(
        """
        CREATE TABLE fb_crossposter_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_fb_page_id TEXT NOT NULL UNIQUE,
            schedule_times_json TEXT DEFAULT '["11:30", "19:30"]',
            daily_quota INTEGER DEFAULT 2,
            lead_time_minutes INTEGER DEFAULT 60,
            publication_timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh'
        );
        CREATE TABLE fb_crossposter_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_page_id TEXT DEFAULT '',
            youtube_id TEXT NOT NULL,
            youtube_url TEXT DEFAULT '',
            original_title TEXT DEFAULT '',
            original_description TEXT DEFAULT '',
            original_tags_json TEXT DEFAULT '[]',
            thumbnail_url TEXT DEFAULT '',
            youtube_upload_date TEXT DEFAULT '',
            fb_title TEXT DEFAULT '',
            fb_description TEXT DEFAULT '',
            fb_description_source TEXT DEFAULT 'auto',
            status TEXT DEFAULT 'pending',
            sort_order INTEGER DEFAULT 0,
            scheduled_publish_time INTEGER DEFAULT 0,
            meta_scheduled_publish_time INTEGER DEFAULT 0,
            fb_post_id TEXT DEFAULT '',
            upload_video_id TEXT DEFAULT '',
            checkpoint_phase TEXT DEFAULT '',
            meta_state TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            UNIQUE(youtube_id, target_page_id)
        );
        """
    )
    conn.close()
    return database_path


def _insert_queue_item(page_id: str, youtube_id: str, *, status: str = "pending") -> int:
    conn = sqlite3.connect(db.DB_PATH)
    cursor = conn.execute(
        """
        INSERT INTO fb_crossposter_queue (
            target_page_id, youtube_id, status, original_title, sort_order
        ) VALUES (?, ?, ?, ?, 1)
        """,
        (page_id, youtube_id, status, youtube_id),
    )
    conn.commit()
    item_id = int(cursor.lastrowid)
    conn.close()
    return item_id


def test_same_wall_clock_creates_different_epoch_per_fanpage_timezone(
    isolated_fb_database,
):
    now_utc = datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc)
    start_date = datetime.date(2026, 1, 16)
    vietnam_item = _insert_queue_item("vn", "video-vn")
    new_york_item = _insert_queue_item("ny", "video-ny")

    db.recalculate_fb_queue_schedule(
        1,
        ["19:30"],
        target_page_id="vn",
        start_date=start_date,
        timezone_name="Asia/Ho_Chi_Minh",
        now_utc=now_utc,
    )
    db.recalculate_fb_queue_schedule(
        1,
        ["19:30"],
        target_page_id="ny",
        start_date=start_date,
        timezone_name="America/New_York",
        now_utc=now_utc,
    )

    vietnam_epoch = db.get_fb_crossposter_queue_item(vietnam_item)[
        "scheduled_publish_time"
    ]
    new_york_epoch = db.get_fb_crossposter_queue_item(new_york_item)[
        "scheduled_publish_time"
    ]
    assert datetime.datetime.fromtimestamp(
        vietnam_epoch, ZoneInfo("Asia/Ho_Chi_Minh")
    ).strftime("%Y-%m-%d %H:%M") == "2026-01-16 19:30"
    assert datetime.datetime.fromtimestamp(
        new_york_epoch, ZoneInfo("America/New_York")
    ).strftime("%Y-%m-%d %H:%M") == "2026-01-16 19:30"
    assert new_york_epoch - vietnam_epoch == 12 * 60 * 60


def test_recalculate_skips_nonexistent_dst_slot(isolated_fb_database):
    item_id = _insert_queue_item("ny", "video-dst")
    db.recalculate_fb_queue_schedule(
        1,
        ["02:30"],
        target_page_id="ny",
        start_date=datetime.date(2026, 3, 8),
        timezone_name="America/New_York",
        now_utc=datetime.datetime(2026, 3, 7, tzinfo=datetime.timezone.utc),
    )
    scheduled_epoch = db.get_fb_crossposter_queue_item(item_id)[
        "scheduled_publish_time"
    ]
    scheduled_local = datetime.datetime.fromtimestamp(
        scheduled_epoch, ZoneInfo("America/New_York")
    )
    assert scheduled_local.strftime("%Y-%m-%d %H:%M %z") == "2026-03-09 02:30 -0400"


def test_reserve_uses_campaign_timezone_and_new_rows_use_utc_iso(
    isolated_fb_database,
):
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        """
        INSERT INTO fb_crossposter_settings (
            target_fb_page_id, schedule_times_json, daily_quota,
            lead_time_minutes, publication_timezone
        ) VALUES ('vn', '["19:30"]', 1, 30, 'Asia/Ho_Chi_Minh')
        """
    )
    conn.commit()
    conn.close()
    item_id = _insert_queue_item("vn", "video-reserve")

    reserved_epoch = db.reserve_next_fb_queue_slot(
        item_id,
        now_utc=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
    )
    assert datetime.datetime.fromtimestamp(
        reserved_epoch, ZoneInfo("Asia/Ho_Chi_Minh")
    ).strftime("%Y-%m-%d %H:%M") == "2026-01-15 19:30"

    db.upsert_fb_crossposter_queue_items(
        [{"youtube_id": "video-new", "original_title": "New video"}],
        target_page_id="vn",
    )
    new_item = db.get_fb_crossposter_queue(target_page_id="vn", search="video-new")[
        "items"
    ][0]
    assert new_item["created_at"].endswith("+00:00")
    assert new_item["updated_at"].endswith("+00:00")


def test_recalculate_leaves_meta_submitted_rows_unchanged(isolated_fb_database):
    item_id = _insert_queue_item("vn", "video-meta", status="meta_scheduled")
    original_epoch = 1_800_000_000
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        """
        UPDATE fb_crossposter_queue
        SET scheduled_publish_time = ?, fb_post_id = 'meta-123', meta_state = 'scheduled'
        WHERE id = ?
        """,
        (original_epoch, item_id),
    )
    conn.commit()
    conn.close()

    assert db.recalculate_fb_queue_schedule(
        1,
        ["19:30"],
        target_page_id="vn",
        timezone_name="Asia/Ho_Chi_Minh",
        now_utc=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
    ) == 0
    assert db.get_fb_crossposter_queue_item(item_id)[
        "scheduled_publish_time"
    ] == original_epoch


def test_queue_update_converts_local_datetime_with_campaign_timezone(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.db,
        "get_fb_crossposter_queue_item",
        lambda _item_id: {"id": 7, "target_page_id": "ny"},
    )
    monkeypatch.setattr(
        main.db,
        "get_fb_crossposter_settings",
        lambda _page_id: {"publication_timezone": "America/New_York"},
    )
    monkeypatch.setattr(
        main.db,
        "update_fb_crossposter_queue_item",
        lambda _item_id, fields: captured.update(fields) or True,
    )

    main.update_fb_crossposter_item(
        7,
        main.FBCrossPosterItemUpdatePayload(
            scheduled_publish_local="2026-07-15T19:30"
        ),
    )
    expected = int(
        datetime.datetime(
            2026,
            7,
            15,
            19,
            30,
            tzinfo=ZoneInfo("America/New_York"),
        ).timestamp()
    )
    assert captured["scheduled_publish_time"] == expected


def test_queue_update_keeps_legacy_epoch_contract(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.db,
        "update_fb_crossposter_queue_item",
        lambda _item_id, fields: captured.update(fields) or True,
    )
    monkeypatch.setattr(
        main.db,
        "get_fb_crossposter_queue_item",
        lambda _item_id: {"id": 7, **captured},
    )

    main.update_fb_crossposter_item(
        7,
        main.FBCrossPosterItemUpdatePayload(scheduled_publish_time=1_800_000_000),
    )
    assert captured["scheduled_publish_time"] == 1_800_000_000


def test_queue_update_rejects_nonexistent_dst_local_time(monkeypatch):
    monkeypatch.setattr(
        main.db,
        "get_fb_crossposter_queue_item",
        lambda _item_id: {"id": 7, "target_page_id": "ny"},
    )
    monkeypatch.setattr(
        main.db,
        "get_fb_crossposter_settings",
        lambda _page_id: {"publication_timezone": "America/New_York"},
    )

    with pytest.raises(HTTPException) as exc_info:
        main.update_fb_crossposter_item(
            7,
            main.FBCrossPosterItemUpdatePayload(
                scheduled_publish_local="2026-03-08T02:30"
            ),
        )
    assert exc_info.value.status_code == 422
    assert "DST" in str(exc_info.value.detail)


def test_settings_reject_invalid_iana_timezone(monkeypatch):
    monkeypatch.setattr(main.db, "get_fb_crossposter_settings", lambda _page_id: {})
    with pytest.raises(HTTPException) as exc_info:
        main.save_fb_crossposter_settings(
            main.FBCrossPosterSettingsPayload(
                target_fb_page_id="page-invalid-tz",
                publication_timezone="Invalid/Timezone",
            )
        )
    assert exc_info.value.status_code == 422
    assert "Invalid/Timezone" in str(exc_info.value.detail)
