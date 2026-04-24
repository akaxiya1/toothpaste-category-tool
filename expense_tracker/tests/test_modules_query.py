from datetime import datetime

import pytest

from expense_tracker.db import DBManager, Transaction
from expense_tracker.modules.query import execute, parse_query


@pytest.fixture()
def db(tmp_path):
    d = DBManager(tmp_path / "t.db")
    # Seed a small spread of rows across April 2026.
    for day, amt, cat, sub, mer in [
        ("01", 15, "餐饮", "咖啡", "瑞幸咖啡"),
        ("05", 120, "交通", "打车", "滴滴出行"),
        ("09", 35, "餐饮", "快餐", "肯德基"),
        ("15", 600, "居住", "酒店", "万豪酒店"),
        ("22", 8, "餐饮", "咖啡", "瑞幸咖啡"),
    ]:
        d.insert_transaction(Transaction(
            amount=amt, direction="expense", merchant=mer,
            category=cat, subcategory=sub,
            occurred_at=f"2026-04-{day}T12:00:00",
        ))
    return d


NOW = datetime(2026, 4, 23, 15, 0, 0)


def test_parse_amount_gt(db):
    f = parse_query(db, "超过 100", now=NOW)
    assert f.amount_gte == 100
    assert f.amount_lte is None


def test_parse_amount_lt(db):
    f = parse_query(db, "低于 20", now=NOW)
    assert f.amount_lte == 20


def test_parse_amount_range(db):
    f = parse_query(db, "10~100", now=NOW)
    assert f.amount_gte == 10 and f.amount_lte == 100


def test_parse_this_month(db):
    f = parse_query(db, "本月", now=NOW)
    assert f.since.startswith("2026-04-01")
    assert f.until.startswith("2026-04-23")


def test_parse_last_week_is_sane(db):
    f = parse_query(db, "上周", now=NOW)
    # Last week relative to 2026-04-23 (Thu) → Mon 2026-04-13 .. Sun 2026-04-19
    assert f.since.startswith("2026-04-13")
    assert f.until.startswith("2026-04-19")


def test_parse_absolute_month(db):
    f = parse_query(db, "2026-04", now=NOW)
    assert f.since.startswith("2026-04-01")
    assert f.until.startswith("2026-04-30")


def test_parse_mixed(db):
    f = parse_query(db, "本月 咖啡 超过10", now=NOW)
    assert f.amount_gte == 10
    assert f.subcategory == "咖啡"
    assert f.since.startswith("2026-04-01")


def test_parse_merchant_contains(db):
    f = parse_query(db, "瑞幸", now=NOW)
    assert f.merchant_contains == "瑞幸"


def test_execute_category_and_range(db):
    f = parse_query(db, "餐饮 本月", now=NOW)
    r = execute(db, f)
    assert r.total_count == 3  # 3 rows in 餐饮 this month
    assert r.total_amount == 58.0


def test_execute_merchant_substring(db):
    f = parse_query(db, "瑞幸", now=NOW)
    r = execute(db, f)
    assert r.total_count == 2
    assert all(row["merchant"] == "瑞幸咖啡" for row in r.rows)


def test_execute_amount_threshold(db):
    f = parse_query(db, "超过 100", now=NOW)
    r = execute(db, f)
    assert r.total_count == 2
    assert all(row["amount"] >= 100 for row in r.rows)


def test_execute_empty_query_returns_all(db):
    f = parse_query(db, "", now=NOW)
    r = execute(db, f)
    assert r.total_count == 5
