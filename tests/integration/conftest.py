import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from cardlegends.infrastructure.database import create_database

ROOT = Path(__file__).resolve().parents[2]


def migrate(url, target, command="upgrade"):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, target],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "Alembic failed (details omitted to protect credentials)"


@pytest.fixture
def database_url():
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "TEST_DATABASE_URL не задан: требуется отдельная PostgreSQL 16+ с суффиксом _test"
        )
    url = make_url(value)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").endswith("_test"):
        pytest.fail("Тесты требуют PostgreSQL asyncpg и отдельную БД с суффиксом _test")
    return value


@pytest.fixture
async def database(database_url):
    # Эти тесты применяют и откатывают миграцию; только отдельная тестовая БД, без xdist.
    await asyncio.to_thread(migrate, database_url, "head")
    engine, sessions = create_database(database_url)
    try:
        async with engine.begin() as conn:
            version = int(await conn.scalar(text("SHOW server_version_num")))
            assert version >= 160000
            await conn.execute(text("TRUNCATE players CASCADE"))
        yield engine, sessions
    finally:
        await engine.dispose()
        await asyncio.to_thread(migrate, database_url, "base", "downgrade")
        verification, _ = create_database(database_url)
        try:
            async with verification.connect() as conn:
                assert await conn.scalar(text("SELECT to_regclass('public.players')")) is None
        finally:
            await verification.dispose()


@pytest.fixture
def fate_assets(tmp_path):
    # Tiny synthetic fixtures exercise byte-preserving delivery, never shipped as card art.
    from PIL import Image

    from cardlegends.domain.fate import CARD_IDS

    root = tmp_path / "cards"
    root.mkdir()
    for i, code in enumerate(CARD_IDS):
        Image.new("RGB", (80, 120), (20 + i, 40, 60)).save(root / f"{code}.png")
    return root


@pytest.fixture
async def fate_game(database, fate_assets):
    from cardlegends.infrastructure.catalog import seed
    from cardlegends.infrastructure.game_repository import GameService
    from cardlegends.infrastructure.players import PostgresPlayerRepository

    _, sessions = database
    await seed(sessions)
    await PostgresPlayerRepository(sessions).upsert(42, "Игрок", None)
    return GameService(sessions, randbelow=lambda _: 0, cooldown_seconds=0, cards_dir=fate_assets)
