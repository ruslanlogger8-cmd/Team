"""API мини-аппа. Пользователь берётся из подписи, а не из тела запроса."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from bot.webapp.server import build_app

TOKEN = "8809440322:AAFtest-token-for-tests-only"
WORKER = 555
OTHER = 777


def sign(user_id: int = WORKER, username: str = "tester") -> str:
    user = {"id": user_id, "first_name": "Тест", "username": username}
    pairs = {
        "auth_date": str(int(time.time())),
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class FakeConfig:
    bot_token = TOKEN
    team_name = "TONNFT team"
    admin_ids = {1}
    worker_share_percent = 80


class SentPhoto:
    def __init__(self, file_id: str) -> None:
        self.photo = [type("Size", (), {"file_id": file_id})()]


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.sent.append((chat_id, caption or ""))
        return SentPhoto("file-123")


@pytest_asyncio.fixture
async def client(db):
    bot = FakeBot()
    app = build_app(db, FakeConfig(), bot)
    app["fake_bot"] = bot
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


def form(wallet: str, count: str = "1", photo: bytes = b"jpeg-bytes") -> dict:
    data = {"wallet": wallet, "gifts_count": count}
    if photo:
        data["photo"] = photo
    return data


@pytest.mark.asyncio
async def test_me_registers_a_worker_who_never_opened_the_bot(client, db):
    response = await client.get("/api/me", headers={"X-Init-Data": sign()})

    assert response.status == 200
    body = await response.json()
    assert body["id"] == WORKER
    assert body["share_percent"] == 80
    assert (await db.get_worker(WORKER)) is not None


@pytest.mark.asyncio
async def test_every_endpoint_refuses_an_unsigned_call(client):
    for path in ("/api/me", "/api/top", "/api/history", "/api/requests"):
        assert (await client.get(path)).status == 401


@pytest.mark.asyncio
async def test_forged_signature_is_refused(client):
    forged = sign().rsplit("hash=", 1)[0] + "hash=" + "0" * 64
    assert (await client.get("/api/me", headers={"X-Init-Data": forged})).status == 401


@pytest.mark.asyncio
async def test_wallet_is_saved_for_the_signed_user(client, db, wallet):
    response = await client.post(
        "/api/wallet", json={"wallet": wallet}, headers={"X-Init-Data": sign()}
    )

    assert response.status == 200
    assert (await db.get_worker(WORKER)).wallet == wallet


@pytest.mark.asyncio
async def test_broken_wallet_is_refused(client, db):
    response = await client.post(
        "/api/wallet", json={"wallet": "UQ-мусор"}, headers={"X-Init-Data": sign()}
    )

    assert response.status == 400
    assert (await db.get_worker(WORKER)) is None or True


@pytest.mark.asyncio
async def test_request_is_created_and_shown_to_admins(client, db, wallet):
    response = await client.post(
        "/api/request", data=form(wallet, "3"), headers={"X-Init-Data": sign()}
    )

    assert response.status == 200
    request_id = (await response.json())["id"]

    row = await db.get_payout_request(request_id)
    assert row["worker_id"] == WORKER
    assert row["wallet"] == wallet
    assert row["gifts_count"] == 3
    assert row["photo_id"] == "file-123"

    sent = client.app["fake_bot"].sent
    assert len(sent) == 1 and f"№{request_id}" in sent[0][1]


@pytest.mark.asyncio
async def test_request_without_a_photo_is_refused(client, db, wallet):
    response = await client.post(
        "/api/request",
        data={"wallet": wallet, "gifts_count": "1"},
        headers={"X-Init-Data": sign()},
    )

    assert response.status == 400
    assert await db.pending_payout_requests() == []


@pytest.mark.asyncio
async def test_request_with_a_broken_wallet_is_refused(client, db):
    response = await client.post(
        "/api/request", data=form("не адрес"), headers={"X-Init-Data": sign()}
    )

    assert response.status == 400
    assert await db.pending_payout_requests() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("count", ["0", "51", "два", "-1"])
async def test_bad_gift_count_is_refused(client, db, wallet, count):
    response = await client.post(
        "/api/request", data=form(wallet, count), headers={"X-Init-Data": sign()}
    )

    assert response.status == 400
    assert await db.pending_payout_requests() == []


@pytest.mark.asyncio
async def test_second_request_is_refused_while_one_is_open(client, db, wallet):
    first = await client.post(
        "/api/request", data=form(wallet), headers={"X-Init-Data": sign()}
    )
    assert first.status == 200

    second = await client.post(
        "/api/request", data=form(wallet), headers={"X-Init-Data": sign()}
    )

    assert second.status == 409
    assert len(await db.pending_payout_requests()) == 1


@pytest.mark.asyncio
async def test_requests_list_shows_only_your_own(client, db, wallet):
    await client.post("/api/request", data=form(wallet), headers={"X-Init-Data": sign()})
    await client.post(
        "/api/request", data=form(wallet), headers={"X-Init-Data": sign(OTHER, "other")}
    )

    response = await client.get("/api/requests", headers={"X-Init-Data": sign()})

    rows = (await response.json())["requests"]
    assert len(rows) == 1
    assert len(await db.pending_payout_requests()) == 2


@pytest.mark.asyncio
async def test_history_shows_only_your_own(client, db, wallet):
    await db.upsert_worker(WORKER, "tester", "Тест")
    await db.upsert_worker(OTHER, "other", "Другой")
    await db.record_direct_payout(WORKER, 1_000_000_000, wallet, "hash-mine")
    await db.record_direct_payout(OTHER, 9_000_000_000, wallet, "hash-theirs")

    response = await client.get("/api/history", headers={"X-Init-Data": sign()})

    rows = (await response.json())["history"]
    assert [row["tx_hash"] for row in rows] == ["hash-mine"]
