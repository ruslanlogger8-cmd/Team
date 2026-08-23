"""Создаёт сессию для MRKT и печатает её в виде строки для Railway.

Запусти локально один раз:
    export TG_API_ID=...
    export TG_API_HASH=...
    python scripts/gen_mrkt_session.py

Введёшь телефон и код — скрипт авторизуется, заберёт получившийся файл
сессии и выведет его в base64. Эту строку кладёшь в MRKT_SESSION_B64.

Сессия даёт полный доступ к аккаунту: только в переменные окружения,
никогда в репозиторий.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path

SESSION_NAME = "mrkt"


async def main() -> None:
    api_id = os.environ.get("TG_API_ID") or input("TG_API_ID (my.telegram.org): ").strip()
    api_hash = os.environ.get("TG_API_HASH") or input("TG_API_HASH: ").strip()

    try:
        from amrkt import MarketClient
    except ImportError:
        print("Сначала: pip install amrkt")
        sys.exit(1)

    workdir = Path(".").resolve()
    path = workdir / f"{SESSION_NAME}.session"
    if path.exists():
        path.unlink()

    print("\nСейчас Telegram спросит телефон и код подтверждения.\n")
    async with MarketClient(
        api_id=int(api_id), api_hash=api_hash,
        session_name=SESSION_NAME, workdir=str(workdir),
    ) as client:
        me = await client.get_user_info()
        print(f"\nАвторизован: {me}")

    if not path.exists():
        print(f"Файл сессии не появился по пути {path} — проверь workdir.")
        sys.exit(1)

    encoded = base64.b64encode(path.read_bytes()).decode()
    print()
    print("=" * 72)
    print("MRKT_SESSION_B64=" + encoded)
    print("=" * 72)
    print("Скопируй строку целиком в переменные Railway.")
    print(f"Локальный файл {path} после этого можно удалить.")


if __name__ == "__main__":
    asyncio.run(main())
