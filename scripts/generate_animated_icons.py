"""Script to generate 50 beautiful, diverse, transparent looping animated GIF stickers."""

import math
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ICONS_DIR = Path("e:/yt/Tool/Auto_YT/Tool-auto-login-GPT/data/animated_icons")
ICONS_DIR.mkdir(parents=True, exist_ok=True)

N_FRAMES = 24
SIZE = 140
CENTER = SIZE // 2


def save_gif(frames, filename, duration=45):
    filepath = ICONS_DIR / filename
    frames[0].save(
        filepath,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,
    )
    print(f"[OK] Generated {filename}")


# ==========================================
# 1. WEATHER & NATURE (1-7)
# ==========================================

# 1. sun_smiling.gif
def make_sun_smiling():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.08 * math.sin(t * 2 * math.pi)
        rot = t * 2 * math.pi / 8
        for r_idx in range(8):
            ang = rot + r_idx * (2 * math.pi / 8)
            rlen = 48 + 8 * math.sin(t * 2 * math.pi + r_idx)
            rx, ry = CENTER + rlen * math.cos(ang), CENTER + rlen * math.sin(ang)
            d.line([(CENTER, CENTER), (rx, ry)], fill=(255, 170, 0, 255), width=7)
            d.ellipse([rx - 4, ry - 4, rx + 4, ry + 4], fill=(255, 150, 0, 255))
        r = int(28 * scale)
        d.ellipse([CENTER - r, CENTER - r, CENTER + r, CENTER + r], fill=(255, 215, 0, 255), outline=(255, 140, 0, 255), width=3)
        d.ellipse([CENTER - 13, CENTER - 8, CENTER - 7, CENTER], fill=(80, 40, 10, 255))
        d.ellipse([CENTER + 7, CENTER - 8, CENTER + 13, CENTER], fill=(80, 40, 10, 255))
        d.ellipse([CENTER - 18, CENTER + 2, CENTER - 10, CENTER + 8], fill=(255, 105, 135, 200))
        d.ellipse([CENTER + 10, CENTER + 2, CENTER + 18, CENTER + 8], fill=(255, 105, 135, 200))
        d.arc([CENTER - 8, CENTER - 2, CENTER + 8, CENTER + 12], 10, 170, fill=(120, 40, 10, 255), width=3)
        frames.append(im)
    save_gif(frames, "01_sun_smiling.gif")


# 2. rain_cloud.gif
def make_rain_cloud():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        # Raindrops
        for drop_i in range(4):
            dx = 35 + drop_i * 22
            dt_drop = (t + drop_i * 0.25) % 1.0
            dy = 75 + dt_drop * 45
            d.line([(dx, dy), (dx - 4, dy + 10)], fill=(56, 189, 248, int(255 * (1 - dt_drop * 0.5))), width=3)
        # Cloud
        d.ellipse([30, 45, 75, 80], fill=(147, 197, 253, 255))
        d.ellipse([65, 35, 110, 80], fill=(191, 219, 254, 255))
        d.ellipse([45, 30, 95, 75], fill=(224, 242, 254, 255))
        d.rounded_rectangle([32, 55, 108, 80], radius=8, fill=(191, 219, 254, 255))
        frames.append(im)
    save_gif(frames, "02_rain_cloud.gif")


# 3. thunder_lightning.gif
def make_thunder_lightning():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        flash = math.sin(t * 4 * math.pi) > 0.2
        # Dark cloud
        c_fill = (100, 116, 139, 255) if not flash else (148, 163, 184, 255)
        d.ellipse([28, 35, 75, 75], fill=c_fill)
        d.ellipse([65, 25, 112, 75], fill=c_fill)
        d.ellipse([45, 20, 95, 70], fill=c_fill)
        d.rounded_rectangle([30, 48, 110, 75], radius=8, fill=c_fill)
        # Lightning bolt
        if flash:
            bolt = [(68, 65), (55, 95), (68, 95), (58, 125), (85, 90), (72, 90), (82, 65)]
            d.polygon(bolt, fill=(250, 204, 21, 255), outline=(234, 88, 12, 255))
        frames.append(im)
    save_gif(frames, "03_thunder_lightning.gif")


# 4. rainbow_glow.gif
def make_rainbow_glow():
    frames = []
    colors = [(239, 68, 68), (249, 115, 22), (234, 179, 8), (34, 197, 94), (59, 130, 246), (168, 85, 247)]
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        for i, col in enumerate(colors):
            r = 55 - i * 5
            d.arc([CENTER - r, CENTER - r + 15, CENTER + r, CENTER + r + 15], 180, 360, fill=(*col, 255), width=5)
        # Sparkle
        sp_x = CENTER + int(35 * math.cos(t * 2 * math.pi))
        sp_y = 50 + int(10 * math.sin(t * 2 * math.pi))
        d.ellipse([sp_x - 4, sp_y - 4, sp_x + 4, sp_y + 4], fill=(255, 255, 255, 240))
        frames.append(im)
    save_gif(frames, "04_rainbow_glow.gif")


# 5. falling_leaves.gif
def make_falling_leaves():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        for i, (base_x, color) in enumerate([(45, (234, 88, 12)), (95, (217, 119, 6)), (70, (220, 38, 38))]):
            st = (t + i * 0.33) % 1.0
            ly = int(20 + st * 95)
            lx = int(base_x + 18 * math.sin(st * 3 * math.pi))
            rot_scale = max(0.2, abs(math.sin(st * 4 * math.pi)))
            d.ellipse([lx - 10, ly - int(14 * rot_scale), lx + 10, ly + int(14 * rot_scale)], fill=(*color, 255), outline=(120, 53, 15, 255), width=1)
            d.line([(lx, ly - 8), (lx, ly + 8)], fill=(120, 53, 15, 255), width=1)
        frames.append(im)
    save_gif(frames, "05_falling_leaves.gif")


