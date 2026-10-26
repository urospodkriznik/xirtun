"""Add a person: an interactive walk-through that sets up their own instance (ADR-017).

Run:  make adduser      (or: uv run python -m xirtun.adduser)

It asks for a name, walks through creating a bot with @BotFather, checks the token
against Telegram, finds the person's chat id by waiting for them to press Start in
their new bot, and writes ``.env.<name>`` holding only what is theirs. Everything
else (models, schedules, usually the Gemini key) comes from the shared ``.env``. On
the VM it then enables ``xirtun@<name>``; locally it prints the command to run.

This is a terminal tool talking to the operator, so it uses print()/input() rather
than logging — the one place in the app that does.

The pure helpers (name rules, token checks, picking chats out of updates, rendering
the file) are separate from the prompts so they can be tested without a terminal.
"""

from __future__ import annotations

import getpass
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

# The repo checkout: src/xirtun/adduser.py -> the folder holding .env and data/.
ROOT = Path(__file__).resolve().parents[2]
# Present only where the systemd template is installed, i.e. on the VM.
SYSTEMD_UNIT = Path("/etc/systemd/system/xirtun@.service")
TELEGRAM_API = "https://api.telegram.org"

# Becomes a systemd instance name, a file suffix and a folder name, so keep it to
# characters that are safe in all three.
NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,19}$")
# What BotFather hands out: "<bot id>:<secret>".
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
# How long to wait for the person to press Start before falling back to typing an id.
_WAIT_SECONDS = 180


# --- pure helpers ---

def name_problem(name: str, root: Path = ROOT) -> str | None:
    """Why `name` can't be used for a new person, or None if it can."""
    if not NAME_RE.match(name):
        return "Use 2-20 lowercase letters or digits, starting with a letter (e.g. 'ana')."
    if (root / f".env.{name}").exists():
        return f".env.{name} already exists — that person is already set up."
    data = root / "data" / name
    if data.exists() and any(data.iterdir()):
        return f"data/{name}/ already holds data — pick another name or move it away first."
    return None


def token_shape_ok(token: str) -> bool:
    return bool(_TOKEN_RE.match(token))


def token_owner(token: str, root: Path = ROOT) -> str | None:
    """The settings file already using this bot token, if any. Two instances can't
    share a bot: Telegram hands its messages to one reader at a time."""
    for path in sorted(root.glob(".env*")):
        if path.name == ".env.example" or not path.is_file():
            continue
        if dotenv_values(path).get("TELEGRAM_TOKEN") == token:
            return path.name
    return None


