from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageText,
    GetMe,
    SendMessage,
    SendPhoto,
)
from aiogram.types import Chat, Message, User

from cardlegends.application.players import PlayerService
from cardlegends.domain.players import Player


class MemoryPlayers:
    def __init__(self):
        self.rows = {}

    async def upsert(self, telegram_id, display_name, username):
        now = datetime.now(UTC)
        old = self.rows.get(telegram_id)
        row = Player(
            old.id if old else uuid4(),
            telegram_id,
            display_name,
            username,
            old.created_at if old else now,
            now,
            now,
        )
        self.rows[telegram_id] = row
        return row

    async def get_by_telegram_id(self, telegram_id):
        return self.rows.get(telegram_id)


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.closed = False

    async def close(self):
        self.closed = True

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram API
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="CardLegends", username="test_bot")
        if isinstance(
            method, (SendMessage, EditMessageText, EditMessageCaption, SendPhoto, EditMessageMedia)
        ):
            return Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=getattr(method, "text", None),
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Network requests are forbidden")
        yield  # pragma: no cover


@pytest.fixture
async def bot():
    session = RecordingSession()
    # Синтетический токен используется только с RecordingSession, сети здесь нет.
    bot = Bot(
        "123456:" + "a" * 35,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    yield bot
    await session.close()


@pytest.fixture
def memory_players():
    return MemoryPlayers()


@pytest.fixture
def service(memory_players):
    return PlayerService(memory_players)
