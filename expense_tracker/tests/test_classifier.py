import os
import tempfile

import pytest

from expense_tracker.classifier import Classifier
from expense_tracker.db import DBManager, Transaction


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "test.db")


def test_keyword_match(db):
    c = Classifier(db)
    r = c.classify("瑞幸咖啡", "微信支付 -15元 商户：瑞幸咖啡")
    assert r.category == "餐饮"
    assert r.subcategory == "咖啡"
    assert r.source == "keyword"


def test_history_overrides_keyword(db):
    c = Classifier(db)
    # User corrected: 京东 should be classified as 办公 instead of 购物
    c.remember("京东自营商城", "办公", "耗材")
    r = c.classify("京东自营商城", "支付宝 -200 商户：京东自营商城")
    assert r.category == "办公"
    assert r.subcategory == "耗材"
    assert r.source == "history"


def test_fallback_when_unknown(db):
    c = Classifier(db)
    r = c.classify("某神秘小店", "支付宝 -3 元")
    assert r.category == "其他"
    assert r.source == "fallback"


def test_dedup_blocks_duplicate_insert(db):
    tx = Transaction(amount=15.0, merchant="瑞幸咖啡", direction="expense",
                     occurred_at="2026-04-23T12:34:00")
    first = db.insert_transaction(tx)
    dup = Transaction(amount=15.0, merchant="瑞幸咖啡", direction="expense",
                      occurred_at="2026-04-23T12:34:30")  # same minute
    second = db.insert_transaction(dup)
    assert first is not None
    assert second is None  # dedup hit


def test_soft_delete_hides_from_listing(db):
    tx_id = db.insert_transaction(Transaction(amount=5.0, merchant="x"))
    assert tx_id is not None
    assert any(r["id"] == tx_id for r in db.list_transactions())
    assert db.soft_delete(tx_id)
    assert all(r["id"] != tx_id for r in db.list_transactions())


def test_stats_only_counts_expense(db):
    db.insert_transaction(Transaction(amount=10, merchant="m1", category="餐饮", direction="expense"))
    db.insert_transaction(Transaction(amount=100, merchant="m2", category="收入", direction="income",
                                      occurred_at="2026-04-23T13:00:00"))
    stats = db.stats_by_category(since_days=365)
    cats = {s["category"]: s["total"] for s in stats}
    assert cats.get("餐饮") == 10
    assert "收入" not in cats
