import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text

from cardlegends.infrastructure.game_repository import GameService

pytestmark = pytest.mark.integration


async def migrate(url, revision, action="upgrade"):
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", action, revision],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "Migration failed (output omitted to protect credentials)"


async def test_populated_v0005_upgrade_preserves_rpg_and_backfills_only_reliable_facts(
    fate_game, database, database_url
):
    game = fate_game
    engine, _ = database
    op = await game.summon(42, 1, uuid4().hex)
    card, _ = (await game.results(42, op))[0]
    await game.assign_team(42, 1, card.id)
    await game.level_up(42, card.id, 1, uuid4().hex[:16])
    run, *_ = await game.fight_tower(42, 1, uuid4().hex[:16])
    await game.daily(42)
    async with engine.begin() as s:
        await s.execute(text("UPDATE owned_cards SET level=50,np=3 WHERE id=:id"), {"id": card.id})
    await engine.dispose()
    await migrate(database_url, "0005_tower_squad_training", "downgrade")
    async with engine.connect() as s:
        tables = list(
            await s.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename != 'alembic_version'"
                )
            )
        )
        before = {
            t: list(await s.scalars(text(f'SELECT to_jsonb(t) FROM "{t}" t'))) for t in tables
        }
    await engine.dispose()
    await migrate(database_url, "head")
    await migrate(database_url, "head")
    async with engine.connect() as s:
        for table, oldrows in before.items():
            rows = list(await s.scalars(text(f'SELECT to_jsonb(t) FROM "{table}" t')))
            assert all(
                any(all(new[k] == v for k, v in old.items()) for new in rows) for old in oldrows
            )
        assert await s.scalar(text("SELECT count(*) FROM reward_grants")) == 0
        assert await s.scalar(text("SELECT tutorial_skipped FROM player_journeys")) is True
        assert await s.scalar(
            text("SELECT first_clear FROM tower_runs WHERE id=:id"), {"id": run.id}
        )
    restarted = GameService(game.sessions, cards_dir=game.cards_dir)
    target, _, _ = await restarted.card(42, card.id)
    assert (target.level, target.np) == (50, 3)
    assert (await restarted.team(42))[0][1].id == card.id
    assert (await restarted.tower_info(42))[0] == 1
    await restarted.visit(42)
    assert (await restarted.tutorial(42))[0]
    _, grants, _ = await restarted.reward_board(42, "achievements")
    assert any(g.key.endswith(":level50") for g in grants)
    assert any(g.key.endswith(":victory") for g in grants)
