import sqlite3

import pytest

from expense_tracker.db import DBManager, Transaction
from expense_tracker.modules.split_transaction import SplitError, SplitManager, SplitPart


@pytest.fixture()
def db(tmp_path):
    return DBManager(tmp_path / "t.db")


def _parent_status(db: DBManager, tx_id: int) -> str:
    with sqlite3.connect(db.path) as conn:
        row = conn.execute("SELECT status FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    return row[0]


def test_split_exact_match(db):
    parent_id = db.insert_transaction(Transaction(amount=100.0, merchant="团建餐厅", category="餐饮"))
    sm = SplitManager(db)
    ids = sm.split(parent_id, [
        SplitPart(amount=60.0, category="餐饮", subcategory="正餐"),
        SplitPart(amount=40.0, category="交通", subcategory="打车"),
    ])
    assert len(ids) == 2
    assert _parent_status(db, parent_id) == "split"
    assert sm.children_of(parent_id) == ids


def test_split_mismatch_rejected(db):
    parent_id = db.insert_transaction(Transaction(amount=100.0, merchant="x"))
    sm = SplitManager(db)
    with pytest.raises(SplitError):
        sm.split(parent_id, [SplitPart(amount=60.0, category="a"),
                             SplitPart(amount=30.0, category="b")])


def test_split_only_once(db):
    parent_id = db.insert_transaction(Transaction(amount=20.0, merchant="x"))
    sm = SplitManager(db)
    sm.split(parent_id, [SplitPart(amount=10.0, category="a"),
                         SplitPart(amount=10.0, category="b")])
    with pytest.raises(SplitError):
        sm.split(parent_id, [SplitPart(amount=20.0, category="c")])
