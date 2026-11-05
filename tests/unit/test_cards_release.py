import io
from random import Random
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, UnidentifiedImageError

from cardlegends.domain.game import RARITIES, WEIGHTS, GameError, draw
from cardlegends.infrastructure.art_sources import Danbooru, SourceError, Zerochan, ZerochanAPI
from cardlegends.infrastructure.cards import render, validate_image
from cardlegends.infrastructure.catalog import catalog_data


def test_catalog_weights_guarantee_and_render():
    data = catalog_data()
    assert len(data) == len({c["slug"] for c in data}) == 30
    assert len({c["franchise_id"] for c in data}) == 6
    assert sum(WEIGHTS) == 100
    pool = {r: [SimpleNamespace(rarity=r)] for r in RARITIES}
    for seed in range(100):
        assert any(c.rarity in RARITIES[2:] for c in draw(pool, 10, rng=Random(seed)))
    with pytest.raises(GameError):
        draw({**pool, "UR": []}, 1)
    for c in data:
        result = render(c["name_ru"], c["franchise"], c["rarity"])
        assert result == render(c["name_ru"], c["franchise"], c["rarity"])
        assert Image.open(io.BytesIO(result)).size == (750, 1050)
        assert len(result) < 1_000_000
    with pytest.raises(UnidentifiedImageError):
        validate_image(b"corrupt")


async def test_danbooru_tags_retry_and_sfw(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("cardlegends.infrastructure.art_sources.asyncio.sleep", no_sleep)
    count = 0
    post = dict(
        id=1,
        rating="g",
        image_width=750,
        image_height=1050,
        file_ext="jpg",
        tag_string="artoria_pendragon_(fate) fate_(series) solo",
        tag_string_character="artoria_pendragon_(fate)",
        file_url="https://example.test/art.jpg",
    )

    def handler(req):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        if req.url.path == "/tags.json":
            return httpx.Response(200, json=[{"name": "artoria_pendragon_(fate)", "category": 4}])
        return httpx.Response(
            200,
            json=[
                post,
                {**post, "rating": "e"},
                {**post, "tag_string": post["tag_string"] + " loli"},
            ],
        )

    source = Danbooru(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    results = await source.search("artoria_pendragon_(fate)", "fate_(series)")
    assert len(results) == 1 and results[0]["permission"] == "unknown_license"
    assert results[0]["metadata_json"]["file_url"]
    await source.close()


async def test_zerochan_requires_username_and_manual_url_validation():
    with pytest.raises(SourceError):
        ZerochanAPI(None)
    with pytest.raises(SourceError):
        Zerochan.candidate("https://example.test/1", "1")
    value = Zerochan.candidate("https://www.zerochan.net/1", "1")
    assert value["permission"] == "unknown_license"

    def handler(req):
        if req.url.path == "/1":
            return httpx.Response(
                200, json={"id": 1, "tags": ["Artoria", "Fate"], "width": 750, "height": 1050}
            )
        return httpx.Response(200, json={"items": [{"id": 1}]})

    source = ZerochanAPI("operator", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert len(await source.search("Artoria", "Fate")) == 1
    await source.close()


def test_guarantee_explicitly_replaces_last_slot_when_all_rolls_are_n():
    class LowRng:
        def choices(self, population, weights):
            return [population[0]]

        def choice(self, pool):
            return pool[0]

    pool = {r: [SimpleNamespace(rarity=r)] for r in RARITIES}
    result = draw(pool, 10, rng=LowRng())
    assert [c.rarity for c in result] == ["N"] * 9 + ["SR"]
    with pytest.raises(GameError):
        draw(pool, 1, weights=(50, 30, 15, 4, 0))


def test_retry_after_is_not_shortened_to_bypass_a_server_limit():
    from cardlegends.infrastructure.art_sources import backoff

    assert backoff(httpx.Response(503), 2) == 4
    assert backoff(httpx.Response(429, headers={"Retry-After": "20"}), 0) == 20
    with pytest.raises(SourceError):
        backoff(httpx.Response(429, headers={"Retry-After": "3600"}), 0)
