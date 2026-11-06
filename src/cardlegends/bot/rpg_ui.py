"""RPG screens: formatting/navigation only; services own every persisted action."""

from html import escape as h

from cardlegends.bot.game_ui import back, coins, display, keyboard, menu, send_png
from cardlegends.bot.visuals import battle_image, show_art
from cardlegends.domain.activities import ACHIEVEMENTS, progress_bar
from cardlegends.domain.progression import combat_stats, power
from cardlegends.domain.tower import battle, enemy_name, enemy_stats


def stat_bar(value, maximum, width=12):
    """Compact Telegram-safe meter for HP/EXP and other bounded stats."""
    maximum = max(1, int(maximum))
    value = max(0, min(int(value), maximum))
    filled = round(width * value / maximum)
    return f"{'🟩' * filled}{'⬛' * (width - filled)}"


class RPGScreens:
    def __init__(self, game):
        self.game = game

    async def art(self, event, text, markup, scene, title, subtitle, data=None):
        if not await show_art(
            event, text, markup, scene=scene, title=title, subtitle=subtitle, data=data
        ):
            await display(event, text, markup)

    async def home(self, event):
        user = event.from_user.id
        await self.game.visit(user)
        p = await self.game.profile_v2(user)
        _, grants, now = await self.game.reward_board(user, "all")
        ready = sum(
            g.claimed_at is None and (g.expires_at is None or g.expires_at > now) for g in grants
        )
        skipped, steps = await self.game.tutorial(user)
        text = (
            f"<b>✨ ANIMEVAULT CARDS</b>  <i>Fate Collection</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {h(p['player'].display_name)}  ·  Ранг <b>{p['level']}</b>\n"
            f"🪙 <b>{coins(p['balance'])}</b> Coins     💠 <b>{coins(p['training_xp'])}</b> EXP\n"
            f"📚 Коллекция <b>{p['unique']}/4</b>     🏰 Этаж <b>{p['highest']}</b>\n"
            f"{progress_bar(p['unique'], 4)}\n\n"
            f"🎁 Доступно наград: <b>{ready}</b>\nВыбери следующий шаг приключения."
        )
        if not skipped and not all(steps):
            text += f"\nПутеводитель: {sum(steps)}/8 — открыт в помощи."
        character = await self.game.fate_character("AV-F001")
        await send_png(event, self.game, character, text, menu())

    async def profile(self, event):
        p = await self.game.profile_v2(event.from_user.id)
        card = p["strongest"]
        strongest = (
            (
                f"{h(card[1].name_en)} · ур. {card[0].level} · NP {card[0].np} · "
                f"{power(combat_stats(card[1], card[0]))} силы"
            )
            if card
            else "Карточек пока нет"
        )
        await self.art(
            event,
            f"<b>👑 ПРОФИЛЬ ИГРОКА</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🧑 {h(p['player'].display_name)}  ·  <b>Ранг {p['level']}</b>\n"
            f"🆔 <code>{event.from_user.id}</code>\n\n"
            f"<b>Прогресс аккаунта</b>\n{stat_bar(p['xp'] % 100, 100)}  {p['xp'] % 100}/100 EXP\n"
            f"До нового ранга: <b>{100 - p['xp'] % 100}</b> EXP\n\n"
            f"🪙 Coins: <b>{coins(p['balance'])}</b>\n💠 EXP карт: <b>{coins(p['training_xp'])}</b>\n"
            f"📚 Коллекция: <b>{p['unique']}/4</b> героев · {p['total']} экземпляров\n"
            f"🗃 Архив: {p['archived']} · Всего копий: {p['total_all']}\n\n"
            f"⚔ <b>Сильнейшая карта</b>\n{strongest}\n\n"
            f"🏰 Башня: <b>{p['highest']}</b> этаж\n"
            f"🏆 Победы: {p['wins']}  ·  Поражения: {p['losses']}\n"
            f"🎖 Достижения: {p['achievements']}/{len(ACHIEVEMENTS)}\n"
            f"📅 В игре с {p['player'].created_at:%d.%m.%Y} UTC",
            keyboard([[("Коллекция", "r2:inv:0:power:all:all"), ("Мой отряд", "fc:team")], back()]),
            "profile",
            "ЗАЛ СЛАВЫ",
            "Твой путь начинается здесь",
        )

    async def inventory(self, event, page=0, sort="power", filter_="all", value="all"):
        rows, total, page, team = await self.game.inventory(
            event.from_user.id, page, sort, filter_, value
        )

        def link(p=page, s=sort, f=filter_, v=value):
            return f"r2:inv:{p}:{s}:{f}:{v}"

        buttons = [
            [
                (
                    f"{'★ ' if card.favorite else ''}{'⚔ ' if card.id in team else ''}"
                    f"{c.name_en} · {c.rarity} · {card.level} · NP{card.np}",
                    f"fc:card:{card.id.hex}",
                )
            ]
            for card, c in rows
        ]
        sorts = ("power", "level", "rarity", "np", "time")
        sort_names = {
            "power": "сила",
            "level": "уровень",
            "rarity": "редкость",
            "np": "NP",
            "time": "новые",
        }
        buttons += [
            [
                ("Фильтры", f"r2:filters:{sort}"),
                (
                    f"Порядок: {sort_names[sort]}",
                    link(0, sorts[(sorts.index(sort) + 1) % len(sorts)]),
                ),
            ],
            [("Сброс", link(0, f="all", v="all"))],
            [("←", link(max(0, page - 1))), ("→", link(min(max(0, (total - 1) // 6), page + 1)))],
            [("По персонажам", "fc:collection"), ("Призыв", "fc:summon")],
            back(),
        ]
        await display(
            event,
            f"<b>МОЯ КОЛЛЕКЦИЯ</b>\n━━━━━━━━━━━━━━\n"
            f"Экземпляров: {total} · Страница {page + 1}/{max(1, (total + 5) // 6)}\n"
            f"Фильтр: {
                h(
                    {
                        'all': 'все',
                        'rarity': 'редкость',
                        'character': 'герой',
                        'franchise': 'франшиза',
                        'level': 'уровень',
                        'np': 'NP',
                        'favorite': 'избранные',
                        'team': 'отряд',
                        'duplicates': 'дубликаты',
                    }[filter_]
                )
            } {h(value) if value != 'all' else ''}\n"
            + (
                "Получите первую карточку через призыв или сбросьте фильтры."
                if not rows
                else "У каждой копии свой уровень, NP и место в отряде."
            ),
            keyboard(buttons),
        )

    async def filters(self, event, sort):
        def b(label, f, v="all"):
            return (label, f"r2:inv:0:{sort}:{f}:{v}")

        if sort not in ("power", "level", "rarity", "np", "time"):
            raise ValueError
        from cardlegends.domain.fate import CARD_IDS

        franchises = await self.game.franchise_options()
        rows = [
            [b("Все", "all"), b("Избранные", "favorite")],
            [b("В отряде", "team"), b("Дубликаты", "duplicates")],
            [b(r, "rarity", r) for r in ("N", "R", "SR", "SSR", "UR")],
            [b("Уровень ≥10", "level", "10"), b("Уровень ≥50", "level", "50")],
            [b("NP ≥2", "np", "2"), b("NP 5", "np", "5")],
        ]
        rows += [[b(code, "character", code)] for code in CARD_IDS]
        rows += [
            [b(f.name, "franchise", f.id)]
            for f in franchises
            if len(f"r2:inv:0:{sort}:franchise:{f.id}".encode()) <= 64
        ]
        await display(
            event,
            "<b>ФИЛЬТРЫ КОЛЛЕКЦИИ</b>\nВыберите редкость, героя, франшизу или развитие.",
            keyboard(rows + [[("Назад", f"r2:inv:0:{sort}:all:all")], back()]),
        )

    async def objectives(self, event, scope, page=0):
        if scope not in ("daily", "weekly", "achievements"):
            raise ValueError
        await self.game.tutorial(event.from_user.id, "tasks")
        board, grants, now = await self.game.reward_board(event.from_user.id, scope)
        page = max(0, min(page, (len(board) - 1) // 5))
        bykey = {g.key: g for g in grants}
        titles = {
            "daily": "ЕЖЕДНЕВНЫЕ ЗАДАНИЯ",
            "weekly": "ЕЖЕНЕДЕЛЬНЫЕ ЗАДАНИЯ",
            "achievements": "ДОСТИЖЕНИЯ",
        }
        text = f"<b>{titles[scope]}</b>\n"
        text += "━━━━━━━━━━━━━━\n"
        rows = []
        for obj, value, key in board[page * 5 : page * 5 + 5]:
            g = bykey.get(key)
            state = "Получено ✓" if g and g.claimed_at else "Готово ◆" if g else "В процессе"
            text += (
                f"\n<b>{h(obj.name)}</b> · {state}\n{h(obj.description)}\n"
                f"{progress_bar(value, obj.target)} {min(value, obj.target)}/{obj.target}\n"
                f"+{obj.coins} Coins · +{obj.xp} EXP\n"
            )
            if g and not g.claimed_at and (g.expires_at is None or g.expires_at > now):
                rows.append([(f"Получить: {obj.name}", f"r2:claim:{g.id.hex}")])
        if scope != "achievements":
            text += "\nСброс: 00:00 UTC" + (" в понедельник" if scope == "weekly" else " ежедневно")
        rows += [
            [
                ("←", f"r2:q:{scope}:{max(0, page - 1)}"),
                ("→", f"r2:q:{scope}:{min((len(board) - 1) // 5, page + 1)}"),
            ],
            [("Ежедневные", "r2:q:daily:0"), ("Еженедельные", "r2:q:weekly:0")],
            [("Все награды", "r2:rewards:all:0")],
            back(),
        ]
        await display(event, text, keyboard(rows))

    async def rewards(self, event, category="all", page=0):
        _, grants, now = await self.game.reward_board(event.from_user.id, category)
        grants = [g for g in grants if category == "all" or g.category == category]
        page = max(0, min(page, max(0, (len(grants) - 1) // 6)))
        text = f"<b>{'ИГРОВАЯ ПОЧТА' if category == 'mail' else 'НАГРАДЫ'}</b>\n━━━━━━━━━━━━━━\n"
        rows = []
        for g in grants[page * 6 : page * 6 + 6]:
            status = (
                "Получено ✓"
                if g.claimed_at
                else "Период завершён"
                if g.expires_at and g.expires_at <= now
                else "Доступно ◆"
            )
            text += f"\n{h(g.reason[:80])} · {status}\n{g.coins} Coins · {g.xp} EXP\n"
            if g.category == "mail":
                rows.append([("Открыть письмо", f"r2:letter:{g.id.hex}")])
            elif status == "Доступно ◆":
                rows.append([("Получить: " + g.reason[:32], f"r2:claim:{g.id.hex}")])
        if not grants:
            text += "Пока нет подарков. Награды открываются за задания, достижения и этажи башни."
        rows += [
            [("Получить всё", f"r2:claimall:{category}")],
            [
                ("←", f"r2:rewards:{category}:{max(0, page - 1)}"),
                ("→", f"r2:rewards:{category}:{min(max(0, (len(grants) - 1) // 6), page + 1)}"),
            ],
            [("Задания", "r2:q:daily:0"), ("Достижения", "r2:q:achievements:0")],
            back(),
        ]
        await display(event, text, keyboard(rows))

    async def letter(self, event, reward_id):
        grant, now = await self.game.reward_detail(event.from_user.id, reward_id)
        text = (
            f"<b>ИГРОВАЯ ПОЧТА</b>\n━━━━━━━━━━━━━━\n{h(grant.reason)}\n"
            f"Получатель: {event.from_user.id}\nID: <code>{grant.id}</code>\n"
            f"{coins(grant.coins)} Coins · {coins(grant.xp)} EXP\n"
            f"Создано: {grant.created_at:%d.%m.%Y %H:%M} UTC\n"
        )
        rows = []
        if grant.claimed_at:
            text += f"Получено: {grant.claimed_at:%d.%m.%Y %H:%M} UTC"
        elif grant.expires_at and grant.expires_at <= now:
            text += "Срок получения завершён."
        else:
            text += "Награда доступна."
            rows.append([("Получить", f"r2:claim:{grant.id.hex}")])
        rows += [[("Назад к письмам", "r2:rewards:mail:0")], back()]
        await display(event, text, keyboard(rows))

    async def calendar(self, event):
        total, today, (position, extra, xp) = await self.game.calendar(event.from_user.id)
        text = (
            "<b>КАЛЕНДАРЬ ПОСЕЩЕНИЙ</b>\n━━━━━━━━━━━━━━\n"
            f"Успешных дней: {total}\n{progress_bar((total - 1) % 7 + 1 if total else 0, 7, 7)}\n"
            f"{'Сегодня получено' if today else 'Следующий день'}: {position}/7\n"
            f"База: 300 Coins · Дополнительно: {extra} Coins и {xp} EXP\n\n"
            "Пропуск не сбрасывает календарь. После семи получений начинается новый цикл.\n"
            "Бонусы календаря забираются отдельно в наградах. Новый день — 00:00 UTC."
        )
        await display(
            event,
            text,
            keyboard(
                [
                    [("Получить ежедневный бонус", "fc:daily")],
                    [("Бонусы календаря", "r2:rewards:calendar:0")],
                    back(),
                ]
            ),
        )

    async def tutorial(self, event, action=None):
        skipped, steps = await self.game.tutorial(event.from_user.id, action)
        labels = (
            "Получить стартовые монеты",
            "Совершить призыв",
            "Открыть экземпляр карточки",
            "Добавить карточку в отряд",
            "Победить на первом этаже",
            "Получить EXP",
            "Повысить уровень карточки",
            "Открыть задания",
        )
        links = (
            "fc:home",
            "fc:summon",
            "r2:inv:0:power:all:all",
            "fc:team",
            "fc:tower",
            "r2:rewards:all:0",
            "r2:inv:0:power:all:all",
            "r2:q:daily:0",
        )
        text = "<b>ПУТЕВОДИТЕЛЬ</b>\n" + progress_bar(sum(steps), 8) + f" {sum(steps)}/8\n\n"
        text += "\n".join(
            f"{'✓' if ok else '○'} {i}. {label}"
            for i, (label, ok) in enumerate(zip(labels, steps, strict=True), 1)
        )
        text += "\n\nОбучение можно пропустить; весь прогресс останется."
        next_step = next((i for i, x in enumerate(steps) if not x), 7)
        rows = (
            [[("Продолжить обучение", "r2:tutorial:open")]]
            if skipped
            else [[("Следующий шаг", links[next_step])]]
        )
        rows += [[("Пропустить", "r2:tutorial:skip")], back()]
        await display(event, text, keyboard(rows))

    async def activities(self, event):
        await self.art(
            event,
            "<b>🎮 ЦЕНТР АКТИВНОСТЕЙ</b>\n━━━━━━━━━━━━━━━━━━\n"
            "🏰 <b>Бесконечная башня</b> — сражайся, поднимайся выше и добывай Coins/EXP.\n\n"
            "📜 <b>Задания</b> — ежедневные и еженедельные цели с гарантированными наградами.\n\n"
            "🏆 <b>Достижения</b> — долгосрочные испытания и памятные трофеи.\n\n"
            "📅 <b>Календарь</b> — последовательные бонусы за посещения.\n\n"
            "⚡ Энергии и ожидания между боями нет — можно играть в своём темпе.",
            keyboard(
                [
                    [("Бесконечная башня", "fc:tower"), ("Мой отряд", "fc:team")],
                    [("Задания дня", "r2:q:daily:0"), ("Задания недели", "r2:q:weekly:0")],
                    [("Достижения", "r2:q:achievements:0"), ("Календарь", "r2:calendar")],
                    back(),
                ]
            ),
            "activities",
            "КАРТА ПРИКЛЮЧЕНИЙ",
            "Выбери следующую цель",
        )

    async def tower(self, event, floor=None):
        highest, members = await self.game.tower_info(event.from_user.id)
        floor = highest + 1 if floor is None else max(1, min(floor, highest + 1))
        enemy = enemy_stats(floor)
        fighters = [combat_stats(c, card) for _, card, c in members]
        expected = battle(floor, fighters) if fighters else None
        text = (
            f"<b>🏰 БЕСКОНЕЧНАЯ БАШНЯ</b>  ·  Этаж <b>{floor}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👹 <b>{enemy_name(floor)}</b>\n"
            f"❤️ HP врага: <b>{coins(enemy['hp'])}</b>\n{stat_bar(enemy['hp'], enemy['hp'])}\n"
            f"⚔ ATK {coins(enemy['atk'])}   🛡 DEF {coins(enemy['def'])}\n"
            f"🏆 Рекорд: <b>{highest}</b>   👥 Отряд: <b>{len(members)}/3</b>\n"
            + "\n".join(
                f"{slot}. {h(c.name_en)} · ур. {card.level} · NP {card.np}"
                for slot, card, c in members
            )
        )
        if expected and not expected.victory:
            text += (
                "\n\n⚠ Силы команды пока недостаточно по прогнозу. "
                "Прокачайте карты или фармите открытый этаж."
            )
        if not members:
            text += "\nСначала соберите отряд из 1–3 карточек."
        from uuid import uuid4

        rows = []
        if members:
            rows.append(
                [
                    (
                        "Штурмовать" if floor > highest else "Повторить (½ награды)",
                        f"fc:tf:{floor}:{uuid4().hex[:16]}",
                    )
                ]
            )
        rows += [
            [
                ("←", f"r2:tower:{max(1, floor - 1)}"),
                ("→", f"r2:tower:{min(highest + 1, floor + 1)}"),
            ],
            [
                ("−10", f"r2:tower:{max(1, floor - 10)}"),
                ("+10", f"r2:tower:{min(highest + 1, floor + 10)}"),
            ],
            [("Мой отряд", "fc:team"), ("Награды этажей", "r2:rewards:all:0")],
            back(),
        ]
        team_hp = sum(fighter["hp"] for fighter in fighters)
        poster = battle_image(floor, enemy_name(floor), enemy["hp"], enemy["hp"], team_hp, team_hp)
        await self.art(
            event,
            text,
            keyboard(rows),
            "tower",
            "БЕСКОНЕЧНАЯ БАШНЯ",
            "Покори следующий этаж",
            poster,
        )

    async def handle(self, event, parts):
        action = parts[1] if len(parts) > 1 else ""
        if action == "slot" and len(parts) == 3:
            await self.game.card(event.from_user.id, parts[2])
            await display(
                event,
                "<b>В КАКОЙ СЛОТ?</b>\nВыберите слот для этого экземпляра.",
                keyboard([[(str(slot), f"fc:ta:{slot}:{parts[2]}") for slot in (1, 2, 3)], back()]),
            )
        elif action == "inv" and len(parts) == 6:
            await self.inventory(event, int(parts[2]), *parts[3:])
        elif action == "filters" and len(parts) == 3:
            await self.filters(event, parts[2])
        elif action == "q" and len(parts) == 4:
            await self.objectives(event, parts[2], int(parts[3]))
        elif action == "rewards" and len(parts) == 4:
            await self.rewards(event, parts[2], int(parts[3]))
        elif action == "claim" and len(parts) == 3:
            n, c, x = await self.game.claim_rewards(event.from_user.id, parts[2])
            await display(
                event,
                f"<b>Награда сохранена</b>\nПолучено: {n} · +{coins(c)} Coins · +{coins(x)} EXP",
                keyboard([[("Награды", "r2:rewards:all:0")], back()]),
            )
        elif action == "claimall" and len(parts) == 3:
            n, c, x = await self.game.claim_rewards(
                event.from_user.id, category=None if parts[2] == "all" else parts[2]
            )
            await display(
                event,
                f"Получено наград: {n}\n+{coins(c)} Coins · +{coins(x)} EXP",
                keyboard([[("Награды", "r2:rewards:all:0")], back()]),
            )
        elif action == "letter" and len(parts) == 3:
            await self.letter(event, parts[2])
        elif action == "calendar" and len(parts) == 2:
            await self.calendar(event)
        elif action == "tutorial" and len(parts) == 3 and parts[2] in ("open", "skip", "show"):
            await self.tutorial(event, None if parts[2] == "show" else parts[2])
        elif action == "activities" and len(parts) == 2:
            await self.activities(event)
        elif action == "tower" and len(parts) == 3:
            await self.tower(event, int(parts[2]))
        else:
            raise ValueError
