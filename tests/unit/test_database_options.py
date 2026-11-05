import inspect

import asyncpg
import pytest

from cardlegends.infrastructure.config import ConfigurationError
from cardlegends.infrastructure.database import create_database


@pytest.mark.parametrize(
    "mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
)
async def test_libpq_sslmode_is_translated_without_changing_tls_mode(mode):
    engine, _ = create_database(
        f"postgresql+asyncpg://user:test_only@localhost/example?sslmode={mode}"
    )
    try:
        args, kwargs = engine.dialect.create_connect_args(engine.url)
        assert "sslmode" not in kwargs
        assert kwargs["ssl"] == mode
        # Проверка реальной сигнатуры asyncpg.connect без подключения к сети.
        inspect.signature(asyncpg.connect).bind(*args, **kwargs)
        assert engine.sync_engine.hide_parameters is True
    finally:
        await engine.dispose()


@pytest.mark.parametrize("query", ["", "?ssl=require"])
async def test_existing_asyncpg_url_is_unchanged(query):
    value = f"postgresql+asyncpg://user:test_only@localhost/example{query}"
    engine, _ = create_database(value)
    try:
        assert engine.url.render_as_string(hide_password=False) == value
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "query",
    [
        "sslmode=invalid-secret-value",
        "sslmode=require&sslmode=disable",
        "sslmode=require&ssl=disable",
    ],
)
def test_ambiguous_or_invalid_tls_options_are_rejected_without_url(query):
    with pytest.raises(ConfigurationError) as error:
        create_database(f"postgresql+asyncpg://user:secret-password@localhost/example?{query}")
    assert "secret-password" not in str(error.value)
    assert "invalid-secret-value" not in str(error.value)
