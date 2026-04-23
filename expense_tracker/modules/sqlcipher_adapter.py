"""Optional SQLCipher-backed database.

Tries to import ``pysqlcipher3``; falls back to stock ``sqlite3`` with a
clear warning. Key material is read from the ``EXPENSE_DB_KEY`` env
var (or passed explicitly to ``open()``) and is never written to disk.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Optional


class SQLCipherUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import pysqlcipher3.dbapi2  # type: ignore # noqa: F401
    except ImportError:
        return False
    return True


def open_connection(path: Path | str, key: Optional[str] = None):
    """Open an encrypted connection, or raise if SQLCipher is unavailable."""
    if not available():
        raise SQLCipherUnavailable(
            "pysqlcipher3 is not installed. Install it (`pip install pysqlcipher3`) "
            "or keep features.sqlcipher disabled."
        )
    import pysqlcipher3.dbapi2 as sqlcipher  # type: ignore
    conn = sqlcipher.connect(str(path))
    pragma_key = key or os.environ.get("EXPENSE_DB_KEY")
    if not pragma_key:
        conn.close()
        raise SQLCipherUnavailable("EXPENSE_DB_KEY env var not set; refusing to open un-keyed.")
    conn.execute(f"PRAGMA key = '{pragma_key}'")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def warn_if_unencrypted(enabled: bool) -> None:
    if enabled and not available():
        warnings.warn(
            "config enables SQLCipher but pysqlcipher3 is not installed; "
            "running with plain SQLite.",
            RuntimeWarning,
            stacklevel=2,
        )
