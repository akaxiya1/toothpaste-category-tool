"""CSV export + rolling statistics."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable

from .db import DBManager

CSV_FIELDS = [
    "id", "occurred_at", "amount", "direction", "merchant", "account",
    "category", "subcategory", "confidence", "status", "source", "note", "raw_text",
]


def to_csv(rows: Iterable[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def weekly_summary(db: DBManager, weeks: int = 8) -> list[dict]:
    cutoff = (datetime.now() - timedelta(weeks=weeks)).isoformat()
    rows = db.list_transactions(limit=100_000)
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"expense": 0.0, "income": 0.0, "refund": 0.0})
    for r in rows:
        if r["occurred_at"] < cutoff:
            continue
        direction = r.get("direction") or "expense"
        if direction not in buckets[""]:  # type: ignore[index]
            continue
        try:
            dt = datetime.fromisoformat(r["occurred_at"])
        except ValueError:
            continue
        iso_year, iso_week, _ = dt.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        buckets[key][direction] += float(r["amount"] or 0)
    return [
        {"period": k, **{kk: round(vv, 2) for kk, vv in v.items()}}
        for k, v in sorted(buckets.items())
        if k
    ]
