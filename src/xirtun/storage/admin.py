"""Destructive data-management helpers (used by the /cleardata command)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

# The only thing a wipe keeps: the Telegram polling offset (messaging/telegram.py's
# OFFSET_KEY). It says nothing about the person — it's the bot's place in the message
# stream — and losing it would replay every old message, including the /cleardata
# that started the wipe.
KEEP_KV_KEYS = ("tg_offset",)

# The memory files the app writes into a person's data folder. Named one by one rather
# than "everything in the folder": a misconfigured DATA_DIR must never turn this into
# deleting whatever else happens to live there.
_MEMORY_FILES = ("diet.md", "observations.md")
_MEMORY_DIRS = ("diet.history",)


def user_tables(conn: sqlite3.Connection) -> list[str]:
    """Every table in the schema, read from the database itself — so a table added
    later is wiped too, without anyone having to remember to list it here."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [row["name"] for row in rows]


def reset_all(conn: sqlite3.Connection, data_dir: Path) -> None:
    """Erase everything this person has stored: every table, every kv entry except
    the polling offset, and the markdown memory files with their history.

    Then VACUUM, so the deleted rows are actually gone from the file rather than left
    in free pages where they could still be recovered.
    """
    # Every table is being emptied, so the order foreign keys would impose doesn't
    # matter. The pragma only takes effect outside a transaction, hence the commit.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in user_tables(conn):
            if table == "kv":
                placeholders = ", ".join("?" for _ in KEEP_KV_KEYS)
                conn.execute(f"DELETE FROM kv WHERE k NOT IN ({placeholders})", KEEP_KV_KEYS)
            else:
                # Names come from sqlite_master, never from user input.
                conn.execute(f'DELETE FROM "{table}"')
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("VACUUM")
    # With WAL, old pages can linger in the -wal file until a checkpoint; fold it into
    # the main file and truncate it so they don't.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    for name in _MEMORY_FILES:
        (data_dir / name).unlink(missing_ok=True)
    for name in _MEMORY_DIRS:
        path = data_dir / name
        if path.exists():
            shutil.rmtree(path)
