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

from cardlegends.infrastructure.catalog import CatalogIdentityConflict
from cardlegends.infrastructure.config import ConfigurationError

# reason -> (техническая причина, следующий шаг); все строки заданы приложением.
REASONS = {
    "database_schema_missing": (
        "PostgreSQL: таблица или представление отсутствует.",
        "Выполните alembic upgrade head в той же БД перед запуском бота.",
    ),
    "catalog_identity_conflict": (
        "Зарезервированные ID/slug стартового каталога заняты другими персонажами.",
        "Сверьте каталог с assets/catalog.json; данные не изменены, нужен разбор оператора.",
    ),
    "database_permission_denied": (
        "PostgreSQL: недостаточно прав на объект БД.",
        "Проверьте DDL для миграций и runtime grants; для первого seed нужны "
        "SELECT/INSERT на franchises, characters, banners, banner_pools (см. README).",
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
    "connection_reset": (
        "Удалённый узел или посредник сбросил соединение.",
        "Проверьте endpoint, порт, сетевые правила и логи сервиса/прокси в момент сбоя.",
    ),
    "connection_closed": (
        "Соединение было закрыто или прервано.",
        "Проверьте доступность endpoint, требования TLS и логи сервиса/прокси.",
    ),
    "connection_error": (
        "Ошибка соединения без более точной структурной причины.",
        "Проверьте endpoint, DNS, порт, TLS и логи PostgreSQL/прокси; Ready недостаточно.",
    ),
    "postgres_tls_upgrade_rejected": (
        "Драйвер сообщил, что endpoint отклонил PostgreSQL SSLRequest.",
        "Сверьте endpoint/порт и режим TLS с провайдером; не отключайте TLS наугад.",
    ),
    "postgres_ssl_exchange_error": (
        "До установки TLS получен PostgreSQL ErrorResponse; его поля не подтверждены.",
        "Проверьте логи PostgreSQL/прокси и TLS-настройки; данные до TLS не считаются доверенными.",
    ),
    "tls_handshake_closed": (
        "Соединение закрылось во время TLS handshake.",
        "Проверьте требования TLS, endpoint, сертификаты и логи PostgreSQL/прокси.",
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


def safe_error_origin(chain: list[BaseException]) -> str | None:
    """Последний известный module:function:line, без путей, исходников и locals."""
    for error in reversed(chain):
        origin = None
        trace = error.__traceback__
        while trace is not None:
            module = trace.tb_frame.f_globals.get("__name__", "")
            function = trace.tb_frame.f_code.co_name
            allowed = isinstance(module, str) and (
                module in {"ssl", "socket"}
                or module.startswith(("asyncpg.", "sqlalchemy.", "asyncio.", "cardlegends."))
            )
            if (
                allowed
                and re.fullmatch(r"[A-Za-z0-9_.]+", module)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function)
            ):
                origin = f"{module}:{function}:{trace.tb_lineno}"
            trace = trace.tb_next
        if origin is not None:
            return origin
    return None


def failure_details(error: BaseException) -> dict[str, str | int]:
    chain = exception_chain(error)
    result: dict[str, str | int] = {
        "error_type": type(error).__name__,
        "cause_type": type(chain[-1]).__name__,
    }
    origin = safe_error_origin(chain)
    if origin is not None:
        result["error_origin"] = origin
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
            (CatalogIdentityConflict, "catalog_identity_conflict"),
            (ssl.SSLCertVerificationError, "tls_certificate_verification_failed"),
            (ssl.SSLError, "tls_error"),
            (socket.gaierror, "dns_resolution_failed"),
            (ConnectionRefusedError, "connection_refused"),
            (ConnectionResetError, "connection_reset"),
            (ConnectionAbortedError, "connection_closed"),
            (BrokenPipeError, "connection_closed"),
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
    if reason == "unexpected_error":
        # Только фиксированные сообщения из asyncpg; переменный host:port не выводится.
        # ErrorResponse до TLS не разбираем: это недоверенные данные (CVE-2024-10977).
        for item in chain:
            if (
                type(item) is not ConnectionError
                or not item.args
                or not isinstance(item.args[0], str)
            ):
                continue
            message = item.args[0]
            if re.fullmatch(r'PostgreSQL server at "[^\r\n]+" rejected SSL upgrade', message):
                reason = "postgres_tls_upgrade_rejected"
            elif re.fullmatch(
                r'PostgreSQL server at "[^\r\n]+": '
                r"server sent an error response during SSL exchange; "
                r"check the server logs for details",
                message,
            ):
                reason = "postgres_ssl_exchange_error"
            elif message == "connection closed during TLS handshake":
                reason = "tls_handshake_closed"
            elif message == "unexpected connection_lost() call":
                reason = "connection_closed"
            if reason != "unexpected_error":
                break
    if reason == "unexpected_error":
        # Более точные типы/errno всегда имеют приоритет над базовым ConnectionError.
        numbers = {item.errno for item in chain if isinstance(getattr(item, "errno", None), int)}
        if (
            any(isinstance(item, ConnectionResetError) for item in chain)
            or errno.ECONNRESET in numbers
        ):
            reason = "connection_reset"
        elif any(
            isinstance(item, (ConnectionAbortedError, BrokenPipeError)) for item in chain
        ) or numbers.intersection({errno.ECONNABORTED, errno.EPIPE}):
            reason = "connection_closed"
        elif errno.ECONNREFUSED in numbers:
            reason = "connection_refused"
        elif errno.ETIMEDOUT in numbers:
            reason = "connection_timeout"
        elif any(isinstance(item, ConnectionError) for item in chain):
            reason = "connection_error"
    result["reason"] = reason
    result["technical_reason"], result["hint"] = REASONS[reason]
    return result


def log_failure(
    logger: logging.Logger,
    event: str,
    error: BaseException,
    phase: str,
    *,
    context: dict[str, str | int] | None = None,
) -> None:
    details = failure_details(error)
    # Некоторые панели показывают только event, игнорируя остальные JSON-поля.
    # Поэтому безопасные детали также включены в текст события.
    metadata = {**(context or {}), "phase": phase, **details}
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
