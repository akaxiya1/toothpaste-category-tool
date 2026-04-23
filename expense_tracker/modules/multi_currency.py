"""Multi-currency support -- stored as a side table to avoid migrating
V1's ``transactions`` schema.

The FX table is seeded from a user-provided JSON file (``fx_rates.json``)
shaped like ``{"2026-04-22": {"USD": 7.25, "EUR": 7.85}}``. No network
calls are made; rolling-latest fallback is applied when the date is
missing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..db import DBManager

SCHEMA = """
CREATE TABLE IF NOT EXISTS transaction_fx (
    tx_id           INTEGER PRIMARY KEY,
    currency        TEXT NOT NULL,
    original_amount REAL NOT NULL,
    rate_to_base    REAL NOT NULL,
    base_currency   TEXT NOT NULL DEFAULT 'CNY',
    rate_date       TEXT NOT NULL
);
"""


@dataclass
class FXRecord:
    currency: str
    original_amount: float
    rate_to_base: float
    base_currency: str
    rate_date: str


class MultiCurrency:
    def __init__(self, db: DBManager, rate_file: Optional[Path | str] = None, base: str = "CNY"):
        self.db = db
        self.base = base
        self.rate_file = Path(rate_file) if rate_file else None
        self._cache: dict[str, dict[str, float]] = {}
        self._migrate()
        if self.rate_file and self.rate_file.exists():
            self.reload_rates()

    def _migrate(self) -> None:
        with sqlite3.connect(self.db.path) as conn:
            conn.executescript(SCHEMA)

    def reload_rates(self) -> None:
        if not self.rate_file or not self.rate_file.exists():
            self._cache = {}
            return
        with self.rate_file.open("r", encoding="utf-8") as fh:
            self._cache = json.load(fh)

    def rate(self, currency: str, on: Optional[date] = None) -> float:
        if currency == self.base:
            return 1.0
        target = (on or date.today()).isoformat()
        if target in self._cache and currency in self._cache[target]:
            return float(self._cache[target][currency])
        # fallback: nearest earlier date
        for d in sorted(self._cache.keys(), reverse=True):
            if d <= target and currency in self._cache[d]:
                return float(self._cache[d][currency])
        raise KeyError(f"no FX rate for {currency} on or before {target}")

    def attach(self, tx_id: int, currency: str, original_amount: float,
               on: Optional[date] = None) -> FXRecord:
        """Record FX details for a transaction that was persisted in base amount."""
        rate = self.rate(currency, on=on)
        rec = FXRecord(
            currency=currency,
            original_amount=original_amount,
            rate_to_base=rate,
            base_currency=self.base,
            rate_date=(on or date.today()).isoformat(),
        )
        with sqlite3.connect(self.db.path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO transaction_fx
                    (tx_id, currency, original_amount, rate_to_base, base_currency, rate_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tx_id, currency, original_amount, rate, self.base, rec.rate_date),
            )
        return rec

    def to_base(self, amount: float, currency: str, on: Optional[date] = None) -> float:
        return round(amount * self.rate(currency, on=on), 2)

    def lookup(self, tx_id: int) -> Optional[FXRecord]:
        with sqlite3.connect(self.db.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM transaction_fx WHERE tx_id = ?", (tx_id,)
            ).fetchone()
        if row is None:
            return None
        return FXRecord(
            currency=row["currency"], original_amount=row["original_amount"],
            rate_to_base=row["rate_to_base"], base_currency=row["base_currency"],
            rate_date=row["rate_date"],
        )
