import asyncio
import logging

from alembic import context
from cardlegends.infrastructure.config import ConfigurationError, load_database_settings
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.diagnostics import log_failure
from cardlegends.infrastructure.logging import configure_logging
from cardlegends.infrastructure.models import Base

configure_logging()
logger = logging.getLogger("cardlegends.migrations")


def database_url() -> str:
    try:
        return load_database_settings().database_url.get_secret_value()
    except ConfigurationError as exc:
        log_failure(logger, "migration_failed", exc, "migration_configuration")
        raise SystemExit(2) from None


def migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    engine, _ = create_database(database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=database_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    try:
        asyncio.run(online())
    except Exception as exc:
        log_failure(logger, "migration_failed", exc, "migration_upgrade_or_downgrade")
        raise SystemExit(1) from None
