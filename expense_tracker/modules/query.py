"""Natural-language-ish query for the Ctrl+K command palette.

Rule-based parser; zero ML dependency, predictable latency. Supported::

    上周咖啡 超过50
    本月 打车
    > 100 餐饮
    4月 瑞幸
    2026-04-01 ~ 2026-04-15 京东
    昨天

Extracted filters (any may be absent):
    - time window: 今天 / 昨天 / 前天 / 本周 / 上周 / 本月 / 上月 / 今年 /
                   YYYY-MM-DD / YYYY-MM / M月D日 / range "A ~ B"
    - amount range: 超过N / 低于N / > N / < N / N~M
    - category / subcategory (matched against DB's known set)
    - merchant substring: leftover tokens
"""

from __future__ import annotations

import re
import sqlite3
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from ..db import DBManager


@dataclass
class QueryFilter:
    since: Optional[str] = None
    until: Optional[str] = None
    amount_gte: Optional[float] = None
    amount_lte: Optional[float] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    merchant_contains: Optional[str] = None
    direction: Optional[str] = None
    free_text: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], "")}


@dataclass
class QueryResult:
    rows: list[dict]
    total_count: int
    total_amount: float
    filter: QueryFilter

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "total_count": self.total_count,
            "total_amount": round(self.total_amount, 2),
            "filter": self.filter.to_dict(),
        }


# ------------------------------------------------------------------ time

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _day_start(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time())


def _day_end(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time().replace(microsecond=0))


