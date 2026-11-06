import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select, text

from cardlegends.application.players import PlayerService
from cardlegends.bot.app import create_dispatcher
from cardlegends.domain.game import GameError
from cardlegends.infrastructure.game_models import CurrencyTransaction, Summon
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


def command_update(command, update_id=1):
    return Update(
        update_id=update_id,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=42, type="private"),
            from_user=User(id=42, is_bot=False, first_name="Тест"),
            text=command,
        ),
    )


def callback_update(data, update_id=1, user=42, chat=42):
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=user, is_bot=False, first_name="Тест"),
            chat_instance="test",
            data=data,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=chat, type="private"),
                text="menu",
            ),
        ),
    )


def dispatcher(game):
    return create_dispatcher(PlayerService(PostgresPlayerRepository(game.sessions)), game)


async def test_repeat_start_does_not_regrant_new_account_bonus(fate_game, bot):
    game = fate_game
    routes = dispatcher(game)
    await routes.feed_update(bot, command_update("/start"))
    await game.summon(42, 1, uuid4().hex)
    await routes.feed_update(bot, command_update("/start", 2))
    assert (await game.profile(42))["balance"] == 900
    async with game.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CurrencyTransaction)
                .where(CurrencyTransaction.kind == "initial")
            )
            == 1
        )


async def test_all_commands_catalog_collection_and_locked_preview(fate_game, bot):
    routes = dispatcher(fate_game)
    for i, command in enumerate(
        ("/start", "/summon", "/collection", "/catalog", "/profile", "/rates", "/help", "/history"),
        1,
    ):
        await routes.feed_update(bot, command_update(command, i))
        response = bot.session.calls[-1]
        if command == "/start":
            assert isinstance(response, SendPhoto)
            assert "ANIMEVAULT" in response.caption
        else:
            assert isinstance(response, SendMessage)
    await routes.feed_update(bot, command_update("/collection", 20))
    text_ = bot.session.calls[-1].text
    assert "0/4" in text_ and text_.count("×0") == 4 and "Assassin × Archer" in text_
    before = len(bot.session.calls)
    await routes.feed_update(bot, callback_update("fc:owned:AV-L001", 21))
    assert "скрыто" in bot.session.calls[-1].text
    assert not any(isinstance(c, SendPhoto) for c in bot.session.calls[before:])
    await routes.feed_update(bot, callback_update("fc:catalog:3", 22))
    assert "UR LIMITED — Assassin × Archer" in bot.session.calls[-1].text
    assert "1%" in bot.session.calls[-1].text and "4/4" in bot.session.calls[-1].text
    await routes.feed_update(bot, callback_update("fc:preview:AV-L001", 23))
    assert isinstance(bot.session.calls[-1], SendPhoto)
    for call in bot.session.calls:
        markup = getattr(call, "reply_markup", None)
        if markup:
            assert all(
                len(b.callback_data.encode()) <= 64 for row in markup.inline_keyboard for b in row
            )


async def test_single_double_click_and_update_replay_after_telegram_failure(
    fate_game, bot, monkeypatch
):
    game = fate_game
    routes = dispatcher(game)
    send = bot.session.make_request
    failed = False

    async def flaky(bot_, method, **kwargs):
        nonlocal failed
        if isinstance(method, SendPhoto) and not failed:
            failed = True
            raise TelegramNetworkError(method=method, message="synthetic transport error")
        return await send(bot_, method, **kwargs)

    monkeypatch.setattr(bot.session, "make_request", flaky)
    key = uuid4().hex
    event = callback_update(f"fc:draw:1:{key}", 100)
    await routes.feed_update(bot, event)
    assert (await game.profile(42))["balance"] == 900
    assert "Результат сохранён" in bot.session.calls[-1].text
    await routes.feed_update(bot, event)  # Same Telegram update, same button.
    await routes.feed_update(bot, callback_update(f"fc:draw:1:{key}", 101))  # Double tap.
    assert (await game.profile(42))["balance"] == 900
    assert len(await game.history(42)) == 1
    result = bot.session.calls[-1]
    assert isinstance(result, SendPhoto)
    assert "Потрачено: 100" in result.caption and "Остаток: 900" in result.caption
    assert "Класс: Saber" in result.caption and "Редкость: SSR" in result.caption


