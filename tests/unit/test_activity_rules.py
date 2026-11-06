from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cardlegends.domain.activities import (
    ACHIEVEMENTS,
    DAILY,
    WEEKLY,
    calendar_bonus,
    credit,
    period,
)
from cardlegends.domain.game import GameError
from cardlegends.domain.progression import training_plan
from cardlegends.domain.tower import battle, enemy_name, enemy_stats, rewards


def test_utc_midnight_and_monday_periods():
    sunday = datetime(2026, 11, 8, 23, 59, 59, tzinfo=UTC)
    assert period("daily", sunday) == (
        datetime(2026, 11, 8, tzinfo=UTC),
        datetime(2026, 11, 9, tzinfo=UTC),
    )
    assert period("weekly", sunday) == (
        datetime(2026, 11, 2, tzinfo=UTC),
        datetime(2026, 11, 9, tzinfo=UTC),
    )
    monday = sunday + timedelta(seconds=1)
    assert period("weekly", monday)[0] == monday
    offset = monday.astimezone(timezone(timedelta(hours=-7)))
    assert period("daily", offset)[0] == monday


def test_reward_definitions_and_calendar_cycles():
    for definitions in (DAILY, WEEKLY, ACHIEVEMENTS):
        assert len({d.id for d in definitions}) == len(definitions)
        assert all(d.coins >= 0 and d.xp > 0 and d.target > 0 for d in definitions)
    assert len(DAILY) == 6 and len(WEEKLY) == 5 and len(ACHIEVEMENTS) == 17
    assert calendar_bonus(0) == (1, 0, 20)
    assert calendar_bonus(6) == (7, 100, 200)
    assert calendar_bonus(7) == (1, 0, 20)


def test_credit_checks_all_limits_before_mutation():
    wallet = SimpleNamespace(balance=100, xp=10, training_xp=5)
    credit(wallet, 40, 20)
    assert (wallet.balance, wallet.xp, wallet.training_xp) == (140, 30, 25)
    before = vars(wallet).copy()
    for coins, xp in ((-1, 0), (0, -1), (2**63, 1), (0, 2**63), (True, 0)):
        with pytest.raises(GameError):
            credit(wallet, coins, xp)
        assert vars(wallet) == before


def test_max_training_exact_cost_and_caps():
    assert training_plan(1, 0) == (0, 0)
    assert training_plan(1, 110) == (10, 110)
    assert training_plan(1, 9900) == (99, 9900)
    assert training_plan(100, 2**63 - 1) == (0, 0)
    assert training_plan(99, 197) == (0, 0)
    assert training_plan(99, 198) == (1, 198)


def test_archetypes_reports_and_unbounded_floor_arithmetic():
    assert len({enemy_name(f) for f in range(1, 6)}) == 5
    old = enemy_stats(1)
    for floor in range(2, 1001):
        new = enemy_stats(floor)
        assert all(new[k] >= old[k] for k in old)
        old = new
    huge = enemy_stats(2**63 - 1)
    assert max(huge.values()) <= 2**60
    fight = battle(1, [{"hp": 1600, "atk": 120, "def": 120}])
    assert fight.victory and fight.damage_dealt == 900
    assert fight.damage_received == 1600 - fight.remaining_hp
    assert len(fight.log) == fight.turns
    assert not battle(100, [{"hp": 1600, "atk": 120, "def": 120}]).victory
    assert rewards(1, True) == (105, 50) and rewards(1, False) == (52, 25)


def test_completed_squads_can_keep_climbing_all_archetypes():
    from cardlegends.domain.progression import combat_stats
    from cardlegends.infrastructure.catalog import catalog_data

    for entry in catalog_data():
        c = SimpleNamespace(**entry)
        stats = combat_stats(c, SimpleNamespace(level=100, np=5))
        for floor in range(1_000_000, 1_000_005):
            assert battle(floor, [stats] * 3).victory
    assert rewards(1_000_000, False) == (200, 150)
