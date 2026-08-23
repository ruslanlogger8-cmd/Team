"""Обёртка над tonutils: отправка TON с горячего кошелька.

Кошелёк инициализируется из seed-фразы (env). Версия кошелька настраивается,
т.к. from_mnemonic должна совпадать с реальной версией контракта, иначе
средства «не найдутся».
"""
from __future__ import annotations

import logging
import secrets

from .config import Config
from .utils import fmt_ton, nano_to_ton

logger = logging.getLogger(__name__)

class DryRunPayer:
    """Заглушка для демо: ничего не отправляет, возвращает фейковый хэш.

    Включается через DRY_RUN=true. Кошелёк и seed-фраза не нужны.
    """

    def __init__(self) -> None:
        self.address = "DRY_RUN (кошелёк не подключён)"

    async def send(self, destination: str, amount_nano: int) -> str:
        fake_hash = "dryrun_" + secrets.token_hex(16)
        logger.warning(
            "DRY_RUN: выплата %s на %s НЕ отправлена, фейковый хэш %s",
            fmt_ton(amount_nano), destination, fake_hash,
        )
        return fake_hash


class TonPayer:
    def __init__(self, config: Config) -> None:
        # Импорт внутри — чтобы в DRY_RUN не требовался установленный tonutils.
        from tonutils.client import ToncenterV3Client
        from tonutils.wallet import WalletV3R2, WalletV4R2, WalletV5R1

        wallet_classes = {"v3r2": WalletV3R2, "v4r2": WalletV4R2, "v5r1": WalletV5R1}
        wallet_cls = wallet_classes.get(config.wallet_version)
        if wallet_cls is None:
            raise RuntimeError(
                f"Неизвестная WALLET_VERSION={config.wallet_version!r}. "
                f"Допустимо: {', '.join(wallet_classes)}"
            )
        client = ToncenterV3Client(
            is_testnet=config.is_testnet,
            api_key=config.toncenter_api_key or None,
        )
        wallet, _pub, _priv, _mnemonic = wallet_cls.from_mnemonic(client, config.wallet_mnemonic)
        self._wallet = wallet
        self._comment = config.payout_comment
        self.address = wallet.address.to_str()

    async def send(self, destination: str, amount_nano: int) -> str:
        """Отправляет amount_nano нанотонов на destination. Возвращает tx-хэш.

        Сетевая комиссия списывается с горячего кошелька сверх суммы.
        Бросает исключение при любой ошибке отправки — вызывающий делает возврат.
        """
        amount_ton = float(nano_to_ton(amount_nano))
        tx_hash = await self._wallet.transfer(
            destination=destination,
            amount=amount_ton,
            body=self._comment,
        )
        return str(tx_hash)


def create_payer(config: Config) -> "TonPayer | DryRunPayer":
    """Возвращает реальный отправитель TON либо заглушку, если DRY_RUN=true."""
    if config.dry_run:
        logger.warning("=" * 60)
        logger.warning("DRY_RUN включён — реальные выплаты НЕ отправляются")
        logger.warning("=" * 60)
        return DryRunPayer()
    return TonPayer(config)
