import os

import pytest

from bot.utils import (
    build_ton_address,
    fmt_ton,
    is_valid_ton_address,
    nano_to_ton,
    parse_ton,
    ton_to_nano,
)


class TestAmounts:
    @pytest.mark.parametrize("text,expected", [
        ("1", 1_000_000_000),
        ("1.5", 1_500_000_000),
        ("1,5", 1_500_000_000),
        (" 2 ", 2_000_000_000),
        ("0.000000001", 1),
        ("0.0000000004", 0),          # усечение вниз, не округление вверх
        ("1000000", 10**15),
    ])
    def test_parse_ton(self, text, expected):
        assert parse_ton(text) == expected

    @pytest.mark.parametrize("bad", ["", "   ", "abc", "1.2.3", "1e", "--5", "NaN", "Infinity"])
    def test_parse_ton_rejects_garbage(self, bad):
        with pytest.raises(ValueError):
            parse_ton(bad)

    @pytest.mark.parametrize("nano,text", [
        (1_500_000_000, "1.5 TON"),
        (1_000_000_000, "1 TON"),
        (50_000_000, "0.05 TON"),
        (0, "0 TON"),
        (1, "0.000000001 TON"),
    ])
    def test_fmt_ton(self, nano, text):
        assert fmt_ton(nano) == text

    def test_roundtrip(self):
        for value in ("0.1", "1", "12.345678901", "999999"):
            assert nano_to_ton(ton_to_nano(value)) == nano_to_ton(parse_ton(value))


class TestAddress:
    def test_generated_addresses_are_valid(self):
        for i in range(200):
            addr = build_ton_address(os.urandom(32), bounceable=i % 2 == 0)
            assert is_valid_ton_address(addr)

    def test_single_char_typo_is_rejected(self):
        """Контрольная сумма обязана ловить опечатку — иначе выплата уйдёт в никуда."""
        addr = build_ton_address(os.urandom(32))
        for pos in range(len(addr)):
            replacement = "A" if addr[pos] != "A" else "B"
            broken = addr[:pos] + replacement + addr[pos + 1:]
            assert not is_valid_ton_address(broken), f"опечатка на позиции {pos} прошла"

    def test_truncated_and_extended_rejected(self):
        addr = build_ton_address(os.urandom(32))
        assert not is_valid_ton_address(addr[:-1])
        assert not is_valid_ton_address(addr + "A")

    @pytest.mark.parametrize("bad", [
        "", "   ", "hello", "UQ" + "A" * 46, "UQshort",
        "0:" + "z" * 64, "not:anaddress", "0x" + "a" * 40,
    ])
    def test_garbage_rejected(self, bad):
        assert not is_valid_ton_address(bad)

    @pytest.mark.parametrize("raw", ["0:" + "a" * 64, "-1:" + "F" * 64, "0:" + "0" * 64])
    def test_raw_accepted(self, raw):
        assert is_valid_ton_address(raw)

    def test_whitespace_tolerated(self):
        addr = build_ton_address(os.urandom(32))
        assert is_valid_ton_address(f"  {addr}  ")