# 6. snowflake_spin.gif
def make_snowflake_spin():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        rot = t * 2 * math.pi / 6
        for arm in range(6):
            ang = rot + arm * (math.pi / 3)
            ex = CENTER + 45 * math.cos(ang)
            ey = CENTER + 45 * math.sin(ang)
            d.line([(CENTER, CENTER), (ex, ey)], fill=(186, 230, 253, 255), width=4)
            # branches
            for b_dist in [20, 35]:
                bx = CENTER + b_dist * math.cos(ang)
                by = CENTER + b_dist * math.sin(ang)
                b1x = bx + 10 * math.cos(ang + math.pi / 4)
                b1y = by + 10 * math.sin(ang + math.pi / 4)
                b2x = bx + 10 * math.cos(ang - math.pi / 4)
                b2y = by + 10 * math.sin(ang - math.pi / 4)
                d.line([(bx, by), (b1x, b1y)], fill=(186, 230, 253, 255), width=3)
                d.line([(bx, by), (b2x, b2y)], fill=(186, 230, 253, 255), width=3)
        d.ellipse([CENTER - 6, CENTER - 6, CENTER + 6, CENTER + 6], fill=(255, 255, 255, 255))
        frames.append(im)
    save_gif(frames, "06_snowflake_spin.gif")


# 7. blossom_flower.gif
def make_blossom_flower():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.1 * math.sin(t * 2 * math.pi)
        rot = t * 2 * math.pi / 5
        for p in range(5):
            ang = rot + p * (2 * math.pi / 5)
            px = CENTER + int(24 * scale * math.cos(ang))
            py = CENTER + int(24 * scale * math.sin(ang))
            d.ellipse([px - int(16 * scale), py - int(16 * scale), px + int(16 * scale), py + int(16 * scale)], fill=(244, 114, 182, 255), outline=(219, 39, 119, 255), width=2)
        d.ellipse([CENTER - 12, CENTER - 12, CENTER + 12, CENTER + 12], fill=(253, 224, 71, 255), outline=(234, 179, 8, 255), width=2)
        frames.append(im)
    save_gif(frames, "07_blossom_flower.gif")


# ==========================================
# 2. MUSIC & AUDIO (8-14)
# ==========================================

# 8. music_equalizer.gif
def make_music_equalizer():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        num_bars = 5
        bar_w = 14
        bar_gap = 7
        total_w = num_bars * bar_w + (num_bars - 1) * bar_gap
        start_x = (SIZE - total_w) // 2
        for i in range(num_bars):
            h_phase = math.sin(t * 4 * math.pi + i * 1.2)
            h = int(25 + 40 * (0.5 + 0.5 * h_phase))
            bx = start_x + i * (bar_w + bar_gap)
            by = CENTER + 35 - h
            col = (14, 165, 233) if i % 2 == 0 else (168, 85, 247)
            d.rounded_rectangle([bx, by, bx + bar_w, CENTER + 35], radius=4, fill=(*col, 255))
        frames.append(im)
    save_gif(frames, "08_music_equalizer.gif")


# 9. cassette_tape.gif
def make_cassette_tape():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.rounded_rectangle([25, 40, 115, 100], radius=8, fill=(30, 41, 59, 255), outline=(56, 189, 248, 255), width=3)
        d.rectangle([45, 55, 95, 85], fill=(241, 245, 249, 255))
        for rx, ry in [(56, 70), (84, 70)]:
            d.ellipse([rx - 10, ry - 10, rx + 10, ry + 10], fill=(15, 23, 42, 255), outline=(203, 213, 225, 255), width=2)
            ang = t * 2 * math.pi
            d.line([(rx, ry), (rx + 8 * math.cos(ang), ry + 8 * math.sin(ang))], fill=(255, 255, 255, 255), width=2)
        frames.append(im)
    save_gif(frames, "09_cassette_tape.gif")


# 10. microphone_onair.gif
def make_microphone_onair():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        pulse = math.sin(t * 2 * math.pi) > 0
        # Mic body
        d.rounded_rectangle([CENTER - 14, 30, CENTER + 14, 75], radius=14, fill=(226, 232, 240, 255), outline=(100, 116, 139, 255), width=3)
        # Mesh lines
        for y in [42, 52, 62]:
            d.line([(CENTER - 12, y), (CENTER + 12, y)], fill=(148, 163, 184, 255), width=2)
        # Stand
        d.arc([CENTER - 22, 50, CENTER + 22, 85], 0, 180, fill=(100, 116, 139, 255), width=4)
        d.line([(CENTER, 85), (CENTER, 108)], fill=(100, 116, 139, 255), width=4)
        d.line([(CENTER - 20, 108), (CENTER + 20, 108)], fill=(100, 116, 139, 255), width=5)
        # On Air Light
        col = (239, 68, 68, 255) if pulse else (120, 20, 20, 255)
        d.ellipse([CENTER - 4, 34, CENTER + 4, 42], fill=col)
        frames.append(im)
    save_gif(frames, "10_microphone_onair.gif")


# 11. audio_speaker.gif
def make_audio_speaker():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.12 * max(0, math.sin(t * 2 * math.pi))
        # Speaker box
        d.rounded_rectangle([35, 25, 105, 115], radius=8, fill=(24, 24, 27, 255), outline=(82, 82, 91, 255), width=3)
        # Tweeter
        d.ellipse([CENTER - 10, 36, CENTER + 10, 56], fill=(39, 39, 42, 255), outline=(161, 161, 170, 255), width=2)
        # Woofer pulsing
        wr = int(22 * scale)
        d.ellipse([CENTER - wr, 85 - wr, CENTER + wr, 85 + wr], fill=(220, 38, 38, 255), outline=(254, 202, 202, 255), width=3)
        frames.append(im)
    save_gif(frames, "11_audio_speaker.gif")


# 12. vinyl_rainbow.gif
def make_vinyl_rainbow():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.ellipse([CENTER - 48, CENTER - 48, CENTER + 48, CENTER + 48], fill=(24, 24, 27, 255), outline=(63, 63, 70, 255), width=2)
        for gr in [40, 32, 24]:
            d.ellipse([CENTER - gr, CENTER - gr, CENTER + gr, CENTER + gr], outline=(39, 39, 42, 255), width=1)
        ang = t * 2 * math.pi
        d.line([(CENTER, CENTER), (CENTER + 32 * math.cos(ang), CENTER + 32 * math.sin(ang))], fill=(56, 189, 248, 120), width=6)
        d.ellipse([CENTER - 14, CENTER - 14, CENTER + 14, CENTER + 14], fill=(236, 72, 153, 255))
        d.ellipse([CENTER - 4, CENTER - 4, CENTER + 4, CENTER + 4], fill=(255, 255, 255, 255))
        frames.append(im)
    save_gif(frames, "12_vinyl_rainbow.gif")


