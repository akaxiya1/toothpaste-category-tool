"""Merchant name normalisation.

Chinese payment channels are notorious for shape-shifting merchant
names: "瑞幸咖啡" / "瑞幸咖啡(上海店)" / "LUCKIN COFFEE" all refer to
the same shop. If they go straight into ``merchant_history`` the
learning loop fragments across three rows.

This module keeps an explicit ``alias -> canonical`` table. The app
layer normalises merchant strings before calling classifier / history,
and the ``/aliases`` endpoints let the UI manage the mapping.

``suggest()`` proposes a canonical form when we think two names are the
same merchant, but **never** merges automatically -- cost of a false
merge is high (you lose category separation for a real second brand).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

from ..db import DBManager

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchant_alias (
    alias      TEXT PRIMARY KEY,
    canonical  TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alias_canonical ON merchant_alias(canonical);
"""


@dataclass
class AliasSuggestion:
    alias: str
    canonical: str
    score: float      # 0..1, higher = more confident
    reason: str


def _normalise_token(s: str) -> str:
    """Lower + strip whitespace + drop common parenthetical suffixes."""
    out = s.strip().lower()
    # drop tail "(...分店)" / "（...店）" etc.
    for opener, closer in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】")):
        i = out.find(opener)
        j = out.rfind(closer)
        if 0 <= i < j:
            out = out[:i] + out[j + 1:]
    return out.strip()


def _edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Standard Levenshtein, cut off at ``limit`` for speed."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        min_row = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < min_row:
                min_row = cur[j]
        if min_row > limit:
            return limit + 1
        prev = cur
    return prev[-1]


class MerchantAlias:
    def __init__(self, db: DBManager):
        self.db = db
        with sqlite3.connect(self.db.path) as conn:
            conn.executescript(SCHEMA)

    # ---------- lookup ----------

    def normalize(self, merchant: Optional[str]) -> Optional[str]:
        if not merchant:
            return merchant
        with sqlite3.connect(self.db.path) as conn:
            row = conn.execute(
                "SELECT canonical FROM merchant_alias WHERE alias = ?", (merchant,)
            ).fetchone()
        return row[0] if row else merchant

    def add(self, alias: str, canonical: str) -> None:
        if not alias or not canonical or alias == canonical:
            return
        with sqlite3.connect(self.db.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO merchant_alias(alias, canonical) VALUES (?, ?)",
                (alias, canonical),
            )

    def remove(self, alias: str) -> bool:
        with sqlite3.connect(self.db.path) as conn:
            cur = conn.execute("DELETE FROM merchant_alias WHERE alias = ?", (alias,))
            return cur.rowcount > 0

    def list_all(self) -> list[dict]:
        with sqlite3.connect(self.db.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT alias, canonical, created_at FROM merchant_alias ORDER BY canonical, alias"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- suggest ----------

    def suggest(self, merchant: str, known: Optional[Iterable[str]] = None) -> Optional[AliasSuggestion]:
        """Return a suggested canonical for ``merchant`` if a similar name
        already exists in ``merchant_history``. Never auto-applies."""
        if not merchant:
            return None
        if known is None:
            with sqlite3.connect(self.db.path) as conn:
                known = [r[0] for r in conn.execute(
                    "SELECT merchant FROM merchant_history"
                ).fetchall()]
        target_norm = _normalise_token(merchant)
        best: Optional[AliasSuggestion] = None
        for name in known:
            if not name or name == merchant:
                continue
            name_norm = _normalise_token(name)
            reason = None
            score = 0.0
            if name_norm == target_norm:
                reason, score = "normalised-match", 0.95
            elif name_norm in target_norm or target_norm in name_norm:
                reason, score = "substring", 0.85
            else:
                d = _edit_distance(name_norm, target_norm, limit=2)
                if d <= 2 and max(len(name_norm), len(target_norm)) >= 3:
                    reason, score = f"edit-distance-{d}", 0.75 - 0.1 * d
            if reason and (best is None or score > best.score):
                best = AliasSuggestion(alias=merchant, canonical=name,
                                       score=round(score, 2), reason=reason)
        return best

    # ---------- merge ----------

    def merge_history(self, alias: str, canonical: str) -> None:
        """Fold ``alias``'s merchant_history row into ``canonical``'s row,
        then register the alias so future writes go straight to canonical."""
        if not alias or alias == canonical:
            return
        with sqlite3.connect(self.db.path) as conn:
            row = conn.execute(
                "SELECT hit_count, category, subcategory FROM merchant_history WHERE merchant = ?",
                (alias,),
            ).fetchone()
            if row:
                hit_count, category, subcategory = row
                conn.execute(
                    """
                    INSERT INTO merchant_history(merchant, category, subcategory, hit_count, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(merchant) DO UPDATE SET
                        hit_count = merchant_history.hit_count + excluded.hit_count,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (canonical, category, subcategory, hit_count),
                )
                conn.execute("DELETE FROM merchant_history WHERE merchant = ?", (alias,))
            conn.execute(
                "INSERT OR REPLACE INTO merchant_alias(alias, canonical) VALUES (?, ?)",
                (alias, canonical),
            )
