"""Per-instance card progression preserves pre-existing summons and inventory."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from cardlegends.application.players import PlayerService
from cardlegends.bot.app import create_dispatcher
from cardlegends.domain.game import GameError
from cardlegends.domain.progression import combat_stats
from cardlegends.infrastructure.game_models import CardUpgrade, CurrencyTransaction, OwnedCard
from cardlegends.infrastructure.players import PostgresPlayerRepository
from tests.integration.test_fate_flows import callback_update

pytestmark = pytest.mark.integration


async def test_level_up_and_replay_persist_and_charge_once(fate_game):
    game = fate_game
    operation = await game.summon(42, 10, uuid4().hex)
    target, character = (await game.results(42, operation))[0]
    await game.daily(42)  # No premium purchases: daily grants 300.
    key = uuid4().hex[:16]
    assert await game.level_up(42, target.id, 10, key) is True
    assert await game.level_up(42, target.id, 10, key) is False
    card, _, copies = await game.card(42, target.id)
    assert card.level == 11 and card.np == 1 and copies == 10
    assert combat_stats(character, card)["hp"] == 1920
    assert (await game.profile(42))["balance"] == 190
    with pytest.raises(GameError, match="использована"):
        await game.level_up(42, uuid4(), 10, key)
    with pytest.raises(GameError, match="Недостаточно"):
        await game.level_up(42, target.id, 10, uuid4().hex[:16])
    async with game.sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(CurrencyTransaction).where(
                CurrencyTransaction.kind == "card_level"
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(CardUpgrade).where(CardUpgrade.kind == "level")
        ) == 1
    # Reading from a new service instance will still see the committed values.
    from cardlegends.infrastructure.game_repository import GameService

    restarted = GameService(game.sessions, cards_dir=game.cards_dir)
    assert (await restarted.card(42, target.id))[0].level == 11


async def test_np_fusion_max_5_idempotence_and_historical_summon_results(fate_game):
    game = fate_game
    operation = await game.summon(42, 10, uuid4().hex)
    target, character = (await game.results(42, operation))[0]
    first_key = uuid4().hex[:16]
    assert await game.fuse_np(42, target.id, first_key) is True
    assert await game.fuse_np(42, target.id, first_key) is False
    for _ in range(3):
        assert await game.fuse_np(42, target.id, uuid4().hex[:16]) is True
    card, _, copies = await game.card(42, target.id)
    assert card.level == 1 and card.np == 5 and copies == 6
    assert combat_stats(character, card)["atk"] == 192
    assert (await game.profile(42))["total"] == 6
    assert len(await game.results(42, operation)) == 10
    with pytest.raises(GameError, match="максимальный"):
        await game.fuse_np(42, target.id, uuid4().hex[:16])
    async with game.sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(OwnedCard).where(
                OwnedCard.consumed_at.is_not(None)
            )
        ) == 4
        assert await session.scalar(
            select(func.count()).select_from(CardUpgrade).where(CardUpgrade.kind == "np")
        ) == 4
    assert (await game.profile(42))["balance"] == 0


async def test_fusion_never_consumes_favorite_or_upgraded_material(fate_game):
    game = fate_game
    op = await game.summon(42, 10, uuid4().hex)
    cards = [owned for owned, _ in await game.results(42, op)]
    target = cards[0]
    for card in cards[1:]:
        await game.favorite(42, card.id, True)
    with pytest.raises(GameError, match="Нет подходящего"):
        await game.fuse_np(42, target.id, uuid4().hex[:16])
    await game.favorite(42, cards[1].id, False)
    assert await game.fuse_np(42, target.id, uuid4().hex[:16])
    assert (await game.card(42, target.id))[0].np == 2
    with pytest.raises(GameError, match="не найдена"):
        await game.card(42, cards[1].id)
    assert len(await game.results(42, op)) == 10


async def test_cannot_upgrade_or_fuse_another_players_card(fate_game):
    game = fate_game
    op = await game.summon(42, 10, uuid4().hex)
    target, _ = (await game.results(42, op))[0]
    await PostgresPlayerRepository(game.sessions).upsert(43, "Другой", None)
    with pytest.raises(GameError, match="не найдена"):
        await game.level_up(43, target.id, 1, uuid4().hex[:16])
    with pytest.raises(GameError, match="не найдена"):
        await game.fuse_np(43, target.id, uuid4().hex[:16])
    assert (await game.profile(43))["balance"] == 1000


async def test_telegram_card_buttons_show_level_np_and_spend_only_once(fate_game, bot):
    game = fate_game
    op = await game.summon(42, 10, uuid4().hex)
    target, _ = (await game.results(42, op))[0]
    await game.daily(42)
    routes = create_dispatcher(PlayerService(PostgresPlayerRepository(game.sessions)), game)
    await routes.feed_update(bot, callback_update(f"fc:card:{target.id.hex}", 501))
    art = bot.session.calls[-1]
    assert "Уровень 1/100 • NP 1/5" in art.caption
    level_button = next(
        b.callback_data
        for row in art.reply_markup.inline_keyboard
        for b in row
        if b.callback_data.startswith("fc:l:") and ":1:" in b.callback_data
    )
    assert len(level_button.encode()) <= 64
    await routes.feed_update(bot, callback_update(level_button, 502))
    await routes.feed_update(bot, callback_update(level_button, 503))
    upgraded, _, _ = await game.card(42, target.id)
    assert upgraded.level == 2
    assert "Уровень 2/100" in bot.session.calls[-1].caption
    assert (await game.profile(42))["balance"] == 298
