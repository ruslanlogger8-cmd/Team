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
        [btn("Заявка на выплату", "m:payout_request", SUCCESS, "withdraw")],
        [btn("Подать заявку на подарок", "m:claim", SUCCESS, "gift")],
        [btn("Топ воркеров", "m:top", PRIMARY, "top")],
        [btn("Вывести баланс", "m:withdraw", SUCCESS, "coin")],
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
            [btn("Заявки на выплату", "a:requests", SUCCESS, "withdraw")],
            [btn("Воркеры", "a:workers", PRIMARY, "users")],
            [btn("Статистика", "a:stats", PRIMARY, "stats")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )


def claim_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Отправить ссылку", "m:claim_send", SUCCESS, "link")],
            [btn("Мои подарки", "m:my_gifts", PRIMARY, "history")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )


def withdraw_choice(all_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(f"Вывести всё · {all_text}", "wd:all", SUCCESS, "withdraw")],
            [btn("Указать сумму", "wd:part", PRIMARY, "coin")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )


def claim_decision(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            btn("Подтвердить", f"cl:ok:{request_id}", SUCCESS, "check"),
            btn("Отклонить", f"cl:no:{request_id}", DANGER, "cross"),
        ]]
    )


def pay_button(worker_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn("Выплатить сейчас", f"pay:{worker_id}", SUCCESS, "withdraw")]]
    )


def withdrawal_actions(withdrawal_id: int) -> InlineKeyboardMarkup:
    """Решение админа по придержанной заявке воркера."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Выплатить", f"wpay:{withdrawal_id}", SUCCESS, "withdraw")],
            [btn("Отклонить", f"wrej:{withdrawal_id}", DANGER, "cross")],
        ]
    )


def payout_request_menu() -> InlineKeyboardMarkup:
    """Экран заявки на выплату у воркера."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Подать заявку", "pr:new", SUCCESS, "withdraw")],
            [btn("Мои заявки", "pr:mine", PRIMARY, "history")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )


def wallet_choice(saved: str) -> InlineKeyboardMarkup:
    """Куда платить: сохранённый адрес или новый."""
    short = f"{saved[:6]}…{saved[-4:]}" if len(saved) > 12 else saved
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(f"На сохранённый · {short}", "pr:saved", SUCCESS, "wallet")],
            [btn("Указать другой адрес", "pr:other", PRIMARY, "key")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )


def request_decision(request_id: int) -> InlineKeyboardMarkup:
    """Решение админа по заявке на выплату."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            btn("Принять", f"pr:ok:{request_id}", SUCCESS, "check"),
            btn("Отказать", f"pr:no:{request_id}", DANGER, "cross"),
        ]]
    )


def confirm_share(request_id: int) -> InlineKeyboardMarkup:
    """Последний шаг перед отправкой: сумма уже посчитана и показана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Отправить", f"pr:pay:{request_id}", SUCCESS, "withdraw")],
            [btn("Отмена", f"pr:cancel:{request_id}", DANGER, "cross")],
        ]
    )


def workers_list(rows: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Список воркеров кнопками — чтобы не вводить id руками."""
    keyboard = [
        [btn(name, f"wk:{worker_id}", PRIMARY, "profile")]
        for worker_id, name in rows
    ]
    keyboard.append([btn("В меню", "m:main", PRIMARY, "back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def worker_actions(worker_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Начислить", f"wk:credit:{worker_id}", SUCCESS, "coin")],
            [btn("Выплатить баланс", f"pay:{worker_id}", SUCCESS, "withdraw")],
            [btn("К списку", "a:workers", PRIMARY, "back")],
        ]
    )


def gifts_count_choice() -> InlineKeyboardMarkup:
    """Первый шаг заявки: один подарок или несколько."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Один подарок", "pr:one", SUCCESS, "gift")],
            [btn("Несколько", "pr:many", PRIMARY, "gift")],
            [btn("В меню", "m:main", PRIMARY, "back")],
        ]
    )
