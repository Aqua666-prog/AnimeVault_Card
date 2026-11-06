import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from cardlegends.domain.game import GameError
from cardlegends.infrastructure.game_models import (
    CardUpgrade,
    CurrencyTransaction,
    OwnedCard,
    PlayerJourney,
    PlayerVisit,
    RewardGrant,
    Wallet,
)
from cardlegends.infrastructure.game_repository import GameService
from cardlegends.infrastructure.players import PostgresPlayerRepository

pytestmark = pytest.mark.integration


def fixed_clock(game, monkeypatch, now):
    monkeypatch.setattr(game, "_now", AsyncMock(return_value=now))


async def test_daily_weekly_progress_replay_and_expired_claim(fate_game, monkeypatch):
    game = fate_game
    now = datetime.now(UTC)
    fixed_clock(game, monkeypatch, now)
    await game.visit(42)
    await game.visit(42)
    sid = await game.summon(42, 1, uuid4().hex)
    await game.daily(42)
    rows, grants, _ = await game.reward_board(42, "daily")
    values = {obj.id: value for obj, value, _ in rows}
    assert values["login"] == values["summon"] == values["bonus"] == 1
    login = next(g for g in grants if g.key.endswith(":login"))
    before = await game.profile(42)
    results = await asyncio.gather(*[game.claim_rewards(42, login.id) for _ in range(8)])
    assert sum(n for n, _, _ in results) == 1
    assert (await game.profile(42))["balance"] == before["balance"] + 40
    assert (await game.profile(42))["training_xp"] == before["training_xp"] + 20
    summon = next(g for g in grants if g.category == "daily" and g.key.endswith(":summon"))
    fixed_clock(game, monkeypatch, now + timedelta(days=8))
    with pytest.raises(GameError, match="Период завершён"):
        await game.claim_rewards(42, summon.id)
    fresh, _, _ = await game.reward_board(42, "weekly")
    assert all(value == 0 for _, value, _ in fresh)
    assert len(await game.results(42, sid)) == 1


async def test_mail_claim_all_atomic_owner_checks_and_overflow(fate_game):
    game = fate_game
    for i in range(3):
        await game.gift(42, f"gift{i}", "Special <gift>", 100, 50)
    await game.gift(42, "gift0", "Special <gift>", 100, 50)
    with pytest.raises(GameError):
        await game.gift(42, "gift0", "other", 1, 1)
    _, grants, _ = await game.reward_board(42, "mail")
    mail = [g for g in grants if g.category == "mail"]
    assert len(mail) == 3
    await PostgresPlayerRepository(game.sessions).upsert(43, "Other", None)
    with pytest.raises(GameError):
        await game.claim_rewards(43, mail[0].id)
    claimed = await asyncio.gather(*[game.claim_rewards(42, category="mail") for _ in range(6)])
    assert sum(n for n, _, _ in claimed) == 3
    assert (await game.profile(42))["balance"] == 1300
    assert (await game.profile(42))["training_xp"] == 150
    await game.gift(42, "overflow", "Overflow", 10, 1)
    async with game.sessions.begin() as s:
        player = (await game.profile(42))["player"]
        wallet = await s.get(Wallet, player.id)
        wallet.balance = 2**63 - 5
    with pytest.raises(GameError, match="Лимит"):
        await game.claim_rewards(42, category="mail")
    async with game.sessions() as s:
        g = await s.scalar(select(RewardGrant).where(RewardGrant.key == "mail:overflow"))
        assert g.claimed_at is None


async def test_calendar_seven_claims_skip_days_and_restart(fate_game, monkeypatch):
    game = fate_game
    base = datetime(2026, 11, 2, 12, tzinfo=UTC)
    for i in range(7):
        fixed_clock(game, monkeypatch, base + timedelta(days=i * 2))
        await game.daily(42)
        with pytest.raises(GameError):
            await game.daily(42)
    total, claimed, bonus = await game.calendar(42)
    assert total == 7 and claimed and bonus == (7, 100, 200)
    n, c, x = await game.claim_rewards(42, category="calendar")
    assert (n, c, x) == (7, 100, 620)
    restarted = GameService(game.sessions, cards_dir=game.cards_dir)
    fixed_clock(restarted, monkeypatch, base + timedelta(days=15))
    total, claimed, bonus = await restarted.calendar(42)
    assert total == 7 and not claimed and bonus == (1, 0, 20)
    assert (await restarted.profile(42))["balance"] == 3200


async def test_max_upgrade_preserves_uuid_np_squad_and_achievement_history(fate_game):
    game = fate_game
    sid = await game.summon(42, 10, uuid4().hex)
    cards = [c for c, _ in await game.results(42, sid)]
    target = cards[0]
    await game.assign_team(42, 1, target.id)
    await game.favorite(42, cards[1].id, True)
    async with game.sessions.begin() as s:
        wallet = await s.get(Wallet, target.player_id)
        wallet.training_xp = 9900
    key = uuid4().hex[:16]
    assert await game.level_up(42, target.id, 0, key)
    assert not await game.level_up(42, target.id, 0, key)
    card, _, _ = await game.card(42, target.id)
    assert card.level == 100 and card.np == 1
    assert (await game.team(42))[0][1].id == target.id
    await game.fuse_np(42, target.id, uuid4().hex[:16])
    assert (await game.card(42, cards[1].id))[0].consumed_at is None
    board, grants, _ = await game.reward_board(42, "achievements")
    assert any(g.key.endswith(":level100") for g in grants)
    assert any(g.key.endswith(":np2") for g in grants)
    await game.claim_rewards(42, category="achievements")
    old = (await game.profile(42))["balance"]
    assert (await game.claim_rewards(42, category="achievements"))[0] == 0
    assert (await game.profile(42))["balance"] == old
    async with game.sessions() as s:
        upgrade = await s.scalar(select(CardUpgrade).where(CardUpgrade.steps == 99))
        assert upgrade.xp_cost == 9900
    rows, total, _, _ = await game.inventory(42, filter_="team")
    assert total == 1 and rows[0][0].id == target.id
    assert (await game.inventory(42, filter_="level", value="50"))[1] == 1
    assert (await game.inventory(42, filter_="favorite"))[1] == 1
    assert (await game.inventory(42, filter_="duplicates"))[1] == 9
    assert (await game.inventory(42, filter_="np", value="2"))[1] == 1
    assert (await game.inventory(42, filter_="character", value="AV-F001"))[1] == 9
    assert (await game.inventory(42, filter_="franchise", value="fate"))[1] == 9
    assert (await game.inventory(42, sort="power"))[0][0][0].id == target.id


