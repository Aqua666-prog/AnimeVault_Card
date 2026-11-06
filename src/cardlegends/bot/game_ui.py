import asyncio
import logging
from collections import Counter
from contextlib import nullcontext
from html import escape as h
from uuid import UUID, uuid4

from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    Message,
)

from cardlegends.bot.visuals import battle_image
from cardlegends.domain.fate import BANNER_ID, BANNER_NAME, CARD_IDS, DROP_RATES
from cardlegends.domain.game import GameError
from cardlegends.domain.progression import MAX_LEVEL, MAX_NP, combat_stats, power, upgrade_cost
from cardlegends.infrastructure.diagnostics import log_failure
from cardlegends.infrastructure.fate_images import CardImageError, check_card_images, load_card_png

logger = logging.getLogger(__name__)


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def menu():
    return keyboard(
        [
            [("👑 Профиль", "fc:profile"), ("🃏 Коллекция", "r2:inv:0:power:all:all")],
            [("✨ Призыв", "fc:summon"), ("⚔️ Отряд", "fc:team")],
            [("🏰 Башня", "fc:tower"), ("🗺️ Активности", "r2:activities")],
            [("📜 Задания", "r2:q:daily:0"), ("🏆 Достижения", "r2:q:achievements:0")],
            [("🎁 Награды", "r2:rewards:all:0"), ("✉️ Почта", "r2:rewards:mail:0")],
            [("❔ Помощь", "fc:help"), ("📅 Календарь", "r2:calendar")],
        ]
    )


def back():
    return [("🏠 Главное меню", "fc:home")]


def coins(value):
    return format(value, ",").replace(",", " ")


def meter(value, maximum, width=10):
    maximum = max(1, int(maximum))
    value = max(0, min(int(value), maximum))
    filled = round(width * value / maximum)
    return f"{'🟩' * filled}{'⬛' * (width - filled)}"


def rarity(character):
    return "UR Limited" if character.limited else character.rarity


async def display(event, text, markup=None):
    markup = markup or keyboard([back()])
    if isinstance(event, CallbackQuery):
        if not isinstance(event.message, Message):
            return
        # Photo captions have a 1024-char limit; catalog/result text has its own message.
        if event.message.photo or event.message.document:
            if len(text) <= 1024:
                try:
                    await event.message.edit_caption(caption=text, reply_markup=markup)
                except TelegramBadRequest as error:
                    if "message is not modified" not in error.message.lower():
                        raise
            else:
                await event.message.answer(text, reply_markup=markup)
            return
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise
    else:
        await event.answer(text, reply_markup=markup)


def card_text(c):
    limited = "UR LIMITED — Assassin × Archer" if c.limited else f"{c.rarity} — {c.card_class}"
    return (
        f"<b>{h(c.name_en)}</b>\n{h(c.code)} • {h(c.collection_name)}\n"
        f"Класс: {h(c.card_class)}\nРедкость: {rarity(c)}\n{limited}\n"
        f"Лимитированная: {'да' if c.limited else 'нет'}\n"
        f"Шанс: {DROP_RATES[c.code] // 100}%\n"
        f"Базовые статы (Ур. 1, NP 1): HP {c.base_hp} • ATK {c.base_atk} • DEF {c.base_def}"
    )


async def send_png(event, game, character, caption=None, markup=None):
    # Bound local PNG memory and concurrent media requests, including cache fallbacks.
    async with getattr(game, "media_lock", nullcontext()):
        return await _send_png(event, game, character, caption, markup)


