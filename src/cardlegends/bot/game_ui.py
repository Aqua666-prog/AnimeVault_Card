import asyncio
import hashlib
from html import escape as h
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from cardlegends.domain.game import RARITIES, GameError
from cardlegends.infrastructure.cards import VERSION, render, validate_image
from cardlegends.infrastructure.catalog import catalog_data

RENDER_LOCK = asyncio.Semaphore(1)


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
            [("Призыв", "av:summon"), ("Коллекция", "av:list:o:0:all:all:rarity:0")],
            [("Профиль", "av:profile"), ("Ежедневная награда", "av:daily")],
            [("Каталог", "av:list:c:0:all:all:rarity:0"), ("Помощь", "av:help")],
        ]
    )


def back():
    return [("Главное меню", "av:home")]


async def display(event, text, markup):
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        try:
            if event.message.photo:
                await event.message.edit_caption(caption=text, reply_markup=markup)
            else:
                await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise
    else:
        await event.answer(text, reply_markup=markup)


async def show_card(query, game, character, card=None, copies=0):
    approved = await game.illustration(character.image_asset_id)
    content = approved.content if approved else None
    if content:
        try:
            await asyncio.to_thread(validate_image, content)
        except Exception:
            approved, content = None, None
    franchise = next(
        c["franchise"] for c in catalog_data() if c["franchise_id"] == character.franchise_id
    )
    key = hashlib.sha256(
        (VERSION + character.name_ru + franchise + character.rarity).encode() + (content or b"")
    ).hexdigest()
    cached = await game.cached_file(key)
    caption = f"<b>{h(character.name_ru)}</b>\n{h(franchise)} • {character.rarity}"
    if card:
        caption += (
            f"\nЭкземпляров персонажа: {copies}\nИзбранное: {'да' if card.favorite else 'нет'}"
        )
    if approved:
        caption += "\n" + h(approved.attribution or "") + "\n" + h(approved.page_url)
    else:
        caption += "\nИллюстрация: нейтральная заглушка"
    rows = []
    if card:
        rows.append(
            [
                (
                    "Убрать из избранного" if card.favorite else "В избранное",
                    f"av:fav:{card.id.hex}:{int(not card.favorite)}",
                )
            ]
        )
    banner = await game.banner_info()
    rows += [
        [
            ("Коллекция", "av:list:o:0:all:all:rarity:0"),
            (f"Ещё призыв • {banner.single_cost}", f"av:draw:1:{uuid4().hex}"),
        ],
        back(),
    ]

    async def media(file):
        if isinstance(query.message, Message) and query.message.photo:
            result = await query.message.edit_media(
                media=InputMediaPhoto(media=file, caption=caption), reply_markup=keyboard(rows)
            )
        else:
            result = await query.bot.send_photo(
                query.from_user.id, photo=file, caption=caption, reply_markup=keyboard(rows)
            )
        return result

    try:
        result = await media(cached) if cached else None
    except TelegramBadRequest:
        result = None
    if result is None:
        async with RENDER_LOCK:
            image = await asyncio.to_thread(
                render, character.name_ru, franchise, character.rarity, content
            )
        result = await media(BufferedInputFile(image, filename="card.jpg"))
    if isinstance(result, Message) and result.photo:
        await game.save_file(key, result.photo[-1].file_id)


