"""Хендлеры работника: меню, профиль, кошелёк, топ, история, вывод."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Config
from ..db import Database
from ..emoji import e, esc
from ..keyboards import back_menu, confirm_withdraw, history_nav, main_menu
from ..states import WalletForm
from ..ui import reset_state, safe_edit
from ..utils import fmt_ton, is_valid_ton_address

logger = logging.getLogger(__name__)
router = Router()

PER_PAGE = 5
_STATUS = {"paid": "✅ выплачено", "processing": "⏳ в обработке", "failed": "❌ ошибка"}

# Страховка поверх БД-резерва: один активный вывод на пользователя в процессе.
# Словарь чистится после освобождения, иначе растёт на каждого пользователя навсегда.
_user_locks: dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def _withdraw_lock(user_id: int):
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    try:
        async with lock:
            yield
    finally:
        if not lock.locked() and not _user_locks.get(user_id, lock)._waiters:
            _user_locks.pop(user_id, None)


def _dt(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m.%Y %H:%M")


async def _show_main(target: Message | CallbackQuery, config: Config, name: str) -> None:
    text = (
        f"{e('fire')} <b>Панель выплат</b>\n\n"
        f"{e('user')} {esc(name)}\n\n"
        f"Выбери раздел ниже."
    )
    keyboard = main_menu(is_admin=_is_admin(target, config))
    if isinstance(target, CallbackQuery):
        await safe_edit(target, text, keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


def _is_admin(target: Message | CallbackQuery, config: Config) -> bool:
    return target.from_user.id in config.admin_ids


@router.message(CommandStart())
async def start(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    await db.upsert_worker(user.id, user.username, user.full_name)
    await _show_main(message, config, user.full_name)


@router.callback_query(F.data == "m:main")
async def back_to_main(call: CallbackQuery, config: Config, state: FSMContext) -> None:
    await state.clear()
    await _show_main(call, config, call.from_user.full_name)
    await call.answer()


@router.callback_query(F.data == "m:profile")
async def profile(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return
    paid_total, paid_count = await db.worker_totals(worker.user_id)
    wallet = f"<code>{esc(worker.wallet)}</code>" if worker.wallet else "не задан"
    await safe_edit(
        call,
        f"{e('user')} <b>Профиль</b>\n\n"
        f"{e('id')} ID: <code>{worker.user_id}</code>\n"
        f"{e('user')} Имя: {esc(worker.full_name)}\n"
        f"{e('wallet')} Кошелёк: {wallet}\n\n"
        f"{e('money')} Баланс: <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('check')} Выплачено всего: <b>{fmt_ton(paid_total)}</b>\n"
        f"{e('history')} Выплат: <b>{paid_count}</b>",
        reply_markup=back_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "m:balance")
async def balance(call: CallbackQuery, db: Database, config: Config, state: FSMContext) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return
    note = ""
    if worker.balance_nano < config.min_withdraw_nano:
        note = f"\n\n{e('warn')} Минимум для вывода: {fmt_ton(config.min_withdraw_nano)}"
    await safe_edit(
        call,
        f"{e('money')} <b>Баланс</b>\n\n"
        f"Доступно: <b>{fmt_ton(worker.balance_nano)}</b>{note}",
        reply_markup=back_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "m:top")
async def top(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    rows = await db.get_top(10)
    if not rows:
        body = "Пока никто не получал выплат."
    else:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        body = "\n".join(
            f"{medals.get(i, f'{i}.')} <b>{esc(name)}</b> — {fmt_ton(total)} ({cnt})"
            for i, (name, total, cnt) in enumerate(rows, 1)
        )
    await safe_edit(
        call,
        f"{e('trophy')} <b>Топ-10 по выплатам</b>\n\n{body}", reply_markup=back_menu()
    )
    await call.answer()


@router.callback_query(F.data == "m:history")
@router.callback_query(F.data.startswith("h:"))
async def history(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    if call.data == "h:noop":
        await call.answer()
        return
    page = int(call.data.split(":")[1]) if call.data.startswith("h:") else 1

    total = await db.count_withdrawals(call.from_user.id)
    total_pages = max(1, -(-total // PER_PAGE))
    page = min(max(1, page), total_pages)
    rows = await db.get_withdrawals(call.from_user.id, page, PER_PAGE)

    if not rows:
        body = "Заявок пока не было."
    else:
        body = "\n\n".join(
            f"<b>#{wid}</b> · {_dt(ts)}\n"
            f"{fmt_ton(amount)} · {_STATUS.get(status, status)}"
            + (f"\n<code>{esc(tx)}</code>" if tx else "")
            for wid, amount, status, tx, ts in rows
        )
    await safe_edit(
        call,
        f"{e('history')} <b>История заявок</b>\n\n{body}",
        reply_markup=history_nav(page, total_pages),
    )
    await call.answer()


@router.callback_query(F.data == "m:wallet")
async def wallet_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WalletForm.waiting_address)
    await safe_edit(
        call,
        f"{e('wallet')} <b>Кошелёк</b>\n\n"
        f"Пришли адрес TON-кошелька (UQ.../EQ...) — на него уходят выплаты.",
        reply_markup=back_menu(),
    )
    await call.answer()


@router.message(WalletForm.waiting_address, F.text)
async def wallet_save(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    address = message.text.strip()
    if not is_valid_ton_address(address):
        await message.answer(
            f"{e('cross')} <b>Адрес не прошёл проверку.</b>\n\n"
            f"Скопируй адрес целиком из кошелька — он должен начинаться с "
            f"<code>UQ</code> или <code>EQ</code> и быть длиной 48 символов.\n\n"
            f"Проверяется контрольная сумма, поэтому даже одна опечатка "
            f"не пройдёт — так выплата не уйдёт в никуда."
        )
        return
    await db.set_wallet(message.from_user.id, address)
    await state.clear()
    await message.answer(
        f"{e('check')} Кошелёк сохранён:\n<code>{esc(address)}</code>",
        reply_markup=main_menu(is_admin=message.from_user.id in config.admin_ids),
    )


@router.callback_query(F.data == "m:withdraw")
async def withdraw_request(call: CallbackQuery, db: Database, config: Config, state: FSMContext) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return
    if not worker.wallet:
        await call.answer("Сначала задай кошелёк", show_alert=True)
        return
    if worker.balance_nano < config.min_withdraw_nano:
        await call.answer(
            f"Минимум {fmt_ton(config.min_withdraw_nano)}, у тебя {fmt_ton(worker.balance_nano)}",
            show_alert=True,
        )
        return
    await safe_edit(
        call,
        f"{e('send')} <b>Подтверждение вывода</b>\n\n"
        f"{e('money')} Сумма: <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('wallet')} На адрес:\n<code>{esc(worker.wallet)}</code>",
        reply_markup=confirm_withdraw(fmt_ton(worker.balance_nano)),
    )
    await call.answer()


@router.callback_query(F.data == "wd:no")
async def withdraw_cancel(call: CallbackQuery, config: Config) -> None:
    await _show_main(call, config, call.from_user.full_name)
    await call.answer("Отменено")


@router.callback_query(F.data == "wd:yes")
async def withdraw_confirm(call: CallbackQuery, db: Database, config: Config, payer) -> None:
    user_id = call.from_user.id
    await call.answer()

    async with _withdraw_lock(user_id):
        reserved = await db.reserve_withdrawal(user_id, config.min_withdraw_nano)
        if reserved is None:
            await safe_edit(
                call,
                f"{e('cross')} Заявку создать не удалось: недостаточный баланс, "
                f"нет кошелька или предыдущий вывод ещё в обработке.",
                reply_markup=back_menu(),
            )
            return

        withdrawal_id, wallet, amount_nano = reserved
        await safe_edit(call, f"{e('time')} Отправляю {fmt_ton(amount_nano)}...")

        try:
            tx_hash = await payer.send(wallet, amount_nano)
        except Exception as exc:  # noqa: BLE001 — любая ошибка => возврат средств
            logger.exception("Выплата #%s провалилась", withdrawal_id)
            await db.mark_failed_and_refund(withdrawal_id, user_id, amount_nano, repr(exc))
            await safe_edit(
                call,
                f"{e('cross')} Отправить не удалось. Средства возвращены на баланс.",
                reply_markup=back_menu(),
            )
            for admin_id in config.admin_ids:
                try:
                    await call.bot.send_message(
                        admin_id,
                        f"{e('warn')} Выплата #{withdrawal_id} провалена "
                        f"({fmt_ton(amount_nano)}, user {user_id}): {esc(exc)}",
                    )
                except Exception:  # noqa: BLE001
                    pass
            return

        await db.mark_paid(withdrawal_id, tx_hash)
        demo = f"\n\n{e('warn')} DRY_RUN: реальные TON не отправлены" if config.dry_run else ""
        await safe_edit(
            call,
            f"{e('check')} <b>Отправлено {fmt_ton(amount_nano)}</b>\n\n"
            f"TX: <code>{esc(tx_hash)}</code>{demo}",
            reply_markup=back_menu(),
        )