async def _send_png(event, game, character, caption=None, markup=None):
    """Send original bytes; cache is an optimization, never the sole image source."""
    png = await asyncio.to_thread(load_card_png, character.code, game.cards_dir)
    caption = caption or card_text(character)
    markup = markup or keyboard([back()])

    async def send(chat_id, **kwargs):
        if (
            isinstance(event, CallbackQuery)
            and isinstance(event.message, Message)
            and (
                (event.message.photo and not png.as_document)
                or (event.message.document and png.as_document)
            )
        ):
            media_cls = InputMediaDocument if png.as_document else InputMediaPhoto
            try:
                return await event.message.edit_media(
                    media=media_cls(
                        media=kwargs["document" if png.as_document else "photo"],
                        caption=kwargs["caption"],
                    ),
                    reply_markup=kwargs["reply_markup"],
                )
            except TelegramBadRequest as error:
                if "message is not modified" in error.message.lower():
                    return event.message
                raise
        method = event.bot.send_document if png.as_document else event.bot.send_photo
        return await method(chat_id, **kwargs)

    media_arg = "document" if png.as_document else "photo"
    key = png.cache_key
    try:
        cached = await game.cached_file(key)
    except Exception as error:
        log_failure(logger, "card_cache_read_failed", error, "telegram_media_cache")
        cached = None
    result = None
    if cached:
        try:
            result = await send(
                event.from_user.id, **{media_arg: cached}, caption=caption, reply_markup=markup
            )
        except TelegramBadRequest:
            # Telegram can expire/reject a file_id; the local PNG remains authoritative.
            pass
    if result is None:
        result = await send(
            event.from_user.id,
            **{media_arg: BufferedInputFile(png.content, filename=f"{character.code}.png")},
            caption=caption,
            reply_markup=markup,
        )
    if isinstance(result, Message):
        file_id = (
            result.document.file_id
            if png.as_document and result.document
            else result.photo[-1].file_id
            if result.photo
            else None
        )
        if file_id:
            try:
                await game.save_file(key, file_id)
            except Exception as error:
                # A cache write failure must not turn a delivered reward into a failed summon.
                log_failure(logger, "card_cache_failed", error, "telegram_media_cache")


async def show_card(event, game, character, card=None, copies=0, notice=None):
    """Compatibility for owned-card links; prepared Fate art is never rendered."""
    if character.code in CARD_IDS:
        rows = []
        caption = (h(notice) + "\n" if notice else "") + card_text(character)
        if card:
            stats = combat_stats(character, card)
            caption += (
                f"\n\n<b>Уровень {card.level}/{MAX_LEVEL} • NP {card.np}/{MAX_NP}</b>"
                f"\n⭐ {meter(card.level, MAX_LEVEL)}"
                f"\n💎 NP {'◆' * card.np}{'◇' * (MAX_NP - card.np)}"
                f"\n❤️ HP: {stats['hp']} • ⚔️ ATK: {stats['atk']} • 🛡️ DEF: {stats['def']}"
                f"\nАктивных копий: {copies}"
            )
            await game.tutorial(event.from_user.id, "card")
            team = await game.team(event.from_user.id)
            slots = [str(slot) for slot, item, _ in team if item.id == card.id]
            caption += (
                f"\nСила: {power(stats)} · Отряд: {', '.join(slots) if slots else 'нет'}"
                f"\nИзбранное: {'да' if card.favorite else 'нет'}"
                f"\nДо уровня: {2 * card.level if card.level < 100 else 'MAX'} EXP"
            )
            rows.append([("В отряд", f"r2:slot:{card.id.hex}")])
            if card.level < MAX_LEVEL:
                rows.append([("↑ Максимум за EXP", f"fc:l:{card.id.hex}:0:{uuid4().hex[:16]}")])
                new_key = uuid4().hex[:16]
                cost = upgrade_cost(card.level, 1)
                rows.append(
                    [(f"↑ Уровень +1 — {coins(cost)} EXP", f"fc:l:{card.id.hex}:1:{new_key}")]
                )
            if card.level <= MAX_LEVEL - 10:
                new_key = uuid4().hex[:16]
                cost = upgrade_cost(card.level, 10)
                rows.append(
                    [(f"↑ Уровень +10 — {coins(cost)} EXP", f"fc:l:{card.id.hex}:10:{new_key}")]
                )
            if card.np < MAX_NP:
                rows.append([("Слить дубликат → NP +1", f"fc:n:{card.id.hex}:{uuid4().hex[:16]}")])
            rows.append(
                [("← Коллекция", "r2:inv:0:power:all:all"), ("Награды", "r2:rewards:all:0")]
            )
            rows.append([("Все экземпляры персонажа", f"fc:copies:{character.code}:0")])
            rows.append(
                [
                    (
                        "Убрать из избранного" if card.favorite else "В избранное",
                        f"fc:fav:{card.id.hex}:{int(not card.favorite)}",
                    )
                ]
            )
        await send_png(event, game, character, caption, keyboard(rows + [back()]))
    else:
        # Legacy records and favorites remain accessible without inventing new card images.
        rows = []
        if card:
            rows.append(
                [
                    (
                        "Убрать из избранного" if card.favorite else "В избранное",
                        f"fc:fav:{card.id.hex}:{int(not card.favorite)}",
                    )
                ]
            )
        rows += [[("Архив коллекции", "fc:archive:0")], back()]
        text = (
            f"<b>Архив 0.2</b>\n{h(character.name_ru)} • {character.rarity}\n"
            f"Копий: {copies}\nЭкземпляр: {card.id if card else '—'}\n"
            "Исходные данные и арты сохранены в БД."
        )
        art = await game.illustration(character.image_asset_id)
        if art:
            caption = text + "\n" + h(art.attribution) + "\n" + h(art.page_url)
            if len(caption) <= 1024:
                try:
                    async with getattr(game, "media_lock", nullcontext()):
                        await event.bot.send_photo(
                            event.from_user.id,
                            photo=BufferedInputFile(art.content, filename="archive.jpg"),
                            caption=caption,
                            reply_markup=keyboard(rows),
                        )
                    return
                except TelegramAPIError as error:
                    log_failure(logger, "archive_delivery_failed", error, "telegram_media")
            text = caption
        await display(event, text, keyboard(rows))


