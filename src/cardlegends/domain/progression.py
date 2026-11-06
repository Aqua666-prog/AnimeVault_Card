"""Deterministic per-card stats and level-up costs (no floating-point rounding)."""

from cardlegends.domain.game import GameError

MAX_LEVEL = 100
MAX_NP = 5
COINS_PER_LEVEL_POINT = 2


def upgrade_cost(current_level: int, steps: int) -> int:
    if (
        type(current_level) is not int
        or type(steps) is not int
        or not 1 <= current_level <= MAX_LEVEL
        or not 1 <= steps <= 10
        or current_level + steps > MAX_LEVEL
    ):
        raise GameError("Максимальный уровень карты — 100.")
    # Each transition n -> n+1 costs 2*n coins; 1 -> 100 costs 9,900 coins.
    return COINS_PER_LEVEL_POINT * (steps * current_level + steps * (steps - 1) // 2)


def combat_stats(character, card) -> dict[str, int]:
    """Level +2% base per step, NP +15% per step; multiplicative, integer only."""
    level, np = card.level, card.np
    if not (1 <= level <= MAX_LEVEL and 1 <= np <= MAX_NP):
        raise GameError("Некорректный уровень или NP карточки.")
    level_factor = 100 + 2 * (level - 1)
    np_factor = 100 + 15 * (np - 1)

    def scaled(base: int) -> int:
        return base * level_factor * np_factor // 10_000

    return {
        "hp": scaled(character.base_hp),
        "atk": scaled(character.base_atk),
        "def": scaled(character.base_def),
    }
