import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from cardlegends import main as entrypoint

pytestmark = pytest.mark.integration


def run_process(command, url, *, bot_token=True):
    environment = {**os.environ, "DATABASE_URL": url, "LOG_LEVEL": "INFO"}
    if bot_token:
        environment["BOT_TOKEN"] = "123456:" + "a" * 35
    else:
        environment.pop("BOT_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", *command],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def last_error(result):
    records = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    return next(record for record in reversed(records) if record["level"] == "ERROR")


async def test_ready_postgres_with_missing_players_has_safe_actionable_error(
    database, database_url
):
    engine, _ = database
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE players RENAME TO saved_players"))
    try:
        result = await asyncio.to_thread(run_process, ["cardlegends.main"], database_url)
        assert result.returncode == 1
        payload = last_error(result)
        assert payload["phase"] == "database_check"
        assert payload["sqlstate"] == "42P01"
        assert payload["reason"] == "database_schema_missing"
        assert payload["cause_type"] == "UndefinedTableError"
        assert "reason=database_schema_missing" in payload["event"]
        assert "alembic upgrade head" in payload["hint"]
        assert database_url not in result.stderr
        assert "Traceback" not in result.stderr
        assert "telegram_commands" not in result.stderr
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE saved_players RENAME TO players"))


async def test_migrations_then_bot_start_with_real_postgres_and_fake_telegram(
    database,
    database_url,
    monkeypatch,
):
    # database fixture применяет настоящий Alembic upgrade перед запуском приложения.
    bot = MagicMock()
    bot.session.close = AsyncMock()
    bot.set_my_commands = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.start_polling = AsyncMock()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("BOT_TOKEN", "123456:" + "a" * 35)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setattr(entrypoint, "Bot", lambda *a, **kw: bot)
    monkeypatch.setattr(entrypoint, "create_dispatcher", lambda *args: dispatcher)
    await entrypoint.run()
    bot.set_my_commands.assert_awaited_once()
    dispatcher.start_polling.assert_awaited_once()
    bot.session.close.assert_awaited_once()


@pytest.mark.parametrize(
    "command,phase",
    [
        (["alembic", "upgrade", "head"], "migration_connect"),
        (["cardlegends.infrastructure.db_check"], "database_connect"),
    ],
)
async def test_authentication_error_is_diagnostic_and_never_prints_password(
    database,
    database_url,
    command,
    phase,
):
    password = "intentionally-wrong-local-test-password"
    url = make_url(database_url).set(password=password).render_as_string(hide_password=False)
    result = await asyncio.to_thread(run_process, command, url, bot_token=False)
    assert result.returncode == 1
    payload = last_error(result)
    assert payload["reason"] == "database_authentication_failed"
    assert payload["sqlstate"] == "28P01"
    assert payload["cause_type"] == "InvalidPasswordError"
    assert payload["phase"] == phase
    assert payload["db_connect_timeout_s"] == payload["db_command_timeout_s"] == 5
    assert payload["elapsed_ms"] >= 0
    assert password not in result.stderr + result.stdout
    assert url not in result.stderr + result.stdout
    assert "Traceback" not in result.stderr


async def test_db_check_needs_no_telegram_token_and_does_not_change_database(
    database, database_url
):
    engine, _ = database
    async with engine.connect() as connection:
        count_before = await connection.scalar(text("SELECT count(*) FROM players"))
        revision_before = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    result = await asyncio.to_thread(
        run_process, ["cardlegends.infrastructure.db_check"], database_url, bot_token=False
    )
    assert result.returncode == 0
    assert "database_check_succeeded" in result.stderr
    assert database_url not in result.stderr
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM players")) == count_before
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == revision_before
        )
