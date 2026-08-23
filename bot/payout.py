"""Единая логика выплаты — используется и кнопкой, и авто-режимом.

Вся работа с деньгами живёт здесь, чтобы ручной и автоматический путь не
разъехались: любая правка применяется сразу к обоим.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from .db import Database
from .utils import fmt_ton

logger = logging.getLogger(__name__)

# Один активный вывод на пользователя внутри процесса — страховка поверх
# резерва в БД. Словарь чистится после освобождения, иначе растёт навсегда.
_locks: dict[int, asyncio.Lock] = {}


class Payer(Protocol):
    address: str

    async def send(self, destination: str, amount_nano: int) -> str: ...


@dataclass(frozen=True)
class PayoutResult:
    status: Literal["paid", "failed", "skipped", "blocked"]
    amount_nano: int = 0
    withdrawal_id: int | None = None
    tx_hash: str | None = None
    error: str | None = None

    @property
    def is_paid(self) -> bool:
        return self.status == "paid"


@asynccontextmanager
async def _user_lock(user_id: int):
    lock = _locks.setdefault(user_id, asyncio.Lock())
    try:
        async with lock:
            yield
    finally:
        if not lock.locked() and not lock._waiters:
            _locks.pop(user_id, None)


async def check_limits(
    db: Database, requested_nano: int, max_single_nano: int, max_daily_nano: int
) -> str | None:
    """Проверяет лимиты выплат. Возвращает причину отказа или None.

    Лимиты существуют не для честных сценариев, а чтобы ограничить ущерб от
    ошибки в коде или опечатки в команде: даже если что-то посчитается
    неверно, за раз и за сутки уйдёт не больше заданного.
    """
    if max_single_nano > 0 and requested_nano > max_single_nano:
        return (
            f"разовый лимит {fmt_ton(max_single_nano)}, "
            f"запрошено {fmt_ton(requested_nano)}"
        )

    if max_daily_nano > 0:
        day_ago = int(time.time()) - 86400
        already = await db.paid_since(day_ago)
        if already + requested_nano > max_daily_nano:
            return (
                f"суточный лимит {fmt_ton(max_daily_nano)}, "
                f"за сутки уже {fmt_ton(already)}, запрошено {fmt_ton(requested_nano)}"
            )
    return None


async def execute_payout(
    db: Database,
    payer: Payer,
    user_id: int,
    min_nano: int,
    amount_nano: int | None = None,
    max_single_nano: int = 0,
    max_daily_nano: int = 0,
) -> PayoutResult:
    """Выплачивает весь баланс работника.

    amount_nano=None — выводится весь баланс, иначе указанная сумма.

    Баланс резервируется одной транзакцией до отправки, поэтому двойная выплата
    невозможна. При сбое сети средства возвращаются на баланс.

    status='skipped' — выплачивать нечего: мало баланса, нет кошелька либо
    предыдущая заявка ещё в обработке.
    """
    async with _user_lock(user_id):
        reserved = await db.reserve_withdrawal(user_id, min_nano, amount_nano)
        if reserved is None:
            return PayoutResult(status="skipped")

        withdrawal_id, wallet, amount_nano = reserved

        # Лимит проверяем после резерва: только здесь известна точная сумма,
        # когда выводится «весь баланс». Отказ возвращает деньги на место.
        blocked = await check_limits(db, amount_nano, max_single_nano, max_daily_nano)
        if blocked:
            await db.mark_failed_and_refund(
                withdrawal_id, user_id, amount_nano, f"лимит: {blocked}"
            )
            logger.warning("Выплата #%s отклонена лимитом: %s", withdrawal_id, blocked)
            return PayoutResult(
                status="blocked",
                amount_nano=amount_nano,
                withdrawal_id=withdrawal_id,
                error=blocked,
            )

        try:
            tx_hash = await payer.send(wallet, amount_nano)
        except Exception as exc:  # noqa: BLE001 — любой сбой означает возврат
            logger.exception("Выплата #%s провалилась", withdrawal_id)
            await db.mark_failed_and_refund(withdrawal_id, user_id, amount_nano, repr(exc))
            return PayoutResult(
                status="failed",
                amount_nano=amount_nano,
                withdrawal_id=withdrawal_id,
                error=str(exc) or exc.__class__.__name__,
            )

        await db.mark_paid(withdrawal_id, tx_hash)
        logger.info("Выплата #%s: %s нанотон → %s", withdrawal_id, amount_nano, wallet)
        return PayoutResult(
            status="paid",
            amount_nano=amount_nano,
            withdrawal_id=withdrawal_id,
            tx_hash=tx_hash,
        )
