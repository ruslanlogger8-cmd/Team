"""Отправка TON: seqno читается у контракта, отказ по нему — один повтор."""
from __future__ import annotations

import pytest

from bot.ton import TonPayer, _is_seqno_mismatch


class FakeMessage:
    as_b64 = "boc"
    normalized_hash = "hash-1"


class FakeWallet:
    """Кошелёк, который отвергает всё, кроме своего текущего номера."""

    def __init__(self, chain_seqno: int = 7, uninit: bool = False) -> None:
        self.chain_seqno = chain_seqno
        self.is_uninit = uninit
        self.balance = 5_000_000_000
        self.seqno_calls = 0
        self.sent_with: list[int | None] = []
        self.seqno_fails = 0
        self._params_model = dict

    async def refresh(self) -> None:
        pass

    async def seqno(self) -> int:
        self.seqno_calls += 1
        if self.seqno_fails > 0:
            self.seqno_fails -= 1
            raise RuntimeError("toncenter 500")
        return self.chain_seqno

    async def transfer(self, destination, amount, body, params):
        self.sent_with.append(None if params is None else params.get("seqno"))
        return FakeMessage()


class FakeClient:
    def __init__(self, reject_first: bool = False) -> None:
        self.reject_first = reject_first
        self.sends = 0

    async def connect(self) -> None:
        pass

    async def send_message(self, boc: str) -> None:
        self.sends += 1
        if self.reject_first and self.sends == 1:
            raise RuntimeError(
                "cannot apply external message to current state : "
                "inbound external message rejected by transaction ABC: "
                "exitcode=33, steps=23, gas_used=0"
            )


def _payer(wallet: FakeWallet, client: FakeClient) -> TonPayer:
    payer = TonPayer.__new__(TonPayer)
    payer._wallet = wallet
    payer._client = client
    payer._connected = True
    payer._comment = "payout"
    payer.address = "EQtest"
    return payer


def test_detects_only_the_seqno_exit_code():
    assert _is_seqno_mismatch(RuntimeError("exitcode=33, steps=23"))
    assert not _is_seqno_mismatch(RuntimeError("exitcode=34"))
    assert not _is_seqno_mismatch(RuntimeError("exitcode=333"))


@pytest.mark.asyncio
async def test_seqno_comes_from_the_contract(monkeypatch):
    wallet, client = FakeWallet(chain_seqno=7), FakeClient()

    assert await _payer(wallet, client).send("EQdest", 1_000_000_000) == "hash-1"
    assert wallet.sent_with == [7]


@pytest.mark.asyncio
async def test_retries_once_with_a_fresh_seqno(monkeypatch):
    """Отказ по seqno означает, что сообщение не исполнилось — повтор безопасен."""
    monkeypatch.setattr("bot.ton.SEQNO_RETRY_DELAY_SEC", 0)
    wallet, client = FakeWallet(chain_seqno=7), FakeClient(reject_first=True)
    payer = _payer(wallet, client)

    assert await payer.send("EQdest", 1_000_000_000) == "hash-1"
    assert client.sends == 2
    assert wallet.seqno_calls == 2


@pytest.mark.asyncio
async def test_other_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr("bot.ton.SEQNO_RETRY_DELAY_SEC", 0)

    class Broken(FakeClient):
        async def send_message(self, boc: str) -> None:
            self.sends += 1
            raise RuntimeError("exitcode=34")

    wallet, client = FakeWallet(), Broken()
    with pytest.raises(RuntimeError, match="exitcode=34"):
        await _payer(wallet, client).send("EQdest", 1_000_000_000)
    assert client.sends == 1


@pytest.mark.asyncio
async def test_unreadable_seqno_on_a_live_wallet_is_an_error():
    """Молчаливый ноль здесь — гарантированный отказ контракта."""
    wallet, client = FakeWallet(uninit=False), FakeClient()
    wallet.seqno_fails = 99

    with pytest.raises(RuntimeError, match="seqno"):
        await _payer(wallet, client).send("EQdest", 1_000_000_000)
    assert client.sends == 0


@pytest.mark.asyncio
async def test_undeployed_wallet_sends_without_seqno():
    """Первая транзакция разворачивает контракт: номера ещё нет и это нормально."""
    wallet, client = FakeWallet(uninit=True), FakeClient()
    wallet.seqno_fails = 99

    assert await _payer(wallet, client).send("EQdest", 1_000_000_000) == "hash-1"
    assert wallet.sent_with == [None]
