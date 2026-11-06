"""Original, server-rendered cinematic art for the Telegram RPG.

No remote image APIs, unsafe HTML, secret data, or fabricated player stats.
Images are JPEG under Telegram's photo limit; original collectible PNGs stay untouched.
"""

from functools import lru_cache
from io import BytesIO
from math import cos, pi, sin
from pathlib import Path
from random import Random

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 960, 540
BASE = Path(__file__).resolve().parents[1] / "assets" / "DejaVuSans.ttf"
PALETTES = {
    "home": ((9, 17, 44), (47, 22, 82), (216, 177, 93)),
    "profile": ((11, 23, 53), (28, 51, 96), (104, 189, 248)),
    "team": ((9, 28, 49), (26, 55, 85), (111, 226, 201)),
    "tower": ((24, 11, 43), (80, 25, 65), (251, 120, 147)),
    "summon": ((22, 10, 51), (66, 25, 99), (230, 173, 255)),
    "activities": ((10, 26, 49), (41, 48, 91), (242, 187, 99)),
    "victory": ((14, 29, 50), (26, 76, 72), (252, 215, 110)),
    "defeat": ((27, 17, 41), (70, 32, 44), (245, 130, 142)),
}


def _font(size):
    return ImageFont.truetype(str(BASE), size)


def _mix(a, b, t):
    return tuple(int(v * (1 - t) + w * t) for v, w in zip(a, b, strict=True))


