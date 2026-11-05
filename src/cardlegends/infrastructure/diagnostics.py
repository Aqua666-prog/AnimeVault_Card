"""Безопасные причины ошибок: только известные категории и структурные атрибуты.

Тексты исключений, SQL, параметры запросов и URL никогда не сериализуются.
"""

import errno
import logging
import re
import socket
import ssl

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)

from cardlegends.infrastructure.config import ConfigurationError

# reason -> (техническая причина, следующий шаг); все строки заданы приложением.
REASONS = {
    "database_schema_missing": (
        "PostgreSQL: таблица или представление отсутствует.",
        "Выполните alembic upgrade head в той же БД перед запуском бота.",
    ),
    "database_permission_denied": (
        "PostgreSQL: недостаточно прав на объект БД.",
        "Проверьте права роли: DDL для миграций, SELECT/INSERT/UPDATE players для бота.",
    ),
    "database_authentication_failed": (
        "PostgreSQL отклонил аутентификацию.",
        "Проверьте актуальность учётных данных в настройках хостинга.",
    ),
    "database_not_found": (
        "PostgreSQL: указанная база данных не существует.",
        "Проверьте выбор базы данных в настройках приложения.",
    ),
    "database_connection_limit": (
        "PostgreSQL: исчерпан лимит подключений.",
        "Проверьте лимит БД и число процессов приложения.",
    ),
    "database_unavailable": (
        "PostgreSQL временно не принимает подключения или соединение потеряно.",
        "Проверьте доступность БД из контейнера приложения.",
    ),
    "dns_resolution_failed": (
        "Не удалось разрешить сетевое имя.",
        "Проверьте DNS и доступность внутреннего адреса из контейнера приложения.",
    ),
    "connection_refused": (
        "Соединение отклонено удалённым узлом.",
        "Проверьте адрес, порт и сетевую доступность сервиса на указанном этапе.",
    ),
    "network_unreachable": (
        "Сеть или узел недоступны.",
        "Проверьте сетевые правила и маршрутизацию хостинга.",
    ),
    "connection_timeout": (
        "Истекло время ожидания подключения или операции.",
        "Проверьте сеть и нагрузку сервиса на указанном этапе.",
    ),
    "tls_certificate_verification_failed": (
        "Не удалось проверить TLS-сертификат.",
        "Проверьте доверенный CA и требования провайдера; не отключайте проверку TLS.",
    ),
    "tls_error": (
        "Ошибка TLS-соединения.",
        "Проверьте требования сервиса к TLS и параметры подключения.",
    ),
    "telegram_unauthorized": (
        "Telegram отклонил авторизацию бота.",
        "Проверьте BOT_TOKEN в настройках хостинга и его действительность у BotFather.",
    ),
    "telegram_forbidden": (
        "Telegram запретил операцию.",
        "Проверьте доступность бота и разрешения операции.",
    ),
    "telegram_conflict": (
        "Telegram отклонил getUpdates из-за конфликта polling или активного webhook.",
        "Оставьте один polling-процесс и проверьте getWebhookInfo; удаление webhook отдельно.",
    ),
    "telegram_network_error": (
        "Не удалось выполнить сетевой запрос Telegram Bot API.",
        "Проверьте исходящий HTTPS, DNS и TLS к api.telegram.org.",
    ),
    "telegram_rate_limited": (
        "Telegram ограничил частоту запросов.",
        "Проверьте число процессов и повторных запусков; дождитесь снятия ограничения.",
    ),
    "telegram_request_rejected": (
        "Telegram отклонил параметры запроса.",
        "Проверьте операцию Telegram на указанном этапе; исходный ответ скрыт.",
    ),
    "configuration_invalid": (
        "Конфигурация приложения не прошла проверку.",
        "Проверьте BOT_TOKEN, DATABASE_URL, LOG_LEVEL и совместимость параметров URL.",
    ),
    "unexpected_error": (
        "Исключение не входит в список безопасных диагностических категорий.",
        "Передайте phase, error_type и cause_type разработчику; секреты не нужны.",
    ),
}
SQLSTATE_REASONS = {
    "42P01": "database_schema_missing",
    "42501": "database_permission_denied",
    "28P01": "database_authentication_failed",
    "28000": "database_authentication_failed",
    "3D000": "database_not_found",
    "53300": "database_connection_limit",
    "57P03": "database_unavailable",
}


def exception_chain(error: BaseException) -> list[BaseException]:
    chain = []
    pending = [error]
    seen = set()
    while pending and len(chain) < 16:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        # SQLAlchemy прячет ошибку PostgreSQL в orig, а asyncpg adapter — в cause.
        for nested in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__ if not current.__suppress_context__ else None,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return chain


def failure_details(error: BaseException) -> dict[str, str | int]:
    chain = exception_chain(error)
    result: dict[str, str | int] = {
        "error_type": type(error).__name__,
        "cause_type": type(chain[-1]).__name__,
    }
    reason = "unexpected_error"
    for item in chain:
        code = getattr(item, "sqlstate", None)
        if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code):
            result["sqlstate"] = code
            if code in SQLSTATE_REASONS:
                reason = SQLSTATE_REASONS[code]
            elif code.startswith("08"):
                reason = "database_unavailable"
            break
    # Сначала ищем конкретную причину в цепочке, затем общее сетевое исключение.
    if reason == "unexpected_error":
        types = (
            (ConfigurationError, "configuration_invalid"),
            (ssl.SSLCertVerificationError, "tls_certificate_verification_failed"),
            (ssl.SSLError, "tls_error"),
            (socket.gaierror, "dns_resolution_failed"),
            (ConnectionRefusedError, "connection_refused"),
            (TimeoutError, "connection_timeout"),
            (TelegramUnauthorizedError, "telegram_unauthorized"),
            (TelegramForbiddenError, "telegram_forbidden"),
            (TelegramConflictError, "telegram_conflict"),
            (TelegramRetryAfter, "telegram_rate_limited"),
            (TelegramBadRequest, "telegram_request_rejected"),
            (TelegramNetworkError, "telegram_network_error"),
        )
        for error_type, category in types:
            if any(isinstance(item, error_type) for item in chain):
                reason = category
                break
    for item in chain:
        number = getattr(item, "errno", None)
        if isinstance(number, int):
            result["errno"] = number
            if reason == "unexpected_error" and number in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
                reason = "network_unreachable"
            break
    result["reason"] = reason
    result["technical_reason"], result["hint"] = REASONS[reason]
    return result


def log_failure(logger: logging.Logger, event: str, error: BaseException, phase: str) -> None:
    details = failure_details(error)
    # Некоторые панели показывают только event, игнорируя остальные JSON-поля.
    # Поэтому безопасные детали также включены в текст события.
    metadata = {"phase": phase, **details}
    summary = " ".join(
        f"{key}={value}"
        for key, value in metadata.items()
        if key not in {"technical_reason", "hint"}
    )
    logger.error(
        "%s %s: %s Действие: %s",
        event,
        summary,
        details["technical_reason"],
        details["hint"],
        extra=metadata,
    )
