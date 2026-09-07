"""Экраны администратора: начисление, статистика, разбор зависших заявок."""
from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..config import Config
from ..db import Database
from ..emoji import e, esc, premium_enabled
from ..gifts.pricing import worker_share
from ..keyboards import (
    admin_menu, back_menu, confirm_share, request_decision, worker_actions,
    workers_list,
)
from ..payout import approve_payout, check_limits, execute_payout, reject_payout
from ..states import ApproveForm, CreditForm
from ..ton import is_seqno_mismatch
from ..ui import safe_edit
from ..utils import fmt_ton, parse_ton

router = Router()


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


@router.message(Command("credit"))
async def credit(message: Message, db: Database, config: Config, payer) -> None:
    """/credit <user_id> <сумма> [комментарий] — начислить баланс работнику."""
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            f"{e('warn')} <b>Формат команды</b>\n"
            f"<code>/credit ID СУММА [коммент]</code>\n\n"
            f"{e('dot')} Пример · <code>/credit 7712345678 1.5 за неделю</code>"
        )
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer(f"{e('cross')} ID должен быть числом, а не <code>{esc(parts[1])}</code>")
        return

    try:
        amount_nano = parse_ton(parts[2])
    except ValueError as exc:
        await message.answer(f"{e('cross')} Сумма · {esc(exc)}")
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
        await message.answer(f"{e('cross')} {esc(reason)}")
        return

    sign = "+" if amount_nano > 0 else ""
    await message.answer(
        f"{e('check')} <b>Начислено</b>\n"
        f"{e('coin')} {sign}{fmt_ton(amount_nano)}\n"
        f"{e('profile')} Работник · <code>{target_id}</code>\n"
        f"{e('balance')} Новый баланс · <b>{fmt_ton(new_balance)}</b>"
    )
    await _notify(
        message, target_id,
        f"{e('balance')} <b>Начисление</b>\n"
        f"{e('coin')} {sign}{fmt_ton(amount_nano)}\n"
        f"{e('dot')} Баланс · <b>{fmt_ton(new_balance)}</b>",
    )

    if config.auto_payout and amount_nano > 0:
        await _auto_payout(message, db, config, payer, target_id)


async def _notify(source, user_id: int, text: str) -> bool:
    try:
        await source.bot.send_message(user_id, text)
        return True
    except Exception:  # noqa: BLE001 — работник мог заблокировать бота
        return False


async def _auto_payout(message: Message, db: Database, config: Config, payer, user_id: int) -> None:
    """Отправляет баланс сразу после начисления, без действий работника."""
    result = await execute_payout(
        db, payer, user_id, config.min_withdraw_nano,
        max_single_nano=config.max_payout_nano,
        max_daily_nano=config.max_daily_payout_nano,
    )

    if result.status == "skipped":
        worker = await db.get_worker(user_id)
        reason = (
            "кошелёк не указан" if worker and not worker.wallet
            else f"баланс ниже минимума {fmt_ton(config.min_withdraw_nano)}"
        )
        await message.answer(f"{e('time')} Автовыплата отложена · {reason}")
        return

    if result.status == "blocked":
        await message.answer(
            f"{e('shield')} <b>Автовыплата остановлена лимитом</b>\n"
            f"{e('dot')} {esc(result.error)}\n"
            f"{e('check')} Средства остались на балансе воркера."
        )
        return

    if result.status == "failed":
        await message.answer(
            f"{e('cross')} <b>Автовыплата не прошла</b>\n"
            f"{e('dot')} Заявка №{result.withdrawal_id}\n"
            f"{e('warn')} {esc(result.error)}\n"
            f"{e('check')} Средства возвращены на баланс."
        )
        return

    demo = f"\n{e('warn')} Режим DRY_RUN" if config.dry_run else ""
    await message.answer(
        f"{e('withdraw')} <b>Автовыплата отправлена</b>\n"
        f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
        f"{e('link')} <code>{esc(result.tx_hash)}</code>{demo}"
    )
    await _notify(
        message, user_id,
        f"{e('check')} <b>Выплата отправлена</b>\n"
        f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
        f"{e('link')} <code>{esc(result.tx_hash)}</code>{demo}",
    )


@router.message(Command("resolve"))
async def resolve(message: Message, db: Database, config: Config) -> None:
    """/resolve <id> sent|refund — закрыть заявку, зависшую после сбоя."""
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or parts[2].split()[0] not in ("sent", "refund"):
        await message.answer(
            f"{e('warn')} <b>Формат команды</b>\n"
            f"<code>/resolve НОМЕР sent|refund</code>\n\n"
            f"{e('check')} <b>sent</b> · TON реально ушли, сверь адрес в блокчейне\n"
            f"{e('cross')} <b>refund</b> · не ушли, вернуть работнику на баланс"
        )
        return

    try:
        withdrawal_id = int(parts[1])
    except ValueError:
        await message.answer(f"{e('cross')} Номер заявки должен быть числом.")
        return

    action = parts[2].split()[0]
    try:
        await db.resolve_stuck(withdrawal_id, sent=action == "sent")
    except ValueError:
        await message.answer(f"{e('cross')} Заявка №{withdrawal_id} не найдена или уже закрыта.")
        return

    outcome = "помечена выплаченной" if action == "sent" else "возвращена на баланс"
    await message.answer(f"{e('check')} Заявка №{withdrawal_id} {outcome}.")


