import pytest

from expense_tracker.db import DBManager
from expense_tracker.modules.merchant_alias import MerchantAlias


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def test_normalize_passthrough_when_no_alias(db):
    a = MerchantAlias(db)
    assert a.normalize("瑞幸咖啡") == "瑞幸咖啡"


def test_add_and_normalize(db):
    a = MerchantAlias(db)
    a.add("瑞幸咖啡(上海店)", "瑞幸咖啡")
    assert a.normalize("瑞幸咖啡(上海店)") == "瑞幸咖啡"
    assert a.normalize("瑞幸咖啡") == "瑞幸咖啡"


def test_suggest_parenthetical_suffix(db):
    a = MerchantAlias(db)
    db.upsert_merchant_history("瑞幸咖啡", "餐饮", "咖啡")
    s = a.suggest("瑞幸咖啡(静安店)")
    assert s is not None
    assert s.canonical == "瑞幸咖啡"
    # parenthetical suffix is stripped by the normaliser, which is the
    # strongest signal; "substring" would be the next-best reason.
    assert s.reason in ("normalised-match", "substring")


def test_suggest_substring_no_paren(db):
    a = MerchantAlias(db)
    db.upsert_merchant_history("瑞幸", "餐饮", "咖啡")
    s = a.suggest("瑞幸咖啡总店")
    assert s is not None
    assert s.canonical == "瑞幸"
    assert s.reason == "substring"


def test_suggest_edit_distance(db):
    a = MerchantAlias(db)
    db.upsert_merchant_history("Luckin Coffee", "餐饮", "咖啡")
    s = a.suggest("Luckn Coffee")
    assert s is not None
    assert s.canonical == "Luckin Coffee"
    assert s.reason.startswith("edit-distance-")


def test_suggest_none_when_no_match(db):
    a = MerchantAlias(db)
    db.upsert_merchant_history("万豪酒店", "居住", "酒店")
    assert a.suggest("瑞幸咖啡") is None


def test_merge_history_combines_hit_count(db):
    a = MerchantAlias(db)
    db.upsert_merchant_history("瑞幸", "餐饮", "咖啡")
    db.upsert_merchant_history("瑞幸", "餐饮", "咖啡")   # hit_count=2
    db.upsert_merchant_history("瑞幸咖啡", "餐饮", "咖啡")  # hit_count=1
    a.merge_history("瑞幸", "瑞幸咖啡")
    # the alias record is removed, canonical absorbs hit_count
    import sqlite3
    with sqlite3.connect(db.path) as conn:
        rows = dict(conn.execute("SELECT merchant, hit_count FROM merchant_history").fetchall())
    assert "瑞幸" not in rows
    assert rows["瑞幸咖啡"] == 3
    assert a.normalize("瑞幸") == "瑞幸咖啡"


def test_remove_alias(db):
    a = MerchantAlias(db)
    a.add("x", "y")
    assert a.remove("x") is True
    assert a.remove("x") is False
    assert a.normalize("x") == "x"
