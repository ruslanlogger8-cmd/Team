"""Конфигурация из env. Секреты только через переменные окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_set(raw: str, var_name: str) -> set[int]:
    result: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            raise RuntimeError(
                f"{var_name} должен содержать только числовые Telegram id через запятую "
                f"(например: 7712345678,8823456789), а получено {part!r}. "
                f"Похоже, значение попало не в ту переменную окружения."
            ) from None
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
    use_premium_emoji: bool
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

        admins = _int_set(os.environ.get("ADMIN_IDS", ""), "ADMIN_IDS")
        if not admins:
            raise RuntimeError("ADMIN_IDS не задан (id админов через запятую)")

        min_ton_raw = os.environ.get("MIN_WITHDRAW_TON", "0.1").strip() or "0.1"
        try:
            min_ton = float(min_ton_raw.replace(",", "."))
        except ValueError:
            raise RuntimeError(
                f"MIN_WITHDRAW_TON должен быть числом (например 0.1), а получено {min_ton_raw!r}"
            ) from None
        if min_ton <= 0:
            raise RuntimeError("MIN_WITHDRAW_TON должен быть больше нуля")
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
            use_premium_emoji=os.environ.get("USE_PREMIUM_EMOJI", "true").lower() in ("1", "true", "yes"),
            payout_comment=os.environ.get("PAYOUT_COMMENT", "payout").strip() or "payout",
        )
