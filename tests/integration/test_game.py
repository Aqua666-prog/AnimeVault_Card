import asyncio
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from cardlegends.application.game import GameService
from cardlegends.domain.game import GameError
from cardlegends.infrastructure.catalog import seed
from cardlegends.infrastructure.game_models import (
    Character,
    CurrencyTransaction,
    OwnedCard,
)
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def game(database, fate_assets):
    engine, sessions = database
    await seed(sessions)
    await seed(sessions)
    await PostgresPlayerRepository(sessions).upsert(42, "Игрок", None)
    return GameService(
        sessions, randbelow=Random(7).randrange, cooldown_seconds=0, cards_dir=fate_assets
    )


async def test_initial_daily_and_concurrent_registration(game):
    profiles = await asyncio.gather(*[game.profile(42) for _ in range(8)])
    assert all(p["balance"] == 1000 for p in profiles)
    results = await asyncio.gather(*[game.daily(42) for _ in range(8)], return_exceptions=True)
    assert sum(isinstance(r, int) for r in results) == 1
    assert (await game.profile(42))["balance"] == 1300
    async with game.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(CurrencyTransaction)) == 2
        assert await s.scalar(select(func.count()).select_from(Character)) == 4


async def test_ten_independent_idempotency_duplicates_and_favorites(game):
    key = uuid4().hex
    ids = await asyncio.gather(*[game.summon(42, 10, key) for _ in range(6)])
    assert len(set(ids)) == 1
    results = await game.results(42, ids[0])
    assert len(results) == 10
    assert any(c.rarity in ("SR", "SSR", "UR") for _, c in results)
    profile = await game.profile(42)
    assert profile["balance"] == 0 and profile["total"] == 10 and profile["xp"] == 100
    assert profile["level"] == 2
    card = results[0][0]
    await game.favorite(42, card.id, True)
    await game.favorite(42, card.id, True)
    rows, total, page = await game.browse(42, owned=True, fav=True)
    assert total == 1 and rows[0][0].id == card.id
    catalog, count, _ = await game.browse(42, fav=True)
    assert count == 1 and catalog[0][0].id == card.character_id
    assert (await game.browse(43, fav=True))[1] == 0
    catalog, _, _ = await game.browse(42, sort="time")
    assert catalog[0][0].id == 1004
    await PostgresPlayerRepository(game.sessions).upsert(43, "Чужой", None)
    with pytest.raises(GameError):
        await game.favorite(43, card.id, False)
    assert await game.results(43, ids[0]) == []
    _, c, copies = await game.card(42, card.id)
    assert copies >= 1
    assert len(await game.history(42)) == 1
    assert (await game.browse(42, owned=True, rarity=c.rarity))[1] >= copies


async def test_concurrent_spend_never_negative_and_insufficient_rolls_back(game):
    ids = await asyncio.gather(
        *[game.summon(42, 1, uuid4().hex) for _ in range(15)], return_exceptions=True
    )
    assert sum(not isinstance(x, Exception) for x in ids) == 10
    profile = await game.profile(42)
    assert profile["balance"] == 0 and profile["total"] == 10
    with pytest.raises(GameError):
        await game.summon(42, 1, uuid4().hex)
    assert (await game.profile(42))["total"] == 10
    async with game.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(OwnedCard)) == 10


async def test_invalid_banner_does_not_charge_and_collection_sort(game):
    async with game.sessions.begin() as s:
        characters = await s.scalars(select(Character).where(Character.rarity == "UR"))
        for c in characters:
            c.is_active = False
    with pytest.raises(GameError):
        await game.summon(42, 1, uuid4().hex)
    assert (await game.profile(42))["balance"] == 1000
    rows, total, _ = await game.browse(42, franchise="fate")
    assert total == 3 and all(row[0].franchise_id == "fate" for row in rows)
    assert (await game.browse(42, owned=True))[1] == 0


