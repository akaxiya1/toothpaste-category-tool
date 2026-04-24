from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import ToolApplication
from backend.logic import enrich_sku
from backend.storage import Database


def sample_sku(**overrides):
    base = {
        "sku_code": "SKU001",
        "brand": "狮王",
        "product_name": "狮王清新薄荷牙膏",
        "spec_text": "120g",
        "efficacy_tags": "防蛀",
        "current_price": 18.9,
        "purchase_price": 11.6,
        "six_month_sales": 120,
    }
    base.update(overrides)
    return base


class AppBrandRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_temp_root = Path(__file__).resolve().parent.parent / "data" / "test_temp"
        self.workspace_temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.workspace_temp_root / f"brand_app_{uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tool = ToolApplication()
        self.tool.db = Database(self.temp_dir / "brand_recommendations.sqlite3")
        self.tool.db.upsert_skus(
            [
                enrich_sku(sample_sku()),
                enrich_sku(sample_sku(
                    sku_code="SKU002",
                    brand="佳洁士",
                    product_name="佳洁士美白牙膏",
                    efficacy_tags="美白",
                    current_price=23.9,
                    purchase_price=15.2,
                    six_month_sales=200,
                )),
            ]
        )

    @patch("app.build_candidates_from_crawled_items")
    @patch("app.crawl_hot_products")
    def test_brand_recommendations_auto_crawl_when_brand_candidates_insufficient(self, crawl_hot_products, build_candidates) -> None:
        crawl_hot_products.return_value = {
            "items": [
                {
                    "platform": "taobao",
                    "title": "狮王亮白修护牙膏 120g",
                    "url": "https://item.taobao.com/item.htm?id=100",
                    "price": 24.9,
                    "sales_text": "月销 9000+",
                    "rank": 1,
                }
            ],
            "errors": {},
            "platform_reports": [],
            "keywords_used": ["狮王 牙膏"],
        }
        build_candidates.return_value = [
            {
                "brand": "狮王",
                "product_name": "狮王亮白修护牙膏",
                "spec_text": "120g",
                "efficacy_tags": "美白",
                "online_reference_price": 24.9,
                "expected_purchase_price": 14.2,
                "source_platform": "淘宝",
                "product_url": "https://item.taobao.com/item.htm?id=100",
                "heat_score": 88,
                "differentiation": "同品牌亮白爆款",
                "target_group": "成人",
            }
        ]

        result = self.tool.brand_recommendations({"brand": "狮王"})

        self.assertTrue(result["auto_crawl_triggered"])
        self.assertEqual(result["crawl_status"], "crawl_success")
        self.assertTrue(result["missing_brand_hits"])
        self.assertEqual(result["missing_brand_hits"][0]["brand"], "狮王")
        self.assertTrue(any(item["brand"] == "狮王" for item in self.tool.db.list_candidates()))

    @patch("app.crawl_hot_products")
    def test_brand_recommendations_fall_back_to_cached_candidates_when_crawl_fails(self, crawl_hot_products) -> None:
        self.tool.save_candidate(
            {
                "brand": "狮王",
                "product_name": "狮王儿童木糖醇牙膏",
                "spec_text": "90g",
                "efficacy_tags": "儿童",
                "online_reference_price": 16.9,
                "expected_purchase_price": 9.1,
                "source_platform": "淘宝",
                "heat_score": 78,
                "differentiation": "本地已录入的同品牌候选",
                "target_group": "儿童",
            }
        )
        crawl_hot_products.side_effect = RuntimeError("mock crawl blocked")

        result = self.tool.brand_recommendations({"brand": "狮王"})

        self.assertTrue(result["used_cached_candidates"])
        self.assertTrue(result["auto_crawl_triggered"])
        self.assertEqual(result["crawl_status"], "crawl_failed")
        self.assertEqual(result["fallback_mode"], "local_after_crawl_failure")
        self.assertTrue(result["missing_brand_hits"])
        self.assertEqual(result["missing_brand_hits"][0]["brand"], "狮王")
 

class AppProcurementTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_temp_root = Path(__file__).resolve().parent.parent / "data" / "test_temp"
        self.workspace_temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.workspace_temp_root / f"procurement_app_{uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.tool = ToolApplication()
        self.tool.db = Database(self.temp_dir / "procurement_tracking.sqlite3")
        self.tool.db.upsert_skus(
            [
                enrich_sku(sample_sku()),
                enrich_sku(
                    sample_sku(
                        sku_code="SKU002",
                        brand="浣虫磥澹?",
                        product_name="浣虫磥澹編鐧界墮鑶?",
                        efficacy_tags="缇庣櫧",
                        current_price=23.9,
                        purchase_price=15.2,
                        six_month_sales=200,
                    )
                ),
            ]
        )
        self.candidate_id = self.tool.save_candidate(
            {
                "brand": "鐙帇",
                "product_name": "鐙帇浜櫧淇姢鐗欒啅",
                "spec_text": "120g",
                "efficacy_tags": "缇庣櫧",
                "online_reference_price": 24.9,
                "expected_purchase_price": 14.2,
                "source_platform": "娣樺疂",
                "product_url": "https://item.taobao.com/item.htm?id=100",
                "heat_score": 88,
                "differentiation": "鍚屽搧鐗屼寒鐧界垎娆?",
                "intended_replace_sku": "",
                "notes": "",
                "fluoride": 1,
                "target_group": "鎴愪汉",
                "promo_type": "甯歌娆?",
                "must_keep": 0,
                "substitute_relation": "",
            }
        )["id"]

    def test_save_candidate_launch_plan_is_reflected_in_state(self) -> None:
        result = self.tool.save_candidate_launch_plan(
            self.candidate_id,
            {
                "first_order_qty": 18,
                "actual_launch_qty": 12,
                "actual_launch_date": "2026-04-24",
                "actual_launch_price": 23.5,
                "review_cycle_days": 14,
                "launch_status": "launched",
                "launch_notes": "棣栨壒宸蹭笂鏋?",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"]["first_order_qty"], 18)
        self.assertEqual(result["item"]["actual_launch_qty"], 12)
        self.assertEqual(result["item"]["launch_status"], "launched")
        state = self.tool.state()
        self.assertEqual(state["procurement"]["summary"]["tracked_count"], 1)
        self.assertEqual(state["procurement"]["launch_queue"][0]["first_order_qty"], 18)

    def test_add_candidate_review_log_updates_review_history(self) -> None:
        self.tool.save_candidate_launch_plan(
            self.candidate_id,
            {
                "first_order_qty": 16,
                "actual_launch_qty": 16,
                "actual_launch_date": "2026-04-20",
                "actual_launch_price": 24.9,
                "review_cycle_days": 7,
                "launch_status": "launched",
            },
        )

        result = self.tool.add_candidate_review_log(
            self.candidate_id,
            {
                "review_date": "2026-04-27",
                "cycle_label": "7澶╁鐩?",
                "sales_units": 11,
                "sales_amount": 273.9,
                "gross_margin_rate": 0.32,
                "decision": "replenish",
                "notes": "鍔ㄩ攢杈炬爣锛屽彲缁х画琛ヨ揣",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["review"]["sales_units"], 11)
        self.assertEqual(result["item"]["review_log_count"], 1)
        self.assertEqual(result["item"]["latest_review"]["decision"], "replenish")
        self.assertEqual(result["item"]["next_review_date"], "2026-05-04")

    def test_update_candidate_review_log_updates_existing_review(self) -> None:
        self.tool.save_candidate_launch_plan(
            self.candidate_id,
            {
                "first_order_qty": 12,
                "actual_launch_qty": 12,
                "actual_launch_date": "2026-04-20",
                "review_cycle_days": 14,
                "launch_status": "launched",
            },
        )
        created = self.tool.add_candidate_review_log(
            self.candidate_id,
            {
                "review_date": "2026-04-27",
                "cycle_label": "7天复盘",
                "sales_units": 6,
                "sales_amount": 149.4,
                "gross_margin_rate": 0.28,
                "decision": "observe",
                "notes": "initial review",
            },
        )

        result = self.tool.update_candidate_review_log(
            self.candidate_id,
            int(created["review"]["id"]),
            {
                "review_date": "2026-05-04",
                "cycle_label": "14天复盘",
                "sales_units": 15,
                "sales_amount": 373.5,
                "gross_margin_rate": 36,
                "decision": "replenish",
                "notes": "update review",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["review"]["review_date"], "2026-05-04")
        self.assertEqual(result["review"]["cycle_label"], "14天复盘")
        self.assertEqual(result["review"]["sales_units"], 15)
        self.assertAlmostEqual(result["review"]["gross_margin_rate"], 0.36)
        self.assertEqual(result["item"]["latest_review"]["decision"], "replenish")
        self.assertEqual(result["item"]["review_log_count"], 1)
        self.assertEqual(result["item"]["next_review_date"], "2026-05-18")

    def test_delete_candidate_review_log_removes_history(self) -> None:
        self.tool.save_candidate_launch_plan(
            self.candidate_id,
            {
                "first_order_qty": 10,
                "actual_launch_qty": 10,
                "actual_launch_date": "2026-04-20",
                "review_cycle_days": 7,
                "launch_status": "launched",
            },
        )
        created = self.tool.add_candidate_review_log(
            self.candidate_id,
            {
                "review_date": "2026-04-27",
                "cycle_label": "7天复盘",
                "sales_units": 8,
                "sales_amount": 199.2,
                "gross_margin_rate": 0.31,
                "decision": "observe",
                "notes": "delete me",
            },
        )

        result = self.tool.delete_candidate_review_log(self.candidate_id, int(created["review"]["id"]))

        self.assertTrue(result["ok"])
        item = result["item"]
        self.assertEqual(item["review_log_count"], 0)
        self.assertIsNone(item["latest_review"])
        self.assertEqual(item["next_review_date"], "2026-04-27")

    def test_state_exposes_market_reference_and_procurement_actions(self) -> None:
        state = self.tool.state()

        self.assertIn("market_reference", state)
        self.assertIn("procurement_actions", state)
        self.assertIn("feedback_proposals", state)
        self.assertIn("strategy_overrides_summary", state)
        self.assertTrue(state["market_reference"]["rows"])
        self.assertTrue(state["procurement_actions"]["candidates"])
        candidate_action = state["procurement_actions"]["candidates"][0]
        self.assertGreater(candidate_action["suggested_first_order_qty"], 0)
        self.assertIn("confidence_level", candidate_action)

    def test_update_procurement_action_persists_manual_adjustment(self) -> None:
        state = self.tool.state()
        candidate_action = state["procurement_actions"]["candidates"][0]

        result = self.tool.update_procurement_action(
            candidate_action["action_key"],
            {
                "suggested_first_order_qty": 20,
                "recommended_price": 25.8,
                "review_cycle_days": 14,
                "status": "已确认",
                "notes": "manual adjust",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"]["suggested_first_order_qty"], 20)
        self.assertEqual(result["action"]["status"], "已确认")
        self.assertAlmostEqual(result["action"]["recommended_price"], 25.8)

    def test_feedback_proposal_acceptance_writes_strategy_override(self) -> None:
        extra_candidate_ids = []
        for index in range(3):
            extra_candidate_ids.append(
                self.tool.save_candidate(
                    {
                        "brand": "额外品牌",
                        "product_name": f"额外候选{index}",
                        "spec_text": "120g",
                        "efficacy_tags": "美白",
                        "online_reference_price": 21.9 + index,
                        "expected_purchase_price": 12.5 + index,
                        "source_platform": "淘宝",
                        "product_url": f"https://item.taobao.com/item.htm?id={200 + index}",
                        "heat_score": 82,
                        "differentiation": "同平台测试候选",
                        "target_group": "成人",
                    }
                )["id"]
            )

        for candidate_id in extra_candidate_ids:
            self.tool.save_candidate_launch_plan(
                candidate_id,
                {
                    "first_order_qty": 10,
                    "actual_launch_qty": 10,
                    "actual_launch_date": "2026-04-20",
                    "review_cycle_days": 7,
                    "launch_status": "launched",
                },
            )
            self.tool.add_candidate_review_log(
                candidate_id,
                {
                    "review_date": "2026-04-27",
                    "cycle_label": "7天复盘",
                    "sales_units": 1,
                    "sales_amount": 22.0,
                    "gross_margin_rate": 0.2,
                    "decision": "observe",
                    "notes": "weak result",
                },
            )

        state = self.tool.state()
        proposal = next(
            item
            for item in state["feedback_proposals"]
            if item["proposal_type"] == "platform_heat_weight"
        )

        result = self.tool.decide_feedback_proposal(proposal["proposal_key"], {"decision": "accepted"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["proposal"]["decision_status"], "accepted")
        self.assertGreaterEqual(result["strategy_overrides_summary"]["active_count"], 1)

    def test_state_exposes_strategic_workbench_sections(self) -> None:
        state = self.tool.state()

        self.assertIn("market_intelligence", state)
        self.assertIn("category_strategy", state)
        self.assertIn("brand_strategy_cards", state)
        self.assertIn("competitor_watch", state)
        self.assertIn("learning_summary", state)
        self.assertTrue(isinstance(state["brand_strategy_cards"], list))
        self.assertIn("summary", state["market_intelligence"])
        self.assertIn("strategic_actions_flat", state["category_strategy"])

    def test_save_brand_watchlist_is_reflected_in_competitor_watch(self) -> None:
        result = self.tool.save_brand_watchlist({"brand": "狮王", "source_platforms": ["taobao", "jd"]})

        self.assertTrue(result["ok"])
        self.assertEqual(result["watchlist"]["brand"], "狮王")
        self.assertTrue(any(item["brand"] == "狮王" for item in result["competitor_watch"]["watchlists"]))

    def test_brand_strategy_detail_returns_current_skus_and_missing_hits(self) -> None:
        self.tool.save_candidate(
            {
                "brand": "狮王",
                "product_name": "狮王亮白修护牙膏",
                "spec_text": "120g",
                "efficacy_tags": "美白",
                "online_reference_price": 24.9,
                "expected_purchase_price": 14.0,
                "source_platform": "淘宝",
                "product_url": "https://item.taobao.com/item.htm?id=555",
                "heat_score": 90,
                "differentiation": "同品牌亮白爆款",
                "target_group": "成人",
            }
        )

        detail = self.tool.brand_strategy_detail("狮王")

        self.assertEqual(detail["brand"], "狮王")
        self.assertTrue(detail["current_brand_skus"])
        self.assertTrue(isinstance(detail["missing_brand_hits"], list))

    def test_learning_summary_generates_strategic_feedback_from_repeated_weak_reviews(self) -> None:
        candidate_ids = []
        for index in range(3):
            candidate_ids.append(
                self.tool.save_candidate(
                    {
                        "brand": "学习品牌",
                        "product_name": f"学习品牌候选{index}",
                        "spec_text": "120g",
                        "efficacy_tags": "美白",
                        "online_reference_price": 23.9,
                        "expected_purchase_price": 13.5,
                        "source_platform": "淘宝",
                        "product_url": f"https://item.taobao.com/item.htm?id={700 + index}",
                        "heat_score": 86,
                        "differentiation": "用于复盘学习测试",
                        "target_group": "成人",
                    }
                )["id"]
            )

        for candidate_id in candidate_ids:
            self.tool.save_candidate_launch_plan(
                candidate_id,
                {
                    "first_order_qty": 10,
                    "actual_launch_qty": 10,
                    "actual_launch_date": "2026-04-20",
                    "review_cycle_days": 7,
                    "launch_status": "launched",
                },
            )
            self.tool.add_candidate_review_log(
                candidate_id,
                {
                    "review_date": "2026-04-27",
                    "cycle_label": "7天复盘",
                    "sales_units": 1,
                    "sales_amount": 23.9,
                    "gross_margin_rate": 0.2,
                    "decision": "observe",
                    "notes": "weak review",
                },
            )

        state = self.tool.state()

        self.assertGreaterEqual(state["learning_summary"]["summary"]["evidence_count"], 3)
        self.assertTrue(
            any(
                item["proposal_type"] == "brand_strategy_adjustment"
                for item in state["learning_summary"]["feedback_proposals"]
            )
        )


if __name__ == "__main__":
    unittest.main()
