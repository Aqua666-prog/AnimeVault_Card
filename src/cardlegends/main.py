import asyncio
import logging
from contextlib import AsyncExitStack

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from sqlalchemy import text

from cardlegends.application.players import PlayerService
from cardlegends.bot.app import create_dispatcher
from cardlegends.infrastructure.config import ConfigurationError, load_settings
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.logging import configure_logging
from cardlegends.infrastructure.players import PostgresPlayerRepository

logger = logging.getLogger("cardlegends.main")


async def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    async with AsyncExitStack() as stack:
        engine, sessions = create_database(settings.database_url.get_secret_value())
        stack.push_async_callback(engine.dispose)
        # Проверяем подключение и наличие миграции до обращения к Telegram.
        async with engine.connect() as connection:
            await connection.execute(text("SELECT id FROM players LIMIT 0"))
        bot = Bot(
            settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        stack.push_async_callback(bot.session.close)
        dispatcher = create_dispatcher(PlayerService(PostgresPlayerRepository(sessions)))
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Регистрация и главное меню"),
                BotCommand(command="profile", description="Ваш профиль"),
                BotCommand(command="help", description="Помощь"),
            ]
        )
        logger.info("polling_started")
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False
        )
        logger.info("polling_stopped")


def main() -> None:
    configure_logging()
    try:
        asyncio.run(run())
    except ConfigurationError as exc:
        # Сообщение сформировано нами и не включает значения настроек.
        logger.error(str(exc))
        raise SystemExit(2) from None
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.error(
            "startup_or_polling_failed: проверьте БД, миграции и доступ к Telegram",
            extra={"error_type": type(exc).__name__},
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
