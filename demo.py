"""Демо полного цикла выплат без Telegram и без реальной сети TON.

Запуск: python demo.py

Использует настоящий слой БД (bot/db.py) — то есть проверяется та же логика,
что работает в проде. Отправка TON подменена заглушкой, деньги никуда не уходят.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from bot.db import Database
from bot.utils import build_ton_address, fmt_ton, ton_to_nano

MIN_WITHDRAW = ton_to_nano("0.1")

ADMIN_ID = 500100
WORKERS = {
    777001: ("ivan", "Иван"),
    777002: ("petr", "Пётр"),
}


class FakeTonPayer:
    """Заглушка вместо реальной отправки TON. fail=True имитирует сбой сети."""

    def __init__(self) -> None:
        self.fail = False
        self.sent: list[tuple[str, int]] = []

    async def send(self, destination: str, amount_nano: int) -> str:
        if self.fail:
            raise ConnectionError("toncenter timeout")
        self.sent.append((destination, amount_nano))
        return f"tx_{len(self.sent):04d}_{amount_nano}"


def step(n: str, text: str) -> None:
    print(f"\n\033[1m{n}\033[0m {text}")


def line(text: str) -> None:
    print(f"   {text}")


async def payout(db: Database, payer: FakeTonPayer, user_id: int) -> None:
    """Повторяет логику хендлера вывода из bot/handlers/common.py."""
    reserved = await db.reserve_withdrawal(user_id, MIN_WITHDRAW)
    if reserved is None:
        line("❌ отказ: мало баланса, нет кошелька или есть активная заявка")
        return

    withdrawal_id, wallet, amount = reserved
    line(f"заявка #{withdrawal_id}: зарезервировано {fmt_ton(amount)} → {wallet[:12]}...")
    worker = await db.get_worker(user_id)
    line(f"баланс во время обработки: {fmt_ton(worker.balance_nano)} (списан заранее)")

    try:
        tx_hash = await payer.send(wallet, amount)
    except Exception as exc:
        await db.mark_failed_and_refund(withdrawal_id, user_id, amount, repr(exc))
        worker = await db.get_worker(user_id)
        line(f"⚠️  сбой отправки: {exc}")
        line(f"↩️  средства возвращены, баланс: {fmt_ton(worker.balance_nano)}")
        return

    await db.mark_paid(withdrawal_id, tx_hash)
    line(f"✅ отправлено {fmt_ton(amount)}, TX: {tx_hash}")


async def main() -> None:
    db_path = tempfile.mktemp(suffix=".db")
    db = Database(db_path)
    await db.connect()
    payer = FakeTonPayer()

    print("=" * 62)
    print("  ДЕМО: полный цикл выплат (БД настоящая, отправка TON — заглушка)")
    print("=" * 62)

    step("1.", "Работники нажимают /start")
    for uid, (username, name) in WORKERS.items():
        await db.upsert_worker(uid, username, name)
        line(f"{name} (id {uid}) зарегистрирован, баланс {fmt_ton(0)}")

    step("2.", "Работники задают TON-кошельки")
    wallets = {uid: build_ton_address(os.urandom(32)) for uid in WORKERS}
    for uid, wallet in wallets.items():
        await db.set_wallet(uid, wallet)
        line(f"{WORKERS[uid][1]}: {wallet[:16]}...")

    step("3.", "Админ начисляет баланс   /credit <id> <TON>")
    for uid, amount in ((777001, "2.5"), (777002, "0.05")):
        new_balance = await db.credit(uid, ton_to_nano(amount), ADMIN_ID, "за неделю")
        line(f"{WORKERS[uid][1]}: +{amount} TON → баланс {fmt_ton(new_balance)}")

    step("4.", "Иван жмёт «Вывести» — успешный путь")
    await payout(db, payer, 777001)

    step("5.", "Иван жмёт «Вывести» повторно — баланс уже 0")
    await payout(db, payer, 777001)

    step("6.", "Пётр жмёт «Вывести» — баланс ниже минимума 0.1 TON")
    await payout(db, payer, 777002)

    step("7.", "Пётр докидывает баланс и выводит, но сеть падает")
    await db.credit(777002, ton_to_nano("1.0"), ADMIN_ID, "доплата")
    payer.fail = True
    await payout(db, payer, 777002)

    step("8.", "Сеть починилась — Пётр выводит снова")
    payer.fail = False
    await payout(db, payer, 777002)

    step("9.", "Итог   /stats")
    stats = await db.stats()
    line(f"работников:              {stats['workers']}")
    line(f"баланс к выплате:        {fmt_ton(stats['total_balance_nano'])}")
    line(f"выплат проведено:        {stats['paid_count']}")
    line(f"выплачено всего:         {fmt_ton(stats['paid_total_nano'])}")
    line(f"реальных отправок:       {len(payer.sent)}")

    step("10.", "Как выглядит интерфейс")
    from bot.emoji import configure
    from bot.keyboards import main_menu, confirm_withdraw

    configure(False)
    print()
    print("   ┌─ Главное меню " + "─" * 30)
    for row in main_menu(is_admin=True).inline_keyboard:
        cells = []
        for b in row:
            colour = {"primary": "синяя", "success": "зелёная", "danger": "красная"}
            mark = f" [{colour[b.style]}]" if b.style else ""
            cells.append(f"{b.text}{mark}")
        print("   │ " + "   ".join(cells))
    print("   └" + "─" * 45)
    print()
    print("   ┌─ Подтверждение вывода " + "─" * 22)
    for row in confirm_withdraw("2.5 TON").inline_keyboard:
        for b in row:
            colour = {"primary": "синяя", "success": "зелёная", "danger": "красная"}
            print(f"   │ {b.text} [{colour.get(b.style, '—')}]")
    print("   └" + "─" * 45)

    top = await db.get_top(10)
    print()
    print("   ┌─ Топ-10 " + "─" * 36)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, (name, total, cnt) in enumerate(top, 1):
        print(f"   │ {medals.get(i, str(i) + '.')} {name} — {fmt_ton(total)} ({cnt})")
    print("   └" + "─" * 45)

    await db.close()
    os.remove(db_path)
    print("\n" + "=" * 62)
    print("  Демо завершено. Деньги никуда не уходили.")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
