"""Tower and squad persist in PostgreSQL and award every button press at most once."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select

from cardlegends.application.players import PlayerService
from cardlegends.bot.app import create_dispatcher
from cardlegends.domain.game import GameError
from cardlegends.infrastructure.game_models import (
    CardUpgrade,
    CurrencyTransaction,
    OwnedCard,
    TowerRun,
)
from cardlegends.infrastructure.game_repository import GameService
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


def callback(data, index):
    return Update(
        update_id=index,
        callback_query=CallbackQuery(
            id=str(index),
            from_user=User(id=42, is_bot=False, first_name="Тест"),
            chat_instance="test",
            data=data,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=42, type="private"),
                text="menu",
            ),
        ),
    )


async def test_training_xp_spent_without_changing_coins_or_account_xp(fate_game):
    game = fate_game
    sid = await game.summon(42, 1, uuid4().hex)
    card, _ = (await game.results(42, sid))[0]
    before = await game.profile(42)
    assert before["balance"] == 900 and before["xp"] == before["training_xp"] == 10
    key = uuid4().hex[:16]
    assert await game.level_up(42, card.id, 1, key)
    assert await game.level_up(42, card.id, 1, key) is False
    after = await game.profile(42)
    assert after["balance"] == 900 and after["xp"] == 10
    assert after["training_xp"] == 8
    async with game.sessions() as session:
        upgrades = list(
            await session.scalars(select(CardUpgrade).where(CardUpgrade.kind == "level"))
        )
        assert len(upgrades) == 1
        assert upgrades[0].coin_cost == 0 and upgrades[0].xp_cost == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CurrencyTransaction)
                .where(CurrencyTransaction.kind == "card_level")
            )
            == 0
        )


async def test_team_slots_prevent_reusing_card_and_protect_fusion_material(fate_game):
    game = fate_game
    sid = await game.summon(42, 10, uuid4().hex)
    cards = [card for card, _ in await game.results(42, sid)]
    await game.assign_team(42, 1, cards[0].id)
    await game.assign_team(42, 2, cards[1].id)
    with pytest.raises(GameError, match="другом слоте"):
        await game.assign_team(42, 3, cards[0].id)
    with pytest.raises(GameError, match="не найдена"):
        await game.assign_team(42, 3, uuid4())
    assert await game.fuse_np(42, cards[0].id, uuid4().hex[:16])
    members = await game.team(42)
    assert [slot for slot, _, _ in members] == [1, 2]
    assert (await game.card(42, cards[1].id))[0].np == 1
    assert (await game.card(42, cards[0].id))[0].np == 2
    async with game.sessions() as session:
        consumed = set(
            await session.scalars(select(OwnedCard.id).where(OwnedCard.consumed_at.is_not(None)))
        )
    assert len(consumed) == 1
    assert cards[0].id not in consumed and cards[1].id not in consumed
    await game.remove_team(42, 2)
    assert [slot for slot, _, _ in await game.team(42)] == [1]
    await PostgresPlayerRepository(game.sessions).upsert(43, "Посторонний", None)
    with pytest.raises(GameError, match="не найдена"):
        await game.assign_team(43, 1, cards[0].id)


async def test_tower_clear_replay_idempotence_and_persistence(fate_game):
    game = fate_game
    sid = await game.summon(42, 1, uuid4().hex)
    card, _ = (await game.results(42, sid))[0]
    with pytest.raises(GameError, match="отряд"):
        await game.fight_tower(42, 1, uuid4().hex[:16])
    await game.assign_team(42, 1, card.id)
    key = uuid4().hex[:16]
    run, highest, balance, xp = await game.fight_tower(42, 1, key)
    assert run.victory and run.floor == highest == 1
    assert (run.coins, run.xp) == (105, 50)
    assert balance == 1005 and xp == 60
    old, highest, balance, xp = await game.fight_tower(42, 1, key)
    assert old.id == run.id and (highest, balance, xp) == (1, 1005, 60)
    again, highest, balance, xp = await game.fight_tower(42, 1, uuid4().hex[:16])
    assert again.victory and (again.coins, again.xp) == (52, 25)
    assert (highest, balance, xp) == (1, 1057, 85)
    with pytest.raises(GameError, match="не открыт"):
        await game.fight_tower(42, 3, uuid4().hex[:16])
    restarted = GameService(game.sessions, cards_dir=game.cards_dir)
    assert (await restarted.tower_info(42))[0] == 1
    assert len(await restarted.team(42)) == 1
    async with game.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TowerRun)) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CurrencyTransaction)
                .where(CurrencyTransaction.kind == "tower")
            )
            == 2
        )


async def test_telegram_team_tower_callbacks_with_real_db(fate_game, bot):
    game = fate_game
    sid = await game.summon(42, 1, uuid4().hex)
    card, _ = (await game.results(42, sid))[0]
    routes = create_dispatcher(PlayerService(PostgresPlayerRepository(game.sessions)), game)
    await routes.feed_update(bot, callback("fc:team", 400))
    assert "Свободный слот" in bot.session.calls[-1].caption
    await routes.feed_update(bot, callback("fc:ts:1:0", 401))
    assert "Выберите карту" in bot.session.calls[-1].text
    await routes.feed_update(bot, callback(f"fc:ta:1:{card.id.hex}", 402))
    assert "Artoria Pendragon" in bot.session.calls[-1].caption
    await routes.feed_update(bot, callback("fc:tower", 403))
    markup = bot.session.calls[-1].reply_markup
    fight = next(
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data.startswith("fc:tf:")
    )
    assert len(fight.encode()) <= 64
    await routes.feed_update(bot, callback(fight, 404))
    last = bot.session.calls[-1]
    assert "ПОБЕДА!" in (getattr(last, "caption", None) or getattr(last, "text", ""))
    await routes.feed_update(bot, callback(fight, 405))
    assert (await game.profile(42))["balance"] == 1005
