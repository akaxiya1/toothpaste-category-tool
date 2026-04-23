from datetime import datetime, timedelta

import pytest

from expense_tracker.db import DBManager
from expense_tracker.modules.subscription import SubscriptionCalendar, detect


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def test_detect_monthly():
    hint = detect("微信支付 -25.00元 自动续费 iCloud 50GB")
    assert hint is not None
    assert hint.cadence == "monthly"


def test_detect_yearly():
    hint = detect("年付会员 365 元续费成功")
    assert hint and hint.cadence == "yearly"


def test_detect_none():
    assert detect("瑞幸咖啡 -15 元") is None


def test_record_and_upcoming(db):
    cal = SubscriptionCalendar(db)
    occurred = (datetime.now() - timedelta(days=28)).isoformat(timespec="seconds")
    cal.record("iCloud", 25.0, "monthly", occurred_at=occurred)
    # monthly cadence = 30 days, so next_due is ~2 days away -> visible within 7 days
    upcoming = cal.upcoming(within_days=7)
    assert any(s["merchant"] == "iCloud" for s in upcoming)


def test_upsert_updates_next_due(db):
    cal = SubscriptionCalendar(db)
    cal.record("Netflix", 45.0, "monthly",
               occurred_at=(datetime.now() - timedelta(days=40)).isoformat())
    cal.record("Netflix", 45.0, "monthly",
               occurred_at=datetime.now().isoformat())
    upcoming = cal.upcoming(within_days=365)
    netflix = [s for s in upcoming if s["merchant"] == "Netflix"]
    assert len(netflix) == 1  # upsert, not duplicate
