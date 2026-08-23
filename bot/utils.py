"""Утилиты: конвертация нанотонов, валидация TON-адреса, форматирование."""
from __future__ import annotations

import base64
import binascii
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN

NANO = 1_000_000_000

# raw-адрес: workchain:hex64
_RAW = re.compile(r"^(-?\d+):([0-9a-fA-F]{64})$")
# дружелюбный: 48 символов base64 / base64url
_FRIENDLY_CHARS = re.compile(r"^[A-Za-z0-9_\-+/]{48}$")


def ton_to_nano(amount_ton: float | str | Decimal) -> int:
    """TON → нанотоны. Бросает InvalidOperation на нечисловом вводе."""
    return int((Decimal(str(amount_ton)) * NANO).to_integral_value(rounding=ROUND_DOWN))


def nano_to_ton(amount_nano: int) -> Decimal:
    return (Decimal(amount_nano) / NANO).quantize(Decimal("0.000000001"))


def fmt_ton(amount_nano: int) -> str:
    """Человекочитаемая сумма: 1.5 TON, 0.05 TON, 12 TON."""
    value = nano_to_ton(amount_nano).normalize()
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text or '0'} TON"


def parse_ton(text: str) -> int:
    """Разбирает пользовательский ввод суммы в нанотоны.

    Принимает '1.5', '1,5', ' 2 ', '0.000000001'. Бросает ValueError на мусоре.
    """
    cleaned = text.strip().replace(",", ".").replace(" ", "")
    if not cleaned:
        raise ValueError("пустая сумма")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"не число: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"не число: {text!r}")
    return int((value * NANO).to_integral_value(rounding=ROUND_DOWN))


def _crc16_xmodem(data: bytes) -> int:
    """CRC16-CCITT (XMODEM) — контрольная сумма в дружелюбном TON-адресе."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def is_valid_ton_address(address: str) -> bool:
    """Проверяет TON-адрес, включая контрольную сумму.

    Без проверки CRC опечатка в адресе выглядит валидной, и выплата уходит
    в несуществующий кошелёк безвозвратно. Поддерживает дружелюбный формат
    (base64/base64url, 48 символов) и raw (workchain:hex64).
    """
    address = address.strip()
    if not address:
        return False

    raw = _RAW.match(address)
    if raw:
        workchain = int(raw.group(1))
        return -128 <= workchain <= 127

    if not _FRIENDLY_CHARS.match(address):
        return False

    try:
        decoded = base64.urlsafe_b64decode(address.replace("+", "-").replace("/", "_"))
    except (binascii.Error, ValueError):
        return False

    if len(decoded) != 36:
        return False

    body, checksum = decoded[:34], decoded[34:]
    return _crc16_xmodem(body) == int.from_bytes(checksum, "big")


def build_ton_address(account_id: bytes, workchain: int = 0, bounceable: bool = True) -> str:
    """Собирает корректный дружелюбный адрес. Используется в тестах."""
    if len(account_id) != 32:
        raise ValueError("account_id должен быть 32 байта")
    tag = 0x11 if bounceable else 0x51
    body = bytes([tag, workchain & 0xFF]) + account_id
    body += _crc16_xmodem(body).to_bytes(2, "big")
    return base64.urlsafe_b64encode(body).decode()
