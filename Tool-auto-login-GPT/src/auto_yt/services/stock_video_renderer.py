"""Stock Video / Radio Story Video Rendering Engine.

Muxes background video footage from a repository with:
- MP3 main audio (OmniVoice / TTS)
- Picture-in-picture Thumbnail at 1 of 4 randomized corners
- Stylized Title Card at the opposite corner across center axis
- Animated Sticker / Icon adjacent to dynamic Audio Waveform Visualizer
- Multi-video randomized playlist concat & looping to match exact audio duration.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Sequence

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from auto_yt.paths import (
    ANIMATED_ICONS_DIR,
    AUDIO_DIR,
    BACKGROUND_VIDEOS_DIR,
    RENDERS_DIR,
    THUMBNAILS_DIR,
)
from auto_yt.services import database as db
from auto_yt.services import process_registry

logger = logging.getLogger(__name__)

TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
TARGET_FPS = 30
SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
SUPPORTED_ICON_EXTS = {".gif", ".webp", ".png"}


class StockVideoRenderError(RuntimeError):
    pass


def probe_video_info(file_path: Path) -> dict[str, Any]:
    """Probe video resolution, pixel format, and duration."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", str(file_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    w, h, pix, dur = 0, 0, "", 0.0
    m_vid = re.search(r"Video:.*?([a-zA-Z0-9_]+)(?:\([^)]+\))?,\s*(\d+)x(\d+)", res.stderr)
    if m_vid:
        pix = m_vid.group(1)
        w = int(m_vid.group(2))
        h = int(m_vid.group(3))
    m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if m_dur:
        hd, md, sd = m_dur.groups()
        dur = float(hd) * 3600 + float(md) * 60 + float(sd)
    return {"width": w, "height": h, "pix_fmt": pix, "duration": dur}


def probe_media_duration(file_path: Path) -> float:
    """Probe audio/video duration in seconds using ffprobe/ffmpeg."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ffprobe_exe = ffmpeg_exe.replace("ffmpeg", "ffprobe")
    
    # Try ffprobe if available
    if os.path.exists(ffprobe_exe):
        cmd = [
            ffprobe_exe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration_str = res.stdout.strip()
            if duration_str:
                return float(duration_str)
        except Exception:
            pass

    # Fallback to ffmpeg -i info parsing
    cmd = [ffmpeg_exe, "-i", str(file_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if match:
        h, m, s = match.groups()
        return float(h) * 3600 + float(m) * 60 + float(s)

    raise StockVideoRenderError(f"Không thể đo thời lượng của file: {file_path}")


def _escape_subtitle_path(path: Path) -> str:
    """Escape Windows paths properly for FFmpeg subtitles filter graph."""
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'")
    return value


def get_available_background_videos(custom_dir: Path | str | None = None) -> list[Path]:
    """Scan and return list of all valid video files in background videos directory."""
    target_dir = Path(custom_dir).resolve() if custom_dir else BACKGROUND_VIDEOS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    videos: list[Path] = []
    for item in target_dir.rglob("*"):
        if (
            item.is_file()
            and not item.name.startswith(".")
            and "tmp" not in item.name.lower()
            and item.suffix.lower() in SUPPORTED_VIDEO_EXTS
        ):
            videos.append(item)

    return sorted(videos)


def ensure_default_animated_icons(target_dir: Path) -> list[Path]:
    """Generate high quality transparent animated icons if directory is empty."""
    target_dir.mkdir(parents=True, exist_ok=True)
    num_frames = 24
    
    # 1. Cute Smiling Sun
    sun_file = target_dir / "sun_cute.gif"
    if not sun_file.exists():
        frames = []
        for f in range(num_frames):
            im = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)
            t = f / num_frames
            scale = 1.0 + 0.08 * math.sin(t * 2 * math.pi)
            rot = t * 2 * math.pi / 8
            cx, cy = 70, 70
            for r_idx in range(8):
                angle = rot + r_idx * (2 * math.pi / 8)
                ray_len = 48 + 8 * math.sin(t * 2 * math.pi + r_idx)
                rx = cx + ray_len * math.cos(angle)
                ry = cy + ray_len * math.sin(angle)
                draw.line([(cx, cy), (rx, ry)], fill=(255, 170, 0, 255), width=7)
                draw.ellipse([rx - 4, ry - 4, rx + 4, ry + 4], fill=(255, 150, 0, 255))
            r = int(28 * scale)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 215, 0, 255), outline=(255, 140, 0, 255), width=3)
            eye_offset, eye_y = 10, cy - 4
            draw.ellipse([cx - eye_offset - 3, eye_y - 4, cx - eye_offset + 3, eye_y + 4], fill=(80, 40, 10, 255))
            draw.ellipse([cx + eye_offset - 3, eye_y - 4, cx + eye_offset + 3, eye_y + 4], fill=(80, 40, 10, 255))
            draw.ellipse([cx - eye_offset - 1, eye_y - 3, cx - eye_offset + 1, eye_y - 1], fill=(255, 255, 255, 255))
            draw.ellipse([cx + eye_offset - 1, eye_y - 3, cx + eye_offset + 1, eye_y - 1], fill=(255, 255, 255, 255))
            draw.ellipse([cx - eye_offset - 6, eye_y + 6, cx - eye_offset + 2, eye_y + 12], fill=(255, 105, 135, 200))
            draw.ellipse([cx + eye_offset - 2, eye_y + 6, cx + eye_offset + 6, eye_y + 12], fill=(255, 105, 135, 200))
            draw.arc([cx - 8, cy - 2, cx + 8, cy + 12], start=10, end=170, fill=(120, 40, 10, 255), width=3)
            frames.append(im)
        frames[0].save(sun_file, save_all=True, append_images=frames[1:], duration=45, loop=0, disposal=2)

    # 2. Floating Music Notes
    notes_file = target_dir / "music_notes.gif"
    if not notes_file.exists():
        frames = []
        for f in range(num_frames):
            im = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)
            t = f / num_frames
            y1 = 90 - 50 * ((t + 0.0) % 1.0)
            x1 = 50 + 15 * math.sin(t * 2 * math.pi)
            alpha1 = int(255 * math.sin(((t + 0.0) % 1.0) * math.pi))
            if alpha1 > 20:
                draw.ellipse([x1 - 10, y1 + 5, x1 + 4, y1 + 17], fill=(168, 85, 247, alpha1))
                draw.line([(x1 + 3, y1 + 10), (x1 + 3, y1 - 18)], fill=(168, 85, 247, alpha1), width=4)
                draw.arc([x1 + 3, y1 - 20, x1 + 22, y1 - 6], start=270, end=90, fill=(168, 85, 247, alpha1), width=4)
            y2 = 100 - 60 * ((t + 0.5) % 1.0)
            x2 = 85 + 12 * math.cos(t * 2 * math.pi)
            alpha2 = int(255 * math.sin(((t + 0.5) % 1.0) * math.pi))
            if alpha2 > 20:
                draw.ellipse([x2 - 18, y2 + 5, x2 - 6, y2 + 16], fill=(56, 189, 248, alpha2))
                draw.ellipse([x2 + 2, y2, x2 + 14, y2 + 11], fill=(56, 189, 248, alpha2))
                draw.line([(x2 - 7, y2 + 10), (x2 - 7, y2 - 15)], fill=(56, 189, 248, alpha2), width=3)
                draw.line([(x2 + 13, y2 + 5), (x2 + 13, y2 - 20)], fill=(56, 189, 248, alpha2), width=3)
                draw.line([(x2 - 7, y2 - 15), (x2 + 13, y2 - 20)], fill=(56, 189, 248, alpha2), width=5)
            frames.append(im)
        frames[0].save(notes_file, save_all=True, append_images=frames[1:], duration=45, loop=0, disposal=2)

    # 3. Spinning Vinyl Record
    vinyl_file = target_dir / "vinyl_record.gif"
    if not vinyl_file.exists():
        frames = []
        for f in range(num_frames):
            im = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)
            t = f / num_frames
            cx, cy = 70, 70
            draw.ellipse([cx - 50, cy - 50, cx + 50, cy + 50], fill=(24, 24, 27, 255), outline=(63, 63, 70, 255), width=2)
            for gr in [42, 35, 28]:
                draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], outline=(39, 39, 42, 255), width=1)
            angle = t * 2 * math.pi
            shine_x, shine_y = cx + 30 * math.cos(angle), cy + 30 * math.sin(angle)
            draw.line([(cx, cy), (shine_x, shine_y)], fill=(255, 255, 255, 80), width=8)
            draw.line([(cx, cy), (cx - 30 * math.cos(angle), cy - 30 * math.sin(angle))], fill=(255, 255, 255, 80), width=8)
            draw.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(239, 68, 68, 255), outline=(252, 165, 165, 255), width=2)
            draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(255, 255, 255, 255))
            frames.append(im)
        frames[0].save(vinyl_file, save_all=True, append_images=frames[1:], duration=45, loop=0, disposal=2)

    # 4. Twinkling Sparkle Star
    star_file = target_dir / "sparkle_star.gif"
    if not star_file.exists():
        frames = []
        for f in range(num_frames):
            im = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)
            t = f / num_frames
            cx, cy = 70, 70
            scale = 0.8 + 0.4 * (0.5 + 0.5 * math.sin(t * 2 * math.pi))
            star_points = []
            outer_r, inner_r = 45 * scale, 10 * scale
            for p in range(8):
                ang = p * math.pi / 4
                rad = outer_r if p % 2 == 0 else inner_r
                star_points.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
            draw.polygon(star_points, fill=(250, 204, 21, 255), outline=(234, 88, 12, 255))
            draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(255, 255, 255, 240))
            frames.append(im)
        frames[0].save(star_file, save_all=True, append_images=frames[1:], duration=45, loop=0, disposal=2)

    icons = [p for p in target_dir.glob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_ICON_EXTS]
    return sorted(icons)


def get_available_animated_icons(custom_dir: Path | str | None = None) -> list[Path]:
    """Scan and return list of animated sticker icons (auto-generating defaults if empty)."""
    target_dir = Path(custom_dir).resolve() if custom_dir else ANIMATED_ICONS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    icons = [item for item in target_dir.glob("*") if item.is_file() and item.suffix.lower() in SUPPORTED_ICON_EXTS]
    if not icons:
        icons = ensure_default_animated_icons(target_dir)

    return sorted(icons)


def _get_system_font(size: int = 34) -> ImageFont.FreeTypeFont:
    """Load Vietnamese Unicode font from Windows system fonts or default."""
    candidates = [
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf",
        "C:\\Windows\\Fonts\\tahomabd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for font_path in candidates:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate_thumbnail_overlay(
    thumbnail_path: Path,
    output_path: Path,
    target_w: int = 580,
    target_h: int = 326,
) -> Path:
    """Pre-render thumbnail with rounded corners, clean white border, and soft drop shadow."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with Image.open(thumbnail_path) as source:
        rgb = source.convert("RGBA")
        resized = rgb.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # 1. Rounded rectangle mask
    mask = Image.new("L", (target_w, target_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, target_w, target_h], radius=16, fill=255)

    # 2. Border container
    border_padding = 6
    border_w = target_w + border_padding * 2
    border_h = target_h + border_padding * 2
    border_im = Image.new("RGBA", (border_w, border_h), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_im)
    border_draw.rounded_rectangle(
        [0, 0, border_w, border_h],
        radius=18,
        fill=(255, 255, 255, 255),
        outline=(226, 232, 240, 255),
        width=2,
    )
    border_im.paste(resized, (border_padding, border_padding), mask)

    # 3. Soft Drop Shadow
    shadow_pad = 20
    shadow_w = border_w + shadow_pad * 2
    shadow_h = border_h + shadow_pad * 2
    final_canvas = Image.new("RGBA", (shadow_w, shadow_h), (0, 0, 0, 0))
    sh_draw = ImageDraw.Draw(final_canvas)
    sh_draw.rounded_rectangle(
        [shadow_pad, shadow_pad + 4, shadow_pad + border_w, shadow_pad + border_h + 4],
        radius=20,
        fill=(0, 0, 0, 160),
    )
    final_canvas = final_canvas.filter(ImageFilter.GaussianBlur(10))
    final_canvas.paste(border_im, (shadow_pad, shadow_pad), border_im)

    final_canvas.save(output_path, "PNG")
    return output_path


def generate_title_card_overlay(
    title: str,
    output_path: Path,
    max_card_width: int = 900,
    text_color: tuple[int, int, int, int] = (91, 33, 182, 255),
) -> tuple[Path, int, int]:
    """Pre-render title card with rounded corners, subtle border, shadow, and centered text."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font = _get_system_font(size=34)

    # Wrap title into lines
    words = title.strip().split()
    lines: list[str] = []
    curr_line: list[str] = []
    max_text_width = max_card_width - 80

    for word in words:
        curr_line.append(word)
        bbox = font.getbbox(" ".join(curr_line))
        if bbox[2] - bbox[0] > max_text_width:
            curr_line.pop()
            if curr_line:
                lines.append(" ".join(curr_line))
            curr_line = [word]
    if curr_line:
        lines.append(" ".join(curr_line))
    if not lines:
        lines = [title]

    line_height = 46
    card_h = len(lines) * line_height + 36
    card_w = max_card_width

    # Create inner card
    card_surface = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    cs_draw = ImageDraw.Draw(card_surface)
    cs_draw.rounded_rectangle(
        [0, 0, card_w, card_h],
        radius=14,
        fill=(255, 255, 255, 245),
        outline=(226, 232, 240, 255),
        width=2,
    )

    y_offset = 20
    for line in lines:
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x_pos = (card_w - line_w) // 2
        cs_draw.text((x_pos, y_offset), line, font=font, fill=text_color)
        y_offset += line_height

    # Shadow container
    shadow_pad = 18
    total_w = card_w + shadow_pad * 2
    total_h = card_h + shadow_pad * 2
    final_card = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    fc_draw = ImageDraw.Draw(final_card)
    fc_draw.rounded_rectangle(
        [shadow_pad, shadow_pad + 3, shadow_pad + card_w, shadow_pad + card_h + 3],
        radius=16,
        fill=(0, 0, 0, 140),
    )
    final_card = final_card.filter(ImageFilter.GaussianBlur(8))
    final_card.paste(card_surface, (shadow_pad, shadow_pad), card_surface)

    final_card.save(output_path, "PNG")
    return output_path, total_w, total_h


TITLE_OUTLINE_PALETTE: list[tuple[int, int, int]] = [
    (250, 204, 21),   # Golden Yellow (như ảnh mẫu)
    (245, 158, 11),   # Amber/Gold
    (251, 146, 60),   # Bright Orange
    (6, 182, 212),    # Cyan/Neon Blue
    (168, 85, 247),   # Purple/Violet
    (239, 68, 68),    # Crimson Red
    (34, 197, 94),    # Bright Green
    (244, 63, 94),    # Hot Pink
]


def generate_title_text_overlay(
    title: str,
    output_path: Path,
    max_width: int = 1150,
    outline_color: tuple[int, int, int] | None = None,
    font_size: int = 42,
) -> tuple[Path, int, int, tuple[int, int, int]]:
    """Pre-render bold title text with white fill, thick random-colored outline, and dark drop shadow (no background box)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if outline_color is None:
        outline_color = secrets.choice(TITLE_OUTLINE_PALETTE)

    font = _get_system_font(size=font_size)
    words = title.strip().split()

    if len(words) <= 14:
        mid = max(1, len(words) // 2)
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    else:
        lines = []
        curr: list[str] = []
        for w in words:
            curr.append(w)
            bbox = font.getbbox(" ".join(curr))
            if (bbox[2] - bbox[0]) > (max_width - 80):
                curr.pop()
                if curr:
                    lines.append(" ".join(curr))
                curr = [w]
        if curr:
            lines.append(" ".join(curr))

    lines = [l for l in lines if l.strip()]
    if not lines:
        lines = [title]

    line_height = int(font_size * 1.3)
    pad = 20
    total_h = len(lines) * line_height + pad * 2
    total_w = max_width

    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    stroke_w = max(6, int(font_size * 0.16))
    y_offset = pad

    for line in lines:
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x_pos = (total_w - line_w) // 2

        # 1. Dark outer shadow for high contrast on light backgrounds
        for dx, dy in [(-3, 3), (0, 4), (3, 3), (4, 0), (3, -3), (0, -4), (-3, -3), (-4, 0)]:
            draw.text(
                (x_pos + dx, y_offset + dy + 2),
                line,
                font=font,
                fill=(0, 0, 0, 230),
                stroke_width=stroke_w + 3,
                stroke_fill=(0, 0, 0, 230),
            )

        # 2. White text with vibrant colored outline
        draw.text(
            (x_pos, y_offset),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_w,
            stroke_fill=(*outline_color, 255),
        )

        y_offset += line_height

    canvas.save(output_path, "PNG")
    return output_path, total_w, total_h, outline_color


WAVE_COLORS_PALETTE: list[str] = [
    "white@0.95",       # Bright White
    "#00E5FF@0.95",     # Electric Cyan
    "#FFD700@0.95",     # Golden Yellow
    "#22C55E@0.95",     # Vibrant Green
    "#FF6B00@0.95",     # Bright Orange
    "#F43F5E@0.95",     # Neon Rose / Pink
    "#A855F7@0.95",     # Electric Purple
    "#38BDF8@0.95",     # Sky Blue
]

WAVE_BARS_COUNT: int = 15
WAVE_BAR_WIDTH: int = 14
WAVE_BAR_GAP: int = 18
WAVE_BAR_PITCH: int = WAVE_BAR_WIDTH + WAVE_BAR_GAP  # 32
WAVE_DEFAULT_WIDTH: int = WAVE_BARS_COUNT * WAVE_BAR_PITCH  # 480
WAVE_DEFAULT_HEIGHT: int = 260


def _boxes_intersect(b1: tuple[int, int, int, int], b2: tuple[int, int, int, int]) -> bool:
    """Return True if rectangle b1 intersects rectangle b2 (x1, y1, x2, y2)."""
    return not (b1[2] < b2[0] or b1[0] > b2[2] or b1[3] < b2[1] or b1[1] > b2[3])


def pick_random_waveform_coordinates(
    thumb_x: int,
    thumb_y: int,
    thumb_w: int,
    thumb_h: int,
    card_x: int,
    card_y: int,
    card_w: int,
    card_h: int,
    wave_w: int = WAVE_DEFAULT_WIDTH,
    wave_h: int = WAVE_DEFAULT_HEIGHT,
    canvas_w: int = TARGET_WIDTH,
    canvas_h: int = TARGET_HEIGHT,
    margin: int = 40,
    buffer_pad: int = 25,
) -> tuple[int, int]:
    """Pick random (x, y) coordinates for the large waveform strictly avoiding Thumbnail and Title overlays."""
    t_box = (
        thumb_x - buffer_pad,
        thumb_y - buffer_pad,
        thumb_x + thumb_w + buffer_pad,
        thumb_y + thumb_h + buffer_pad,
    )
    c_box = (
        card_x - buffer_pad,
        card_y - buffer_pad,
        card_x + card_w + buffer_pad,
        card_y + card_h + buffer_pad,
    )

    max_x = max(margin, canvas_w - wave_w - margin)
    max_y = max(margin, canvas_h - wave_h - margin)

    center_x = (canvas_w - wave_w) // 2
    center_y = (canvas_h - wave_h) // 2

    # Attempt random placement (100 tries)
    for _ in range(100):
        rx = secrets.randbelow(max_x - margin + 1) + margin
        ry = secrets.randbelow(max_y - margin + 1) + margin
        w_box = (rx, ry, rx + wave_w, ry + wave_h)
        if not _boxes_intersect(w_box, t_box) and not _boxes_intersect(w_box, c_box):
            return rx, ry

    # Balanced fallback positions across canvas
    safe_candidates = [
        (center_x, center_y),                          # Exact center
        (center_x, margin + 40),                       # Top center
        (max_x - 40, margin + 40),                     # Top right
        (margin + 40, margin + 40),                    # Top left
        (center_x, max(margin, center_y - 120)),       # Upper center
        (max_x - 40, max(margin, center_y - 80)),      # Mid right
    ]
    for sx, sy in safe_candidates:
        w_box = (sx, sy, sx + wave_w, sy + wave_h)
        if not _boxes_intersect(w_box, t_box) and not _boxes_intersect(w_box, c_box):
            return sx, sy

    return center_x, center_y


def calculate_layout_coordinates(
    corner: str = "bottom_left",
    thumb_w: int = 580,
    thumb_h: int = 326,
    card_w: int = 1150,
    card_h: int = 150,
    icon_w: int = 100,
    icon_h: int = 100,
    wave_w: int = WAVE_DEFAULT_WIDTH,
    wave_h: int = WAVE_DEFAULT_HEIGHT,
    canvas_w: int = TARGET_WIDTH,
    canvas_h: int = TARGET_HEIGHT,
    margin: int = 40,
) -> dict[str, tuple[int, int]]:
    """Compute (x, y) coordinates for Thumbnail, Title Card, Icon, and Waveform.
    
    Waveform is placed at a collision-free random position (avoiding Thumbnail & Title).
    Thumbnail PiP is placed at the specified corner (default: bottom-left).
    Title text is placed at the opposite corner (default: bottom-right).
    """
    coords = {}

    if corner == "bottom_right":
        # Thumbnail bottom-right, Title Card bottom-left
        coords["thumb"] = (canvas_w - thumb_w - margin, canvas_h - thumb_h - margin)
        card_x, card_y = margin, canvas_h - card_h - margin
        coords["card"] = (card_x, card_y)
    elif corner == "top_right":
        coords["thumb"] = (canvas_w - thumb_w - margin, margin)
        card_x, card_y = margin, margin
        coords["card"] = (card_x, card_y)
    elif corner == "top_left":
        coords["thumb"] = (margin, margin)
        card_x, card_y = canvas_w - card_w - margin, margin
        coords["card"] = (card_x, card_y)
    else:  # bottom_left (Default layout as in reference image)
        coords["thumb"] = (margin, canvas_h - thumb_h - margin)
        card_x = canvas_w - card_w - margin
        card_y = canvas_h - card_h - margin
        coords["card"] = (card_x, card_y)

    t_x, t_y = coords["thumb"]
    c_x, c_y = coords["card"]

    # Random waveform coordinates avoiding thumbnail and title
    wave_x, wave_y = pick_random_waveform_coordinates(
        thumb_x=t_x,
        thumb_y=t_y,
        thumb_w=thumb_w,
        thumb_h=thumb_h,
        card_x=c_x,
        card_y=c_y,
        card_w=card_w,
        card_h=card_h,
        wave_w=wave_w,
        wave_h=wave_h,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        margin=margin,
    )
    coords["wave"] = (wave_x, wave_y)
    coords["icon"] = (wave_x, wave_y)

    return coords


def build_background_playlist(
    available_videos: Sequence[Path],
    target_duration: float,
    target_w: int = TARGET_WIDTH,
    target_h: int = TARGET_HEIGHT,
) -> tuple[list[Path], dict[Path, float]]:
    """Select and loop a curated, resolution-uniform set of background videos."""
    if not available_videos:
        raise StockVideoRenderError("Danh sách video nền trống.")

    pool = list(available_videos)
    random.shuffle(pool)

    # 1. Curate a set of 8-15 high quality clips matching target resolution and duration >= 3.0s
    curated_candidates: list[tuple[Path, float]] = []
    durations: dict[Path, float] = {}

    sample_pool = pool[: min(len(pool), 80)]
    for video in sample_pool:
        try:
            info = probe_video_info(video)
            dur = info.get("duration") or 0.0
            if dur <= 0.0:
                try:
                    dur = probe_media_duration(video)
                except Exception:
                    pass
            if info["width"] == target_w and info["height"] == target_h and dur >= 3.0:
                curated_candidates.append((video, dur))
                durations[video] = dur
                if len(curated_candidates) >= 15:
                    break
        except Exception:
            continue

    # If no exact target_w x target_h matches found, fallback to any valid clips >= 2s
    if not curated_candidates:
        for video in sample_pool:
            try:
                dur = 0.0
                try:
                    info = probe_video_info(video)
                    dur = info.get("duration") or 0.0
                except Exception:
                    pass
                if dur <= 0.0:
                    dur = probe_media_duration(video)
                if dur >= 2.0:
                    curated_candidates.append((video, dur))
                    durations[video] = dur
                    if len(curated_candidates) >= 10:
                        break
            except Exception:
                continue

    # Final fallback for tests or minimal environments
    if not curated_candidates and sample_pool:
        for video in sample_pool:
            dur = 10.0
            try:
                dur = probe_media_duration(video)
            except Exception:
                pass
            curated_candidates.append((video, max(dur, 1.0)))
            durations[video] = max(dur, 1.0)
            if len(curated_candidates) >= 10:
                break

    if not curated_candidates:
        raise StockVideoRenderError("Không tìm thấy video nền hợp lệ trong kho video.")

    # 2. Build playlist by looping through curated candidates until target_duration + 60s is covered
    playlist: list[Path] = []
    accumulated_duration = 0.0
    buffer_target = target_duration + 60.0

    max_loops = 2000
    loop_count = 0
    while accumulated_duration < buffer_target and loop_count < max_loops:
        loop_count += 1
        shuffled_round = list(curated_candidates)
        random.shuffle(shuffled_round)
        for video, dur in shuffled_round:
            playlist.append(video)
            accumulated_duration += dur
            if accumulated_duration >= buffer_target:
                break

    logger.info(
        "Stock Video: Selected %d unique clips (%s) looped into %d playlist items (total %.1fs for target %.1fs)",
        len(curated_candidates),
        f"{target_w}x{target_h}",
        len(playlist),
        accumulated_duration,
        target_duration,
    )
    return playlist, durations


def produce_stock_video(
    video_id: int,
    snapshot: dict,
    progress: Callable[[str, str], None],
    cancel_check: Callable[[], None],
    force_new_project: bool = False,
) -> dict:
    """Execute complete stock video rendering for video_id."""
    del force_new_project
    cancel_check()
    progress("Đang kiểm tra dữ liệu đầu vào cho Video Nền...", "stock_video_prepare")

    # 1. Fetch Video record from DB
    video = db.get_video(video_id)
    if not video:
        raise StockVideoRenderError(f"Không tìm thấy video ID={video_id}")

    title = str(video.get("generated_title") or video.get("title") or f"Video {video_id}").strip()

    # 2. Locate audio file
    audio_path: Path | None = None

    # 2a. Check audio_task in database
    audio_task = db.get_audio_task(video_id)
    if audio_task:
        if audio_task.get("audio_url"):
            parsed = urllib.parse.urlparse(str(audio_task["audio_url"]))
            fname = Path(parsed.path).name
            if fname and (AUDIO_DIR / fname).is_file() and (AUDIO_DIR / fname).stat().st_size > 0:
                audio_path = AUDIO_DIR / fname
        if audio_path is None and audio_task.get("request_hash"):
            candidate = AUDIO_DIR / f"video_{video_id}_{audio_task['request_hash'][:16]}.mp3"
            if candidate.is_file() and candidate.stat().st_size > 0:
                audio_path = candidate

    # 2b. Check audio artifact in DB
    if audio_path is None:
        artifact = db.get_latest_video_artifact(video_id, "audio", status="ready")
        if artifact and (artifact.get("path") or artifact.get("file_path")):
            candidate = Path(artifact.get("path") or artifact.get("file_path")).resolve()
            if candidate.is_file() and candidate.stat().st_size > 0:
                audio_path = candidate

    # 2c. Check standard video_{video_id}.mp3
    if audio_path is None:
        candidate = AUDIO_DIR / f"video_{video_id}.mp3"
        if candidate.is_file() and candidate.stat().st_size > 0:
            audio_path = candidate

    # 2d. Check glob matching video_{video_id}_*.mp3 excluding preview files
    if audio_path is None:
        matches = [
            p for p in AUDIO_DIR.glob(f"video_{video_id}_*.mp3")
            if "_preview_" not in p.name and p.is_file() and p.stat().st_size > 0
        ]
        if matches:
            audio_path = max(matches, key=lambda p: p.stat().st_mtime)

    if audio_path is None or not audio_path.exists():
        raise StockVideoRenderError(f"Không tìm thấy file audio MP3 cho video {video_id}.")

    audio_duration = probe_media_duration(audio_path)
    if audio_duration <= 0.1:
        raise StockVideoRenderError(f"File audio có thời lượng không hợp lệ ({audio_duration}s)")

    logger.info("Stock Video: Video ID %s audio duration is %.2fs (file: %s)", video_id, audio_duration, audio_path.name)

    logger.info("Stock Video: Video ID %s audio duration is %.2fs (file: %s)", video_id, audio_duration, audio_path.name)

    # 2e. Subtitles are explicitly omitted for stock video flow
    srt_path: Path | None = None

    # 3. Locate thumbnail images: thumb_no_text (for 3s full intro) and thumb_path (for PiP)
    thumb_no_text_path: Path | None = None
    thumb_path: Path | None = None

    # 3a. Extract from generated script markers if present
    script_text = str(video.get("generated_script") or "")
    if script_text:
        match_no_text = re.search(r"### \[THUMBNAIL KHÔNG CHỮ\]\s*\[IMAGE_URL:/api/thumbnails/([^\]]+)\]", script_text)
        if match_no_text:
            candidate = THUMBNAILS_DIR / match_no_text.group(1).strip()
            if candidate.is_file():
                thumb_no_text_path = candidate

        match_with_text = re.search(r"### \[THUMBNAIL CÓ CHỮ\]\s*\[IMAGE_URL:/api/thumbnails/([^\]]+)\]", script_text)
        if match_with_text:
            candidate = THUMBNAILS_DIR / match_with_text.group(1).strip()
            if candidate.is_file():
                thumb_path = candidate

        if thumb_path is None:
            for m in re.finditer(r"\[IMAGE_URL:/api/thumbnails/([^\]]+)\]", script_text):
                candidate = THUMBNAILS_DIR / m.group(1).strip()
                if candidate.is_file():
                    thumb_path = candidate
                    break

    # 3b. Check standard filesystem filenames
    if thumb_no_text_path is None:
        for fname in [f"thumb_no_text_{video_id}.png", f"thumb_{video_id}_no_text.png"]:
            cand = THUMBNAILS_DIR / fname
            if cand.is_file():
                thumb_no_text_path = cand
                break

    if thumb_path is None:
        for fname in [f"thumb_with_text_{video_id}.png", f"thumb_{video_id}.png", f"thumb_no_text_{video_id}.png"]:
            cand = THUMBNAILS_DIR / fname
            if cand.is_file():
                thumb_path = cand
                break

    # 3c. Check DB artifacts
    if thumb_no_text_path is None:
        art_no_text = db.get_latest_video_artifact(video_id, "thumbnail_without_text", status="ready")
        if art_no_text and (art_no_text.get("path") or art_no_text.get("file_path")):
            cand = Path(art_no_text.get("path") or art_no_text.get("file_path")).resolve()
            if cand.is_file():
                thumb_no_text_path = cand

    if thumb_path is None:
        art_thumb = (
            db.get_latest_video_artifact(video_id, "thumbnail_with_text", status="ready")
            or db.get_latest_video_artifact(video_id, "thumbnail_without_text", status="ready")
        )
        if art_thumb and (art_thumb.get("path") or art_thumb.get("file_path")):
            cand = Path(art_thumb.get("path") or art_thumb.get("file_path")).resolve()
            if cand.is_file():
                thumb_path = cand

    # If thumb_no_text_path is not found, fallback to thumb_path
    if thumb_no_text_path is None:
        thumb_no_text_path = thumb_path

    # If still none, create a placeholder thumbnail
    if thumb_path is None or thumb_no_text_path is None:
        placeholder_thumb = THUMBNAILS_DIR / f"thumb_fallback_{video_id}.png"
        placeholder_thumb.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1280, 720), (30, 41, 59))
        draw = ImageDraw.Draw(img)
        draw.text((100, 320), title[:60], fill=(255, 255, 255))
        img.save(placeholder_thumb)
        if thumb_path is None:
            thumb_path = placeholder_thumb
        if thumb_no_text_path is None:
            thumb_no_text_path = placeholder_thumb

    # 4. Check Background Videos repository
    bg_custom_dir = snapshot.get("background_videos_dir") or snapshot.get("custom_stock_videos_path")
    available_videos = get_available_background_videos(bg_custom_dir)
    if not available_videos:
        raise StockVideoRenderError(
            f"Không tìm thấy video nền nào trong kho video: {bg_custom_dir or BACKGROUND_VIDEOS_DIR}. "
            f"Vui lòng thêm ít nhất 1 video MP4 vào thư mục."
        )

    cancel_check()
    progress("Đang chuẩn bị playlist video nền và bố cục ngẫu nhiên...", "stock_video_layout")

    # Audio starts after 3.0s full intro thumbnail
    intro_delay = 3.0
    total_duration = audio_duration + intro_delay

    playlist, video_durations = build_background_playlist(available_videos, total_duration)

    # 5. Corner layout (Default: bottom-left for thumbnail PiP, bottom-right for title)
    corner = str((snapshot.get("stock_video_settings") or {}).get("thumbnail_corner_mode") or "bottom_left").strip().lower()
    if corner not in {"bottom_right", "bottom_left", "top_right", "top_left"}:
        corner = "bottom_left"
    logger.info("Stock Video: Selected corner '%s' for Video ID %s", corner, video_id)

    # 6. Generate Pre-rendered Overlays (Full 3s Intro, PiP Thumbnail & Direct Title Overlay)
    scratch_dir = RENDERS_DIR / "temp" / f"video_{video_id}_{int(time.time())}"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # 6a. Full 1080p Intro Thumbnail (3s without text)
    thumb_full_path = scratch_dir / "thumb_full_1080p.png"
    with Image.open(thumb_no_text_path) as source_im:
        rgb_full = source_im.convert("RGB")
        resized_full = rgb_full.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
        resized_full.save(thumb_full_path, "PNG")

    # 6b. PiP Thumbnail (rounded, bordered, drop shadow)
    thumb_styled_path = scratch_dir / "thumb_styled.png"
    generate_thumbnail_overlay(thumb_path, thumb_styled_path, target_w=580, target_h=326)

    # 6c. Title text overlay (white fill, random-colored outline, drop shadow, no box)
    title_styled_path = scratch_dir / "title_styled.png"
    _, title_total_w, title_total_h, chosen_outline = generate_title_text_overlay(
        title,
        title_styled_path,
        max_width=1150,
        font_size=42,
    )

    # 7. Calculate Coordinates (Collision-free random Waveform, Thumb PiP bottom-left, Title bottom-right)
    with Image.open(thumb_styled_path) as im_t:
        thumb_total_w, thumb_total_h = im_t.size

    coords = calculate_layout_coordinates(
        corner=corner,
        thumb_w=thumb_total_w,
        thumb_h=thumb_total_h,
        card_w=title_total_w,
        card_h=title_total_h,
        wave_w=WAVE_DEFAULT_WIDTH,
        wave_h=WAVE_DEFAULT_HEIGHT,
        canvas_w=TARGET_WIDTH,
        canvas_h=TARGET_HEIGHT,
        margin=40,
    )

    # 8. Write Concat List
    concat_list_file = scratch_dir / "concat_videos.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for video_item in playlist:
            escaped_path = video_item.as_posix().replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")

    # 9. Prepare FFmpeg Filter Complex
    t_x, t_y = coords["thumb"]
    c_x, c_y = coords["card"]
    w_x, w_y = coords["wave"]

    chosen_wave_color = secrets.choice(WAVE_COLORS_PALETTE)
    bar_start_offset = (WAVE_BAR_PITCH - WAVE_BAR_WIDTH) // 2
    bar_end_offset = bar_start_offset + WAVE_BAR_WIDTH - 1

    filter_complex = (
        f"[0:v]settb=AVTB,setpts=N/({TARGET_FPS}*TB),fps={TARGET_FPS},"
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1,format=yuv420p[bg];"
        f"[1:a]adelay=delays=3000:all=1,asplit=2[a_delayed_wave][a_out];"
        f"[a_delayed_wave]showwaves=s={WAVE_BARS_COUNT}x{WAVE_DEFAULT_HEIGHT}:mode=cline:colors={chosen_wave_color}:scale=sqrt:draw=full:r={TARGET_FPS},"
        f"format=yuva420p,scale={WAVE_DEFAULT_WIDTH}:{WAVE_DEFAULT_HEIGHT}:flags=neighbor,format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(between(mod(X,{WAVE_BAR_PITCH}),{bar_start_offset},{bar_end_offset}),alpha(X,Y),0)',"
        f"settb=AVTB,setpts=PTS-STARTPTS[wave];"
        f"[bg][2:v]overlay=0:0:enable='between(t,0,3)':eof_action=repeat[v_intro];"
        f"[v_intro][wave]overlay={w_x}:{w_y}:enable='gte(t,3)':eof_action=pass[v_wave];"
        f"[v_wave][3:v]overlay={t_x}:{t_y}:enable='gte(t,3)':eof_action=repeat[v_thumb];"
        f"[v_thumb][4:v]overlay={c_x}:{c_y}:enable='gte(t,3)':eof_action=repeat[final_v]"
    )

    # 10. Target Output Path
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    slug = db.extract_generated_video_slug(
        script_text,
        default_title=title,
    ) or f"video_{video_id}"
    resolved_name = db.resolve_render_filename(video_id, slug, renders_dir=RENDERS_DIR)
    output_filename = f"{resolved_name}.mp4"
    output_path = RENDERS_DIR / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-fflags", "+genpts+igndts+discardcorrupt",
        "-avoid_negative_ts", "make_zero",
        "-err_detect", "ignore_err",
        "-reinit_filter", "0",
        "-f", "concat", "-safe", "0", "-auto_convert", "1", "-segment_time_metadata", "1", "-i", str(concat_list_file),
        "-i", str(audio_path),
        "-loop", "1", "-i", str(thumb_full_path),
        "-loop", "1", "-i", str(thumb_styled_path),
        "-loop", "1", "-i", str(title_styled_path),
        "-filter_complex", filter_complex,
        "-map", "[final_v]",
        "-map", "[a_out]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-fps_mode", "cfr",
        "-max_muxing_queue_size", "4096",
        "-threads", "0",
        "-t", f"{total_duration:.3f}",
        str(output_path),
    ]

    cancel_check()
    progress("Đang tiến hành encode MP4 (Stock Video Mode)...", "stock_video_encoding")

    logger.info("Running FFmpeg stock render: %s", " ".join(cmd[:10]))
    ffmpeg_log_path = scratch_dir / "ffmpeg_render.log"
    ffmpeg_log = open(ffmpeg_log_path, "w", encoding="utf-8", errors="replace")

    ffmpeg_extra_kwargs = {}
    if sys.platform == "win32" and hasattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS"):
        ffmpeg_extra_kwargs["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS

    proc = subprocess.Popen(
        cmd,
        stdout=ffmpeg_log,
        stderr=subprocess.STDOUT,
        **ffmpeg_extra_kwargs,
    )
    process_registry.register_process(f"video:{video_id}", proc)

    try:
        last_progress_time = time.time()
        while proc.poll() is None:
            cancel_check()
            if time.time() - last_progress_time > 8.0:
                last_progress_time = time.time()
                try:
                    if ffmpeg_log_path.exists():
                        with open(ffmpeg_log_path, "r", encoding="utf-8", errors="replace") as f_read:
                            lines = f_read.readlines()
                            for line in reversed(lines[-20:]):
                                match = re.search(r"time=(\d+:\d+:\d+(?:\.\d+)?)", line)
                                if match:
                                    current_time_str = match.group(1)
                                    tot_h = int(total_duration // 3600)
                                    tot_m = int((total_duration % 3600) // 60)
                                    tot_s = int(total_duration % 60)
                                    progress(
                                        f"Đang encode MP4 ({current_time_str} / {tot_h:02d}:{tot_m:02d}:{tot_s:02d})...",
                                        "stock_video_encoding",
                                    )
                                    break
                except Exception:
                    pass
            time.sleep(2.0)

        ffmpeg_log.flush()
        ffmpeg_log.close()
        process_registry.unregister_process(f"video:{video_id}", proc)

        if proc.returncode != 0:
            error_tail = ""
            saved_log_path = None
            try:
                if ffmpeg_log_path.exists():
                    raw_log = ffmpeg_log_path.read_text(encoding="utf-8", errors="replace")
                    # Save persistent debug log into data/logs
                    logs_dir = Path(scratch_dir.parent.parent.parent / "logs")
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    saved_log_path = logs_dir / f"ffmpeg_stock_error_video_{video_id}_{timestamp_str}.log"
                    saved_log_path.write_text(raw_log, encoding="utf-8")

                    lines = [line.strip() for line in raw_log.splitlines() if line.strip()]
                    err_lines = [
                        l for l in lines
                        if any(k in l.lower() for k in ["error", "fatal", "invalid", "conversion failed", "no such", "failed", "cannot"])
                        and not l.startswith("frame=")
                    ]
                    if err_lines:
                        error_tail = " | ".join(err_lines[-5:])
                    else:
                        error_tail = raw_log[-2000:].strip()
            except Exception as log_ex:
                logger.warning("Failed to parse error log: %s", log_ex)

            log_hint = f" (Log: {saved_log_path.name})" if saved_log_path else ""
            raise StockVideoRenderError(f"FFmpeg render lỗi (code {proc.returncode}): {error_tail}{log_hint}")
    except Exception:
        if proc.poll() is None:
            proc.kill()
        try:
            ffmpeg_log.close()
        except Exception:
            pass
        process_registry.unregister_process(f"video:{video_id}", proc)
        shutil.rmtree(scratch_dir, ignore_errors=True)
        raise

    # Clean up scratch temp files
    shutil.rmtree(scratch_dir, ignore_errors=True)

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise StockVideoRenderError(f"Không tạo được file MP4 đầu ra: {output_path}")

    logger.info("Stock Video rendered successfully: %s (%.2f MB)", output_path, output_path.stat().st_size / (1024 * 1024))

    # 12. Register Final Artifact in DB
    artifact = db.upsert_video_artifact(
        video_id=video_id,
        artifact_type="final_mp4",
        path=str(output_path.resolve()),
        content_hash=f"stock_{video_id}_{timestamp_str}",
        size_bytes=output_path.stat().st_size,
        duration_seconds=total_duration,
        mime_type="video/mp4",
        status="ready",
    )

    return {
        "artifact": artifact,
        "render_mode": "stock_video",
        "video_path": str(output_path),
        "captions_path": None,
        "duration": total_duration,
        "corner": corner,
        "icon": "center_waveform",
        "title_outline_color": list(chosen_outline),
        "wave_color": chosen_wave_color,
        "wave_coords": [w_x, w_y],
    }