def _parse_relative(token: str, now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    now = now or datetime.now()
    today = now.date()
    if token in ("今天", "today"):
        return _iso(_day_start(today)), _iso(_day_end(today))
    if token in ("昨天", "yesterday"):
        y = today - timedelta(days=1)
        return _iso(_day_start(y)), _iso(_day_end(y))
    if token == "前天":
        y = today - timedelta(days=2)
        return _iso(_day_start(y)), _iso(_day_end(y))
    if token in ("本周", "this-week"):
        start = today - timedelta(days=today.weekday())
        return _iso(_day_start(start)), _iso(_day_end(today))
    if token in ("上周", "last-week"):
        this_mon = today - timedelta(days=today.weekday())
        last_mon = this_mon - timedelta(days=7)
        last_sun = this_mon - timedelta(days=1)
        return _iso(_day_start(last_mon)), _iso(_day_end(last_sun))
    if token in ("本月", "this-month"):
        start = today.replace(day=1)
        return _iso(_day_start(start)), _iso(_day_end(today))
    if token in ("上月", "last-month"):
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        return _iso(_day_start(first_prev)), _iso(_day_end(last_prev))
    if token in ("今年", "this-year"):
        start = today.replace(month=1, day=1)
        return _iso(_day_start(start)), _iso(_day_end(today))
    if token in ("去年", "last-year"):
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
        return _iso(_day_start(start)), _iso(_day_end(end))
    return None


_DATE_FULL = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_DATE_MONTH = re.compile(r"\b(\d{4})[-/](\d{1,2})\b")
_DATE_CN = re.compile(r"\b(\d{1,2})月(\d{1,2})日\b")
_DATE_CN_MONTH = re.compile(r"\b(\d{1,2})月\b")


def _match_absolute_date(tok: str, now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    now = now or datetime.now()
    if m := _DATE_FULL.fullmatch(tok):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return _iso(_day_start(d)), _iso(_day_end(d))
    if m := _DATE_MONTH.fullmatch(tok):
        y, mo = int(m[1]), int(m[2])
        start = date(y, mo, 1)
        end = date(y, mo, monthrange(y, mo)[1])
        return _iso(_day_start(start)), _iso(_day_end(end))
    if m := _DATE_CN.fullmatch(tok):
        y = now.year
        d = date(y, int(m[1]), int(m[2]))
        return _iso(_day_start(d)), _iso(_day_end(d))
    if m := _DATE_CN_MONTH.fullmatch(tok):
        y, mo = now.year, int(m[1])
        start = date(y, mo, 1)
        end = date(y, mo, monthrange(y, mo)[1])
        return _iso(_day_start(start)), _iso(_day_end(end))
    return None


# ------------------------------------------------------------------ amount

_AMT_GT = re.compile(r"(?:超过|大于|高于|多于|over)\s*(\d+(?:\.\d+)?)|>\s*(\d+(?:\.\d+)?)", re.I)
_AMT_LT = re.compile(r"(?:低于|小于|少于|under|below)\s*(\d+(?:\.\d+)?)|<\s*(\d+(?:\.\d+)?)", re.I)
_AMT_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[~～-]\s*(\d+(?:\.\d+)?)")


# ------------------------------------------------------------------ parser

def _known_categories(db: DBManager) -> tuple[set[str], set[str]]:
    with sqlite3.connect(db.path) as conn:
        cats = {r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM transactions WHERE status!='deleted' AND category IS NOT NULL"
        )}
        subs = {r[0] for r in conn.execute(
            "SELECT DISTINCT subcategory FROM transactions WHERE status!='deleted' AND subcategory IS NOT NULL"
        )}
    for kw in ("餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "人情", "办公", "其他"):
        cats.add(kw)
    return cats, subs


def parse_query(db: DBManager, text: str, now: Optional[datetime] = None) -> QueryFilter:
    now = now or datetime.now()
    filt = QueryFilter(raw=text)
    if not text.strip():
        return filt

    remaining = text.strip()

    # 1) absolute dates FIRST (so "2026-04" isn't mistaken for "2026 ~ 4" range)
    for pattern in (_DATE_FULL, _DATE_MONTH, _DATE_CN, _DATE_CN_MONTH):
        if m := pattern.search(remaining):
            hit = _match_absolute_date(m.group(0), now=now)
            if hit:
                filt.since = filt.since or hit[0]
                filt.until = hit[1]
                remaining = pattern.sub(" ", remaining, count=1)
                break

    # 2) relative time phrases (longest first to avoid "上周" vs "周")
    for phrase in ("前天", "昨天", "今天", "上周", "本周", "上月", "本月", "今年", "去年"):
        if phrase in remaining:
            hit = _parse_relative(phrase, now=now)
            if hit:
                filt.since, filt.until = hit
                remaining = remaining.replace(phrase, " ", 1)
                break

    # 3) amount filters (now safe: date fragments already consumed)
    if m := _AMT_GT.search(remaining):
        filt.amount_gte = float(m.group(1) or m.group(2))
        remaining = _AMT_GT.sub(" ", remaining, count=1)
    if m := _AMT_LT.search(remaining):
        filt.amount_lte = float(m.group(1) or m.group(2))
        remaining = _AMT_LT.sub(" ", remaining, count=1)
    if m := _AMT_RANGE.search(remaining):
        lo, hi = float(m[1]), float(m[2])
        if lo > hi:
            lo, hi = hi, lo
        # Reject YYYY-MM shapes even if we somehow got here: year-looking
        # left side with small right side.
        if not (lo >= 1900 and hi <= 12):
            filt.amount_gte = lo
            filt.amount_lte = hi
            remaining = _AMT_RANGE.sub(" ", remaining, count=1)

    # 4) direction words
    for tok, direction in (("支出", "expense"), ("消费", "expense"),
                           ("收入", "income"), ("收款", "income"),
                           ("退款", "refund")):
        if tok in remaining:
            filt.direction = direction
            remaining = remaining.replace(tok, " ", 1)
            break

    # 5) categories / subcategories
    cats, subs = _known_categories(db)
    tokens = [t for t in re.split(r"[\s，,、]+", remaining) if t]
    leftover: list[str] = []
    for tok in tokens:
        if tok in cats and not filt.category:
            filt.category = tok
        elif tok in subs and not filt.subcategory:
            filt.subcategory = tok
        else:
            leftover.append(tok)

    if leftover:
        # Concatenate non-claimed tokens as merchant substring (rough but useful).
        filt.merchant_contains = " ".join(leftover)
        filt.free_text = leftover
    return filt


# ------------------------------------------------------------------ execute

def execute(db: DBManager, filt: QueryFilter, limit: int = 50) -> QueryResult:
    sql = "SELECT * FROM transactions WHERE status != 'deleted'"
    params: list = []
    if filt.since:
        sql += " AND occurred_at >= ?"; params.append(filt.since)
    if filt.until:
        sql += " AND occurred_at <= ?"; params.append(filt.until)
    if filt.amount_gte is not None:
        sql += " AND amount >= ?"; params.append(filt.amount_gte)
    if filt.amount_lte is not None:
        sql += " AND amount <= ?"; params.append(filt.amount_lte)
    if filt.category:
        sql += " AND category = ?"; params.append(filt.category)
    if filt.subcategory:
        sql += " AND subcategory = ?"; params.append(filt.subcategory)
    if filt.direction:
        sql += " AND direction = ?"; params.append(filt.direction)
    if filt.merchant_contains:
        # Fuzzy across merchant + raw_text so queries like "瑞幸" still hit
        # transactions that stored the canonical "Luckin Coffee".
        sql += " AND (merchant LIKE ? OR raw_text LIKE ?)"
        pat = f"%{filt.merchant_contains}%"
        params.extend([pat, pat])
    sql += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)

    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Aggregate: sum across ALL matching rows, not just the limited page.
    count_sql = sql.replace("SELECT *", "SELECT COUNT(*), COALESCE(SUM(amount),0)").replace(" ORDER BY occurred_at DESC LIMIT ?", "")
    with sqlite3.connect(db.path) as conn:
        total_count, total_amount = conn.execute(count_sql, params[:-1]).fetchone()

    return QueryResult(
        rows=rows,
        total_count=int(total_count or 0),
        total_amount=float(total_amount or 0.0),
        filter=filt,
    )
