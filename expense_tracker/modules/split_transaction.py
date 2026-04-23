"""Split a single transaction into multiple linked children.

Stored via an additive ``transaction_splits`` table. We do NOT physically
delete the parent -- we mark it ``status='split'`` so downstream
reports can either show the parent (aggregate) or the children
(category breakdown) without double counting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

from ..db import DBManager, Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS transaction_splits (
    parent_id INTEGER NOT NULL,
    child_id  INTEGER NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE INDEX IF NOT EXISTS idx_split_parent ON transaction_splits(parent_id);
CREATE INDEX IF NOT EXISTS idx_split_child  ON transaction_splits(child_id);
"""


@dataclass
class SplitPart:
    amount: float
    category: str
    subcategory: Optional[str] = None
    note: Optional[str] = None


class SplitError(ValueError):
    pass


class SplitManager:
    def __init__(self, db: DBManager):
        self.db = db
        with sqlite3.connect(self.db.path) as conn:
            conn.executescript(SCHEMA)

    def _parent(self, tx_id: int) -> dict:
        with sqlite3.connect(self.db.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if row is None:
            raise SplitError(f"transaction {tx_id} not found")
        return dict(row)

    def split(self, tx_id: int, parts: Iterable[SplitPart], tolerance: float = 0.01) -> list[int]:
        parts = list(parts)
        if not parts:
            raise SplitError("need at least one part")
        parent = self._parent(tx_id)
        total = round(sum(p.amount for p in parts), 2)
        if abs(total - float(parent["amount"])) > tolerance:
            raise SplitError(
                f"split total {total} does not match parent {parent['amount']} (tolerance {tolerance})"
            )
        if parent["status"] == "split":
            raise SplitError("transaction already split")

        new_ids: list[int] = []
        for idx, part in enumerate(parts, start=1):
            child = Transaction(
                amount=part.amount,
                direction=parent["direction"],
                merchant=parent["merchant"],
                account=parent["account"],
                raw_text=parent["raw_text"],
                category=part.category,
                subcategory=part.subcategory,
                confidence=1.0,
                source="split",
                note=part.note or f"split-of:{tx_id}#{idx}",
                occurred_at=parent["occurred_at"],
            )
            # Salt the dedup hash so children don't collide with each other or
            # with the parent. We keep dedup semantics for duplicate *inserts*,
            # but splits are a deliberate act.
            child.dedup_hash = child.compute_dedup_hash() + f":split:{tx_id}:{idx}"
            new_id = self.db.insert_transaction(child)
            if new_id is None:
                raise SplitError("failed to insert split child (dedup conflict)")
            new_ids.append(new_id)

        with sqlite3.connect(self.db.path) as conn:
            conn.executemany(
                "INSERT INTO transaction_splits(parent_id, child_id) VALUES (?, ?)",
                [(tx_id, cid) for cid in new_ids],
            )
            conn.execute(
                "UPDATE transactions SET status='split', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (tx_id,),
            )
        return new_ids

    def children_of(self, tx_id: int) -> list[int]:
        with sqlite3.connect(self.db.path) as conn:
            rows = conn.execute(
                "SELECT child_id FROM transaction_splits WHERE parent_id = ? ORDER BY child_id",
                (tx_id,),
            ).fetchall()
        return [r[0] for r in rows]