# 13. treble_clef.gif
def make_treble_clef():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.08 * math.sin(t * 2 * math.pi)
        # Golden treble clef body
        d.ellipse([CENTER - 18, 55, CENTER + 18, 85], outline=(234, 179, 8, 255), width=4)
        d.arc([CENTER - 12, 30, CENTER + 12, 60], 90, 270, fill=(234, 179, 8, 255), width=4)
        d.line([(CENTER + 2, 25), (CENTER + 2, 105)], fill=(234, 179, 8, 255), width=4)
        d.ellipse([CENTER - 8, 100, CENTER + 6, 112], fill=(234, 179, 8, 255))
        # Floating small sparkles
        for sp in range(3):
            spt = (t + sp * 0.33) % 1.0
            sx = int(CENTER + 30 + 10 * math.sin(spt * 2 * math.pi))
            sy = int(90 - spt * 60)
            d.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=(250, 204, 21, int(255 * (1 - spt))))
        frames.append(im)
    save_gif(frames, "13_treble_clef.gif")


# 14. headphones_neon.gif
def make_headphones_neon():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.06 * math.sin(t * 2 * math.pi)
        d.arc([CENTER - 36 * scale, CENTER - 38 * scale, CENTER + 36 * scale, CENTER + 30 * scale], 180, 0, fill=(168, 85, 247, 255), width=6)
        d.rounded_rectangle([CENTER - 44 * scale, CENTER - 5 * scale, CENTER - 28 * scale, CENTER + 30 * scale], radius=6, fill=(56, 189, 248, 255), outline=(14, 165, 233, 255), width=2)
        d.rounded_rectangle([CENTER + 28 * scale, CENTER - 5 * scale, CENTER + 44 * scale, CENTER + 30 * scale], radius=6, fill=(56, 189, 248, 255), outline=(14, 165, 233, 255), width=2)
        frames.append(im)
    save_gif(frames, "14_headphones_neon.gif")


# ==========================================
# 3. EMOTION & REACTIONS (15-22)
# ==========================================

# 15. heart_beating.gif
def make_heart_beating():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        beat = math.sin(t * 2 * math.pi)
        scale = 1.0 + 0.18 * max(0, beat)
        d.ellipse([CENTER - 24 * scale, CENTER - 20 * scale, CENTER, CENTER + 6 * scale], fill=(239, 68, 68, 255))
        d.ellipse([CENTER, CENTER - 20 * scale, CENTER + 24 * scale, CENTER + 6 * scale], fill=(239, 68, 68, 255))
        d.polygon([(CENTER - 22 * scale, CENTER - 2 * scale), (CENTER + 22 * scale, CENTER - 2 * scale), (CENTER, CENTER + 28 * scale)], fill=(239, 68, 68, 255))
        d.ellipse([CENTER - 16 * scale, CENTER - 14 * scale, CENTER - 8 * scale, CENTER - 6 * scale], fill=(255, 255, 255, 180))
        frames.append(im)
    save_gif(frames, "15_heart_beating.gif")


# 16. fire_passion.gif
def make_fire_passion():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wobble = 6 * math.sin(t * 2 * math.pi)
        flame_outer = [(CENTER - 28, CENTER + 30), (CENTER - 32 + wobble, CENTER), (CENTER - 15 + wobble, CENTER - 30), (CENTER + wobble, CENTER - 48), (CENTER + 18 - wobble, CENTER - 25), (CENTER + 30 - wobble, CENTER + 5), (CENTER + 25, CENTER + 30)]
        d.polygon(flame_outer, fill=(249, 115, 22, 255))
        flame_inner = [(CENTER - 16, CENTER + 30), (CENTER - 18 + wobble * 0.6, CENTER + 10), (CENTER - 8 + wobble * 0.6, CENTER - 12), (CENTER + wobble * 0.6, CENTER - 28), (CENTER + 10 - wobble * 0.6, CENTER - 10), (CENTER + 16 - wobble * 0.6, CENTER + 15), (CENTER + 14, CENTER + 30)]
        d.polygon(flame_inner, fill=(253, 224, 71, 255))
        frames.append(im)
    save_gif(frames, "16_fire_passion.gif")


# 17. crying_teardrop.gif
def make_crying_teardrop():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.ellipse([CENTER - 38, CENTER - 38, CENTER + 38, CENTER + 38], fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=3)
        # Sad eyes
        d.arc([CENTER - 24, CENTER - 18, CENTER - 8, CENTER - 6], 180, 360, fill=(30, 41, 59, 255), width=3)
        d.arc([CENTER + 8, CENTER - 18, CENTER + 24, CENTER - 6], 180, 360, fill=(30, 41, 59, 255), width=3)
        # Sad mouth
        d.arc([CENTER - 14, CENTER + 14, CENTER + 14, CENTER + 32], 180, 360, fill=(30, 41, 59, 255), width=3)
        # Falling tears
        for eye_x in [CENTER - 16, CENTER + 16]:
            dt_tear = (t + (0.5 if eye_x > CENTER else 0.0)) % 1.0
            ty = CENTER - 5 + int(dt_tear * 35)
            d.ellipse([eye_x - 4, ty - 6, eye_x + 4, ty + 6], fill=(56, 189, 248, 220))
        frames.append(im)
    save_gif(frames, "17_crying_teardrop.gif")


# 18. laughing_joy.gif
def make_laughing_joy():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        bounce_y = int(4 * math.sin(t * 2 * math.pi))
        cy = CENTER + bounce_y
        d.ellipse([CENTER - 38, cy - 38, CENTER + 38, cy + 38], fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=3)
        # XD eyes
        d.line([(CENTER - 24, cy - 14), (CENTER - 10, cy - 6)], fill=(30, 41, 59, 255), width=3)
        d.line([(CENTER - 24, cy + 2), (CENTER - 10, cy - 6)], fill=(30, 41, 59, 255), width=3)
        d.line([(CENTER + 24, cy - 14), (CENTER + 10, cy - 6)], fill=(30, 41, 59, 255), width=3)
        d.line([(CENTER + 24, cy + 2), (CENTER + 10, cy - 6)], fill=(30, 41, 59, 255), width=3)
        # Big open smile
        d.pieslice([CENTER - 20, cy - 4, CENTER + 20, cy + 26], 0, 180, fill=(185, 28, 28, 255))
        frames.append(im)
    save_gif(frames, "18_laughing_joy.gif")


