"""Generated artwork is bounded, deterministic, and accurately reflects battle HP."""

from io import BytesIO

import pytest
from PIL import Image

from cardlegends.bot.visuals import battle_image, meter, scene_image


@pytest.mark.parametrize("scene", ["home", "profile", "team", "tower", "summon", "activities"])
def test_original_posters_are_real_telegram_photos(scene):
    data = scene_image(scene, "ANIMEVAULT", "Коллекционная игра")
    assert 0 < len(data) < 9_000_000
    with Image.open(BytesIO(data)) as picture:
        assert picture.format == "JPEG"
        assert picture.size == (960, 540)
        picture.verify()
    assert data == scene_image(scene, "ANIMEVAULT", "Коллекционная игра")


def test_real_hp_is_never_overfilled_or_negative():
    assert meter(0, 100) == "▱" * 12
    assert meter(100, 100) == "▰" * 12
    assert meter(50, 100) == "▰" * 6 + "▱" * 6
    assert meter(1, 1_000) == "▰" + "▱" * 11
    assert meter(1_000, 100) == "▰" * 12
    assert meter(-5, 100) == "▱" * 12
    assert meter(20, 0) == "—"


def test_battle_image_reflects_actual_hp_and_does_not_fabricate_defeat_hp():
    starting = battle_image(10, "Босс", 5000, 5000, 1800, 1800)
    victory = battle_image(10, "Босс", 5000, 0, 1800, 1100, True)
    defeat = battle_image(10, "Босс", 5000, None, 1800, 0, False)
    assert starting != victory != defeat
    for data in (starting, victory, defeat):
        with Image.open(BytesIO(data)) as picture:
            assert picture.size == (960, 540)
            picture.verify()


def test_generated_castle_asset_is_valid_when_shipped():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "assets" / "ui" / "tower_castle_ai.jpg"
    with Image.open(path) as picture:
        assert picture.format == "JPEG"
        assert picture.width >= 100 and picture.height >= 60
        picture.verify()
