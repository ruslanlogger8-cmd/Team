"""Экраны работника: меню, профиль, баланс, кошелёк, топ, история, вывод."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Config
from ..db import Database
from ..emoji import e, esc
from ..gifts.claim import ClaimResult, submit_claim
from ..keyboards import (
    back_menu, claim_menu, confirm_withdraw, history_nav, main_menu, wallet_menu,
)
from ..payout import execute_payout
from ..states import ClaimForm, WalletForm
from ..ui import reset_state, safe_edit, send_screen
from ..utils import fmt_ton, is_valid_ton_address

logger = logging.getLogger(__name__)
router = Router()

PER_PAGE = 5
RULE = "━━━━━━━━━━━━━━━━━━━━"
MEDALS = ("gold", "silver", "bronze")
STATUS = {
    "paid": ("check", "выплачено"),
    "processing": ("time", "в обработке"),
    "failed": ("cross", "ошибка"),
}


def _dt(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d.%m.%Y в %H:%M")


def _menu_text(name: str, config: Config, balance_nano: int | None) -> str:
    head = (
        f"{e('logo')} <b>{esc(config.team_name)}</b>\n"
        f"{RULE}\n"
        f"{e('wave')} Привет, <b>{esc(name)}</b>\n"
    )
    if balance_nano is not None:
        head += f"{e('balance')} Баланс · <b>{fmt_ton(balance_nano)}</b>\n"
    mode = (
        f"{e('withdraw')} Выплаты приходят автоматически"
        if config.auto_payout
        else f"{e('withdraw')} Вывод — кнопкой «Вывести средства»"
    )
    return head + f"\n{mode}"


async def _open_menu(
    target: Message | CallbackQuery, config: Config, balance_nano: int | None = None
) -> None:
    text = _menu_text(target.from_user.full_name, config, balance_nano)
    keyboard = main_menu(is_admin=target.from_user.id in config.admin_ids)
    if isinstance(target, CallbackQuery):
        await safe_edit(target, text, keyboard)
    else:
        await send_screen(target, text, keyboard, photo=config.menu_photo or None)


@router.message(CommandStart())
async def start(message: Message, db: Database, config: Config, state: FSMContext) -> None:
    await reset_state(state)
    user = message.from_user
    await db.upsert_worker(user.id, user.username, user.full_name)
    worker = await db.get_worker(user.id)
    await _open_menu(message, config, worker.balance_nano if worker else None)


@router.callback_query(F.data == "m:main")
async def back_to_menu(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    await _open_menu(call, config, worker.balance_nano if worker else None)
    await call.answer()


@router.callback_query(F.data == "m:profile")
async def profile(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return

    paid_total, paid_count = await db.worker_totals(worker.user_id)
    wallet = f"<code>{esc(worker.wallet)}</code>" if worker.wallet else "<i>не указан</i>"
    await safe_edit(
        call,
        f"{e('profile')} <b>Профиль</b>\n"
        f"{RULE}\n"
        f"{e('id')} Имя · <b>{esc(worker.full_name)}</b>\n"
        f"{e('dot')} ID · <code>{worker.user_id}</code>\n"
        f"{e('wallet')} Кошелёк · {wallet}\n\n"
        f"{e('balance')} Баланс · <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('check')} Всего выплачено · <b>{fmt_ton(paid_total)}</b>\n"
        f"{e('history')} Выплат · <b>{paid_count}</b>",
        back_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "m:balance")
async def balance(call: CallbackQuery, db: Database, config: Config, state: FSMContext) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return

    if worker.balance_nano >= config.min_withdraw_nano:
        hint = f"{e('check')} Можно выводить"
    else:
        hint = (
            f"{e('warn')} Минимум для вывода · "
            f"<b>{fmt_ton(config.min_withdraw_nano)}</b>"
        )
    await safe_edit(
        call,
        f"{e('balance')} <b>Баланс</b>\n"
        f"{RULE}\n"
        f"{e('coin')} Доступно · <b>{fmt_ton(worker.balance_nano)}</b>\n\n"
        f"{hint}",
        back_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "m:top")
async def top(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    rows = await db.get_top(10)
    if not rows:
        body = f"{e('dot')} Выплат пока не было."
    else:
        lines = []
        for place, (name, total, count) in enumerate(rows, 1):
            mark = e(MEDALS[place - 1]) if place <= 3 else f"{e('dot')} {place}."
            lines.append(f"{mark} <b>{esc(name)}</b> · {fmt_ton(total)} · {count}")
        body = "\n".join(lines)
    await safe_edit(
        call,
        f"{e('top')} <b>Топ воркеров</b>\n"
        f"{RULE}\n{body}\n\n"
        f"{e('star')} Рейтинг по сумме выплат",
        back_menu(),
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
        body = f"{e('dot')} Заявок пока не было."
    else:
        blocks = []
        for wid, amount, status, tx_hash, created in rows:
            icon_key, label = STATUS.get(status, ("dot", status))
            block = (
                f"{e(icon_key)} <b>Заявка №{wid}</b> · {label}\n"
                f"{e('coin')} {fmt_ton(amount)}\n"
                f"{e('clock')} {_dt(created)}"
            )
            if tx_hash:
                block += f"\n<code>{esc(tx_hash)}</code>"
            blocks.append(block)
        body = f"\n\n{RULE}\n".join(blocks)

    await safe_edit(
        call,
        f"{e('history')} <b>История выплат</b>\n{RULE}\n{body}",
        history_nav(page, total_pages),
    )
    await call.answer()


@router.callback_query(F.data == "m:wallet")
async def wallet_screen(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    current = (
        f"<code>{esc(worker.wallet)}</code>" if worker and worker.wallet else "<i>не указан</i>"
    )
    await safe_edit(
        call,
        f"{e('wallet')} <b>Кошелёк</b>\n"
        f"{RULE}\n"
        f"{e('dot')} Текущий · {current}\n\n"
        f"{e('shield')} Адрес проверяется по контрольной сумме — "
        f"выплата не уйдёт по ошибочному адресу.",
        wallet_menu(has_wallet=bool(worker and worker.wallet)),
    )
    await call.answer()


@router.callback_query(F.data == "m:wallet_set")
async def wallet_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WalletForm.waiting_address)
    await safe_edit(
        call,
        f"{e('key')} <b>Новый адрес кошелька</b>\n"
        f"{RULE}\n"
        f"{e('dot')} Пришли адрес TON одним сообщением.\n"
        f"{e('dot')} Начинается с <code>UQ</code> или <code>EQ</code>, 48 символов.",
        back_menu(),
    )
    await call.answer()


@router.message(WalletForm.waiting_address, F.text)
async def wallet_save(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    address = message.text.strip()
    if not is_valid_ton_address(address):
        await message.answer(
            f"{e('cross')} <b>Адрес не прошёл проверку</b>\n"
            f"{RULE}\n"
            f"{e('dot')} Скопируй адрес целиком из кошелька.\n"
            f"{e('dot')} Проверяется контрольная сумма, поэтому даже одна "
            f"опечатка не пройдёт — так деньги не уйдут в никуда.\n\n"
            f"{e('warn')} Пришли корректный адрес или вернись в меню."
        )
        return

    await db.set_wallet(message.from_user.id, address)
    await state.clear()
    await send_screen(
        message,
        f"{e('check')} <b>Кошелёк сохранён</b>\n"
        f"{RULE}\n<code>{esc(address)}</code>",
        main_menu(is_admin=message.from_user.id in config.admin_ids),
        photo=config.menu_photo or None,
    )


@router.callback_query(F.data == "m:withdraw")
async def withdraw_request(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    await reset_state(state)
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return
    if not worker.wallet:
        await call.answer("Сначала укажи кошелёк", show_alert=True)
        return
    if worker.balance_nano < config.min_withdraw_nano:
        await call.answer(
            f"Минимум {fmt_ton(config.min_withdraw_nano)}, "
            f"у тебя {fmt_ton(worker.balance_nano)}",
            show_alert=True,
        )
        return

    await safe_edit(
        call,
        f"{e('withdraw')} <b>Подтверждение вывода</b>\n"
        f"{RULE}\n"
        f"{e('coin')} Сумма · <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('wallet')} Адрес получателя\n<code>{esc(worker.wallet)}</code>\n\n"
        f"{e('warn')} Отправляется весь баланс. Операция необратима.",
        confirm_withdraw(fmt_ton(worker.balance_nano)),
    )
    await call.answer()


@router.callback_query(F.data == "wd:no")
async def withdraw_cancel(call: CallbackQuery, db: Database, config: Config) -> None:
    worker = await db.get_worker(call.from_user.id)
    await _open_menu(call, config, worker.balance_nano if worker else None)
    await call.answer("Отменено")


@router.callback_query(F.data == "wd:yes")
async def withdraw_confirm(call: CallbackQuery, db: Database, config: Config, payer) -> None:
    await call.answer()
    await safe_edit(call, f"{e('time')} <b>Отправляю перевод…</b>")

    result = await execute_payout(db, payer, call.from_user.id, config.min_withdraw_nano)

    if result.status == "skipped":
        await safe_edit(
            call,
            f"{e('cross')} <b>Выводить нечего</b>\n"
            f"{RULE}\n"
            f"{e('dot')} Баланс ниже минимума, не указан кошелёк "
            f"или прошлая заявка ещё в обработке.",
            back_menu(),
        )
        return

    if result.status == "failed":
        await safe_edit(
            call,
            f"{e('cross')} <b>Перевод не прошёл</b>\n"
            f"{RULE}\n"
            f"{e('check')} Средства возвращены на баланс.\n"
            f"{e('dot')} Попробуй позже — администратор уведомлён.",
            back_menu(),
        )
        await _alert_admins(call.bot, config, result, call.from_user.id)
        return

    demo = f"\n\n{e('warn')} Режим DRY_RUN · реальные TON не отправлялись" if config.dry_run else ""
    await safe_edit(
        call,
        f"{e('check')} <b>Выплата отправлена</b>\n"
        f"{RULE}\n"
        f"{e('coin')} Сумма · <b>{fmt_ton(result.amount_nano)}</b>\n"
        f"{e('link')} Транзакция\n<code>{esc(result.tx_hash)}</code>{demo}",
        back_menu(),
    )


async def _alert_admins(bot, config: Config, result, user_id: int) -> None:
    text = (
        f"{e('warn')} <b>Выплата не прошла</b>\n"
        f"{RULE}\n"
        f"{e('dot')} Заявка №{result.withdrawal_id}\n"
        f"{e('coin')} {fmt_ton(result.amount_nano)}\n"
        f"{e('profile')} Работник <code>{user_id}</code>\n"
        f"{e('cross')} {esc(result.error)}"
    )
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 — админ мог не запускать бота
            logger.warning("Не удалось уведомить админа %s", admin_id)


# ─── Заявка на подарок ─────────────────────────────────────────────────

GIFT_STATUS = {
    "received": ("time", "принят, ждёт отправки на маркет"),
    "deposited": ("next", "отправлен на маркет"),
    "listed": ("up", "выставлен на продажу"),
    "sold": ("check", "продан, доля начисляется"),
    "paid": ("check", "продан, доля выплачена"),
    "skipped": ("cross", "пропущен"),
}


@router.callback_query(F.data == "m:claim")
async def claim_screen(call: CallbackQuery, config: Config, state: FSMContext) -> None:
    await reset_state(state)
    if not config.gifts_enabled:
        await call.answer("Приём подарков сейчас выключен", show_alert=True)
        return
    await safe_edit(
        call,
        f"{e('gift')} <b>Заявка на подарок</b>\n"
        f"{RULE}\n"
        f"{e('dot')} Отправил подарок и хочешь закрепить его за собой — "
        f"пришли ссылку на него.\n\n"
        f"{e('shield')} Заявка проходит, только если подарок реально дошёл. "
        f"Чужой закрепить нельзя.\n\n"
        f"{e('star')} Доля · <b>{config.worker_share_percent}%</b> от суммы продажи",
        claim_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "m:claim_send")
async def claim_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ClaimForm.waiting_link)
    await safe_edit(
        call,
        f"{e('link')} <b>Ссылка на подарок</b>\n"
        f"{RULE}\n"
        f"{e('dot')} Открой подарок в Telegram, нажми «Поделиться» "
        f"и пришли ссылку сюда.\n\n"
        f"{e('dot')} Вид ссылки · <code>t.me/nft/PlushPepe-42</code>",
        back_menu(),
    )
    await call.answer()


@router.message(ClaimForm.waiting_link, F.text)
async def claim_submit(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    claim = await submit_claim(db, message.from_user.id, message.text)

    if claim.result is ClaimResult.BAD_LINK:
        await message.answer(
            f"{e('cross')} <b>Ссылку не разобрал</b>\n"
            f"{RULE}\n"
            f"{e('dot')} Нужна ссылка вида <code>t.me/nft/PlushPepe-42</code>\n"
            f"{e('dot')} Открой подарок → «Поделиться» → скопируй ссылку"
        )
        return

    await state.clear()
    keyboard = main_menu(is_admin=message.from_user.id in config.admin_ids)

    if claim.result is ClaimResult.NOT_FOUND:
        await message.answer(
            f"{e('cross')} <b>Такой подарок не поступал</b>\n"
            f"{RULE}\n"
            f"{e('dot')} <code>{esc(claim.slug)}</code>\n\n"
            f"{e('time')} Если только что отправил — подожди минуту и повтори.\n"
            f"{e('warn')} Проверь, что отправлял на нужный аккаунт.",
            reply_markup=keyboard,
        )
        return

    if claim.result is ClaimResult.TAKEN:
        await message.answer(
            f"{e('warn')} <b>Подарок уже закреплён</b>\n"
            f"{RULE}\n"
            f"{e('dot')} {esc(claim.title)}\n\n"
            f"{e('shield')} Он числится за другим воркером. "
            f"Если это ошибка — напиши администратору.",
            reply_markup=keyboard,
        )
        return

    icon_key, label = GIFT_STATUS.get(claim.status, ("dot", claim.status))
    head = (
        "Заявка принята" if claim.result is ClaimResult.ATTACHED
        else "Этот подарок уже за тобой"
    )
    await message.answer(
        f"{e('check')} <b>{head}</b>\n"
        f"{RULE}\n"
        f"{e('gift')} {esc(claim.title)}\n"
        f"{e(icon_key)} Статус · {label}\n\n"
        f"{e('star')} После продажи получишь <b>{config.worker_share_percent}%</b> "
        f"на баланс, выплата уйдёт автоматически.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "m:my_gifts")
async def my_gifts(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await reset_state(state)
    rows = await db.gifts_by_worker(call.from_user.id)

    if not rows:
        body = f"{e('dot')} Пока ни одного подарка за тобой не числится."
    else:
        blocks = []
        for row in rows:
            icon_key, label = GIFT_STATUS.get(row["status"], ("dot", row["status"]))
            block = f"{e(icon_key)} <b>{esc(row['title'] or row['slug'])}</b>\n{label}"
            if row["share_nano"]:
                block += f"\n{e('coin')} Доля · <b>{fmt_ton(row['share_nano'])}</b>"
            elif row["list_price_nano"]:
                block += f"\n{e('coin')} Цена · {fmt_ton(row['list_price_nano'])}"
            blocks.append(block)
        body = "\n\n".join(blocks)

    await safe_edit(call, f"{e('gift')} <b>Мои подарки</b>\n{RULE}\n{body}", claim_menu())
    await call.answer()
