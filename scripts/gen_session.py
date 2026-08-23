"""Генератор строковой сессии Telethon для вотчера подарков.

Запусти локально один раз:  python scripts/gen_session.py
Введёшь телефон и код — получишь строку для переменной TG_SESSION.

Строка сессии даёт полный доступ к аккаунту: храни её только в переменных
окружения, никогда не коммить и никому не пересылай.
"""
from __future__ import annotations

import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = os.environ.get("TG_API_ID") or input("TG_API_ID (my.telegram.org): ").strip()
    api_hash = os.environ.get("TG_API_HASH") or input("TG_API_HASH: ").strip()

    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        me = await client.get_me()
        print()
        print("=" * 70)
        print(f"Аккаунт: {me.first_name} @{me.username or '—'} (id {me.id})")
        print("=" * 70)
        print("TG_SESSION=" + client.session.save())
        print("=" * 70)
        print("Вставь строку в переменные Railway. Не коммить в репозиторий.")


if __name__ == "__main__":
    asyncio.run(main())
