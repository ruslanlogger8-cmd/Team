"""Конфигурация из env. Секреты только через переменные окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_set(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            result.add(int(part))
    return result


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: set[int]
    wallet_mnemonic: list[str]
    wallet_version: str
    is_testnet: bool
    toncenter_api_key: str
    min_withdraw_nano: int
    db_path: str
    dry_run: bool
    payout_comment: str = field(default="payout")

    @staticmethod
    def load() -> "Config":
        token = os.environ.get("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не задан в env")

        dry_run = os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes")

        mnemonic_raw = os.environ.get("WALLET_MNEMONIC", "").strip()
        mnemonic = mnemonic_raw.split()
        if not dry_run and len(mnemonic) not in (24, 12):
            raise RuntimeError(
                "WALLET_MNEMONIC должен содержать 24 (или 12) слов seed-фразы горячего кошелька. "
                "Для демо без кошелька поставь DRY_RUN=true"
            )

        admins = _int_set(os.environ.get("ADMIN_IDS", ""))
        if not admins:
            raise RuntimeError("ADMIN_IDS не задан (id админов через запятую)")

        min_ton = float(os.environ.get("MIN_WITHDRAW_TON", "0.1"))
        return Config(
            bot_token=token,
            admin_ids=admins,
            wallet_mnemonic=mnemonic,
            wallet_version=os.environ.get("WALLET_VERSION", "v4r2").lower().strip(),
            is_testnet=os.environ.get("TON_TESTNET", "false").lower() in ("1", "true", "yes"),
            toncenter_api_key=os.environ.get("TONCENTER_API_KEY", "").strip(),
            min_withdraw_nano=int(round(min_ton * 1_000_000_000)),
            db_path=os.environ.get("DB_PATH", "payouts.db"),
            dry_run=dry_run,
            payout_comment=os.environ.get("PAYOUT_COMMENT", "payout").strip() or "payout",
        )
