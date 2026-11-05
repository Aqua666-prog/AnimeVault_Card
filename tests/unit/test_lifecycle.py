from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from cardlegends import main as entrypoint


@pytest.mark.parametrize("failure", [None, "database", "telegram", "polling"])
async def test_resources_close_on_shutdown_and_startup_failure(monkeypatch, failure):
    settings = SimpleNamespace(
        bot_token=SecretStr("123456:" + "a" * 35),
        database_url=SecretStr("postgresql+asyncpg://user:password@host/db"),
        log_level="INFO",
    )
    engine = MagicMock()
    engine.dispose = AsyncMock()
    connection = engine.connect.return_value.__aenter__.return_value
    connection.execute = AsyncMock()
    bot = MagicMock()
    bot.session.close = AsyncMock()
    bot.set_my_commands = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.start_polling = AsyncMock()
    if failure == "database":
        connection.execute.side_effect = RuntimeError("database")
    elif failure == "telegram":
        bot.set_my_commands.side_effect = RuntimeError("telegram")
    elif failure == "polling":
        dispatcher.start_polling.side_effect = RuntimeError("polling")
    monkeypatch.setattr(entrypoint, "load_settings", lambda: settings)
    monkeypatch.setattr(entrypoint, "create_database", lambda _: (engine, MagicMock()))
    monkeypatch.setattr(entrypoint, "Bot", lambda *a, **kw: bot)
    monkeypatch.setattr(entrypoint, "create_dispatcher", lambda _: dispatcher)
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            await entrypoint.run()
    else:
        await entrypoint.run()
        assert dispatcher.start_polling.call_args.kwargs["close_bot_session"] is False
    engine.dispose.assert_awaited_once()
    if failure != "database":
        bot.session.close.assert_awaited_once()
