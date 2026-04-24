import pytest

from expense_tracker.db import DBManager, Transaction
from expense_tracker.modules.reconcile import (
    StatementEntry,
    bulk_import,
    parse_statement,
    reconcile,
)


# A synthetic but faithful WeChat bill.
WECHAT_CSV = """微信支付账单明细
微信昵称：测试
起始时间：[2026-04-01 00:00:00]
终止时间：[2026-04-30 23:59:59]
----------------------微信支付账单明细列表--------------------
交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注
2026-04-02 12:03:00,商户消费,瑞幸咖啡,冰美式,支出,¥15.00,零钱,支付成功,4200001111,M001,/
2026-04-03 19:15:00,商户消费,肯德基,套餐,支出,¥35.50,零钱,支付成功,4200002222,M002,/
2026-04-04 09:00:00,转账,张三,转账,支出,¥200.00,零钱,支付失败,4200003333,M003,/
"""

ALIPAY_CSV = """------------------------------------------------------------------------------------
支付宝（中国）网络技术有限公司  电子客户回单
------------------------------------------------------------------------------------
交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
2026-04-05 08:30:00,餐饮美食,星巴克,s@example.com,拿铁,支出,28.00,余额,交易成功,2026040501,X1,/
2026-04-06 21:00:00,交通出行,滴滴出行,d@example.com,快车,支出,42.50,花呗,交易成功,2026040602,X2,/
"""


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def test_parse_wechat_skips_failed_rows():
    entries = parse_statement(WECHAT_CSV)
    assert [e.channel for e in entries] == ["wechat", "wechat"]
    assert [e.merchant for e in entries] == ["瑞幸咖啡", "肯德基"]
    assert [e.amount for e in entries] == [15.00, 35.50]
    assert all(e.direction == "expense" for e in entries)
    assert entries[0].external_id == "4200001111"


def test_parse_alipay():
    entries = parse_statement(ALIPAY_CSV)
    assert [e.channel for e in entries] == ["alipay", "alipay"]
    assert [e.merchant for e in entries] == ["星巴克", "滴滴出行"]
    assert [e.amount for e in entries] == [28.00, 42.50]


def test_reconcile_matches_and_misses(db):
    # DB has one tx matching the first wechat row; the second is missing.
    db.insert_transaction(Transaction(
        amount=15.0, direction="expense", merchant="瑞幸咖啡",
        occurred_at="2026-04-02T12:05:00",
    ))
    entries = parse_statement(WECHAT_CSV)
    report = reconcile(db.list_transactions(limit=1000), entries)
    assert report.stats["matched"] == 1
    assert report.stats["missing_in_db"] == 1
    assert report.stats["amount_mismatch"] == 0
    assert report.missing_in_db[0]["merchant"] == "肯德基"


def test_reconcile_amount_mismatch(db):
    db.insert_transaction(Transaction(
        amount=14.50, direction="expense", merchant="瑞幸咖啡",
        occurred_at="2026-04-02T12:00:00",
    ))
    # Fake statement says 15.00.
    entries = [StatementEntry(
        occurred_at="2026-04-02T12:05:00", amount=15.00, direction="expense",
        merchant="瑞幸咖啡", channel="wechat",
    )]
    report = reconcile(db.list_transactions(limit=1000), entries)
    assert report.stats["amount_mismatch"] == 1
    assert report.amount_mismatch[0]["delta"] == 0.5


def test_reconcile_missing_in_statement_flags_local_only(db):
    db.insert_transaction(Transaction(
        amount=7.0, direction="expense", merchant="煎饼摊", source="manual",
        occurred_at="2026-04-07T08:00:00",
    ))
    report = reconcile(db.list_transactions(limit=1000), [])
    assert report.stats["missing_in_statement"] == 1
    assert report.missing_in_statement[0]["merchant"] == "煎饼摊"


def test_bulk_import_and_dedup(db):
    entries = [{
        "amount": 15.0, "direction": "expense", "merchant": "瑞幸咖啡",
        "occurred_at": "2026-04-02T12:03:00", "channel": "wechat",
        "external_id": "4200001111", "description": "冰美式",
    }]
    out1 = bulk_import(db, entries)
    assert len(out1["created"]) == 1
    assert len(out1["duplicates"]) == 0
    # Importing the same thing again hits the V1 dedup_hash UNIQUE.
    out2 = bulk_import(db, entries)
    assert len(out2["created"]) == 0
    assert len(out2["duplicates"]) == 1


def test_empty_or_unknown_text_is_empty():
    assert parse_statement("") == []
    assert parse_statement("not a statement") == []
