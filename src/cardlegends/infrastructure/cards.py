import io
from functools import lru_cache
from importlib.resources import files

from PIL import Image, ImageDraw, ImageFont, ImageOps

from cardlegends.domain.game import COLORS, RARITIES

VERSION = "minimal-1"
MAX_BYTES = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 8_000_000


def validate_image(content):
    if len(content) > MAX_BYTES:
        raise ValueError("Изображение больше 8 MiB.")
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"} or image.width < 500 or image.height < 700:
            raise ValueError("Нужен JPEG/PNG/WebP не меньше 500 × 700.")
        if image.width * image.height > 8_000_000:
            raise ValueError("Изображение больше 8 мегапикселей.")
        image.verify()


def render(name, franchise, rarity, content=None):
    canvas = Image.new("RGB", (750, 1050), "#101114")
    accent = COLORS[RARITIES.index(rarity)]
    draw = ImageDraw.Draw(canvas)
    if content:
        validate_image(content)
        with Image.open(io.BytesIO(content)) as art:
            # Fit preserves the whole artwork, including signatures; never crop attribution.
            fit = ImageOps.contain(ImageOps.exif_transpose(art).convert("RGB"), (710, 790))
            canvas.paste(fit, ((750 - fit.width) // 2, 20 + (790 - fit.height) // 2))
    else:
        draw.rounded_rectangle((24, 24, 726, 800), radius=24, fill="#1B1D22")
        draw.ellipse((185, 235, 565, 615), outline=accent, width=3)
        draw.text((375, 425), "AV", anchor="mm", font=font(100), fill=accent)
        draw.text((375, 700), "ANIMEVAULT", anchor="mm", font=font(24), fill="#A1A1AA")
    draw.rectangle((32, 827, 718, 830), fill=accent)
    y = 853
    lines = []
    current = ""
    for word in name.split():
        test = (current + " " + word).strip()
        if draw.textlength(test, font=font(34)) > 680:
            lines.append(current)
            current = word
        else:
            current = test
    lines.append(current)
    for line in lines[:3]:
        draw.text((34, y), line, font=font(34), fill="#F4F4F5")
        y += 42
    draw.text((34, 1005), franchise, font=font(21), fill="#A1A1AA")
    draw.text((715, 1005), rarity, anchor="ra", font=font(24), fill=accent)
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=88, optimize=False, progressive=False)
    return output.getvalue()


@lru_cache(maxsize=16)
def font(size):
    return ImageFont.truetype(str(files("cardlegends").joinpath("assets/DejaVuSans.ttf")), size)
