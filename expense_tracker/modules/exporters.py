"""Extended exporters: Beancount, GnuCash CSV, and privacy-stats.

- ``to_beancount`` produces plain-text Beancount entries suitable for
  ``bean-check``. Accounts are derived from direction + category.
- ``to_gnucash_csv`` emits the "Transactions" CSV shape that GnuCash's
  importer understands.
- ``privacy_stats`` returns bucketized aggregates that carry no
  merchant, raw_text, or individual amounts -- safe to share.
"""

from __future__ import annotations

import csv
import io
import re as _re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

ASSET_ACCOUNT = "Assets:Cash"
INCOME_ROOT = "Income"
EXPENSE_ROOT = "Expenses"


def _safe_account_segment(name: str) -> str:
    # Beancount components must start with a capital letter (or CJK) and
    # contain only alphanumerics/hyphens.
    cleaned = _re.sub(r"[^\w一-鿿-]", "-", name).strip("-")
    if not cleaned:
        return "Misc"
    first = cleaned[0]
    if not first.isupper() and not ("一" <= first <= "鿿"):
        cleaned = cleaned.capitalize()
    return cleaned


def _beancount_account(direction: str, category: str | None, subcategory: str | None) -> str:
    root = INCOME_ROOT if direction in ("income", "refund") else EXPENSE_ROOT
    parts = [p for p in [category or "Uncategorized", subcategory] if p]
    return ":".join([root, *(_safe_account_segment(p) for p in parts)])


def to_beancount(rows: Iterable[dict], base_currency: str = "CNY") -> str:
    buf = io.StringIO()
    buf.write(f"option \"title\" \"Expense Tracker Export\"\n")
    buf.write(f"option \"operating_currency\" \"{base_currency}\"\n\n")
    buf.write(f"1970-01-01 open {ASSET_ACCOUNT} {base_currency}\n\n")
    opened_accounts: set[str] = {ASSET_ACCOUNT}
    entries = []
    for r in rows:
        if r.get("status") == "deleted":
            continue
        occurred = r.get("occurred_at") or ""
        try:
            day = datetime.fromisoformat(occurred).date().isoformat()
        except ValueError:
            day = datetime.now().date().isoformat()
        direction = r.get("direction") or "expense"
        account = _beancount_account(direction, r.get("category"), r.get("subcategory"))
        opened_accounts.add(account)
        amount = float(r.get("amount") or 0)
        narration = (r.get("merchant") or "").replace("\"", "'") or "(merchant unknown)"
        if direction == "expense":
            entries.append(
                f"{day} * \"{narration}\"\n"
                f"  {account}      {amount:.2f} {base_currency}\n"
                f"  {ASSET_ACCOUNT}  -{amount:.2f} {base_currency}\n"
            )
        else:
            entries.append(
                f"{day} * \"{narration}\"\n"
                f"  {ASSET_ACCOUNT}  {amount:.2f} {base_currency}\n"
                f"  {account}     -{amount:.2f} {base_currency}\n"
            )
    for acc in sorted(opened_accounts - {ASSET_ACCOUNT}):
        buf.write(f"1970-01-01 open {acc} {base_currency}\n")
    buf.write("\n")
    buf.write("\n".join(entries))
    return buf.getvalue()


def to_gnucash_csv(rows: Iterable[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Description", "Account", "Deposit", "Withdrawal", "Category", "Notes"])
    for r in rows:
        if r.get("status") == "deleted":
            continue
        direction = r.get("direction") or "expense"
        amount = float(r.get("amount") or 0)
        deposit = amount if direction in ("income", "refund") else ""
        withdrawal = amount if direction == "expense" else ""
        writer.writerow([
            (r.get("occurred_at") or "").split("T")[0],
            r.get("merchant") or "",
            r.get("account") or ASSET_ACCOUNT,
            deposit,
            withdrawal,
            "/".join(filter(None, [r.get("category"), r.get("subcategory")])),
            r.get("note") or "",
        ])
    return buf.getvalue()


def privacy_stats(rows: Iterable[dict]) -> dict:
    """Return a privacy-safe aggregate: no merchant, no raw text, no single amounts."""
    buckets = (0, 20, 50, 100, 300, 1000)
    bucket_labels = ["<20", "20-50", "50-100", "100-300", "300-1000", ">=1000"]
    by_category: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "sum": 0.0, "buckets": Counter(),
    })
    for r in rows:
        if r.get("status") == "deleted" or r.get("direction") != "expense":
            continue
        amount = float(r.get("amount") or 0)
        cat = r.get("category") or "其他"
        entry = by_category[cat]
        entry["count"] += 1
        entry["sum"] += amount
        idx = 0
        for i, b in enumerate(buckets[1:], start=1):
            if amount < b:
                idx = i - 1
                break
        else:
            idx = len(buckets) - 1
        entry["buckets"][bucket_labels[idx]] += 1
    for cat, entry in by_category.items():
        entry["sum"] = round(entry["sum"], 2)
        entry["buckets"] = dict(entry["buckets"])
    return dict(by_category)
