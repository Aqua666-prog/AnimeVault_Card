"""Cinematic, generated Telegram screen artwork; original card PNG files are never modified."""

from io import BytesIO
from pathlib import Path
from random import Random

from PIL import Image, ImageDraw, ImageFont, ImageOps

from cardlegends.infrastructure.fate_images import cards_root

WIDTH = 960
HEIGHT = 420
FONT = Path(__file__).resolve().parents[1] / "assets" / "DejaVuSans.ttf"

PALETTES = {
    "home": ((29, 35, 83), (118, 91, 243)),
    "profile": ((22, 54, 87), (58, 178, 228)),
    "collection": ((33, 36, 78), (151, 119, 255)),
    "team": ((19, 52, 83), (65, 186, 238)),
    "tower": ((71, 23, 61), (246, 75, 111)),
    "victory": ((24, 69, 69), (65, 220, 160)),
    "defeat": ((72, 26, 49), (245, 99, 126)),
    "summon": ((65, 32, 80), (254, 171, 86)),
    "calendar": ((50, 33, 77), (242, 183, 89)),
    "activities": ((33, 43, 77), (138, 125, 241)),
}


def _font(size):
    return ImageFont.truetype(str(FONT), size)


def _fit_text(draw, text, max_width, size=45):
    font = _font(size)
    while draw.textbbox((0, 0), text, font=font)[2] > max_width and size > 19:
        size -= 2
        font = _font(size)
    return font


def _track(draw, x, y, width, value, maximum, color, label):
    current = max(0, min(int(value), max(1, int(maximum))))
    maximum = max(1, int(maximum))
    draw.rounded_rectangle((x, y, x + width, y + 17), radius=8, fill=(34, 43, 64))
    inner = max(0, (width - 6) * current // maximum)
    if inner > 0:
        draw.rounded_rectangle((x + 3, y + 3, x + 3 + inner, y + 14),
                               radius=5, fill=color)
    draw.text((x, y - 30), label, font=_font(17), fill=(225, 228, 244))


def _portrait(image, draw, code, root, box):
    path = root / f"{code}.png"
    if not path.is_file():
        return
    try:
        with Image.open(path) as original:
            if original.width * original.height > 40_000_000:
                return
            art = ImageOps.fit(original.convert("RGB"), (box[2], box[3]),
                               method=Image.Resampling.LANCZOS, centering=(0.5, 0.26))
    except (OSError, ValueError):
        return
    mask = Image.new("L", art.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, art.width - 1, art.height - 1),
                                            radius=17, fill=255)
    image.paste(art, (box[0], box[1]), mask)
    draw.rounded_rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]),
                           radius=17, outline=(220, 184, 107), width=3)


