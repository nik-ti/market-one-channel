"""Read-only SQLite connector for the dashboard.

Opens data/news.db in read-only mode (via the sqlite "file:...?mode=ro" URI) so
the dashboard can never write to it, and every connection gets a busy_timeout
so a query never hangs while news-channel's own processes are writing to the
same file — it raises sqlite3.OperationalError instead, which the API layer
turns into a 503.
"""

from __future__ import annotations

import sqlite3
import traceback
from pathlib import Path
from typing import Any

# The database this whole dashboard reads. Never written to from here.
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"

# How long a single query may wait on a lock before giving up. Keeps a
# concurrent write from making the dashboard hang instead of failing fast.
QUERY_TIMEOUT_SECONDS = 5.0


class DatabaseUnavailableError(Exception):
    """Raised when the DB file is missing or a query times out / fails."""


def get_connection() -> sqlite3.Connection:
    """Open a fresh read-only connection. Callers should use it in a `with`
    block (via query()/query_one() below) so it always gets closed."""
    if not DB_PATH.exists():
        raise DatabaseUnavailableError(f"database file not found: {DB_PATH}")

    try:
        # mode=ro: the OS/SQLite layer refuses any write, on top of us simply
        # never issuing one. uri=True is required to parse the "file:" form.
        conn = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro",
            uri=True,
            timeout=QUERY_TIMEOUT_SECONDS,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # Belt and suspenders: also cap how long SQLite itself will wait on a
        # busy lock before raising, in milliseconds.
        conn.execute(f"PRAGMA busy_timeout = {int(QUERY_TIMEOUT_SECONDS * 1000)}")
        return conn
    except sqlite3.Error as exc:
        print("[db_connector] failed to open database:")
        traceback.print_exc()
        raise DatabaseUnavailableError(str(exc)) from exc


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return a list of plain dicts (JSON-friendly)."""
    try:
        with get_connection() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    except DatabaseUnavailableError:
        raise
    except sqlite3.OperationalError as exc:
        # This is the "database is locked" / timeout case called out in the
        # spec: log it, then let the API layer turn it into a 503.
        print(f"[db_connector] query timed out or DB locked: {exc}")
        traceback.print_exc()
        raise DatabaseUnavailableError(f"query timeout: {exc}") from exc
    except sqlite3.Error as exc:
        print(f"[db_connector] unexpected sqlite error: {exc}")
        traceback.print_exc()
        raise DatabaseUnavailableError(str(exc)) from exc


def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None
