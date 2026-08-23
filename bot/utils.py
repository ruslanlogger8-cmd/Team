"""Утилиты: конвертация нанотонов, валидация адреса, форматирование."""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_DOWN

NANO = 1_000_000_000

# Дружелюбный (base64url, 48 симв.) либо raw (workchain:hex64) адрес TON.
_FRIENDLY = re.compile(r"^[A-Za-z0-9_-]{48}$")
_RAW = re.compile(r"^-?\d:[0-9a-fA-F]{64}$")


def ton_to_nano(amount_ton: float | str | Decimal) -> int:
    return int((Decimal(str(amount_ton)) * NANO).to_integral_value(rounding=ROUND_DOWN))


def nano_to_ton(amount_nano: int) -> Decimal:
    return (Decimal(amount_nano) / NANO).quantize(Decimal("0.000000001"))


def fmt_ton(amount_nano: int) -> str:
    value = nano_to_ton(amount_nano).normalize()
    text = format(value, "f")
    return f"{text} TON"


def is_valid_ton_address(address: str) -> bool:
    address = address.strip()
    return bool(_FRIENDLY.match(address) or _RAW.match(address))
