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
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_load_config_defaults_timezone_to_utc(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    assert load_config().timezone.key == "UTC"


def _real_env_files(monkeypatch, tmp_path, *, shared: str, personal: str) -> None:
    """Point ENV_FILE at real files, with the environment swapped for a throwaway copy
    so whatever load_dotenv sets disappears after the test."""
    monkeypatch.setattr("os.environ", {})
    (tmp_path / ".env").write_text(shared)
    (tmp_path / ".env.uros").write_text(personal)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / ".env.uros"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


def test_personal_file_overrides_shared_env(monkeypatch, tmp_path):
    _real_env_files(
        monkeypatch, tmp_path,
        shared="GEMINI_API_KEY=shared-key\nLLM_CHEAP_MODEL=shared-model\nWEEKLY_CRON=0 9 * * 0\n",
        personal="TELEGRAM_TOKEN=t\nTELEGRAM_CHAT_ID=1\nLLM_CHEAP_MODEL=my-model\n",
    )

    config = load_config()

    assert config.cheap_model == "my-model"         # the person's file wins
    assert config.weekly_cron == "0 9 * * 0"        # filled in from the shared .env
    assert config.gemini_api_key == "shared-key"
    assert config.telegram_chat_id == "1"


def test_real_environment_beats_both_files(monkeypatch, tmp_path):
    _real_env_files(
        monkeypatch, tmp_path,
        shared="GEMINI_API_KEY=k\nDATA_DIR=/shared\n",
        personal="TELEGRAM_TOKEN=t\nTELEGRAM_CHAT_ID=1\nDATA_DIR=/mine\n",
    )

    assert load_config().data_dir == tmp_path / "data"   # what systemd set


def test_telegram_settings_are_never_inherited(monkeypatch, tmp_path):
    _real_env_files(
        monkeypatch, tmp_path,
        shared="TELEGRAM_TOKEN=someone-elses\nTELEGRAM_CHAT_ID=2\nGEMINI_API_KEY=k\n",
        personal="TELEGRAM_CHAT_ID=1\n",
    )

    with pytest.raises(ConfigError, match="TELEGRAM_TOKEN"):
        load_config()


def test_load_config_missing_env_file_raises(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / ".env.nobody"))

    with pytest.raises(ConfigError, match="ENV_FILE"):
        load_config()
