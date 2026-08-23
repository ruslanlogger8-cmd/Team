"""Подарки: регистрация, выставление, продажа, доля воркеру."""
import os
import time

import pytest

from bot.gifts.pricing import listing_price, worker_share
from bot.gifts.service import GiftService
from bot.gifts.watcher import IncomingGift, parse_gift_action
from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio


# ─── Заглушки ──────────────────────────────────────────────────────────

class FakeItem:
    def __init__(self, gift_id, slug, title="", price=0):
        self.gift_id, self.slug, self.title = gift_id, slug, title
        self.on_sale, self.price_nano = bool(price), price


class FakeMarket:
    def __init__(self, items=None, floor=0, fail_listing=False):
        self.items = {i.slug: i for i in (items or [])}
        self.floor = floor
        self.fail_listing = fail_listing
        self.listed: list[tuple[str, int]] = []

    async def inventory(self):
        return list(self.items.values())

    async def floor_price_nano(self, title):
        return self.floor

    async def list_for_sale(self, market_gift_id, price_nano):
        if self.fail_listing:
            raise RuntimeError("маркет отказал")
        self.listed.append((market_gift_id, price_nano))
        return True

    async def sold_since(self, known_slugs):
        return [(s, 0) for s in known_slugs if s not in self.items]

    def sell(self, slug):
        """Имитирует покупку: подарок пропадает из инвентаря."""
        self.items.pop(slug, None)


class Cfg:
    worker_share_percent = 80
    undercut_percent = 3
    min_list_price_nano = ton_to_nano("0.5")


async def _worker(db, uid=1):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))


def gift(slug="g-1", sender=1, cooldown=0):
    return IncomingGift(slug, 900, "Plush Pepe", 7, sender, cooldown)


# ─── Расчёты ───────────────────────────────────────────────────────────

class TestPricing:
    @pytest.mark.parametrize("percent,expected", [(80, "8"), (70, "7"), (100, "10"), (0, "0")])
    async def test_share(self, percent, expected):
        assert worker_share(ton_to_nano("10"), percent) == ton_to_nano(expected)

    async def test_share_rounds_down(self):
        """Округление вверх съедало бы кассу на копейки с каждой сделки."""
        assert worker_share(3, 80) == 2

    @pytest.mark.parametrize("bad", [-1, 101])
    async def test_share_rejects_bad_percent(self, bad):
        with pytest.raises(ValueError):
            worker_share(100, bad)

    async def test_negative_sale_rejected(self):
        with pytest.raises(ValueError):
            worker_share(-1, 80)

    async def test_listing_undercuts_floor(self):
        assert listing_price(ton_to_nano("12"), 3, ton_to_nano("1")) == ton_to_nano("11.64")

    async def test_listing_respects_minimum(self):
        assert listing_price(ton_to_nano("0.4"), 3, ton_to_nano("1")) == ton_to_nano("1")

    async def test_unknown_floor_uses_minimum(self):
        assert listing_price(0, 3, ton_to_nano("1")) == ton_to_nano("1")


# ─── Разбор события ────────────────────────────────────────────────────

class TestParsing:
    def _action(self, slug=None, hidden=False, cooldown=0):
        gift_obj = type("G", (), {"slug": slug, "gift_id": 900, "title": "Plush"})()
        return type("A", (), {
            "gift": gift_obj, "name_hidden": hidden,
            "saved_id": 7, "can_resell_at": cooldown,
        })()

    async def test_plain_gift_without_slug_ignored(self):
        assert parse_gift_action(self._action(), 111) is None

    async def test_non_gift_ignored(self):
        assert parse_gift_action(object(), 111) is None

    async def test_unique_gift_parsed(self):
        result = parse_gift_action(self._action("plush-42"), 111)
        assert result.slug == "plush-42" and result.from_user_id == 111
        assert result.is_attributed

    async def test_hidden_sender_is_unattributed(self):
        """Скрытого отправителя привязать не к кому — платить наугад нельзя."""
        result = parse_gift_action(self._action("x-1", hidden=True), 111)
        assert result.from_user_id is None and not result.is_attributed

    async def test_cooldown_carried_over(self):
        assert parse_gift_action(self._action("y-2", cooldown=1893456000), 1).can_resell_at == 1893456000


# ─── Регистрация ───────────────────────────────────────────────────────