@router.message(Command("gift"))
async def attach_gift(message: Message, db: Database, config: Config) -> None:
    """/gift <slug> <id_воркера> — привязать подарок со скрытым отправителем."""
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        pending = [
            row for row in await db.gifts_by_status("received") if row["worker_id"] is None
        ]
        listing = "\n".join(
            f"{e('dot')} <code>{esc(row['slug'])}</code> · {esc(row['title'] or '—')}"
            for row in pending[:15]
        ) or f"{e('check')} Непривязанных подарков нет."
        await message.answer(
            f"{e('warn')} <b>Формат команды</b>\n"
            f"<code>/gift SLUG ID_воркера</code>\n\n"
            f"{e('gift')} <b>Ждут привязки</b>\n{listing}"
        )
        return

    slug = parts[1].strip()
    try:
        worker_id = int(parts[2].split()[0])
    except ValueError:
        await message.answer(f"{e('cross')} ID воркера должен быть числом.")
        return

    if await db.get_gift(slug) is None:
        await message.answer(f"{e('cross')} Подарок <code>{esc(slug)}</code> не найден.")
        return
    if await db.get_worker(worker_id) is None:
        await message.answer(f"{e('cross')} Воркер <code>{worker_id}</code> не нажимал /start.")
        return

    await db.attach_gift_worker(slug, worker_id)
    await message.answer(
        f"{e('check')} Подарок <code>{esc(slug)}</code> привязан к воркеру "
        f"<code>{worker_id}</code> — уйдёт в продажу на ближайшем круге."
    )


@router.message(Command("drop"))
async def drop_gift(message: Message, db: Database, config: Config) -> None:
    """/drop <slug|all> [причина] — снять подарки, до которых бот не дотянется.

    Смена аккаунта оставляет подарки на старом: передать их некому, а цикл
    будет биться о них каждый круг. Здесь они закрываются разом.
    """
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        stuck = sum(
            len(await db.gifts_by_status(status))
            for status in ("received", "deposited", "listed")
        )
        await message.answer(
            f"{e('warn')} <b>Формат команды</b>\n"
            f"<code>/drop SLUG [причина]</code>\n"
            f"<code>/drop all [причина]</code>\n\n"
            f"{e('dot')} В работе сейчас · <b>{stuck}</b>\n"
            f"{e('shield')} Проданные и выплаченные не трогаются."
        )
        return

    target = parts[1].strip()
    note = (parts[2].strip() if len(parts) > 2 else "снято администратором")

    if target == "all":
        count = await db.drop_unfinished_gifts(note)
        await message.answer(
            f"{e('check')} <b>Снято с обработки</b>\n"
            f"{e('gift')} Подарков · <b>{count}</b>\n"
            f"{e('dot')} Причина · {esc(note)}"
        )
        return

    gift = await db.get_gift(target)
    if gift is None:
        await message.answer(f"{e('cross')} Подарок <code>{esc(target)}</code> не найден.")
        return
    if await db.drop_unfinished_gifts(note, slug=target) == 0:
        await message.answer(
            f"{e('warn')} Подарок <code>{esc(target)}</code> в статусе "
            f"<b>{esc(gift['status'])}</b> — такие не снимаются."
        )
        return

    await message.answer(
        f"{e('check')} Подарок <code>{esc(target)}</code> снят с обработки.\n"
        f"{e('dot')} Причина · {esc(note)}"
    )


@router.message(Command("gifts"))
async def gifts_summary(message: Message, db: Database, config: Config) -> None:
    """/gifts — сводка и состояние каждого подарка."""
    if not _is_admin(message.from_user.id, config):
        return
    if not config.gifts_enabled:
        await message.answer(
            f"{e('warn')} Подсистема подарков выключена.\n"
            f"{e('dot')} Включается переменной <code>GIFTS_ENABLED=true</code>"
        )
        return

    stats = await db.gift_stats()
    text = (
        f"{e('gift')} <b>Подарки</b>\n"
        f"{e('dot')} Принято · <b>{stats['received']}</b>\n"
        f"{e('next')} Передано на маркет · <b>{stats.get('deposited', 0)}</b>\n"
        f"{e('up')} Выставлено · <b>{stats['listed']}</b>\n"
        f"{e('check')} Продано · <b>{stats['sold']}</b>\n"
        f"{e('coin')} Оборот · <b>{fmt_ton(stats['revenue_nano'])}</b>\n\n"
        f"{e('star')} Доля воркера · <b>{config.worker_share_percent}%</b>"
    )
    if stats["skipped"]:
        text += f"\n{e('cross')} Пропущено · <b>{stats['skipped']}</b>"
    await message.answer(text)

    # Детали по тем, что ещё не проданы — видно, что именно их держит.
    waiting = []
    for status in ("received", "deposited", "listed"):
        waiting.extend(await db.gifts_by_status(status))
    if not waiting:
        return

    now = int(time.time())
    blocks = []
    for row in waiting[:15]:
        title = row["title"] or row["slug"]
        lines = [f"{e('gift')} <b>{esc(title)}</b>", f"<code>{esc(row['slug'])}</code>"]

        if row["worker_id"]:
            lines.append(f"{e('profile')} Воркер · <code>{row['worker_id']}</code>")
        else:
            sender = row["sender_id"]
            lines.append(
                f"{e('warn')} Не привязан"
                + (f" · прислал <code>{sender}</code>" if sender else " · отправитель неизвестен")
            )

        left = row["can_resell_at"] - now
        if left > 0:
            days, rest = divmod(left, 86400)
            hours = rest // 3600
            when = f"{days} д {hours} ч" if days else f"{hours} ч"
            lines.append(f"{e('time')} Кулдаун ещё {when}")
        elif row["status"] == "received":
            lines.append(f"{e('check')} Готов к передаче на маркет")
        elif row["status"] == "deposited":
            lines.append(f"{e('next')} Передан, ждёт появления в инвентаре")
        elif row["status"] == "listed":
            lines.append(f"{e('up')} Выставлен за {fmt_ton(row['list_price_nano'])}")

        blocks.append("\n".join(lines))

    await message.answer(
        f"{e('dot')} <b>В работе</b>\n\n" + "\n\n".join(blocks)
    )


