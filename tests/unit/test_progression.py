from types import SimpleNamespace

import pytest

from cardlegends.domain.game import GameError
from cardlegends.domain.progression import MAX_LEVEL, MAX_NP, combat_stats, upgrade_cost


def test_level_costs_and_boundaries():
    assert MAX_LEVEL == 100
    assert MAX_NP == 5
    assert upgrade_cost(1, 1) == 2
    assert upgrade_cost(1, 10) == 110
    assert sum(upgrade_cost(level, 1) for level in range(1, 100)) == 9900
    assert upgrade_cost(99, 1) == 198
    for level, steps in ((100, 1), (91, 10), (0, 1), (1, 0), (1, 11)):
        with pytest.raises(GameError):
            upgrade_cost(level, steps)
    with pytest.raises(GameError):
        upgrade_cost(True, 1)


def test_stats_scale_with_level_and_np_and_are_integer():
    character = SimpleNamespace(base_hp=1600, base_atk=120, base_def=120)
    card = SimpleNamespace(level=1, np=1)
    assert combat_stats(character, card) == {"hp": 1600, "atk": 120, "def": 120}
    card.level = 100
    assert combat_stats(character, card) == {"hp": 4768, "atk": 357, "def": 357}
    card.np = 5
    assert combat_stats(character, card) == {"hp": 7628, "atk": 572, "def": 572}
    card.level = 1
    assert combat_stats(character, card)["atk"] == 192
    for level, np in ((0, 1), (101, 1), (1, 0), (1, 6)):
        card.level, card.np = level, np
        with pytest.raises(GameError):
            combat_stats(character, card)
