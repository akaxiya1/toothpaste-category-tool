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


if __name__ == "__main__":
    unittest.main()
