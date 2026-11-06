"""Generated bot artwork must be stable, safe and sized for Telegram photo captions."""

from io import BytesIO

import pytest
from PIL import Image

from cardlegends.bot.game_ui import hit_bar
from cardlegends.bot.visuals import HEIGHT, WIDTH, render_scene


@pytest.mark.parametrize(
    "kind",
    (
        "home",
        "profile",
        "collection",
        "team",
        "tower",
        "summon",
        "calendar",
        "activities",
        "victory",
        "defeat",
    ),
)
def test_scene_returns_real_jpeg_without_modifying_art(kind, tmp_path):
    raw = render_scene(
        kind,
        subtitle="Игровой экран",
        metrics=("100 монет", "50 EXP", "Уровень 5"),
        cards_dir=tmp_path,
        hp=(125, 500) if kind == "tower" else None,
        progress=(2, 4),
    )
    assert raw.startswith(b"\xff\xd8\xff")
    assert len(raw) < 1_000_000
    with Image.open(BytesIO(raw)) as preview:
        assert preview.format == "JPEG"
        assert preview.size == (WIDTH, HEIGHT)
        assert preview.getpixel((10, 10)) != preview.getpixel((WIDTH // 2, HEIGHT // 2))
    assert not list(tmp_path.iterdir())


def test_hp_bar_boundaries():
    assert hit_bar(0, 100) == "▱" * 12 + " 0%"
    assert hit_bar(100, 100) == "▰" * 12 + " 100%"
    assert hit_bar(1, 100).startswith("▰")
    assert hit_bar(-15, 100).endswith("0%")
    assert hit_bar(500, 100).endswith("100%")


def test_unknown_scene_is_rejected():
    with pytest.raises(ValueError):
        render_scene("no_such_screen")
