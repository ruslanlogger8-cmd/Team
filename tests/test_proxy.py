"""Разбор TG_PROXY: кривой адрес должен падать, а не уходить напрямую."""
from __future__ import annotations

import pytest

from bot.gifts.proxy import parse_proxy


def test_empty_means_no_proxy():
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None


def test_socks5_with_credentials():
    assert parse_proxy("socks5://user:pass@1.2.3.4:1080") == {
        "proxy_type": "socks5",
        "addr": "1.2.3.4",
        "port": 1080,
        "rdns": True,
        "username": "user",
        "password": "pass",
    }


def test_without_credentials_keys_are_absent():
    """Telethon не должен получать пустой логин — это не то же, что его отсутствие."""
    proxy = parse_proxy("socks5://1.2.3.4:1080")
    assert "username" not in proxy and "password" not in proxy


def test_http_scheme_allowed():
    assert parse_proxy("http://1.2.3.4:8080")["proxy_type"] == "http"


@pytest.mark.parametrize(
    "url",
    ["1.2.3.4:1080", "socks5://1.2.3.4", "ftp://1.2.3.4:21", "socks5://:1080"],
)
def test_broken_address_raises(url):
    """Тихий отказ означал бы прямое соединение — ровно то, от чего прокси нужен."""
    with pytest.raises(RuntimeError):
        parse_proxy(url)
