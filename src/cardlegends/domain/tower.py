"""Deterministic, replayable tower encounters. No finite final floor or RNG."""

from dataclasses import dataclass

from cardlegends.domain.game import GameError


@dataclass(frozen=True)
class BattleResult:
    victory: bool
    turns: int
    remaining_hp: int


def enemy_stats(floor: int) -> dict[str, int]:
    if type(floor) is not int or floor < 1:
        raise GameError("Неверный этаж башни.")
    step = floor - 1
    return {
        "hp": 900 + 250 * step + 20 * step * step,
        "atk": 80 + 18 * step + step * step,
        "def": 40 + 10 * step + step * step // 4,
    }


def battle(floor: int, fighters: list[dict[str, int]]) -> BattleResult:
    """Team strikes first. HP resets between battles; max 60 rounds."""
    enemy = enemy_stats(floor)
    if not 1 <= len(fighters) <= 3 or any(
        min(stats["hp"], stats["atk"], stats["def"]) <= 0 for stats in fighters
    ):
        raise GameError("Для сражения нужен отряд из 1–3 карточек.")
    health = sum(stats["hp"] for stats in fighters)
    attack = sum(stats["atk"] for stats in fighters)
    defense = sum(stats["def"] for stats in fighters) // len(fighters)
    enemy_hp = enemy["hp"]
    for turn in range(1, 61):
        enemy_hp -= max(1, attack - enemy["def"] // 2)
        if enemy_hp <= 0:
            return BattleResult(True, turn, health)
        health -= max(1, enemy["atk"] - defense // 2)
        if health <= 0:
            return BattleResult(False, turn, 0)
    return BattleResult(False, 60, max(0, health))


def rewards(floor: int, first_clear: bool) -> tuple[int, int]:
    """Previous floors remain farmable, with half rewards on repeat clears."""
    enemy_stats(floor)  # Validate the floor.
    coins = min(100_000, 80 + 25 * floor)
    xp = min(50_000, 35 + 15 * floor)
    return (coins, xp) if first_clear else (max(1, coins // 2), max(1, xp // 2))
