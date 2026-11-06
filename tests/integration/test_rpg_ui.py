from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram.methods import DeleteMessage, EditMessageMedia, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from cardlegends.application.players import PlayerService
from cardlegends.bot.app import create_dispatcher
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


def event(data, index=1, photo=False):
    msg = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        text=None if photo else "menu",
        photo=[PhotoSize(file_id="current", file_unique_id="u", width=80, height=120)]
        if photo
        else None,
    )
    return Update(
        update_id=index,
        callback_query=CallbackQuery(
            id=str(index),
            from_user=User(id=42, is_bot=False, first_name="<Игрок>"),
            chat_instance="x",
            data=data,
            message=msg,
        ),
    )


async def test_all_new_sections_work_with_real_dispatcher_and_db(fate_game, bot, caplog):
    game = fate_game
    routes = create_dispatcher(PlayerService(PostgresPlayerRepository(game.sessions)), game)
    values = (
        "fc:home",
        "fc:profile",
        "r2:inv:0:power:all:all",
        "r2:filters:power",
        "r2:q:daily:0",
        "r2:q:weekly:0",
        "r2:q:achievements:0",
        "r2:q:achievements:3",
        "r2:calendar",
        "r2:tutorial:show",
        "r2:activities",
        "r2:rewards:all:0",
        "r2:rewards:mail:0",
        "fc:team",
        "fc:tower",
        "fc:summon",
        "fc:help",
    )
    for i, value in enumerate(values, 1000):
        before = len(bot.session.calls)
        await routes.feed_update(bot, event(value, i))
        assert len(bot.session.calls) > before + 1, value
        call = bot.session.calls[-1]
        text = getattr(call, "text", "") or getattr(call, "caption", "") or ""
        assert "Не удалось" not in text, value
        assert len(text) <= 4096
        if call.reply_markup:
            assert all(
                len(b.callback_data.encode()) <= 64
                for row in call.reply_markup.inline_keyboard
                for b in row
            )
    assert not any(record.levelname == "ERROR" for record in caplog.records)
    for command in (
        "/quests",
        "/weekly",
        "/achievements",
        "/rewards",
        "/mail",
        "/calendar",
        "/activities",
        "/tutorial",
    ):
        await routes.feed_update(
            bot,
            Update(
                update_id=1100,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=42, type="private"),
                    from_user=User(id=42, is_bot=False, first_name="Test"),
                    text=command,
                ),
            ),
        )
        expected = SendPhoto if command in ("/calendar", "/activities") else SendMessage
        assert isinstance(bot.session.calls[-1], expected)


async def test_receipt_replay_mail_html_and_card_media_edit(fate_game, bot):
    game = fate_game
    routes = create_dispatcher(PlayerService(PostgresPlayerRepository(game.sessions)), game)
    await game.gift(42, "ui", "<b>Not HTML</b> & gift", 12, 5)
    _, grants, _ = await game.reward_board(42, "mail")
    letter = next(g for g in grants if g.category == "mail")
    await routes.feed_update(bot, event(f"r2:letter:{letter.id.hex}", 1200))
    assert "&lt;b&gt;" in bot.session.calls[-1].text
    e = event(f"r2:claim:{letter.id.hex}", 1201)
    await routes.feed_update(bot, e)
    await routes.feed_update(bot, e)
    assert (await game.profile(42))["balance"] == 1012
    op = await game.summon(42, 1, uuid4().hex)
    card, _ = (await game.results(42, op))[0]
    await routes.feed_update(bot, event(f"fc:card:{card.id.hex}", 1202, photo=True))
    assert isinstance(bot.session.calls[-1], EditMessageMedia)
    assert len(bot.session.calls[-1].media.caption) <= 1024
    await routes.feed_update(bot, event("fc:profile", 1203))
    assert isinstance(bot.session.calls[-1], SendPhoto)


async def test_cinematic_media_transitions_do_not_leave_stale_art(fate_game, bot):
    routes = create_dispatcher(
        PlayerService(PostgresPlayerRepository(fate_game.sessions)), fate_game
    )
    await routes.feed_update(bot, event("fc:home", 1600, photo=True))
    assert isinstance(bot.session.calls[-1], EditMessageMedia)
    assert len(bot.session.calls[-1].media.caption) <= 1024
    await routes.feed_update(bot, event("fc:help", 1601, photo=True))
    assert isinstance(bot.session.calls[-1], SendMessage)
    assert any(isinstance(call, DeleteMessage) for call in bot.session.calls)
