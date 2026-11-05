import asyncio
import logging
from time import monotonic

from alembic import context
from cardlegends.infrastructure.config import ConfigurationError, load_database_settings
from cardlegends.infrastructure.database import create_database, database_log_context
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
    phase = "migration_configuration"
    metadata = {}
    engine = None
    started = monotonic()
    try:
        url = database_url()
        phase = "migration_engine"
        metadata = database_log_context(url)
        engine, _ = create_database(url)
        phase = "migration_connect"
        started = monotonic()
        logger.info(
            "migration_stage phase=%s %s",
            phase,
            " ".join(f"{key}={value}" for key, value in metadata.items()),
            extra={"phase": phase, **metadata},
        )
        async with engine.connect() as connection:
            phase = "migration_upgrade_or_downgrade"
            started = monotonic()
            logger.info("migration_stage phase=%s", phase, extra={"phase": phase})
            await connection.run_sync(migrate)
    except Exception as exc:
        log_failure(
            logger,
            "migration_failed",
            exc,
            phase,
            context={**metadata, "elapsed_ms": int((monotonic() - started) * 1000)},
        )
        raise SystemExit(1) from None
    finally:
        if engine is not None:
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
        log_failure(logger, "migration_failed", exc, "migration_event_loop_or_cleanup")
        raise SystemExit(1) from None
