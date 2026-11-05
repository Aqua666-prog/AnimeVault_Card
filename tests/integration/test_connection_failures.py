"""Локальные TCP-имитаторы: проверка настоящего asyncpg, без доступа к Dada Cloud."""

import asyncio
import json
import os
import socket
import struct

import pytest

from cardlegends.infrastructure import database as db
from cardlegends.infrastructure.diagnostics import failure_details

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "response,reason",
    [
        (b"N", "postgres_tls_upgrade_rejected"),
        # Поля/SQLSTATE недоверенного ErrorResponse не должны попадать в диагностику.
        (b"Eprivate-test-server-error-password-marker-42P01", "postgres_ssl_exchange_error"),
    ],
)
async def test_real_asyncpg_ssl_exchange_is_safely_classified(response, reason):
    completed = asyncio.get_running_loop().create_future()

    async def reject_ssl(reader, writer):
        try:
            packet = await reader.readexactly(8)
            assert packet == struct.pack("!ll", 8, 80877103)
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            if not completed.done():
                completed.set_result(None)

    async with await asyncio.start_server(reject_ssl, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"postgresql+asyncpg://test:private-test-password@127.0.0.1:{port}/db?ssl=require"
        engine, _ = db.create_database(url)
        try:
            with pytest.raises(ConnectionError) as error:
                async with engine.connect():
                    pytest.fail("Connection to the rejecting stub must fail")
            result = failure_details(error.value)
            assert result["reason"] == reason
            assert result["error_type"] == result["cause_type"] == "ConnectionError"
            assert result["error_origin"].startswith("asyncpg.connect_utils:")
            assert "/" not in result["error_origin"]
            assert "sqlstate" not in result
            assert "127.0.0.1" not in json.dumps(result)
            assert "private-test" not in json.dumps(result)
            await asyncio.wait_for(completed, 2)
        finally:
            await engine.dispose()


async def test_real_connect_to_bound_but_non_listening_socket_is_refused():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        engine, _ = db.create_database(
            f"postgresql+asyncpg://test:test_only@127.0.0.1:{port}/db?ssl=disable"
        )
        try:
            with pytest.raises(OSError) as error:
                async with engine.connect():
                    pytest.fail("Non-listening socket must refuse connection")
            assert failure_details(error.value)["reason"] == "connection_refused"
        finally:
            await engine.dispose()


async def test_stalled_ssl_exchange_obeys_connect_timeout(monkeypatch):
    monkeypatch.setattr(db, "CONNECT_TIMEOUT_SECONDS", 0.1)
    completed = asyncio.get_running_loop().create_future()

    async def stall(reader, writer):
        try:
            await reader.read()  # Закроется при отмене подключения клиентом.
        finally:
            writer.close()
            await writer.wait_closed()
            if not completed.done():
                completed.set_result(None)

    async with await asyncio.start_server(stall, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        engine, _ = db.create_database(
            f"postgresql+asyncpg://test:test_only@127.0.0.1:{port}/db?ssl=require"
        )
        try:
            with pytest.raises(TimeoutError) as error:
                async with engine.connect():
                    pytest.fail("Stalled SSL exchange must time out")
            assert failure_details(error.value)["reason"] == "connection_timeout"
            await asyncio.wait_for(completed, 2)
        finally:
            await engine.dispose()


async def test_alembic_tls_failure_blocks_bot_in_the_cloud_start_command():
    async def reject(reader, writer):
        try:
            await reader.readexactly(8)
            writer.write(b"N")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(reject, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"postgresql+asyncpg://test:private-test-password@127.0.0.1:{port}/db?ssl=require"
        environment = {**os.environ, "DATABASE_URL": url, "LOG_LEVEL": "INFO"}
        environment.pop("BOT_TOKEN", None)
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            "alembic upgrade head && exec python -m cardlegends.main",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        output = stderr.decode()
        assert process.returncode == 1
        records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        failure = next(record for record in records if record["level"] == "ERROR")
        assert failure["phase"] == "migration_connect"
        assert failure["reason"] == "postgres_tls_upgrade_rejected"
        assert failure["db_tls_mode"] == "require"
        assert failure["db_tls_source"] == "url"
        assert failure["db_port"] == port
        assert failure["error_origin"].startswith("asyncpg.connect_utils:")
        assert "db_tls_mode=require" in failure["event"]
        assert "error_origin=" in failure["event"]
        assert "startup_phase" not in output
        assert "configuration_failed" not in output
        assert "private-test-password" not in output + stdout.decode()
        assert "127.0.0.1" not in output
        assert url not in output
