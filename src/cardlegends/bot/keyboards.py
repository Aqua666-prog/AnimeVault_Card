from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Профиль", callback_data="menu:profile")],
            [
                InlineKeyboardButton(text="Коллекция", callback_data="menu:collection"),
                InlineKeyboardButton(text="Призыв", callback_data="menu:summon"),
            ],
            [
                InlineKeyboardButton(text="Сражения", callback_data="menu:battles"),
                InlineKeyboardButton(text="Помощь", callback_data="menu:help"),
            ],
        ]
    )
