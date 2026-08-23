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

# Запас на комиссию сети. Реальная плата за перевод ~0.005–0.01 TON, берём
# с запасом: лучше отказать заранее с понятным текстом, чем поймать отказ
# от ноды посреди выплаты.
GAS_RESERVE_NANO = 50_000_000  # 0.05 TON


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


def _wallet_classes() -> dict:
    from tonutils.contracts.wallet import WalletV3R2, WalletV4R2, WalletV5R1

    return {"v3r2": WalletV3R2, "v4r2": WalletV4R2, "v5r1": WalletV5R1}


class TonPayer:
    """Реальные выплаты. Кошелёк поднимается из seed-фразы при старте.

    WALLET_VERSION=auto — версия определяется по блокчейну: из одной seed-фразы
    каждая версия даёт свой адрес, и активен на сети только настоящий.
    """

    def __init__(self, config: Config) -> None:
        from tonutils.clients import ToncenterClient
        from tonutils.clients.base import NetworkGlobalID

        classes = _wallet_classes()
        self._version = config.wallet_version
        if self._version != "auto" and self._version not in classes:
            raise RuntimeError(
                f"Неизвестная WALLET_VERSION={self._version!r}. "
                f"Допустимо: auto, {', '.join(classes)}"
            )

        network = NetworkGlobalID.TESTNET if config.is_testnet else NetworkGlobalID.MAINNET
        self._client = ToncenterClient(network, api_key=config.toncenter_api_key or None)
        self._mnemonic = config.wallet_mnemonic
        self._comment = config.payout_comment
        self._connected = False
        self._wallet = None

        if self._version == "auto":
            # Адреса известны сразу, а какой из них живой — выяснится при старте.
            self.address = "определяется по блокчейну…"
        else:
            wallet, *_ = classes[self._version].from_mnemonic(self._client, self._mnemonic)
            self._wallet = wallet
            self.address = wallet.address.to_str()

    async def _detect_version(self) -> None:
        """Находит версию кошелька, реально развёрнутую в сети.

        Проверяем все варианты и берём активный. Если активных несколько —
        тот, где больше денег: с него и платим.
        """
        classes = _wallet_classes()
        best_name, best_wallet, best_balance = None, None, -1

        for name, wallet_cls in classes.items():
            wallet, *_ = wallet_cls.from_mnemonic(self._client, self._mnemonic)
            try:
                await wallet.refresh()
                active = bool(wallet.is_active)
                balance = int(wallet.balance or 0)
            except Exception as exc:  # noqa: BLE001 — недоступную версию пропускаем
                logger.warning("Версия %s не проверилась: %s", name, exc)
                continue

            logger.info(
                "  %s → %s | активен: %s | баланс: %s",
                name, wallet.address.to_str(), active, fmt_ton(balance),
            )
            if active and balance > best_balance:
                best_name, best_wallet, best_balance = name, wallet, balance

        if best_wallet is None:
            raise RuntimeError(
                "Не удалось определить версию кошелька: ни один из адресов "
                "(v3r2, v4r2, v5r1) не активен в сети. Пополни кошелёк хотя бы "
                "на 0.01 TON — до первой транзакции контракт не развёрнут — "
                "либо задай WALLET_VERSION вручную."
            )

        self._version = best_name
        self._wallet = best_wallet
        self.address = best_wallet.address.to_str()
        logger.info(
            "Версия кошелька определена: WALLET_VERSION=%s, баланс %s",
            best_name, fmt_ton(best_balance),
        )

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self._client.connect()
            self._connected = True
        if self._wallet is None:
            logger.info("Определяю версию кошелька по seed-фразе:")
            await self._detect_version()

    async def prepare(self) -> None:
        """Ранняя проверка при старте: адрес в логе до первой выплаты."""
        await self._ensure_connected()

    async def send(self, destination: str, amount_nano: int) -> str:
        """Переводит amount_nano нанотонов. Возвращает хэш транзакции.

        Комиссия сети списывается с горячего кошелька сверх суммы, поэтому на
        нём нужен запас на газ. Любая ошибка пробрасывается — вызывающий код
        вернёт средства на баланс работника.
        """
        await self._ensure_connected()
        await self._wallet.refresh()

        balance = int(self._wallet.balance or 0)
        needed = amount_nano + GAS_RESERVE_NANO
        if balance < needed:
            raise RuntimeError(
                f"на горячем кошельке {fmt_ton(balance)}, а нужно минимум "
                f"{fmt_ton(needed)} — сумма выплаты плюс комиссия сети. "
                f"Пополни {self.address}"
            )

        # Адрес кошелька существует сразу, но сам контракт появляется в сети
        # только с первой ИСХОДЯЩЕЙ транзакцией. Пополнение его не разворачивает,
        # поэтому к первому переводу прикладываем код контракта.
        state_init = None
        if self._wallet.is_uninit:
            state_init = self._wallet.state_init
            logger.info(
                "Кошелёк %s ещё не развёрнут — первая выплата задеплоит контракт",
                self.address,
            )

        message = await self._wallet.transfer(
            destination=destination,
            amount=amount_nano,      # именно нанотоны, не TON
            body=self._comment,
            state_init=state_init,
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
