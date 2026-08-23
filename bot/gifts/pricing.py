"""Расчёт цены выставления и доли воркера."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN


def worker_share(sold_price_nano: int, percent: int) -> int:
    """Доля воркера от суммы продажи, округление вниз в пользу кассы.

    Округление вниз обязательно: вверх — и на дистанции касса уходит в минус
    на копейки с каждой сделки.
    """
    if sold_price_nano < 0:
        raise ValueError("отрицательная сумма продажи")
    if not 0 <= percent <= 100:
        raise ValueError(f"процент вне диапазона 0..100: {percent}")
    return int(
        (Decimal(sold_price_nano) * percent / 100).to_integral_value(rounding=ROUND_DOWN)
    )


def listing_price(floor_nano: int, undercut_percent: int, min_price_nano: int) -> int:
    """Цена выставления: чуть ниже флора, чтобы уходило быстро.

    Ниже min_price_nano не опускаемся — иначе подарок уйдёт дешевле, чем стоит
    возня с ним. Если флор неизвестен (0), выставляем по минимуму.
    """
    if floor_nano <= 0:
        return min_price_nano
    if not 0 <= undercut_percent < 100:
        raise ValueError(f"недоцена вне диапазона 0..99: {undercut_percent}")
    price = int(
        (Decimal(floor_nano) * (100 - undercut_percent) / 100).to_integral_value(
            rounding=ROUND_DOWN
        )
    )
    return max(price, min_price_nano)