async def test_ten_shows_ten_results_duplicates_and_images_without_message_spam(fate_game, bot):
    game = fate_game
    routes = dispatcher(game)
    await routes.feed_update(bot, callback_update(f"fc:draw:10:{uuid4().hex}", 300))
    result = bot.session.calls[-1]
    assert "10. Artoria Pendragon" in result.text and "Artoria Pendragon ×10" in result.text
    assert "Повторных экземпляров: 9" in result.text
    assert "Потрачено: 1 000" in result.text and "Остаток: 0" in result.text
    assert len(bot.session.calls) == 2  # One callback ack, one compact text result.
    await routes.feed_update(bot, callback_update("fc:owned:AV-F001", 301))
    assert isinstance(bot.session.calls[-1], SendPhoto)
    entries = await game.collection(42)
    assert [q for _, q, *_ in entries] == [10, 0, 0, 0]
    assert (await game.profile(42))["unique"] == 1


async def test_limited_is_one_item_and_progress_reaches_four(fate_game):
    game = fate_game
    values = iter([0, 3300, 6600, 9900])
    game.randbelow = lambda _: next(values)
    for _ in range(4):
        await game.summon(42, 1, uuid4().hex)
    entries = await game.collection(42)
    assert [q for _, q, *_ in entries] == [1, 1, 1, 1]
    assert entries[-1][0].name_en == "Kama & Ishtar"
    assert (await game.profile(42))["unique"] == 4
    assert all(first is not None and last is not None for _, _, first, last in entries)


async def test_replay_identity_checks_before_cooldown_and_altered_operation(fate_game):
    game = fate_game
    game.cooldown_seconds = 60
    key = uuid4().hex
    operation = await game.summon(42, 1, key, telegram_update_id=100)
    assert await game.summon(42, 1, key, telegram_update_id=101) == operation
    assert await game.summon(42, 1, uuid4().hex, telegram_update_id=100) == operation
    with pytest.raises(GameError, match="выполнена"):
        await game.summon(42, 10, key)
    with pytest.raises(GameError, match="Слишком быстро"):
        await game.summon(42, 1, uuid4().hex, telegram_update_id=102)
    assert (await game.profile(42))["balance"] == 900


async def test_missing_asset_blocks_new_spend_but_preserves_replay(fate_game):
    game = fate_game
    key = uuid4().hex
    operation = await game.summon(42, 1, key)
    (game.cards_dir / "AV-L001.png").unlink()
    with pytest.raises(GameError, match="assets/cards/AV-L001.png"):
        await game.summon(42, 1, uuid4().hex)
    assert await game.summon(42, 1, key) == operation
    assert (await game.profile(42))["balance"] == 900
    assert len(await game.results(42, operation)) == 1


async def test_forged_callbacks_cannot_spend_or_read_another_users_result(fate_game, bot):
    game = fate_game
    operation = await game.summon(42, 1, uuid4().hex)
    await PostgresPlayerRepository(game.sessions).upsert(43, "Другой", None)
    routes = dispatcher(game)
    for i, data in enumerate(
        (
            "fc:draw:10:not-a-uuid",
            "fc:draw:-1:" + uuid4().hex,
            "fc:preview:../../secrets",
            "fc:catalog:4",
        ),
        200,
    ):
        await routes.feed_update(bot, callback_update(data, i))
    await routes.feed_update(bot, callback_update(f"fc:result:{operation.hex}", 210, 43, 43))
    assert "не найден" in bot.session.calls[-1].text
    await routes.feed_update(bot, callback_update(f"fc:draw:1:{uuid4().hex}", 211, 43, 42))
    assert (await game.profile(43))["balance"] == 1000
    assert (await game.profile(42))["balance"] == 900


async def test_old_draw_button_only_opens_new_banner_without_charge(fate_game, bot):
    game = fate_game
    await dispatcher(game).feed_update(bot, callback_update(f"av:draw:10:{uuid4().hex}"))
    assert (await game.profile(42))["balance"] == 1000
    assert "Fate Collection" in bot.session.calls[-1].text
    assert len(await game.history(42)) == 0


async def test_parallel_update_identity_and_insufficient_ten_leave_no_partial_rewards(fate_game):
    game = fate_game
    operations = await asyncio.gather(
        *[game.summon(42, 1, uuid4().hex, telegram_update_id=777) for _ in range(6)]
    )
    assert len(set(operations)) == 1
    with pytest.raises(GameError, match="Недостаточно"):
        await game.summon(42, 10, uuid4().hex)
    assert (await game.profile(42))["balance"] == 900
    async with game.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Summon)) == 1
        assert await s.scalar(text("SELECT count(*) FROM owned_cards")) == 1
