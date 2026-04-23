"""Monthly budget status + anomaly alerts.

Budgets come from ``config.yaml`` (``budgets.monthly``) -- lightweight,
no new table. Anomalies are derived on the fly from the last N days of
``transactions``; no separate store to keep in sync.

Alerts are **informational only** -- they never block inserts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from ..db import DBManager


def budget_status(db: DBManager, monthly_budgets: dict[str, float],
                  reference: Optional[datetime] = None) -> list[dict]:
    """Return ``[{category, used, limit, pct, remaining, state}]`` for the
    calendar month of ``reference`` (default: now)."""
    now = reference or datetime.now()
    # Calendar-month window (not rolling 30 days).
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cutoff_iso = month_start.isoformat()

    rows = [
        r for r in db.list_transactions(limit=100_000)
        if r.get("direction") == "expense" and (r.get("occurred_at") or "") >= cutoff_iso
    ]
    used_by_cat: dict[str, float] = defaultdict(float)
    for r in rows:
        used_by_cat[r.get("category") or "其他"] += float(r.get("amount") or 0)

    out: list[dict] = []
    for cat, limit in monthly_budgets.items():
        used = round(used_by_cat.get(cat, 0.0), 2)
        pct = round(used / limit * 100, 1) if limit else 0.0
        remaining = round(limit - used, 2)
        if pct >= 100:
            state = "over"
        elif pct >= 80:
            state = "warn"
        elif pct >= 50:
            state = "ok"
        else:
            state = "low"
        out.append({
            "category": cat, "used": used, "limit": float(limit),
            "pct": pct, "remaining": remaining, "state": state,
        })
    out.sort(key=lambda r: r["pct"], reverse=True)
    return out


def detect_anomalies(db: DBManager, window_days: int = 90,
                     lookback_days: int = 30, sigma: float = 2.0,
                     reference: Optional[datetime] = None) -> list[dict]:
    """Find categories where spending in the last ``lookback_days`` is
    significantly above the baseline built from the preceding ``window_days``.

    Output: ``[{category, recent, baseline_mean, baseline_std, z, kind}]``
    sorted by severity.
    """
    now = reference or datetime.now()
    recent_cutoff = (now - timedelta(days=lookback_days)).isoformat()
    baseline_cutoff = (now - timedelta(days=lookback_days + window_days)).isoformat()

    rows = db.list_transactions(limit=100_000)
    recent: dict[str, list[float]] = defaultdict(list)
    baseline: dict[str, list[float]] = defaultdict(list)

    for r in rows:
        if r.get("direction") != "expense":
            continue
        when = r.get("occurred_at") or ""
        amount = float(r.get("amount") or 0)
        cat = r.get("category") or "其他"
        if when >= recent_cutoff:
            recent[cat].append(amount)
        elif when >= baseline_cutoff:
            baseline[cat].append(amount)

    alerts: list[dict] = []
    for cat, vals in recent.items():
        if not vals:
            continue
        recent_sum = round(sum(vals), 2)
        base_vals = baseline.get(cat, [])
        if len(base_vals) < 3:
            continue  # not enough data
        # Compare *daily* averages to make window length irrelevant.
        recent_daily = recent_sum / lookback_days
        base_daily_mean = sum(base_vals) / window_days
        # Standard deviation of daily spend in baseline (chunk by day).
        day_buckets: dict[str, float] = defaultdict(float)
        for r in rows:
            if r.get("direction") != "expense" or (r.get("category") or "其他") != cat:
                continue
            when = r.get("occurred_at") or ""
            if baseline_cutoff <= when < recent_cutoff:
                day = when[:10]
                day_buckets[day] += float(r.get("amount") or 0)
        if not day_buckets:
            continue
        mean = base_daily_mean
        var = sum((v - mean) ** 2 for v in day_buckets.values()) / max(1, len(day_buckets))
        std = math.sqrt(var) if var > 0 else 0.0
        z = (recent_daily - mean) / std if std > 0 else 0.0
        if z >= sigma or recent_daily >= mean * 1.5:
            alerts.append({
                "category": cat,
                "kind": "category_spike",
                "recent_total": recent_sum,
                "recent_daily_avg": round(recent_daily, 2),
                "baseline_daily_avg": round(mean, 2),
                "baseline_std": round(std, 2),
                "z": round(z, 2),
            })

    # Merchant-level: a *new* merchant (not seen in baseline) whose single
    # transaction is >= 3x the user's usual per-transaction median.
    all_amounts = [float(r["amount"]) for r in rows
                   if r.get("direction") == "expense" and r.get("amount")]
    if all_amounts:
        sorted_a = sorted(all_amounts)
        median = sorted_a[len(sorted_a) // 2]
        known_merchants: set[str] = {
            r["merchant"] for r in rows
            if (r.get("occurred_at") or "") < recent_cutoff and r.get("merchant")
        }
        for r in rows:
            if (r.get("occurred_at") or "") < recent_cutoff:
                continue
            m = r.get("merchant")
            if not m or m in known_merchants:
                continue
            amt = float(r.get("amount") or 0)
            if amt >= max(median * 3, 100):  # hard floor so tiny medians don't trigger
                alerts.append({
                    "kind": "new_merchant_spike",
                    "merchant": m,
                    "amount": round(amt, 2),
                    "median": round(median, 2),
                    "occurred_at": r.get("occurred_at"),
                })

    alerts.sort(key=lambda a: a.get("z", 0) + (1 if a["kind"] == "new_merchant_spike" else 0),
                reverse=True)
    return alerts
