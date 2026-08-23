"""Хендлеры работника: регистрация, баланс, кошелёк, вывод."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Config
from ..db import Database
from ..keyboards import confirm_withdraw, worker_menu
from ..states import WalletForm
from ..utils import fmt_ton, is_valid_ton_address

logger = logging.getLogger(__name__)
router = Router()

# По одному активному выводу на пользователя в рамках процесса (страховка поверх БД-резерва).
_user_locks: dict[int, asyncio.Lock] = {}


def _lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


@router.message(CommandStart())
async def start(message: Message, db: Database) -> None:
    user = message.from_user
    await db.upsert_worker(user.id, user.username, user.full_name)
    await message.answer(
        "Бот выплат. Задай TON-кошелёк через «💼 Кошелёк», "
        "затем выводи баланс кнопкой «💸 Вывести».",
        reply_markup=worker_menu(),
    )


@router.message(F.text == "💰 Баланс")
async def balance(message: Message, db: Database) -> None:
    worker = await db.get_worker(message.from_user.id)
    if worker is None:
        await message.answer("Нажми /start")
        return
    wallet = worker.wallet or "не задан"
    await message.answer(f"Баланс: {fmt_ton(worker.balance_nano)}\nКошелёк: <code>{wallet}</code>")


@router.message(F.text == "💼 Кошелёк")
async def wallet_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(WalletForm.waiting_address)
    await message.answer("Пришли адрес TON-кошелька (UQ.../EQ...). Он для приёма выплат.")


@router.message(WalletForm.waiting_address, F.text)
async def wallet_save(message: Message, state: FSMContext, db: Database) -> None:
    address = message.text.strip()
    if not is_valid_ton_address(address):
        await message.answer("Не похоже на TON-адрес. Пришли корректный UQ.../EQ... или отмени /start")
        return
    await db.set_wallet(message.from_user.id, address)
    await state.clear()
    await message.answer(f"Кошелёк сохранён:\n<code>{address}</code>", reply_markup=worker_menu())


@router.message(F.text == "💸 Вывести")
async def withdraw_request(message: Message, db: Database, config: Config) -> None:
    worker = await db.get_worker(message.from_user.id)
    if worker is None:
        await message.answer("Нажми /start")
        return
    if not worker.wallet:
        await message.answer("Сначала задай кошелёк через «💼 Кошелёк».")
        return
    if worker.balance_nano < config.min_withdraw_nano:
        await message.answer(
            f"Минимум для вывода: {fmt_ton(config.min_withdraw_nano)}. "
            f"Твой баланс: {fmt_ton(worker.balance_nano)}."
        )
        return
    await message.answer(
        f"Вывести {fmt_ton(worker.balance_nano)} на\n<code>{worker.wallet}</code>?",
        reply_markup=confirm_withdraw(fmt_ton(worker.balance_nano)),
    )


@router.callback_query(F.data == "wd:no")
async def withdraw_cancel(call: CallbackQuery) -> None:
    await call.message.edit_text("Отменено.")
    await call.answer()


@router.callback_query(F.data == "wd:yes")
async def withdraw_confirm(call: CallbackQuery, db: Database, config: Config, payer) -> None:
    user_id = call.from_user.id
    await call.answer()

    async with _lock(user_id):
        reserved = await db.reserve_withdrawal(user_id, config.min_withdraw_nano)
        if reserved is None:
            await call.message.edit_text(
                "Не удалось создать заявку: недостаточный баланс, нет кошелька "
                "или предыдущий вывод ещё в обработке."
            )
            return

        withdrawal_id, wallet, amount_nano = reserved
        await call.message.edit_text(f"⏳ Отправляю {fmt_ton(amount_nano)}...")

        try:
            tx_hash = await payer.send(wallet, amount_nano)
        except Exception as exc:  # noqa: BLE001 — любая ошибка => возврат средств
            logger.exception("Выплата #%s провалилась", withdrawal_id)
            await db.mark_failed_and_refund(withdrawal_id, user_id, amount_nano, repr(exc))
            await call.message.edit_text(
                "❌ Не удалось отправить. Средства возвращены на баланс, попробуй позже."
            )
            for admin_id in config.admin_ids:
                try:
                    await call.bot.send_message(
                        admin_id,
                        f"⚠️ Выплата #{withdrawal_id} провалена ({fmt_ton(amount_nano)}, "
                        f"user {user_id}): {exc!r}",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return

        await db.mark_paid(withdrawal_id, tx_hash)
        await call.message.edit_text(
            f"✅ Отправлено {fmt_ton(amount_nano)}\nTX: <code>{tx_hash}</code>"
        )