def create_game_router(game, players):
    from cardlegends.bot.rpg_ui import RPGScreens

    rpg = RPGScreens(game)
    router = Router(name="fate_first_summon")

    async def home(event):
        await rpg.home(event)

    async def profile(event):
        await rpg.profile(event)

    async def team(event):
        members = await game.team(event.from_user.id)
        slots = {slot: (card, character) for slot, card, character in members}
        text = "<b>Мой отряд — до 3 карточек</b>\n"
        rows = []
        hp = atk = defense = 0
        for slot in range(1, 4):
            item = slots.get(slot)
            if item:
                card, character = item
                stats = combat_stats(character, card)
                hp += stats["hp"]
                atk += stats["atk"]
                defense += stats["def"]
                text += f"\n{slot}. {h(character.name_en)} — Ур. {card.level}, NP {card.np}/5"
                rows.append(
                    [
                        (f"Изменить слот {slot}", f"fc:ts:{slot}:0"),
                        ("Убрать", f"fc:tr:{slot}"),
                    ]
                )
            else:
                text += f"\n{slot}. Свободный слот"
                rows.append([(f"Добавить в слот {slot}", f"fc:ts:{slot}:0")])
        text += f"\n\nСумма характеристик: HP {hp} • ATK {atk} • DEF {defense}"
        text += f"\nБоевая мощь: {hp + 5 * atk + 3 * defense}"
        rows += [[(f"Развить: {c.name_en}", f"fc:card:{card.id.hex}")] for _, card, c in members]
        text += "\nВ бою участвуют только назначенные экземпляры. Они защищены от слияния."
        rows.append([("В башню", "fc:tower")])
        await rpg.art(
            event,
            text,
            keyboard(rows + [back()]),
            "team",
            "МОЙ ОТРЯД",
            "Три героя — одна цель",
        )

    async def team_pick(event, slot, page):
        if slot not in (1, 2, 3):
            raise GameError("Неверный слот отряда.")
        items, total, page = await game.team_candidates(event.from_user.id, page)
        if not items:
            await display(event, "Нет доступных карточек Fate. Сначала выполните призыв.")
            return
        rows = [
            [
                (
                    f"{character.name_en} — Ур. {card.level}, NP {card.np}/5",
                    f"fc:ta:{slot}:{card.id.hex}",
                )
            ]
            for card, character in items
        ]
        if total > 8:
            rows.append(
                [
                    ("←", f"fc:ts:{slot}:{max(0, page - 1)}"),
                    ("→", f"fc:ts:{slot}:{min((total - 1) // 8, page + 1)}"),
                ]
            )
        rows.append([("Назад к отряду", "fc:team")])
        await display(
            event,
            f"<b>Выберите карту в слот {slot}</b>\n"
            f"Страница {page + 1}/{max(1, (total + 7) // 8)}. "
            "Одна и та же копия не может занимать два слота.",
            keyboard(rows),
        )

    async def tower(event):
        await rpg.tower(event)

    async def tower_fight(event, floor, key):
        run, highest, balance, training_xp = await game.fight_tower(event.from_user.id, floor, key)
        title = "ПОБЕДА!" if run.victory else "ПОРАЖЕНИЕ"
        report = run.report or {}
        logs = report.get("log", [])
        excerpt = logs if len(logs) <= 6 else logs[:3] + ["…"] + logs[-3:]
        enemy_hp = report.get('stats', {}).get('hp', 1)
        team_max_hp = sum(x.get('stats', {}).get('hp', 0) for x in report.get('team', [])) or 1
        details = (
            f"\n\n👹 <b>{h(report.get('enemy', 'Архивный противник'))}</b>"
            f"\n❤️ HP противника: {coins(max(0, enemy_hp - report.get('dealt', 0)))}"
            f"/{coins(enemy_hp)}"
            f"\n{meter(enemy_hp - report.get('dealt', 0), enemy_hp)}"
            f"\n🛡 HP отряда: {coins(run.remaining_hp)}/{coins(team_max_hp)}"
            f"\n{meter(run.remaining_hp, team_max_hp)}"
            f"\n⚔ ATK {coins(report.get('stats', {}).get('atk', 0))}"
            f" · DEF {coins(report.get('stats', {}).get('def', 0))}"
            "\n👥 Отряд: "
            + ", ".join(h(x["name"]) for x in report.get("team", []))
            + f"\n💥 Нанесено: {coins(report.get('dealt', 0))}"
            + f" · Получено: {coins(report.get('received', 0))}\n"
            + "\n".join(h(line) for line in excerpt)
        )
        if run.first_clear:
            details += "\n◆ Новый этаж открыт. Проверьте достижения и награды."
        poster = battle_image(
            run.floor,
            str(report.get("enemy", "Противник")),
            max(1, int(enemy_hp)),
            0 if run.victory else None,
            max(1, int(team_max_hp)),
            int(run.remaining_hp),
            run.victory,
        )
        await rpg.art(
            event,
            f"<b>🏰 Башня · этаж {run.floor}: {title}</b>\n"
            f"⚔ Раундов: <b>{run.turns}</b>\n"
            f"🎁 Награда: <b>+{coins(run.coins)} Coins</b> · <b>+{coins(run.xp)} EXP</b>\n"
            f"🪙 Баланс: {coins(balance)} · 💠 EXP: {coins(training_xp)}\n"
            f"🏆 Рекорд: этаж {highest}" + details,
            keyboard(
                [
                    [("Продолжить башню", "fc:tower")],
                    [
                        ("Повторить этот этаж", f"r2:tower:{run.floor}"),
                        ("Награды", "r2:rewards:all:0"),
                    ],
                    [("Улучшить отряд", "fc:team")],
                    back(),
                ]
            ),
            "victory" if run.victory else "defeat",
            "ПОБЕДА" if run.victory else "ПОРАЖЕНИЕ",
            "Боевой отчёт",
            poster,
        )

    async def rates(event):
        characters = await game.collection(event.from_user.id)
        text = f"<b>{BANNER_NAME}</b>\n"
        text += "\n".join(
            f"{c.code} • {h(c.name_en)}: {DROP_RATES[c.code] // 100}%" for c, *_ in characters
        )
        text += (
            "\n\n×1 — 100 монет; ×10 — 1 000 монет.\n"
            "Десять независимых розыгрышей. Без скидки, pity и гарантий.\n"
            "Шансы одинаковы для всех игроков и не зависят от времени."
        )
        await display(event, text)

    async def summon_menu(event):
        banner = await game.banner_info()
        issues = await asyncio.to_thread(check_card_images, game.cards_dir)
        await rpg.art(
            event,
            f"<b>{h(banner.name)}</b>\n"
            "Artoria 33% • Morgan 33% • Enkidu 33% • Kama &amp; Ishtar 1%\n"
            "×10: десять независимых розыгрышей, без гарантий и скидки.\n"
            + (
                "\nИзображения ещё не готовы. Монеты не списываются.\n" + "\n".join(issues)
                if issues
                else "\nВыберите призыв:"
            ),
            keyboard(
                [
                    [("Призвать ×1 — 100 монет", f"fc:draw:1:{uuid4().hex}")],
                    [("Призвать ×10 — 1 000 монет", f"fc:draw:10:{uuid4().hex}")],
                    [("Шансы", "fc:rates"), ("Каталог", "fc:catalog:0")],
                    back(),
                ]
            ),
            "summon",
            "ВРАТА ПРИЗЫВА",
            "FATE COLLECTION",
        )

    async def collection(event):
        entries = await game.collection(event.from_user.id)
        text = "<b>Моя коллекция — Fate</b>\n"
        rows = []
        for c, quantity, *_ in entries:
            text += f"\n{'🔒 ' if not quantity else ''}{h(c.name_en)} ×{quantity}\n"
            text += f"{c.code} • {rarity(c)} • {h(c.card_class)}\n"
            rows.append(
                [(f"{'🔒 ' if not quantity else ''}{c.name_en} ×{quantity}", f"fc:owned:{c.code}")]
            )
        rows.append([("Фильтры и развитие", "r2:inv:0:power:all:all")])
        text += f"\nСобрано: {sum(q > 0 for _, q, *_ in entries)}/4"
        await display(event, text, keyboard(rows + [back()]))

    async def copies(event, code, page):
        character = await game.fate_character(code)
        cards, total, page = await game.copies(event.from_user.id, code, page)
        if not cards:
            raise GameError("Вы пока не получили эту карточку.")
        rows = [
            [
                (
                    f"{'★ ' if item.favorite else ''}Ур. {item.level} • NP {item.np}/5",
                    f"fc:card:{item.id.hex}",
                )
            ]
            for item in cards
        ]
        if total > 8:
            rows.append(
                [
                    ("←", f"fc:copies:{code}:{max(0, page - 1)}"),
                    ("→", f"fc:copies:{code}:{min((total - 1) // 8, page + 1)}"),
                ]
            )
        rows.append(("Назад в коллекцию", "fc:collection"))
        await display(
            event,
            f"<b>{h(character.name_en)} — экземпляры</b>\n"
            f"Всего: {total} • Страница {page + 1}/{max(1, (total + 7) // 8)}\n"
            "Выберите экземпляр для прокачки или слияния.",
            keyboard([[row] if isinstance(row, tuple) else row for row in rows]),
        )

    async def catalog(event, page):
        codes = list(CARD_IDS)
        if not 0 <= page < len(codes):
            raise ValueError
        c = await game.fate_character(codes[page])
        await display(
            event,
            card_text(c) + f"\n\nКарточка {page + 1}/4",
            keyboard(
                [
                    [("←", f"fc:catalog:{(page - 1) % 4}"), ("→", f"fc:catalog:{(page + 1) % 4}")],
                    [("Посмотреть PNG", f"fc:preview:{c.code}")],
                    back(),
                ]
            ),
        )

    async def results(event, operation_id):
        operation = await game.operation(event.from_user.id, operation_id)
        items = await game.results(event.from_user.id, operation.id)
        if not items:
            raise GameError("Результаты призыва не найдены.")
        if operation.banner_id != BANNER_ID:
            text = "<b>Архивный призыв 0.2</b>\n" + "\n".join(
                f"{i}. {h(c.name_ru)} • {c.rarity}" for i, (_, c) in enumerate(items, 1)
            )
            await display(event, text)
            return
        counts = Counter(c.code for _, c in items)
        unique = {c.code: c for _, c in items}
        header = (
            "🌟 <b>UR LIMITED! Призыв завершён!</b>"
            if "AV-L001" in counts
            else ("<b>Призыв завершён!</b>")
        )
        costs = (
            f"\nПотрачено: {coins(operation.total_cost)} монет"
            f"\nОстаток: {coins(operation.balance_after)} монет"
        )
        text = (
            header
            + "\n\n"
            + "\n".join(
                f"{i}. {'🌟 ' if c.limited else ''}{h(c.name_en)} — {rarity(c)}"
                for i, (_, c) in enumerate(items, 1)
            )
        )
        if len(items) == 1:
            c = items[0][1]
            text = header + f"\n\n{h(c.name_en)}\nКласс: {h(c.card_class)}\nРедкость: {rarity(c)}"
        else:
            text += "\n\nКопии в этом призыве:\n" + "\n".join(
                f"{h(unique[code].name_en)} ×{quantity}" for code, quantity in counts.items()
            )
            text += f"\nПовторных экземпляров: {len(items) - len(counts)}"
        text += costs
        rows = [
            [(f"PNG: {c.name_en} ×{counts[code]}", f"fc:owned:{code}")]
            for code, c in unique.items()
        ]
        rows += [
            [("Повторить показ результата", f"fc:result:{operation.id.hex}")],
            [("Ещё призыв", "fc:summon")],
            back(),
        ]
        markup = keyboard(rows)
        if len(items) == 1:
            try:
                await send_png(event, game, items[0][1], text, markup)
                return
            except CardImageError as error:
                text += "\n\n" + h(str(error))
            except TelegramAPIError as error:
                log_failure(logger, "summon_delivery_failed", error, "telegram_result_delivery")
                text += "\n\nРезультат сохранён. Telegram не принял изображение; повторите показ."
        await display(event, text, markup)

    async def history(event):
        operations = await game.history(event.from_user.id)
        await display(
            event,
            "<b>История призывов</b>\nПоследние 10 операций."
            if operations
            else "Вы ещё не призывали карточки.",
            keyboard(
                [
                    [
                        (
                            f"{s.created_at:%d.%m %H:%M} • ×{s.count}"
                            + (" • архив" if s.banner_id != BANNER_ID else ""),
                            f"fc:result:{s.id.hex}",
                        )
                    ]
                    for s in operations
                ]
                + [back()]
            ),
        )

    async def archive(event, page=0):
        rows, total, page = await game.browse(
            event.from_user.id, owned=True, page=page, archived=True
        )
        await display(
            event,
            f"<b>Архив коллекции 0.2</b>\nСохранено экземпляров: {total}\n"
            f"Страница {page + 1}/{max(1, (total + 4) // 5)}",
            keyboard(
                [
                    [
                        (
                            f"{'★ ' if card.favorite else ''}{c.name_ru} • {c.rarity}",
                            f"fc:card:{card.id.hex}",
                        )
                    ]
                    for card, c in rows
                ]
                + [
                    [
                        ("←", f"fc:archive:{max(0, page - 1)}"),
                        ("→", f"fc:archive:{min(max(0, (total - 1) // 5), page + 1)}"),
                    ],
                    back(),
                ]
            ),
        )

    async def help_page(event):
        await display(
            event,
            "<b>AnimeVault Cards — Fate</b>\n/start — меню\n"
            "/summon — призыв\n/collection — моя коллекция\n/catalog — каталог\n"
            "/profile — профиль\n/rates — точные шансы\n/history — история\n"
            "/daily — ежедневная награда 300 монет (00:00 UTC)\n"
            "/team — отряд до 3 карт\n/tower — бесконечная башня\n/help — помощь\n"
            "Уровень карточки: 1–100, +1 или +10 за EXP (2 × текущий уровень).\n"
            "Опыт карт зарабатывается призывами и прохождением башни.\n"
            "NP: 1–5, слияние с чистым дубликатом Ур. 1/NP 1 того же героя.\n"
            "HP/ATK/DEF растут от уровня и NP. Избранные копии не расходуются.\n"
            "Новый аккаунт: 1 000 AnimeVault Coins один раз. Реальных платежей нет.\n"
            "Старые экземпляры доступны в архиве, баланс и прогресс сохраняются.",
            keyboard(
                [
                    [("Путеводитель", "r2:tutorial:open"), ("Активности", "r2:activities")],
                    [("Каталог", "fc:catalog:0"), ("Шансы", "fc:rates")],
                    [("История призывов", "fc:history"), ("Архив", "fc:archive:0")],
                    back(),
                ]
            ),
        )

    async def daily(event, update_id=None):
        balance = await game.daily(event.from_user.id, telegram_update_id=update_id)
        await display(
            event,
            f"Награда получена: +{game.economy.daily} монет.\nБаланс: {balance}"
            "\n◆ Бонус календаря доступен в наградах.",
            keyboard(
                [[("Календарь", "r2:calendar"), ("Награды", "r2:rewards:calendar:0")], back()]
            ),
        )

    @router.message(Command("start"))
    async def start(message):
        if message.chat.type != "private":
            return
        await players.register(
            message.from_user.id, message.from_user.full_name, message.from_user.username
        )
        await home(message)

    @router.message(
        Command(
            "summon",
            "collection",
            "catalog",
            "profile",
            "rates",
            "help",
            "history",
            "daily",
            "team",
            "tower",
            "quests",
            "weekly",
            "achievements",
            "rewards",
            "mail",
            "activities",
            "calendar",
            "tutorial",
        )
    )
    async def command(message, event_update):
        if message.chat.type != "private":
            return
        name = message.text.split()[0].split("@")[0][1:]
        actions = {
            "summon": summon_menu,
            "collection": collection,
            "profile": profile,
            "rates": rates,
            "help": help_page,
            "history": history,
            "daily": daily,
            "team": team,
            "tower": tower,
            "quests": lambda e: rpg.objectives(e, "daily"),
            "weekly": lambda e: rpg.objectives(e, "weekly"),
            "achievements": lambda e: rpg.objectives(e, "achievements"),
            "rewards": rpg.rewards,
            "mail": lambda e: rpg.rewards(e, "mail"),
            "activities": rpg.activities,
            "calendar": rpg.calendar,
            "tutorial": lambda e: rpg.tutorial(e, "open"),
        }
        try:
            if name not in ("help", "rates", "catalog"):
                await game.visit(message.from_user.id)
            if name == "daily":
                await daily(message, event_update.update_id)
            elif name == "catalog":
                await catalog(message, 0)
            else:
                await actions[name](message)
        except GameError as error:
            await display(message, h(str(error)))

    @router.message()
    async def unknown(message):
        if message.chat.type == "private":
            await display(message, "Неизвестная команда. Используйте /help.", menu())

    @router.callback_query()
    async def callback(query, event_update):
        try:
            busy = any(
                (query.data or "").startswith(prefix)
                for prefix in ("fc:tf:", "fc:l:", "fc:n:", "r2:claim")
            )
            await query.answer("Сохраняем результат…" if busy else None)
        except TelegramAPIError as error:
            log_failure(logger, "callback_ack_failed", error, "telegram_callback_ack")
        if (
            not isinstance(query.message, Message)
            or query.message.chat.type != "private"
            or query.message.chat.id != query.from_user.id
        ):
            return
        parts = (query.data or "").split(":")
        # Old menus stay useful; old draw buttons only open the new banner, never spend.
        if parts[0] in ("av", "menu"):
            if len(parts) >= 2 and parts[1] in ("card", "fav"):
                parts = ["fc", *parts[1:]]
            elif len(parts) >= 3 and parts[1] == "result":
                parts = ["fc", "result", parts[2]]
            else:
                action = parts[1] if len(parts) > 1 else "stale"
                action = {
                    "list": "collection" if len(parts) > 2 and parts[2] == "o" else "catalog",
                    "draw": "summon",
                }.get(action, action)
                parts = ["fc", action] + (["0"] if action == "catalog" else [])
        try:
            await game.visit(query.from_user.id)
            if parts[0] == "r2":
                await rpg.handle(query, parts)
                return
            if len(parts) < 2 or parts[0] != "fc":
                raise ValueError
            action = parts[1]
            simple = {
                "home": home,
                "profile": profile,
                "collection": collection,
                "rates": rates,
                "summon": summon_menu,
                "help": help_page,
                "history": history,
                "daily": daily,
                "team": team,
                "tower": tower,
            }
            if action == "daily" and len(parts) == 2:
                await daily(query, event_update.update_id)
            elif action in simple and len(parts) == 2:
                await simple[action](query)
            elif action == "draw" and len(parts) == 4 and parts[2] in ("1", "10"):
                sid = await game.summon(
                    query.from_user.id,
                    int(parts[2]),
                    parts[3],
                    telegram_update_id=event_update.update_id,
                )
                await results(query, sid)
            elif action == "ts" and len(parts) == 4:
                await team_pick(query, int(parts[2]), int(parts[3]))
            elif action == "ta" and len(parts) == 4:
                await game.assign_team(query.from_user.id, int(parts[2]), parts[3])
                await team(query)
            elif action == "tr" and len(parts) == 3:
                await game.remove_team(query.from_user.id, int(parts[2]))
                await team(query)
            elif action == "tf" and len(parts) == 4:
                await tower_fight(query, int(parts[2]), parts[3])
            elif action == "result" and len(parts) == 3:
                await results(query, UUID(parts[2]))
            elif action == "catalog" and len(parts) == 3:
                await catalog(query, int(parts[2]))
            elif action in ("owned", "preview") and len(parts) == 3:
                if action == "owned":
                    card, c, copies = await game.owned_character(query.from_user.id, parts[2])
                    await show_card(query, game, c, card, copies)
                else:
                    c = await game.fate_character(parts[2])
                    await send_png(query, game, c)
            elif action == "copies" and len(parts) == 4:
                await copies(query, parts[2], int(parts[3]))
            elif action == "l" and len(parts) == 5:
                changed = await game.level_up(query.from_user.id, parts[2], int(parts[3]), parts[4])
                card, c, quantity = await game.card(query.from_user.id, parts[2])
                await show_card(
                    query,
                    game,
                    c,
                    card,
                    quantity,
                    "◆ Уровень повышен! Прогресс заданий сохранён."
                    if changed
                    else "Операция уже выполнена; показан текущий экземпляр.",
                )
            elif action == "n" and len(parts) == 4:
                changed = await game.fuse_np(query.from_user.id, parts[2], parts[3])
                card, c, quantity = await game.card(query.from_user.id, parts[2])
                await show_card(
                    query,
                    game,
                    c,
                    card,
                    quantity,
                    "◆ NP повышен! Проверьте достижения."
                    if changed
                    else "Операция уже выполнена; показан текущий экземпляр.",
                )
            elif action == "archive" and len(parts) == 3:
                await archive(query, int(parts[2]))
            elif action == "card" and len(parts) == 3:
                card, c, copies = await game.card(query.from_user.id, parts[2])
                await show_card(query, game, c, card, copies)
            elif action == "fav" and len(parts) == 4 and parts[3] in ("0", "1"):
                await game.favorite(query.from_user.id, parts[2], parts[3] == "1")
                await display(
                    query,
                    "Избранное обновлено.",
                    keyboard([[("Открыть карточку", f"fc:card:{UUID(parts[2]).hex}")], back()]),
                )
            else:
                raise ValueError
        except (ValueError, IndexError, AttributeError):
            await display(query, "Кнопка устарела. Откройте /start.", menu())
        except GameError as error:
            await display(query, h(str(error)), menu())

    return router
