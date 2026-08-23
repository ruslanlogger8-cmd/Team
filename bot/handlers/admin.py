"""Экраны администратора: начисление, статистика, разбор зависших заявок."""
from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..config import Config
from ..db import Database
from ..emoji import e, esc, premium_enabled
from ..keyboards import admin_menu, back_menu
from ..payout import execute_payout
from ..ui import safe_edit
from ..utils import fmt_ton, parse_ton

router = Router()
RULE = "━━━━━━━━━━━━━━━━━━━━"


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
            f"{RULE}\n"
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
        f"{RULE}\n"
        f"{e('coin')} {sign}{fmt_ton(amount_nano)}\n"
        f"{e('profile')} Работник · <code>{target_id}</code>\n"
        f"{e('balance')} Новый баланс · <b>{fmt_ton(new_balance)}</b>"
    )
    await _notify(
        message, target_id,
        f"{e('balance')} <b>Начисление</b>\n"
        f"{RULE}\n"
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
    result = await execute_payout(db, payer, user_id, config.min_withdraw_nano)

    if result.status == "skipped":
        worker = await db.get_worker(user_id)
        reason = (
            "кошелёк не указан" if worker and not worker.wallet
            else f"баланс ниже минимума {fmt_ton(config.min_withdraw_nano)}"
        )
        await message.answer(f"{e('time')} Автовыплата отложена · {reason}")
        return

    if result.status == "failed":
        await message.answer(
            f"{e('cross')} <b>Автовыплата не прошла</b>\n"
            f"{RULE}\n"
            f"{e('dot')} Заявка №{result.withdrawal_id}\n"
            f"{e('warn')} {esc(result.error)}\n"
            f"{e('check')} Средства возвращены на баланс."
        )
        return

    demo = f"\n{e('warn')} Режим DRY_RUN" if config.dry_run else ""
    await message.answer(
        f"{e('withdraw')} <b>Автовыплата отправлена</b>\n"
        f"{RULE}\n"
        f"{e('coin')} <b>{fmt_ton(result.amount_nano)}</b>\n"
        f"{e('link')} <code>{esc(result.tx_hash)}</code>{demo}"
    )
    await _notify(
        message, user_id,
        f"{e('check')} <b>Выплата отправлена</b>\n"
        f"{RULE}\n"
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
            f"{RULE}\n"
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
            f"{RULE}\n"
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
        f"{RULE}\n"
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
        f"{e('dot')} <b>В работе</b>\n{RULE}\n" + "\n\n".join(blocks)
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
        f"{RULE}\n<code>{message.from_user.id}</code>"
    )


@router.message(Command("help"))
async def help_command(message: Message, config: Config) -> None:
    text = (
        f"{e('logo')} <b>Как пользоваться</b>\n"
        f"{RULE}\n"
        f"{e('wallet')} Укажи TON-кошелёк в разделе «Кошелёк»\n"
        f"{e('balance')} Дождись начисления баланса\n"
        f"{e('withdraw')} Нажми «Вывести средства»\n\n"
        f"{e('shield')} Адрес проверяется по контрольной сумме — "
        f"опечатка не пройдёт."
    )
    if message.from_user.id in config.admin_ids:
        text += (
            f"\n\n{e('admin')} <b>Команды администратора</b>\n"
            f"{RULE}\n"
            f"<code>/credit ID СУММА [коммент]</code>\n"
            f"<code>/resolve НОМЕР sent|refund</code>\n"
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
        f"{RULE}\n"
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
        f"{RULE}\n"
        f"{e('dot')} Режим выплат · <b>{mode}</b>\n\n"
        f"<code>/credit ID СУММА [коммент]</code>\n"
        f"<code>/resolve НОМЕР sent|refund</code>\n"
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
            f"{RULE}\n{e('dot')} Её обработали раньше.",
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
        f"{RULE}\n"
        f"{e('gift')} {esc(title)}\n"
        f"{e('profile')} Воркер · <code>{worker_id}</code>\n"
        f"{e('next')} Передавал с · {esc(request.get('sender_username') or '—')}",
    )
    await call.answer()

    worker_text = (
        f"{e('check')} <b>Подарок закреплён за тобой</b>\n"
        f"{RULE}\n"
        f"{e('gift')} {esc(title)}\n\n"
        f"{e('star')} После продажи получишь {config.worker_share_percent}% на баланс."
        if approved else
        f"{e('cross')} <b>Заявка отклонена</b>\n"
        f"{RULE}\n"
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
        f"{RULE}\n"
        f"{e('gift')} Всего на аккаунте · <b>{len(existing)}</b>\n"
        f"{e('dot')} Новых · <b>{summary.get('registered', 0)}</b>\n"
        f"{e('warn')} Без отправителя · <b>{summary.get('unattributed', 0)}</b>\n"
        f"{e('time')} Уже были · <b>{summary.get('duplicate', 0)}</b>"
    )
