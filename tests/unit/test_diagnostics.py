import errno
import json
import logging
import socket
import ssl
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramConflictError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetUpdates
from sqlalchemy.exc import ProgrammingError

from cardlegends import main as entrypoint
from cardlegends.infrastructure.diagnostics import failure_details, log_failure
from cardlegends.infrastructure.logging import JsonFormatter

SECRET = "private-test-marker-must-never-be-logged"


class PostgresFailure(Exception):
    def __init__(self, code):
        self.sqlstate = code
        super().__init__(SECRET)


@pytest.mark.parametrize(
    "code,reason",
    [
        ("42P01", "database_schema_missing"),
        ("42501", "database_permission_denied"),
        ("28P01", "database_authentication_failed"),
        ("3D000", "database_not_found"),
        ("53300", "database_connection_limit"),
        ("57P03", "database_unavailable"),
        ("08006", "database_unavailable"),
    ],
)
def test_sqlalchemy_orig_preserves_sqlstate_without_sql_or_parameters(code, reason):
    error = ProgrammingError(SECRET, {"password": SECRET}, PostgresFailure(code))
    result = failure_details(error)
    assert result["sqlstate"] == code
    assert result["reason"] == reason
    assert result["error_type"] == "ProgrammingError"
    assert result["cause_type"] == "PostgresFailure"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "error,reason",
    [
        (socket.gaierror(-2, SECRET), "dns_resolution_failed"),
        (ConnectionRefusedError(errno.ECONNREFUSED, SECRET), "connection_refused"),
        (OSError(errno.ENETUNREACH, SECRET), "network_unreachable"),
        (TimeoutError(SECRET), "connection_timeout"),
        (ssl.SSLCertVerificationError(1, SECRET), "tls_certificate_verification_failed"),
        (RuntimeError(SECRET), "unexpected_error"),
    ],
)
def test_safe_network_and_unknown_reasons(error, reason):
    result = failure_details(error)
    assert result["reason"] == reason
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "error_type,reason",
    [
        (TelegramUnauthorizedError, "telegram_unauthorized"),
        (TelegramConflictError, "telegram_conflict"),
        (TelegramNetworkError, "telegram_network_error"),
    ],
)
def test_aiogram_log_arguments_keep_safe_reason_when_message_is_hidden(error_type, reason):
    error = error_type(method=GetUpdates(), message=SECRET)
    record = logging.LogRecord(
        "aiogram.dispatcher",
        logging.ERROR,
        "",
        1,
        "Failed to fetch updates - %s: %s",
        (type(error).__name__, error),
        None,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["reason"] == reason
    assert payload["error_type"] == error_type.__name__
    assert reason in payload["event"]
    assert SECRET not in json.dumps(payload)


def test_nested_tls_reason_is_more_precise_than_generic_telegram_network_error():
    error = TelegramNetworkError(method=GetUpdates(), message=SECRET)
    error.__cause__ = ssl.SSLCertVerificationError(1, SECRET)
    result = failure_details(error)
    assert result["reason"] == "tls_certificate_verification_failed"
    assert result["cause_type"] == "SSLCertVerificationError"


def test_exception_chain_with_cycle_terminates():
    error = RuntimeError(SECRET)
    error.__cause__ = error
    assert failure_details(error)["reason"] == "unexpected_error"


def test_safe_diagnostics_are_in_json_and_event_for_message_only_cloud_panels():
    records = []
    logger = logging.Logger("cardlegends.test", logging.ERROR)
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    error = ProgrammingError(SECRET, {"token": SECRET}, PostgresFailure("42P01"))
    log_failure(logger, "startup_or_polling_failed", error, "database_check")
    payload = json.loads(JsonFormatter().format(records[0]))
    for key in ("error_type", "cause_type", "reason", "phase", "sqlstate"):
        assert f"{key}={payload[key]}" in payload["event"]
    assert payload["technical_reason"]
    assert "alembic upgrade head" in payload["hint"]
    assert SECRET not in json.dumps(payload)


def test_type_error_does_not_guess_which_driver_argument_failed():
    records = []
    logger = logging.Logger("cardlegends.test", logging.ERROR)
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    log_failure(logger, "startup_or_polling_failed", TypeError(SECRET), "database_check")
    assert records[0].reason == "unexpected_error"
    assert records[0].error_type == "TypeError"


def test_main_logs_cause_and_phase_without_traceback_or_secret(monkeypatch, capsys):
    original = PostgresFailure("42501")
    monkeypatch.setattr(
        entrypoint,
        "run",
        AsyncMock(side_effect=entrypoint.StartupFailure("database_check", original)),
    )
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    stderr = capsys.readouterr().err
    payload = json.loads(stderr)
    assert payload["reason"] == "database_permission_denied"
    assert payload["phase"] == "database_check"
    assert "error_type=PostgresFailure" in payload["event"]
    assert SECRET not in stderr
    assert "Traceback" not in stderr


def test_arbitrary_exception_message_cannot_leak_token_password_or_full_dsn():
    token = "123456:" + "a" * 35
    password = "private-test-password-marker"
    dsn = f"postgresql+asyncpg://user:{password}@host/db"
    raw_message = f"{dsn} https://api.telegram.org/bot{token}/getUpdates"
    error = RuntimeError(raw_message)
    record = logging.LogRecord(
        "aiogram.dispatcher",
        logging.ERROR,
        "",
        1,
        "%s",
        (error,),
        None,
    )
    serialized = JsonFormatter().format(record)
    for sensitive in (token, password, dsn):
        assert sensitive not in serialized
    assert json.loads(serialized)["reason"] == "unexpected_error"


def test_catalog_conflict_has_safe_actionable_diagnostic():
    from cardlegends.infrastructure.catalog import CatalogIdentityConflict

    details = failure_details(CatalogIdentityConflict(SECRET))
    assert details["reason"] == "catalog_identity_conflict"
    assert "assets/catalog.json" in details["hint"]
    assert SECRET not in json.dumps(details)
