"""Слой БД (aiosqlite). Балансы хранятся в нанотонах (int) — без float-ошибок.

Резервирование средств при выводе делается в одной транзакции:
списываем баланс и создаём заявку со статусом 'processing' атомарно,
поэтому двойной вывод невозможен даже при спаме кнопки или рестарте.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    full_name    TEXT,
    wallet       TEXT,
    balance_nano INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS withdrawals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    amount_nano  INTEGER NOT NULL,
    wallet       TEXT NOT NULL,
    status       TEXT NOT NULL,           -- processing | paid | failed
    tx_hash      TEXT,
    error        TEXT,
    created_at   INTEGER NOT NULL,
    finished_at  INTEGER
);
CREATE TABLE IF NOT EXISTS credits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    amount_nano INTEGER NOT NULL,
    admin_id    INTEGER NOT NULL,
    comment     TEXT,
    created_at  INTEGER NOT NULL
);
"""


@dataclass
class Worker:
    user_id: int
    username: str | None
    full_name: str
    wallet: str | None
    balance_nano: int


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        # aiosqlite держит одно соединение на весь процесс, поэтому все операции,
        # меняющие деньги, сериализуются этим локом. Без него параллельные заявки
        # ломают транзакции друг друга.
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database не подключена")
        return self._conn

    async def upsert_worker(self, user_id: int, username: str | None, full_name: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO workers (user_id, username, full_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                                               full_name=excluded.full_name
            """,
            (user_id, username, full_name, int(time.time())),
        )
        await self.conn.commit()

    async def get_worker(self, user_id: int) -> Worker | None:
        cur = await self.conn.execute(
            "SELECT user_id, username, full_name, wallet, balance_nano FROM workers WHERE user_id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return Worker(row["user_id"], row["username"], row["full_name"], row["wallet"], row["balance_nano"])

    async def set_wallet(self, user_id: int, wallet: str) -> None:
        await self.conn.execute("UPDATE workers SET wallet=? WHERE user_id=?", (wallet, user_id))
        await self.conn.commit()

    async def credit(self, user_id: int, amount_nano: int, admin_id: int, comment: str) -> int:
        """Начисляет баланс. Возвращает новый баланс. Работник должен существовать."""
        async with self._lock:
            cur = await self.conn.execute("SELECT balance_nano FROM workers WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if row is None:
                raise ValueError("worker_not_found")
            new_balance = row["balance_nano"] + amount_nano
            if new_balance < 0:
                raise ValueError("negative_balance")
            await self.conn.execute("UPDATE workers SET balance_nano=? WHERE user_id=?", (new_balance, user_id))
            await self.conn.execute(
                "INSERT INTO credits (user_id, amount_nano, admin_id, comment, created_at) VALUES (?,?,?,?,?)",
                (user_id, amount_nano, admin_id, comment, int(time.time())),
            )
            await self.conn.commit()
            return new_balance

    async def reserve_withdrawal(self, user_id: int, min_nano: int) -> tuple[int, str, str] | None:
        """Атомарно резервирует ВЕСЬ баланс под вывод.

        Возвращает (withdrawal_id, wallet, amount_nano) или None, если:
        кошелёк не задан, баланс < min, либо уже есть активная заявка.
        """
        async with self._lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                cur = await self.conn.execute(
                    "SELECT wallet, balance_nano FROM workers WHERE user_id=?", (user_id,)
                )
                row = await cur.fetchone()
                if row is None or not row["wallet"] or row["balance_nano"] < min_nano:
                    await self.conn.rollback()
                    return None

                cur = await self.conn.execute(
                    "SELECT COUNT(*) AS c FROM withdrawals WHERE user_id=? AND status='processing'",
                    (user_id,),
                )
                if (await cur.fetchone())["c"] > 0:
                    await self.conn.rollback()
                    return None

                amount = row["balance_nano"]
                wallet = row["wallet"]
                await self.conn.execute("UPDATE workers SET balance_nano=0 WHERE user_id=?", (user_id,))
                cur = await self.conn.execute(
                    "INSERT INTO withdrawals (user_id, amount_nano, wallet, status, created_at) "
                    "VALUES (?,?,?,'processing',?)",
                    (user_id, amount, wallet, int(time.time())),
                )
                withdrawal_id = cur.lastrowid
                await self.conn.commit()
                return withdrawal_id, wallet, amount
            except Exception:
                await self.conn.rollback()
                raise

    async def mark_paid(self, withdrawal_id: int, tx_hash: str) -> None:
        await self.conn.execute(
            "UPDATE withdrawals SET status='paid', tx_hash=?, finished_at=? WHERE id=?",
            (tx_hash, int(time.time()), withdrawal_id),
        )
        await self.conn.commit()

    async def mark_failed_and_refund(self, withdrawal_id: int, user_id: int, amount_nano: int, error: str) -> None:
        """Возврат зарезервированных средств на баланс при сбое выплаты."""
        async with self._lock:
            await self.conn.execute(
                "UPDATE withdrawals SET status='failed', error=?, finished_at=? WHERE id=?",
                (error[:500], int(time.time()), withdrawal_id),
            )
            await self.conn.execute(
                "UPDATE workers SET balance_nano=balance_nano+? WHERE user_id=?",
                (amount_nano, user_id),
            )
            await self.conn.commit()

    async def stats(self) -> dict[str, int]:
        cur = await self.conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(balance_nano),0) AS s FROM workers")
        w = await cur.fetchone()
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(amount_nano),0) AS s, COUNT(*) AS c FROM withdrawals WHERE status='paid'"
        )
        p = await cur.fetchone()
        return {
            "workers": w["c"],
            "total_balance_nano": w["s"],
            "paid_count": p["c"],
            "paid_total_nano": p["s"],
        }
