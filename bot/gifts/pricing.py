"""Расчёт цены выставления и доли воркера.

Главный принцип: подарок не должен уйти дешевле своей реальной цены.
Флор берётся по связке «модель + фон», а не по коллекции — иначе редкий фон
продастся по цене рядового подарка из той же коллекции.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum


class PriceSource(str, Enum):
    BACKDROP_MODEL = "по модели и фону"
    COLLECTION = "по коллекции"
    NONE = "неизвестен"


@dataclass(frozen=True)
class PriceDecision:
    """Решение по цене. list_it=False означает «не выставлять»."""

    list_it: bool
    price_nano: int = 0
    floor_nano: int = 0
    source: PriceSource = PriceSource.NONE
    reason: str = ""


def worker_share(sold_price_nano: int, percent: int) -> int:
    """Доля воркера от суммы продажи, округление вниз в пользу кассы."""
    if sold_price_nano < 0:
        raise ValueError("отрицательная сумма продажи")
    if not 0 <= percent <= 100:
        raise ValueError(f"процент вне диапазона 0..100: {percent}")
    return int(
        (Decimal(sold_price_nano) * percent / 100).to_integral_value(rounding=ROUND_DOWN)
    )


def decide_price(
    floor_by_backdrop_model: int,
    floor_by_collection: int,
    undercut_percent: int,
    min_price_nano: int,
    allow_collection_floor: bool,
) -> PriceDecision:
    """Выбирает цену выставления или отказывается выставлять.

    Порядок источников:
      1. флор по модели и фону — единственный корректный ориентир
      2. флор по коллекции — только если это явно разрешено; он ниже реального
         для редких атрибутов, поэтому по умолчанию им не пользуемся
      3. ничего нет — не выставляем совсем

    Отказ выставить всегда безопаснее продажи вслепую: подарок полежит
    до следующего круга, а не уйдёт за бесценок.
    """
    if not 0 <= undercut_percent < 100:
        raise ValueError(f"недоцена вне диапазона 0..99: {undercut_percent}")

    if floor_by_backdrop_model > 0:
        floor, source = floor_by_backdrop_model, PriceSource.BACKDROP_MODEL
    elif floor_by_collection > 0 and allow_collection_floor:
        floor, source = floor_by_collection, PriceSource.COLLECTION
    elif floor_by_collection > 0:
        return PriceDecision(
            list_it=False,
            floor_nano=floor_by_collection,
            source=PriceSource.COLLECTION,
            reason=(
                "нет флора по модели и фону, а флор по коллекции занижает "
                "редкие атрибуты — выставлять вслепую нельзя"
            ),
        )
    else:
        return PriceDecision(
            list_it=False, reason="MRKT не отдал ни одного флора по этому подарку"
        )

    price = int(
        (Decimal(floor) * (100 - undercut_percent) / 100).to_integral_value(
            rounding=ROUND_DOWN
        )
    )

    if price < min_price_nano:
        # Ниже порога не выставляем: лучше пусть лежит, чем уйдёт за бесценок.
        return PriceDecision(
            list_it=False,
            floor_nano=floor,
            source=source,
            reason=f"цена {price} ниже порога {min_price_nano}",
        )

    return PriceDecision(list_it=True, price_nano=price, floor_nano=floor, source=source)
