"""SQLite persistence layer.

Schema differs from the original spec on purpose; see README "design reflections".
Notable additions:
    - direction      : expense / income / refund / transfer (避免退款被算作支出)
    - account        : 微信余额 / 信用卡 / 储蓄卡 ...
    - dedup_hash     : 防止剪贴板重复触发产生重复流水
    - source         : clipboard / manual / import (审计)
    - updated_at     : 修改时间 (审计)
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Optional

DEFAULT_DB_PATH = Path.home() / ".expense_tracker" / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at   TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    amount        REAL    NOT NULL,
    direction     TEXT    NOT NULL DEFAULT 'expense',
    merchant      TEXT,
    account       TEXT,
    raw_text      TEXT,
    category      TEXT,
    subcategory   TEXT,
    confidence    REAL    NOT NULL DEFAULT 1.0,
    status        TEXT    NOT NULL DEFAULT 'confirmed',
    source        TEXT    NOT NULL DEFAULT 'manual',
    note          TEXT,
    dedup_hash    TEXT    UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_tx_occurred ON transactions(occurred_at);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_tx_merchant ON transactions(merchant);

CREATE TABLE IF NOT EXISTS category_map (
    keyword     TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    subcategory TEXT
);

CREATE TABLE IF NOT EXISTS merchant_history (
    merchant    TEXT PRIMARY KEY,
    category    TEXT,
    subcategory TEXT,
    hit_count   INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period      TEXT NOT NULL,           -- e.g. '2026-04' or 'weekly'
    category    TEXT,                    -- NULL = total
    amount      REAL NOT NULL
);
"""


@dataclass
class Transaction:
    amount: float
    merchant: Optional[str] = None
    raw_text: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    direction: str = "expense"
    account: Optional[str] = None
    confidence: float = 1.0
    status: str = "confirmed"
    source: str = "manual"
    note: Optional[str] = None
    occurred_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    id: Optional[int] = None
    dedup_hash: Optional[str] = None

    def compute_dedup_hash(self) -> str:
        # Round to the minute so two clipboard hits within the same minute collapse.
        try:
            ts = datetime.fromisoformat(self.occurred_at).replace(second=0, microsecond=0).isoformat()
        except ValueError:
            ts = self.occurred_at
        payload = f"{ts}|{self.amount:.2f}|{self.direction}|{(self.merchant or '').strip()}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class DBManager:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    # ----- transactions -----

    def insert_transaction(self, tx: Transaction) -> Optional[int]:
        if not tx.dedup_hash:
            tx.dedup_hash = tx.compute_dedup_hash()
        with self._lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO transactions
                        (occurred_at, amount, direction, merchant, account, raw_text,
                         category, subcategory, confidence, status, source, note, dedup_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tx.occurred_at, tx.amount, tx.direction, tx.merchant, tx.account,
                        tx.raw_text, tx.category, tx.subcategory, tx.confidence, tx.status,
                        tx.source, tx.note, tx.dedup_hash,
                    ),
                )
                return cur.lastrowid
            except sqlite3.IntegrityError:
                # duplicate dedup_hash -> ignored on purpose
                return None

    def update_category(self, tx_id: int, category: str, subcategory: Optional[str] = None) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE transactions
                SET category = ?, subcategory = ?, updated_at = CURRENT_TIMESTAMP, confidence = 1.0
                WHERE id = ? AND status != 'deleted'
                """,
                (category, subcategory, tx_id),
            )
            return cur.rowcount > 0

    def soft_delete(self, tx_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE transactions SET status='deleted', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (tx_id,),
            )
            return cur.rowcount > 0

    def list_transactions(self, limit: int = 200, since_days: Optional[int] = None) -> list[dict]:
        sql = "SELECT * FROM transactions WHERE status != 'deleted'"
        params: list = []
        if since_days is not None:
            cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
            sql += " AND occurred_at >= ?"
            params.append(cutoff)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def stats_by_category(self, since_days: int = 30) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS count, ROUND(SUM(amount), 2) AS total
                FROM transactions
                WHERE status != 'deleted' AND direction = 'expense' AND occurred_at >= ?
                GROUP BY category
                ORDER BY total DESC
                """,
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- learning loop -----

    def upsert_merchant_history(self, merchant: str, category: str, subcategory: Optional[str]) -> None:
        if not merchant:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO merchant_history(merchant, category, subcategory, hit_count, updated_at)
                VALUES(?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(merchant) DO UPDATE SET
                    category = excluded.category,
                    subcategory = excluded.subcategory,
                    hit_count = merchant_history.hit_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (merchant, category, subcategory),
            )

    def lookup_merchant_history(self, merchant: str) -> Optional[dict]:
        if not merchant:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM merchant_history WHERE merchant = ?", (merchant,)
            ).fetchone()
            return dict(row) if row else None

    def seed_category_map(self, mapping: Iterable[tuple[str, str, Optional[str]]]) -> None:
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO category_map(keyword, category, subcategory) VALUES (?,?,?)",
                list(mapping),
            )

    def list_category_map(self) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM category_map").fetchall()]
