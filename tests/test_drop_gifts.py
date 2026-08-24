"""Снятие подарков, оставшихся на потерянном аккаунте."""
from __future__ import annotations

import pytest


async def _gift(db, slug: str, status: str) -> None:
    await db.add_gift(slug, 1, slug, None, 100, 0)
    if status != "received":
        await db.conn.execute("UPDATE gifts SET status=? WHERE slug=?", (status, slug))
        await db.conn.commit()


@pytest.mark.asyncio
async def test_drop_all_leaves_money_statuses_alone(db):
    """Проданные и выплаченные не трогаем: доля по ним уже начислена."""
    await _gift(db, "a-1", "received")
    await _gift(db, "a-2", "deposited")
    await _gift(db, "a-3", "listed")
    await _gift(db, "a-4", "sold")
    await _gift(db, "a-5", "paid")

    assert await db.drop_unfinished_gifts("аккаунт заморожен") == 3

    assert (await db.get_gift("a-1"))["status"] == "skipped"
    assert (await db.get_gift("a-1"))["note"] == "аккаунт заморожен"
    assert (await db.get_gift("a-4"))["status"] == "sold"
    assert (await db.get_gift("a-5"))["status"] == "paid"


@pytest.mark.asyncio
async def test_drop_one_by_slug(db):
    await _gift(db, "b-1", "received")
    await _gift(db, "b-2", "received")

    assert await db.drop_unfinished_gifts("не тот", slug="b-1") == 1
    assert (await db.get_gift("b-1"))["status"] == "skipped"
    assert (await db.get_gift("b-2"))["status"] == "received"


@pytest.mark.asyncio
async def test_drop_sold_slug_reports_nothing_changed(db):
    """Ноль строк — сигнал хендлеру, что подарок снимать нельзя."""
    await _gift(db, "c-1", "sold")
    assert await db.drop_unfinished_gifts("поздно", slug="c-1") == 0


@pytest.mark.asyncio
async def test_dropped_gift_leaves_the_work_queue(db):
    """Снятый подарок больше не попадает в круг передачи на маркет."""
    await _gift(db, "d-1", "received")
    await db.drop_unfinished_gifts("аккаунт заморожен")
    assert await db.gifts_by_status("received", ready_only=True) == []
