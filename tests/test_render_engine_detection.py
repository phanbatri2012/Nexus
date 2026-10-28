"""Test video render engine detection and filtering."""
import json
import pytest
from nexus_engine.services import database as db

def test_render_engine_constants():
    assert db.RENDER_ENGINE_GOOGLE_FLOW == "google_flow"
    assert db.RENDER_ENGINE_MUSE_AI == "muse_ai"
    assert db.RENDER_ENGINE_STOCK_VIDEO == "stock_video"
    assert db.RENDER_ENGINE_DRAFT == "draft"
    assert "google_flow" in db.RENDER_ENGINES
    assert "muse_ai" in db.RENDER_ENGINES
    assert "stock_video" in db.RENDER_ENGINES

def test_resolve_video_render_engines_batch():
    # Pass mock video records
    sample_videos = [
        {"id": 9999991, "production_snapshot_json": json.dumps({"render_mode": "muse_ai"})},
        {"id": 9999992, "production_snapshot_json": json.dumps({"render_mode": "stock_video"})},
        {"id": 9999993, "production_snapshot_json": json.dumps({"render_mode": "google_flow"})},
        {"id": 9999994, "production_snapshot_json": "{}"},
    ]
    resolved = db.resolve_video_render_engines(sample_videos)
    assert resolved[0]["render_engine"] == "muse_ai"
    assert resolved[0]["render_engine_label"] == "Muse AI"
    assert resolved[0]["render_engine_icon"] == "🎬"

    assert resolved[1]["render_engine"] == "stock_video"
    assert resolved[1]["render_engine_label"] == "Video có sẵn"
    assert resolved[1]["render_engine_icon"] == "🎞️"

    assert resolved[2]["render_engine"] == "google_flow"
    assert resolved[2]["render_engine_label"] == "Google Flow"
    assert resolved[2]["render_engine_icon"] == "⚡"

    assert resolved[3]["render_engine"] == "draft"
    assert resolved[3]["render_engine_label"] == "Kịch bản gốc"
    assert resolved[3]["render_engine_icon"] == "📝"

def test_get_all_videos_includes_render_engine():
    res = db.get_all_videos(limit=3)
    assert "items" in res
    for item in res["items"]:
        assert "render_engine" in item
        assert "render_engine_label" in item
        assert "render_engine_icon" in item
        assert item["render_engine"] in db.RENDER_ENGINES
