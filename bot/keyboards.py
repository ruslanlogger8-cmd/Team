from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


def worker_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="💼 Кошелёк")],
            [KeyboardButton(text="💸 Вывести")],
        ],
        resize_keyboard=True,
    )


def confirm_withdraw(amount_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"✅ Вывести {amount_text}", callback_data="wd:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="wd:no"),
            ]
        ]
    )
