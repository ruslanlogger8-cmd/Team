"""Клавиатуры. Каждая кнопка цветная и с премиум-иконкой.

Обычные эмодзи в подписях не используются: иконка приходит отдельным полем
icon_custom_emoji_id, поэтому без Premium кнопка покажет чистый текст,
а не подменённый символ.
"""
from __future__ import annotations

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .emoji import icon

# Цвет по смыслу действия: деньги — зелёный, отмена и выход — красный,
# всё остальное — синий. Кнопок без цвета в интерфейсе нет.
PRIMARY = ButtonStyle.PRIMARY
SUCCESS = ButtonStyle.SUCCESS
DANGER = ButtonStyle.DANGER


def btn(
    text: str,
    callback_data: str,
    style: ButtonStyle = PRIMARY,
    icon_key: str | None = None,
) -> InlineKeyboardButton:
    kwargs: dict = {"text": text, "callback_data": callback_data, "style": style}
    if icon_key:
        emoji_id = icon(icon_key)
        if emoji_id:
            kwargs["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**kwargs)


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            btn("Профиль", "m:profile", PRIMARY, "profile"),
            btn("Баланс", "m:balance", PRIMARY, "balance"),
        ],
        [
            btn("Кошелёк", "m:wallet", PRIMARY, "wallet"),
            btn("История", "m:history", PRIMARY, "history"),
        ],
        [btn("Топ воркеров", "m:top", PRIMARY, "top")],
        [btn("Вывести средства", "m:withdraw", SUCCESS, "withdraw")],
    ]
    if is_admin:
        rows.append([btn("Панель администратора", "m:admin", DANGER, "admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn("В меню", "m:main", PRIMARY, "back")]]
    )


def wallet_menu(has_wallet: bool) -> InlineKeyboardMarkup:
    label = "Изменить кошелёк" if has_wallet else "Указать кошелёк"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(label, "m:wallet_set", SUCCESS, "key")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )


def confirm_withdraw(amount_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(f"Подтвердить · {amount_text}", "wd:yes", SUCCESS, "check")],
            [btn("Отменить", "wd:no", DANGER, "cross")],
        ]
    )


def history_nav(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(btn("Назад", f"h:{page - 1}", PRIMARY, "back"))
    if total_pages > 1:
        nav.append(btn(f"{page} из {total_pages}", "h:noop", PRIMARY, "dot"))
    if page < total_pages:
        nav.append(btn("Вперёд", f"h:{page + 1}", PRIMARY, "next"))
    rows = [nav] if nav else []
    rows.append([btn("В меню", "m:main", PRIMARY, "back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Статистика", "a:stats", PRIMARY, "stats")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )
