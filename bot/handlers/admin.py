"""Хендлеры админа: начисление, статистика, разбор зависших заявок."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..config import Config
from ..db import Database
from ..emoji import e, esc
from ..keyboards import admin_menu, back_menu
from ..ui import safe_edit
from ..utils import fmt_ton, parse_ton

router = Router()


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(Command("credit"))
async def credit(message: Message, db: Database, config: Config) -> None:
    """/credit <user_id> <amount_ton> [комментарий] — начислить баланс."""
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            f"{e('warn')} Формат: <code>/credit &lt;user_id&gt; &lt;сумма&gt; [коммент]</code>\n"
            f"Пример: <code>/credit 7712345678 1.5 за неделю</code>"
        )
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer(f"{e('cross')} user_id должен быть числом, а не {esc(parts[1])!r}")
        return

    try:
        amount_nano = parse_ton(parts[2])
    except ValueError as exc:
        await message.answer(f"{e('cross')} Сумма: {esc(exc)}")
        return

    if amount_nano == 0:
        await message.answer(f"{e('cross')} Сумма не может быть нулевой.")
        return

    comment = parts[3] if len(parts) > 3 else ""
    try:
        new_balance = await db.credit(target_id, amount_nano, message.from_user.id, comment)
    except ValueError as exc:
        reason = {
            "worker_not_found": "работник не найден — пусть сначала нажмёт /start",
            "negative_balance": "баланс ушёл бы в минус",
        }.get(str(exc), str(exc))
        await message.answer(f"{e('cross')} {reason}")
        return

    sign = "+" if amount_nano > 0 else ""
    await message.answer(
        f"{e('check')} {sign}{fmt_ton(amount_nano)} работнику <code>{target_id}</code>\n"
        f"{e('money')} Новый баланс: <b>{fmt_ton(new_balance)}</b>"
    )
    try:
        await message.bot.send_message(
            target_id,
            f"{e('money')} Начислено <b>{sign}{fmt_ton(amount_nano)}</b>\n"
            f"Баланс: <b>{fmt_ton(new_balance)}</b>",
        )
    except Exception:  # noqa: BLE001 — работник мог заблокировать бота
        await message.answer(f"{e('warn')} Начислено, но уведомить работника не удалось.")


@router.message(Command("resolve"))
async def resolve(message: Message, db: Database, config: Config) -> None:
    """/resolve <id> sent|refund — закрыть заявку, зависшую после падения."""
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or parts[2].split()[0] not in ("sent", "refund"):
        await message.answer(
            f"{e('warn')} Формат: <code>/resolve &lt;id&gt; sent|refund</code>\n\n"
            f"<b>sent</b> — TON реально ушли (проверь адрес в блокчейне)\n"
            f"<b>refund</b> — не ушли, вернуть на баланс работнику"
        )
        return

    try:
        withdrawal_id = int(parts[1])
    except ValueError:
        await message.answer(f"{e('cross')} id должен быть числом")
        return

    action = parts[2].split()[0]
    try:
        await db.resolve_stuck(withdrawal_id, sent=action == "sent")
    except ValueError:
        await message.answer(f"{e('cross')} Заявка #{withdrawal_id} не найдена или уже закрыта.")
        return

    outcome = "помечена как выплаченная" if action == "sent" else "возвращена на баланс"
    await message.answer(f"{e('check')} Заявка #{withdrawal_id} {outcome}.")


@router.message(Command("stats"))
async def stats_command(message: Message, db: Database, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await message.answer(await _stats_text(db, config))


@router.message(Command("id"))
async def my_id(message: Message) -> None:
    await message.answer(f"{e('id')} Твой ID: <code>{message.from_user.id}</code>")


@router.message(Command("help"))
async def help_command(message: Message, config: Config) -> None:
    text = (
        f"{e('fire')} <b>Как пользоваться</b>\n\n"
        f"1. {e('wallet')} Задай TON-кошелёк в разделе «Кошелёк»\n"
        f"2. {e('money')} Дождись начисления баланса\n"
        f"3. {e('send')} Нажми «Вывести» — TON придут на твой кошелёк\n\n"
        f"Адрес проверяется по контрольной сумме, опечатка не пройдёт."
    )
    if message.from_user.id in config.admin_ids:
        text += (
            f"\n\n{e('gear')} <b>Админ</b>\n"
            f"<code>/credit &lt;id&gt; &lt;сумма&gt; [коммент]</code>\n"
            f"<code>/resolve &lt;id&gt; sent|refund</code>\n"
            f"<code>/stats</code>"
        )
    await message.answer(text)


async def _stats_text(db: Database, config: Config) -> str:
    data = await db.stats()
    stuck = await db.find_stuck_withdrawals()
    text = (
        f"{e('chart')} <b>Статистика</b>\n\n"
        f"{e('user')} Работников: <b>{data['workers']}</b>\n"
        f"{e('money')} Баланс к выплате: <b>{fmt_ton(data['total_balance_nano'])}</b>\n"
        f"{e('check')} Выплат проведено: <b>{data['paid_count']}</b>\n"
        f"{e('send')} Выплачено всего: <b>{fmt_ton(data['paid_total_nano'])}</b>"
    )
    if stuck:
        text += f"\n\n{e('warn')} Зависших заявок: <b>{len(stuck)}</b> — разбери через /resolve"
    if config.dry_run:
        text += f"\n\n{e('warn')} DRY_RUN — реальные выплаты не отправляются"
    return text


@router.callback_query(F.data == "m:admin")
async def admin_panel(call: CallbackQuery, config: Config) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return
    await safe_edit(
        call,
        f"{e('gear')} <b>Админка</b>\n\n"
        f"<code>/credit &lt;id&gt; &lt;сумма&gt; [коммент]</code> — начислить\n"
        f"<code>/resolve &lt;id&gt; sent|refund</code> — разобрать зависшую заявку\n"
        f"<code>/stats</code> — сводка",
        admin_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "a:stats")
async def admin_stats(call: CallbackQuery, db: Database, config: Config) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return
    await safe_edit(call, await _stats_text(db, config), back_menu())
    await call.answer()
