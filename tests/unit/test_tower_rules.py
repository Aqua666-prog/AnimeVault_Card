"""A tower grows indefinitely; deterministic combat never gambles with a wallet."""

from types import SimpleNamespace

import pytest

from cardlegends.domain.game import GameError
from cardlegends.domain.progression import combat_stats
from cardlegends.domain.tower import battle, enemy_stats, rewards


def test_enemy_growth_and_rewards():
    assert enemy_stats(1) == {"hp": 900, "atk": 80, "def": 40}
    assert enemy_stats(2)["hp"] > enemy_stats(1)["hp"]
    assert rewards(1, True) == (105, 50)
    assert rewards(1, False) == (52, 25)
    assert rewards(100_000, True) == (400, 300)
    with pytest.raises(GameError):
        rewards(0, True)


def test_battle_scales_with_level_np_and_team_size():
    character = SimpleNamespace(base_hp=1600, base_atk=120, base_def=120)
    lvl1 = combat_stats(character, SimpleNamespace(level=1, np=1))
    lvl100 = combat_stats(character, SimpleNamespace(level=100, np=5))
    assert battle(1, [lvl1]).victory
    assert battle(20, [lvl1]).victory is False
    assert battle(20, [lvl100, lvl100, lvl100]).victory
    assert battle(1, [lvl1]).turns <= 60
    for fighters in ([], [lvl1] * 4):
        with pytest.raises(GameError):
            battle(1, fighters)
