"""Обёртка над tonutils: отправка TON с горячего кошелька.

Кошелёк инициализируется из seed-фразы (env). Версия кошелька настраивается,
т.к. from_mnemonic должна совпадать с реальной версией контракта, иначе
средства «не найдутся».
"""
from __future__ import annotations

from tonutils.client import ToncenterV3Client
from tonutils.wallet import WalletV3R2, WalletV4R2, WalletV5R1

from .config import Config
from .utils import nano_to_ton

_WALLET_CLASSES = {
    "v3r2": WalletV3R2,
    "v4r2": WalletV4R2,
    "v5r1": WalletV5R1,
}


class TonPayer:
    def __init__(self, config: Config) -> None:
        wallet_cls = _WALLET_CLASSES.get(config.wallet_version)
        if wallet_cls is None:
            raise RuntimeError(
                f"Неизвестная WALLET_VERSION={config.wallet_version!r}. "
                f"Допустимо: {', '.join(_WALLET_CLASSES)}"
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
