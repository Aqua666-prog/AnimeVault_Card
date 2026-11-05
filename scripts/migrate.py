"""Применить миграции и выдать роли бота права только на таблицу игроков."""

import asyncio
import logging

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from cardlegends.infrastructure.config import load_database_settings
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.diagnostics import log_failure
from cardlegends.infrastructure.logging import configure_logging


async def grant_runtime_permissions() -> None:
    settings = load_database_settings()
    engine, _ = create_database(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("GRANT SELECT, INSERT, UPDATE ON TABLE players TO cardlegends_app")
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    phase = "migration_upgrade"
    try:
        command.upgrade(Config("alembic.ini"), "head")
        phase = "migration_runtime_grants"
        asyncio.run(grant_runtime_permissions())
    except Exception as exc:
        log_failure(logging.getLogger("cardlegends.migrations"), "migration_failed", exc, phase)
        raise SystemExit(1) from None