def _background(scene):
    top, bottom, gold = PALETTES[scene]
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        color = _mix(top, bottom, y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=color)
    rng = Random(sum(ord(c) for c in scene) * 2027)
    for _ in range(170):
        x, y = rng.randrange(WIDTH), rng.randrange(HEIGHT - 50)
        size = rng.choice((1, 1, 1, 2, 3))
        draw.ellipse((x, y, x + size, y + size), fill=(173, 184, 231))
    # Glowing magical moon and concentric thin sigils
    center = (755, 225)
    for n in range(11, 0, -1):
        radius = 110 + n * 8
        ring = _mix(bottom, gold, (12 - n) / 28)
        draw.ellipse(
            (center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius),
            outline=ring,
            width=1,
        )
    draw.ellipse((680, 150, 830, 300), fill=_mix(top, gold, 0.33))
    draw.ellipse((692, 162, 818, 288), fill=_mix(top, gold, 0.42))
    # Abstract original castle/tower silhouette; no copyrighted character artwork
    skyline = (5, 14, 35)
    draw.polygon(
        [
            (590, 450),
            (620, 300),
            (648, 315),
            (665, 200),
            (696, 200),
            (718, 360),
            (760, 340),
            (790, 190),
            (830, 210),
            (855, 350),
            (905, 340),
            (930, 450),
        ],
        fill=skyline,
    )
    for x, y, roof, width in (
        (620, 300, 270, 42),
        (675, 200, 160, 50),
        (790, 190, 125, 62),
        (879, 330, 270, 44),
    ):
        draw.polygon([(x, y), (x + width // 2, roof), (x + width, y)], fill=(16, 23, 52))
        draw.rectangle((x, y, x + width, 460), fill=skyline)
        draw.line((x, y, x + width, y), fill=_mix(top, gold, 0.65), width=2)
        for wy in range(y + 28, 440, 46):
            for wx in range(x + 10, x + width - 5, 22):
                draw.rounded_rectangle(
                    (wx, wy, wx + 7, wy + 17), radius=3, fill=_mix(skyline, gold, 0.65)
                )
    draw.polygon([(560, 456), (900, 410), (959, 440), (959, 539), (560, 539)], fill=skyline)
    if scene in ("home", "tower", "activities", "victory", "defeat"):
        # Generated original fantasy castle crop, kept independent from card PNG assets.
        for artwork in (
            Path.cwd() / "assets" / "ui" / "tower_castle_ai.jpg",
            Path(__file__).resolve().parents[1] / "assets" / "ui" / "tower_castle_ai.jpg",
        ):
            try:
                with Image.open(artwork) as source:
                    castle = source.convert("RGB").resize((360, 220), Image.Resampling.LANCZOS)
                image.paste(castle, (570, 190), Image.new("L", castle.size, 115))
                break
            except (OSError, ValueError):
                continue  # Procedural castle is a fully functional offline fallback.
    # Floating particles, connecting lines and side panel
    for angle in range(0, 360, 30):
        x = 755 + cos(angle * pi / 180) * 191
        y = 225 + sin(angle * pi / 180) * 191
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=gold)
    draw.rounded_rectangle((39, 42, 562, 478), radius=27, fill=_mix(top, bottom, 0.2))
    draw.rounded_rectangle((39, 42, 562, 478), radius=27, outline=_mix(top, gold, 0.60), width=3)
    draw.line((74, 112, 508, 112), fill=gold, width=2)
    draw.line((74, 434, 508, 434), fill=gold, width=2)
    return image, gold


@lru_cache(maxsize=24)
def scene_image(scene, title, subtitle):
    if scene not in PALETTES:
        raise ValueError("Unknown artwork scene")
    image, accent = _background(scene)
    draw = ImageDraw.Draw(image)
    # Text is part of illustration only; operative controls stay native Telegram buttons.
    draw.text(
        (73, 145), title[:22], font=_font(44 if len(title) <= 15 else 31), fill=(250, 246, 233)
    )
    draw.text((74, 215), subtitle[:42], font=_font(24), fill=accent)
    draw.text((74, 371), "ANIMEVAULT  /  CARDS", font=_font(21), fill=(206, 211, 236))
    draw.text((74, 402), "COLLECT  •  TRAIN  •  CONQUER", font=_font(17), fill=accent)
    return _encode(image)


def meter(value, maximum, width=12):
    """Honest, bounded text progress bar; unknown HP must not be fabricated."""
    if not isinstance(value, int) or not isinstance(maximum, int) or maximum <= 0:
        return "—"
    filled = min(width, max(0, value) * width // maximum)
    if value > 0 and filled == 0:
        filled = 1
    return "▰" * filled + "▱" * (width - filled)


def _draw_meter(draw, left, top, value, maximum, color):
    width, height = 600, 32
    draw.rounded_rectangle(
        (left, top, left + width, top + height),
        radius=12,
        fill=(28, 33, 59),
        outline=(149, 158, 186),
        width=2,
    )
    portion = min(1.0, max(0.0, value / max(1, maximum)))
    if portion:
        filled = max(8, round((width - 8) * portion))
        draw.rounded_rectangle(
            (left + 4, top + 4, left + filled, top + height - 4), radius=8, fill=color
        )


def battle_image(floor, enemy_name, enemy_max_hp, enemy_hp, team_max_hp, team_hp, victory=None):
    """Render real battle HP values, never invented progress or cached rewards."""
    scene = "victory" if victory is True else "defeat" if victory is False else "tower"
    image, accent = _background(scene)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (58, 115, 906, 425), radius=22, fill=(14, 24, 49), outline=accent, width=3
    )
    draw.text((78, 131), f"БАШНЯ  •  ЭТАЖ {floor}", font=_font(34), fill=accent)
    draw.text((78, 185), enemy_name[:37], font=_font(27), fill=(244, 240, 252))
    if enemy_hp is None:
        draw.text(
            (79, 227),
            "HP ВРАГА: исход боя без точного остатка",
            font=_font(18),
            fill=(248, 175, 184),
        )
    else:
        draw.text(
            (79, 227),
            f"HP ВРАГА     {enemy_hp:,} / {enemy_max_hp:,}",
            font=_font(21),
            fill=(247, 188, 192),
        )
        _draw_meter(draw, 80, 260, enemy_hp, enemy_max_hp, (226, 75, 108))
    draw.text(
        (79, 314),
        f"HP ОТРЯДА    {team_hp:,} / {team_max_hp:,}",
        font=_font(21),
        fill=(154, 223, 191),
    )
    _draw_meter(draw, 80, 348, team_hp, team_max_hp, (52, 192, 139))
    draw.text(
        (80, 387),
        "ПОБЕДА" if victory is True else "ПОРАЖЕНИЕ" if victory is False else "ГОТОВНОСТЬ К БОЮ",
        font=_font(20),
        fill=accent,
    )
    return _encode(image)


def _encode(image):
    output = BytesIO()
    image.save(output, format="JPEG", quality=87, optimize=True)
    result = output.getvalue()
    if len(result) > 9_000_000:
        raise ValueError("Poster exceeds Telegram photo limit")
    return result


async def show_art(event, caption, markup, *, scene="home", title="", subtitle="", data=None):
    """Send photo or edit one existing photo. A long caption uses the text UI instead."""
    if len(caption) > 1024:
        return False
    raw = data if data is not None else scene_image(scene, title, subtitle)
    file = BufferedInputFile(raw, filename=f"animevault-{scene}.jpg")
    if isinstance(event, CallbackQuery):
        if not isinstance(event.message, Message):
            return False
        if event.message.photo:
            try:
                await event.message.edit_media(
                    InputMediaPhoto(media=file, caption=caption), reply_markup=markup
                )
                return True
            except TelegramBadRequest as error:
                if "message is not modified" in error.message.lower():
                    return True
                # Telegram may reject old media edits; new photo is a safe fallback.
                if "can't be edited" not in error.message.lower():
                    raise
        await event.message.answer_photo(photo=file, caption=caption, reply_markup=markup)
    else:
        await event.answer_photo(photo=file, caption=caption, reply_markup=markup)
    return True