# 19. broken_heart.gif
def make_broken_heart():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        split = int(4 * abs(math.sin(t * 2 * math.pi)))
        # Left half
        d.pieslice([CENTER - 32 - split, CENTER - 26, CENTER + 6 - split, CENTER + 12], 180, 0, fill=(225, 29, 72, 255))
        d.polygon([(CENTER - 30 - split, CENTER - 6), (CENTER + 4 - split, CENTER - 6), (CENTER - split, CENTER + 26)], fill=(225, 29, 72, 255))
        # Right half
        d.pieslice([CENTER - 6 + split, CENTER - 26, CENTER + 32 + split, CENTER + 12], 180, 0, fill=(225, 29, 72, 255))
        d.polygon([(CENTER - 4 + split, CENTER - 6), (CENTER + 30 + split, CENTER - 6), (CENTER + split, CENTER + 26)], fill=(225, 29, 72, 255))
        frames.append(im)
    save_gif(frames, "19_broken_heart.gif")


# 20. sparkling_eyes.gif
def make_sparkling_eyes():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.ellipse([CENTER - 38, CENTER - 38, CENTER + 38, CENTER + 38], fill=(254, 240, 138, 255), outline=(234, 179, 8, 255), width=3)
        # Star eyes rotating
        for ex in [CENTER - 16, CENTER + 16]:
            star_pts = []
            for p in range(8):
                ang = t * 2 * math.pi + p * math.pi / 4
                rad = 12 if p % 2 == 0 else 4
                star_pts.append((ex + rad * math.cos(ang), CENTER - 8 + rad * math.sin(ang)))
            d.polygon(star_pts, fill=(234, 88, 12, 255))
        d.arc([CENTER - 14, CENTER + 4, CENTER + 14, CENTER + 22], 10, 170, fill=(120, 53, 15, 255), width=3)
        frames.append(im)
    save_gif(frames, "20_sparkling_eyes.gif")


# 21. clapping_hands.gif
def make_clapping_hands():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        clap = math.sin(t * 4 * math.pi)
        ang_left = 30 + 15 * clap
        d.rounded_rectangle([CENTER - 30, CENTER - 10, CENTER - 5, CENTER + 30], radius=8, fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=2)
        d.rounded_rectangle([CENTER + 5, CENTER - 10, CENTER + 30, CENTER + 30], radius=8, fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=2)
        # Spark burst
        if clap > 0.5:
            d.line([(CENTER, CENTER - 20), (CENTER, CENTER - 32)], fill=(234, 88, 12, 255), width=3)
            d.line([(CENTER - 10, CENTER - 18), (CENTER - 18, CENTER - 26)], fill=(234, 88, 12, 255), width=3)
            d.line([(CENTER + 10, CENTER - 18), (CENTER + 18, CENTER - 26)], fill=(234, 88, 12, 255), width=3)
        frames.append(im)
    save_gif(frames, "21_clapping_hands.gif")


# 22. thumbs_up.gif
def make_thumbs_up():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.1 * math.sin(t * 2 * math.pi)
        # Fist
        d.rounded_rectangle([CENTER - 20 * scale, CENTER - 5 * scale, CENTER + 20 * scale, CENTER + 30 * scale], radius=8, fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=3)
        # Thumb
        d.rounded_rectangle([CENTER - 18 * scale, CENTER - 32 * scale, CENTER - 2 * scale, CENTER], radius=8, fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=3)
        frames.append(im)
    save_gif(frames, "22_thumbs_up.gif")


# ==========================================
# 4. COZY LIFE & OBJECTS (23-30)
# ==========================================

# 23. coffee_steam.gif
def make_coffee_steam():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        for s_idx in range(3):
            sx_base = CENTER - 18 + s_idx * 18
            st = (t + s_idx * 0.33) % 1.0
            sy = CENTER - 5 - st * 40
            sx = sx_base + 8 * math.sin(st * 3 * math.pi)
            s_alpha = int(220 * math.sin(st * math.pi))
            if s_alpha > 15:
                d.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=(220, 220, 240, s_alpha))
        d.rounded_rectangle([CENTER - 28, CENTER + 2, CENTER + 20, CENTER + 45], radius=6, fill=(245, 158, 11, 255), outline=(217, 119, 6, 255), width=3)
        d.arc([CENTER + 12, CENTER + 10, CENTER + 38, CENTER + 36], 270, 90, fill=(217, 119, 6, 255), width=5)
        d.ellipse([CENTER - 38, CENTER + 42, CENTER + 30, CENTER + 52], fill=(251, 191, 36, 255), outline=(217, 119, 6, 255), width=2)
        frames.append(im)
    save_gif(frames, "23_coffee_steam.gif")


# 24. tea_pot.gif
def make_tea_pot():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.ellipse([CENTER - 30, CENTER - 10, CENTER + 25, CENTER + 40], fill=(20, 184, 166, 255), outline=(13, 148, 136, 255), width=3)
        d.rounded_rectangle([CENTER - 18, CENTER - 20, CENTER + 12, CENTER - 10], radius=4, fill=(13, 148, 136, 255))
        d.arc([CENTER - 45, CENTER - 5, CENTER - 25, CENTER + 25], 90, 270, fill=(13, 148, 136, 255), width=4)
        # Spout
        d.polygon([(CENTER + 20, CENTER + 5), (CENTER + 42, CENTER - 15), (CENTER + 42, CENTER - 8), (CENTER + 25, CENTER + 20)], fill=(13, 148, 136, 255))
        frames.append(im)
    save_gif(frames, "24_tea_pot.gif")


