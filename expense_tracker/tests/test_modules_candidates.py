import pytest

from expense_tracker.classifier import Classifier
from expense_tracker.db import DBManager
from expense_tracker.modules.candidates import top_candidates


@pytest.fixture()
def c(tmp_path):
    db = DBManager(tmp_path / "t.db")
    return Classifier(db)


def test_keyword_fills_top3(c):
    # merchant text that hits exactly one keyword category
    cands = top_candidates(c, "瑞幸咖啡", "微信支付 -15 元 商户：瑞幸咖啡", k=3)
    assert len(cands) == 3
    assert cands[0].category == "餐饮"
    # fallback fills the rest with distinct (cat, sub) pairs
    keys = [(x.category, x.subcategory) for x in cands]
    assert len(set(keys)) == 3


def test_history_tops_keyword(c):
    c.db.upsert_merchant_history("万豪酒店", "居住", "酒店")
    cands = top_candidates(c, "万豪酒店", "微信支付 -600 元", k=3)
    assert cands[0].category == "居住"
    assert cands[0].source == "history"


def test_no_input_falls_back(c):
    cands = top_candidates(c, None, None, k=3)
    assert all(x.source == "fallback" for x in cands)
    assert len(cands) == 3


def test_candidate_dedup(c):
    # Two keywords that map to the same (cat, sub) should not appear twice
    c.db.seed_category_map([("超长关键词瑞幸咖啡旗舰店", "餐饮", "咖啡")])
    c.reload()
    cands = top_candidates(c, "瑞幸咖啡", "这是超长关键词瑞幸咖啡旗舰店", k=3)
    seen = [(x.category, x.subcategory) for x in cands]
    assert len(seen) == len(set(seen))
