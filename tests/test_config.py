"""Smoke tests for config loading.

This is exactly the kind of test that would have caught the "tests passed but the
app crashed on startup" bugs — it actually calls load_config().

Uses pytest's ``monkeypatch`` fixture to set env vars and stub ``load_dotenv`` for
the duration of each test, so a real local .env file can't influence the result.
"""

import pytest

from xirtun.config import ConfigError, load_config


def test_load_config_reads_env_and_applies_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("xirtun.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_CHEAP_MODEL", raising=False)  # force the default path

    config = load_config()

    assert config.telegram_token == "t"
    assert config.telegram_chat_id == "123"
    assert config.data_dir == tmp_path
    assert config.cheap_model == "gemini-2.5-flash-lite"  # default applied


def test_load_config_missing_required_raises(monkeypatch):
    monkeypatch.setattr("xirtun.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    with pytest.raises(ConfigError):
        load_config()


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setattr("xirtun.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_load_config_parses_timezone(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TIMEZONE", "Europe/Ljubljana")
    assert load_config().timezone.key == "Europe/Ljubljana"


def test_load_config_bad_timezone_raises(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TIMEZONE", "Mars/Olympus")
    with pytest.raises(ConfigError):
        load_config()
