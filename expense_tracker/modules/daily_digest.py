"""Build a daily summary + schedule it (default 22:00).

- ``build_digest(db, on)`` → (title, markdown_body). Pure function, tested.
- ``DigestScheduler`` fires at the configured local time. Uses
  ``threading.Timer``; no new dependency. If the app restarts after the
  target time has passed today, the next run is tomorrow.

Content:
    标题: 今日账单 ¥142.50 · vs 上周 +18%
    正文: 分类 Top-3、商户 Top-1、较上周对比、连续打卡天数。
"""

from __future__ import annotations

import threading
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from ..db import DBManager
from .notifier import Notifier, NullNotifier


def _totals_for(db: DBManager, day: date) -> dict:
    start = datetime.combine(day, time.min).isoformat()
    end = datetime.combine(day, time.max).isoformat()
    rows = [
        r for r in db.list_transactions(limit=10_000)
        if start <= r["occurred_at"] <= end and r.get("direction") == "expense"
    ]
    total = round(sum(float(r["amount"]) for r in rows), 2)
    by_cat: Counter[str] = Counter()
    by_merchant: Counter[str] = Counter()
    for r in rows:
        by_cat[r.get("category") or "其他"] += float(r["amount"])
        if r.get("merchant"):
            by_merchant[r["merchant"]] += float(r["amount"])
    return {"total": total, "count": len(rows), "by_category": by_cat, "by_merchant": by_merchant}


def _weekly_avg(db: DBManager, ending: date, days: int = 7) -> float:
    """Mean daily expense across the previous ``days`` days (excluding ``ending``)."""
    totals = []
    for i in range(1, days + 1):
        day = ending - timedelta(days=i)
        totals.append(_totals_for(db, day)["total"])
    if not totals:
        return 0.0
    return round(sum(totals) / len(totals), 2)


def _streak_days(db: DBManager, today: date, max_lookback: int = 120) -> int:
    """Consecutive days (ending today) with at least one confirmed expense."""
    streak = 0
    for i in range(max_lookback):
        day = today - timedelta(days=i)
        if _totals_for(db, day)["count"] > 0:
            streak += 1
        else:
            break
    return streak


def build_digest(db: DBManager, on: Optional[date] = None) -> tuple[str, str]:
    day = on or date.today()
    today = _totals_for(db, day)
    week_avg = _weekly_avg(db, day)
    streak = _streak_days(db, day)

    if today["count"] == 0:
        title = f"今日账单 ¥0 · {day.isoformat()}"
        body = (
            f"今天还没记账。连续打卡：**{streak} 天**\n\n"
            f"如果忘了，打开记账页粘贴账单即可补录。"
        )
        return title, body

    delta_pct = None
    if week_avg > 0:
        delta_pct = round((today["total"] - week_avg) / week_avg * 100)

    sign = "+" if delta_pct is not None and delta_pct >= 0 else ""
    delta_line = f"vs 上周均 ¥{week_avg:.2f} · {sign}{delta_pct}%" if delta_pct is not None else f"vs 上周均 ¥{week_avg:.2f}"
    title = f"今日账单 ¥{today['total']:.2f} · {delta_line}"

    lines: list[str] = []
    lines.append(f"### {day.isoformat()} · 共 {today['count']} 笔 · {delta_line}")
    lines.append("")
    lines.append("**分类 Top**")
    for cat, amount in today["by_category"].most_common(3):
        pct = round(amount / today["total"] * 100)
        lines.append(f"- {cat}: ¥{amount:.2f} ({pct}%)")
    if today["by_merchant"]:
        merchant, amount = today["by_merchant"].most_common(1)[0]
        lines.append("")
        lines.append(f"**今日之最**: {merchant} ¥{amount:.2f}")
    lines.append("")
    lines.append(f"🎯 连续打卡：**{streak} 天**")
    return title, "\n".join(lines)


class DigestScheduler:
    """Fire ``callback`` once per day at ``hh:mm`` local time."""

    def __init__(self, hh: int, mm: int, callback: Callable[[], None]):
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("invalid time")
        self.hh = hh
        self.mm = mm
        self.callback = callback
        self._timer: Optional[threading.Timer] = None
        self._stopped = False

    def start(self) -> None:
        self._schedule()

    def stop(self) -> None:
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()

    def _next_run_seconds(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now()
        target = now.replace(hour=self.hh, minute=self.mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(1.0, (target - now).total_seconds())

    def _schedule(self) -> None:
        if self._stopped:
            return
        delay = self._next_run_seconds()
        self._timer = threading.Timer(delay, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        if self._stopped:
            return
        try:
            self.callback()
        except Exception as exc:  # pragma: no cover
            print(f"[digest] callback error: {exc}")
        self._schedule()


def run_digest_job(db: DBManager, notifier: Notifier) -> bool:
    title, body = build_digest(db)
    result = notifier.send(title, body)
    return bool(result.ok)


def null_job() -> None:
    NullNotifier().send("", "")