class TestRegister:
    async def test_registers_known_worker(self, db):
        await _worker(db)
        service = GiftService(db, Cfg())
        assert await service.register(gift()) == "registered"
        assert (await db.get_gift("g-1"))["worker_id"] == 1

    async def test_duplicate_slug_ignored(self, db):
        """Повтор означал бы вторую выплату за один подарок."""
        await _worker(db)
        service = GiftService(db, Cfg())
        assert await service.register(gift()) == "registered"
        assert await service.register(gift()) == "duplicate"

    async def test_hidden_sender_stored_unattributed(self, db):
        service = GiftService(db, Cfg())
        assert await service.register(gift(sender=None)) == "unattributed"
        assert (await db.get_gift("g-1"))["worker_id"] is None

    async def test_unknown_sender_stored_unattributed(self, db):
        service = GiftService(db, Cfg())
        assert await service.register(gift(sender=999)) == "unattributed"


# ─── Выставление ───────────────────────────────────────────────────────

class TestListing:
    async def test_lists_ready_gift_below_floor(self, db):
        await _worker(db)
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await service.register(gift())

        listed = await service.list_ready_gifts()
        assert listed == [("g-1", ton_to_nano("9.7"))]
        assert (await db.get_gift("g-1"))["status"] == "listed"

    async def test_gift_in_cooldown_not_listed(self, db):
        await _worker(db)
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await service.register(gift(cooldown=int(time.time()) + 86400))
        assert await service.list_ready_gifts() == []

    async def test_unattributed_gift_not_listed(self, db):
        """Продав подарок без привязки, мы не узнаем, кому платить."""
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await service.register(gift(sender=None))
        assert await service.list_ready_gifts() == []

    async def test_gift_absent_from_market_waits(self, db):
        await _worker(db)
        service = GiftService(db, Cfg(), FakeMarket([], floor=ton_to_nano("10")))
        await service.register(gift())
        assert await service.list_ready_gifts() == []
        assert (await db.get_gift("g-1"))["status"] == "received"

    async def test_listing_failure_keeps_state(self, db):
        await _worker(db)
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"), fail_listing=True)
        service = GiftService(db, Cfg(), market)
        await service.register(gift())
        assert await service.list_ready_gifts() == []
        assert (await db.get_gift("g-1"))["status"] == "received"


# ─── Продажа и доля ────────────────────────────────────────────────────

class TestSales:
    async def _listed(self, db, market, service):
        await _worker(db)
        await service.register(gift())
        await service.list_ready_gifts()

    async def test_sale_credits_worker_share(self, db):
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await self._listed(db, market, service)

        market.sell("g-1")
        sales = await service.collect_sales()

        price = ton_to_nano("9.7")
        share = worker_share(price, 80)
        assert sales == [("g-1", price, share)]
        assert (await db.get_worker(1)).balance_nano == share
        assert (await db.get_gift("g-1"))["status"] == "paid"

    async def test_unsold_gift_not_paid(self, db):
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await self._listed(db, market, service)
        assert await service.collect_sales() == []
        assert (await db.get_worker(1)).balance_nano == 0

    async def test_sale_counted_once(self, db):
        """Повторный опрос не должен начислить долю второй раз."""
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await self._listed(db, market, service)
        market.sell("g-1")

        await service.collect_sales()
        first = (await db.get_worker(1)).balance_nano
        assert await service.collect_sales() == []
        assert (await db.get_worker(1)).balance_nano == first

    async def test_house_keeps_remainder(self, db):
        """Воркеру 80%, кассе 20% — сумма сходится ровно."""
        market = FakeMarket([FakeItem("m1", "g-1")], floor=ton_to_nano("10"))
        service = GiftService(db, Cfg(), market)
        await self._listed(db, market, service)
        market.sell("g-1")
        (_, sold, share), = await service.collect_sales()
        assert share == worker_share(sold, 80)
        assert sold - share == sold - int(sold * 0.8)


class TestSummary:
    async def test_counts_cooldown(self, db):
        await _worker(db)
        service = GiftService(db, Cfg())
        await service.register(gift("a", cooldown=int(time.time()) + 3600))
        await service.register(gift("b"))
        summary = await service.pending_summary()
        assert summary["received"] == 2
        assert summary["in_cooldown"] == 1
