"""Подарки: регистрация, выставление, продажа, доля воркеру."""
import os
import time

import pytest

from bot.gifts.pricing import PriceSource, decide_price, worker_share
from bot.gifts.service import GiftService
from bot.gifts.watcher import IncomingGift, parse_gift_action
from bot.utils import build_ton_address, ton_to_nano

pytestmark = pytest.mark.asyncio


# ─── Заглушки ──────────────────────────────────────────────────────────

from bot.gifts.market import InventoryItem


def item(slug="g-1", floor_bm=0, floor_col=0, locked=False, on_sale=False):
    return InventoryItem(
        market_id=f"m-{slug}", slug=slug, title="Plush Pepe",
        collection="Plush Pepe", model="Albino", backdrop="Black",
        floor_backdrop_model_nano=floor_bm, floor_collection_nano=floor_col,
        on_sale=on_sale, locked=locked, price_nano=0,
    )


class FakeDepositor:
    """Заглушка передачи подарка на аккаунт MRKT."""

    def __init__(self, fail=False):
        self.fail = fail
        self.sent: list[str] = []

    async def deposit(self, slug):
        if self.fail:
            raise RuntimeError("подарок ещё в кулдауне")
        self.sent.append(slug)


class FakeMarket:
    def __init__(self, items=None, comparable=0, fail_listing=False):
        self.items = {i.slug: i for i in (items or [])}
        self.comparable = comparable
        self.fail_listing = fail_listing
        self.listed: list[tuple[str, int]] = []

    async def inventory(self):
        return list(self.items.values())

    async def cheapest_comparable_nano(self, inv_item):
        return self.comparable

    async def list_for_sale(self, market_id, price_nano):
        if self.fail_listing:
            raise RuntimeError("маркет отказал")
        self.listed.append((market_id, price_nano))

    def sell(self, slug):
        """Имитирует покупку: подарок пропадает из инвентаря."""
        self.items.pop(slug, None)


class Cfg:
    worker_share_percent = 80
    undercut_percent = 3
    min_list_price_nano = ton_to_nano("0.5")
    allow_collection_floor = False


async def _worker(db, uid=1):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))


def gift(slug="g-1", sender=1, cooldown=0):
    return IncomingGift(slug, 900, "Plush Pepe", 7, sender, cooldown)


# ─── Расчёты ───────────────────────────────────────────────────────────

