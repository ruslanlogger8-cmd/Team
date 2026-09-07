"""Заявка на выплату: фото + адрес, решение админа, доля от суммы продажи."""
from __future__ import annotations

import time

import pytest

from bot.gifts.pricing import worker_share

NANO = 1_000_000_000


async def _worker(db, user_id=100, name="Вася"):
    await db.upsert_worker(user_id, "vasya", name)


@pytest.mark.asyncio
async def test_request_is_created_and_listed(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, "photo-1")

    rows = await db.pending_payout_requests()
    assert [row["id"] for row in rows] == [request_id]
    assert rows[0]["wallet"] == wallet
    assert rows[0]["photo_id"] == "photo-1"
    assert rows[0]["username"] == "vasya"


@pytest.mark.asyncio
async def test_second_request_is_blocked_while_one_is_open(db, wallet):
    await _worker(db)
    await db.add_payout_request(100, wallet, "photo-1")

    assert await db.has_open_payout_request(100) is True
    assert await db.has_open_payout_request(999) is False


@pytest.mark.asyncio
async def test_closed_request_frees_the_worker(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)
    await db.finish_payout_request(request_id, "rejected", note="отказ")

    assert await db.has_open_payout_request(100) is False


@pytest.mark.asyncio
async def test_only_the_first_tap_takes_the_request(db, wallet):
    """Второй админ (или второе нажатие) не должен запустить выплату повторно."""
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)

    assert await db.take_payout_request(request_id) is not None
    assert await db.take_payout_request(request_id) is None


@pytest.mark.asyncio
async def test_release_returns_the_request_to_the_queue(db, wallet):
    """Отмена на шаге суммы не закрывает заявку — воркер ничего не теряет."""
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)

    await db.release_payout_request(request_id)

    assert (await db.get_payout_request(request_id))["status"] == "pending"
    assert await db.take_payout_request(request_id) is not None


@pytest.mark.asyncio
async def test_release_does_not_reopen_a_paid_request(db, wallet):
    """Иначе сбой после отправки давал бы шанс заплатить второй раз."""
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)
    await db.finish_payout_request(request_id, "paid", NANO, NANO, "hash-1")

    await db.release_payout_request(request_id)

    assert (await db.get_payout_request(request_id))["status"] == "paid"


@pytest.mark.asyncio
async def test_paid_request_stores_sale_and_share(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)

    sale = 12 * NANO + NANO // 2          # 12.5 TON
    share = worker_share(sale, 80)        # 10 TON
    await db.finish_payout_request(request_id, "paid", sale, share, "hash-1")

    row = await db.get_payout_request(request_id)
    assert row["status"] == "paid"
    assert row["sale_nano"] == sale
    assert row["share_nano"] == share == 10 * NANO
    assert row["tx_hash"] == "hash-1"


@pytest.mark.asyncio
async def test_direct_payout_reaches_history_top_and_limit(db, wallet):
    """Выплата мимо баланса обязана считаться везде, где считаются деньги."""
    await _worker(db)
    await db.record_direct_payout(100, 10 * NANO, wallet, "hash-1")

    total, count = await db.worker_totals(100)
    assert (total, count) == (10 * NANO, 1)
    assert await db.paid_since(int(time.time()) - 60) == 10 * NANO
    assert await db.get_top() == [("Вася", 10 * NANO, 1)]
    _id, amount, status, tx_hash, _at = (await db.get_withdrawals(100))[0]
    assert (amount, status, tx_hash) == (10 * NANO, "paid", "hash-1")


@pytest.mark.asyncio
async def test_direct_payout_leaves_the_balance_alone(db, wallet):
    """Деньги идут от суммы продажи, а не с баланса — списывать нечего."""
    await _worker(db)
    await db.credit(100, 3 * NANO, 1, "старое")

    await db.record_direct_payout(100, 10 * NANO, wallet, "hash-1")

    assert (await db.get_worker(100)).balance_nano == 3 * NANO


@pytest.mark.asyncio
async def test_workers_are_listed_for_buttons(db):
    """Список для кнопок: сначала те, у кого есть баланс."""
    await _worker(db, 100, "Вася")
    await _worker(db, 200, "Петя")
    await db.credit(200, NANO, 1, "тест")

    rows = await db.all_workers()

    assert [worker_id for worker_id, _, _ in rows] == [200, 100]


@pytest.mark.asyncio
async def test_request_remembers_how_many_gifts(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, "photo-1", gifts_count=3)

    assert (await db.get_payout_request(request_id))["gifts_count"] == 3
    assert (await db.pending_payout_requests())[0]["gifts_count"] == 3


@pytest.mark.asyncio
async def test_single_gift_is_the_default(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)

    assert (await db.get_payout_request(request_id))["gifts_count"] == 1


@pytest.mark.asyncio
async def test_zero_is_stored_as_one(db, wallet):
    """Ноль подарков — бессмыслица, а нулём легко испортить подсчёт."""
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None, gifts_count=0)

    assert (await db.get_payout_request(request_id))["gifts_count"] == 1


@pytest.mark.asyncio
async def test_unknown_outcome_stays_closed_until_checked(db, wallet):
    """Заявка с неясным исходом не должна выплачиваться одним нажатием."""
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)
    await db.finish_payout_request(request_id, "failed", NANO, NANO, note="таймаут")

    assert await db.take_payout_request(request_id) is None
    assert await db.pending_payout_requests() == []


@pytest.mark.asyncio
async def test_reopen_returns_a_failed_request(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)
    await db.finish_payout_request(request_id, "failed", NANO, NANO, note="таймаут")

    assert await db.reopen_payout_request(request_id) is not None
    assert (await db.get_payout_request(request_id))["status"] == "pending"


@pytest.mark.asyncio
async def test_reopen_refuses_a_paid_request(db, wallet):
    """Открыть выплаченную заявку — это и есть двойной платёж."""
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)
    await db.finish_payout_request(request_id, "paid", NANO, NANO, "hash-1")

    assert await db.reopen_payout_request(request_id) is None
    assert (await db.get_payout_request(request_id))["status"] == "paid"


@pytest.mark.asyncio
async def test_reopen_refuses_a_rejected_request(db, wallet):
    await _worker(db)
    request_id = await db.add_payout_request(100, wallet, None)
    await db.take_payout_request(request_id)
    await db.finish_payout_request(request_id, "rejected", note="отказ")

    assert await db.reopen_payout_request(request_id) is None
