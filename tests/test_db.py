import asyncio
import os
import time

import pytest

from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio
MIN = ton_to_nano("0.1")


async def _ready_worker(db, uid=1, balance="5.0"):
    wallet = build_ton_address(os.urandom(32))
    await db.upsert_worker(uid, "user", f"Работник {uid}")
    await db.set_wallet(uid, wallet)
    if balance:
        await db.credit(uid, ton_to_nano(balance), 999, "тест")
    return wallet


class TestCredit:
    async def test_credit_accumulates(self, db):
        await _ready_worker(db, balance=None)
        assert await db.credit(1, ton_to_nano("1.5"), 9, "a") == ton_to_nano("1.5")
        assert await db.credit(1, ton_to_nano("2.5"), 9, "b") == ton_to_nano("4.0")

    async def test_credit_unknown_worker(self, db):
        with pytest.raises(ValueError, match="worker_not_found"):
            await db.credit(999, 100, 9, "")

    async def test_negative_credit_allowed_but_not_below_zero(self, db):
        await _ready_worker(db, balance="1.0")
        assert await db.credit(1, ton_to_nano("-0.4"), 9, "штраф") == ton_to_nano("0.6")
        with pytest.raises(ValueError, match="negative_balance"):
            await db.credit(1, ton_to_nano("-99"), 9, "перебор")

    async def test_concurrent_credits_are_not_lost(self, db):
        await _ready_worker(db, balance=None)
        await asyncio.gather(*[db.credit(1, ton_to_nano("1.0"), 9, "") for _ in range(20)])
        worker = await db.get_worker(1)
        assert worker.balance_nano == ton_to_nano("20.0")


class TestWithdrawal:
    async def test_reserve_takes_whole_balance(self, db):
        wallet = await _ready_worker(db, balance="2.5")
        wid, got_wallet, amount = await db.reserve_withdrawal(1, MIN)
        assert amount == ton_to_nano("2.5")
        assert got_wallet == wallet
        assert (await db.get_worker(1)).balance_nano == 0

    async def test_second_reserve_blocked(self, db):
        await _ready_worker(db)
        assert await db.reserve_withdrawal(1, MIN) is not None
        assert await db.reserve_withdrawal(1, MIN) is None

    async def test_below_minimum_rejected(self, db):
        await _ready_worker(db, balance="0.05")
        assert await db.reserve_withdrawal(1, MIN) is None

    async def test_no_wallet_rejected(self, db):
        await db.upsert_worker(2, "u", "Без кошелька")
        await db.credit(2, ton_to_nano("5"), 9, "")
        assert await db.reserve_withdrawal(2, MIN) is None

    async def test_concurrent_clicks_reserve_exactly_once(self, db):
        """Двойной вывод — прямая потеря денег, должен быть невозможен."""
        await _ready_worker(db, balance="5.0")
        results = await asyncio.gather(
            *[db.reserve_withdrawal(1, MIN) for _ in range(25)], return_exceptions=True
        )
        reserved = [r for r in results if isinstance(r, tuple)]
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(reserved) == 1
        assert errors == []
        assert sum(r[2] for r in reserved) == ton_to_nano("5.0")

    async def test_refund_restores_balance(self, db):
        await _ready_worker(db, balance="3.0")
        wid, _, amount = await db.reserve_withdrawal(1, MIN)
        await db.mark_failed_and_refund(wid, 1, amount, "сеть упала")
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("3.0")

    async def test_paid_counts_in_stats(self, db):
        await _ready_worker(db, balance="3.0")
        wid, _, _ = await db.reserve_withdrawal(1, MIN)
        await db.mark_paid(wid, "tx_1")
        stats = await db.stats()
        assert stats["paid_count"] == 1
        assert stats["paid_total_nano"] == ton_to_nano("3.0")
        assert stats["total_balance_nano"] == 0


class TestStuckRecovery:
    async def _make_stuck(self, db, age_sec=600):
        await _ready_worker(db, balance="4.0")
        wid, _, amount = await db.reserve_withdrawal(1, MIN)
        await db.conn.execute(
            "UPDATE withdrawals SET created_at=? WHERE id=?",
            (int(time.time()) - age_sec, wid),
        )
        await db.conn.commit()
        return wid, amount

    async def test_fresh_withdrawal_not_stuck(self, db):
        await _ready_worker(db)
        await db.reserve_withdrawal(1, MIN)
        assert await db.find_stuck_withdrawals(300) == []

    async def test_old_processing_detected(self, db):
        wid, amount = await self._make_stuck(db)
        stuck = await db.find_stuck_withdrawals(300)
        assert [(s[0], s[2]) for s in stuck] == [(wid, amount)]

    async def test_resolve_refund_returns_money(self, db):
        wid, _ = await self._make_stuck(db)
        await db.resolve_stuck(wid, sent=False)
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("4.0")
        assert await db.find_stuck_withdrawals(300) == []

    async def test_resolve_sent_keeps_money_out(self, db):
        wid, _ = await self._make_stuck(db)
        await db.resolve_stuck(wid, sent=True, note="tx_abc")
        assert (await db.get_worker(1)).balance_nano == 0
        assert (await db.stats())["paid_count"] == 1

    async def test_double_resolve_rejected(self, db):
        """Повторное закрытие вернуло бы деньги дважды."""
        wid, _ = await self._make_stuck(db)
        await db.resolve_stuck(wid, sent=False)
        with pytest.raises(ValueError, match="not_processing"):
            await db.resolve_stuck(wid, sent=False)


class TestReports:
    async def test_top_sorted_by_paid(self, db):
        for uid, amount in ((1, "3.0"), (2, "5.5"), (3, "1.2")):
            await _ready_worker(db, uid=uid, balance=amount)
            wid, _, _ = await db.reserve_withdrawal(uid, MIN)
            await db.mark_paid(wid, f"tx{uid}")
        top = await db.get_top(10)
        assert [name for name, _, _ in top] == ["Работник 2", "Работник 1", "Работник 3"]

    async def test_top_excludes_unpaid(self, db):
        await _ready_worker(db, balance="9.0")
        assert await db.get_top() == []

    async def test_history_pagination(self, db):
        await _ready_worker(db, balance=None)
        for _ in range(7):
            await db.credit(1, ton_to_nano("1.0"), 9, "")
            wid, _, _ = await db.reserve_withdrawal(1, MIN)
            await db.mark_paid(wid, f"tx{wid}")
        assert await db.count_withdrawals(1) == 7
        assert len(await db.get_withdrawals(1, page=1, per_page=5)) == 5
        assert len(await db.get_withdrawals(1, page=2, per_page=5)) == 2
        assert await db.get_withdrawals(1, page=99, per_page=5) == []

    async def test_worker_totals(self, db):
        await _ready_worker(db, balance="2.0")
        wid, _, _ = await db.reserve_withdrawal(1, MIN)
        await db.mark_paid(wid, "tx")
        assert await db.worker_totals(1) == (ton_to_nano("2.0"), 1)