async def test_rollback_after_card_insert_failure(game, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    original = AsyncSession.flush

    async def fail_on_card(session, *args, **kwargs):
        if any(isinstance(obj, OwnedCard) for obj in session.new):
            raise RuntimeError("injected failure")
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", fail_on_card)
    with pytest.raises(RuntimeError, match="injected failure"):
        await game.summon(42, 1, uuid4().hex)
    monkeypatch.setattr(AsyncSession, "flush", original)
    profile = await game.profile(42)
    assert profile["balance"] == 1000 and profile["total"] == profile["summons"] == 0


async def test_art_rights_import_replacement_and_rejection(game, tmp_path):
    from PIL import Image

    from cardlegends.infrastructure.art_admin import ArtAdmin
    from cardlegends.infrastructure.game_models import ImageAsset

    admin = ArtAdmin(game.sessions)
    values = dict(
        source="manual",
        post_id="own-drawing",
        page_url="https://example.test/rights",
        artist="Operator",
        permission="unknown_license",
        metadata_json={},
    )
    asset = await admin.save_candidate("av-f001", values)
    assert await admin.save_candidate("av-f001", values) is None
    path = tmp_path / "image.png"
    Image.new("RGB", (750, 1050), "blue").save(path)
    with pytest.raises(ValueError):
        await admin.import_file(asset, path)
    with pytest.raises(ValueError):
        await admin.approve(asset, "CC0", "explicit rights statement", "Operator", False)
    await admin.approve(asset, "CC0", "explicit rights statement", "Operator", True)
    await admin.import_file(asset, path)
    await admin.import_file(asset, path)
    async with game.sessions.begin() as s:
        stored = await s.get(ImageAsset, asset)
        assert stored.content and stored.permission == "approved"
        character = await s.scalar(select(Character).where(Character.slug == "av-f001"))
        assert character.image_asset_id == asset
        stored.permission = "rejected"
    with pytest.raises(ValueError):
        await admin.import_file(asset, path)


async def test_real_game_router_navigation_callbacks_and_privacy(game, bot):
    from datetime import UTC, datetime

    from aiogram.methods import AnswerCallbackQuery
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from cardlegends.application.players import PlayerService
    from cardlegends.bot.app import create_dispatcher

    dispatcher = create_dispatcher(PlayerService(PostgresPlayerRepository(game.sessions)), game)
    user = User(id=42, is_bot=False, first_name="Тест")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=user,
        text="/start",
    )
    await dispatcher.feed_update(bot, Update(update_id=1, message=message))
    assert "ANIMEVAULT" in bot.session.calls[-1].text
    for index, data in enumerate(
        [
            "av:summon",
            "av:list:c:0:all:all:rarity:0",
            "av:list:o:0:all:all:time:0",
            "av:profile",
            "av:daily",
            "av:daily",
            "av:help",
            "av:history",
            "av:garbage",
        ],
        2,
    ):
        before = len(bot.session.calls)
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=index,
                callback_query=CallbackQuery(
                    id=str(index), from_user=user, chat_instance="x", data=data, message=message
                ),
            ),
        )
        assert isinstance(bot.session.calls[before], AnswerCallbackQuery)
        assert len(bot.session.calls) > before + 1
        markup = bot.session.calls[-1].reply_markup
        for row in markup.inline_keyboard:
            assert all(len(b.callback_data.encode()) <= 64 for b in row)
    group = message.model_copy(update={"chat": Chat(id=-1, type="group")})
    before = len(bot.session.calls)
    await dispatcher.feed_update(
        bot,
        Update(
            update_id=100,
            callback_query=CallbackQuery(
                id="group", from_user=user, chat_instance="x", data="av:profile", message=group
            ),
        ),
    )
    assert len(bot.session.calls) == before + 1


async def test_prepared_png_is_not_replaced_by_external_art(game, bot):
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, User

    from cardlegends.bot.game_ui import show_card
    from cardlegends.infrastructure.game_models import ImageAsset

    async with game.sessions.begin() as s:
        asset = ImageAsset(
            character_slug="av-f001",
            source="manual",
            post_id="test",
            page_url="https://example.test/1",
            content=b"unrelated art",
            permission="unknown_license",
        )
        s.add(asset)
        await s.flush()
        char = await s.get(Character, 1001)
        char.image_asset_id = asset.id
    query = CallbackQuery(
        id="test",
        from_user=User(id=42, is_bot=False, first_name="Тест"),
        chat_instance="x",
        message=Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=42, type="private")),
    ).as_(bot)
    await show_card(query, game, char)
    assert bot.session.calls[-1].photo.data == (game.cards_dir / "AV-F001.png").read_bytes()
    assert "Artoria Pendragon" in bot.session.calls[-1].caption
    assert "заглушка" not in bot.session.calls[-1].caption


async def test_existing_player_survives_upgrade_and_game_survives_new_engine(
    database, database_url, fate_assets
):
    import os
    import subprocess
    import sys

    from cardlegends.infrastructure.database import create_database

    engine, sessions = database
    original = await PostgresPlayerRepository(sessions).upsert(42, "Существующий игрок", None)
    await engine.dispose()
    for action, target in [("downgrade", "54b3f0a493e1"), ("upgrade", "head")]:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", action, target],
            env={**os.environ, "DATABASE_URL": database_url},
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    fresh, ss = create_database(database_url)
    try:
        preserved = await PostgresPlayerRepository(ss).get_by_telegram_id(42)
        assert preserved.id == original.id and preserved.created_at == original.created_at
        await seed(ss)
        game = GameService(ss, randbelow=Random(5).randrange, cards_dir=fate_assets)
        sid = await game.summon(42, 1, uuid4().hex)
        card = (await game.results(42, sid))[0][0]
        await game.favorite(42, card.id, True)
        await game.daily(42)
    finally:
        await fresh.dispose()
    restarted, ss = create_database(database_url)
    try:
        game = GameService(ss)
        profile = await game.profile(42)
        assert profile["balance"] == 1200 and profile["total"] == 1 and profile["xp"] == 10
        assert (await game.card(42, card.id))[0].favorite is True
        assert len(await game.results(42, sid)) == 1
        with pytest.raises(GameError):
            await game.daily(42)
    finally:
        await restarted.dispose()
