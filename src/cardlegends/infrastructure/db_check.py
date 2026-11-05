"""Проверка подключения и SELECT 1 без токена Telegram и изменения схемы.

Запуск: python -m cardlegends.infrastructure.db_check
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from time import monotonic

from sqlalchemy import text

from cardlegends.infrastructure.config import ConfigurationError, load_database_settings
from cardlegends.infrastructure.database import create_database, database_log_context
from cardlegends.infrastructure.diagnostics import log_failure
from cardlegends.infrastructure.logging import configure_logging

logger = logging.getLogger("cardlegends.db_check")


async def check() -> int:
    phase = "database_configuration"
    metadata = {}
    started = monotonic()
    try:
        settings = load_database_settings()
        url = settings.database_url.get_secret_value()
        phase = "database_engine"
        metadata = database_log_context(url)
        async with AsyncExitStack() as stack:
            engine, _ = create_database(url)
            stack.push_async_callback(engine.dispose)
            phase = "database_connect"
            logger.info(
                "database_check phase=%s %s",
                phase,
                " ".join(f"{key}={value}" for key, value in metadata.items()),
                extra={"phase": phase, **metadata},
            )
            async with engine.connect() as connection:
                phase = "database_ping"
                await connection.execute(text("SELECT 1"))
            phase = "database_cleanup"
        logger.info("database_check_succeeded")
        return 0
    except Exception as exc:
        log_failure(
            logger,
            "database_check_failed",
            exc,
            phase,
            context={**metadata, "elapsed_ms": int((monotonic() - started) * 1000)},
        )
        return 2 if isinstance(exc, ConfigurationError) else 1


def main() -> None:
    configure_logging()
    raise SystemExit(asyncio.run(check()))


if __name__ == "__main__":
    main()