def chats_from_updates(updates: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Distinct private chats that wrote to the bot, as (chat id, who), oldest first."""
    seen: dict[str, str] = {}
    for update in updates:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") != "private" or "id" not in chat:
            continue
        who = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
        if chat.get("username"):
            who = f"{who} (@{chat['username']})".strip()
        seen.setdefault(str(chat["id"]), who or "unknown")
    return list(seen.items())


def render_env(name: str, token: str, chat_id: str, gemini_key: str | None) -> str:
    lines = [
        f"# {name}'s own settings (ADR-017). Anything not set here comes from the shared",
        "# .env beside it, except the Telegram keys: those must always live here.",
        f"TELEGRAM_TOKEN={token}",
        f"TELEGRAM_CHAT_ID={chat_id}",
    ]
    if gemini_key:
        lines.append(f"GEMINI_API_KEY={gemini_key}")
    return "\n".join(lines) + "\n"


# --- Telegram ---

def get_bot_username(token: str) -> str | None:
    """The bot's @username if Telegram accepts the token, else None."""
    try:
        resp = httpx.get(f"{TELEGRAM_API}/bot{token}/getMe", timeout=15)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("result", {}).get("username")


def wait_for_chats(token: str, seconds: int = _WAIT_SECONDS) -> list[tuple[str, str]]:
    """Long-poll until someone writes to the bot (or time runs out).

    Never acknowledges anything (no offset), so the message stays queued and is the
    first thing the person's instance answers once it starts.
    """
    waited = 0
    while waited < seconds:
        try:
            resp = httpx.get(
                f"{TELEGRAM_API}/bot{token}/getUpdates", params={"timeout": 20}, timeout=30
            )
            chats = chats_from_updates(resp.json().get("result", []))
        except httpx.HTTPError:
            chats = []
        if chats:
            return chats
        waited += 20
    return []


# --- the walk-through ---

def require_checkout_owner() -> None:
    """Exit unless we run as the user owning the checkout — the user the service runs
    as. A settings file written by anyone else (mode 600) would be unreadable to the
    service, and the bot would fail to start; files deleted by anyone else might not
    be theirs to delete."""
    owner_uid = ROOT.stat().st_uid
    if owner_uid != os.getuid():
        owner = pwd.getpwuid(owner_uid).pw_name
        print(f"✗ Run this as {owner}, the user that owns {ROOT} (e.g. sudo -iu {owner}).")
        sys.exit(1)


def _ask(prompt: str, *, secret: bool = False) -> str:
    return (getpass.getpass(prompt) if secret else input(prompt)).strip()


def _yes(prompt: str) -> bool:
    return _ask(f"{prompt} [Y/n] ").lower() in ("", "y", "yes")


def _ask_name() -> str:
    while True:
        name = _ask("Short name for this person (used for their files, e.g. 'ana'): ").lower()
        problem = name_problem(name)
        if problem is None:
            return name
        print(f"  ✗ {problem}")


def _ask_token() -> tuple[str, str]:
    print(
        "\nStep 1 — create their bot (about a minute, on your phone or desktop):\n"
        "  1. In Telegram, open @BotFather (https://t.me/BotFather) and send /newbot\n"
        "  2. Give it a display name — anything, e.g. \"Ana's nutritionist\"\n"
        "  3. Give it a username — it must end in 'bot', e.g. ana_nutrition_bot\n"
        "  4. BotFather replies with a token like 123456789:AAH... — paste it below\n"
    )
    while True:
        token = _ask("Bot token: ")
        if not token_shape_ok(token):
            print("  ✗ That doesn't look like a BotFather token (digits, a colon, then a long code).")
            continue
        owner = token_owner(token)
        if owner:
            print(f"  ✗ {owner} already uses this bot. Each person needs their own — run /newbot again.")
            continue
        username = get_bot_username(token)
        if username is None:
            print("  ✗ Telegram didn't accept that token. Copy it again from BotFather.")
            continue
        print(f"  ✓ That's @{username}")
        return token, username


def _ask_chat_id(token: str, username: str, name: str) -> str:
    print(
        f"\nStep 2 — find {name}'s chat id:\n"
        f"  Ask {name} to open https://t.me/{username} and press Start (or send anything).\n"
        f"  Waiting up to {_WAIT_SECONDS // 60} minutes… (Ctrl-C to type the id yourself)"
    )
    try:
        chats = wait_for_chats(token)
    except KeyboardInterrupt:
        chats = []
        print()
    for chat_id, who in chats:
        if _yes(f"  Got a message from {who} (chat id {chat_id}). Is that {name}?"):
            return chat_id
    while True:
        chat_id = _ask(f"  {name}'s chat id (they can get it from @userinfobot): ")
        if re.fullmatch(r"-?\d+", chat_id):
            return chat_id
        print("  ✗ A chat id is a number.")


def _ask_gemini_key(name: str, shared: dict[str, str | None]) -> str | None:
    if shared.get("GEMINI_API_KEY"):
        key = _ask(
            f"\nStep 3 — Gemini API key for {name} (Enter to use the shared one in .env, "
            "input hidden): ",
            secret=True,
        )
        return key or None
    while True:
        key = _ask(f"\nStep 3 — Gemini API key for {name} (no shared one in .env; hidden): ",
                   secret=True)
        if key:
            return key


def _write_env(path: Path, content: str) -> None:
    # Created owner-only from the start (not chmod'ed after), since it holds secrets.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


def _start(name: str) -> None:
    unit = f"xirtun@{name}"
    if not SYSTEMD_UNIT.exists():
        print(
            "\nNo systemd template here (not the VM), so start it by hand:\n"
            f"  ENV_FILE=.env.{name} DATA_DIR=data/{name} uv run python -m xirtun.main"
        )
        return
    if not _yes(f"\nStart {unit} now (and on every boot)?"):
        print(f"  Later: sudo systemctl enable --now {unit}")
        return
    # -n: never prompt. The deploy user is allowed exactly this command (DEPLOY.md
    # step 8); anyone else gets the command to run themselves.
    result = subprocess.run(["sudo", "-n", "systemctl", "enable", "--now", unit])
    if result.returncode != 0:
        print(f"  ✗ Couldn't start it. As a sudo-capable user, run: sudo systemctl enable --now {unit}")
        return
    print(f"  ✓ {unit} is running. Logs: make logs WHO={name}")


def main() -> None:
    print("Add a person to xirtun — each person gets their own bot, settings and data.\n")
    require_checkout_owner()
    shared = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}

    name = _ask_name()
    token, username = _ask_token()
    chat_id = _ask_chat_id(token, username, name)
    gemini_key = _ask_gemini_key(name, shared)

    env_path = ROOT / f".env.{name}"
    _write_env(env_path, render_env(name, token, chat_id, gemini_key))
    print(f"\n  ✓ Wrote {env_path.name} (readable only by you). Their data will live in data/{name}/.")

    _start(name)
    print(
        f"\nDone. When {name}'s bot starts it answers their first message with the "
        "onboarding interview."
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
