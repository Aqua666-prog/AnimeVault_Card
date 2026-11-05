from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cardlegends.bot.app import create_dispatcher
from cardlegends.bot.middleware import DATABASE_ERROR
from cardlegends.domain.players import PersistenceError
from cardlegends.infrastructure.logging import correlation_id


def message_update(text, chat_type="private"):
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=42, type=chat_type),
            from_user=User(id=42, is_bot=False, first_name="<Иван>"),
            text=text,
        ),
    )


def callback_update(data):
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="callback-1",
            from_user=User(id=42, is_bot=False, first_name="Иван"),
            chat_instance="test",
            data=data,
            message=Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=-100, type="group")),
        ),
    )


async def test_start_repeat_and_menu_through_dispatcher(bot, service, memory_players):
    dispatcher = create_dispatcher(service)
    for _ in range(2):
        await dispatcher.feed_update(bot, message_update("/start"))
    assert len(memory_players.rows) == 1
    calls = [call for call in bot.session.calls if isinstance(call, SendMessage)]
    assert len(calls) == 2
    assert "&lt;Иван&gt;" in calls[-1].text
    buttons = [b.text for row in calls[-1].reply_markup.inline_keyboard for b in row]
    assert buttons == ["Профиль", "Коллекция", "Призыв", "Сражения", "Помощь"]


async def test_profile_routes_only_current_user(bot, service):
    await service.register(42, "Мой профиль", None)
    await service.register(99, "Чужое приватное имя", None)
    dispatcher = create_dispatcher(service)
    await dispatcher.feed_update(bot, message_update("/profile 99"))
    call = bot.session.calls[-1]
    assert "Мой профиль" in call.text
    assert "Чужое приватное имя" not in call.text
    assert "Новичок" in call.text


async def test_profile_before_registration(bot, service):
    await create_dispatcher(service).feed_update(bot, message_update("/profile"))
    assert "/start" in bot.session.calls[-1].text


@pytest.mark.parametrize("data", ["menu:collection", "menu:summon", "menu:battles"])
async def test_unfinished_buttons_acknowledge_callback(bot, service, data):
    await create_dispatcher(service).feed_update(bot, callback_update(data))
    assert len(bot.session.calls) == 1
    call = bot.session.calls[0]
    assert isinstance(call, AnswerCallbackQuery)
    assert call.text == "Раздел в разработке"
    assert call.show_alert is True


@pytest.mark.parametrize("data", ["old-button", None])
async def test_unknown_callbacks_are_acknowledged(bot, service, data):
    await create_dispatcher(service).feed_update(bot, callback_update(data))
    assert isinstance(bot.session.calls[-1], AnswerCallbackQuery)
    assert "устарела" in bot.session.calls[-1].text


async def test_profile_button_sends_only_private_profile(bot, service):
    await service.register(42, "Личный профиль", None)
    await create_dispatcher(service).feed_update(bot, callback_update("menu:profile"))
    assert isinstance(bot.session.calls[0], AnswerCallbackQuery)
    assert bot.session.calls[1].chat_id == 42
    assert "Личный профиль" in bot.session.calls[1].text


@pytest.mark.parametrize("text", ["/start", "/profile"])
async def test_groups_do_not_register_or_expose_profiles(bot, service, memory_players, text):
    await create_dispatcher(service).feed_update(bot, message_update(text, "group"))
    assert memory_players.rows == {}
    assert "профиль" not in bot.session.calls[-1].text.lower()


async def test_help_and_unknown_command(bot, service):
    dispatcher = create_dispatcher(service)
    await dispatcher.feed_update(bot, message_update("/help"))
    assert "/start" in bot.session.calls[-1].text
    assert "следующих этапах" in bot.session.calls[-1].text
    await dispatcher.feed_update(bot, message_update("/missing"))
    assert "Неизвестная команда" in bot.session.calls[-1].text


async def test_help_callback(bot, service):
    await create_dispatcher(service).feed_update(bot, callback_update("menu:help"))
    assert isinstance(bot.session.calls[0], AnswerCallbackQuery)
    assert "/help" in bot.session.calls[1].text


@pytest.mark.parametrize("callback", [False, True])
async def test_database_errors_are_safe_and_correlation_is_reset(bot, service, callback, caplog):
    service.repository.get_by_telegram_id = AsyncMock(
        side_effect=PersistenceError("secret DSN must not escape")
    )
    event = callback_update("menu:profile") if callback else message_update("/profile")
    await create_dispatcher(service).feed_update(bot, event)
    assert bot.session.calls[-1].text == DATABASE_ERROR
    assert "secret DSN" not in caplog.text
    assert correlation_id.get() is None
