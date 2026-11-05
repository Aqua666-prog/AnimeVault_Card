import re

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(Exception):
    pass


class DatabaseSettings(BaseSettings):
    # .env загружает Docker Compose или оболочка; приложение читает только окружение.
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+asyncpg"
                and bool(url.host and url.database and url.username and url.password)
                and (url.port is None or 1 <= url.port <= 65535)
            )
        except (ArgumentError, ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("ожидается PostgreSQL DSN с драйвером asyncpg") from None
        return value


class Settings(DatabaseSettings):
    bot_token: SecretStr
    log_level: str = "INFO"

    @field_validator("bot_token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[1-9][0-9]*:[A-Za-z0-9_-]{30,}", value.get_secret_value()):
            raise ValueError("ожидается токен BotFather")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("недопустимый уровень логирования")
        return value


def load_database_settings() -> DatabaseSettings:
    try:
        return DatabaseSettings()
    except ValidationError:
        raise ConfigurationError(
            "Ошибка конфигурации: проверьте DATABASE_URL (postgresql+asyncpg)."
        ) from None


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError:
        raise ConfigurationError(
            "Ошибка конфигурации: проверьте BOT_TOKEN, DATABASE_URL и LOG_LEVEL. "
            "Используйте .env.example как образец."
        ) from None