# 25. book_reading.gif
def make_book_reading():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        page_flip = math.sin(t * 2 * math.pi)
        # Book base
        d.polygon([(CENTER, CENTER + 20), (CENTER - 45, CENTER + 10), (CENTER - 45, CENTER - 20), (CENTER, CENTER - 10)], fill=(241, 245, 249, 255), outline=(71, 85, 105, 255))
        d.polygon([(CENTER, CENTER + 20), (CENTER + 45, CENTER + 10), (CENTER + 45, CENTER - 20), (CENTER, CENTER - 10)], fill=(241, 245, 249, 255), outline=(71, 85, 105, 255))
        # Flipping page
        flip_x = CENTER + int(35 * page_flip)
        d.polygon([(CENTER, CENTER + 20), (flip_x, CENTER - 15), (flip_x, CENTER - 35), (CENTER, CENTER - 10)], fill=(255, 255, 255, 255), outline=(148, 163, 184, 255))
        frames.append(im)
    save_gif(frames, "25_book_reading.gif")


# 26. candle_flame.gif
def make_candle_flame():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wobble = 4 * math.sin(t * 4 * math.pi)
        # Candle base
        d.rounded_rectangle([CENTER - 14, CENTER - 5, CENTER + 14, CENTER + 45], radius=4, fill=(244, 63, 94, 255), outline=(190, 18, 60, 255), width=2)
        # Wick
        d.line([(CENTER, CENTER - 5), (CENTER, CENTER - 14)], fill=(30, 41, 59, 255), width=2)
        # Flame
        flame = [(CENTER - 8 + wobble, CENTER - 14), (CENTER + wobble, CENTER - 36), (CENTER + 8 + wobble, CENTER - 14)]
        d.polygon(flame, fill=(250, 204, 21, 255), outline=(234, 88, 12, 255))
        frames.append(im)
    save_gif(frames, "26_candle_flame.gif")


# 27. hourglass_sand.gif
def make_hourglass_sand():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        # Glass frame
        d.polygon([(CENTER - 25, 30), (CENTER + 25, 30), (CENTER + 4, CENTER), (CENTER + 25, 110), (CENTER - 25, 110), (CENTER - 4, CENTER)], outline=(180, 83, 9, 255), width=3)
        # Sand flow stream
        d.line([(CENTER, CENTER - 10), (CENTER, CENTER + 30)], fill=(245, 158, 11, 255), width=2)
        # Bottom sand accumulating
        h_sand = int(10 + 12 * t)
        d.polygon([(CENTER - 20, 108), (CENTER + 20, 108), (CENTER, 108 - h_sand)], fill=(245, 158, 11, 255))
        frames.append(im)
    save_gif(frames, "27_hourglass_sand.gif")


# 28. alarm_clock.gif
def make_alarm_clock():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        shake = int(3 * math.sin(t * 8 * math.pi))
        cx = CENTER + shake
        # Bells
        d.pieslice([cx - 32, 28, cx - 12, 48], 180, 0, fill=(234, 179, 8, 255))
        d.pieslice([cx + 12, 28, cx + 32, 48], 180, 0, fill=(234, 179, 8, 255))
        # Body
        d.ellipse([cx - 32, 40, cx + 32, 104], fill=(239, 68, 68, 255), outline=(185, 28, 28, 255), width=3)
        d.ellipse([cx - 24, 48, cx + 24, 96], fill=(255, 255, 255, 255))
        # Clock hands
        d.line([(cx, 72), (cx, 56)], fill=(30, 41, 59, 255), width=3)
        d.line([(cx, 72), (cx + 12, 72)], fill=(30, 41, 59, 255), width=3)
        frames.append(im)
    save_gif(frames, "28_alarm_clock.gif")


# 29. lightbulb_idea.gif
def make_lightbulb_idea():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        glow = math.sin(t * 2 * math.pi) > 0
        fill_col = (253, 224, 71, 255) if glow else (203, 213, 225, 255)
        # Bulb
        d.ellipse([CENTER - 26, 30, CENTER + 26, 82], fill=fill_col, outline=(202, 138, 4, 255), width=3)
        d.polygon([(CENTER - 16, 75), (CENTER + 16, 75), (CENTER + 12, 95), (CENTER - 12, 95)], fill=(148, 163, 184, 255))
        if glow:
            for ray in range(6):
                ang = ray * math.pi / 3
                rx1 = CENTER + 36 * math.cos(ang)
                ry1 = 56 + 36 * math.sin(ang)
                rx2 = CENTER + 48 * math.cos(ang)
                ry2 = 56 + 48 * math.sin(ang)
                d.line([(rx1, ry1), (rx2, ry2)], fill=(234, 179, 8, 255), width=3)
        frames.append(im)
    save_gif(frames, "29_lightbulb_idea.gif")


# 30. vintage_radio.gif
def make_vintage_radio():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.line([(CENTER - 20, 50), (CENTER - 38, 25)], fill=(100, 116, 139, 255), width=3)
        d.rounded_rectangle([25, 50, 115, 105], radius=8, fill=(180, 83, 9, 255), outline=(120, 53, 15, 255), width=3)
        # Speaker grill
        d.ellipse([35, 60, 75, 100], fill=(69, 26, 3, 255), outline=(217, 119, 6, 255), width=2)
        # Tuner dial
        d.rectangle([82, 62, 105, 78], fill=(254, 240, 138, 255))
        d.ellipse([88, 85, 98, 95], fill=(217, 119, 6, 255))
        frames.append(im)
    save_gif(frames, "30_vintage_radio.gif")


# ==========================================
# 5. ANIMALS & CUTE MASCOTS (31-38)
# ==========================================

# 31. cat_paw.gif
def make_cat_paw():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wave_y = int(6 * math.sin(t * 2 * math.pi))
        cy = CENTER + wave_y
        d.ellipse([CENTER - 24, cy - 10, CENTER + 24, cy + 30], fill=(255, 255, 255, 255), outline=(226, 232, 240, 255), width=2)
        d.ellipse([CENTER - 16, cy + 2, CENTER + 16, cy + 24], fill=(244, 114, 182, 255))
        for px, py in [(CENTER - 18, cy - 12), (CENTER - 6, cy - 18), (CENTER + 6, cy - 18), (CENTER + 18, cy - 12)]:
            d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(244, 114, 182, 255))
        frames.append(im)
    save_gif(frames, "31_cat_paw.gif")


