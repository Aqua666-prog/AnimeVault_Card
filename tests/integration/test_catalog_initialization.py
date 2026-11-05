"""Real PostgreSQL: startup bootstrap, preservation, locking and separate DB privileges."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import ProgrammingError

from cardlegends.application.game import GameService
from cardlegends.infrastructure.catalog import CATALOG_LOCK, CatalogIdentityConflict, seed
from cardlegends.infrastructure.database import create_database
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


async def snapshot(engine):
    async with engine.connect() as conn:
        tables = await conn.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        )
        return {
            table: sorted(
                map(str, await conn.scalars(text(f'SELECT to_jsonb(t) FROM "{table}" t')))
            )
            for table in tables
        }


async def test_empty_database_then_repeat_is_exactly_unchanged(database):
    engine, sessions = database
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM characters")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM banners")) == 0
    await seed(sessions)
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM characters")) == 30
        assert await conn.scalar(text("SELECT count(*) FROM franchises")) == 6
        assert await conn.scalar(text("SELECT count(*) FROM banner_pools")) == 30
        assert await conn.scalar(text("SELECT count(*) FROM players")) == 0
        row = (await conn.execute(text("SELECT * FROM banners WHERE id = 1"))).mappings().one()
        assert row["active"] and row["starts_at"] is None and row["ends_at"] is None
        assert row["weights"] == [50, 30, 15, 4, 1]
    before = await snapshot(engine)
    await seed(sessions)
    assert await snapshot(engine) == before


async def test_existing_game_art_catalog_and_custom_banner_are_preserved(database):
    engine, sessions = database
    await seed(sessions)
    await PostgresPlayerRepository(sessions).upsert(42, "Игрок", None)
    game = GameService(sessions)
    await game.profile(42)
    await game.summon(42, 1, "existing-summon")
    await game.daily(42)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE characters SET name_ru = 'Изменено', is_active = false WHERE id = 1")
        )
        await conn.execute(
            text(
                "UPDATE banners SET single_cost = 123, ten_cost = 999, "
                "active = false, weights = '[40,40,10,9,1]' WHERE id = 1"
            )
        )
        await conn.execute(text("DELETE FROM banner_pools WHERE character_id != 1"))
        await conn.execute(text("UPDATE owned_cards SET favorite = true"))
        await conn.execute(
            text(
                "INSERT INTO image_assets "
                "(id, character_slug, source, post_id, page_url, permission, "
                "sfw_reviewed, metadata_json, content) VALUES "
                "(:id, 'custom', 'manual', '1', 'local', 'approved', true, '{}', "
                "decode('abcd', 'hex'))"
            ),
            {"id": uuid4()},
        )
        await conn.execute(
            text(
                "UPDATE characters SET image_asset_id = "
                "(SELECT id FROM image_assets LIMIT 1) WHERE id = 1"
            )
        )
    before = await snapshot(engine)
    await seed(sessions)
    assert await snapshot(engine) == before


async def test_partial_catalog_is_completed_without_overwriting_rows(database):
    engine, sessions = database
    await seed(sessions)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM banner_pools"))
        await conn.execute(text("DELETE FROM banners"))
        await conn.execute(text("DELETE FROM characters WHERE id > 1"))
        await conn.execute(text("UPDATE characters SET description = 'Сохранить' WHERE id = 1"))
    await seed(sessions)
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM characters")) == 30
        assert await conn.scalar(text("SELECT count(*) FROM banner_pools")) == 30
        assert (
            await conn.scalar(text("SELECT description FROM characters WHERE id = 1"))
            == "Сохранить"
        )


async def test_identity_collision_fails_without_changes(database):
    engine, sessions = database
    await seed(sessions)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE characters SET slug = 'operator-character' WHERE id = 1"))
    before = await snapshot(engine)
    with pytest.raises(CatalogIdentityConflict):
        await seed(sessions)
    assert await snapshot(engine) == before


async def test_concurrent_initializers_serialize_on_transaction_lock(database):
    engine, sessions = database
    async with engine.begin() as blocker:
        await blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_LOCK})
        tasks = [asyncio.create_task(seed(sessions)) for _ in range(3)]
        try:
            async with asyncio.timeout(3):
                while True:
                    async with engine.connect() as conn:
                        waiting = await conn.scalar(
                            text(
                                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                                "AND NOT granted AND objid = :objid AND classid = :classid"
                            ),
                            {"objid": CATALOG_LOCK & 0xFFFFFFFF, "classid": CATALOG_LOCK >> 32},
                        )
                    if waiting == 3:
                        break
                    await asyncio.sleep(0.02)
            assert not any(task.done() for task in tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    await asyncio.gather(*tasks)
    before = await snapshot(engine)
    await seed(sessions)
    assert await snapshot(engine) == before


async def test_seed_privileges_differ_from_migrations_and_rollback_atomically(
    database, database_url
):
    engine, _ = database
    role = "bootstrap_test_" + uuid4().hex
    limited, sessions = create_database(database_url)

    @event.listens_for(limited.sync_engine, "connect")
    def set_role(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET ROLE "{role}"')
        cursor.close()

    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            await conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
            await conn.execute(text(f'GRANT INSERT ON franchises, characters TO "{role}"'))
        before = await snapshot(engine)
        with pytest.raises(ProgrammingError):
            await seed(sessions)  # Banners INSERT denied, earlier inserts must roll back.
        assert await snapshot(engine) == before
        async with engine.begin() as conn:
            await conn.execute(text(f'GRANT INSERT ON banners, banner_pools TO "{role}"'))
        await seed(sessions)  # No owner, DDL, UPDATE, DELETE, sequence or superuser rights needed.
        before = await snapshot(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f'REVOKE INSERT ON franchises, characters, banners, banner_pools FROM "{role}"'
                )
            )
        await seed(sessions)  # An initialized installation works even with SELECT-only catalog.
        assert await snapshot(engine) == before
    finally:
        await limited.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP OWNED BY "{role}"'))
            await conn.execute(text(f'DROP ROLE "{role}"'))
