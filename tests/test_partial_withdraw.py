"""Частичный вывод: указанная сумма списывается, остаток сохраняется."""
import asyncio
import os

import pytest

from bot.payout import execute_payout
from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio
MIN = ton_to_nano("0.1")


class FakePayer:
    address = "EQtest"

    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send(self, destination, amount_nano):
        if self.fail:
            raise ConnectionError("нет сети")
        self.sent.append((destination, amount_nano))
        return f"tx_{len(self.sent)}"


async def _worker(db, balance="5.0", uid=1):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))
    await db.credit(uid, ton_to_nano(balance), 9, "")


class TestReserve:
    async def test_partial_keeps_remainder(self, db):
        await _worker(db, "5.0")
        wid, _, amount = await db.reserve_withdrawal(1, MIN, ton_to_nano("2.0"))
        assert amount == ton_to_nano("2.0")
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("3.0")

    async def test_none_takes_everything(self, db):
        await _worker(db, "5.0")
        _, _, amount = await db.reserve_withdrawal(1, MIN)
        assert amount == ton_to_nano("5.0")
        assert (await db.get_worker(1)).balance_nano == 0

    async def test_more_than_balance_rejected(self, db):
        await _worker(db, "1.0")
        assert await db.reserve_withdrawal(1, MIN, ton_to_nano("5.0")) is None
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("1.0")

    async def test_below_minimum_rejected(self, db):
        await _worker(db, "5.0")
        assert await db.reserve_withdrawal(1, MIN, ton_to_nano("0.01")) is None

    @pytest.mark.parametrize("bad", [0, -1])
    async def test_zero_or_negative_rejected(self, db, bad):
        await _worker(db, "5.0")
        assert await db.reserve_withdrawal(1, MIN, bad) is None

    async def test_exact_balance_allowed(self, db):
        await _worker(db, "5.0")
        _, _, amount = await db.reserve_withdrawal(1, MIN, ton_to_nano("5.0"))
        assert amount == ton_to_nano("5.0")
        assert (await db.get_worker(1)).balance_nano == 0


class TestPayout:
    async def test_partial_payout_sends_exact_amount(self, db):
        await _worker(db, "5.0")
        payer = FakePayer()
        result = await execute_payout(db, payer, 1, MIN, ton_to_nano("1.5"))
        assert result.is_paid and result.amount_nano == ton_to_nano("1.5")
        assert payer.sent[0][1] == ton_to_nano("1.5")
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("3.5")

    async def test_two_partials_in_sequence(self, db):
        await _worker(db, "5.0")
        payer = FakePayer()
        await execute_payout(db, payer, 1, MIN, ton_to_nano("2.0"))
        await execute_payout(db, payer, 1, MIN, ton_to_nano("1.0"))
        assert [a for _, a in payer.sent] == [ton_to_nano("2.0"), ton_to_nano("1.0")]
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("2.0")

    async def test_failed_partial_returns_exact_amount(self, db):
        await _worker(db, "5.0")
        result = await execute_payout(db, FakePayer(fail=True), 1, MIN, ton_to_nano("2.0"))
        assert result.status == "failed"
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("5.0")

    async def test_concurrent_partials_do_not_corrupt_balance(self, db):
        """Параллельные выводы по 2 из 5: каждый законен, но баланс не уходит в минус.

        От двойного нажатия защищает одноразовый флаг в хендлере — здесь
        проверяется только, что сам слой БД не выдаёт больше, чем есть.
        """
        await _worker(db, "5.0")
        payer = FakePayer()
        results = await asyncio.gather(
            *[execute_payout(db, payer, 1, MIN, ton_to_nano("2.0")) for _ in range(15)],
            return_exceptions=True,
        )
        assert [r for r in results if isinstance(r, Exception)] == []
        paid = [r for r in results if r.is_paid]
        assert len(paid) == 2                      # 5 = 2 + 2 + остаток 1
        worker = await db.get_worker(1)
        assert worker.balance_nano == ton_to_nano("1.0")
        assert sum(a for _, a in payer.sent) + worker.balance_nano == ton_to_nano("5.0")

    async def test_accounting_holds(self, db):
        await _worker(db, "5.0")
        payer = FakePayer()
        await execute_payout(db, payer, 1, MIN, ton_to_nano("1.5"))
        stats = await db.stats()
        assert stats["paid_total_nano"] + stats["total_balance_nano"] == ton_to_nano("5.0")
