"""Авто-режим: начислил — деньги ушли сразу, без нажатия работником."""
import asyncio
import os

import pytest

from bot.payout import execute_payout
from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio
MIN = ton_to_nano("0.1")


class FakePayer:
    address = "EQtest"

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.sent = []

    async def send(self, destination, amount_nano):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("нет сети")
        self.sent.append((destination, amount_nano))
        return f"tx_{len(self.sent)}"


async def _worker(db, uid=1, wallet=True, balance=None):
    await db.upsert_worker(uid, "u", f"W{uid}")
    if wallet:
        await db.set_wallet(uid, build_ton_address(os.urandom(32)))
    if balance:
        await db.credit(uid, ton_to_nano(balance), 9, "")


class TestExecutePayout:
    async def test_pays_full_balance(self, db):
        await _worker(db, balance="2.5")
        payer = FakePayer()
        result = await execute_payout(db, payer, 1, MIN)
        assert result.is_paid
        assert result.amount_nano == ton_to_nano("2.5")
        assert result.tx_hash == "tx_1"
        assert (await db.get_worker(1)).balance_nano == 0

    async def test_skipped_without_wallet(self, db):
        await _worker(db, wallet=False, balance="5.0")
        result = await execute_payout(db, FakePayer(), 1, MIN)
        assert result.status == "skipped"
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("5.0")

    async def test_skipped_below_minimum(self, db):
        await _worker(db, balance="0.05")
        result = await execute_payout(db, FakePayer(), 1, MIN)
        assert result.status == "skipped"

    async def test_skipped_when_nothing_to_pay(self, db):
        await _worker(db)
        assert (await execute_payout(db, FakePayer(), 1, MIN)).status == "skipped"

    async def test_failure_refunds_and_reports(self, db):
        await _worker(db, balance="3.0")
        result = await execute_payout(db, FakePayer(fail_times=1), 1, MIN)
        assert result.status == "failed"
        assert result.error
        assert result.amount_nano == ton_to_nano("3.0")
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("3.0")

    async def test_unknown_worker_is_skipped(self, db):
        assert (await execute_payout(db, FakePayer(), 999, MIN)).status == "skipped"


class TestAutoSequence:
    async def test_each_credit_triggers_one_payout(self, db):
        """Три начисления подряд — три выплаты, суммы совпадают."""
        await _worker(db)
        payer = FakePayer()
        for amount in ("1.0", "2.0", "0.5"):
            await db.credit(1, ton_to_nano(amount), 9, "")
            result = await execute_payout(db, payer, 1, MIN)
            assert result.is_paid
            assert result.amount_nano == ton_to_nano(amount)
        assert len(payer.sent) == 3
        assert sum(a for _, a in payer.sent) == ton_to_nano("3.5")

    async def test_balance_accumulates_until_minimum(self, db):
        """Мелкие начисления копятся, пока не наберут минимум."""
        await _worker(db)
        payer = FakePayer()
        await db.credit(1, ton_to_nano("0.04"), 9, "")
        assert (await execute_payout(db, payer, 1, MIN)).status == "skipped"
        await db.credit(1, ton_to_nano("0.04"), 9, "")
        assert (await execute_payout(db, payer, 1, MIN)).status == "skipped"
        await db.credit(1, ton_to_nano("0.04"), 9, "")
        result = await execute_payout(db, payer, 1, MIN)
        assert result.is_paid
        assert result.amount_nano == ton_to_nano("0.12")


class TestConcurrency:
    async def test_parallel_triggers_pay_once(self, db):
        """Кнопка и автовыплата могли сработать одновременно — деньги уходят один раз."""
        await _worker(db, balance="4.0")
        payer = FakePayer()
        results = await asyncio.gather(
            *[execute_payout(db, payer, 1, MIN) for _ in range(15)],
            return_exceptions=True,
        )
        assert [r for r in results if isinstance(r, Exception)] == []
        assert sum(1 for r in results if r.is_paid) == 1
        assert len(payer.sent) == 1
        assert payer.sent[0][1] == ton_to_nano("4.0")

    async def test_lock_dict_does_not_leak(self, db):
        from bot.payout import _locks
        await _worker(db, balance="1.0")
        await execute_payout(db, FakePayer(), 1, MIN)
        assert _locks == {}


class TestAccounting:
    async def test_nothing_created_or_lost(self, db):
        credited = 0
        payer = FakePayer(fail_times=1)   # первая выплата провалится
        for uid in range(1, 5):
            await _worker(db, uid=uid)
            amount = ton_to_nano(str(uid))
            await db.credit(uid, amount, 9, "")
            credited += amount
            await execute_payout(db, payer, uid, MIN)
        stats = await db.stats()
        assert stats["paid_total_nano"] + stats["total_balance_nano"] == credited
