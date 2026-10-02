"""Unit & Integration tests for Stock Video / Radio Story Rendering Engine."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from auto_yt.services import database as db
from auto_yt.services import stock_video_renderer
from auto_yt.services import video_production


class StockVideoRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test.db"

        self.db_patch = patch.object(db, "DB_PATH", self.db_path)
        self.db_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_get_available_background_videos_and_icons(self):
        # Create dummy video and icon files
        bg_dir = self.temp_path / "bg"
        bg_dir.mkdir()
        (bg_dir / "clip1.mp4").touch()
        (bg_dir / "clip2.MOV").touch()
        (bg_dir / "ignore.txt").touch()

        icon_dir = self.temp_path / "icons"
        icon_dir.mkdir()
        (icon_dir / "sun.gif").touch()
        (icon_dir / "heart.webp").touch()

        videos = stock_video_renderer.get_available_background_videos(bg_dir)
        self.assertEqual(len(videos), 2)

        icons = stock_video_renderer.get_available_animated_icons(icon_dir)
        self.assertEqual(len(icons), 2)

    def test_calculate_layout_coordinates_all_4_corners(self):
        corners = ["bottom_right", "bottom_left", "top_right", "top_left"]
        for corner in corners:
            coords = stock_video_renderer.calculate_layout_coordinates(
                corner=corner,
                thumb_w=580,
                thumb_h=330,
                card_w=900,
                card_h=150,
                icon_w=100,
                icon_h=100,
                wave_w=360,
                wave_h=70,
                canvas_w=1920,
                canvas_h=1080,
                margin=40,
            )
            self.assertIn("thumb", coords)
            self.assertIn("card", coords)
            self.assertIn("icon", coords)
            self.assertIn("wave", coords)

            # Check bounds within 1920x1080
            for name, (x, y) in coords.items():
                self.assertGreaterEqual(x, 0, f"{corner} {name} x < 0")
                self.assertGreaterEqual(y, 0, f"{corner} {name} y < 0")
                self.assertLess(x, 1920, f"{corner} {name} x >= 1920")
                self.assertLess(y, 1080, f"{corner} {name} y >= 1080")

    def test_build_background_playlist_repeats_when_needed(self):
        v1 = self.temp_path / "v1.mp4"
        v2 = self.temp_path / "v2.mp4"
        v1.touch()
        v2.touch()

        # Mock probe_media_duration to return 10s per video
        with patch.object(stock_video_renderer, "probe_media_duration", return_value=10.0):
            # Target duration: 45s -> requires 5 video segments
            playlist = stock_video_renderer.build_background_playlist([v1, v2], target_duration=45.0)
            self.assertGreaterEqual(len(playlist), 5)

    def test_generate_thumbnail_and_title_card_overlays(self):
        raw_thumb = self.temp_path / "raw_thumb.png"
        img = Image.new("RGB", (640, 360), (50, 100, 150))
        img.save(raw_thumb)

        out_thumb = self.temp_path / "styled_thumb.png"
        stock_video_renderer.generate_thumbnail_overlay(raw_thumb, out_thumb, target_w=560, target_h=315)
        self.assertTrue(out_thumb.exists())
        with Image.open(out_thumb) as t_im:
            self.assertGreater(t_im.width, 560)
            self.assertGreater(t_im.height, 315)

        out_title = self.temp_path / "styled_title.png"
        _, w, h = stock_video_renderer.generate_title_card_overlay(
            "Tiêu Đề Video Mẫu Rất Hay Và Sâu Sắc",
            out_title,
            max_card_width=900,
        )
        self.assertTrue(out_title.exists())
        self.assertGreater(w, 800)
        self.assertGreater(h, 50)

    def test_produce_video_delegates_to_stock_video_when_configured(self):
        # Create video in DB
        video_id = db.save_video(
            url="https://youtube.com/watch?v=mock123",
            title="Video Test Stock Mode",
            transcript="Nội dung kịch bản mẫu",
            generated_script="### [TITLE]\nVideo Test Stock Mode\n\n### [CHAPTERS]\n00:00 - Mở đầu",
            prompt_version="prompt_v1",
        )

        mock_result = {
            "artifact": {"id": 99, "artifact_type": "final_mp4", "file_path": "/path/video.mp4"},
            "render_mode": "stock_video",
        }

        with patch("auto_yt.services.stock_video_renderer.produce_stock_video", return_value=mock_result) as mock_stock:
            snapshot = {
                "render_mode": "stock_video",
                "pipeline": {"render_mode": "stock_video"},
            }
            res = video_production.produce_video(
                video_id=video_id,
                snapshot=snapshot,
                progress=lambda m, s="": None,
                cancel_check=lambda: None,
            )
            mock_stock.assert_called_once()
            self.assertEqual(res["render_mode"], "stock_video")

    def test_get_render_status_endpoint_details_and_active_detection(self):
        from auto_yt import main

        video_id = db.save_video(
            url="https://youtube.com/watch?v=render_stat_test",
            title="Render Status Test",
            transcript="Transcript",
            generated_script="Script",
            prompt_version="default",
        )

        # 1. Initially, no artifact and no job
        status1 = main.get_render_status(video_id)
        self.assertFalse(status1["has_mp4"])
        self.assertFalse(status1["is_active"])
        self.assertIsNone(status1["mp4_details"])

        # 2. Add an existing ready artifact
        mp4_file = self.temp_path / "final.mp4"
        mp4_file.write_bytes(b"x" * 1048576) # 1 MB
        artifact = db.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(mp4_file),
            content_hash="mp4-test-hash",
            duration_seconds=3660.72,
            size_bytes=641311985,
            status="ready",
        )

        status2 = main.get_render_status(video_id)
        self.assertTrue(status2["has_mp4"])
        self.assertFalse(status2["is_active"])
        self.assertIsNotNone(status2["mp4_details"])
        self.assertEqual(status2["mp4_details"]["duration_formatted"], "01:01:01")
        self.assertEqual(status2["mp4_details"]["size_formatted"], "611.6 MB")
        self.assertEqual(status2["mp4_details"]["resolution"], "1080p FHD")
        self.assertEqual(status2["mp4_details"]["fps"], "30 FPS")

        # 3. Create an active running job (e.g. user clicked recreate)
        job = db.create_system_job(
            job_id="job-active-render-123",
            job_type="video_render",
            title="Dựng video MP4 (Tạo mới)",
            payload={"video_id": video_id},
        )
        db.update_system_job(job["id"], video_id=video_id, status="running", progress="Đang encode MP4 (00:15:00 / 01:01:00)...")

        status3 = main.get_render_status(video_id)
        self.assertTrue(status3["is_active"])
        self.assertEqual(status3["job"]["status"], "running")
        self.assertEqual(status3["job"]["progress"], "Đang encode MP4 (00:15:00 / 01:01:00)...")


if __name__ == "__main__":
    unittest.main()

