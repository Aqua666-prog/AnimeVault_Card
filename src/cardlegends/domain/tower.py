"""Deterministic, bounded auto-combat with gradually growing stats and five archetypes."""

from dataclasses import dataclass

from cardlegends.domain.game import GameError

ARCHETYPES = (
    "Босс · Страж врат",
    "Сбалансированный · Страж",
    "Защитник · Бастион",
    "Быстрый атакующий · Тень",
    "Живучий · Колосс",
)


@dataclass(frozen=True)
class BattleResult:
    victory: bool
    turns: int
    remaining_hp: int
    damage_dealt: int = 0
    damage_received: int = 0
    log: tuple[str, ...] = ()


def enemy_stats(floor: int) -> dict[str, int]:
    if type(floor) is not int or not 1 <= floor < 2**63:
        raise GameError("Неверный этаж башни.")
    step = floor - 1
    # Smooth bounded growth: a completed level-100/NP-5 squad can keep climbing.
    # No sudden boss stat multiplier and no mathematically final floor.
    return {
        "hp": 900 + 50_000 * step // (step + 150),
        "atk": 80 + 600 * step // (step + 150),
        "def": 40 + 500 * step // (step + 150),
    }


def enemy_name(floor):
    enemy_stats(floor)
    return ARCHETYPES[floor % 5]


def battle(floor, fighters):
    enemy = enemy_stats(floor)
    if not 1 <= len(fighters) <= 3 or any(min(s["hp"], s["atk"], s["def"]) <= 0 for s in fighters):
        raise GameError("Для сражения нужен отряд из 1–3 карточек.")
    health = sum(s["hp"] for s in fighters)
    attack = sum(s["atk"] for s in fighters)
    defense = sum(s["def"] for s in fighters) // len(fighters)
    enemy_hp = enemy["hp"]
    dealt = received = 0
    log = []
    for turn in range(1, 61):
        hit = max(1, attack - enemy["def"] // 2)
        if floor % 5 == 2 and turn % 3 == 0:
            hit = max(1, hit * 9 // 10)
        actual = min(enemy_hp, hit)
        enemy_hp -= actual
        dealt += actual
        if enemy_hp <= 0:
            log.append(f"Раунд {turn}: отряд −{actual} HP врагу; победа.")
            return BattleResult(True, turn, health, dealt, received, tuple(log))
        hit = max(1, enemy["atk"] - defense // 2)
        if (floor % 5 == 3 and turn % 3 == 0) or (floor % 5 == 0 and turn % 5 == 0):
            hit = hit * 6 // 5
        incoming = min(health, hit)
        health -= incoming
        received += incoming
        heal = 0
        if floor % 5 == 4 and turn % 3 == 0:
            heal = min(enemy["hp"] - enemy_hp, enemy["hp"] // 100)
            enemy_hp += heal
        log.append(
            f"Раунд {turn}: нанесено {actual}, получено {incoming}"
            + (f", враг восстановил {heal} HP" if heal else "")
            + "."
        )
        if health <= 0:
            return BattleResult(False, turn, 0, dealt, received, tuple(log))
    return BattleResult(False, 60, health, dealt, received, tuple(log))


def rewards(floor, first_clear):
    enemy_stats(floor)
    coins = min(400, 80 + 25 * floor)
    xp = min(300, 35 + 15 * floor)
    return (coins, xp) if first_clear else (max(1, coins // 2), max(1, xp // 2))
