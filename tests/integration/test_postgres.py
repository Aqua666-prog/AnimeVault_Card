import asyncio

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from cardlegends.application.players import PlayerService
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


async def test_repeat_registration_persists_after_recreating_engine(database, database_url):
    engine, sessions = database
    repository = PostgresPlayerRepository(sessions)
    first = await repository.upsert(2**40, "Старое имя", "old_name")
    second = await repository.upsert(2**40, "Новое имя", None)
    assert first.id == second.id
    assert first.created_at == second.created_at
    assert second.updated_at >= first.updated_at
    assert second.last_seen_at is not None
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM players")) == 1
    await engine.dispose()
    fresh_engine, fresh_sessions = create_database(database_url)
    try:
        profile = await PlayerService(PostgresPlayerRepository(fresh_sessions)).profile(2**40)
        assert profile.display_name == "Новое имя"
        assert profile.registered_at == first.created_at
        assert profile.status == "Новичок"
    finally:
        await fresh_engine.dispose()


async def test_concurrent_registration_is_atomic(database):
    engine, sessions = database
    repository = PostgresPlayerRepository(sessions)
    players = await asyncio.gather(
        *[repository.upsert(2**40, f"Игрок {n}", None) for n in range(30)]
    )
    assert len({p.id for p in players}) == 1
    assert len({p.created_at for p in players}) == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM players")) == 1


async def test_same_username_is_allowed_for_different_players(database):
    _, sessions = database
    repository = PostgresPlayerRepository(sessions)
    first = await repository.upsert(1, "Первый", "shared")
    second = await repository.upsert(2, "Второй", "shared")
    assert first.id != second.id
    assert await repository.get_by_telegram_id(3) is None


async def test_migration_schema_and_unique_constraint(database):
    engine, _ = database
    async with engine.connect() as connection:
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("players"))
        assert {col["name"] for col in columns} == {
            "id",
            "telegram_id",
            "display_name",
            "username",
            "created_at",
            "updated_at",
            "last_seen_at",
        }
        columns = {col["name"]: col for col in columns}
        assert columns["telegram_id"]["nullable"] is False
        assert str(columns["telegram_id"]["type"]) == "BIGINT"
        assert columns["display_name"]["type"].length == 255
        assert columns["created_at"]["type"].timezone is True
        assert columns["last_seen_at"]["nullable"] is True
        constraints = await connection.run_sync(
            lambda conn: inspect(conn).get_unique_constraints("players")
        )
        assert any(c["column_names"] == ["telegram_id"] for c in constraints)
    async with engine.begin() as connection:
        await connection.execute(
            text("""
            INSERT INTO players(id, telegram_id, display_name)
            VALUES ('00000000-0000-0000-0000-000000000001', 42, 'Первый')
        """)
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO players(id, telegram_id, display_name)
                VALUES ('00000000-0000-0000-0000-000000000002', 42, 'Второй')
            """)
            )


async def test_alembic_matches_orm_metadata(database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from cardlegends.infrastructure.models import Base

    engine, _ = database
    async with engine.connect() as connection:
        differences = await connection.run_sync(
            lambda conn: compare_metadata(MigrationContext.configure(conn), Base.metadata)
        )
    assert differences == []
