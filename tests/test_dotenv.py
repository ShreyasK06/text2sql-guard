"""The .env loader fills missing variables only, never overrides, and can be disabled."""
from __future__ import annotations

import os

from app.config import load_dotenv


def _write(tmp_path, text: str):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_missing_names_and_skips_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_NO_DOTENV", raising=False)
    monkeypatch.delenv("DOTENV_TEST_A", raising=False)
    monkeypatch.delenv("DOTENV_TEST_B", raising=False)
    p = _write(tmp_path, "# comment\n\nDOTENV_TEST_A=one\nexport DOTENV_TEST_B = two \nnot a line\n")
    loaded = load_dotenv(p)
    assert sorted(loaded) == ["DOTENV_TEST_A", "DOTENV_TEST_B"]
    assert os.environ["DOTENV_TEST_A"] == "one"
    assert os.environ["DOTENV_TEST_B"] == "two"
    monkeypatch.delenv("DOTENV_TEST_A")
    monkeypatch.delenv("DOTENV_TEST_B")


def test_real_environment_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_NO_DOTENV", raising=False)
    monkeypatch.setenv("DOTENV_TEST_C", "from-env")
    p = _write(tmp_path, "DOTENV_TEST_C=from-file\n")
    assert load_dotenv(p) == []
    assert os.environ["DOTENV_TEST_C"] == "from-env"


def test_strips_matching_quotes_only(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_NO_DOTENV", raising=False)
    monkeypatch.delenv("DOTENV_TEST_D", raising=False)
    monkeypatch.delenv("DOTENV_TEST_E", raising=False)
    p = _write(tmp_path, "DOTENV_TEST_D=\"quoted value\"\nDOTENV_TEST_E='mismatch\"\n")
    load_dotenv(p)
    assert os.environ["DOTENV_TEST_D"] == "quoted value"
    assert os.environ["DOTENV_TEST_E"] == "'mismatch\""
    monkeypatch.delenv("DOTENV_TEST_D")
    monkeypatch.delenv("DOTENV_TEST_E")


def test_disabled_by_flag_and_missing_file_is_harmless(tmp_path, monkeypatch):
    p = _write(tmp_path, "DOTENV_TEST_F=x\n")
    monkeypatch.delenv("DOTENV_TEST_F", raising=False)
    monkeypatch.setenv("T2S_NO_DOTENV", "1")
    assert load_dotenv(p) == []
    assert "DOTENV_TEST_F" not in os.environ
    monkeypatch.delenv("T2S_NO_DOTENV")
    assert load_dotenv(tmp_path / "does-not-exist.env") == []
