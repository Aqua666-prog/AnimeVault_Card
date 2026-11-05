from dataclasses import dataclass
from random import SystemRandom

RARITIES = ("N", "R", "SR", "SSR", "UR")
WEIGHTS = (50, 30, 15, 4, 1)
COLORS = ("#A1A1AA", "#70B7ED", "#AA8CF5", "#D4B06A", "#F5719C")


class GameError(Exception):
    pass


@dataclass(frozen=True)
class Economy:
    initial: int = 1000
    daily: int = 300
    single: int = 100
    ten: int = 900
    xp_per_card: int = 10
    xp_per_level: int = 100


def draw(pool, count, weights=WEIGHTS, rng=None):
    rng = rng or SystemRandom()
    if len(weights) != 5 or any(w <= 0 for w in weights) or sum(weights) != 100:
        raise GameError("Неверные вероятности баннера.")
    if any(not pool.get(rarity) for rarity in RARITIES):
        raise GameError("Баннер недоступен: не все редкости представлены.")
    if count not in (1, 10):
        raise GameError("Допускается призыв одной или десяти карточек.")
    result = [rng.choice(pool[rng.choices(RARITIES, weights=weights)[0]]) for _ in range(count)]
    if count == 10 and all(c.rarity in RARITIES[:2] for c in result):
        # Последний слот явно заменяется выбором из условного распределения SR+ 15:4:1.
        result[-1] = rng.choice(pool[rng.choices(RARITIES[2:], weights=weights[2:])[0]])
    return result
