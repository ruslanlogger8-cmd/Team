"""Оркестрация: подарок → выставление на MRKT → продажа → доля воркеру.

Состояния:
    received → listed → sold → paid
                 ↘ skipped (нет привязки, нет флора, ошибка)

Доля начисляется обычным db.credit, поэтому дальше работает та же выплатная
механика, что и для ручных начислений, включая автовыплату.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..config import Config
from ..db import Database
from .pricing import PriceDecision, PriceSource, decide_price, worker_share
from .watcher import IncomingGift

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListingResult:
    slug: str
    title: str
    price_nano: int
    floor_nano: int
    source: PriceSource


@dataclass(frozen=True)
class SaleResult:
    slug: str
    title: str
    sold_nano: int
    share_nano: int
    worker_id: int


class GiftService:
    def __init__(self, db: Database, config: Config, market=None, depositor=None) -> None:
        self._db = db
        self._config = config
        self._market = market
        self._depositor = depositor

    async def register(self, gift: IncomingGift) -> str:
        """Записывает поступивший подарок. Повторный slug отбрасывается —
        иначе за один подарок заплатили бы дважды."""
        worker_id = gift.from_user_id
        if worker_id is not None and await self._db.get_worker(worker_id) is None:
            worker_id = None

        row_id = await self._db.add_gift(
            slug=gift.slug,
            gift_id=gift.gift_id,
            title=gift.title,
            saved_id=gift.saved_id,
            worker_id=worker_id,
            can_resell_at=gift.can_resell_at,
        )
        if row_id is None:
            return "duplicate"
        return "registered" if worker_id is not None else "unattributed"

    async def _price_for(self, item) -> PriceDecision:
        """Решение по цене с запасным поиском сопоставимых лотов."""
        narrow = item.floor_backdrop_model_nano
        if narrow <= 0 and self._market is not None:
            narrow = await self._market.cheapest_comparable_nano(item)

        return decide_price(
            floor_by_backdrop_model=narrow,
            floor_by_collection=item.floor_collection_nano,
            undercut_percent=self._config.undercut_percent,
            min_price_nano=self._config.min_list_price_nano,
            allow_collection_floor=self._config.allow_collection_floor,
        )

    async def deposit_ready_gifts(self) -> list[tuple[str, str]]:
        """Передаёт на аккаунт MRKT подарки, вышедшие из кулдауна.

        Без этого шага подарок остаётся на аккаунте и на маркете не появится.
        Возвращает (slug, исход) — исход 'deposited' либо текст ошибки.
        """
        if self._depositor is None:
            return []

        results: list[tuple[str, str]] = []
        for row in await self._db.gifts_by_status("received", ready_only=True):
            slug = row["slug"]
            if row["worker_id"] is None:
                continue        # некому платить — не отдаём на маркет

            try:
                await self._depositor.deposit(slug)
            except Exception as exc:  # noqa: BLE001 — кулдаун, нехватка Stars и т.п.
                logger.warning("Подарок %s не передан на MRKT: %s", slug, exc)
                results.append((slug, str(exc)))
                continue

            await self._db.mark_gift_deposited(slug)
            results.append((slug, "deposited"))
        return results

    async def list_ready_gifts(self) -> list[ListingResult]:
        """Выставляет подарки, вышедшие из кулдауна и имеющие цену.

        Подарок без привязки к воркеру не выставляется никогда: продав его,
        мы не будем знать, кому платить.
        """
        if self._market is None:
            return []

        inventory = {item.slug: item for item in await self._market.inventory() if item.slug}
        results: list[ListingResult] = []

        for row in await self._db.gifts_by_status("deposited"):
            slug = row["slug"]
            if row["worker_id"] is None:
                continue

            item = inventory.get(slug)
            if item is None:
                continue        # ещё не доехал до MRKT
            if not item.sellable:
                continue        # залочен маркетом, ждём

            decision = await self._price_for(item)
            if not decision.list_it:
                logger.warning("Не выставляю %s: %s", slug, decision.reason)
                continue

            try:
                await self._market.list_for_sale(item.market_id, decision.price_nano)
            except Exception as exc:  # noqa: BLE001 — маркет мог отказать
                logger.warning("Не выставился %s: %s", slug, exc)
                continue

            await self._db.mark_gift_listed(slug, decision.price_nano)
            results.append(
                ListingResult(
                    slug=slug,
                    title=row["title"] or item.title,
                    price_nano=decision.price_nano,
                    floor_nano=decision.floor_nano,
                    source=decision.source,
                )
            )
        return results

    async def collect_sales(self) -> list[SaleResult]:
        """Находит проданные подарки и начисляет долю воркеру."""
        if self._market is None:
            return []

        listed = {row["slug"]: row for row in await self._db.gifts_by_status("listed")}
        if not listed:
            return []

        present = {item.slug for item in await self._market.inventory() if item.slug}
        results: list[SaleResult] = []

        for slug, row in listed.items():
            if slug in present:
                continue        # всё ещё в инвентаре — не продан

            sold_price = row["list_price_nano"]
            share = worker_share(sold_price, self._config.worker_share_percent)
            await self._db.mark_gift_sold(slug, sold_price, share)

            try:
                await self._db.credit(
                    row["worker_id"], share, 0, f"подарок {row['title'] or slug}"
                )
            except ValueError as exc:
                await self._db.mark_gift_skipped(slug, f"начисление не прошло: {exc}")
                logger.error("Доля за %s не начислена: %s", slug, exc)
                continue

            await self._db.mark_gift_paid(slug)
            results.append(
                SaleResult(
                    slug=slug,
                    title=row["title"] or slug,
                    sold_nano=sold_price,
                    share_nano=share,
                    worker_id=row["worker_id"],
                )
            )
        return results

    async def pending_summary(self) -> dict[str, int]:
        stats = await self._db.gift_stats()
        now = int(time.time())
        stats["in_cooldown"] = sum(
            1 for row in await self._db.gifts_by_status("received") if row["can_resell_at"] > now
        )
        return stats
