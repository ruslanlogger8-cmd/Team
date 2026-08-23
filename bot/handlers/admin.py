"""Хендлеры админа: начисление баланса, статистика."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from ..config import Config
from ..db import Database
from ..utils import fmt_ton, ton_to_nano

router = Router()


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(Command("credit"))
async def credit(message: Message, db: Database, config: Config) -> None:
    """/credit <user_id> <amount_ton> [комментарий] — начислить работнику баланс."""
    if not _is_admin(message.from_user.id, config):
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Формат: /credit <user_id> <amount_ton> [коммент]")
        return
    try:
        target_id = int(parts[1])
        amount_nano = ton_to_nano(parts[2])
    except (ValueError, ArithmeticError):
        await message.answer("user_id и amount_ton должны быть числами. Пример: /credit 123456 1.5")
        return
    if amount_nano == 0:
        await message.answer("Сумма не может быть 0.")
        return
    comment = parts[3] if len(parts) > 3 else ""
    try:
        new_balance = await db.credit(target_id, amount_nano, message.from_user.id, comment)
    except ValueError as exc:
        reason = "работник не найден (пусть нажмёт /start)" if str(exc) == "worker_not_found" else "баланс ушёл бы в минус"
        await message.answer(f"Ошибка: {reason}.")
        return
    await message.answer(
        f"Начислено {fmt_ton(amount_nano)} работнику {target_id}. Новый баланс: {fmt_ton(new_balance)}."
    )
    try:
        await message.bot.send_message(target_id, f"💰 Начислено {fmt_ton(amount_nano)}. Баланс: {fmt_ton(new_balance)}.")
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("stats"))
async def stats(message: Message, db: Database, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    data = await db.stats()
    await message.answer(
        "📊 Статистика\n"
        f"Работников: {data['workers']}\n"
        f"Суммарный баланс к выплате: {fmt_ton(data['total_balance_nano'])}\n"
        f"Выплат проведено: {data['paid_count']}\n"
        f"Выплачено всего: {fmt_ton(data['paid_total_nano'])}"
    )


@router.message(Command("id"))
async def my_id(message: Message) -> None:
    await message.answer(f"Твой id: <code>{message.from_user.id}</code>")
