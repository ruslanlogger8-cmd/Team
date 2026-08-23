"""Структурные инварианты — ловят регрессии, которые не видны в обычных тестах."""
import ast
import pathlib

import pytest

DB_SOURCE = pathlib.Path("bot/db.py")
# connect() выполняется до старта поллинга, конкуренции там нет
EXEMPT = {"connect"}


def _methods_with_commit():
    tree = ast.parse(DB_SOURCE.read_text(encoding="utf-8"))
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for func in (n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)):
            source = ast.unparse(func)
            if "commit()" in source:
                yield func.name, source


class TestDatabaseLocking:
    """aiosqlite держит одно соединение: commit без лока закрывает чужую транзакцию."""

    @pytest.mark.parametrize("name,source", list(_methods_with_commit()))
    def test_every_committing_method_holds_the_lock(self, name, source):
        if name in EXEMPT:
            pytest.skip(f"{name} выполняется до конкурентной нагрузки")
        assert "self._lock" in source, (
            f"Database.{name} коммитит без self._lock — при параллельных запросах "
            f"это ломает чужую транзакцию и теряет деньги"
        )

    def test_invariant_covers_something(self):
        assert len(list(_methods_with_commit())) >= 6


class TestNoHardcodedSecrets:
    """Токены и seed-фразы попадают в репозиторий только через .env."""

    @pytest.mark.parametrize("path", sorted(pathlib.Path("bot").rglob("*.py")))
    def test_no_bot_token_literal(self, path):
        import re
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\d{8,12}:AA[\w-]{30,}", source), f"{path}: похоже на токен бота"

    def test_env_example_has_no_real_values(self):
        for line in pathlib.Path(".env.example").read_text(encoding="utf-8").splitlines():
            if line.startswith("BOT_TOKEN=") or line.startswith("WALLET_MNEMONIC="):
                value = line.split("=", 1)[1].strip()
                assert value in ("", "word1 word2 ... word24"), f"реальное значение в .env.example: {line}"


def test_rule_is_single_and_short():
    """Разделитель — один на весь бот и не длиннее ширины экрана телефона.

    Длинная линия переносится на вторую строку и выглядит как обрывок,
    а три копии константы разъезжаются при правке.
    """
    from bot.ui import RULE

    assert set(RULE) == {"━"}
    assert len(RULE) <= 12, "линия переносится на телефоне"

    root = pathlib.Path(__file__).resolve().parents[1] / "bot"
    duplicates = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "ui.py" and 'RULE = "' in path.read_text(encoding="utf-8")
    ]
    assert not duplicates, f"копии RULE вне bot/ui.py: {duplicates}"
