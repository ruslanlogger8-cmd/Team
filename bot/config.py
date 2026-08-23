"""Конфигурация из env. Секреты только через переменные окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} должен быть целым числом, а получено {raw!r}") from None
    if not low <= value <= high:
        raise RuntimeError(f"{name} должен быть в диапазоне {low}..{high}, а получено {value}")
    return value


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise RuntimeError(f"{name} должен быть числом, а получено {raw!r}") from None


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
    auto_payout: bool
    menu_photo: str
    team_name: str
    gifts_enabled: bool
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    worker_share_percent: int
    undercut_percent: int
    min_list_price_nano: int
    allow_collection_floor: bool
    gifts_poll_sec: int
    mrkt_workdir: str
    mrkt_deposit_account: str
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

        gifts_enabled = os.environ.get("GIFTS_ENABLED", "false").lower() in ("1", "true", "yes")
        api_id_raw = os.environ.get("TG_API_ID", "0").strip() or "0"
        try:
            api_id = int(api_id_raw)
        except ValueError:
            raise RuntimeError(f"TG_API_ID должен быть числом, а получено {api_id_raw!r}") from None

        if gifts_enabled:
            missing = [
                name for name, value in (
                    ("TG_API_ID", api_id),
                    ("TG_API_HASH", os.environ.get("TG_API_HASH", "").strip()),
                    ("TG_SESSION", os.environ.get("TG_SESSION", "").strip()),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "GIFTS_ENABLED=true требует " + ", ".join(missing)
                    + ". Получи их на my.telegram.org, сессию — скриптом scripts/gen_session.py"
                )

        share = _int_env("WORKER_SHARE_PERCENT", 80, 0, 100)

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
            wallet_version=os.environ.get("WALLET_VERSION", "auto").lower().strip() or "auto",
            is_testnet=os.environ.get("TON_TESTNET", "false").lower() in ("1", "true", "yes"),
            toncenter_api_key=os.environ.get("TONCENTER_API_KEY", "").strip(),
            min_withdraw_nano=int(round(min_ton * 1_000_000_000)),
            db_path=os.environ.get("DB_PATH", "payouts.db"),
            dry_run=dry_run,
            use_premium_emoji=os.environ.get("USE_PREMIUM_EMOJI", "true").lower() in ("1", "true", "yes"),
            auto_payout=os.environ.get("AUTO_PAYOUT", "false").lower() in ("1", "true", "yes"),
            menu_photo=os.environ.get("MENU_PHOTO", "assets/team.png").strip(),
            team_name=os.environ.get("TEAM_NAME", "TONNFT team").strip() or "TONNFT team",
            gifts_enabled=gifts_enabled,
            tg_api_id=api_id,
            tg_api_hash=os.environ.get("TG_API_HASH", "").strip(),
            tg_session=os.environ.get("TG_SESSION", "").strip(),
            worker_share_percent=share,
            undercut_percent=_int_env("UNDERCUT_PERCENT", 3, 0, 99),
            min_list_price_nano=int(round(_float_env("MIN_LIST_PRICE_TON", 0.5) * 1_000_000_000)),
            allow_collection_floor=os.environ.get("ALLOW_COLLECTION_FLOOR", "false").lower()
            in ("1", "true", "yes"),
            gifts_poll_sec=_int_env("GIFTS_POLL_SEC", 120, 30, 3600),
            mrkt_workdir=os.environ.get("MRKT_WORKDIR", "").strip()
            or os.path.dirname(os.path.abspath(os.environ.get("DB_PATH", "payouts.db"))),
            mrkt_deposit_account=os.environ.get("MRKT_DEPOSIT_ACCOUNT", "mrktbank").strip()
            .lstrip("@") or "mrktbank",
            payout_comment=os.environ.get("PAYOUT_COMMENT", "payout").strip() or "payout",
        )
