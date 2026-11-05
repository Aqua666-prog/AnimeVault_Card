import ipaddress
import os

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from cardlegends.infrastructure.config import ConfigurationError

CONNECT_TIMEOUT_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 5
TLS_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def database_log_context(url: str) -> dict[str, str | int]:
    """Только безопасные категории. Ни host, ни database/user/password не возвращаются."""
    parsed = make_url(url)
    try:
        host_kind = "ipv4" if ipaddress.ip_address(parsed.host or "").version == 4 else "ipv6"
    except ValueError:
        host_kind = "dns"
    if "sslmode" in parsed.query or "ssl" in parsed.query:
        mode = parsed.query.get("sslmode", parsed.query.get("ssl"))
        source = "url"
    elif "PGSSLMODE" in os.environ:
        mode = os.environ["PGSSLMODE"]
        source = "environment"
    else:
        # asyncpg использует prefer по умолчанию для TCP, если PGSSLMODE не задан.
        mode, source = "prefer", "driver_default"
    mode = mode if isinstance(mode, str) and mode in TLS_MODES else "invalid"
    return {
        "db_host_kind": host_kind,
        "db_port": parsed.port or 5432,
        "db_tls_mode": mode,
        "db_tls_source": source,
        "db_connect_timeout_s": CONNECT_TIMEOUT_SECONDS,
        "db_command_timeout_s": COMMAND_TIMEOUT_SECONDS,
    }


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    parsed = make_url(url)
    # Облачные PostgreSQL URL часто используют параметр libpq sslmode.
    # asyncpg принимает тот же режим через аргумент ssl; значение режима сохраняется.
    if "sslmode" in parsed.query:
        mode = parsed.query["sslmode"]
        if not isinstance(mode, str) or mode not in TLS_MODES or "ssl" in parsed.query:
            raise ConfigurationError(
                "Ошибка конфигурации: задайте один допустимый режим TLS через ssl или sslmode."
            )
        parsed = parsed.difference_update_query(["sslmode"]).update_query_dict({"ssl": mode})
    engine = create_async_engine(
        parsed,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "timeout": CONNECT_TIMEOUT_SECONDS,
            "command_timeout": COMMAND_TIMEOUT_SECONDS,
        },
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
