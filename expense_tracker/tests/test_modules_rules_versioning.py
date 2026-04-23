import json

import pytest

from expense_tracker.db import DBManager
from expense_tracker.modules.rules_versioning import RulesVersioning


@pytest.fixture()
def db(tmp_path):
    d = DBManager(tmp_path / "t.db")
    d.seed_category_map([("瑞幸", "餐饮", "咖啡")])
    return d


def test_snapshot_creates_versioned_file(db, tmp_path):
    rv = RulesVersioning(db, dir_path=tmp_path / "rules")
    p1 = rv.snapshot(note="initial")
    p2 = rv.snapshot(note="second")
    assert p1.name == "rules_v1.json"
    assert p2.name == "rules_v2.json"
    payload = json.loads(p2.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert any(r["keyword"] == "瑞幸" for r in payload["category_map"])


def test_list_versions(db, tmp_path):
    rv = RulesVersioning(db, dir_path=tmp_path / "rules")
    rv.snapshot()
    rv.snapshot(note="tweak")
    versions = rv.list_versions()
    assert [v["version"] for v in versions] == [1, 2]


def test_rollback_replaces_category_map(db, tmp_path):
    rv = RulesVersioning(db, dir_path=tmp_path / "rules")
    rv.snapshot(note="v1")
    db.seed_category_map([("麦当劳", "餐饮", "快餐")])
    assert any(r["keyword"] == "麦当劳" for r in db.list_category_map())
    rv.rollback(1)
    assert all(r["keyword"] != "麦当劳" for r in db.list_category_map())
