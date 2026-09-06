"""Ручной режим: воркер подаёт заявку, деньги уходят по кнопке админа."""
from __future__ import annotations

import pytest

from bot.payout import approve_payout, reject_payout, request_payout

NANO = 1_000_000_000


class Payer:
    address = "EQtest"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, int]] = []

    async def send(self, destination: str, amount_nano: int) -> str:
        if self.fail:
            raise RuntimeError("сеть недоступна")
        self.sent.append((destination, amount_nano))
        return "hash-1"


async def _worker(db, wallet, balance_nano=5 * NANO):
    await db.upsert_worker(100, "vasya", "Вася")
    await db.set_wallet(100, wallet)
    await db.credit(100, balance_nano, 1, "тест")


@pytest.mark.asyncio
async def test_request_holds_money_without_sending(db, wallet):
    """Баланс списывается сразу: иначе те же деньги уйдут вторым путём."""
    await _worker(db, wallet)
    result = await request_payout(db, 100, NANO // 10)

    assert result.status == "held"
    assert result.amount_nano == 5 * NANO
    assert (await db.get_worker(100)).balance_nano == 0
    assert (await db.get_withdrawal(result.withdrawal_id))["status"] == "hold"


@pytest.mark.asyncio
async def test_second_request_is_refused_while_one_waits(db, wallet):
    await _worker(db, wallet)
    await db.credit(100, 5 * NANO, 1, "ещё")
    first = await request_payout(db, 100, NANO // 10, NANO)
    assert first.status == "held"

    second = await request_payout(db, 100, NANO // 10, NANO)
    assert second.status == "skipped"


@pytest.mark.asyncio
async def test_approve_sends_and_pays(db, wallet):
    await _worker(db, wallet)
    payer = Payer()
    held = await request_payout(db, 100, NANO // 10)

    result = await approve_payout(db, payer, held.withdrawal_id)

    assert result.status == "paid"
    assert result.tx_hash == "hash-1"
    assert payer.sent == [(wallet, 5 * NANO)]
    assert (await db.get_withdrawal(held.withdrawal_id))["status"] == "paid"


@pytest.mark.asyncio
async def test_double_tap_pays_once(db, wallet):
    """Вторая кнопка не должна отправить те же деньги ещё раз."""
    await _worker(db, wallet)
    payer = Payer()
    held = await request_payout(db, 100, NANO // 10)

    first = await approve_payout(db, payer, held.withdrawal_id)
    second = await approve_payout(db, payer, held.withdrawal_id)

    assert first.status == "paid"
    assert second.status == "skipped"
    assert len(payer.sent) == 1


@pytest.mark.asyncio
async def test_network_failure_returns_money(db, wallet):
    await _worker(db, wallet)
    held = await request_payout(db, 100, NANO // 10)

    result = await approve_payout(db, Payer(fail=True), held.withdrawal_id)

    assert result.status == "failed"
    assert (await db.get_worker(100)).balance_nano == 5 * NANO


@pytest.mark.asyncio
async def test_limit_blocks_and_returns_money(db, wallet):
    await _worker(db, wallet)
    payer = Payer()
    held = await request_payout(db, 100, NANO // 10)

    result = await approve_payout(db, payer, held.withdrawal_id, max_single_nano=NANO)

    assert result.status == "blocked"
    assert payer.sent == []
    assert (await db.get_worker(100)).balance_nano == 5 * NANO


@pytest.mark.asyncio
async def test_reject_returns_money(db, wallet):
    await _worker(db, wallet)
    held = await request_payout(db, 100, NANO // 10)

    result = await reject_payout(db, held.withdrawal_id, "отклонено администратором")

    assert result.status == "failed"
    assert (await db.get_worker(100)).balance_nano == 5 * NANO
    assert await db.held_withdrawals() == []


@pytest.mark.asyncio
async def test_rejected_request_cannot_be_paid_afterwards(db, wallet):
    await _worker(db, wallet)
    payer = Payer()
    held = await request_payout(db, 100, NANO // 10)
    await reject_payout(db, held.withdrawal_id, "отклонено")

    assert (await approve_payout(db, payer, held.withdrawal_id)).status == "skipped"
    assert payer.sent == []


@pytest.mark.asyncio
async def test_held_request_is_not_reported_as_stuck(db, wallet):
    """Заявка ждёт админа намеренно — тревожить перезапуском её не надо."""
    await _worker(db, wallet)
    held = await request_payout(db, 100, NANO // 10)
    await db.conn.execute(
        "UPDATE withdrawals SET created_at=created_at-9999 WHERE id=?",
        (held.withdrawal_id,),
    )
    await db.conn.commit()

    assert await db.find_stuck_withdrawals() == []
    assert len(await db.held_withdrawals()) == 1


@pytest.mark.asyncio
async def test_partial_request_leaves_the_rest(db, wallet):
    await _worker(db, wallet)
    held = await request_payout(db, 100, NANO // 10, 2 * NANO)

    assert held.amount_nano == 2 * NANO
    assert (await db.get_worker(100)).balance_nano == 3 * NANO
