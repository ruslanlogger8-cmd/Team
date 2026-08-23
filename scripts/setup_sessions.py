"""Создаёт обе сессии для подсистемы подарков за один запуск.

Нужны два разных доступа к одному твоему аккаунту:
    TG_SESSION        — вотчер подарков (Telethon)
    MRKT_SESSION_B64  — работа с маркетом (amrkt поверх pyrogram)

Запуск:
    pip install telethon amrkt
    export TG_API_ID=...
    export TG_API_HASH=...
    python scripts/setup_sessions.py

Телефон и код спросят дважды — это нормально, авторизации разные.
Обе строки дают полный доступ к аккаунту: только в переменные окружения,
никогда в репозиторий и никому в переписку.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path

SESSION_NAME = "mrkt"


def _credentials() -> tuple[int, str]:
    api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    if not api_id:
        api_id = input("TG_API_ID (my.telegram.org): ").strip()
    if not api_hash:
        api_hash = input("TG_API_HASH: ").strip()
    try:
        return int(api_id), api_hash
    except ValueError:
        print(f"TG_API_ID должен быть числом, а получено {api_id!r}")
        sys.exit(1)


async def make_telethon_session(api_id: int, api_hash: str) -> str:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    print("\n[1/2] Вотчер подарков — Telethon\n")
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        print(f"      Вошёл как @{me.username or me.id}")
        return client.session.save()


async def make_mrkt_session(api_id: int, api_hash: str) -> str:
    from amrkt import MarketClient

    print("\n[2/2] Доступ к MRKT — amrkt\n")
    workdir = Path(".").resolve()
    path = workdir / f"{SESSION_NAME}.session"
    if path.exists():
        path.unlink()

    async with MarketClient(
        api_id=api_id, api_hash=api_hash,
        session_name=SESSION_NAME, workdir=str(workdir),
    ) as client:
        await client.get_user_info()
        print("      Авторизация в MRKT прошла")

    if not path.exists():
        print(f"      Файл сессии не появился: {path}")
        sys.exit(1)

    encoded = base64.b64encode(path.read_bytes()).decode()
    path.unlink()
    return encoded


async def main() -> None:
    api_id, api_hash = _credentials()

    try:
        tg_session = await make_telethon_session(api_id, api_hash)
        mrkt_session = await make_mrkt_session(api_id, api_hash)
    except ImportError as exc:
        print(f"\nНе хватает зависимости: {exc}\nПоставь: pip install telethon amrkt")
        sys.exit(1)

    print("\n" + "=" * 74)
    print("  Вставь это в Railway → Variables → Raw Editor")
    print("=" * 74)
    print(f"GIFTS_ENABLED=true")
    print(f"TG_API_ID={api_id}")
    print(f"TG_API_HASH={api_hash}")
    print(f"TG_SESSION={tg_session}")
    print(f"MRKT_SESSION_B64={mrkt_session}")
    print("WORKER_SHARE_PERCENT=80")
    print("UNDERCUT_PERCENT=3")
    print("MIN_LIST_PRICE_TON=0.5")
    print("=" * 74)
    print("  Каждая строка — одной строкой, без переносов.")
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(main())
