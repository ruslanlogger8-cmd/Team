"""Ответы amrkt: инвентарь приходит моделью GiftList, а не списком."""
from __future__ import annotations

import pytest

from bot.gifts.market import Market, _gifts_of


class Gift:
    """Минимальная копия модели amrkt — только поля, которые читаем."""

    def __init__(self, gift_id: str, name: str, price: int = 0) -> None:
        self.id = gift_id
        self.name = name
        self.title = name
        self.collection_name = "PlushPepe"
        self.model_name = "Classic"
        self.backdrop_name = "Black"
        self.floor_price_by_backdrop_model = 12_000_000_000
        self.floor_price_by_collection = 5_000_000_000
        self.is_on_sale = False
        self.is_locked = False
        self.is_locked_for_sale = False
        self.sale_price = price


class GiftList:
    """Как pydantic-модель: подарки лежат в поле, а не в самом объекте."""

    def __init__(self, items: list[Gift]) -> None:
        self.items = items
        self.total = len(items)

    def __iter__(self):
        # Ровно то, что делает pydantic: пары (поле, значение).
        yield "items", self.items
        yield "total", self.total


class FakeClient:
    def __init__(self, result) -> None:
        self.result = result

    async def get_inventory(self, **kwargs):
        return self.result

    async def search_gifts(self, **kwargs):
        return self.result


def _market(result) -> Market:
    market = Market.__new__(Market)
    market._client = FakeClient(result)
    return market


def test_unwraps_a_model_with_items():
    gifts = [Gift("1", "PlushPepe-1"), Gift("2", "PlushPepe-2")]
    assert _gifts_of(GiftList(gifts)) == gifts


def test_unwraps_a_plain_list():
    gifts = [Gift("1", "PlushPepe-1")]
    assert _gifts_of(gifts) == gifts


def test_empty_answer_is_not_an_error():
    assert _gifts_of(None) == []
    assert _gifts_of(GiftList([])) == []


@pytest.mark.asyncio
async def test_inventory_reads_gifts_not_field_names():
    """Обход модели напрямую дал бы пары (поле, значение), и каждый подарок
    вышел бы пустым — инвентарь молча выглядел бы пустым."""
    market = _market(GiftList([Gift("m1", "PlushPepe-1"), Gift("m2", "PlushPepe-2")]))

    items = await market.inventory()

    assert [item.slug for item in items] == ["PlushPepe-1", "PlushPepe-2"]
    assert [item.market_id for item in items] == ["m1", "m2"]
    assert items[0].floor_backdrop_model_nano == 12_000_000_000
    assert items[0].sellable


@pytest.mark.asyncio
async def test_cheapest_comparable_reads_prices_from_the_model():
    market = _market(GiftList([Gift("1", "a", price=9), Gift("2", "b", price=4)]))
    item = (await _market(GiftList([Gift("m1", "PlushPepe-1")])).inventory())[0]

    assert await market.cheapest_comparable_nano(item) == 4
