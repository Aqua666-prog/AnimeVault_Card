from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from cardlegends.application.players import PlayerProfile, PlayerService
from cardlegends.bot.keyboards import main_menu

HELP_TEXT = (
    "<b>CardLegends — помощь</b>\n"
    "/start — регистрация и главное меню\n"
    "/profile — ваш профиль\n"
    "/help — помощь\n\n"
    "Коллекционирование карточек, призыв и сражения появятся в следующих этапах."
)
NOT_REGISTERED = "Сначала зарегистрируйтесь командой /start."


def profile_text(profile: PlayerProfile) -> str:
    return (
        "<b>Ваш профиль</b>\n"
        f"Telegram ID: <code>{profile.telegram_id}</code>\n"
        f"Имя: {escape(profile.display_name)}\n"
        f"Дата регистрации: {profile.registered_at:%d.%m.%Y %H:%M} UTC\n"
        f"Статус: {escape(profile.status)}"
    )


async def start(message: Message, players: PlayerService) -> None:
    if message.from_user is None:
        return
    profile = await players.register(
        message.from_user.id, message.from_user.full_name, message.from_user.username
    )
    await message.answer(
        f"Добро пожаловать в CardLegends, {escape(profile.display_name)}!\n"
        "Ваш профиль сохранён. Выберите раздел:",
        reply_markup=main_menu(),
    )


async def profile(message: Message, players: PlayerService) -> None:
    if message.from_user is None:
        return
    player = await players.profile(message.from_user.id)
    await message.answer(profile_text(player) if player else NOT_REGISTERED)


async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


async def profile_callback(query: CallbackQuery, players: PlayerService) -> None:
    player = await players.profile(query.from_user.id)
    await query.answer()
    # Всегда отправляем профиль лично: кнопка могла быть нажата в групповом чате.
    await query.bot.send_message(
        query.from_user.id, profile_text(player) if player else NOT_REGISTERED
    )


async def help_callback(query: CallbackQuery) -> None:
    await query.answer()
    await query.bot.send_message(query.from_user.id, HELP_TEXT, reply_markup=main_menu())


async def unfinished_callback(query: CallbackQuery) -> None:
    await query.answer("Раздел в разработке", show_alert=True)


async def unknown_callback(query: CallbackQuery) -> None:
    await query.answer("Кнопка устарела. Откройте меню командой /start.", show_alert=True)


async def unknown_command(message: Message) -> None:
    await message.answer("Неизвестная команда. Используйте /help.")


def create_router() -> Router:
    router = Router(name="commands")
    # Профиль и регистрация доступны только в личном чате.
    router.message.register(start, CommandStart(), F.chat.type == "private")
    router.message.register(profile, Command("profile"), F.chat.type == "private")
    router.message.register(help_command, Command("help"))
    router.callback_query.register(profile_callback, F.data == "menu:profile")
    router.callback_query.register(help_callback, F.data == "menu:help")
    router.callback_query.register(
        unfinished_callback, F.data.in_({"menu:collection", "menu:summon", "menu:battles"})
    )
    router.callback_query.register(unknown_callback)
    router.message.register(unknown_command, F.text.startswith("/"))
    return router