# 32. cat_nodding.gif
def make_cat_nodding():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        nod_y = int(8 * math.sin(t * 2 * math.pi))
        cy = CENTER + nod_y
        # Ears
        d.polygon([(CENTER - 28, cy - 15), (CENTER - 36, cy - 40), (CENTER - 10, cy - 25)], fill=(254, 205, 211, 255), outline=(244, 63, 94, 255))
        d.polygon([(CENTER + 28, cy - 15), (CENTER + 36, cy - 40), (CENTER + 10, cy - 25)], fill=(254, 205, 211, 255), outline=(244, 63, 94, 255))
        # Face
        d.ellipse([CENTER - 32, cy - 25, CENTER + 32, cy + 25], fill=(255, 255, 255, 255), outline=(203, 213, 225, 255), width=2)
        # Eyes
        d.ellipse([CENTER - 16, cy - 6, CENTER - 8, cy + 2], fill=(30, 41, 59, 255))
        d.ellipse([CENTER + 8, cy - 6, CENTER + 16, cy + 2], fill=(30, 41, 59, 255))
        # Nose
        d.polygon([(CENTER - 4, cy + 6), (CENTER + 4, cy + 6), (CENTER, cy + 10)], fill=(244, 114, 182, 255))
        frames.append(im)
    save_gif(frames, "32_cat_nodding.gif")


# 33. dog_tail_wag.gif
def make_dog_tail_wag():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wag = 14 * math.sin(t * 4 * math.pi)
        # Tail
        d.line([(CENTER - 20, CENTER + 15), (CENTER - 45, CENTER - 10 + wag)], fill=(180, 83, 9, 255), width=8)
        # Dog body
        d.ellipse([CENTER - 30, CENTER - 10, CENTER + 25, CENTER + 35], fill=(217, 119, 6, 255))
        # Head
        d.ellipse([CENTER + 5, CENTER - 25, CENTER + 40, CENTER + 15], fill=(245, 158, 11, 255))
        # Ear
        d.ellipse([CENTER + 2, CENTER - 15, CENTER + 18, CENTER + 15], fill=(180, 83, 9, 255))
        frames.append(im)
    save_gif(frames, "33_dog_tail_wag.gif")


# 34. butterfly_flight.gif
def make_butterfly_flight():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wing_w = int(35 * abs(math.cos(t * 2 * math.pi)))
        # Left wings
        d.ellipse([CENTER - wing_w, CENTER - 30, CENTER, CENTER + 5], fill=(56, 189, 248, 220), outline=(2, 132, 199, 255))
        d.ellipse([CENTER - int(wing_w * 0.8), CENTER, CENTER, CENTER + 25], fill=(168, 85, 247, 220), outline=(126, 34, 206, 255))
        # Right wings
        d.ellipse([CENTER, CENTER - 30, CENTER + wing_w, CENTER + 5], fill=(56, 189, 248, 220), outline=(2, 132, 199, 255))
        d.ellipse([CENTER, CENTER, CENTER + int(wing_w * 0.8), CENTER + 25], fill=(168, 85, 247, 220), outline=(126, 34, 206, 255))
        # Body
        d.rounded_rectangle([CENTER - 3, CENTER - 20, CENTER + 3, CENTER + 20], radius=3, fill=(15, 23, 42, 255))
        frames.append(im)
    save_gif(frames, "34_butterfly_flight.gif")


# 35. owl_blinking.gif
def make_owl_blinking():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        blink = math.sin(t * 2 * math.pi) > 0.8
        # Owl body
        d.ellipse([CENTER - 30, CENTER - 35, CENTER + 30, CENTER + 35], fill=(120, 53, 15, 255))
        # Eyes
        for ex in [CENTER - 14, CENTER + 14]:
            d.ellipse([ex - 12, CENTER - 18, ex + 12, CENTER + 6], fill=(254, 240, 138, 255))
            if blink:
                d.line([(ex - 10, CENTER - 6), (ex + 10, CENTER - 6)], fill=(30, 41, 59, 255), width=3)
            else:
                d.ellipse([ex - 6, CENTER - 12, ex + 6, CENTER], fill=(30, 41, 59, 255))
        # Beak
        d.polygon([(CENTER - 5, CENTER + 2), (CENTER + 5, CENTER + 2), (CENTER, CENTER + 12)], fill=(245, 158, 11, 255))
        frames.append(im)
    save_gif(frames, "35_owl_blinking.gif")


# 36. penguin_waddle.gif
def make_penguin_waddle():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        tilt = 6 * math.sin(t * 2 * math.pi)
        # Body
        d.ellipse([CENTER - 28 + tilt, CENTER - 35, CENTER + 28 + tilt, CENTER + 35], fill=(15, 23, 42, 255))
        # Belly
        d.ellipse([CENTER - 18 + tilt, CENTER - 20, CENTER + 18 + tilt, CENTER + 30], fill=(255, 255, 255, 255))
        # Beak
        d.polygon([(CENTER - 6 + tilt, CENTER - 15), (CENTER + 6 + tilt, CENTER - 15), (CENTER + tilt, CENTER - 8)], fill=(245, 158, 11, 255))
        # Feet
        d.ellipse([CENTER - 20, CENTER + 30, CENTER - 4, CENTER + 42], fill=(245, 158, 11, 255))
        d.ellipse([CENTER + 4, CENTER + 30, CENTER + 20, CENTER + 42], fill=(245, 158, 11, 255))
        frames.append(im)
    save_gif(frames, "36_penguin_waddle.gif")


