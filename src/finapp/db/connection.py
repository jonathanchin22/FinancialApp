"""SQLite connection setup and a minimal forward-only migrator."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB_PATH = Path(os.environ.get("FINAPP_DB", "data/finapp.db"))


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database with WAL, foreign keys and dict-like rows, and migrate it."""
    path = Path(path) if path is not None else DEFAULT_DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply any migration files not yet recorded. Returns the names applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migration")}
    applied = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.name in done:
            continue
        with conn:
            conn.executescript(f.read_text())
            conn.execute("INSERT INTO schema_migration (name) VALUES (?)", (f.name,))
        applied.append(f.name)
    return applied
