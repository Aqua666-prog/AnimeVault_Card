import math
import secrets
from collections import Counter
from random import Random

import pytest

from cardlegends.domain.fate import DROP_RATES, draw_fate
from cardlegends.domain.game import GameError
from cardlegends.infrastructure.catalog import catalog_data


def test_exact_catalog_and_drop_rate_intervals():
    assert sum(DROP_RATES.values()) == 10000
    assert [
        (c["code"], c["name_en"], c["card_class"], c["rarity"], c["limited"])
        for c in catalog_data()
    ] == [
        ("AV-F001", "Artoria Pendragon", "Saber", "SSR", False),
        ("AV-F002", "Morgan le Fae", "Berserker", "SSR", False),
        ("AV-F003", "Enkidu", "Lancer", "SSR", False),
        ("AV-L001", "Kama & Ishtar", "Assassin × Archer", "UR", True),
    ]
    counts = Counter(draw_fate(1, randbelow=lambda _, r=r: r)[0] for r in range(10000))
    assert counts == DROP_RATES  # Exhaustive boundaries and reachability, no other items.


@pytest.mark.parametrize(
    "roll,code",
    [
        (0, "AV-F001"),
        (3299, "AV-F001"),
        (3300, "AV-F002"),
        (6599, "AV-F002"),
        (6600, "AV-F003"),
        (9899, "AV-F003"),
        (9900, "AV-L001"),
        (9999, "AV-L001"),
    ],
)
def test_boundaries(roll, code):
    assert draw_fate(1, randbelow=lambda n: roll) == [code]


def test_crypto_default_and_ten_independent_rolls_without_guarantee(monkeypatch):
    calls = []

    def random(n):
        calls.append(n)
        return 0

    monkeypatch.setattr(secrets, "randbelow", random)
    assert draw_fate(10) == ["AV-F001"] * 10
    assert calls == [10000] * 10
    with pytest.raises(GameError):
        draw_fate(1, {**DROP_RATES, "AV-L001": 0})
    with pytest.raises(GameError):
        draw_fate(1, randbelow=lambda n: 10000)


def test_large_simulation_with_binomial_six_sigma_tolerance():
    rng = Random(721)
    n = 200_000
    counts = Counter()
    for _ in range(n // 10):
        counts.update(draw_fate(10, randbelow=rng.randrange))
    assert set(counts) == set(DROP_RATES)
    for code, bps in DROP_RATES.items():
        p = bps / 10000  # Statistics only; production rolls use integers throughout.
        assert abs(counts[code] - n * p) < 6 * math.sqrt(n * p * (1 - p))
