import pytest

from bot.config import Config

BASE = {"BOT_TOKEN": "123:abc", "ADMIN_IDS": "7712345678", "DRY_RUN": "true"}


def load(monkeypatch, **overrides):
    for key in ("BOT_TOKEN", "ADMIN_IDS", "DRY_RUN", "WALLET_MNEMONIC", "WALLET_VERSION",
                "MIN_WITHDRAW_TON", "DB_PATH", "TON_TESTNET", "USE_PREMIUM_EMOJI"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**BASE, **overrides}.items():
        if value is not None:
            monkeypatch.setenv(key, value)
    return Config.load()


class TestValidation:
    def test_missing_token(self, monkeypatch):
        with pytest.raises(RuntimeError, match="BOT_TOKEN"):
            load(monkeypatch, BOT_TOKEN=None)

    def test_missing_admins(self, monkeypatch):
        with pytest.raises(RuntimeError, match="ADMIN_IDS"):
            load(monkeypatch, ADMIN_IDS=None)

    def test_admin_ids_with_wrong_value_names_the_variable(self, monkeypatch):
        """Реальная ошибка деплоя: в ADMIN_IDS попало значение другой переменной."""
        with pytest.raises(RuntimeError) as err:
            load(monkeypatch, ADMIN_IDS="true")
        assert "ADMIN_IDS" in str(err.value) and "true" in str(err.value)

    @pytest.mark.parametrize("raw,expected", [
        ("771", {771}),
        ("771,882", {771, 882}),
        ("771, 882 ,993", {771, 882, 993}),
        ("771;882", {771, 882}),
        ("771,771", {771}),
    ])
    def test_admin_ids_parsing(self, monkeypatch, raw, expected):
        assert load(monkeypatch, ADMIN_IDS=raw).admin_ids == expected

    def test_mnemonic_required_without_dry_run(self, monkeypatch):
        with pytest.raises(RuntimeError, match="WALLET_MNEMONIC"):
            load(monkeypatch, DRY_RUN="false")

    def test_dry_run_needs_no_mnemonic(self, monkeypatch):
        assert load(monkeypatch, DRY_RUN="true").dry_run is True

    def test_mnemonic_accepted(self, monkeypatch):
        cfg = load(monkeypatch, DRY_RUN="false", WALLET_MNEMONIC=" ".join(["word"] * 24))
        assert len(cfg.wallet_mnemonic) == 24

    @pytest.mark.parametrize("bad", ["abc", "", "-1", "0"])
    def test_bad_min_withdraw(self, monkeypatch, bad):
        if bad == "":
            assert load(monkeypatch, MIN_WITHDRAW_TON=bad).min_withdraw_nano > 0
            return
        with pytest.raises(RuntimeError, match="MIN_WITHDRAW_TON"):
            load(monkeypatch, MIN_WITHDRAW_TON=bad)

    def test_comma_decimal_accepted(self, monkeypatch):
        assert load(monkeypatch, MIN_WITHDRAW_TON="0,5").min_withdraw_nano == 500_000_000

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("1", True), ("yes", True),
        ("false", False), ("0", False), ("нет", False),
    ])
    def test_boolean_flags(self, monkeypatch, raw, expected):
        assert load(monkeypatch, TON_TESTNET=raw).is_testnet is expected
