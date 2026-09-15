"""Полный путь заявки через настоящие хендлеры, а не через слой базы.

Здесь проверяется то, что увидит человек: воркер жмёт кнопки и присылает
фото, админ подтверждает и вводит сумму, TON уходят. Тесты на базу такой
путь не покрывают — между ними и кнопками лежат ровно те места, где всё
до сих пор и ломалось.
"""
from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import admin as admin_handlers
from bot.handlers import common as common_handlers

NANO = 1_000_000_000
WORKER = 555
ADMIN = 8135785574


class FakeUser:
    def __init__(self, user_id: int, username: str, name: str) -> None:
        self.id = user_id
        self.username = username
        self.full_name = name


class FakeBot:
    """Ловит всё, что бот отправил, — по этому и судим о поведении."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return FakeMessage(user=None, bot=self)

    async def send_photo(self, chat_id, photo, caption=None, **kwargs):
        self.sent.append((chat_id, caption or ""))
        return FakeMessage(user=None, bot=self)


class FakePhotoSize:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class FakeMessage:
    def __init__(self, user, bot, text: str = "", photo: list | None = None) -> None:
        self.from_user = user
        self.bot = bot
        self.text = text
        self.photo = photo
        self.replies: list[str] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.replies.append(text)
        if self.bot is not None:
            self.bot.sent.append((getattr(self.from_user, "id", 0), text))
        return self

    async def answer_photo(self, photo, caption=None, reply_markup=None, **kwargs):
        return await self.answer(caption or "")

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.replies.append(text)
        return self

    async def edit_caption(self, caption, reply_markup=None, **kwargs):
        self.replies.append(caption)
        return self


class FakeCall:
    def __init__(self, user, bot, data: str) -> None:
        self.from_user = user
        self.bot = bot
        self.data = data
        self.message = FakeMessage(user=user, bot=bot)
        self.alerts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False, **kwargs):
        # Хендлеры, работающие и с кнопкой, и с сообщением, зовут answer
        # по-разному. Заглушка принимает оба вида вызова.
        if text:
            self.alerts.append(text)


class FakePayer:
    address = "EQhot"

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.sent: list[tuple[str, int]] = []

    async def send(self, destination: str, amount_nano: int) -> str:
        if self.fail:
            raise RuntimeError(self.fail)
        self.sent.append((destination, amount_nano))
        return "0xhash"


class FakeConfig:
    admin_ids = {ADMIN}
    worker_share_percent = 80
    min_withdraw_nano = NANO // 10
    max_payout_nano = 50 * NANO
    max_daily_payout_nano = 300 * NANO
    dry_run = False
    team_name = "TONNFT team"
    webapp_url = ""
    team_chat_url = ""
    auto_payout = False
    withdraw_needs_approval = True
    menu_photo = ""


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def storage():
    return MemoryStorage()


def state_for(storage, user_id: int) -> FSMContext:
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id),
    )


async def file_request(db, bot, storage, wallet, count: str = "1") -> None:
    """Воркер проходит заявку так же, как пальцем по экрану."""
    user = FakeUser(WORKER, "vasya", "Вася")
    state = state_for(storage, WORKER)
    config = FakeConfig()

    await db.upsert_worker(WORKER, "vasya", "Вася")

    await common_handlers.payout_request_start(
        FakeCall(user, bot, "pr:new"), db, state
    )
    if count == "1":
        await common_handlers.payout_request_one(FakeCall(user, bot, "pr:one"), state)
    else:
        await common_handlers.payout_request_many(FakeCall(user, bot, "pr:many"), state)
        await common_handlers.payout_request_count(
            FakeMessage(user, bot, text=count), state
        )

    photo = FakeMessage(user, bot, photo=[FakePhotoSize("screenshot-1")])
    await common_handlers.payout_request_photo(photo, db, state)

    await common_handlers.payout_request_wallet(
        FakeMessage(user, bot, text=wallet), db, config, state
    )


async def approve(db, bot, storage, payer, request_id: int, sale: str) -> FakeCall:
    """Админ жмёт «Принять», вводит сумму, жмёт «Отправить»."""
    user = FakeUser(ADMIN, "garant", "Garant")
    state = state_for(storage, ADMIN)
    config = FakeConfig()

    await admin_handlers.request_accept(
        FakeCall(user, bot, f"pr:ok:{request_id}"), db, config, state
    )
    await admin_handlers.request_sale_amount(
        FakeMessage(user, bot, text=sale), db, config, state
    )
    pay = FakeCall(user, bot, f"pr:pay:{request_id}")
    await admin_handlers.request_pay(pay, db, config, payer, state)
    return pay


@pytest.mark.asyncio
async def test_worker_files_a_request_and_admin_pays_it(db, bot, storage, wallet):
    """Главный путь целиком: от кнопки воркера до TON на его кошельке."""
    await file_request(db, bot, storage, wallet)

    rows = await db.pending_payout_requests()
    assert len(rows) == 1
    request_id = rows[0]["id"]
    assert rows[0]["photo_id"] == "screenshot-1"
    assert rows[0]["wallet"] == wallet

    # Заявка с фото ушла админу.
    assert any(chat == ADMIN and f"№{request_id}" in text for chat, text in bot.sent)

    payer = FakePayer()
    await approve(db, bot, storage, payer, request_id, "12.5")

    # 80% от 12.5 — ровно 10 TON, и ушли они на адрес из заявки.
    assert payer.sent == [(wallet, 10 * NANO)]

    saved = await db.get_payout_request(request_id)
    assert saved["status"] == "paid"
    assert saved["sale_nano"] == 12 * NANO + NANO // 2
    assert saved["share_nano"] == 10 * NANO
    assert saved["tx_hash"] == "0xhash"

    # Воркеру пришёл хеш.
    assert any(chat == WORKER and "0xhash" in text for chat, text in bot.sent)

    # Выплата видна в истории и в топе.
    total, count = await db.worker_totals(WORKER)
    assert (total, count) == (10 * NANO, 1)
    assert await db.get_top() == [("Вася", 10 * NANO, 1)]


@pytest.mark.asyncio
async def test_several_gifts_go_in_one_request(db, bot, storage, wallet):
    await file_request(db, bot, storage, wallet, count="3")

    rows = await db.pending_payout_requests()
    assert rows[0]["gifts_count"] == 3

    payer = FakePayer()
    await approve(db, bot, storage, payer, rows[0]["id"], "20")

    assert payer.sent == [(wallet, 16 * NANO)]


@pytest.mark.asyncio
async def test_saved_wallet_is_offered_and_used(db, bot, storage, wallet):
    """Кошелёк уже сохранён — воркер жмёт кнопку, а не вводит адрес заново."""
    user = FakeUser(WORKER, "vasya", "Вася")
    state = state_for(storage, WORKER)
    config = FakeConfig()
    await db.upsert_worker(WORKER, "vasya", "Вася")
    await db.set_wallet(WORKER, wallet)

    await common_handlers.payout_request_start(FakeCall(user, bot, "pr:new"), db, state)
    await common_handlers.payout_request_one(FakeCall(user, bot, "pr:one"), state)
    await common_handlers.payout_request_photo(
        FakeMessage(user, bot, photo=[FakePhotoSize("screenshot-1")]), db, state
    )
    await common_handlers.payout_request_saved_wallet(
        FakeCall(user, bot, "pr:saved"), db, config, state
    )

    assert (await db.pending_payout_requests())[0]["wallet"] == wallet


@pytest.mark.asyncio
async def test_broken_address_does_not_create_a_request(db, bot, storage, wallet):
    user = FakeUser(WORKER, "vasya", "Вася")
    state = state_for(storage, WORKER)
    config = FakeConfig()
    await db.upsert_worker(WORKER, "vasya", "Вася")

    await common_handlers.payout_request_start(FakeCall(user, bot, "pr:new"), db, state)
    await common_handlers.payout_request_one(FakeCall(user, bot, "pr:one"), state)
    await common_handlers.payout_request_photo(
        FakeMessage(user, bot, photo=[FakePhotoSize("s")]), db, state
    )

    typo = FakeMessage(user, bot, text=wallet[:-1] + "X")
    await common_handlers.payout_request_wallet(typo, db, config, state)

    assert await db.pending_payout_requests() == []
    assert any("проверку" in reply for reply in typo.replies)


@pytest.mark.asyncio
async def test_second_tap_on_pay_does_not_pay_twice(db, bot, storage, wallet):
    await file_request(db, bot, storage, wallet)
    request_id = (await db.pending_payout_requests())[0]["id"]

    payer = FakePayer()
    await approve(db, bot, storage, payer, request_id, "12.5")

    # Повторное нажатие той же кнопки.
    again = FakeCall(FakeUser(ADMIN, "garant", "Garant"), bot, f"pr:pay:{request_id}")
    await admin_handlers.request_pay(
        again, db, FakeConfig(), payer, state_for(storage, ADMIN)
    )

    assert len(payer.sent) == 1


@pytest.mark.asyncio
async def test_worker_cannot_approve_his_own_request(db, bot, storage, wallet):
    """Иначе воркер сам себе подписывает любую сумму."""
    await file_request(db, bot, storage, wallet)
    request_id = (await db.pending_payout_requests())[0]["id"]

    worker = FakeUser(WORKER, "vasya", "Вася")
    call = FakeCall(worker, bot, f"pr:ok:{request_id}")
    await admin_handlers.request_accept(call, db, FakeConfig(), state_for(storage, WORKER))

    assert call.alerts == ["Нет доступа"]
    assert (await db.get_payout_request(request_id))["status"] == "pending"


@pytest.mark.asyncio
async def test_rejected_request_pays_nothing(db, bot, storage, wallet):
    await file_request(db, bot, storage, wallet)
    request_id = (await db.pending_payout_requests())[0]["id"]

    admin = FakeUser(ADMIN, "garant", "Garant")
    await admin_handlers.request_reject(
        FakeCall(admin, bot, f"pr:no:{request_id}"), db, FakeConfig(),
        state_for(storage, ADMIN),
    )

    assert (await db.get_payout_request(request_id))["status"] == "rejected"
    assert any(chat == WORKER and "отклонена" in text for chat, text in bot.sent)


@pytest.mark.asyncio
async def test_failed_transfer_keeps_the_request_closed_for_checking(
    db, bot, storage, wallet
):
    """Сеть молчит — платить повторно одним нажатием нельзя."""
    await file_request(db, bot, storage, wallet)
    request_id = (await db.pending_payout_requests())[0]["id"]

    payer = FakePayer(fail="timeout")
    call = await approve(db, bot, storage, payer, request_id, "12.5")

    assert (await db.get_payout_request(request_id))["status"] == "failed"
    assert await db.pending_payout_requests() == []
    assert any("Исход неизвестен" in reply for reply in call.message.replies)


@pytest.mark.asyncio
async def test_contract_refusal_returns_the_request_to_the_queue(
    db, bot, storage, wallet
):
    """Контракт отверг сообщение — деньги не двигались, заявка снова в очереди."""
    await file_request(db, bot, storage, wallet)
    request_id = (await db.pending_payout_requests())[0]["id"]

    payer = FakePayer(fail="rejected by transaction ABC: exitcode=33, steps=23")
    await approve(db, bot, storage, payer, request_id, "12.5")

    assert (await db.get_payout_request(request_id))["status"] == "pending"
    assert len(await db.pending_payout_requests()) == 1


@pytest.mark.asyncio
async def test_limit_stops_an_oversized_payout(db, bot, storage, wallet):
    await file_request(db, bot, storage, wallet)
    request_id = (await db.pending_payout_requests())[0]["id"]

    payer = FakePayer()
    call = await approve(db, bot, storage, payer, request_id, "1000")

    assert payer.sent == []
    assert (await db.get_payout_request(request_id))["status"] == "pending"
    assert any("лимит" in reply for reply in call.message.replies)
