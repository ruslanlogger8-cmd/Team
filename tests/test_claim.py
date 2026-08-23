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
    async def test_free_gift_goes_to_approval_not_to_claimer(self, db):
        """Слаг публичный, поэтому свободный подарок не отдаём по первой просьбе."""
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "https://t.me/nft/PlushPepe-42")
        assert claim.result is ClaimResult.PENDING
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] is None

    async def test_second_claim_on_same_gift_rejected(self, db):
        """Пока заявка висит, второй воркер не может подать свою."""
        await _worker(db, 1)
        await _worker(db, 2)
        await _incoming(db)
        assert (await submit_claim(db, 1, "PlushPepe-42")).result is ClaimResult.PENDING
        assert (await submit_claim(db, 2, "PlushPepe-42")).result is ClaimResult.DUPLICATE

    async def test_approval_attaches_gift(self, db):
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "PlushPepe-42")
        await db.resolve_claim_request(claim.request_id, approved=True)
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 1

    async def test_rejection_leaves_gift_free(self, db):
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "PlushPepe-42")
        await db.resolve_claim_request(claim.request_id, approved=False)
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] is None

    async def test_request_cannot_be_resolved_twice(self, db):
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "PlushPepe-42")
        assert await db.resolve_claim_request(claim.request_id, approved=True) is not None
        assert await db.resolve_claim_request(claim.request_id, approved=True) is None

    async def test_direct_mode_attaches_immediately(self, db):
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "PlushPepe-42", needs_approval=False)
        assert claim.result is ClaimResult.ATTACHED
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 1

    async def test_concurrent_direct_claims_attach_once(self, db):
        """Условный UPDATE не даёт двум заявкам пройти одновременно."""
        import asyncio
        await _worker(db, 1)
        await _worker(db, 2)
        await _incoming(db)
        results = await asyncio.gather(
            submit_claim(db, 1, "PlushPepe-42", needs_approval=False),
            submit_claim(db, 2, "PlushPepe-42", needs_approval=False),
        )
        attached = [r for r in results if r.result is ClaimResult.ATTACHED]
        assert len(attached) == 1

    async def test_repeat_claim_is_idempotent(self, db):
        await _worker(db, 1)
        await _incoming(db, sender=1)
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

        claim = await submit_claim(db, 1, "PlushPepe-42")
        await db.resolve_claim_request(claim.request_id, approved=True)
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


@pytest.mark.asyncio
class TestEvidence:
    """Юзернейм отправителя и скриншот сохраняются вместе с заявкой."""

    async def test_evidence_stored(self, db):
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(
            db, 1, "PlushPepe-42",
            sender_username="@ivan_petrov", photo_id="AgACAgIAAx123",
        )
        request = await db.get_claim_request(claim.request_id)
        assert request["sender_username"] == "@ivan_petrov"
        assert request["photo_id"] == "AgACAgIAAx123"

    async def test_evidence_visible_in_pending_list(self, db):
        await _worker(db, 1)
        await _incoming(db)
        await submit_claim(db, 1, "PlushPepe-42", sender_username="@ivan", photo_id="ph1")
        (row,) = await db.pending_claim_requests()
        assert row["sender_username"] == "@ivan" and row["photo_id"] == "ph1"

    async def test_missing_evidence_still_recorded(self, db):
        """Прямой режим без подтверждения не требует доказательств."""
        await _worker(db, 1)
        await _incoming(db)
        claim = await submit_claim(db, 1, "PlushPepe-42", needs_approval=False)
        assert claim.result is ClaimResult.ATTACHED


class TestUsername:
    @pytest.mark.parametrize("text,expected", [
        ("@ivan_petrov", "@ivan_petrov"),
        ("ivan_petrov", "@ivan_petrov"),
        ("https://t.me/ivan_petrov", "@ivan_petrov"),
        ("  @Ivan_2  ", "@Ivan_2"),
    ])
    def test_recognised(self, text, expected):
        from bot.gifts.claim import parse_username
        assert parse_username(text) == expected

    @pytest.mark.parametrize("bad", ["", "@ab", "мусор", "@" + "x" * 40, "123abc"])
    def test_rejected(self, bad):
        from bot.gifts.claim import parse_username
        assert parse_username(bad) is None


@pytest.mark.asyncio
class TestAutoVerification:
    """Telegram сообщает получателю отправителя — сверяем заявку с ним."""

    async def test_sender_matches_claimer_auto_approved(self, db):
        await _worker(db, 1)
        await _incoming(db, sender=1)
        # подарок уже привязан автоматически
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 1
        claim = await submit_claim(db, 1, "PlushPepe-42")
        assert claim.result is ClaimResult.ALREADY_YOURS

    async def test_other_account_verified_by_username(self, db):
        """Воркер отправлял со второго своего аккаунта и честно его назвал."""
        await _worker(db, 1)
        await _incoming(db, sender=555)          # 555 не заводил бота
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] is None

        async def resolve(username):
            return 555 if username == "@my_second" else None

        claim = await submit_claim(
            db, 1, "PlushPepe-42",
            sender_username="@my_second", resolve_username=resolve,
        )
        assert claim.result is ClaimResult.VERIFIED
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 1

    async def test_wrong_username_rejected(self, db):
        """Назвал чужой аккаунт — Telegram говорит иное, заявка ложная."""
        await _worker(db, 1)
        await _incoming(db, sender=555)

        async def resolve(username):
            return 999

        claim = await submit_claim(
            db, 1, "PlushPepe-42",
            sender_username="@not_mine", resolve_username=resolve,
        )
        assert claim.result is ClaimResult.TAKEN
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] is None

    async def test_claiming_someone_elses_gift_rejected(self, db):
        """Чужой подарок не заберёшь, даже с красивым скриншотом."""
        await _worker(db, 1)
        await _worker(db, 2)
        await _incoming(db, sender=2)

        async def resolve(username):
            return 1

        claim = await submit_claim(
            db, 1, "PlushPepe-42",
            sender_username="@me", resolve_username=resolve, photo_id="fake",
        )
        assert claim.result is ClaimResult.TAKEN
        assert (await db.get_gift("PlushPepe-42"))["worker_id"] == 2

    async def test_unknown_sender_still_goes_to_admin(self, db):
        """Отправителя нет в данных — только тогда нужен человек."""
        await _worker(db, 1)
        await _incoming(db, sender=None)
        claim = await submit_claim(db, 1, "PlushPepe-42", sender_username="@ivan")
        assert claim.result is ClaimResult.PENDING
