"""Tests for the add-a-person walk-through's pure helpers (no terminal, no network)."""

import os

from xirtun.adduser import chats_from_updates, name_problem, render_env, token_owner, token_shape_ok

TOKEN = "123456789:AAH" + "x" * 32


def test_name_rules(tmp_path):
    assert name_problem("ana", tmp_path) is None
    assert name_problem("Ana", tmp_path) is not None        # lowercase only
    assert name_problem("a", tmp_path) is not None          # too short
    assert name_problem("ana smith", tmp_path) is not None
    assert name_problem("../etc", tmp_path) is not None
    assert name_problem("1ana", tmp_path) is not None       # must start with a letter


def test_name_already_set_up(tmp_path):
    (tmp_path / ".env.ana").write_text("TELEGRAM_TOKEN=x\n")
    assert ".env.ana" in name_problem("ana", tmp_path)


def test_name_with_existing_data(tmp_path):
    (tmp_path / "data" / "ana").mkdir(parents=True)
    assert name_problem("ana", tmp_path) is None            # an empty folder is fine
    (tmp_path / "data" / "ana" / "xirtun.db").write_text("")
    assert "data/ana" in name_problem("ana", tmp_path)


def test_token_shape():
    assert token_shape_ok(TOKEN)
    assert not token_shape_ok("not-a-token")
    assert not token_shape_ok("123:short")


def test_token_owner_finds_a_bot_already_in_use(tmp_path):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=k\n")
    (tmp_path / ".env.uros").write_text(f"TELEGRAM_TOKEN={TOKEN}\n")
    (tmp_path / ".env.example").write_text(f"TELEGRAM_TOKEN={TOKEN}\n")  # never counts

    assert token_owner(TOKEN, tmp_path) == ".env.uros"
    assert token_owner("999:" + "y" * 35, tmp_path) is None


def test_chats_from_updates_keeps_distinct_private_chats():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 42, "type": "private", "first_name": "Ana",
                                              "username": "ana_s"}}},
        {"update_id": 2, "message": {"chat": {"id": 42, "type": "private", "first_name": "Ana"}}},
        {"update_id": 3, "message": {"chat": {"id": -100, "type": "group", "title": "Family"}}},
        {"update_id": 4, "edited_message": {"chat": {"id": 7, "type": "private"}}},
    ]
    assert chats_from_updates(updates) == [("42", "Ana (@ana_s)")]


def test_render_env_holds_only_what_is_personal():
    text = render_env("ana", TOKEN, "42", None)
    assert f"TELEGRAM_TOKEN={TOKEN}" in text
    assert "TELEGRAM_CHAT_ID=42" in text
    assert "GEMINI_API_KEY" not in text                    # falls back to the shared .env
    assert "GEMINI_API_KEY=k2" in render_env("ana", TOKEN, "42", "k2")


def test_rendered_file_satisfies_config(tmp_path, monkeypatch):
    """What the walk-through writes must be loadable as a person's file."""
    from xirtun.config import load_config

    monkeypatch.setattr("os.environ", {})
    (tmp_path / ".env").write_text("GEMINI_API_KEY=k\n")
    (tmp_path / ".env.ana").write_text(render_env("ana", TOKEN, "42", None))
    os.environ["ENV_FILE"] = str(tmp_path / ".env.ana")
    os.environ["DATA_DIR"] = str(tmp_path / "data" / "ana")

    config = load_config()
    assert (config.telegram_token, config.telegram_chat_id) == (TOKEN, "42")