# 37. bunny_ears.gif
def make_bunny_ears():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wiggle = 5 * math.sin(t * 2 * math.pi)
        # Ears
        d.ellipse([CENTER - 22 + wiggle, 18, CENTER - 6 + wiggle, 65], fill=(255, 255, 255, 255), outline=(244, 114, 182, 255), width=2)
        d.ellipse([CENTER + 6 - wiggle, 18, CENTER + 22 - wiggle, 65], fill=(255, 255, 255, 255), outline=(244, 114, 182, 255), width=2)
        # Head
        d.ellipse([CENTER - 28, 50, CENTER + 28, 105], fill=(255, 255, 255, 255), outline=(203, 213, 225, 255), width=2)
        d.ellipse([CENTER - 14, 70, CENTER - 6, 80], fill=(30, 41, 59, 255))
        d.ellipse([CENTER + 6, 70, CENTER + 14, 80], fill=(30, 41, 59, 255))
        d.ellipse([CENTER - 20, 80, CENTER - 12, 88], fill=(254, 205, 211, 255))
        d.ellipse([CENTER + 12, 80, CENTER + 20, 88], fill=(254, 205, 211, 255))
        frames.append(im)
    save_gif(frames, "37_bunny_ears.gif")


# 38. teddy_bear.gif
def make_teddy_bear():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.05 * math.sin(t * 2 * math.pi)
        # Ears
        d.ellipse([CENTER - 32, 25, CENTER - 12, 45], fill=(180, 83, 9, 255))
        d.ellipse([CENTER + 12, 25, CENTER + 32, 45], fill=(180, 83, 9, 255))
        # Head
        d.ellipse([CENTER - 30, 32, CENTER + 30, 92], fill=(217, 119, 6, 255))
        # Muzzle
        d.ellipse([CENTER - 14, 58, CENTER + 14, 82], fill=(254, 240, 138, 255))
        d.ellipse([CENTER - 4, 62, CENTER + 4, 70], fill=(30, 41, 59, 255))
        frames.append(im)
    save_gif(frames, "38_teddy_bear.gif")


# ==========================================
# 6. SPACE & MAGIC (39-44)
# ==========================================

# 39. planet_saturn.gif
def make_planet_saturn():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        rot = t * 2 * math.pi
        # Planet body
        d.ellipse([CENTER - 28, CENTER - 28, CENTER + 28, CENTER + 28], fill=(245, 158, 11, 255), outline=(217, 119, 6, 255), width=2)
        # Ring
        d.arc([CENTER - 55, CENTER - 18, CENTER + 55, CENTER + 18], 0, 360, fill=(56, 189, 248, 255), width=4)
        frames.append(im)
    save_gif(frames, "39_planet_saturn.gif")


# 40. shooting_star.gif
def make_shooting_star():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        sx = int(25 + t * 90)
        sy = int(25 + t * 90)
        # Tail
        d.line([(sx - 40, sy - 40), (sx, sy)], fill=(250, 204, 21, int(200 * (1 - t * 0.5))), width=4)
        # Star head
        d.ellipse([sx - 8, sy - 8, sx + 8, sy + 8], fill=(255, 255, 255, 255))
        frames.append(im)
    save_gif(frames, "40_shooting_star.gif")


# 41. crescent_moon.gif
def make_crescent_moon():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        scale = 1.0 + 0.06 * math.sin(t * 2 * math.pi)
        d.pieslice([CENTER - 36, CENTER - 36, CENTER + 36, CENTER + 36], 45, 225, fill=(253, 224, 71, 255))
        d.ellipse([CENTER - 20, CENTER - 36, CENTER + 36, CENTER + 30], fill=(0, 0, 0, 0))
        # Sleeping eye
        d.arc([CENTER - 18, CENTER - 5, CENTER - 6, CENTER + 5], 0, 180, fill=(120, 53, 15, 255), width=2)
        frames.append(im)
    save_gif(frames, "41_crescent_moon.gif")


# 42. magic_wand.gif
def make_magic_wand():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.line([(35, 105), (CENTER + 15, 45)], fill=(147, 51, 234, 255), width=6)
        # Star tip
        star_pts = []
        for p in range(8):
            ang = t * 2 * math.pi + p * math.pi / 4
            rad = 16 if p % 2 == 0 else 6
            star_pts.append((CENTER + 20 + rad * math.cos(ang), 40 + rad * math.sin(ang)))
        d.polygon(star_pts, fill=(250, 204, 21, 255))
        frames.append(im)
    save_gif(frames, "42_magic_wand.gif")


# 43. crystal_ball.gif
def make_crystal_ball():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        # Stand
        d.polygon([(CENTER - 25, 108), (CENTER + 25, 108), (CENTER + 12, 85), (CENTER - 12, 85)], fill=(180, 83, 9, 255))
        # Orb
        d.ellipse([CENTER - 36, 20, CENTER + 36, 92], fill=(168, 85, 247, 180), outline=(192, 132, 252, 255), width=3)
        # Magic mist inside
        ang = t * 2 * math.pi
        mx, my = CENTER + int(12 * math.cos(ang)), 56 + int(12 * math.sin(ang))
        d.ellipse([mx - 10, my - 10, mx + 10, my + 10], fill=(255, 255, 255, 160))
        frames.append(im)
    save_gif(frames, "43_crystal_ball.gif")


# 44. galaxy_spiral.gif
def make_galaxy_spiral():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        rot = t * 2 * math.pi
        for arm in range(3):
            for step in range(25):
                r = step * 1.8
                ang = rot + arm * (2 * math.pi / 3) + step * 0.25
                px = CENTER + r * math.cos(ang)
                py = CENTER + r * math.sin(ang)
                d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(217, 70, 239, int(255 * (step / 25))))
        d.ellipse([CENTER - 8, CENTER - 8, CENTER + 8, CENTER + 8], fill=(255, 255, 255, 255))
        frames.append(im)
    save_gif(frames, "44_galaxy_spiral.gif")


# ==========================================
# 7. VEHICLES & TRAVEL (45-50)
# ==========================================

# 45. car_driving.gif
def make_car_driving():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        bounce = int(3 * math.sin(t * 4 * math.pi))
        cy = CENTER + bounce
        # Car body
        d.rounded_rectangle([25, cy - 5, 115, cy + 25], radius=6, fill=(239, 68, 68, 255))
        # Cabin
        d.rounded_rectangle([45, cy - 25, 95, cy - 5], radius=6, fill=(254, 202, 202, 255))
        # Wheels
        rot = t * 2 * math.pi
        for wx in [45, 95]:
            d.ellipse([wx - 10, cy + 18, wx + 10, cy + 38], fill=(30, 41, 59, 255))
            d.line([(wx, cy + 28), (wx + 6 * math.cos(rot), cy + 28 + 6 * math.sin(rot))], fill=(255, 255, 255, 255), width=2)
        frames.append(im)
    save_gif(frames, "45_car_driving.gif")


