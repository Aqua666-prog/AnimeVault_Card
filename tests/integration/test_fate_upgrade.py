"""Upgrade a populated v0.2 schema: preserve balances, inventory, art and history."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from cardlegends.infrastructure.catalog import seed
from cardlegends.infrastructure.game_repository import GameService
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


async def migration(url, target, action="upgrade"):
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", action, target],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "Migration failed; output omitted to protect credentials"


async def test_populated_v020_upgrade_keeps_all_original_fields_and_archived_functions(
    database, database_url, fate_assets
):
    engine, sessions = database
    await engine.dispose()
    await migration(database_url, "54b3f0a493e1", "downgrade")
    # Populate the historical schema directly: current ORM models reference newer columns.
    player = SimpleNamespace(id=uuid4())
    sid, card_id, art_id = uuid4(), uuid4(), uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO players(id, telegram_id, display_name) "
                "VALUES (:id, 42, 'Старый игрок')"
            ),
            {"id": player.id},
        )
        await c.execute(
            text("INSERT INTO wallets(player_id, balance, xp) VALUES (:id, 1000, 0)"),
            {"id": player.id},
        )
        await c.execute(text("INSERT INTO franchises (id, name) VALUES ('fate', 'Fate')"))
        await c.execute(
            text(
                "INSERT INTO image_assets "
                "(id, character_slug, source, post_id, page_url, permission, sfw_reviewed, "
                "metadata_json, content, license, evidence, attribution) VALUES "
                "(:id, 'artoria', 'manual', '1', 'local', 'approved', true, '{}', :png, "
                "'owned', 'operator', 'artist')"
            ),
            {"id": art_id, "png": (fate_assets / "AV-F001.png").read_bytes()},
        )
        await c.execute(
            text(
                "INSERT INTO characters "
                "(id, slug, name_ru, name_en, franchise_id, rarity, description, tags, is_active, "
                "image_asset_id) VALUES (1, 'artoria', 'Артурия', 'Artoria Pendragon', 'fate', "
                "'SSR', 'legacy', '{}', true, :art)"
            ),
            {"art": art_id},
        )
        await c.execute(
            text(
                "INSERT INTO banners (id, name, description, weights, single_cost, "
                "ten_cost, guaranteed_sr, active) VALUES (1, 'Custom legacy', 'Keep', "
                "'[50,30,15,4,1]', 211, 1777, true, false)"
            )
        )
        await c.execute(text("INSERT INTO banner_pools VALUES (1, 1)"))
        await c.execute(
            text("UPDATE wallets SET balance = 789, xp = 77 WHERE player_id = :p"), {"p": player.id}
        )
        await c.execute(
            text(
                "INSERT INTO owned_cards (id, player_id, character_id, favorite) "
                "VALUES (:id, :p, 1, true)"
            ),
            {"id": card_id, "p": player.id},
        )
        await c.execute(
            text(
                "INSERT INTO summons (id, player_id, banner_id, key, count) "
                "VALUES (:id, :p, 1, :key, 1)"
            ),
            {"id": sid, "p": player.id, "key": uuid4().hex},
        )
        await c.execute(
            text("INSERT INTO summon_results VALUES (:sid, 0, :card)"),
            {"sid": sid, "card": card_id},
        )
        await c.execute(
            text(
                "INSERT INTO currency_transactions "
                "(id, player_id, kind, delta, balance_after, related_id, key) "
                "VALUES (:id, :p, 'summon', -211, 789, :sid, 'legacy-summon')"
            ),
            {"id": uuid4(), "p": player.id, "sid": str(sid)},
        )
        # An old stage-one account never had a wallet. It must not receive a new bonus.
        await c.execute(
            text(
                "INSERT INTO players (id, telegram_id, display_name) "
                "VALUES (:id, 43, 'No old wallet')"
            ),
            {"id": uuid4()},
        )
        tables = list(
            await c.scalars(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' "
                    "AND tablename != 'alembic_version'"
                )
            )
        )
        before = {
            table: list(await c.scalars(text(f'SELECT to_jsonb(t) FROM "{table}" t')))
            for table in tables
        }
    await engine.dispose()
    await migration(database_url, "head")
    await migration(database_url, "head")
    await seed(sessions)
    await seed(sessions)
    async with engine.connect() as c:
        # Every original row still exists and every original column value is identical.
        for table, rows in before.items():
            after = list(await c.scalars(text(f'SELECT to_jsonb(t) FROM "{table}" t')))
            for old in rows:
                assert any(all(new[k] == value for k, value in old.items()) for new in after), table
        assert (
            await c.scalar(text("SELECT total_cost FROM summons WHERE id = :sid"), {"sid": sid})
            == 211
        )
        assert await c.scalar(text("SELECT count(*) FROM characters")) == 5  # 1 archive + 4 Fate
    game = GameService(sessions, cards_dir=fate_assets)
    profile = await game.profile(42)
    assert profile["balance"] == 789 and profile["xp"] == 77
    assert profile["training_xp"] == 77  # One-time migration from the historical EXP total.
    assert profile["unique"] == 0 and profile["size"] == 4 and profile["archived"] == 1
    assert len(await game.results(42, sid)) == 1
    card, character, copies = await game.card(42, card_id)
    assert character.id == 1 and card.favorite and copies == 1
    assert await game.illustration(art_id) is not None
    assert (await game.browse(42, owned=True, archived=True))[1] == 1
    assert (await game.browse(42))[1] == 4
    await game.favorite(42, card_id, False)
    assert not (await game.card(42, card_id))[0].favorite
    await PostgresPlayerRepository(sessions).upsert(42, "Старый игрок", None)
    assert (await game.profile(42))["balance"] == 789
    await PostgresPlayerRepository(sessions).upsert(43, "Old, still no bonus", None)
    assert (await game.profile(43))["balance"] == 0
    new = await PostgresPlayerRepository(sessions).upsert(44, "New player", None)
    assert new.id != player.id and (await game.profile(44))["balance"] == 1000
    await engine.dispose()
    await migration(database_url, "54b3f0a493e1", "downgrade")
    await migration(database_url, "head")  # Metadata rollback/reapply also keeps cards and wallets.
    assert (await game.profile(42))["balance"] == 789
    assert len(await game.results(42, sid)) == 1