class TestPricing:
    MIN = ton_to_nano("0.5")

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

    async def test_uses_backdrop_model_floor(self):
        d = decide_price(ton_to_nano("12"), ton_to_nano("3"), 3, self.MIN, False)
        assert d.list_it and d.price_nano == ton_to_nano("11.64")
        assert d.source is PriceSource.BACKDROP_MODEL

    async def test_rare_backdrop_not_sold_at_collection_floor(self):
        """Ключевая защита: редкий фон стоит 40, коллекция 3 — продаём по 40."""
        d = decide_price(ton_to_nano("40"), ton_to_nano("3"), 3, self.MIN, False)
        assert d.price_nano == ton_to_nano("38.8")
        assert d.price_nano > ton_to_nano("3")

    async def test_refuses_when_only_collection_floor(self):
        """Флор коллекции занижает редкие атрибуты — вслепую не выставляем."""
        d = decide_price(0, ton_to_nano("3"), 3, self.MIN, False)
        assert not d.list_it and "редкие атрибуты" in d.reason

    async def test_collection_floor_allowed_explicitly(self):
        d = decide_price(0, ton_to_nano("3"), 3, self.MIN, True)
        assert d.list_it and d.source is PriceSource.COLLECTION

    async def test_refuses_without_any_floor(self):
        assert not decide_price(0, 0, 3, self.MIN, True).list_it

    async def test_refuses_below_minimum(self):
        d = decide_price(ton_to_nano("0.4"), 0, 3, self.MIN, False)
        assert not d.list_it and "ниже порога" in d.reason

    async def test_undercut_zero_lists_at_floor(self):
        d = decide_price(ton_to_nano("10"), 0, 0, self.MIN, False)
        assert d.price_nano == ton_to_nano("10")

    @pytest.mark.parametrize("bad", [-1, 100, 150])
    async def test_bad_undercut_rejected(self, bad):
        with pytest.raises(ValueError):
            decide_price(ton_to_nano("10"), 0, bad, self.MIN, False)



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

    async def test_hidden_name_still_reveals_sender(self):
        """name_hidden прячет имя от чужих в профиле, но не от получателя."""
        result = parse_gift_action(self._action("x-1", hidden=True), 111)
        assert result.from_user_id == 111 and result.is_attributed
        assert result.name_hidden is True

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
    async def test_lists_using_backdrop_model_floor(self, db):
        await _worker(db)
        market = FakeMarket([item("g-1", floor_bm=ton_to_nano("10"))])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()

        listed = await service.list_ready_gifts()
        assert [(r.slug, r.price_nano) for r in listed] == [("g-1", ton_to_nano("9.7"))]
        assert (await db.get_gift("g-1"))["status"] == "listed"

    async def test_rare_backdrop_not_dumped_at_collection_floor(self, db):
        """Главная защита: коллекция стоит 3, фон+модель 40 — выставляем по 40."""
        await _worker(db)
        market = FakeMarket([
            item("g-1", floor_bm=ton_to_nano("40"), floor_col=ton_to_nano("3"))
        ])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()

        (listing,) = await service.list_ready_gifts()
        assert listing.price_nano == ton_to_nano("38.8")

    async def test_without_narrow_floor_not_listed(self, db):
        """Есть только флор коллекции — выставлять вслепую нельзя."""
        await _worker(db)
        market = FakeMarket([item("g-1", floor_col=ton_to_nano("3"))])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()

        assert await service.list_ready_gifts() == []
        assert (await db.get_gift("g-1"))["status"] == "deposited"

    async def test_falls_back_to_comparable_search(self, db):
        """Если MRKT не отдал флор, берём самый дешёвый лот с теми же атрибутами."""
        await _worker(db)
        market = FakeMarket([item("g-1")], comparable=ton_to_nano("20"))
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()

        (listing,) = await service.list_ready_gifts()
        assert listing.price_nano == ton_to_nano("19.4")

    async def test_gift_in_cooldown_not_listed(self, db):
        await _worker(db)
        market = FakeMarket([item("g-1", floor_bm=ton_to_nano("10"))])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift(cooldown=int(time.time()) + 86400))
        assert await service.deposit_ready_gifts() == []
        assert await service.list_ready_gifts() == []

    async def test_locked_gift_not_listed(self, db):
        await _worker(db)
        market = FakeMarket([item("g-1", floor_bm=ton_to_nano("10"), locked=True)])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()
        assert await service.list_ready_gifts() == []

    async def test_unattributed_gift_not_listed(self, db):
        """Продав подарок без привязки, мы не узнаем, кому платить."""
        market = FakeMarket([item("g-1", floor_bm=ton_to_nano("10"))])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift(sender=None))
        assert await service.deposit_ready_gifts() == []
        assert await service.list_ready_gifts() == []

    async def test_gift_absent_from_market_waits(self, db):
        """Передан, но в инвентаре ещё не появился — ждём следующего круга."""
        await _worker(db)
        service = GiftService(db, Cfg(), FakeMarket([]), FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()
        assert await service.list_ready_gifts() == []
        assert (await db.get_gift("g-1"))["status"] == "deposited"

    async def test_deposit_failure_keeps_gift_for_retry(self, db):
        """Кулдаун или нехватка Stars — подарок остаётся, попробуем позже."""
        await _worker(db)
        service = GiftService(db, Cfg(), FakeMarket([]), FakeDepositor(fail=True))
        await service.register(gift())
        (slug, outcome), = await service.deposit_ready_gifts()
        assert slug == "g-1" and outcome != "deposited"
        assert (await db.get_gift("g-1"))["status"] == "received"

    async def test_listing_failure_keeps_state(self, db):
        await _worker(db)
        market = FakeMarket([item("g-1", floor_bm=ton_to_nano("10"))], fail_listing=True)
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()
        assert await service.list_ready_gifts() == []
        assert (await db.get_gift("g-1"))["status"] == "deposited"


# ─── Продажа и доля ────────────────────────────────────────────────────

class TestSales:
    async def _listed(self, db, floor="10"):
        await _worker(db)
        market = FakeMarket([item("g-1", floor_bm=ton_to_nano(floor))])
        service = GiftService(db, Cfg(), market, FakeDepositor())
        await service.register(gift())
        await service.deposit_ready_gifts()
        await service.list_ready_gifts()
        return market, service

    async def test_sale_credits_worker_share(self, db):
        market, service = await self._listed(db)
        market.sell("g-1")

        (sale,) = await service.collect_sales()
        price = ton_to_nano("9.7")
        assert sale.sold_nano == price
        assert sale.share_nano == worker_share(price, 80)
        assert (await db.get_worker(1)).balance_nano == sale.share_nano
        assert (await db.get_gift("g-1"))["status"] == "paid"

    async def test_unsold_gift_not_paid(self, db):
        _, service = await self._listed(db)
        assert await service.collect_sales() == []
        assert (await db.get_worker(1)).balance_nano == 0

    async def test_sale_counted_once(self, db):
        """Повторный опрос не должен начислить долю второй раз."""
        market, service = await self._listed(db)
        market.sell("g-1")
        await service.collect_sales()
        first = (await db.get_worker(1)).balance_nano
        assert await service.collect_sales() == []
        assert (await db.get_worker(1)).balance_nano == first

    async def test_house_keeps_remainder(self, db):
        market, service = await self._listed(db)
        market.sell("g-1")
        (sale,) = await service.collect_sales()
        assert sale.share_nano == worker_share(sale.sold_nano, 80)
        assert sale.sold_nano - sale.share_nano > 0


class TestSummary:
    async def test_counts_cooldown(self, db):
        await _worker(db)
        service = GiftService(db, Cfg())
        await service.register(gift("a", cooldown=int(time.time()) + 3600))
        await service.register(gift("b"))
        summary = await service.pending_summary()
        assert summary["received"] == 2
        assert summary["in_cooldown"] == 1
