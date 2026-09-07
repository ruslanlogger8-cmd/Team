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
from ..gifts.claim import ClaimResult, parse_nft_slug, parse_username, submit_claim
from ..keyboards import (
    back_menu, claim_menu, confirm_withdraw, gifts_count_choice, history_nav,
    main_menu, payout_request_menu, request_decision, wallet_choice,
    wallet_menu, withdraw_choice, withdrawal_actions,
)
from ..payout import execute_payout, request_payout
from ..states import ClaimForm, PayoutRequestForm, WalletForm, WithdrawForm
from ..ui import reset_state, safe_edit, send_screen
from ..utils import fmt_ton, is_valid_ton_address, parse_ton, plural

logger = logging.getLogger(__name__)
router = Router()

PER_PAGE = 5
# Потолок на количество подарков в одной заявке: защита от опечатки вроде
# «100» и от бессмысленно раздутых заявок.
MAX_GIFTS_PER_REQUEST = 50
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
            times = plural(count, "выплата", "выплаты", "выплат")
            lines.append(
                f"<b>{place}.</b> {esc(name)} — <b>{fmt_ton(total)}</b> "
                f"· {count} {times}"
            )
        # Цитата отделяет список от заголовка сама, без разделителей.
        body = "<blockquote>" + "\n".join(lines) + "</blockquote>"
    await safe_edit(
        call,
        f"{e('top')} <b>Топ воркеров</b>\n\n"
        f"{body}\n\n"
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
        body = "\n\n".join(blocks)

    await safe_edit(
        call,
        f"{e('history')} <b>История выплат</b>\n\n{body}",
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
        f"<code>{esc(address)}</code>",
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
        f"{e('withdraw')} <b>Вывод средств</b>\n"
        f"{e('balance')} Доступно · <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('wallet')} На адрес\n<code>{esc(worker.wallet)}</code>\n\n"
        f"{e('dot')} Минимум · {fmt_ton(config.min_withdraw_nano)}",
        withdraw_choice(fmt_ton(worker.balance_nano)),
    )
    await call.answer()


@router.callback_query(F.data == "wd:part")
async def withdraw_amount_prompt(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return

    await state.set_state(WithdrawForm.waiting_amount)
    await safe_edit(
        call,
        f"{e('coin')} <b>Сколько вывести</b>\n"
        f"{e('balance')} Доступно · <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('dot')} Минимум · {fmt_ton(config.min_withdraw_nano)}\n\n"
        f"{e('dot')} Пришли сумму числом · <code>1.5</code>\n"
        f"{e('dot')} Остаток сохранится на балансе",
        back_menu(),
    )
    await call.answer()


@router.message(WithdrawForm.waiting_amount, F.text)
async def withdraw_amount_entered(
    message: Message, state: FSMContext, db: Database, config: Config
) -> None:
    worker = await db.get_worker(message.from_user.id)
    if worker is None:
        await state.clear()
        await message.answer("Нажми /start")
        return

    try:
        amount = parse_ton(message.text)
    except ValueError:
        await message.answer(
            f"{e('cross')} <b>Это не сумма</b>\n"
            f"{e('dot')} Пришли число · <code>1.5</code> или <code>0,3</code>"
        )
        return

    if amount < config.min_withdraw_nano:
        await message.answer(
            f"{e('cross')} Минимум для вывода · <b>{fmt_ton(config.min_withdraw_nano)}</b>"
        )
        return
    if amount > worker.balance_nano:
        await message.answer(
            f"{e('cross')} <b>Столько нет на балансе</b>\n"
            f"{e('balance')} Доступно · <b>{fmt_ton(worker.balance_nano)}</b>"
        )
        return

    # Флаг делает подтверждение одноразовым: второй тап по кнопке не найдёт
    # его и не спишет сумму повторно.
    await state.set_state(None)
    await state.update_data(pending_withdraw=True, amount_nano=amount)
    rest = worker.balance_nano - amount
    await message.answer(
        f"{e('withdraw')} <b>Подтверждение вывода</b>\n"
        f"{e('coin')} К выводу · <b>{fmt_ton(amount)}</b>\n"
        f"{e('balance')} Останется · {fmt_ton(rest)}\n"
        f"{e('wallet')} На адрес\n<code>{esc(worker.wallet)}</code>",
        reply_markup=confirm_withdraw(fmt_ton(amount)),
    )


@router.callback_query(F.data == "wd:all")
async def withdraw_all(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    worker = await db.get_worker(call.from_user.id)
    if worker is None:
        await call.answer("Нажми /start", show_alert=True)
        return
    await state.update_data(pending_withdraw=True, amount_nano=None)
    await safe_edit(
        call,
        f"{e('withdraw')} <b>Подтверждение вывода</b>\n"
        f"{e('coin')} К выводу · <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('wallet')} На адрес\n<code>{esc(worker.wallet)}</code>\n\n"
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
async def withdraw_confirm(
    call: CallbackQuery, db: Database, config: Config, payer, state: FSMContext
) -> None:
    await call.answer()

    # Подтверждение одноразовое: без взведённого флага это повторный тап
    # по той же кнопке, и списывать сумму второй раз нельзя.
    data = await state.get_data()
    if not data.get("pending_withdraw"):
        await safe_edit(
            call,
            f"{e('warn')} <b>Заявка уже обработана</b>\n"
            f"{e('dot')} Загляни в «Историю», чтобы увидеть её статус.",
            back_menu(),
        )
        return

    amount_nano = data.get("amount_nano")
    await state.clear()

    if config.withdraw_needs_approval:
        await _hold_for_admin(call, db, config, amount_nano)
        return

    await safe_edit(call, f"{e('time')} <b>Отправляю перевод…</b>")

    result = await execute_payout(
        db, payer, call.from_user.id, config.min_withdraw_nano, amount_nano,
        max_single_nano=config.max_payout_nano,
        max_daily_nano=config.max_daily_payout_nano,
    )

    if result.status == "skipped":
        await safe_edit(
            call,
            f"{e('cross')} <b>Выводить нечего</b>\n"
            f"{e('dot')} Баланс ниже минимума, не указан кошелёк "
            f"или прошлая заявка ещё в обработке.",
            back_menu(),
        )
        return

    if result.status == "blocked":
        await safe_edit(
            call,
            f"{e('shield')} <b>Выплата остановлена лимитом</b>\n"
            f"{e('dot')} {esc(result.error)}\n\n"
            f"{e('check')} Средства остались на балансе.\n"
            f"{e('dot')} Напиши администратору.",
            back_menu(),
        )
        await _alert_admins(call.bot, config, result, call.from_user.id)
        return

    if result.status == "failed":
        await safe_edit(
            call,
            f"{e('cross')} <b>Перевод не прошёл</b>\n"
            f"{e('check')} Средства возвращены на баланс.\n"
            f"{e('dot')} Попробуй позже — администратор уведомлён.",
            back_menu(),
        )
        await _alert_admins(call.bot, config, result, call.from_user.id)
        return

    demo = f"\n\n{e('warn')} Режим DRY_RUN · реальные TON не отправлялись" if config.dry_run else ""
    worker = await db.get_worker(call.from_user.id)
    rest = (
        f"\n{e('balance')} Остаток · {fmt_ton(worker.balance_nano)}"
        if worker and worker.balance_nano
        else ""
    )
    await safe_edit(
        call,
        f"{e('check')} <b>Выплата отправлена</b>\n"
        f"{e('coin')} Сумма · <b>{fmt_ton(result.amount_nano)}</b>{rest}\n"
        f"{e('link')} Транзакция\n<code>{esc(result.tx_hash)}</code>{demo}",
        back_menu(),
    )


async def _hold_for_admin(
    call: CallbackQuery, db: Database, config: Config, amount_nano: int | None
) -> None:
    """Режим ручной выплаты: заявка ждёт кнопки админа.

    Деньги списываются с баланса уже сейчас — иначе воркер выведет их вторым
    путём, пока заявка висит.
    """
    result = await request_payout(
        db, call.from_user.id, config.min_withdraw_nano, amount_nano
    )
    if result.status == "skipped":
        await safe_edit(
            call,
            f"{e('cross')} <b>Заявку не создать</b>\n"
            f"{e('dot')} Баланс ниже минимума, не указан кошелёк "
            f"или прошлая заявка ещё не закрыта.",
            back_menu(),
        )
        return

    await safe_edit(
        call,
        f"{e('time')} <b>Заявка отправлена</b>\n"
        f"{e('coin')} Сумма · <b>{fmt_ton(result.amount_nano)}</b>\n"
        f"{e('dot')} Заявка №{result.withdrawal_id}\n\n"
        f"{e('shield')} Выплату подтверждает администратор. "
        f"Как отправит — придёт хеш транзакции.",
        back_menu(),
    )

    worker = await db.get_worker(call.from_user.id)
    who = f"@{worker.username}" if worker and worker.username else str(call.from_user.id)
    for admin_id in config.admin_ids:
        try:
            await call.bot.send_message(
                admin_id,
                f"{e('withdraw')} <b>Заявка на вывод</b>\n"
                f"{e('dot')} Заявка №{result.withdrawal_id}\n"
                f"{e('profile')} {esc(who)} · <code>{call.from_user.id}</code>\n"
                f"{e('coin')} Сумма · <b>{fmt_ton(result.amount_nano)}</b>\n"
                f"{e('wallet')} <code>{esc(worker.wallet if worker else '')}</code>",
                reply_markup=withdrawal_actions(result.withdrawal_id),
            )
        except Exception:  # noqa: BLE001 — админ мог не запускать бота
            logger.warning("Не удалось показать админу %s заявку", admin_id)


async def _alert_admins(bot, config: Config, result, user_id: int) -> None:
    head = (
        "Выплата остановлена лимитом" if result.status == "blocked"
        else "Выплата не прошла"
    )
    text = (
        f"{e('warn')} <b>{head}</b>\n"
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
        f"{e('dot')} Отправил подарок и хочешь закрепить его за собой — "
        f"пришли ссылку на него.\n\n"
        f"{e('shield')} Нужны три вещи · ссылка, юзернейм отправителя, "
        f"скриншот передачи.\n"
        f"{e('dot')} Заявка проходит, только если подарок реально дошёл. "
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
        f"{e('dot')} Шаг 1 из 3 · ссылка на подарок\n"
        f"{e('dot')} Открой подарок в Telegram, нажми «Поделиться» "
        f"и пришли ссылку сюда.\n\n"
        f"{e('dot')} Вид ссылки · <code>t.me/nft/PlushPepe-42</code>\n\n"
        f"{e('shield')} Дальше спрошу юзернейм отправителя и скриншот передачи.",
        back_menu(),
    )
    await call.answer()


@router.message(ClaimForm.waiting_link, F.text)
async def claim_link(message: Message, state: FSMContext, db: Database) -> None:
    slug = parse_nft_slug(message.text)
    if slug is None:
        await message.answer(
            f"{e('cross')} <b>Ссылку не разобрал</b>\n"
            f"{e('dot')} Нужна ссылка вида <code>t.me/nft/PlushPepe-42</code>\n"
            f"{e('dot')} Открой подарок → «Поделиться» → скопируй ссылку"
        )
        return

    # Проверяем существование сразу: незачем гонять человека через три шага,
    # если подарка нет или он уже за кем-то.
    gift = await db.get_gift(slug)
    if gift is None:
        await state.clear()
        await message.answer(
            f"{e('cross')} <b>Такой подарок не поступал</b>\n"
            f"{e('dot')} <code>{esc(slug)}</code>\n\n"
            f"{e('time')} Если только что отправил — подожди минуту и повтори.\n"
            f"{e('warn')} Проверь, что отправлял на нужный аккаунт.",
            reply_markup=back_menu(),
        )
        return

    owner = gift["worker_id"]
    if owner == message.from_user.id:
        await state.clear()
        await message.answer(
            f"{e('check')} <b>Этот подарок уже за тобой</b>\n"
            f"{e('gift')} {esc(gift['title'] or slug)}",
            reply_markup=back_menu(),
        )
        return
    if owner is not None:
        await state.clear()
        await message.answer(
            f"{e('warn')} <b>Подарок уже закреплён</b>\n"
            f"{e('gift')} {esc(gift['title'] or slug)}\n\n"
            f"{e('shield')} Он числится за другим воркером. "
            f"Если это ошибка — напиши администратору.",
            reply_markup=back_menu(),
        )
        return

    await state.update_data(slug=slug, title=gift["title"] or slug)
    await state.set_state(ClaimForm.waiting_username)
    await message.answer(
        f"{e('profile')} <b>С какого аккаунта передавал</b>\n"
        f"{e('gift')} {esc(gift['title'] or slug)}\n\n"
        f"{e('dot')} Пришли юзернейм аккаунта, с которого ушёл подарок.\n"
        f"{e('dot')} Вид · <code>@username</code>\n\n"
        f"{e('shield')} Он нужен, чтобы администратор сверил передачу."
    )


@router.message(ClaimForm.waiting_username, F.text)
async def claim_username(message: Message, state: FSMContext) -> None:
    username = parse_username(message.text)
    if username is None:
        await message.answer(
            f"{e('cross')} <b>Это не юзернейм</b>\n"
            f"{e('dot')} Пришли в виде <code>@username</code>\n"
            f"{e('dot')} От 5 до 32 символов, латиница, цифры и подчёркивания"
        )
        return

    await state.update_data(sender_username=username)
    await state.set_state(ClaimForm.waiting_photo)
    await message.answer(
        f"{e('link')} <b>Скриншот передачи</b>\n"
        f"{e('profile')} Аккаунт · {esc(username)}\n\n"
        f"{e('dot')} Пришли скриншот, где видно передачу подарка менеджеру.\n"
        f"{e('shield')} Без него заявку не примут."
    )


@router.message(ClaimForm.waiting_photo, F.photo)
async def claim_photo(
    message: Message, state: FSMContext, db: Database, config: Config,
    gift_watcher=None,
) -> None:
    data = await state.get_data()
    photo_id = message.photo[-1].file_id

    claim = await submit_claim(
        db,
        message.from_user.id,
        data.get("slug", ""),
        config.claim_needs_approval,
        sender_username=data.get("sender_username", ""),
        photo_id=photo_id,
        resolve_username=(
            gift_watcher.resolve_username if gift_watcher is not None else None
        ),
    )
    await state.clear()
    keyboard = main_menu(is_admin=message.from_user.id in config.admin_ids)

    if claim.result is ClaimResult.DUPLICATE:
        await message.answer(
            f"{e('time')} <b>На этот подарок уже есть заявка</b>\n"
            f"{e('dot')} {esc(claim.title)}\n\n"
            f"{e('shield')} Администратор её рассматривает.",
            reply_markup=keyboard,
        )
        return

    if claim.result is ClaimResult.TAKEN:
        await message.answer(
            f"{e('cross')} <b>Заявка отклонена</b>\n"
            f"{e('shield')} Telegram сообщает, что этот подарок прислал "
            f"другой аккаунт — не тот, что ты указал.\n\n"
            f"{e('dot')} Проверь юзернейм, с которого передавал.\n"
            f"{e('dot')} Если подарок не твой — заявка не пройдёт.",
            reply_markup=keyboard,
        )
        return

    if claim.result is ClaimResult.VERIFIED:
        icon_key, label = GIFT_STATUS.get(claim.status, ("dot", claim.status))
        await message.answer(
            f"{e('check')} <b>Проверено и закреплено</b>\n"
            f"{e('gift')} {esc(claim.title)}\n"
            f"{e(icon_key)} Статус · {label}\n\n"
            f"{e('shield')} Отправитель сверен с данными Telegram — "
            f"подтверждение администратора не нужно.\n"
            f"{e('star')} После продажи получишь <b>{config.worker_share_percent}%</b>.",
            reply_markup=keyboard,
        )
        return

    if claim.result is ClaimResult.PENDING:
        await _notify_claim(
            message.bot, config, claim, message.from_user,
            data.get("sender_username", ""), photo_id,
        )
        await message.answer(
            f"{e('time')} <b>Заявка отправлена на проверку</b>\n"
            f"{e('gift')} {esc(claim.title)}\n"
            f"{e('profile')} Отправлено с · {esc(data.get('sender_username', ''))}\n"
            f"{e('check')} Скриншот приложен\n\n"
            f"{e('shield')} Администратор сверит передачу и подтвердит. "
            f"Ответ придёт сюда же.",
            reply_markup=keyboard,
        )
        return

    icon_key, label = GIFT_STATUS.get(claim.status, ("dot", claim.status))
    await message.answer(
        f"{e('check')} <b>Заявка принята</b>\n"
        f"{e('gift')} {esc(claim.title)}\n"
        f"{e(icon_key)} Статус · {label}\n\n"
        f"{e('star')} После продажи получишь <b>{config.worker_share_percent}%</b> "
        f"на баланс, выплата уйдёт автоматически.",
        reply_markup=keyboard,
    )


@router.message(ClaimForm.waiting_photo)
async def claim_photo_missing(message: Message) -> None:
    await message.answer(
        f"{e('warn')} <b>Нужен именно скриншот</b>\n"
        f"{e('dot')} Пришли картинкой, где видно передачу подарка.\n"
        f"{e('dot')} Файлом или текстом не подойдёт."
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

    await safe_edit(call, f"{e('gift')} <b>Мои подарки</b>\n{body}", claim_menu())
    await call.answer()


async def _notify_claim(bot, config: Config, claim, user, sender_username, photo_id) -> None:
    """Отправляет админам заявку со скриншотом и кнопками решения."""
    from ..keyboards import claim_decision

    caption = (
        f"{e('gift')} <b>Заявка на подарок</b>\n"
        f"{e('dot')} {esc(claim.title)}\n"
        f"<code>{esc(claim.slug)}</code>\n\n"
        f"{e('profile')} Заявитель · {esc(user.full_name)} · @{esc(user.username or '—')}\n"
        f"{e('id')} <code>{user.id}</code>\n"
        f"{e('next')} Передавал с · {esc(sender_username or '—')}\n\n"
        f"{e('warn')} Сверь скриншот и юзернейм, прежде чем подтверждать."
    )
    keyboard = claim_decision(claim.request_id)

    for admin_id in config.admin_ids:
        try:
            if photo_id:
                await bot.send_photo(
                    admin_id, photo_id, caption=caption, reply_markup=keyboard
                )
            else:
                await bot.send_message(admin_id, caption, reply_markup=keyboard)
        except Exception:  # noqa: BLE001 — админ мог не запускать бота
            logger.warning("Не удалось отправить заявку админу %s", admin_id)


# ─── Заявка на выплату: фото передачи + адрес ──────────────────────────

REQUEST_STATUS = {
    "pending": ("time", "ждёт решения"),
    "processing": ("time", "администратор считает сумму"),
    "paid": ("check", "выплачено"),
    "rejected": ("cross", "отказано"),
    "failed": ("warn", "не прошла"),
}


@router.callback_query(F.data == "m:payout_request")
async def payout_request_screen(
    call: CallbackQuery, config: Config, state: FSMContext
) -> None:
    await reset_state(state)
    await safe_edit(
        call,
        f"{e('withdraw')} <b>Заявка на выплату</b>\n"
        f"{e('dot')} Шаг 1 · скриншот передачи подарка\n"
        f"{e('dot')} Шаг 2 · адрес TON, куда отправить\n\n"
        f"{e('star')} Доля · <b>{config.worker_share_percent}%</b> от суммы продажи\n"
        f"{e('shield')} Сумму продажи ставит администратор, "
        f"он же подтверждает выплату.",
        payout_request_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "pr:new")
async def payout_request_start(
    call: CallbackQuery, db: Database, state: FSMContext
) -> None:
    if await db.has_open_payout_request(call.from_user.id):
        await call.answer("Одна заявка уже на рассмотрении", show_alert=True)
        return

    await state.set_state(PayoutRequestForm.waiting_count)
    await safe_edit(
        call,
        f"{e('gift')} <b>Сколько подарков</b>\n"
        f"{e('dot')} Шаг 1 из 3\n"
        f"{e('dot')} На сколько подарков подаёшь заявку?\n\n"
        f"{e('shield')} Заявка одна на все — выплата придёт одной суммой.",
        gifts_count_choice(),
    )
    await call.answer()


@router.callback_query(F.data == "pr:one")
async def payout_request_one(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(gifts_count=1)
    await _ask_for_photo(call, state, count=1)
    await call.answer()


@router.callback_query(F.data == "pr:many")
async def payout_request_many(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PayoutRequestForm.waiting_count)
    await safe_edit(
        call,
        f"{e('gift')} <b>Сколько именно</b>\n"
        f"{e('dot')} Пришли число · <code>3</code>\n\n"
        f"{e('shield')} Столько подарков ты передал менеджеру по этой заявке.",
        back_menu(),
    )
    await call.answer()


@router.message(PayoutRequestForm.waiting_count, F.text)
async def payout_request_count(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_GIFTS_PER_REQUEST:
        await message.answer(
            f"{e('cross')} <b>Это не количество</b>\n"
            f"{e('dot')} Пришли число от 1 до {MAX_GIFTS_PER_REQUEST} · <code>3</code>"
        )
        return

    count = int(raw)
    await state.update_data(gifts_count=count)
    await _ask_for_photo(message, state, count=count)


async def _ask_for_photo(target, state: FSMContext, count: int) -> None:
    """Шаг со скриншотом. Экран одинаков и после кнопки, и после числа."""
    await state.set_state(PayoutRequestForm.waiting_photo)
    text = (
        f"{e('gift')} <b>Скриншот передачи</b>\n"
        f"{e('dot')} Шаг 2 из 3\n"
        f"{e('dot')} Подарков в заявке · <b>{count}</b>\n"
        f"{e('dot')} Пришли фото, где видно, что подарки переданы менеджеру.\n\n"
        f"{e('warn')} Именно фото, не файлом — иначе не приложится к заявке."
    )
    if isinstance(target, CallbackQuery):
        await safe_edit(target, text, back_menu())
    else:
        await target.answer(text, reply_markup=back_menu())


@router.message(PayoutRequestForm.waiting_photo, F.photo)
async def payout_request_photo(
    message: Message, db: Database, state: FSMContext
) -> None:
    # Последний размер — самый крупный из присланных Telegram.
    await state.update_data(photo_id=message.photo[-1].file_id)

    worker = await db.get_worker(message.from_user.id)
    if worker and worker.wallet:
        await state.set_state(None)
        await message.answer(
            f"{e('wallet')} <b>Куда отправить</b>\n"
            f"{e('dot')} Шаг 3 из 3\n"
            f"{e('dot')} Сохранённый адрес\n<code>{esc(worker.wallet)}</code>",
            reply_markup=wallet_choice(worker.wallet),
        )
        return

    await state.set_state(PayoutRequestForm.waiting_wallet)
    await message.answer(
        f"{e('wallet')} <b>Адрес для выплаты</b>\n"
        f"{e('dot')} Шаг 3 из 3\n"
        f"{e('dot')} Пришли адрес TON одним сообщением.\n"
        f"{e('dot')} Начинается с <code>UQ</code> или <code>EQ</code>, 48 символов."
    )


@router.message(PayoutRequestForm.waiting_photo)
async def payout_request_photo_expected(message: Message) -> None:
    """Документ и текст здесь не подходят: к заявке прикладывается фото."""
    await message.answer(
        f"{e('warn')} <b>Жду фото</b>\n"
        f"{e('dot')} Пришли скриншот передачи именно картинкой."
    )


@router.callback_query(F.data == "pr:other")
async def payout_request_other_wallet(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PayoutRequestForm.waiting_wallet)
    await safe_edit(
        call,
        f"{e('key')} <b>Другой адрес</b>\n"
        f"{e('dot')} Пришли адрес TON одним сообщением.\n"
        f"{e('dot')} Начинается с <code>UQ</code> или <code>EQ</code>, 48 символов.\n\n"
        f"{e('shield')} Адрес проверяется по контрольной сумме — "
        f"с опечаткой не пройдёт.",
        back_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "pr:saved")
async def payout_request_saved_wallet(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    worker = await db.get_worker(call.from_user.id)
    if worker is None or not worker.wallet:
        await call.answer("Сохранённого адреса нет", show_alert=True)
        return

    data = await state.get_data()
    await _submit_payout_request(
        call.message, call.from_user, db, config, state,
        wallet=worker.wallet, photo_id=data.get("photo_id"),
        gifts_count=data.get("gifts_count", 1),
    )
    await call.answer()


@router.message(PayoutRequestForm.waiting_wallet, F.text)
async def payout_request_wallet(
    message: Message, db: Database, config: Config, state: FSMContext
) -> None:
    address = (message.text or "").strip()
    if not is_valid_ton_address(address):
        await message.answer(
            f"{e('cross')} <b>Адрес не прошёл проверку</b>\n"
            f"{e('dot')} Скопируй адрес целиком из кошелька.\n"
            f"{e('dot')} Проверяется контрольная сумма, поэтому даже одна "
            f"опечатка не пройдёт."
        )
        return

    data = await state.get_data()
    await _submit_payout_request(
        message, message.from_user, db, config, state,
        wallet=address, photo_id=data.get("photo_id"),
        gifts_count=data.get("gifts_count", 1),
    )


async def _submit_payout_request(
    target: Message, user, db: Database, config: Config, state: FSMContext,
    wallet: str, photo_id: str | None, gifts_count: int = 1,
) -> None:
    """Создаёт заявку и показывает её админам с кнопками решения."""
    await state.clear()

    if await db.has_open_payout_request(user.id):
        await target.answer(
            f"{e('warn')} <b>Заявка уже на рассмотрении</b>\n"
            f"{e('dot')} Дождись решения по ней.",
            reply_markup=back_menu(),
        )
        return

    request_id = await db.add_payout_request(user.id, wallet, photo_id, gifts_count)

    await target.answer(
        f"{e('check')} <b>Заявка отправлена</b>\n"
        f"{e('dot')} Заявка №{request_id}\n"
        f"{e('gift')} Подарков · <b>{gifts_count}</b>\n"
        f"{e('wallet')} <code>{esc(wallet)}</code>\n\n"
        f"{e('time')} Администратор проверит и отправит "
        f"{config.worker_share_percent}% от суммы продажи.",
        reply_markup=back_menu(),
    )

    who = f"@{user.username}" if user.username else str(user.id)
    caption = (
        f"{e('withdraw')} <b>Заявка на выплату</b>\n"
        f"{e('dot')} Заявка №{request_id}\n"
        f"{e('profile')} {esc(who)} · <code>{user.id}</code>\n"
        f"{e('gift')} Подарков · <b>{gifts_count}</b>\n"
        f"{e('wallet')} <code>{esc(wallet)}</code>"
    )
    keyboard = request_decision(request_id)
    for admin_id in config.admin_ids:
        try:
            if photo_id:
                await target.bot.send_photo(
                    admin_id, photo_id, caption=caption, reply_markup=keyboard
                )
            else:
                await target.bot.send_message(admin_id, caption, reply_markup=keyboard)
        except Exception:  # noqa: BLE001 — админ мог не запускать бота
            logger.warning("Не удалось отправить заявку админу %s", admin_id)


@router.callback_query(F.data == "pr:mine")
async def my_payout_requests(
    call: CallbackQuery, db: Database, state: FSMContext
) -> None:
    await reset_state(state)
    rows = [
        row for row in await db.pending_payout_requests()
        if row["worker_id"] == call.from_user.id
    ]
    if not rows:
        await safe_edit(
            call,
            f"{e('dot')} <b>Открытых заявок нет</b>\n"
            f"{e('history')} Выплаченные смотри в «Истории».",
            payout_request_menu(),
        )
        await call.answer()
        return

    icon_key, label = REQUEST_STATUS["pending"]
    body = "\n\n".join(
        f"{e(icon_key)} Заявка №{row['id']} · {label}\n"
        f"{e('wallet')} <code>{esc(row['wallet'])}</code>"
        for row in rows
    )
    await safe_edit(call, f"{e('withdraw')} <b>Мои заявки</b>\n\n{body}", payout_request_menu())
    await call.answer()