def create_game_router(game, players):
    router = Router(name="first_playable")

    async def home(event):
        data = await game.profile(event.from_user.id)
        player = data["player"]
        name = "@" + player.username if player.username else player.display_name
        crystals = format(data["balance"], ",").replace(",", " ")
        await display(
            event,
            f"<b>ANIMEVAULT CARDS</b>\n\nКоллекционируй персонажей любимых аниме.\n\n"
            f"Игрок: {h(name)}\nУровень: {data['level']}\nКристаллы: {crystals}\n"
            f"Коллекция: {data['unique']} / {data['size']}",
            menu(),
        )

    @router.message(Command("start"), F.chat.type == "private")
    async def start(message):
        await players.register(
            message.from_user.id, message.from_user.full_name, message.from_user.username
        )
        await home(message)

    async def profile(event):
        p = await game.profile(event.from_user.id)
        await display(
            event,
            f"<b>Профиль</b>\nTelegram ID: <code>{event.from_user.id}</code>\n"
            f"Имя: {h(p['player'].display_name)}\n"
            f"Дата регистрации: {p['player'].created_at:%d.%m.%Y} UTC\n"
            f"Уровень: {p['level']} • Опыт: {p['xp']}\nКристаллы: {p['balance']}\n"
            f"Уникальные: {p['unique']}/{p['size']} • Все: {p['total']}\nПризывов: {p['summons']}\n"
            f"Последняя активность: {p['player'].last_seen_at:%d.%m.%Y %H:%M} UTC",
            keyboard([back()]),
        )

    @router.message(Command("profile"), F.chat.type == "private")
    async def profile_command(message):
        await profile(message)

    async def help_page(event):
        await display(
            event,
            "<b>AnimeVault Cards</b>\n/start — меню\n/profile — профиль\n/help — помощь\n"
            "Призыв: 100 кристаллов; ×10: 900, минимум одна SR+.\n"
            "N 50% • R 30% • SR 15% • SSR 4% • UR 1%.\n"
            "Ежедневно: 300 кристаллов, сброс в 00:00 UTC.\n"
            "10 опыта за карточку, новый уровень каждые 100 опыта.\n"
            "Дубликаты — отдельные экземпляры. Реальных платежей нет.",
            keyboard([back()]),
        )

    @router.message(Command("help"), F.chat.type == "private")
    async def help_command(message):
        await help_page(message)

    @router.message(F.text.startswith("/"), F.chat.type == "private")
    async def unknown_command(message):
        await message.answer("Неизвестная команда. Используйте /help.", reply_markup=menu())

    @router.callback_query()
    async def callback(query):
        # Acknowledge immediately, before DB/render work. Never display private IDs in groups.
        await query.answer()
        if (
            not isinstance(query.message, Message)
            or query.message.chat.type != "private"
            or (query.message.chat.id != query.from_user.id)
        ):
            return
        parts = (query.data or "").split(":")
        if parts[0] == "menu":
            parts = [
                "av",
                {
                    "collection": "list",
                    "profile": "profile",
                    "help": "help",
                    "summon": "summon",
                }.get(parts[-1], "stale"),
            ]
            if parts[-1] == "list":
                parts += ["o", "0", "all", "all", "rarity", "0"]
        try:
            if len(parts) < 2 or parts[0] != "av":
                raise GameError("Кнопка устарела. Откройте /start.")
            action = parts[1]
            if action == "home":
                await home(query)
            elif action == "profile":
                await profile(query)
            elif action == "help":
                await help_page(query)
            elif action == "daily":
                balance = await game.daily(query.from_user.id)
                await display(
                    query,
                    f"Награда получена: +{game.economy.daily} кристаллов.\nБаланс: {balance}",
                    keyboard([back()]),
                )
            elif action == "summon":
                banner = await game.banner_info()
                odds = " • ".join(
                    f"{r} {w}%" for r, w in zip(RARITIES, banner.weights, strict=True)
                )
                await display(
                    query,
                    f"<b>{h(banner.name)}</b>\n{odds}\n×10: гарантирована SR+.\nВыберите призыв:",
                    keyboard(
                        [
                            [
                                (f"Призыв • {banner.single_cost}", f"av:draw:1:{uuid4().hex}"),
                                (f"×10 • {banner.ten_cost}", f"av:draw:10:{uuid4().hex}"),
                            ],
                            [("История", "av:history")],
                            back(),
                        ]
                    ),
                )
            elif action == "draw":
                if len(parts) != 4:
                    raise ValueError
                UUID(parts[3])
                sid = await game.summon(query.from_user.id, int(parts[2]), parts[3])
                await results(query, sid, 0)
            elif action == "result":
                await results(query, UUID(parts[2]), int(parts[3]))
            elif action == "history":
                history = await game.history(query.from_user.id)
                rows = [
                    [(f"{s.created_at:%d.%m %H:%M} • ×{s.count}", f"av:result:{s.id.hex}:0")]
                    for s in history
                ]
                await display(
                    query,
                    "<b>История призывов</b>\nПоследние 10 операций."
                    if rows
                    else "Вы ещё не призывали карточки.",
                    keyboard(rows + [back()]),
                )
            elif action == "list":
                await listing(query, parts)
            elif action == "card":
                card, character, copies = await game.card(query.from_user.id, parts[2])
                await show_card(query, game, character, card, copies)
            elif action == "char":
                character = await game.character(int(parts[2]))
                if not character or not character.is_active:
                    raise GameError("Персонаж недоступен.")
                await show_card(query, game, character)
            elif action == "fav":
                if parts[3] not in ("0", "1"):
                    raise ValueError
                await game.favorite(query.from_user.id, parts[2], parts[3] == "1")
                await display(
                    query,
                    "Избранное обновлено.",
                    keyboard([[("Открыть карточку", f"av:card:{parts[2]}")], back()]),
                )
            else:
                raise GameError("Кнопка устарела. Откройте /start.")
        except (ValueError, IndexError):
            await display(query, "Кнопка устарела. Откройте /start.", menu())
        except GameError as error:
            await display(query, str(error), menu())

    async def results(query, sid, page):
        items = await game.results(query.from_user.id, sid)
        if not items:
            raise GameError("Призыв не найден.")
        if len(items) == 1:
            card, character, copies = await game.card(query.from_user.id, items[0][0].id)
            await show_card(query, game, character, card, copies)
            return
        page = max(0, min(page, (len(items) - 1) // 5))
        best = max((c for _, c in items), key=lambda c: RARITIES.index(c.rarity))
        rows = [
            [(f"{c.rarity} • {c.name_ru}", f"av:card:{card.id.hex}")]
            for card, c in items[page * 5 : page * 5 + 5]
        ]
        rows += [
            [
                ("←", f"av:result:{sid.hex}:{max(0, page - 1)}"),
                ("→", f"av:result:{sid.hex}:{min((len(items) - 1) // 5, page + 1)}"),
            ],
            [("Ещё призыв", "av:summon")],
            back(),
        ]
        await display(
            query,
            f"<b>Призыв ×{len(items)}</b>\nЛучшая: {h(best.name_ru)} • {best.rarity}\n"
            f"Страница {page + 1}/{(len(items) + 4) // 5}",
            keyboard(rows),
        )

    async def listing(query, parts):
        _, _, kind, page, rarity, franchise, sort, fav = parts
        if (
            kind not in ("o", "c")
            or rarity not in (*RARITIES, "all")
            or sort not in ("rarity", "time")
            or (fav not in ("0", "1"))
        ):
            raise ValueError
        franchises = list(dict.fromkeys(c["franchise_id"] for c in catalog_data()))
        if franchise not in ["all", *franchises]:
            raise ValueError
        rows, total, page = await game.browse(
            query.from_user.id, kind == "o", int(page), rarity, franchise, sort, fav == "1"
        )

        def link(p=page, r=rarity, f=franchise, s=sort, v=fav):
            return f"av:list:{kind}:{p}:{r}:{f}:{s}:{v}"

        buttons = []
        for row in rows:
            card, c = row if kind == "o" else (None, row[0])
            buttons.append(
                [
                    (
                        f"{'★ ' if card and card.favorite else ''}{c.rarity} • {c.name_ru}",
                        f"av:card:{card.id.hex}" if card else f"av:char:{c.id}",
                    )
                ]
            )
        options = ["all", *RARITIES]
        fs = ["all", *franchises]
        buttons += [
            [
                (
                    f"Редкость: {rarity}",
                    link(0, options[(options.index(rarity) + 1) % len(options)]),
                ),
                (f"Аниме: {franchise}", link(0, f=fs[(fs.index(franchise) + 1) % len(fs)])),
            ],
            [
                (
                    "По времени" if sort == "rarity" else "По редкости",
                    link(s="time" if sort == "rarity" else "rarity"),
                ),
                ("Все / избранное", link(0, v="0" if fav == "1" else "1")),
            ],
            [("←", link(max(0, page - 1))), ("→", link(min(max(0, (total - 1) // 5), page + 1)))],
            [("Призыв", "av:summon")],
            back(),
        ]
        await display(
            query,
            ("<b>Коллекция</b>" if kind == "o" else "<b>Каталог</b>")
            + f"\nНайдено: {total} • Страница {page + 1}/{max(1, (total + 4) // 5)}"
            + ("\nПока пусто. Попробуйте призыв или измените фильтры." if not total else ""),
            keyboard(buttons),
        )

    return router
