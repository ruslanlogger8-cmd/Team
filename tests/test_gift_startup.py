"""Подсистема подарков не должна ронять выплатной бот."""
import base64

import pytest

from bot.gifts.session import restore_mrkt_session


class Cfg:
    def __init__(self, workdir):
        self.mrkt_workdir = str(workdir)


class TestSessionRestore:
    def test_writes_file_from_env(self, tmp_path, monkeypatch):
        payload = b"session-bytes"
        monkeypatch.setenv("MRKT_SESSION_B64", base64.b64encode(payload).decode())
        restore_mrkt_session(Cfg(tmp_path))
        assert (tmp_path / "mrkt.session").read_bytes() == payload

    def test_existing_file_not_overwritten(self, tmp_path, monkeypatch):
        """pyrogram обновляет файл в работе — перезапись откатила бы состояние."""
        target = tmp_path / "mrkt.session"
        target.write_bytes(b"live-state")
        monkeypatch.setenv("MRKT_SESSION_B64", base64.b64encode(b"stale").decode())
        restore_mrkt_session(Cfg(tmp_path))
        assert target.read_bytes() == b"live-state"

    def test_missing_env_explains_what_to_do(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MRKT_SESSION_B64", raising=False)
        with pytest.raises(RuntimeError) as err:
            restore_mrkt_session(Cfg(tmp_path))
        assert "gen_mrkt_session" in str(err.value)

    def test_broken_base64_named(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MRKT_SESSION_B64", "не-base64!!!")
        with pytest.raises(RuntimeError, match="base64"):
            restore_mrkt_session(Cfg(tmp_path))

    def test_empty_after_decode_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MRKT_SESSION_B64", "")
        with pytest.raises(RuntimeError):
            restore_mrkt_session(Cfg(tmp_path))
