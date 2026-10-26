"""Remove a person: stop their instance and delete everything stored under their name.

Run:  make removeuser      (or: uv run python -m xirtun.removeuser)

The mirror of adduser.py (ADR-017). It stops and disables ``xirtun@<name>``, then
deletes ``.env.<name>`` and ``data/<name>/`` — their diary, profile, notes, history
snapshots and the database's -wal/-shm files. What lives outside this machine's files
(the bot itself, their Telegram chat, the system journal) can't be deleted from here,
so it says exactly what is left and how to remove it.

Like adduser, a terminal tool talking to the operator: print()/input(), not logging.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from xirtun.adduser import NAME_RE, ROOT, SYSTEMD_UNIT, get_bot_username, require_checkout_owner


# --- pure helpers ---

def removal_targets(name: str, root: Path = ROOT) -> list[Path]:
    """The files and folders stored under `name` that exist right now.

    The name is checked against the same rule adduser enforces before it goes near a
    path, so nothing like '../' can turn a removal into deleting something else.
    """
    if not NAME_RE.match(name):
        raise ValueError(f"not a valid person name: {name!r}")
    candidates = [root / f".env.{name}", root / "data" / name]
    return [path for path in candidates if path.exists()]


def delete(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


# --- systemd ---

def unit_in_use(unit: str) -> bool:
    """Whether the instance is running or set to start on boot."""
    for verb in ("is-active", "is-enabled"):
        result = subprocess.run(["systemctl", verb, unit], capture_output=True, text=True)
        if result.stdout.strip() in ("active", "activating", "enabled"):
            return True
    return False


# --- the walk-through ---

def main() -> None:
    print("Remove a person from xirtun — their bot stops and all their data is deleted.\n")
    require_checkout_owner()

    name = input("Name of the person to remove: ").strip().lower()
    if not NAME_RE.match(name):
        print("✗ Not a valid name (lowercase letters/digits, as used by make adduser).")
        sys.exit(1)

    unit = f"xirtun@{name}"
    targets = removal_targets(name)
    on_vm = SYSTEMD_UNIT.exists()
    running = on_vm and unit_in_use(unit)
    if not targets and not running:
        print(f"✗ Nothing is set up under '{name}'.")
        sys.exit(1)

    # Read the bot's name now, while its token still exists, for the BotFather step.
    env_file = ROOT / f".env.{name}"
    token = dotenv_values(env_file).get("TELEGRAM_TOKEN") if env_file.exists() else None
    bot = get_bot_username(token) if token else None

    print("\nThis permanently deletes:")
    if running:
        print(f"  - the {unit} service (stopped, and no longer started on boot)")
    for path in targets:
        kind = "folder, with everything in it" if path.is_dir() else "settings file"
        print(f"  - {path.relative_to(ROOT)}  ({kind})")
    print("There is no undo. If they want a copy, have them send /exportbackup first.")
    if input(f"\nType the name again to confirm ({name}): ").strip() != name:
        print("Names don't match — nothing was changed.")
        sys.exit(1)

    # Stop the bot BEFORE deleting: a running instance holds the database open and
    # would recreate the data folder on its next write.
    if running:
        result = subprocess.run(["sudo", "-n", "systemctl", "disable", "--now", unit])
        if result.returncode != 0:
            print(
                f"✗ Couldn't stop {unit}, so nothing was deleted. As a sudo-capable user run\n"
                f"    sudo systemctl disable --now {unit}\n"
                "  then run make removeuser again."
            )
            sys.exit(1)
        print(f"  ✓ Stopped and disabled {unit}")

    delete(targets)
    for path in targets:
        print(f"  ✓ Deleted {path.relative_to(ROOT)}")

    print(
        "\nWhat's left is outside these files, and can only be removed by hand:\n"
        f"  - The bot: in @BotFather send /deletebot and pick {f'@{bot}' if bot else 'their bot'}.\n"
        f"  - Their chat with it: {name} can delete it in Telegram.\n"
        "  - Server logs: the system journal keeps the messages the bot logged. It can't be\n"
        "    cleared for one service alone — it's only trimmed once the journal hits its\n"
        "    size cap — so they stay unless a sudo user clears everyone's logs\n"
        "    ('sudo journalctl --vacuum-time=1s').\n"
        "  - Anything the model provider retains under its own terms.\n"
        f"\nDone — {name} is removed."
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
