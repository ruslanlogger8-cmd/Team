"""Хендлеры админа: начисление баланса, статистика."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from aiogram.types import CallbackQuery

from ..config import Config
from ..db import Database
from ..emoji import e, esc
from ..keyboards import admin_menu, back_menu
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


@router.callback_query(F.data == "m:admin")
async def admin_panel(call: CallbackQuery, config: Config) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.edit_text(
        f"{e('gear')} <b>Админка</b>\n\n"
        f"<code>/credit &lt;id&gt; &lt;TON&gt; [коммент]</code> — начислить\n"
        f"<code>/stats</code> — сводка",
        reply_markup=admin_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "a:stats")
async def admin_stats(call: CallbackQuery, db: Database, config: Config) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return
    data = await db.stats()
    mode = f"\n\n{e('warn')} DRY_RUN — выплаты не отправляются" if config.dry_run else ""
    await call.message.edit_text(
        f"{e('chart')} <b>Статистика</b>\n\n"
        f"{e('user')} Работников: <b>{data['workers']}</b>\n"
        f"{e('money')} Баланс к выплате: <b>{fmt_ton(data['total_balance_nano'])}</b>\n"
        f"{e('check')} Выплат проведено: <b>{data['paid_count']}</b>\n"
        f"{e('send')} Выплачено всего: <b>{fmt_ton(data['paid_total_nano'])}</b>{mode}",
        reply_markup=back_menu(),
    )
    await call.answer()
