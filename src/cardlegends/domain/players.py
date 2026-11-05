from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Player:
    id: UUID
    telegram_id: int
    display_name: str
    username: str | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class PlayerRepository(Protocol):
    async def upsert(self, telegram_id: int, display_name: str, username: str | None) -> Player:
        """Атомарно зарегистрировать игрока или обновить его публичные поля."""
        ...

    async def get_by_telegram_id(self, telegram_id: int) -> Player | None: ...


class PersistenceError(Exception):
    """Хранилище временно недоступно; детали не предназначены для пользователя."""
