"""Заявка воркера: разбор ссылки и защита от чужих подарков."""
import os

import pytest

from bot.gifts.claim import ClaimResult, parse_nft_slug, submit_claim
from bot.gifts.service import GiftService
from bot.gifts.watcher import IncomingGift
from bot.utils import build_ton_address



class Cfg:
    worker_share_percent = 80
    undercut_percent = 3
    min_list_price_nano = 500_000_000
    allow_collection_floor = False


async def _worker(db, uid):
    await db.upsert_worker(uid, "u", f"W{uid}")
    await db.set_wallet(uid, build_ton_address(os.urandom(32)))


async def _incoming(db, slug="PlushPepe-42", sender=None):
    service = GiftService(db, Cfg())
    await service.register(IncomingGift(slug, 900, "Plush Pepe", 7, sender, 0))


class TestParsing:
    @pytest.mark.parametrize("text,expected", [
        ("https://t.me/nft/PlushPepe-42", "PlushPepe-42"),
        ("http://t.me/nft/PlushPepe-42", "PlushPepe-42"),
        ("t.me/nft/SnoopDogg-1337", "SnoopDogg-1337"),
        ("https://telegram.me/nft/Lol-9", "Lol-9"),
        ("PlushPepe-42", "PlushPepe-42"),
        ("@PlushPepe-42", "PlushPepe-42"),
        ("  вот https://t.me/nft/Cap-7 держи ", "Cap-7"),
        ("HTTPS://T.ME/NFT/Upper-1", "Upper-1"),
    ])
    def test_recognised(self, text, expected):
        assert parse_nft_slug(text) == expected

    @pytest.mark.parametrize("text", [
        "", "   ", "мусор", "https://t.me/durov", "t.me/nft/", "просто текст", None,
    ])
    def test_rejected(self, text):
        assert parse_nft_slug(text) is None


@pytest.mark.asyncio
class TestClaim:
    async def test_attaches_unclaimed_gift(self, db):
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "https://t.me/nft/PlushPepe-42")
        assert claim.result is ClaimResult.ATTACHED and claim.ok
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 1

    async def test_repeat_claim_is_idempotent(self, db):
        await _worker(db, 1)
        await _incoming(db)
        await submit_claim(db, 1, "PlushPepe-42")
        again = await submit_claim(db, 1, "PlushPepe-42")
        assert again.result is ClaimResult.ALREADY_YOURS and again.ok

    async def test_cannot_take_someone_elses_gift(self, db):
        """Иначе выплату получил бы не тот, кто прислал подарок."""
        await _worker(db, 1)
        await _worker(db, 2)
        await _incoming(db, sender=1)
        claim = await submit_claim(db, 2, "PlushPepe-42")
        assert claim.result is ClaimResult.TAKEN and not claim.ok
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 1

    async def test_unknown_gift_rejected(self, db):
        """Заявить подарок, которого бот не получал, невозможно."""
        await _worker(db, 1)
        claim = await submit_claim(db, 1, "https://t.me/nft/Fake-1")
        assert claim.result is ClaimResult.NOT_FOUND and not claim.ok

    async def test_garbage_link_rejected(self, db):
        await _worker(db, 1)
        claim = await submit_claim(db, 1, "дай денег")
        assert claim.result is ClaimResult.BAD_LINK

    async def test_claim_does_not_create_money(self, db):
        """Заявка только привязывает — баланс меняется лишь при продаже."""
        await _worker(db, 1)
        await _incoming(db)
        before = (await db.get_worker(1)).balance_nano
        await submit_claim(db, 1, "PlushPepe-42")
        assert (await db.get_worker(1)).balance_nano == before

    async def test_claimed_gift_becomes_sellable(self, db):
        """Скрытый отправитель + заявка = подарок снова в обороте."""
        await _worker(db, 1)
        await _incoming(db, sender=None)          # имя скрыто
        service = GiftService(db, Cfg(), None, None)
        assert await service.deposit_ready_gifts() == []

        await submit_claim(db, 1, "PlushPepe-42")
        gift = await db.get_gift("PlushPepe-42")
        assert gift["worker_id"] == 1 and gift["status"] == "received"


@pytest.mark.asyncio
class TestWorkerGifts:
    async def test_lists_only_own(self, db):
        await _worker(db, 1)
        await _worker(db, 2)
        await _incoming(db, "A-1", sender=1)
        await _incoming(db, "B-2", sender=2)
        mine = await db.gifts_by_worker(1)
        assert [g["slug"] for g in mine] == ["A-1"]

    async def test_empty_for_new_worker(self, db):
        await _worker(db, 1)
        assert await db.gifts_by_worker(1) == []
