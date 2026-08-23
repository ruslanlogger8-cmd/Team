"""Доля с продажи должна уходить сразу, а не оседать на балансе."""
import os

import pytest

from bot.gifts.poller import _pay_share
from bot.gifts.service import SaleResult
from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio


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


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))


class Cfg:
    admin_ids = {999}
    auto_payout = True
    min_withdraw_nano = ton_to_nano("0.1")
    max_payout_nano = ton_to_nano("50")
    max_daily_payout_nano = ton_to_nano("300")
    worker_share_percent = 80


async def _worker(db, uid=1, balance="8.0"):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))
    await db.credit(uid, ton_to_nano(balance), 9, "доля")


def sale(worker_id=1, share="8.0"):
    return SaleResult(
        slug="Plush-42", title="Plush Pepe",
        sold_nano=ton_to_nano("10"), share_nano=ton_to_nano(share),
        worker_id=worker_id,
    )


class TestAutoMode:
    async def test_share_leaves_immediately(self, db):
        await _worker(db)
        payer, bot = FakePayer(), FakeBot()
        await _pay_share(bot, db, payer, Cfg(), sale())
        assert payer.sent[0][1] == ton_to_nano("8.0")
        assert (await db.get_worker(1)).balance_nano == 0

    async def test_worker_gets_transaction_hash(self, db):
        await _worker(db)
        bot = FakeBot()
        await _pay_share(bot, db, FakePayer(), Cfg(), sale())
        to_worker = [m for m in bot.messages if m[0] == 1]
        assert to_worker and "tx_1" in to_worker[0][1]

    async def test_no_wallet_keeps_money_on_balance(self, db):
        await db.upsert_worker(1, "u", "W1")
        await db.credit(1, ton_to_nano("8.0"), 9, "доля")
        payer = FakePayer()
        await _pay_share(FakeBot(), db, payer, Cfg(), sale())
        assert payer.sent == []
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("8.0")

    async def test_network_failure_returns_money(self, db):
        await _worker(db)
        await _pay_share(FakeBot(), db, FakePayer(fail=True), Cfg(), sale())
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("8.0")

    async def test_limit_blocks_and_keeps_money(self, db):
        await _worker(db, balance="80.0")
        cfg = Cfg()
        cfg.max_payout_nano = ton_to_nano("10")
        payer = FakePayer()
        await _pay_share(FakeBot(), db, payer, cfg, sale(share="80.0"))
        assert payer.sent == []
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("80.0")


class TestManualMode:
    async def test_admin_gets_button_instead_of_payout(self, db):
        """Ручной режим: деньги ждут нажатия, автоматом не уходят."""
        await _worker(db)
        cfg = Cfg()
        cfg.auto_payout = False
        payer, bot = FakePayer(), FakeBot()

        await _pay_share(bot, db, payer, cfg, sale())

        assert payer.sent == []
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("8.0")
        to_admin = [m for m in bot.messages if m[0] == 999]
        assert to_admin and to_admin[0][2] is not None   # кнопка приложена


class TestGuards:
    async def test_no_payer_does_nothing(self, db):
        await _worker(db)
        await _pay_share(FakeBot(), db, None, Cfg(), sale())
        assert (await db.get_worker(1)).balance_nano == ton_to_nano("8.0")
