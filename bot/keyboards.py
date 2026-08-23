"""Клавиатуры. Цветные кнопки через ButtonStyle (Bot API), иконки — премиум-эмодзи."""
from __future__ import annotations

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .emoji import icon


def btn(
    text: str,
    callback_data: str,
    style: ButtonStyle | None = None,
    icon_key: str | None = None,
) -> InlineKeyboardButton:
    """Кнопка с необязательным цветом и премиум-иконкой."""
    kwargs: dict = {"text": text, "callback_data": callback_data}
    if style is not None:
        kwargs["style"] = style
    if icon_key:
        emoji_id = icon(icon_key)
        if emoji_id:
            kwargs["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**kwargs)


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            btn("👤 Профиль", "m:profile", ButtonStyle.PRIMARY, "user"),
            btn("💰 Баланс", "m:balance", ButtonStyle.PRIMARY, "money"),
        ],
        [
            btn("💼 Кошелёк", "m:wallet", icon_key="wallet"),
            btn("🧾 История", "m:history", icon_key="history"),
        ],
        [btn("🏆 Топ-10", "m:top", icon_key="trophy")],
        [btn("💸 Вывести", "m:withdraw", ButtonStyle.SUCCESS, "send")],
    ]
    if is_admin:
        rows.append([btn("⚙️ Админка", "m:admin", ButtonStyle.DANGER, "gear")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("◀️ Назад", "m:main", icon_key="back")]])


def confirm_withdraw(amount_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(f"✅ Вывести {amount_text}", "wd:yes", ButtonStyle.SUCCESS, "check")],
            [btn("❌ Отмена", "wd:no", ButtonStyle.DANGER, "cross")],
        ]
    )


def history_nav(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(btn("◀️", f"h:{page - 1}"))
    if total_pages > 1:
        nav.append(btn(f"{page}/{total_pages}", "h:noop"))
    if page < total_pages:
        nav.append(btn("▶️", f"h:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([btn("◀️ Назад", "m:main", icon_key="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("📊 Статистика", "a:stats", ButtonStyle.PRIMARY, "chart")],
            [btn("◀️ Назад", "m:main", icon_key="back")],
        ]
    )