async def test_tower_metrics_and_milestone_receipt(fate_game):
    game = fate_game
    sid = await game.summon(42, 1, uuid4().hex)
    card, _ = (await game.results(42, sid))[0]
    async with game.sessions.begin() as s:
        c = await s.get(OwnedCard, card.id)
        c.level = 100
        c.np = 5
    await game.assign_team(42, 1, card.id)
    for floor in range(1, 6):
        run, _, _, _ = await game.fight_tower(42, floor, uuid4().hex[:16])
        assert run.victory and run.first_clear and run.report["team"]
    key = uuid4().hex[:16]
    await game.fight_tower(42, 1, key)
    await game.fight_tower(42, 1, key)
    board, grants, _ = await game.reward_board(42, "weekly")
    values = {obj.id: value for obj, value, _ in board}
    assert values["floors"] == 5 and values["wins"] == 6
    milestone = next(g for g in grants if g.key == "milestone:5")
    assert await game.claim_rewards(42, milestone.id) == (1, 100, 100)
    assert await game.claim_rewards(42, milestone.id) == (0, 0, 0)


async def test_tutorial_new_and_old_accounts_and_skip(fate_game):
    game = fate_game
    await game.visit(42)
    skipped, steps = await game.tutorial(42)
    assert not skipped and steps[0] and not steps[1]
    await game.tutorial(42, "skip")
    assert (await GameService(game.sessions).tutorial(42))[0]
    assert not (await game.tutorial(42, "open"))[0]
    async with game.sessions.begin() as s:
        await s.execute(text("DELETE FROM player_journeys"))
        player = (await game.profile(42))["player"]
        s.add(PlayerJourney(player_id=player.id, tutorial_skipped=True))
    await game.visit(42)
    assert (await game.tutorial(42))[0]
    async with game.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(PlayerVisit)) == 1
        assert (
            await s.scalar(
                select(func.count())
                .select_from(CurrencyTransaction)
                .where(CurrencyTransaction.kind == "initial")
            )
            == 1
        )


async def test_weekly_five_distinct_days_and_monday_expiry(fate_game, monkeypatch):
    game = fate_game
    monday = datetime(2026, 11, 2, 12, tzinfo=UTC)
    for day in range(5):
        fixed_clock(game, monkeypatch, monday + timedelta(days=day))
        await game.daily(42)
    await game.summon(42, 10, uuid4().hex)
    _, grants, _ = await game.reward_board(42, "weekly")
    days = next(g for g in grants if g.category == "weekly" and g.key.endswith(":days"))
    summons = next(g for g in grants if g.category == "weekly" and g.key.endswith(":summons"))
    assert await game.claim_rewards(42, days.id) == (1, 250, 150)
    fixed_clock(game, monkeypatch, monday + timedelta(days=7))
    with pytest.raises(GameError, match="Период завершён"):
        await game.claim_rewards(42, summons.id)
    board, _, _ = await game.reward_board(42, "weekly")
    assert all(value == 0 for _, value, _ in board)


async def test_failed_receipt_commit_rolls_back_every_credit(fate_game, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    game = fate_game
    await game.gift(42, "atomic1", "One", 10, 5)
    await game.gift(42, "atomic2", "Two", 20, 10)
    before = await game.profile(42)
    original = AsyncSession.flush

    async def fail(session, *args, **kwargs):
        if any(isinstance(x, CurrencyTransaction) and x.kind == "reward" for x in session.new):
            raise RuntimeError("injected ledger failure")
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", fail)
    with pytest.raises(RuntimeError):
        await game.claim_rewards(42, category="mail")
    monkeypatch.setattr(AsyncSession, "flush", original)
    after = await game.profile(42)
    assert (after["balance"], after["training_xp"], after["xp"]) == (
        before["balance"],
        before["training_xp"],
        before["xp"],
    )
    _, grants, _ = await game.reward_board(42, "mail")
    assert all(g.claimed_at is None for g in grants if g.category == "mail")
    assert await game.claim_rewards(42, category="mail") == (2, 30, 15)


async def test_daily_update_replay_across_utc_midnight_never_claims_new_day(fate_game, monkeypatch):
    from cardlegends.infrastructure.game_models import DailyReward

    game = fate_game
    before = datetime(2026, 11, 8, 23, 59, 59, tzinfo=UTC)
    fixed_clock(game, monkeypatch, before)
    first = await game.daily(42, telegram_update_id=999)
    fixed_clock(game, monkeypatch, before + timedelta(seconds=2))
    assert await game.daily(42, telegram_update_id=999) == first
    async with game.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(DailyReward)) == 1
    assert await game.daily(42, telegram_update_id=1000) == first + 300
    async with game.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(DailyReward)) == 2
