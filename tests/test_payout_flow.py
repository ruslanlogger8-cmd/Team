"""Полный цикл выплаты: резерв → отправка → успех/сбой, с заглушкой сети."""
import asyncio
import os

import pytest

from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio
MIN = ton_to_nano("0.1")


class FakePayer:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.sent = []

    async def send(self, destination, amount_nano):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("toncenter timeout")
        self.sent.append((destination, amount_nano))
        return f"tx_{len(self.sent)}"


async def payout(db, payer, user_id, min_nano=MIN):
    """Повторяет логику хендлера вывода."""
    reserved = await db.reserve_withdrawal(user_id, min_nano)
    if reserved is None:
        return None
    wid, wallet, amount = reserved
    try:
        tx = await payer.send(wallet, amount)
    except Exception as exc:
        await db.mark_failed_and_refund(wid, user_id, amount, repr(exc))
        return False
    await db.mark_paid(wid, tx)
    return True


async def _worker(db, uid=1, balance="2.0"):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))
    await db.credit(uid, ton_to_nano(balance), 9, "")


class TestHappyPath:
    async def test_successful_payout(self, db):
        await _worker(db, balance="2.5")
        payer = FakePayer()
        assert await payout(db, payer, 1) is True
        assert payer.sent[0][1] == ton_to_nano("2.5")
        assert (await db.get_worker(1)).balance_nano == 0
        assert (await db.stats())["paid_count"] == 1

    async def test_second_payout_without_balance(self, db):
        await _worker(db)
        payer = FakePayer()
        await payout(db, payer, 1)
        assert await payout(db, payer, 1) is None
        assert len(payer.sent) == 1


class TestFailure:
    async def test_network_failure_refunds(self, db):
        await _worker(db, balance="2.0")
        payer = FakePayer(fail_times=1)
        assert await payout(db, payer, 1) is False
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("2.0")
        assert payer.sent == []

    async def test_retry_after_failure_succeeds(self, db):
        await _worker(db, balance="2.0")
        payer = FakePayer(fail_times=1)
        await payout(db, payer, 1)
        assert await payout(db, payer, 1) is True
        assert payer.sent[0][1] == ton_to_nano("2.0")

    async def test_failed_payout_not_counted_as_paid(self, db):
        await _worker(db)
        await payout(db, FakePayer(fail_times=1), 1)
        assert (await db.stats())["paid_count"] == 0


class TestConcurrency:
    async def test_spam_click_sends_once(self, db):
        """Ключевая гарантия: сколько бы раз ни нажали, деньги уходят один раз."""
        await _worker(db, balance="5.0")
        payer = FakePayer()
        results = await asyncio.gather(
            *[payout(db, payer, 1) for _ in range(20)], return_exceptions=True
        )
        assert [r for r in results if isinstance(r, Exception)] == []
        assert results.count(True) == 1
        assert len(payer.sent) == 1
        assert payer.sent[0][1] == ton_to_nano("5.0")

    async def test_different_workers_are_independent(self, db):
        for uid in (1, 2, 3):
            await _worker(db, uid=uid, balance="1.0")
        payer = FakePayer()
        results = await asyncio.gather(*[payout(db, payer, uid) for uid in (1, 2, 3)])
        assert results == [True, True, True]
        assert len(payer.sent) == 3


class TestAccounting:
    async def test_no_money_created_or_lost(self, db):
        """Начислено = выплачено + осталось на балансах. Инвариант учёта."""
        credited = 0
        payer = FakePayer()
        for uid in range(1, 6):
            await db.upsert_worker(uid, "u", f"W{uid}")
            await db.set_wallet(uid, build_ton_address(os.urandom(32)))
            amount = ton_to_nano(str(uid))
            await db.credit(uid, amount, 9, "")
            credited += amount

        await payout(db, payer, 1)
        await payout(db, payer, 3)
        await payout(db, FakePayer(fail_times=1), 5)   # сбой → возврат

        stats = await db.stats()
        assert stats["paid_total_nano"] + stats["total_balance_nano"] == credited
