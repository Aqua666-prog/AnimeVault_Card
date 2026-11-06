"""First Summon uses independent, integer-only card rolls; no rarity guarantees."""

import secrets

from cardlegends.domain.game import GameError

BANNER_ID = 2  # id=1 and old character IDs remain untouched for historical references.
BANNER_CODE = "fate-first-summon"
BANNER_NAME = "Fate Collection — First Summon"
DROP_RATES = {"AV-F001": 3300, "AV-F002": 3300, "AV-F003": 3300, "AV-L001": 100}
CARD_IDS = {"AV-F001": 1001, "AV-F002": 1002, "AV-F003": 1003, "AV-L001": 1004}
TOTAL_BPS = 10_000


def draw_fate(count, rates=None, randbelow=None):
    rates = DROP_RATES if rates is None else rates
    if rates != DROP_RATES or sum(rates.values()) != TOTAL_BPS or count not in (1, 10):
        raise GameError("Некорректная конфигурация Fate-баннера. Призыв недоступен.")
    randbelow = randbelow or secrets.randbelow
    result = []
    for _ in range(count):
        roll = randbelow(TOTAL_BPS)
        if type(roll) is not int or not 0 <= roll < TOTAL_BPS:
            raise GameError("Некорректный результат генератора.")
        boundary = 0
        for code in DROP_RATES:  # Stable interval order, independent of JSONB key order.
            boundary += rates[code]
            if roll < boundary:
                result.append(code)
                break
    return result
