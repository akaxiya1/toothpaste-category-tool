import json
from datetime import date

import pytest

from expense_tracker.db import DBManager, Transaction
from expense_tracker.modules.multi_currency import MultiCurrency


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


@pytest.fixture()
def rates(tmp_path):
    path = tmp_path / "fx.json"
    path.write_text(json.dumps({
        "2026-04-22": {"USD": 7.25, "EUR": 7.85},
        "2026-04-20": {"USD": 7.22},
    }), encoding="utf-8")
    return path


def test_base_currency_rate_is_one(db, rates):
    fx = MultiCurrency(db, rate_file=rates, base="CNY")
    assert fx.rate("CNY") == 1.0


def test_exact_date_match(db, rates):
    fx = MultiCurrency(db, rate_file=rates)
    assert fx.rate("USD", on=date(2026, 4, 22)) == 7.25


def test_nearest_earlier_date(db, rates):
    fx = MultiCurrency(db, rate_file=rates)
    # 4-23 not present -> should fall back to 4-22
    assert fx.rate("USD", on=date(2026, 4, 23)) == 7.25


def test_missing_currency_raises(db, rates):
    fx = MultiCurrency(db, rate_file=rates)
    with pytest.raises(KeyError):
        fx.rate("JPY", on=date(2026, 4, 22))


def test_attach_and_lookup(db, rates):
    fx = MultiCurrency(db, rate_file=rates)
    tx_id = db.insert_transaction(Transaction(amount=72.5, merchant="Starbucks NYC"))
    assert tx_id is not None
    fx.attach(tx_id, "USD", 10.0, on=date(2026, 4, 22))
    rec = fx.lookup(tx_id)
    assert rec is not None
    assert rec.currency == "USD"
    assert rec.rate_to_base == 7.25
    assert rec.original_amount == 10.0


def test_to_base_rounds(db, rates):
    fx = MultiCurrency(db, rate_file=rates)
    assert fx.to_base(10.0, "USD", on=date(2026, 4, 22)) == 72.5
