from expense_tracker.modules.exporters import (
    privacy_stats,
    to_beancount,
    to_gnucash_csv,
)


ROWS = [
    {"id": 1, "occurred_at": "2026-04-20T12:00", "amount": 15.0, "direction": "expense",
     "merchant": "瑞幸咖啡", "category": "餐饮", "subcategory": "咖啡", "status": "confirmed"},
    {"id": 2, "occurred_at": "2026-04-21T09:30", "amount": 4200.0, "direction": "income",
     "merchant": "工资", "category": "收入", "subcategory": None, "status": "confirmed"},
    {"id": 3, "occurred_at": "2026-04-22T18:00", "amount": 99.0, "direction": "expense",
     "merchant": "deleted row", "category": "娱乐", "status": "deleted"},
]


def test_beancount_skips_deleted_and_has_accounts():
    text = to_beancount(ROWS)
    assert "Expenses:餐饮:咖啡" in text
    assert "Income:收入" in text
    assert "deleted row" not in text
    assert "operating_currency" in text


def test_gnucash_csv_header_and_rows():
    text = to_gnucash_csv(ROWS)
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines[0].startswith("Date,")
    body = lines[1:]
    assert len(body) == 2  # deleted row dropped
    assert any("15.0" in l for l in body)


def test_privacy_stats_has_no_merchant():
    stats = privacy_stats(ROWS)
    assert "餐饮" in stats
    assert "收入" not in stats       # only expenses
    dump = str(stats)
    assert "瑞幸咖啡" not in dump
    assert "deleted row" not in dump
    assert stats["餐饮"]["count"] == 1
    assert "<20" in stats["餐饮"]["buckets"]
