import json
import logging
import subprocess
import sys

import pytest

from cardlegends.infrastructure.config import (
    ConfigurationError,
    load_database_settings,
    load_settings,
)
from cardlegends.infrastructure.logging import JsonFormatter, correlation_id


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://user:secret@localhost/db",
        "postgresql+asyncpg://user:secret@localhost:5432/db",
    ],
)
def test_database_settings_do_not_require_telegram_token(monkeypatch, url):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", url)
    assert load_database_settings().database_url.get_secret_value() == url


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///secret",
        "postgres://user:private@host/db",
        "postgresql+asyncpg://user:private@host:bad/db",
        "bad-private-dsn",
    ],
)
def test_bad_database_url_is_sanitized(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ConfigurationError) as error:
        load_database_settings()
    assert url not in str(error.value)
    assert "private" not in str(error.value)
    assert "DATABASE_URL" in str(error.value)


def test_missing_config(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="BOT_TOKEN, DATABASE_URL"):
        load_settings()


def test_startup_invalid_config_exits_without_secrets(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "do-not-print-this-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:hidden-password@host/db")
    result = subprocess.run(
        [sys.executable, "-m", "cardlegends.main"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "Ошибка конфигурации" in result.stderr
    assert "do-not-print-this-token" not in result.stderr
    assert "hidden-password" not in result.stderr
    assert "Traceback" not in result.stderr


def test_json_logs_include_correlation_but_hide_external_messages():
    token = correlation_id.set("123")
    try:
        record = logging.LogRecord(
            "sqlalchemy.engine", logging.ERROR, "", 1, "secret-dsn password", (), None
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["correlation_id"] == "123"
        assert payload["event"] == "external_log"
        assert "password" not in json.dumps(payload)
    finally:
        correlation_id.reset(token)