def render_scene(kind, *, title=None, subtitle="", metrics=(), cards_dir=None,
                 hp=None, progress=None):
    """Generate a JPEG banner in memory; never change card files or gameplay state.

    hp: (current, maximum) for the real-time battle/HP meter.
    progress: (current, maximum) for account or collection progress.
    """
    if kind not in PALETTES:
        raise ValueError("Unknown scene type")
    base, accent = PALETTES[kind]
    image = Image.new("RGB", (WIDTH, HEIGHT), base)
    draw = ImageDraw.Draw(image)
    # Subtle vertical lighting and a dark left panel for legible captions.
    for y in range(HEIGHT):
        mix = y / HEIGHT
        shade = tuple(max(0, min(255, int(c * (0.91 + 0.19 * mix)))) for c in base)
        draw.line((0, y, WIDTH, y), fill=shade)
    draw.polygon(((420, 0), (960, 0), (960, 420), (620, 420)), fill=tuple(
        min(255, int((a + b) / 2)) for a, b in zip(base, accent, strict=True)
    ))
    for i in range(7):
        draw.ellipse((535 - i * 30, -160 + i * 19, 1080 - i * 7, 390 + i * 12),
                     outline=(*accent,), width=2)
    rand = Random("animevault:" + kind)
    for _ in range(95):
        x, y = rand.randrange(35, 940), rand.randrange(22, 390)
        c = rand.randrange(105, 205)
        draw.ellipse((x, y, x + 2, y + 2), fill=(c, c, min(255, c + 35)))
    draw.rounded_rectangle((13, 13, WIDTH - 14, HEIGHT - 14), radius=24,
                           outline=(193, 159, 103), width=3)
    draw.rounded_rectangle((25, 25, WIDTH - 26, HEIGHT - 26), radius=18,
                           outline=(86, 90, 143), width=2)
    draw.rounded_rectangle((42, 42, 542, 375), radius=20, fill=(19, 26, 51),
                           outline=(91, 85, 134), width=2)
    draw.rounded_rectangle((63, 66, 93, 95), radius=9, fill=accent)
    draw.text((106, 66), "ANIMEVAULT  /  CARDS", font=_font(21), fill=(229, 229, 245))
    titles = {
        "home": "Главное меню",
        "profile": "Профиль Мастера",
        "collection": "Моя коллекция",
        "team": "Боевой отряд",
        "tower": "Бесконечная башня",
        "victory": "ПОБЕДА!",
        "defeat": "ПОРАЖЕНИЕ",
        "summon": "Призыв героев",
        "calendar": "Награды за вход",
        "activities": "Активности",
    }
    head = title or titles[kind]
    draw.text((65, 116), head, font=_fit_text(draw, head, 465),
              fill=(250, 238, 207))
    draw.line((66, 187, 497, 187), fill=accent, width=3)
    if subtitle:
        draw.text((68, 206), str(subtitle)[:45],
                  font=_fit_text(draw, str(subtitle)[:45], 425, 24),
                  fill=(207, 218, 245))
    for index, value in enumerate(metrics[:3]):
        label = str(value)[:50]
        draw.text((74, 252 + index * 30), label,
                  font=_fit_text(draw, label, 450, 19),
                  fill=(221, 230, 249))
    if kind in ("home", "team", "collection", "summon", "profile"):
        root = Path(cards_dir) if cards_dir else cards_root()
        if kind == "team":
            cards = ("AV-F001", "AV-F002", "AV-F003")
            coords = ((563, 93, 116, 232), (689, 68, 125, 260), (823, 93, 116, 232))
        elif kind in ("home", "summon"):
            cards = ("AV-F001", "AV-F002", "AV-F003")
            coords = ((553, 104, 124, 226), (686, 77, 128, 268), (824, 104, 120, 226))
        else:
            cards = ("AV-F001", "AV-F002", "AV-F003")
            coords = ((580, 115, 105, 210), (700, 78, 125, 267), (840, 115, 100, 210))
        for code, rect in zip(cards, coords, strict=True):
            _portrait(image, draw, code, root, rect)
    elif kind in ("tower", "victory", "defeat"):
        # Original shadow-knight and castle silhouettes; all drawing is generated.
        draw.ellipse((625, 39, 900, 315), fill=(94, 76, 150), outline=accent, width=5)
        draw.polygon(((630, 346), (684, 260), (714, 225), (747, 164),
                      (775, 210), (800, 127), (827, 204), (850, 260),
                      (914, 345)), fill=(24, 28, 53))
        draw.polygon(((745, 166), (760, 95), (780, 164)), fill=(25, 30, 57))
        draw.polygon(((608, 352), (744, 300), (922, 352)), fill=(22, 30, 51))
        draw.line((635, 314, 863, 92), fill=accent, width=6)
        draw.ellipse((845, 76, 879, 110), fill=(255, 223, 205))
    elif kind in ("calendar", "activities"):
        for i in range(3):
            x = 602 + i * 105
            draw.rounded_rectangle((x, 154, x + 87, 258), radius=15,
                                   fill=(39, 45, 87), outline=(226, 183, 100), width=3)
            draw.ellipse((x + 19, 171, x + 70, 224), fill=(223, 164, 70),
                         outline=(251, 229, 161), width=3)
            draw.text((x + 34, 179), "★", font=_font(25), fill=(255, 246, 204))
    if progress is not None:
        _track(draw, 68, 358, 419, *progress, accent, "ПРОГРЕСС")
    if hp is not None:
        _track(draw, 580, 354, 318, *hp, (243, 83, 116), "HP")
    output = BytesIO()
    image.save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()
