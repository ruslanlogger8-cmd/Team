"""Лимиты выплат: ограничивают ущерб от ошибки, а не честные сценарии."""
import os
import time

import pytest

from bot.payout import check_limits, execute_payout
from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio
MIN = ton_to_nano("0.1")
SINGLE = ton_to_nano("50")
DAILY = ton_to_nano("100")


class FakePayer:
    address = "EQtest"

    def __init__(self):
        self.sent = []

    async def send(self, destination, amount_nano):
        self.sent.append((destination, amount_nano))
        return f"tx_{len(self.sent)}"


async def _worker(db, uid=1, balance="10.0"):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))
    await db.credit(uid, ton_to_nano(balance), 9, "")


class TestSingleLimit:
    async def test_within_limit_passes(self, db):
        assert await check_limits(db, ton_to_nano("10"), SINGLE, DAILY) is None

    async def test_above_limit_blocked(self, db):
        reason = await check_limits(db, ton_to_nano("60"), SINGLE, DAILY)
        assert reason and "разовый лимит" in reason

    async def test_zero_limit_means_unlimited(self, db):
        assert await check_limits(db, ton_to_nano("9999"), 0, 0) is None


class TestDailyLimit:
    async def test_accumulates_across_payouts(self, db):
        """Суточный лимит 100: три по 30 проходят, четвёртая упирается."""
        await _worker(db, balance="200.0")
        payer = FakePayer()
        results = []
        for _ in range(4):
            results.append(await execute_payout(
                db, payer, 1, MIN, ton_to_nano("30"),
                max_single_nano=SINGLE, max_daily_nano=DAILY,
            ))
        assert [r.status for r in results] == ["paid", "paid", "paid", "blocked"]
        assert sum(a for _, a in payer.sent) == ton_to_nano("90")

    async def test_old_payouts_do_not_count(self, db):
        """Лимит скользящий: вчерашние выплаты сегодня не мешают."""
        await _worker(db, balance="200.0")
        payer = FakePayer()
        await execute_payout(
            db, payer, 1, MIN, ton_to_nano("90"),
            max_single_nano=SINGLE * 10, max_daily_nano=DAILY,
        )
        await db.conn.execute(
            "UPDATE withdrawals SET finished_at=? WHERE status='paid'",
            (int(time.time()) - 200_000,),
        )
        await db.conn.commit()
        assert await check_limits(db, ton_to_nano("90"), 0, DAILY) is None


class TestBlockedPayout:
    async def test_money_returns_to_balance(self, db):
        """Блокировка не должна съедать деньги воркера."""
        await _worker(db, balance="80.0")
        result = await execute_payout(
            db, FakePayer(), 1, MIN, ton_to_nano("60"),
            max_single_nano=SINGLE, max_daily_nano=DAILY,
        )
        assert result.status == "blocked"
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("80.0")

    async def test_nothing_sent_when_blocked(self, db):
        await _worker(db, balance="80.0")
        payer = FakePayer()
        await execute_payout(
            db, payer, 1, MIN, ton_to_nano("60"),
            max_single_nano=SINGLE, max_daily_nano=DAILY,
        )
        assert payer.sent == []

    async def test_bounded_loss_under_runaway_credits(self, db):
        """Даже если начислить абсурд, за сутки уйдёт не больше лимита."""
        await _worker(db, balance="0")
        await db.credit(1, ton_to_nano("100000"), 9, "опечатка")
        payer = FakePayer()
        for _ in range(20):
            await execute_payout(
                db, payer, 1, MIN, ton_to_nano("50"),
                max_single_nano=SINGLE, max_daily_nano=DAILY,
            )
        assert sum(a for _, a in payer.sent) <= DAILY
