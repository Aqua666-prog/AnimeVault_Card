import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject, Update

from cardlegends.application.players import PlayerService
from cardlegends.domain.players import PersistenceError
from cardlegends.infrastructure.logging import correlation_id

logger = logging.getLogger(__name__)
DATABASE_ERROR = "База данных временно недоступна. Попробуйте ещё раз позже."
GENERAL_ERROR = "Не удалось выполнить действие. Попробуйте ещё раз позже."


class ServicesMiddleware(BaseMiddleware):
    def __init__(self, players: PlayerService) -> None:
        self.players = players

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        token = correlation_id.set(str(event.update_id) if isinstance(event, Update) else None)
        data["players"] = self.players
        try:
            result = await handler(event, data)
            logger.info("update_processed")
            return result
        except Exception as exc:
            logger.error("update_failed", extra={"error_type": type(exc).__name__})
            text = DATABASE_ERROR if isinstance(exc, PersistenceError) else GENERAL_ERROR
            if isinstance(event, Update):
                try:
                    if event.callback_query:
                        await event.callback_query.answer(text, show_alert=True)
                    elif event.message:
                        await event.message.answer(text)
                except TelegramAPIError as reply_error:
                    logger.warning(
                        "error_reply_failed", extra={"error_type": type(reply_error).__name__}
                    )
            return None
        finally:
            correlation_id.reset(token)
