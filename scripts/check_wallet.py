"""Определяет версию контракта твоего кошелька по seed-фразе.

Из одной seed-фразы каждая версия кошелька даёт СВОЙ адрес. Скрипт выводит
адрес для каждой версии — сравни с тем, что показывает Tonkeeper, и совпавшая
строка и есть твоя WALLET_VERSION.

Запуск:
    export WALLET_MNEMONIC="слово1 слово2 ... слово24"
    python scripts/check_wallet.py

Ничего никуда не отправляет и не выходит в сеть для вычисления адресов.
"""
from __future__ import annotations

import asyncio
import os
import sys

from tonutils.clients import ToncenterClient
from tonutils.clients.base import NetworkGlobalID
from tonutils.contracts.wallet import WalletV3R2, WalletV4R2, WalletV5R1

VERSIONS = {
    "v3r2": WalletV3R2,
    "v4r2": WalletV4R2,
    "v5r1": WalletV5R1,
}


async def main() -> None:
    raw = os.environ.get("WALLET_MNEMONIC", "").strip()
    if not raw:
        raw = input("Seed-фраза (24 слова, через пробел): ").strip()

    mnemonic = raw.split()
    if len(mnemonic) not in (12, 24):
        print(f"Ожидалось 24 слова (или 12), получено {len(mnemonic)}.")
        sys.exit(1)

    testnet = os.environ.get("TON_TESTNET", "false").lower() in ("1", "true", "yes")
    network = NetworkGlobalID.TESTNET if testnet else NetworkGlobalID.MAINNET
    client = ToncenterClient(network, api_key=os.environ.get("TONCENTER_API_KEY") or None)

    print()
    print("=" * 72)
    print(f"  Адреса из твоей seed-фразы · сеть: {'testnet' if testnet else 'mainnet'}")
    print("=" * 72)

    for name, wallet_cls in VERSIONS.items():
        try:
            wallet, *_ = wallet_cls.from_mnemonic(client, mnemonic)
            print(f"  WALLET_VERSION={name:<6} {wallet.address.to_str()}")
        except Exception as exc:  # noqa: BLE001 — покажем, что именно не вышло
            print(f"  WALLET_VERSION={name:<6} ошибка: {exc}")

    print("=" * 72)
    print("  Открой Tonkeeper, скопируй адрес кошелька и найди его в списке выше.")
    print("  Слева от совпавшего адреса — значение для WALLET_VERSION.")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
