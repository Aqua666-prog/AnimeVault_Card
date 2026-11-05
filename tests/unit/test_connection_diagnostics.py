import errno
import json
import logging
import socket
import ssl

import pytest
from sqlalchemy.exc import OperationalError

from cardlegends.infrastructure.database import database_log_context
from cardlegends.infrastructure.diagnostics import failure_details, log_failure
from cardlegends.infrastructure.logging import JsonFormatter

PRIVATE = "private-test-endpoint-marker"


@pytest.mark.parametrize(
    "message,reason",
    [
        (
            f'PostgreSQL server at "{PRIVATE}:5432" rejected SSL upgrade',
            "postgres_tls_upgrade_rejected",
        ),
        (
            f'PostgreSQL server at "{PRIVATE}:5432": '
            "server sent an error response during SSL exchange; check the server logs for details",
            "postgres_ssl_exchange_error",
        ),
        ("connection closed during TLS handshake", "tls_handshake_closed"),
        ("unexpected connection_lost() call", "connection_closed"),
        (PRIVATE, "connection_error"),
    ],
)
def test_plain_connection_error_is_categorized_without_exposing_its_message(message, reason):
    result = failure_details(ConnectionError(message))
    assert result["reason"] == reason
    assert result["error_type"] == result["cause_type"] == "ConnectionError"
    assert PRIVATE not in json.dumps(result)
    assert "sqlstate" not in result


@pytest.mark.parametrize(
    "inner,reason",
    [
        (socket.gaierror(-2, PRIVATE), "dns_resolution_failed"),
        (ssl.SSLCertVerificationError(1, PRIVATE), "tls_certificate_verification_failed"),
        (ConnectionResetError(errno.ECONNRESET, PRIVATE), "connection_reset"),
        (ConnectionAbortedError(errno.ECONNABORTED, PRIVATE), "connection_closed"),
        (BrokenPipeError(errno.EPIPE, PRIVATE), "connection_closed"),
        (TimeoutError(PRIVATE), "connection_timeout"),
    ],
)
def test_specific_inner_failure_wins_over_generic_connection_error(inner, reason):
    outer = ConnectionError(PRIVATE)
    outer.__cause__ = inner
    result = failure_details(OperationalError(PRIVATE, {"password": PRIVATE}, outer))
    assert result["reason"] == reason
    assert result["cause_type"] == type(inner).__name__
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(
    "code,reason",
    [
        (errno.ECONNRESET, "connection_reset"),
        (errno.ECONNABORTED, "connection_closed"),
        (errno.EPIPE, "connection_closed"),
        (errno.ECONNREFUSED, "connection_refused"),
        (errno.ETIMEDOUT, "connection_timeout"),
    ],
)
def test_plain_oserror_errno_is_recognized(code, reason):
    assert failure_details(OSError(code, PRIVATE))["reason"] == reason


def test_arbitrary_connection_error_message_is_not_quoted_or_guessed():
    error = ConnectionError(f"ssl failure: {PRIVATE} password={PRIVATE}")
    result = failure_details(error)
    assert result["reason"] == "connection_error"
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(
    "url_options,env_value,expected,source",
    [
        ("", None, "prefer", "driver_default"),
        ("", "verify-full", "verify-full", "environment"),
        ("", PRIVATE, "invalid", "environment"),
        ("?sslmode=require", "disable", "require", "url"),
        ("?ssl=verify-full", None, "verify-full", "url"),
        (f"?ssl={PRIVATE}", None, "invalid", "url"),
    ],
)
def test_safe_context_contains_effective_tls_mode_and_no_address_or_credentials(
    monkeypatch,
    url_options,
    env_value,
    expected,
    source,
):
    monkeypatch.delenv("PGSSLMODE", raising=False)
    if env_value is not None:
        monkeypatch.setenv("PGSSLMODE", env_value)
    context = database_log_context(
        f"postgresql+asyncpg://{PRIVATE}:{PRIVATE}@{PRIVATE}:5432/{PRIVATE}{url_options}"
    )
    assert context["db_tls_mode"] == expected
    assert context["db_tls_source"] == source
    assert context["db_host_kind"] == "dns"
    assert context["db_port"] == 5432
    assert context["db_connect_timeout_s"] == context["db_command_timeout_s"] == 5
    assert PRIVATE not in json.dumps(context)


def test_connection_context_is_available_in_json_and_message_only_logs(monkeypatch):
    monkeypatch.delenv("PGSSLMODE", raising=False)
    context = database_log_context(f"postgresql+asyncpg://user:{PRIVATE}@{PRIVATE}/db?ssl=require")
    records = []
    logger = logging.Logger("cardlegends.test", logging.ERROR)
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    log_failure(
        logger,
        "migration_failed",
        ConnectionError(PRIVATE),
        "migration_connect",
        context={**context, "elapsed_ms": 23},
    )
    payload = json.loads(JsonFormatter().format(records[0]))
    for key in (*context, "elapsed_ms", "phase", "reason"):
        assert f"{key}={payload[key]}" in payload["event"]
    assert payload["reason"] == "connection_error"
    assert PRIVATE not in json.dumps(payload)


def test_origin_does_not_expose_local_paths_or_unknown_modules():
    # Локальный тестовый модуль не входит в whitelist источников исключений.
    try:
        raise ConnectionError(PRIVATE)
    except ConnectionError as error:
        result = failure_details(error)
    assert "error_origin" not in result
    assert PRIVATE not in json.dumps(result)


def test_db_check_invalid_configuration_is_safe(monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("DATABASE_URL", PRIVATE)
    result = subprocess.run(
        [sys.executable, "-m", "cardlegends.infrastructure.db_check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["reason"] == "configuration_invalid"
    assert payload["phase"] == "database_configuration"
    assert PRIVATE not in result.stderr + result.stdout
