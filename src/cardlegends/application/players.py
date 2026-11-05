from dataclasses import dataclass
from datetime import datetime

from cardlegends.domain.players import PlayerRepository


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    telegram_id: int
    display_name: str
    registered_at: datetime
    status: str = "Новичок"


class PlayerService:
    def __init__(self, repository: PlayerRepository) -> None:
        self.repository = repository

    async def register(
        self, telegram_id: int, display_name: str, username: str | None
    ) -> PlayerProfile:
        player = await self.repository.upsert(telegram_id, display_name[:255], username)
        return PlayerProfile(player.telegram_id, player.display_name, player.created_at)

    async def profile(self, telegram_id: int) -> PlayerProfile | None:
        player = await self.repository.get_by_telegram_id(telegram_id)
        if player is None:
            return None
        return PlayerProfile(player.telegram_id, player.display_name, player.created_at)
