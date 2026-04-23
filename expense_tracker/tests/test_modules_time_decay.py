import sqlite3
from datetime import datetime, timedelta

import pytest

from expense_tracker.db import DBManager
from expense_tracker.modules.time_decay import DecayingClassifier


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def _set_history(db: DBManager, merchant: str, category: str, hit_count: int, age_days: float):
    updated = (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds")
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            """
            INSERT INTO merchant_history(merchant, category, subcategory, hit_count, updated_at)
            VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(merchant) DO UPDATE SET
                category=excluded.category, hit_count=excluded.hit_count, updated_at=excluded.updated_at
            """,
            (merchant, category, hit_count, updated),
        )


def test_recent_history_wins(db):
    _set_history(db, "某小店", "人情", 3, age_days=2)
    c = DecayingClassifier(db, half_life_days=30)
    r = c.classify("某小店", "无关文本")
    assert r.category == "人情"
    assert r.source == "history_decay"


def test_stale_history_falls_through(db):
    _set_history(db, "某小店", "人情", 1, age_days=180)
    c = DecayingClassifier(db, half_life_days=30, min_weight=0.2)
    r = c.classify("某小店", "瑞幸咖啡 -15")
    # stale history decayed below threshold -> keyword rule picks up "瑞幸"
    assert r.category == "餐饮"
    assert r.source == "keyword"


def test_high_hit_count_survives_longer(db):
    _set_history(db, "常去小店", "餐饮", 50, age_days=60)
    c = DecayingClassifier(db, half_life_days=30)
    r = c.classify("常去小店", "")
    # even after 2 half-lives, a heavily-used merchant should still pass
    assert r.source == "history_decay"
    assert r.category == "餐饮"