# 46. airplane_clouds.gif
def make_airplane_clouds():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        wobble = int(4 * math.sin(t * 2 * math.pi))
        cy = CENTER + wobble
        # Airplane fuselage
        d.ellipse([25, cy - 8, 115, cy + 8], fill=(241, 245, 249, 255), outline=(148, 163, 184, 255), width=2)
        # Wings
        d.polygon([(CENTER - 10, cy), (CENTER - 25, cy - 35), (CENTER + 10, cy)], fill=(203, 213, 225, 255))
        d.polygon([(CENTER - 10, cy), (CENTER - 25, cy + 35), (CENTER + 10, cy)], fill=(203, 213, 225, 255))
        frames.append(im)
    save_gif(frames, "46_airplane_clouds.gif")


# 47. train_steam.gif
def make_train_steam():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        # Steam puffs
        for p in range(3):
            pt = (t + p * 0.33) % 1.0
            px = CENTER + 15 - int(pt * 30)
            py = 40 - int(pt * 25)
            d.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(226, 232, 240, int(200 * (1 - pt))))
        # Engine
        d.rounded_rectangle([30, 50, 110, 95], radius=6, fill=(30, 41, 59, 255))
        d.rectangle([90, 40, 105, 50], fill=(239, 68, 68, 255))
        # Wheels
        for wx in [45, 70, 95]:
            d.ellipse([wx - 9, 88, wx + 9, 106], fill=(148, 163, 184, 255))
        frames.append(im)
    save_gif(frames, "47_train_steam.gif")


# 48. hot_air_balloon.gif
def make_hot_air_balloon():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        sway = int(5 * math.sin(t * 2 * math.pi))
        # Balloon
        d.ellipse([CENTER - 32 + sway, 20, CENTER + 32 + sway, 85], fill=(239, 68, 68, 255))
        d.ellipse([CENTER - 16 + sway, 20, CENTER + 16 + sway, 85], fill=(250, 204, 21, 255))
        # Basket
        d.rectangle([CENTER - 10 + sway, 95, CENTER + 10 + sway, 110], fill=(180, 83, 9, 255))
        d.line([(CENTER - 8 + sway, 85), (CENTER - 8 + sway, 95)], fill=(100, 116, 139, 255), width=2)
        d.line([(CENTER + 8 + sway, 85), (CENTER + 8 + sway, 95)], fill=(100, 116, 139, 255), width=2)
        frames.append(im)
    save_gif(frames, "48_hot_air_balloon.gif")


# 49. compass_spin.gif
def make_compass_spin():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        d.ellipse([CENTER - 42, CENTER - 42, CENTER + 42, CENTER + 42], fill=(254, 240, 138, 255), outline=(217, 119, 6, 255), width=3)
        # Needle oscillation
        needle_ang = 0.3 * math.sin(t * 2 * math.pi)
        nx = 28 * math.sin(needle_ang)
        ny = 28 * math.cos(needle_ang)
        d.polygon([(CENTER, CENTER), (CENTER + 6, CENTER), (CENTER + nx, CENTER - ny)], fill=(239, 68, 68, 255))
        d.polygon([(CENTER, CENTER), (CENTER - 6, CENTER), (CENTER - nx, CENTER + ny)], fill=(30, 41, 59, 255))
        d.ellipse([CENTER - 5, CENTER - 5, CENTER + 5, CENTER + 5], fill=(217, 119, 6, 255))
        frames.append(im)
    save_gif(frames, "49_compass_spin.gif")


# 50. pin_location.gif
def make_pin_location():
    frames = []
    for f in range(N_FRAMES):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = f / N_FRAMES
        bounce_y = int(8 * abs(math.sin(t * 2 * math.pi)))
        py = CENTER - 5 - bounce_y
        # Pin head
        d.ellipse([CENTER - 20, py - 35, CENTER + 20, py + 5], fill=(239, 68, 68, 255), outline=(185, 28, 28, 255), width=2)
        d.polygon([(CENTER - 16, py - 10), (CENTER + 16, py - 10), (CENTER, py + 22)], fill=(239, 68, 68, 255))
        d.ellipse([CENTER - 8, py - 22, CENTER + 8, py - 6], fill=(255, 255, 255, 255))
        # Ground shadow pulse
        shadow_w = int(24 * (1 - bounce_y / 15))
        d.ellipse([CENTER - shadow_w, CENTER + 24, CENTER + shadow_w, CENTER + 34], fill=(0, 0, 0, 100))
        frames.append(im)
    save_gif(frames, "50_pin_location.gif")


def main():
    print("Generating all 50 high quality animated icons...")
    make_sun_smiling()
    make_rain_cloud()
    make_thunder_lightning()
    make_rainbow_glow()
    make_falling_leaves()
    make_snowflake_spin()
    make_blossom_flower()

    make_music_equalizer()
    make_cassette_tape()
    make_microphone_onair()
    make_audio_speaker()
    make_vinyl_rainbow()
    make_treble_clef()
    make_headphones_neon()

    make_heart_beating()
    make_fire_passion()
    make_crying_teardrop()
    make_laughing_joy()
    make_broken_heart()
    make_sparkling_eyes()
    make_clapping_hands()
    make_thumbs_up()

    make_coffee_steam()
    make_tea_pot()
    make_book_reading()
    make_candle_flame()
    make_hourglass_sand()
    make_alarm_clock()
    make_lightbulb_idea()
    make_vintage_radio()

    make_cat_paw()
    make_cat_nodding()
    make_dog_tail_wag()
    make_butterfly_flight()
    make_owl_blinking()
    make_penguin_waddle()
    make_bunny_ears()
    make_teddy_bear()

    make_planet_saturn()
    make_shooting_star()
    make_crescent_moon()
    make_magic_wand()
    make_crystal_ball()
    make_galaxy_spiral()

    make_car_driving()
    make_airplane_clouds()
    make_train_steam()
    make_hot_air_balloon()
    make_compass_spin()
    make_pin_location()
    print("[OK] Finished generating all 50 icons!")


if __name__ == "__main__":
    main()
