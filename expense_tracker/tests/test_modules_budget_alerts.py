from datetime import datetime, timedelta

import pytest

from expense_tracker.db import DBManager, Transaction
from expense_tracker.modules.budget_alerts import budget_status, detect_anomalies


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def _insert(db, when: datetime, amount: float, category: str, merchant="m"):
    db.insert_transaction(Transaction(
        amount=amount, merchant=merchant, category=category, direction="expense",
        occurred_at=when.isoformat(timespec="seconds"),
    ))


def test_budget_state_ok_warn_over(db):
    now = datetime.now().replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    _insert(db, now, 1600, "餐饮")        # 80% of 2000 -> warn
    _insert(db, now, 650, "交通")         # 108% of 600 -> over
    _insert(db, now, 100, "娱乐")         # 25% of 400 -> low
    status = budget_status(db, {"餐饮": 2000, "交通": 600, "娱乐": 400}, reference=now)
    by_cat = {s["category"]: s for s in status}
    assert by_cat["餐饮"]["state"] == "warn"
    assert by_cat["交通"]["state"] == "over"
    assert by_cat["娱乐"]["state"] == "low"
    assert by_cat["交通"]["pct"] > 100


def test_budget_ignores_other_months(db):
    now = datetime.now().replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    last_month = now - timedelta(days=35)
    _insert(db, last_month, 5000, "餐饮")  # should NOT count
    status = budget_status(db, {"餐饮": 2000}, reference=now)
    assert status[0]["used"] == 0


def test_anomaly_category_spike(db):
    now = datetime(2026, 4, 23, 12, 0, 0)
    # baseline: low spending per day for last 90 days (approx)
    for i in range(40, 120):
        _insert(db, now - timedelta(days=i), 5.0, "餐饮")
    # recent: very high
    for i in range(0, 30):
        _insert(db, now - timedelta(days=i, hours=1), 40.0, "餐饮")
    alerts = detect_anomalies(db, sigma=2.0, reference=now)
    assert any(a["kind"] == "category_spike" and a["category"] == "餐饮" for a in alerts)


def test_anomaly_new_merchant_spike(db):
    now = datetime(2026, 4, 23, 12, 0, 0)
    # baseline merchants
    for i in range(40, 90):
        _insert(db, now - timedelta(days=i), 20.0, "餐饮", merchant="瑞幸")
    # a single huge new-merchant charge
    _insert(db, now - timedelta(days=1), 500.0, "餐饮", merchant="高档日料")
    alerts = detect_anomalies(db, sigma=2.0, reference=now)
    assert any(a["kind"] == "new_merchant_spike" and a["merchant"] == "高档日料" for a in alerts)


def test_no_anomalies_without_baseline(db):
    now = datetime(2026, 4, 23, 12, 0, 0)
    _insert(db, now, 999, "餐饮")
    alerts = detect_anomalies(db, reference=now)
    # only 1 recent, no baseline -> nothing to compare against
    assert all(a["kind"] != "category_spike" for a in alerts)
