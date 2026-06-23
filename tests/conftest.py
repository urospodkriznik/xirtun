"""Shared pytest fixtures.

Any test that takes a ``conn`` parameter gets a fresh, initialized database in a
temporary directory (``tmp_path`` is a built-in pytest fixture).
"""

import sqlite3
from pathlib import Path

import pytest

from xirtun.storage import db


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    connection = db.get_connection(db_path)
    yield connection            # the test runs here, with `connection` injected
    connection.close()          # teardown after the test
