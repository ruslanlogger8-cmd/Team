"""Единая логика выплаты — используется и кнопкой, и авто-режимом.

Вся работа с деньгами живёт здесь, чтобы ручной и автоматический путь не
разъехались: любая правка применяется сразу к обоим.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from .db import Database

logger = logging.getLogger(__name__)

# Один активный вывод на пользователя внутри процесса — страховка поверх
# резерва в БД. Словарь чистится после освобождения, иначе растёт навсегда.
_locks: dict[int, asyncio.Lock] = {}


class Payer(Protocol):
    address: str

    async def send(self, destination: str, amount_nano: int) -> str: ...


@dataclass(frozen=True)
class PayoutResult:
    status: Literal["paid", "failed", "skipped"]
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


async def execute_payout(
    db: Database,
    payer: Payer,
    user_id: int,
    min_nano: int,
) -> PayoutResult:
    """Выплачивает весь баланс работника.

    Баланс резервируется одной транзакцией до отправки, поэтому двойная выплата
    невозможна. При сбое сети средства возвращаются на баланс.

    status='skipped' — выплачивать нечего: мало баланса, нет кошелька либо
    предыдущая заявка ещё в обработке.
    """
    async with _user_lock(user_id):
        reserved = await db.reserve_withdrawal(user_id, min_nano)
        if reserved is None:
            return PayoutResult(status="skipped")

        withdrawal_id, wallet, amount_nano = reserved

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
