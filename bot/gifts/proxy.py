"""Разбор адреса прокси для MTProto-клиентов.

Аккаунт-человек, работающий круглосуточно с серверного IP, — типовой повод
для заморозки. Прокси задаётся одной переменной TG_PROXY и подставляется
и вотчеру (Telethon), и клиенту MRKT (amrkt поверх pyrogram).

Формат: socks5://user:pass@host:port, socks5://host:port, http://host:port
"""
from __future__ import annotations

from urllib.parse import urlparse

SCHEMES = ("socks5", "socks4", "http")


def parse_proxy(url: str) -> dict | None:
    """URL прокси → словарь для Telethon. None, если прокси не задан.

    Бросает RuntimeError на кривом адресе: молча ходить напрямую нельзя —
    именно от прямого соединения прокси и должен защищать.
    """
    url = (url or "").strip()
    if not url:
        return None

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in SCHEMES:
        raise RuntimeError(
            f"TG_PROXY: схема {scheme or '—'!r} не поддерживается, "
            f"нужна одна из {', '.join(SCHEMES)}"
        )
    if not parsed.hostname or not parsed.port:
        raise RuntimeError(
            "TG_PROXY: нужен хост и порт, например "
            "socks5://user:pass@1.2.3.4:1080"
        )

    proxy = {
        "proxy_type": scheme,
        "addr": parsed.hostname,
        "port": int(parsed.port),
        "rdns": True,
    }
    if parsed.username:
        proxy["username"] = parsed.username
        proxy["password"] = parsed.password or ""
    return proxy
