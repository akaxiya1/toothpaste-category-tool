"""CSV reconciliation against official WeChat / Alipay / generic bills.

Month-end workflow:
1. Export the full month's bill from the WeChat app / Alipay web page.
2. Drop the CSV into the reconcile panel. The module parses it, then
   matches each statement entry against ``transactions`` by
   (date window, direction, amount) with a fuzzy merchant tiebreaker.
3. We return three buckets:
   - ``missing_in_db`` (漏记): show with a checkbox, bulk-import.
   - ``missing_in_statement`` (现金/手录): for user review.
   - ``amount_mismatch``: suspicious, shows delta.

The parser auto-detects format by peeking at the first ~30 lines. V1
dedup_hash semantics are preserved: imports go through
``db.insert_transaction`` which trips the UNIQUE constraint if a
manually-entered row already covers the same minute/amount/merchant.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..db import DBManager, Transaction

# ------------------------------------------------------------------ models

@dataclass
class StatementEntry:
    occurred_at: str
    amount: float
    direction: str
    merchant: Optional[str] = None
    description: Optional[str] = None
    channel: str = "generic"
    external_id: Optional[str] = None
    raw: str = ""


@dataclass
class ReconcileReport:
    matched: list[dict] = field(default_factory=list)
    missing_in_db: list[dict] = field(default_factory=list)
    missing_in_statement: list[dict] = field(default_factory=list)
    amount_mismatch: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "missing_in_db": self.missing_in_db,
            "missing_in_statement": self.missing_in_statement,
            "amount_mismatch": self.amount_mismatch,
            "stats": self.stats,
        }


# ------------------------------------------------------------------ parsing

_WECHAT_HEADER_RE = re.compile(r"交易时间.*交易对方.*金额\(元\)")
_ALIPAY_HEADER_RE = re.compile(r"交易时间.*交易对方.*金额")
_GENERIC_HEADER_RE = re.compile(r"(date|time|时间).*(amount|金额)", re.I)


def _coerce_amount(raw: str) -> float:
    s = (raw or "").replace(",", "").replace("¥", "").replace("￥", "").strip()
    s = s.lstrip("+").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        # "15.00元" / "¥15.00"
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else 0.0


def _coerce_dt(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def _coerce_direction(raw: str, amount_signed: float = 0.0) -> Optional[str]:
    s = (raw or "").strip()
    if s in ("支出", "支付"):
        return "expense"
    if s in ("收入", "收款"):
        return "income"
    if s == "退款":
        return "refund"
    if amount_signed < 0:
        return "expense"
    if amount_signed > 0:
        return "income"
    return None


def _detect_channel(lines: list[str]) -> tuple[str, int]:
    """Return (channel, header_line_index) or ('unknown', -1)."""
    for i, line in enumerate(lines[:40]):
        if _WECHAT_HEADER_RE.search(line):
            return "wechat", i
        if "交易订单号" in line and "交易对方" in line:
            return "alipay", i
        if _ALIPAY_HEADER_RE.search(line):
            return "alipay", i
        if _GENERIC_HEADER_RE.search(line):
            return "generic", i
    return "unknown", -1


def parse_statement(text: str, channel: Optional[str] = None) -> list[StatementEntry]:
    # Strip BOM + normalise newlines.
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines:
        return []

    if channel is None:
        channel, header_idx = _detect_channel(lines)
    else:
        _, header_idx = _detect_channel(lines)
    if header_idx < 0:
        return []

    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))
    entries: list[StatementEntry] = []

    for row in reader:
        # Normalise keys: strip whitespace, trailing punctuation.
        norm = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not any(norm.values()):
            continue

        # Skip rows without a known time column.
        time_raw = (norm.get("交易时间") or norm.get("付款时间")
                    or norm.get("交易创建时间") or norm.get("date") or norm.get("time"))
        occurred = _coerce_dt(time_raw or "")
        if not occurred:
            continue

        amount_raw = (norm.get("金额(元)") or norm.get("金额") or norm.get("amount") or "")
        amount_value = _coerce_amount(amount_raw)

        dir_raw = (norm.get("收/支") or norm.get("direction") or "")
        direction = _coerce_direction(dir_raw, amount_value if amount_raw.startswith(("-", "+")) else 0)

        status = norm.get("当前状态") or norm.get("交易状态") or ""
        # Skip clearly non-settled rows (e.g. "支付失败" / "已关闭" / "等待付款").
        if any(bad in status for bad in ("失败", "关闭", "等待")):
            continue
        # Neutral "/" (often internal transfers) → skip to avoid noise.
        if dir_raw in ("/", "不计收支", "") and abs(amount_value) == 0:
            continue
        if direction is None and dir_raw in ("/", "不计收支"):
            continue
        if direction is None:
            # last-ditch: treat negative as expense, positive as income
            direction = "expense" if amount_value >= 0 else "income"

        merchant = (norm.get("交易对方") or norm.get("merchant") or "").strip() or None
        description = (norm.get("商品") or norm.get("商品说明")
                       or norm.get("description") or "").strip() or None
        external_id = (norm.get("交易单号") or norm.get("交易订单号") or "").strip() or None

        entries.append(StatementEntry(
            occurred_at=occurred,
            amount=round(abs(amount_value), 2),
            direction=direction,
            merchant=merchant,
            description=description,
            channel=channel or "generic",
            external_id=external_id,
            raw=",".join(f"{k}={v}" for k, v in norm.items() if v),
        ))
    return entries


# ------------------------------------------------------------------ matching

def _time_within(a: str, b: str, hours: int) -> bool:
    try:
        ta = datetime.fromisoformat(a)
        tb = datetime.fromisoformat(b)
    except ValueError:
        return False
    return abs((ta - tb).total_seconds()) <= hours * 3600


def _merchant_similarity(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    la, lb = a.lower(), b.lower()
    if la == lb:
        return 1.0
    if la in lb or lb in la:
        return 0.6
    return 0.0


def _score_candidate(entry: StatementEntry, row: dict, window_hours: int,
                     amount_tol: float, strict: bool) -> float:
    if row.get("direction") != entry.direction:
        return -1.0
    amount_diff = abs(float(row.get("amount") or 0) - entry.amount)
    if strict and amount_diff > amount_tol:
        return -1.0
    if not _time_within(entry.occurred_at, row.get("occurred_at") or "", window_hours):
        return -1.0
    merchant_score = _merchant_similarity(entry.merchant, row.get("merchant"))
    # Loose pass: only accept when the merchant match is strong enough to
    # rule out a coincidental time/direction overlap.
    if not strict and merchant_score < 0.6:
        return -1.0
    try:
        delta = abs((datetime.fromisoformat(entry.occurred_at)
                     - datetime.fromisoformat(row["occurred_at"])).total_seconds())
    except ValueError:
        delta = window_hours * 3600
    time_score = max(0.0, 1.0 - delta / (window_hours * 3600))
    return 0.6 * time_score + 0.4 * merchant_score


def reconcile(
    db_rows: Iterable[dict],
    entries: Iterable[StatementEntry],
    window_hours: int = 48,
    amount_tol: float = 0.01,
) -> ReconcileReport:
    """Greedy match: for each statement entry pick the best unused DB row."""
    db_pool = [dict(r) for r in db_rows if r.get("status") != "deleted"]
    entries_list = list(entries)
    used: set[int] = set()
    report = ReconcileReport()

    # Sort entries chronologically so the greedy walk is stable.
    entries_sorted = sorted(entries_list, key=lambda e: e.occurred_at)
    unmatched: list[StatementEntry] = []

    # Pass 1 -- strict (amount within tol).
    for entry in entries_sorted:
        best_idx, best_score = -1, -1.0
        for i, row in enumerate(db_pool):
            if i in used:
                continue
            score = _score_candidate(entry, row, window_hours, amount_tol, strict=True)
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx >= 0 and best_score >= 0:
            used.add(best_idx)
            report.matched.append({"entry": asdict(entry), "db_id": db_pool[best_idx]["id"]})
        else:
            unmatched.append(entry)

    # Pass 2 -- loose (amount may differ, but merchant + time must line up)
    # so we can surface real "金额不符" instead of treating them as pure missing.
    still_unmatched: list[StatementEntry] = []
    for entry in unmatched:
        best_idx, best_score = -1, -1.0
        for i, row in enumerate(db_pool):
            if i in used:
                continue
            score = _score_candidate(entry, row, window_hours, amount_tol, strict=False)
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx >= 0 and best_score >= 0:
            used.add(best_idx)
            row = db_pool[best_idx]
            report.amount_mismatch.append({
                "entry": asdict(entry), "db_row": row,
                "delta": round(entry.amount - float(row["amount"]), 2),
            })
        else:
            still_unmatched.append(entry)

    report.missing_in_db.extend(asdict(e) for e in still_unmatched)

    for i, row in enumerate(db_pool):
        if i not in used:
            # Skip non-expense/income directions to reduce false "missing" noise
            # (splits, transfers, user notes etc.).
            if row.get("direction") in ("split", "transfer"):
                continue
            report.missing_in_statement.append(row)

    report.stats = {
        "total_statement_entries": len(entries_list),
        "total_db_rows": len(db_pool),
        "matched": len(report.matched),
        "missing_in_db": len(report.missing_in_db),
        "missing_in_statement": len(report.missing_in_statement),
        "amount_mismatch": len(report.amount_mismatch),
    }
    return report


# ------------------------------------------------------------------ import

def bulk_import(db: DBManager, entries: Iterable[dict],
                source: str = "reconcile", default_account: Optional[str] = None) -> dict:
    """Insert approved entries (dicts shaped like ``StatementEntry``) into
    ``transactions``. Relies on V1 ``dedup_hash`` to refuse collisions."""
    created: list[int] = []
    duplicates: list[dict] = []
    for raw in entries:
        tx = Transaction(
            amount=float(raw["amount"]),
            direction=raw.get("direction", "expense"),
            merchant=raw.get("merchant"),
            account=default_account or raw.get("channel"),
            raw_text=raw.get("description") or raw.get("raw"),
            confidence=0.9,
            status="confirmed",
            source=source,
            note=raw.get("external_id"),
            occurred_at=raw["occurred_at"],
        )
        new_id = db.insert_transaction(tx)
        if new_id is None:
            duplicates.append(raw)
        else:
            created.append(new_id)
    return {"created": created, "duplicates": duplicates}