@router.message(Command("stats"))
async def stats_command(message: Message, db: Database, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        return
    await message.answer(await _stats_text(db, config))


@router.message(Command("id"))
async def my_id(message: Message) -> None:
    await message.answer(
        f"{e('id')} <b>Твой Telegram ID</b>\n"
        f"<code>{message.from_user.id}</code>"
    )


@router.message(Command("help"))
async def help_command(message: Message, config: Config) -> None:
    text = (
        f"{e('logo')} <b>Как пользоваться</b>\n"
        f"{e('wallet')} Укажи TON-кошелёк в разделе «Кошелёк»\n"
        f"{e('balance')} Дождись начисления баланса\n"
        f"{e('withdraw')} Нажми «Вывести средства»\n\n"
        f"{e('shield')} Адрес проверяется по контрольной сумме — "
        f"опечатка не пройдёт."
    )
    if message.from_user.id in config.admin_ids:
        text += (
            f"\n\n{e('admin')} <b>Команды администратора</b>\n"
            f"<code>/credit ID СУММА [коммент]</code>\n"
            f"<code>/resolve НОМЕР sent|refund</code>\n"
            f"<code>/drop SLUG|all [причина]</code>\n"
            f"<code>/stats</code>"
        )
        if config.gifts_enabled:
            text += (
                f"\n<code>/gifts</code> — сводка по подаркам\n"
                f"<code>/gift SLUG ID</code> — привязать подарок"
            )
    await message.answer(text)


async def _stats_text(db: Database, config: Config) -> str:
    data = await db.stats()
    stuck = await db.find_stuck_withdrawals()
    text = (
        f"{e('stats')} <b>Статистика</b>\n"
        f"{e('users')} Работников · <b>{data['workers']}</b>\n"
        f"{e('balance')} К выплате · <b>{fmt_ton(data['total_balance_nano'])}</b>\n"
        f"{e('check')} Выплат проведено · <b>{data['paid_count']}</b>\n"
        f"{e('withdraw')} Выплачено всего · <b>{fmt_ton(data['paid_total_nano'])}</b>"
    )
    if stuck:
        text += f"\n\n{e('warn')} Зависших заявок · <b>{len(stuck)}</b> — разбери через /resolve"
    if config.use_premium_emoji and not premium_enabled():
        text += (
            f"\n\n{e('warn')} Премиум-эмодзи отключены: у аккаунта бота нет "
            f"Telegram Premium, Telegram отклонил их."
        )
    if config.dry_run:
        text += f"\n\n{e('warn')} Режим DRY_RUN · выплаты не отправляются"
    return text


@router.callback_query(F.data == "m:admin")
async def admin_panel(call: CallbackQuery, config: Config) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return
    mode = "автоматический" if config.auto_payout else "по кнопке"
    await safe_edit(
        call,
        f"{e('admin')} <b>Панель администратора</b>\n"
        f"{e('dot')} Режим выплат · <b>{mode}</b>\n\n"
        f"<code>/credit ID СУММА [коммент]</code>\n"
        f"<code>/resolve НОМЕР sent|refund</code>\n"
        f"<code>/drop SLUG|all [причина]</code>\n"
        f"<code>/stats</code>",
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


@router.callback_query(F.data.startswith("cl:"))
async def resolve_claim(call: CallbackQuery, db: Database, config: Config) -> None:
    """Подтверждение или отклонение заявки воркера на подарок."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    _, decision, raw_id = call.data.split(":", 2)
    try:
        request_id = int(raw_id)
    except ValueError:
        await call.answer("Битая заявка", show_alert=True)
        return

    approved = decision == "ok"
    request = await db.resolve_claim_request(request_id, approved)
    if request is None:
        await safe_edit(
            call,
            f"{e('warn')} <b>Заявка уже закрыта</b>\n"
            f"{e('dot')} Её обработали раньше.",
        )
        await call.answer()
        return

    gift = await db.get_gift(request["slug"])
    title = (gift or {}).get("title") or request["slug"]
    worker_id = request["worker_id"]

    if approved:
        attached = gift and gift["worker_id"] == worker_id
        head = "Заявка подтверждена" if attached else "Подтверждено, но подарок уже занят"
        icon_key = "check" if attached else "warn"
    else:
        head = "Заявка отклонена"
        icon_key = "cross"

    await safe_edit(
        call,
        f"{e(icon_key)} <b>{head}</b>\n"
        f"{e('gift')} {esc(title)}\n"
        f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
        f"{e('next')} Передавал с · {esc(request.get('sender_username') or '—')}",
    )
    await call.answer()

    worker_text = (
        f"{e('check')} <b>Подарок закреплён за тобой</b>\n"
        f"{e('gift')} {esc(title)}\n\n"
        f"{e('star')} После продажи получишь {config.worker_share_percent}% на баланс."
        if approved else
        f"{e('cross')} <b>Заявка отклонена</b>\n"
        f"{e('gift')} {esc(title)}\n\n"
        f"{e('dot')} Если это твой подарок — напиши администратору."
    )
    await _notify(call, worker_id, worker_text)


@router.message(Command("claims"))
async def pending_claims(message: Message, db: Database, config: Config) -> None:
    """/claims — заявки, ждущие решения."""
    if not _is_admin(message.from_user.id, config):
        return

    from ..keyboards import claim_decision

    requests = await db.pending_claim_requests()
    if not requests:
        await message.answer(f"{e('check')} Заявок на рассмотрении нет.")
        return

    await message.answer(f"{e('gift')} <b>Заявки на подарки: {len(requests)}</b>")
    for request in requests[:10]:
        gift = await db.get_gift(request["slug"])
        title = (gift or {}).get("title") or request["slug"]
        caption = (
            f"{e('dot')} {esc(title)}\n"
            f"<code>{esc(request['slug'])}</code>\n"
            f"{e('profile')} Воркер · <code>{request['worker_id']}</code>\n"
            f"{e('next')} Передавал с · {esc(request.get('sender_username') or '—')}"
        )
        photo_id = request.get("photo_id")
        if photo_id:
            await message.answer_photo(
                photo_id, caption=caption, reply_markup=claim_decision(request["id"])
            )
        else:
            await message.answer(caption, reply_markup=claim_decision(request["id"]))


@router.message(Command("sync"))
async def sync_gifts(
    message: Message, db: Database, config: Config,
    gift_watcher=None, gift_service=None,
) -> None:
    """/sync — подтянуть подарки, лежащие на аккаунте.

    Слушатель видит только то, что приходит при работающем боте. Всё, что
    получено до запуска или во время простоя, добирается этой командой.
    """
    if not _is_admin(message.from_user.id, config):
        return
    if not config.gifts_enabled:
        await message.answer(f"{e('warn')} Подсистема подарков выключена.")
        return
    if gift_watcher is None or gift_service is None:
        await message.answer(
            f"{e('cross')} Подсистема подарков не поднялась — смотри логи запуска."
        )
        return

    await message.answer(f"{e('time')} Читаю подарки на аккаунте…")
    try:
        existing = await gift_watcher.list_saved_gifts()
        summary = await gift_service.sync_existing(existing)
    except Exception as exc:  # noqa: BLE001 — покажем причину как есть
        await message.answer(f"{e('cross')} Не получилось · {esc(exc)}")
        return

    await message.answer(
        f"{e('check')} <b>Синхронизация завершена</b>\n"
        f"{e('gift')} Всего на аккаунте · <b>{len(existing)}</b>\n"
        f"{e('dot')} Новых · <b>{summary.get('registered', 0)}</b>\n"
        f"{e('warn')} Без отправителя · <b>{summary.get('unattributed', 0)}</b>\n"
        f"{e('time')} Уже были · <b>{summary.get('duplicate', 0)}</b>"
    )


@router.callback_query(F.data.startswith("pay:"))
async def pay_now(call: CallbackQuery, db: Database, config: Config, payer) -> None:
    """Выплатить баланс воркера одной кнопкой."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        worker_id = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("Битая кнопка", show_alert=True)
        return

    worker = await db.get_worker(worker_id)
    if worker is None:
        await call.answer("Воркер не найден", show_alert=True)
        return

    await call.answer()
    await safe_edit(call, f"{e('time')} Отправляю {fmt_ton(worker.balance_nano)}…")

    result = await execute_payout(
        db, payer, worker_id, config.min_withdraw_nano,
        max_single_nano=config.max_payout_nano,
        max_daily_nano=config.max_daily_payout_nano,
    )

    if result.status == "paid":
        await safe_edit(
            call,
            f"{e('check')} <b>Выплачено</b>\n"
            f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
            f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
            f"{e('link')} <code>{esc(result.tx_hash)}</code>",
        )
        await _notify(
            call, worker_id,
            f"{e('check')} <b>Выплата отправлена</b>\n"
            f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
            f"{e('link')} <code>{esc(result.tx_hash)}</code>",
        )
        return

    reasons = {
        "skipped": "нечего выплачивать · нет кошелька, мало на балансе "
                   "или прошлая заявка ещё в обработке",
        "blocked": f"остановлено лимитом · {esc(result.error)}",
        "failed": f"не прошло · {esc(result.error)}, средства возвращены",
    }
    await safe_edit(
        call,
        f"{e('cross')} <b>Выплата не выполнена</b>\n"
        f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
        f"{e('dot')} {reasons.get(result.status, result.status)}",
    )


@router.message(Command("pay"))
async def pay_command(message: Message, db: Database, config: Config) -> None:
    """/pay <id> — кнопка выплаты для конкретного воркера."""
    if not _is_admin(message.from_user.id, config):
        return

    from ..keyboards import pay_button, withdrawal_actions

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        # Сначала заявки: воркер их уже подал и ждёт именно кнопки.
        held = await db.held_withdrawals()
        for row in held[:15]:
            who = f"@{row['username']}" if row["username"] else str(row["user_id"])
            await message.answer(
                f"{e('withdraw')} <b>Заявка №{row['id']}</b>\n"
                f"{e('profile')} {esc(who)} · <code>{row['user_id']}</code>\n"
                f"{e('coin')} <b>{fmt_ton(row['amount_nano'])}</b>\n"
                f"{e('wallet')} <code>{esc(row['wallet'])}</code>",
                reply_markup=withdrawal_actions(row["id"]),
            )

        pending = [
            w for w in await db.all_workers_with_balance()
        ]
        if not pending:
            if not held:
                await message.answer(f"{e('check')} Балансов к выплате нет.")
            return
        await message.answer(f"{e('withdraw')} <b>Балансы без заявки</b>")
        for worker_id, name, balance in pending[:15]:
            await message.answer(
                f"{e('profile')} {esc(name)} · <code>{worker_id}</code>\n"
                f"{e('coin')} <b>{fmt_ton(balance)}</b>",
                reply_markup=pay_button(worker_id),
            )
        return

    try:
        worker_id = int(parts[1].split()[0])
    except ValueError:
        await message.answer(f"{e('cross')} ID должен быть числом.")
        return

    worker = await db.get_worker(worker_id)
    if worker is None:
        await message.answer(f"{e('cross')} Воркер не найден.")
        return

    await message.answer(
        f"{e('profile')} {esc(worker.full_name)} · <code>{worker_id}</code>\n"
        f"{e('coin')} Баланс · <b>{fmt_ton(worker.balance_nano)}</b>",
        reply_markup=pay_button(worker_id),
    )


@router.callback_query(F.data.startswith("wpay:"))
async def approve_withdrawal(
    call: CallbackQuery, db: Database, config: Config, payer
) -> None:
    """Отправляет придержанную заявку воркера в сеть."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        withdrawal_id = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("Битая кнопка", show_alert=True)
        return

    row = await db.get_withdrawal(withdrawal_id)
    await call.answer()
    await safe_edit(call, f"{e('time')} Отправляю заявку №{withdrawal_id}…")

    result = await approve_payout(
        db, payer, withdrawal_id,
        max_single_nano=config.max_payout_nano,
        max_daily_nano=config.max_daily_payout_nano,
    )
    worker_id = row["user_id"] if row else 0

    if result.status == "paid":
        demo = f"\n{e('warn')} Режим DRY_RUN" if config.dry_run else ""
        await safe_edit(
            call,
            f"{e('check')} <b>Выплачено по заявке №{withdrawal_id}</b>\n"
            f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
            f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
            f"{e('link')} <code>{esc(result.tx_hash)}</code>{demo}",
        )
        await _notify(
            call, worker_id,
            f"{e('check')} <b>Выплата отправлена</b>\n"
            f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
            f"{e('link')} <code>{esc(result.tx_hash)}</code>{demo}",
        )
        return

    reasons = {
        "skipped": "заявка уже закрыта — её обработали раньше",
        "blocked": f"остановлено лимитом · {esc(result.error)}",
        "failed": f"не прошло · {esc(result.error)}, средства возвращены",
    }
    await safe_edit(
        call,
        f"{e('cross')} <b>Заявка №{withdrawal_id} не выплачена</b>\n"
        f"{e('dot')} {reasons.get(result.status, result.status)}",
    )


@router.callback_query(F.data.startswith("wrej:"))
async def decline_withdrawal(call: CallbackQuery, db: Database, config: Config) -> None:
    """Отклоняет заявку и возвращает сумму на баланс воркера."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        withdrawal_id = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("Битая кнопка", show_alert=True)
        return

    row = await db.get_withdrawal(withdrawal_id)
    result = await reject_payout(db, withdrawal_id, "отклонено администратором")
    await call.answer()

    if result.status == "skipped":
        await safe_edit(
            call,
            f"{e('warn')} <b>Заявка №{withdrawal_id} уже закрыта</b>\n"
            f"{e('dot')} Её обработали раньше.",
        )
        return

    worker_id = row["user_id"] if row else 0
    await safe_edit(
        call,
        f"{e('cross')} <b>Заявка №{withdrawal_id} отклонена</b>\n"
        f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
        f"{e('check')} {fmt_ton(result.amount_nano)} возвращены на баланс.",
    )
    await _notify(
        call, worker_id,
        f"{e('cross')} <b>Заявка отклонена</b>\n"
        f"{e('coin')} {fmt_ton(result.amount_nano)} вернулись на баланс.\n"
        f"{e('dot')} Напиши администратору, если это ошибка.",
    )


# ─── Заявки на выплату: принять, ввести сумму продажи, отправить ───────

@router.callback_query(F.data.startswith("pr:ok:"))
async def request_accept(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    """Заявка берётся в работу, дальше админ вводит сумму продажи."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    request_id = int(call.data.rsplit(":", 1)[1])
    row = await db.take_payout_request(request_id)
    if row is None:
        await call.answer("Заявка уже закрыта", show_alert=True)
        return

    await state.set_state(ApproveForm.waiting_sale)
    await state.update_data(request_id=request_id)
    await call.answer()
    count = row["gifts_count"]
    head = "За сколько продан подарок" if count == 1 else "За сколько проданы подарки"
    total = "" if count == 1 else f"{e('dot')} Сумму пиши общую за все {count}\n"
    await call.message.answer(
        f"{e('coin')} <b>{head}</b>\n"
        f"{e('dot')} Заявка №{request_id}\n"
        f"{e('profile')} Воркер · <code>{row['worker_id']}</code>\n"
        f"{e('gift')} Подарков · <b>{count}</b>\n\n"
        f"{total}"
        f"{e('dot')} Пришли сумму продажи в TON · <code>12.5</code>\n"
        f"{e('star')} Воркеру уйдёт {config.worker_share_percent}% от неё."
    )


@router.message(ApproveForm.waiting_sale, F.text)
async def request_sale_amount(
    message: Message, db: Database, config: Config, state: FSMContext
) -> None:
    if not _is_admin(message.from_user.id, config):
        return

    data = await state.get_data()
    request_id = data.get("request_id")
    if request_id is None:
        await state.clear()
        return

    try:
        sale_nano = parse_ton(message.text)
    except ValueError:
        await message.answer(
            f"{e('cross')} <b>Это не сумма</b>\n"
            f"{e('dot')} Пришли число · <code>12.5</code> или <code>0,3</code>"
        )
        return

    if sale_nano <= 0:
        await message.answer(f"{e('cross')} Сумма продажи должна быть больше нуля.")
        return

    share_nano = worker_share(sale_nano, config.worker_share_percent)
    if share_nano <= 0:
        await message.answer(
            f"{e('cross')} <b>Доля вышла нулевой</b>\n"
            f"{e('dot')} Проверь сумму продажи."
        )
        return

    row = await db.get_payout_request(request_id)
    await state.update_data(sale_nano=sale_nano, share_nano=share_nano)
    await state.set_state(None)

    # Подтверждение отдельным шагом: одна лишняя цифра в сумме — это лишние
    # TON, а перевод отменить уже нельзя.
    await message.answer(
        f"{e('withdraw')} <b>Проверь перед отправкой</b>\n"
        f"{e('dot')} Заявка №{request_id}\n"
        f"{e('profile')} Воркер · <code>{row['worker_id']}</code>\n"
        f"{e('gift')} Подарков · <b>{row['gifts_count']}</b>\n"
        f"{e('coin')} Продано за · <b>{fmt_ton(sale_nano)}</b>\n"
        f"{e('star')} Доля {config.worker_share_percent}% · <b>{fmt_ton(share_nano)}</b>\n"
        f"{e('wallet')} <code>{esc(row['wallet'])}</code>",
        reply_markup=confirm_share(request_id),
    )


@router.callback_query(F.data.startswith("pr:pay:"))
async def request_pay(
    call: CallbackQuery, db: Database, config: Config, payer, state: FSMContext
) -> None:
    """Отправляет долю воркеру с горячего кошелька."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    request_id = int(call.data.rsplit(":", 1)[1])
    data = await state.get_data()
    share_nano = data.get("share_nano")
    sale_nano = data.get("sale_nano", 0)
    await state.clear()

    row = await db.get_payout_request(request_id)
    if row is None or row["status"] != "processing" or not share_nano:
        await call.answer("Заявка уже закрыта", show_alert=True)
        return

    await call.answer()
    await safe_edit(call, f"{e('time')} Отправляю {fmt_ton(share_nano)}…")

    blocked = await check_limits(
        db, share_nano, config.max_payout_nano, config.max_daily_payout_nano
    )
    if blocked:
        await db.release_payout_request(request_id)
        await safe_edit(
            call,
            f"{e('shield')} <b>Остановлено лимитом</b>\n"
            f"{e('dot')} {esc(blocked)}\n"
            f"{e('check')} Заявка №{request_id} вернулась в очередь.",
        )
        return

    try:
        tx_hash = await payer.send(row["wallet"], share_nano)
    except Exception as exc:  # noqa: BLE001 — заявка обязана пережить сбой сети
        reason = str(exc) or exc.__class__.__name__

        # Возвращать заявку в очередь можно, только если точно известно, что
        # перевод не исполнился. Контракт, отвергнувший сообщение, — как раз
        # такой случай. Всё остальное (таймаут, разрыв, невнятный ответ ноды)
        # неотличимо от «ушло, но ответ потерялся»: одно нажатие кнопки после
        # такого отправило бы деньги второй раз.
        if is_seqno_mismatch(exc):
            await db.release_payout_request(request_id)
            await safe_edit(
                call,
                f"{e('cross')} <b>Перевод не прошёл</b>\n"
                f"{e('dot')} {esc(reason)}\n"
                f"{e('check')} Контракт отверг сообщение — деньги не списаны.\n"
                f"{e('dot')} Заявка №{request_id} вернулась в очередь.",
            )
            return

        await db.finish_payout_request(
            request_id, "failed", sale_nano, share_nano, note=reason[:300]
        )
        await safe_edit(
            call,
            f"{e('warn')} <b>Исход неизвестен</b>\n"
            f"{e('dot')} Заявка №{request_id}\n"
            f"{e('coin')} <b>{fmt_ton(share_nano)}</b>\n"
            f"{e('wallet')} <code>{esc(row['wallet'])}</code>\n"
            f"{e('cross')} {esc(reason)}\n\n"
            f"{e('shield')} Ответа от сети нет — перевод мог и уйти. "
            f"Проверь кошелёк в блокчейне.\n"
            f"{e('dot')} Ушёл — ничего не делай, заявка закрыта.\n"
            f"{e('dot')} Не ушёл — верни в очередь командой "
            f"<code>/repay {request_id}</code>",
        )
        return

    await db.finish_payout_request(
        request_id, "paid", sale_nano, share_nano, tx_hash,
        note=f"продан за {fmt_ton(sale_nano)}",
    )
    # Та же выплата попадает в общую историю: иначе её не увидят ни топ,
    # ни суточный лимит, ни «История» у воркера.
    await db.record_direct_payout(row["worker_id"], share_nano, row["wallet"], tx_hash)

    demo = f"\n{e('warn')} Режим DRY_RUN" if config.dry_run else ""
    await safe_edit(
        call,
        f"{e('check')} <b>Выплачено по заявке №{request_id}</b>\n"
        f"{e('profile')} Воркер · <code>{row['worker_id']}</code>\n"
        f"{e('coin')} Продан за · {fmt_ton(sale_nano)}\n"
        f"{e('star')} Отправлено · <b>{fmt_ton(share_nano)}</b>\n"
        f"{e('link')} <code>{esc(tx_hash)}</code>{demo}",
    )
    await _notify(
        call, row["worker_id"],
        f"{e('check')} <b>Выплата отправлена</b>\n"
        f"{e('coin')} <b>{fmt_ton(share_nano)}</b>\n"
        f"{e('wallet')} <code>{esc(row['wallet'])}</code>\n"
        f"{e('link')} <code>{esc(tx_hash)}</code>{demo}",
    )


@router.callback_query(F.data.startswith("pr:cancel:"))
async def request_cancel(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    """Отмена на шаге суммы — заявка возвращается в очередь, не закрывается."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    request_id = int(call.data.rsplit(":", 1)[1])
    await state.clear()
    await db.release_payout_request(request_id)
    await safe_edit(
        call,
        f"{e('dot')} <b>Отменено</b>\n"
        f"{e('dot')} Заявка №{request_id} снова в очереди — открой её "
        f"через <code>/requests</code>.",
    )
    await call.answer()


@router.callback_query(F.data.startswith("pr:no:"))
async def request_reject(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    request_id = int(call.data.rsplit(":", 1)[1])
    row = await db.take_payout_request(request_id)
    if row is None:
        await call.answer("Заявка уже закрыта", show_alert=True)
        return

    await state.clear()
    await db.finish_payout_request(request_id, "rejected", note="отказ администратора")
    await call.answer()
    await safe_edit(
        call,
        f"{e('cross')} <b>Заявка №{request_id} отклонена</b>\n"
        f"{e('profile')} Воркер · <code>{row['worker_id']}</code>",
    )
    await _notify(
        call, row["worker_id"],
        f"{e('cross')} <b>Заявка отклонена</b>\n"
        f"{e('dot')} Напиши администратору, если это ошибка.",
    )


@router.message(Command("requests"))
async def requests_command(message: Message, db: Database, config: Config) -> None:
    """/requests — открытые заявки на выплату, каждая со своими кнопками."""
    if not _is_admin(message.from_user.id, config):
        return

    rows = await db.pending_payout_requests()
    if not rows:
        await message.answer(f"{e('check')} Открытых заявок нет.")
        return

    for row in rows[:15]:
        who = f"@{row['username']}" if row["username"] else str(row["worker_id"])
        caption = (
            f"{e('withdraw')} <b>Заявка №{row['id']}</b>\n"
            f"{e('profile')} {esc(who)} · <code>{row['worker_id']}</code>\n"
            f"{e('gift')} Подарков · <b>{row['gifts_count']}</b>\n"
            f"{e('wallet')} <code>{esc(row['wallet'])}</code>"
        )
        if row["photo_id"]:
            await message.answer_photo(
                row["photo_id"], caption=caption,
                reply_markup=request_decision(row["id"]),
            )
        else:
            await message.answer(caption, reply_markup=request_decision(row["id"]))


# ─── Воркеры кнопками: без ввода id руками ────────────────────────────

@router.callback_query(F.data == "a:workers")
async def workers_screen(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    rows = await db.all_workers()
    if not rows:
        await safe_edit(
            call,
            f"{e('users')} <b>Воркеров нет</b>\n"
            f"{e('dot')} Появятся, как только кто-нибудь нажмёт /start.",
            back_menu(),
        )
        await call.answer()
        return

    labels = [
        (worker_id, f"{name} · {fmt_ton(balance)}")
        for worker_id, name, balance in rows
    ]
    await safe_edit(
        call,
        f"{e('users')} <b>Воркеры</b>\n"
        f"{e('dot')} Выбери, чтобы начислить или выплатить.",
        workers_list(labels),
    )
    await call.answer()


@router.callback_query(F.data.startswith("wk:credit:"))
async def worker_credit_prompt(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    worker_id = int(call.data.rsplit(":", 1)[1])
    worker = await db.get_worker(worker_id)
    if worker is None:
        await call.answer("Воркер не найден", show_alert=True)
        return

    await state.set_state(CreditForm.waiting_amount)
    await state.update_data(target_id=worker_id)
    await safe_edit(
        call,
        f"{e('coin')} <b>Начисление</b>\n"
        f"{e('profile')} {esc(worker.full_name)} · <code>{worker_id}</code>\n"
        f"{e('balance')} Сейчас · {fmt_ton(worker.balance_nano)}\n\n"
        f"{e('dot')} Пришли сумму · <code>1.5</code>\n"
        f"{e('dot')} Со знаком минус — списание · <code>-0.5</code>",
        back_menu(),
    )
    await call.answer()


@router.message(CreditForm.waiting_amount, F.text)
async def worker_credit_amount(
    message: Message, db: Database, config: Config, state: FSMContext
) -> None:
    if not _is_admin(message.from_user.id, config):
        return

    data = await state.get_data()
    worker_id = data.get("target_id")
    if worker_id is None:
        await state.clear()
        return

    raw = (message.text or "").strip()
    try:
        amount_nano = parse_ton(raw.lstrip("-"))
    except ValueError:
        await message.answer(
            f"{e('cross')} <b>Это не сумма</b>\n"
            f"{e('dot')} Пришли число · <code>1.5</code> или <code>-0,5</code>"
        )
        return
    if raw.startswith("-"):
        amount_nano = -amount_nano

    await state.clear()
    new_balance = await db.credit(worker_id, amount_nano, message.from_user.id, "кнопкой")
    sign = "+" if amount_nano > 0 else ""
    await message.answer(
        f"{e('check')} <b>Начислено</b>\n"
        f"{e('coin')} {sign}{fmt_ton(amount_nano)}\n"
        f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
        f"{e('balance')} Новый баланс · <b>{fmt_ton(new_balance)}</b>",
        reply_markup=worker_actions(worker_id),
    )
    await _notify(
        message, worker_id,
        f"{e('balance')} <b>Начисление</b>\n"
        f"{e('coin')} {sign}{fmt_ton(amount_nano)}\n"
        f"{e('dot')} Баланс · <b>{fmt_ton(new_balance)}</b>",
    )


@router.callback_query(F.data.startswith("wk:"))
async def worker_card(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    """Карточка воркера. Регистрируется после wk:credit: — иначе перехватит его."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    try:
        worker_id = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer("Битая кнопка", show_alert=True)
        return

    worker = await db.get_worker(worker_id)
    if worker is None:
        await call.answer("Воркер не найден", show_alert=True)
        return

    paid_total, paid_count = await db.worker_totals(worker_id)
    wallet = f"<code>{esc(worker.wallet)}</code>" if worker.wallet else "не указан"
    await safe_edit(
        call,
        f"{e('profile')} <b>{esc(worker.full_name)}</b>\n"
        f"{e('id')} <code>{worker_id}</code>\n"
        f"{e('balance')} Баланс · <b>{fmt_ton(worker.balance_nano)}</b>\n"
        f"{e('check')} Выплачено · {fmt_ton(paid_total)} за {paid_count}\n"
        f"{e('wallet')} {wallet}",
        worker_actions(worker_id),
    )
    await call.answer()


@router.callback_query(F.data == "a:requests")
async def admin_requests_screen(
    call: CallbackQuery, db: Database, config: Config, state: FSMContext
) -> None:
    """Открытые заявки: отправляются отдельными сообщениями, чтобы было видно фото."""
    if not _is_admin(call.from_user.id, config):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    rows = await db.pending_payout_requests()
    if not rows:
        await safe_edit(
            call,
            f"{e('check')} <b>Открытых заявок нет</b>\n"
            f"{e('dot')} Появятся, когда воркер подаст.",
            back_menu(),
        )
        await call.answer()
        return

    await safe_edit(
        call,
        f"{e('withdraw')} <b>Заявок на выплату · {len(rows)}</b>\n"
        f"{e('dot')} Каждая ниже, со своими кнопками.",
        back_menu(),
    )
    for row in rows[:15]:
        who = f"@{row['username']}" if row["username"] else str(row["worker_id"])
        caption = (
            f"{e('withdraw')} <b>Заявка №{row['id']}</b>\n"
            f"{e('profile')} {esc(who)} · <code>{row['worker_id']}</code>\n"
            f"{e('gift')} Подарков · <b>{row['gifts_count']}</b>\n"
            f"{e('wallet')} <code>{esc(row['wallet'])}</code>"
        )
        if row["photo_id"]:
            await call.message.answer_photo(
                row["photo_id"], caption=caption,
                reply_markup=request_decision(row["id"]),
            )
        else:
            await call.message.answer(caption, reply_markup=request_decision(row["id"]))
    await call.answer()


@router.message(Command("repay"))
async def repay_command(message: Message, db: Database, config: Config) -> None:
    """/repay НОМЕР — вернуть в очередь заявку, по которой перевод не ушёл.

    Отдельная команда, а не кнопка: возвращать заявку можно только после того,
    как человек своими глазами посмотрел кошелёк в блокчейне. Кнопка рядом с
    сообщением провоцировала бы нажать её не проверив — и заплатить дважды.
    """
    if not _is_admin(message.from_user.id, config):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().split()[0].isdigit():
        await message.answer(
            f"{e('warn')} <b>Формат команды</b>\n"
            f"<code>/repay НОМЕР</code>\n\n"
            f"{e('shield')} Только для заявок с неизвестным исходом и только "
            f"после проверки кошелька в блокчейне."
        )
        return

    request_id = int(parts[1].strip().split()[0])
    row = await db.reopen_payout_request(request_id)
    if row is None:
        current = await db.get_payout_request(request_id)
        state = current["status"] if current else "не найдена"
        await message.answer(
            f"{e('cross')} <b>Заявка №{request_id} не возвращена</b>\n"
            f"{e('dot')} Статус · <b>{esc(state)}</b>\n"
            f"{e('shield')} Возвращаются только заявки с неизвестным исходом."
        )
        return

    await message.answer(
        f"{e('check')} <b>Заявка №{request_id} снова в очереди</b>\n"
        f"{e('profile')} Воркер · <code>{row['worker_id']}</code>\n"
        f"{e('wallet')} <code>{esc(row['wallet'])}</code>",
        reply_markup=request_decision(request_id),
    )
