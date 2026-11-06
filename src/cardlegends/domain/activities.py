"""Versioned reward definitions, UTC periods and bounded resource arithmetic."""

from dataclasses import dataclass
from datetime import UTC, timedelta

from cardlegends.domain.game import GameError

RESOURCE_LIMIT = 2**63 - 1


@dataclass(frozen=True)
class Objective:
    id: str
    name: str
    description: str
    metric: str
    target: int
    coins: int
    xp: int


DAILY = (
    Objective("login", "Новый день", "Войти в главное меню", "login", 1, 40, 20),
    Objective("summon", "Зов судьбы", "Призвать одну карточку", "summons", 1, 60, 30),
    Objective("win", "Первая победа", "Победить в башне", "wins", 1, 80, 40),
    Objective("battles", "Разминка", "Завершить три боя", "battles", 3, 100, 60),
    Objective("level", "Шаг вперёд", "Повысить уровень карточки", "levels", 1, 60, 40),
    Objective("bonus", "Подарок дня", "Получить ежедневный бонус", "daily", 1, 40, 20),
)
WEEKLY = (
    Objective("summons", "Пять встреч", "Призвать пять карточек", "summons", 5, 250, 150),
    Objective("wins", "Покоритель", "Победить в башне десять раз", "wins", 10, 350, 250),
    Objective("levels", "Наставник", "Повысить уровни суммарно на десять", "levels", 10, 250, 200),
    Objective("floors", "Новые высоты", "Пройти пять новых этажей", "floors", 5, 350, 250),
    Objective("days", "Постоянство", "Получить бонус в пять разных дней", "daily", 5, 250, 150),
)
ACHIEVEMENTS = (
    Objective("first", "Начало коллекции", "Получить первую карточку", "cards", 1, 50, 20),
    Objective("copies", "Коллекционер", "Получить десять экземпляров", "cards", 10, 100, 50),
    Objective("ur", "Редкая встреча", "Получить UR", "ur", 1, 300, 150),
    Objective("fate", "Fate Collection", "Получить все четыре кода баннера", "fate", 4, 400, 200),
    *tuple(
        Objective(f"level{n}", f"Развитие {n}", f"Достичь уровня {n}", "level", n, c, x)
        for n, c, x in ((10, 100, 100), (50, 400, 400), (100, 800, 800))
    ),
    *tuple(
        Objective(f"np{n}", f"Noble Phantasm {n}", f"Достичь NP {n}", "np", n, c, x)
        for n, c, x in ((2, 100, 100), (5, 400, 400))
    ),
    Objective("victory", "Первая победа", "Выиграть бой", "wins", 1, 50, 50),
    *tuple(
        Objective(f"floor{n}", f"Высота {n}", f"Пройти этаж {n}", "floor", n, c, c)
        for n, c in ((10, 200), (50, 600), (100, 1000))
    ),
    Objective("wins100", "Ветеран", "Победить сто раз", "wins", 100, 500, 400),
    *tuple(
        Objective(
            f"summons{n}", f"Призыватель {n}", f"Призвать {n} карточек", "summons", n, c, c // 2
        )
        for n, c in ((10, 100), (50, 300), (100, 600))
    ),
)
MILESTONES = {5: (100, 100), 10: (200, 200), 25: (350, 350), 50: (600, 600), 100: (1000, 1000)}


def period(scope, now):
    now = now.astimezone(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if scope == "weekly":
        start -= timedelta(days=start.weekday())
    elif scope != "daily":
        raise GameError("Неизвестный период.")
    return start, start + timedelta(days=7 if scope == "weekly" else 1)


def credit(wallet, coins=0, xp=0):
    if any(type(n) is not int or n < 0 for n in (coins, xp)):
        raise GameError("Некорректная награда.")
    for name, amount in (("balance", coins), ("xp", xp), ("training_xp", xp)):
        if not 0 <= getattr(wallet, name) + amount <= RESOURCE_LIMIT:
            raise GameError("Лимит ресурсов достигнут. Сначала используйте накопления.")
    wallet.balance += coins
    wallet.xp += xp
    wallet.training_xp += xp


def calendar_bonus(claims):
    day = claims % 7 + 1
    return day, (100 if day == 7 else 0), (200 if day == 7 else day * 20)


def progress_bar(value, target, width=10):
    filled = min(width, max(0, value) * width // max(1, target))
    return "▰" * filled + "▱" * (width - filled)
