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
    Message,
)

from cardlegends.domain.fate import BANNER_ID, BANNER_NAME, CARD_IDS, DROP_RATES
from cardlegends.domain.game import GameError
from cardlegends.domain.progression import MAX_LEVEL, MAX_NP, combat_stats, upgrade_cost
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
    # A token belongs to this button, not to each received callback. Double taps reuse it.
    return keyboard(
        [
            [("Призвать ×1 — 100 монет", f"fc:draw:1:{uuid4().hex}")],
            [("Призвать ×10 — 1 000 монет", f"fc:draw:10:{uuid4().hex}")],
            [("Моя коллекция", "fc:collection"), ("Каталог карточек", "fc:catalog:0")],
            [("Профиль", "fc:profile"), ("Шансы выпадения", "fc:rates")],
            [("История", "fc:history"), ("Ежедневная награда", "fc:daily")],
            [("Архив коллекции 0.2", "fc:archive:0"), ("Помощь", "fc:help")],
        ]
    )


def back():
    return [("Главное меню", "fc:home")]


def coins(value):
    return format(value, ",").replace(",", " ")


def rarity(character):
    return "UR Limited" if character.limited else character.rarity


async def display(event, text, markup=None):
    markup = markup or keyboard([back()])
    if isinstance(event, CallbackQuery):
        if not isinstance(event.message, Message):
            return
        # Photo captions have a 1024-char limit; catalog/result text has its own message.
        if event.message.photo or event.message.document:
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
    send = event.bot.send_document if png.as_document else event.bot.send_photo
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


async def show_card(event, game, character, card=None, copies=0):
    """Compatibility for owned-card links; prepared Fate art is never rendered."""
    if character.code in CARD_IDS:
        rows = []
        caption = card_text(character)
        if card:
            stats = combat_stats(character, card)
            caption += (
                f"\n\n<b>Уровень {card.level}/{MAX_LEVEL} • NP {card.np}/{MAX_NP}</b>"
                f"\nHP: {stats['hp']} • ATK: {stats['atk']} • DEF: {stats['def']}"
                f"\nАктивных копий: {copies}"
            )
            if card.level < MAX_LEVEL:
                new_key = uuid4().hex[:16]
                cost = upgrade_cost(card.level, 1)
                rows.append(
                    [(f"↑ Уровень +1 — {coins(cost)} монет", f"fc:l:{card.id.hex}:1:{new_key}")]
                )
            if card.level <= MAX_LEVEL - 10:
                new_key = uuid4().hex[:16]
                cost = upgrade_cost(card.level, 10)
                rows.append(
                    [(f"↑ Уровень +10 — {coins(cost)} монет", f"fc:l:{card.id.hex}:10:{new_key}")]
                )
            if card.np < MAX_NP:
                rows.append([("Слить дубликат → NP +1", f"fc:n:{card.id.hex}:{uuid4().hex[:16]}")])
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
    router = Router(name="fate_first_summon")

    async def home(event):
        p = await game.profile(event.from_user.id)
        await display(
            event,
            f"<b>ANIMEVAULT CARDS</b>\n{BANNER_NAME}\n\n"
            f"Игрок: {h(p['player'].display_name)}\n"
            f"AnimeVault Coins: {coins(p['balance'])}\n"
            f"Fate Collection: {p['unique']}/4",
            menu(),
        )

    async def profile(event):
        p = await game.profile(event.from_user.id)
        await display(
            event,
            f"<b>Профиль</b>\n{h(p['player'].display_name)}\n"
            f"Регистрация: {p['player'].created_at:%d.%m.%Y} UTC\n"
            f"AnimeVault Coins: {coins(p['balance'])}\n"
            f"Уровень: {p['level']} • Опыт: {p['xp']}\n"
            f"Fate: {p['unique']}/4 • Экземпляров: {p['total']}\n"
            f"Архивных экземпляров: {p['archived']}\nПризывов: {p['summons']}",
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
        await display(
            event,
            f"<b>{h(banner.name)}</b>\n"
            "Artoria 33% • Morgan 33% • Enkidu 33% • Kama &amp; Ishtar 1%\n"
            "×10: десять независимых розыгрышей, без гарантий и скидки.\n"
            + (
                "\nИзображения ещё не готовы. Монеты не списываются.\n" + "\n".join(issues)
                if issues
                else "\nВыберите призыв:"
            ),
            menu(),
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
            "/daily — ежедневная награда 300 монет (00:00 UTC)\n/help — помощь\n"
            "Уровень карточки: 1–100, +1 или +10 за монеты (2 × текущий уровень).\n"
            "NP: 1–5, слияние с чистым дубликатом Ур. 1/NP 1 того же героя.\n"
            "HP/ATK/DEF растут от уровня и NP. Избранные копии не расходуются.\n"
            "Новый аккаунт: 1 000 AnimeVault Coins один раз. Реальных платежей нет.\n"
            "Старые экземпляры доступны в архиве, баланс и прогресс сохраняются.",
        )

    async def daily(event):
        balance = await game.daily(event.from_user.id)
        await display(event, f"Награда получена: +{game.economy.daily} монет.\nБаланс: {balance}")

    @router.message(Command("start"))
    async def start(message):
        if message.chat.type != "private":
            return
        await players.register(
            message.from_user.id, message.from_user.full_name, message.from_user.username
        )
        await home(message)

    @router.message(
        Command("summon", "collection", "catalog", "profile", "rates", "help", "history", "daily")
    )
    async def command(message):
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
        }
        try:
            if name == "catalog":
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
            await query.answer()
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
            }
            if action in simple and len(parts) == 2:
                await simple[action](query)
            elif action == "draw" and len(parts) == 4 and parts[2] in ("1", "10"):
                sid = await game.summon(
                    query.from_user.id,
                    int(parts[2]),
                    parts[3],
                    telegram_update_id=event_update.update_id,
                )
                await results(query, sid)
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
                await game.level_up(query.from_user.id, parts[2], int(parts[3]), parts[4])
                card, c, quantity = await game.card(query.from_user.id, parts[2])
                await show_card(query, game, c, card, quantity)
            elif action == "n" and len(parts) == 4:
                await game.fuse_np(query.from_user.id, parts[2], parts[3])
                card, c, quantity = await game.card(query.from_user.id, parts[2])
                await show_card(query, game, c, card, quantity)
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
