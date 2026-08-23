"""Отправка TON с горячего кошелька через tonutils 2.x.

API 2.x отличается от 0.x: модули переехали (tonutils.contracts.wallet,
tonutils.clients), сумма перевода задаётся в НАНОТОНАХ целым числом, а
transfer() не отправляет транзакцию, а собирает внешнее сообщение — его
отдельно публикует клиент.
"""
from __future__ import annotations

import logging
import secrets

from .config import Config
from .utils import fmt_ton

logger = logging.getLogger(__name__)


class DryRunPayer:
    """Заглушка для демо: ничего не отправляет, возвращает фейковый хэш."""

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
    """Реальные выплаты. Кошелёк поднимается из seed-фразы при старте."""

    def __init__(self, config: Config) -> None:
        from tonutils.clients import ToncenterClient
        from tonutils.clients.base import NetworkGlobalID
        from tonutils.contracts.wallet import WalletV3R2, WalletV4R2, WalletV5R1

        wallet_classes = {"v3r2": WalletV3R2, "v4r2": WalletV4R2, "v5r1": WalletV5R1}
        wallet_cls = wallet_classes.get(config.wallet_version)
        if wallet_cls is None:
            raise RuntimeError(
                f"Неизвестная WALLET_VERSION={config.wallet_version!r}. "
                f"Допустимо: {', '.join(wallet_classes)}"
            )

        network = NetworkGlobalID.TESTNET if config.is_testnet else NetworkGlobalID.MAINNET
        self._client = ToncenterClient(network, api_key=config.toncenter_api_key or None)

        wallet, _public, _private, _mnemonic = wallet_cls.from_mnemonic(
            self._client, config.wallet_mnemonic
        )
        self._wallet = wallet
        self._comment = config.payout_comment
        self._connected = False
        self.address = wallet.address.to_str()

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self._client.connect()
            self._connected = True

    async def send(self, destination: str, amount_nano: int) -> str:
        """Переводит amount_nano нанотонов. Возвращает хэш транзакции.

        Комиссия сети списывается с горячего кошелька сверх суммы, поэтому на
        нём нужен запас на газ. Любая ошибка пробрасывается — вызывающий код
        вернёт средства на баланс работника.
        """
        await self._ensure_connected()

        message = await self._wallet.transfer(
            destination=destination,
            amount=amount_nano,      # именно нанотоны, не TON
            body=self._comment,
        )
        await self._client.send_message(message.as_b64)

        tx_hash = message.normalized_hash
        if isinstance(tx_hash, (bytes, bytearray)):
            tx_hash = tx_hash.hex()
        return str(tx_hash)

    async def balance_nano(self) -> int:
        """Остаток на горячем кошельке — для проверки перед выплатами."""
        await self._ensure_connected()
        return int(await self._wallet.balance)

    async def close(self) -> None:
        if self._connected:
            await self._client.close()
            self._connected = False


def create_payer(config: Config) -> "TonPayer | DryRunPayer":
    """Возвращает реальный отправитель TON либо заглушку при DRY_RUN=true."""
    if config.dry_run:
        logger.warning("=" * 60)
        logger.warning("DRY_RUN включён — реальные выплаты НЕ отправляются")
        logger.warning("=" * 60)
        return DryRunPayer()
    return TonPayer(config)
