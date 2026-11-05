import asyncio
import logging
from contextlib import AsyncExitStack

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from sqlalchemy import text

from cardlegends.application.game import GameService
from cardlegends.application.players import PlayerService
from cardlegends.bot.app import create_dispatcher
from cardlegends.infrastructure.config import ConfigurationError, load_settings
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.diagnostics import log_failure
from cardlegends.infrastructure.logging import configure_logging
from cardlegends.infrastructure.players import PostgresPlayerRepository

logger = logging.getLogger("cardlegends.main")


class StartupFailure(Exception):
    def __init__(self, phase: str, error: Exception) -> None:
        super().__init__(phase)
        self.phase = phase
        self.error = error


async def run() -> None:
    phase = "configuration"
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        async with AsyncExitStack() as stack:
            phase = "database_engine"
            logger.info("startup_phase phase=%s", phase, extra={"phase": phase})
            engine, sessions = create_database(settings.database_url.get_secret_value())
            stack.push_async_callback(engine.dispose)
            phase = "database_check"
            logger.info("startup_phase phase=%s", phase, extra={"phase": phase})
            # Ready у провайдера не гарантирует наличие схемы и прав роли приложения.
            async with engine.connect() as connection:
                await connection.execute(text("SELECT id FROM players LIMIT 0"))
            phase = "telegram_client"
            logger.info("startup_phase phase=%s", phase, extra={"phase": phase})
            bot = Bot(
                settings.bot_token.get_secret_value(),
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            stack.push_async_callback(bot.session.close)
            dispatcher = create_dispatcher(
                PlayerService(PostgresPlayerRepository(sessions)), GameService(sessions)
            )
            phase = "telegram_commands"
            logger.info("startup_phase phase=%s", phase, extra={"phase": phase})
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Регистрация и главное меню"),
                    BotCommand(command="profile", description="Ваш профиль"),
                    BotCommand(command="help", description="Помощь"),
                ]
            )
            phase = "telegram_polling"
            logger.info("polling_starting phase=%s", phase, extra={"phase": phase})
            await dispatcher.start_polling(
                bot, allowed_updates=dispatcher.resolve_used_update_types(), close_bot_session=False
            )
            logger.info("polling_stopped")
    except ConfigurationError:
        raise
    except Exception as exc:
        raise StartupFailure(phase, exc) from exc


def main() -> None:
    configure_logging()
    try:
        asyncio.run(run())
    except ConfigurationError as exc:
        log_failure(logger, "configuration_failed: Ошибка конфигурации", exc, "configuration")
        raise SystemExit(2) from None
    except StartupFailure as exc:
        log_failure(logger, "startup_or_polling_failed", exc.error, exc.phase)
        raise SystemExit(1) from None
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as exc:
        log_failure(logger, "startup_or_polling_failed", exc, "event_loop")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
