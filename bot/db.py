"""Слой БД (aiosqlite). Балансы хранятся в нанотонах (int) — без float-ошибок.

Резервирование средств при выводе делается в одной транзакции:
списываем баланс и создаём заявку со статусом 'processing' атомарно,
поэтому двойной вывод невозможен даже при спаме кнопки или рестарте.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import sqlite3

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
CREATE TABLE IF NOT EXISTS gifts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT UNIQUE,             -- StarGiftUnique.slug, ключ для MRKT
    gift_id        INTEGER,
    title          TEXT,
    saved_id       INTEGER,
    worker_id      INTEGER,                 -- NULL, если отправитель скрыт
    status         TEXT NOT NULL,           -- received | deposited | listed | sold | paid | skipped
    can_resell_at  INTEGER NOT NULL DEFAULT 0,
    list_price_nano INTEGER NOT NULL DEFAULT 0,
    sold_price_nano INTEGER NOT NULL DEFAULT 0,
    share_nano     INTEGER NOT NULL DEFAULT 0,
    note           TEXT,
    received_at    INTEGER NOT NULL,
    sold_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_gifts_status ON gifts(status);
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
    """Слой доступа к SQLite.

    ИНВАРИАНТ: каждый метод, который пишет и коммитит, обязан держать self._lock.
    aiosqlite использует одно соединение на процесс, поэтому коммит без лока
    закрывает чужую открытую транзакцию — деньги при этом теряются или задваиваются.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        # aiosqlite держит одно соединение на весь процесс, поэтому все операции,
        # меняющие деньги, сериализуются этим локом. Без него параллельные заявки
        # ломают транзакции друг друга.
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        # Каталог для базы может отсутствовать (например, volume в Railway
        # прописан в DB_PATH, но не примонтирован) — создаём заранее.
        parent = os.path.dirname(os.path.abspath(self._path))
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Не удалось создать каталог для базы: {parent} ({exc}). "
                f"Если это Railway — подключи Volume с mount path {parent}, "
                f"либо укажи DB_PATH=payouts.db для запуска без тома."
            ) from None

        try:
            self._conn = await aiosqlite.connect(self._path)
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"Не удалось открыть базу {self._path} ({exc}). "
                f"Проверь, что каталог {parent} существует и доступен на запись. "
                f"В Railway это Settings → Volumes → Add Volume с mount path {parent}."
            ) from None
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
        async with self._lock:
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
        async with self._lock:
            await self.conn.execute(
                "UPDATE workers SET wallet=? WHERE user_id=?", (wallet, user_id)
            )
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
        async with self._lock:
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

    # ─── Подарки ──────────────────────────────────────────────────────

    async def add_gift(
        self,
        slug: str,
        gift_id: int,
        title: str,
        saved_id: int | None,
        worker_id: int | None,
        can_resell_at: int,
    ) -> int | None:
        """Регистрирует поступивший подарок. None — если такой slug уже был.

        Повторная регистрация означала бы вторую выплату за один подарок,
        поэтому slug уникален на уровне схемы.
        """
        async with self._lock:
            cur = await self.conn.execute("SELECT id FROM gifts WHERE slug=?", (slug,))
            if await cur.fetchone():
                return None
            cur = await self.conn.execute(
                "INSERT INTO gifts (slug, gift_id, title, saved_id, worker_id, status, "
                "can_resell_at, received_at) VALUES (?,?,?,?,?,'received',?,?)",
                (slug, gift_id, title, saved_id, worker_id, can_resell_at, int(time.time())),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def get_gift(self, slug: str) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM gifts WHERE slug=?", (slug,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def gifts_by_status(self, status: str, ready_only: bool = False) -> list[dict]:
        """Подарки в заданном статусе. ready_only — только вышедшие из кулдауна."""
        query = "SELECT * FROM gifts WHERE status=?"
        params: list = [status]
        if ready_only:
            query += " AND can_resell_at <= ?"
            params.append(int(time.time()))
        query += " ORDER BY id"
        cur = await self.conn.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]

    async def mark_gift_deposited(self, slug: str) -> None:
        """Подарок передан на аккаунт MRKT и ожидает появления в инвентаре."""
        async with self._lock:
            await self.conn.execute(
                "UPDATE gifts SET status='deposited' WHERE slug=?", (slug,)
            )
            await self.conn.commit()

    async def mark_gift_listed(self, slug: str, price_nano: int) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE gifts SET status='listed', list_price_nano=? WHERE slug=?",
                (price_nano, slug),
            )
            await self.conn.commit()

    async def mark_gift_sold(self, slug: str, sold_price_nano: int, share_nano: int) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE gifts SET status='sold', sold_price_nano=?, share_nano=?, sold_at=? "
                "WHERE slug=?",
                (sold_price_nano, share_nano, int(time.time()), slug),
            )
            await self.conn.commit()

    async def mark_gift_paid(self, slug: str) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE gifts SET status='paid' WHERE slug=?", (slug,))
            await self.conn.commit()

    async def mark_gift_skipped(self, slug: str, note: str) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE gifts SET status='skipped', note=? WHERE slug=?", (note[:300], slug)
            )
            await self.conn.commit()

    async def attach_gift_worker(self, slug: str, worker_id: int) -> None:
        """Привязывает подарок к воркеру вручную, если отправитель был скрыт."""
        async with self._lock:
            await self.conn.execute(
                "UPDATE gifts SET worker_id=? WHERE slug=?", (worker_id, slug)
            )
            await self.conn.commit()

    async def gift_stats(self) -> dict[str, int]:
        cur = await self.conn.execute(
            "SELECT status, COUNT(*) AS c, COALESCE(SUM(sold_price_nano),0) AS s FROM gifts "
            "GROUP BY status"
        )
        rows = {r["status"]: (r["c"], r["s"]) for r in await cur.fetchall()}
        return {
            "received": rows.get("received", (0, 0))[0],
            "deposited": rows.get("deposited", (0, 0))[0],
            "listed": rows.get("listed", (0, 0))[0],
            "sold": rows.get("sold", (0, 0))[0] + rows.get("paid", (0, 0))[0],
            "skipped": rows.get("skipped", (0, 0))[0],
            "revenue_nano": sum(v[1] for v in rows.values()),
        }

    async def find_stuck_withdrawals(self, older_than_sec: int = 300) -> list[tuple[int, int, int, str]]:
        """Заявки, зависшие в 'processing' — процесс умер между резервом и отправкой.

        Возвращает (id, user_id, amount_nano, wallet). Автоматически НЕ возвращаем
        средства: транзакция могла уйти в сеть до падения, и возврат означал бы
        двойную выплату. Требуется ручная проверка по адресу в блокчейне.
        """
        threshold = int(time.time()) - older_than_sec
        cur = await self.conn.execute(
            "SELECT id, user_id, amount_nano, wallet FROM withdrawals "
            "WHERE status='processing' AND created_at < ? ORDER BY id",
            (threshold,),
        )
        return [(r["id"], r["user_id"], r["amount_nano"], r["wallet"]) for r in await cur.fetchall()]

    async def resolve_stuck(self, withdrawal_id: int, sent: bool, note: str = "") -> None:
        """Закрывает зависшую заявку: sent=True — деньги ушли, False — вернуть на баланс."""
        async with self._lock:
            cur = await self.conn.execute(
                "SELECT user_id, amount_nano, status FROM withdrawals WHERE id=?", (withdrawal_id,)
            )
            row = await cur.fetchone()
            if row is None or row["status"] != "processing":
                raise ValueError("not_processing")
            if sent:
                await self.conn.execute(
                    "UPDATE withdrawals SET status='paid', tx_hash=?, finished_at=? WHERE id=?",
                    (note or "восстановлено вручную", int(time.time()), withdrawal_id),
                )
            else:
                await self.conn.execute(
                    "UPDATE withdrawals SET status='failed', error=?, finished_at=? WHERE id=?",
                    (note or "возврат после сбоя", int(time.time()), withdrawal_id),
                )
                await self.conn.execute(
                    "UPDATE workers SET balance_nano=balance_nano+? WHERE user_id=?",
                    (row["amount_nano"], row["user_id"]),
                )
            await self.conn.commit()

    async def get_top(self, limit: int = 10) -> list[tuple[str, int, int]]:
        """Топ работников по сумме выплаченного. (имя, всего выплачено, кол-во выплат)."""
        cur = await self.conn.execute(
            """
            SELECT w.full_name AS name,
                   COALESCE(SUM(d.amount_nano), 0) AS total,
                   COUNT(d.id) AS cnt
            FROM workers w
            JOIN withdrawals d ON d.user_id = w.user_id AND d.status = 'paid'
            GROUP BY w.user_id
            ORDER BY total DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [(r["name"], r["total"], r["cnt"]) for r in await cur.fetchall()]

    async def count_withdrawals(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS c FROM withdrawals WHERE user_id=?", (user_id,)
        )
        return (await cur.fetchone())["c"]

    async def get_withdrawals(
        self, user_id: int, page: int = 1, per_page: int = 5
    ) -> list[tuple[int, int, str, str | None, int]]:
        """Страница истории выводов: (id, сумма, статус, tx_hash, время)."""
        offset = max(0, (page - 1) * per_page)
        cur = await self.conn.execute(
            "SELECT id, amount_nano, status, tx_hash, created_at FROM withdrawals "
            "WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, per_page, offset),
        )
        return [
            (r["id"], r["amount_nano"], r["status"], r["tx_hash"], r["created_at"])
            for r in await cur.fetchall()
        ]

    async def worker_totals(self, user_id: int) -> tuple[int, int]:
        """(всего выплачено, количество выплат) по работнику."""
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(amount_nano),0) AS s, COUNT(*) AS c "
            "FROM withdrawals WHERE user_id=? AND status='paid'",
            (user_id,),
        )
        row = await cur.fetchone()
        return row["s"], row["c"]

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
