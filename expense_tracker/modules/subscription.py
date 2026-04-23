"""Detect recurring charges and keep a lightweight calendar.

Triggers (case-insensitive, Chinese and English):
- 自动续费 / 自动扣费 / 自动扣款
- 月付 / 年付 / 季付
- subscription / auto-renew / renew

Cadence is guessed from the hint; the next-due date is computed from
``occurred_at`` and cadence. We never block inserts; alerts are purely
notification-only and produced by ``upcoming()``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..db import DBManager

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscription_calendar (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant   TEXT NOT NULL,
    amount     REAL NOT NULL,
    cadence    TEXT NOT NULL,
    next_due   TEXT NOT NULL,
    last_tx_id INTEGER,
    active     INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(merchant, cadence)
);
CREATE INDEX IF NOT EXISTS idx_sub_due ON subscription_calendar(next_due);
"""

CADENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"年付|年费|annual|yearly", re.I), "yearly"),
    (re.compile(r"季付|季度|quarterly", re.I), "quarterly"),
    (re.compile(r"月付|月费|monthly", re.I), "monthly"),
    (re.compile(r"周付|weekly", re.I), "weekly"),
    (re.compile(r"自动续费|自动扣费|自动扣款|auto[-\s]?renew|subscription", re.I), "monthly"),
]

CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}


@dataclass
class SubscriptionHint:
    cadence: str
    reason: str


def detect(text: str | None) -> Optional[SubscriptionHint]:
    if not text:
        return None
    for pattern, cadence in CADENCE_PATTERNS:
        m = pattern.search(text)
        if m:
            return SubscriptionHint(cadence=cadence, reason=m.group(0))
    return None


class SubscriptionCalendar:
    def __init__(self, db: DBManager):
        self.db = db
        with sqlite3.connect(self.db.path) as conn:
            conn.executescript(SCHEMA)

    def record(self, merchant: str, amount: float, cadence: str,
               occurred_at: Optional[str] = None, tx_id: Optional[int] = None) -> int:
        occurred = datetime.fromisoformat(occurred_at) if occurred_at else datetime.now()
        next_due = (occurred + timedelta(days=CADENCE_DAYS.get(cadence, 30))).isoformat(timespec="seconds")
        with sqlite3.connect(self.db.path) as conn:
            cur = conn.execute(
                """
                INSERT INTO subscription_calendar(merchant, amount, cadence, next_due, last_tx_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(merchant, cadence) DO UPDATE SET
                    amount = excluded.amount,
                    next_due = excluded.next_due,
                    last_tx_id = excluded.last_tx_id,
                    active = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (merchant, amount, cadence, next_due, tx_id),
            )
            return cur.lastrowid or 0

    def upcoming(self, within_days: int = 7) -> list[dict]:
        cutoff = (datetime.now() + timedelta(days=within_days)).isoformat()
        with sqlite3.connect(self.db.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM subscription_calendar WHERE active = 1 AND next_due <= ? ORDER BY next_due",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def deactivate(self, merchant: str, cadence: str) -> None:
        with sqlite3.connect(self.db.path) as conn:
            conn.execute(
                "UPDATE subscription_calendar SET active = 0, updated_at = CURRENT_TIMESTAMP "
                "WHERE merchant = ? AND cadence = ?",
                (merchant, cadence),
            )
