from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from cardlegends.infrastructure.config import ConfigurationError


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    parsed = make_url(url)
    # Облачные PostgreSQL URL часто используют параметр libpq sslmode.
    # asyncpg принимает тот же режим через аргумент ssl; значение режима сохраняется.
    if "sslmode" in parsed.query:
        mode = parsed.query["sslmode"]
        if (
            not isinstance(mode, str)
            or mode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
            or "ssl" in parsed.query
        ):
            raise ConfigurationError(
                "Ошибка конфигурации: задайте один допустимый режим TLS через ssl или sslmode."
            )
        parsed = parsed.difference_update_query(["sslmode"]).update_query_dict({"ssl": mode})
    engine = create_async_engine(
        parsed,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
