from datetime import date, datetime, timedelta

import pytest

from expense_tracker.db import DBManager, Transaction
from expense_tracker.modules.daily_digest import (
    DigestScheduler,
    build_digest,
    run_digest_job,
)
from expense_tracker.modules.notifier import NullNotifier


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def _insert(db, when: datetime, amount: float, category: str, merchant: str = "m", direction="expense"):
    db.insert_transaction(Transaction(
        amount=amount, merchant=merchant, category=category, direction=direction,
        occurred_at=when.isoformat(timespec="seconds"),
    ))


def test_digest_zero_today(db):
    title, body = build_digest(db, on=date.today())
    assert "¥0" in title
    assert "连续打卡" in body


def test_digest_content_today(db):
    today = date.today()
    _insert(db, datetime.combine(today, datetime.min.time()).replace(hour=12), 60.0, "餐饮", "瑞幸")
    _insert(db, datetime.combine(today, datetime.min.time()).replace(hour=13), 30.0, "餐饮", "肯德基")
    _insert(db, datetime.combine(today, datetime.min.time()).replace(hour=18), 50.0, "交通", "滴滴")
    title, body = build_digest(db, on=today)
    assert "¥140" in title
    assert "餐饮" in body and "交通" in body
    assert "连续打卡" in body


def test_streak_counter(db):
    today = date.today()
    for i in range(5):
        _insert(db, datetime.combine(today - timedelta(days=i), datetime.min.time()).replace(hour=12),
                10.0, "餐饮", f"m{i}")
    title, body = build_digest(db, on=today)
    assert "5 天" in body


def test_run_digest_job_with_null_notifier(db):
    today = date.today()
    _insert(db, datetime.combine(today, datetime.min.time()).replace(hour=12), 12, "餐饮", "x")
    assert run_digest_job(db, NullNotifier()) is True


def test_scheduler_next_run_seconds_future_today():
    s = DigestScheduler(23, 59, callback=lambda: None)
    now = datetime(2026, 4, 23, 10, 0, 0)
    secs = s._next_run_seconds(now=now)
    # 23:59 today from 10:00 = 13h 59m = 50340s
    assert 50000 <= secs <= 51000


def test_scheduler_next_run_seconds_rolls_to_tomorrow():
    s = DigestScheduler(8, 0, callback=lambda: None)
    now = datetime(2026, 4, 23, 10, 0, 0)
    secs = s._next_run_seconds(now=now)
    # 8:00 tomorrow from 10:00 today = 22h = 79200s
    assert 78000 <= secs <= 80000


def test_scheduler_rejects_invalid_time():
    with pytest.raises(ValueError):
        DigestScheduler(25, 0, callback=lambda: None)
