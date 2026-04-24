from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.crawlers import (
    DEFAULT_PLATFORMS,
    RawHotItem,
    _build_market_queries,
    _filter_relevant_market_items,
    build_crawl_observations,
    build_market_snapshot_for_sku,
    build_candidates_from_crawled_items,
    crawl_hot_products,
    parse_browser_capture_text,
    parse_pasted_capture_text,
)
from backend.logic import enrich_sku


class CrawlerTransformationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.existing_skus = [
            enrich_sku(
                {
                    "sku_code": "A001",
                    "brand": "云南白药",
                    "product_name": "云南白药薄荷清爽牙膏",
                    "spec_text": "120g",
                    "efficacy_tags": "防蛀",
                    "current_price": 19.9,
                    "purchase_price": 12.5,
                    "six_month_sales": 320,
                }
            )
        ]

    def test_build_candidates_merges_multi_platform_hits(self) -> None:
        raw_items = [
            {
                "platform": "jd",
                "title": "佳洁士3D炫白牙膏 116g",
                "url": "https://item.jd.com/1.html",
                "price": 24.9,
                "sales_text": "2.3万",
                "popularity": 23000,
                "rank": 1,
            },
            {
                "platform": "taobao",
                "title": "佳洁士3D炫白牙膏116g",
                "url": "https://item.taobao.com/item.htm?id=2",
                "price": 23.9,
                "sales_text": "月销 9000+",
                "popularity": 9000,
                "rank": 2,
            },
            {
                "platform": "xiaohongshu",
                "title": "舒适达抗敏修护牙膏100g",
                "url": "https://www.xiaohongshu.com/explore/abc",
                "price": 29.9,
                "sales_text": "5300",
                "popularity": 5300,
                "rank": 3,
            },
        ]

        candidates = build_candidates_from_crawled_items(raw_items, self.existing_skus)
        self.assertEqual(len(candidates), 2)

        merged = next(item for item in candidates if "佳洁士" in item["brand"])
        self.assertEqual(merged["source_platform"], "京东/淘宝")
        self.assertEqual(merged["efficacy_tags"], "美白")
        self.assertEqual(merged["spec_text"], "116g")
        self.assertGreater(merged["heat_score"], 0)

        sensitive = next(item for item in candidates if "舒适达" in item["brand"])
        self.assertEqual(sensitive["efficacy_tags"], "抗敏")
        self.assertEqual(sensitive["target_group"], "成人")

    def test_market_query_builder_returns_fallback_queries(self) -> None:
        queries = _build_market_queries(self.existing_skus[0])
        self.assertGreaterEqual(len(queries), 3)
        self.assertTrue(any("云南白药" in query for query in queries))
        self.assertEqual(len(queries), len(set(queries)))

    def test_market_item_filter_keeps_relevant_taobao_samples(self) -> None:
        items = [
            RawHotItem(
                platform="taobao",
                title="云南白药薄荷清爽牙膏120g家庭装",
                url="https://item.taobao.com/item.htm?id=1",
                price=18.8,
                sales_text="月销 6000+",
                popularity=6000,
                rank=1,
            ),
            RawHotItem(
                platform="taobao",
                title="云南白药清爽薄荷牙膏120g",
                url="https://item.taobao.com/item.htm?id=2",
                price=19.5,
                sales_text="月销 5000+",
                popularity=5000,
                rank=2,
            ),
            RawHotItem(
                platform="taobao",
                title="其他品牌美白牙膏120g",
                url="https://item.taobao.com/item.htm?id=3",
                price=11.9,
                sales_text="月销 9000+",
                popularity=9000,
                rank=3,
            ),
        ]
        filtered = _filter_relevant_market_items(items, self.existing_skus[0], target_count=4)
        self.assertGreaterEqual(len(filtered["items"]), 2)
        self.assertEqual(filtered["quality"], "exact")
        self.assertTrue(all("云南白药" in item.title for item in filtered["items"][:2]))

    def test_build_crawl_observations_tracks_platform_and_keyword_hits(self) -> None:
        observations = build_crawl_observations(
            [
                {
                    "platform": "taobao",
                    "title": "佳洁士3D炫白牙膏116g",
                    "keyword": "美白牙膏",
                },
                {
                    "platform": "jd",
                    "title": "佳洁士3D炫白牙膏116g",
                    "keyword": "牙膏",
                },
            ],
            keyword="牙膏",
        )
        self.assertEqual(len(observations), 1)
        observation = observations[0]
        self.assertEqual(observation["platform_hits"]["taobao"], 1)
        self.assertEqual(observation["platform_hits"]["jd"], 1)
        self.assertEqual(observation["keyword_hits"]["美白牙膏"], 1)
        self.assertEqual(observation["keyword_hits"]["牙膏"], 1)

    def test_parse_browser_capture_text_accepts_script_output_json(self) -> None:
        payload = {
            "platform": "taobao",
            "source_url": "https://s.taobao.com/search?q=%E7%89%99%E8%86%8F",
            "items": [
                {
                    "title": "云南白药薄荷清爽牙膏120g",
                    "url": "https://item.taobao.com/item.htm?id=1",
                    "price": 19.9,
                    "sales_text": "月销 6000+",
                }
            ],
        }
        items = parse_browser_capture_text(json.dumps(payload, ensure_ascii=False), keyword="牙膏")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["platform"], "taobao")
        self.assertEqual(items[0]["price"], 19.9)

    def test_parse_pasted_capture_text_extracts_title_price_and_url(self) -> None:
        raw_text = """
        云南白药薄荷清爽牙膏120g 19.9元 月销 6000+
        https://item.taobao.com/item.htm?id=1

        舒适达抗敏修护牙膏100g ¥29.9 已售 3200
        https://detail.tmall.com/item.htm?id=2
        """
        items = parse_pasted_capture_text(raw_text, platform="taobao", keyword="牙膏")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["price"], 19.9)
        self.assertIn("item.taobao.com", items[0]["url"])
        self.assertIn("牙膏", items[1]["title"])

    @patch("backend.crawlers._crawl_platform")
    @patch("backend.crawlers._warm_up_platform")
    @patch("backend.crawlers._human_pause")
    def test_market_snapshot_can_fall_back_to_cross_platform_samples(self, _pause, _warmup, crawl_platform) -> None:
        def fake_crawl(platform, query, limit, cookie, timeout):
            if platform == "jd":
                return [
                    RawHotItem(
                        platform="jd",
                        title="云南白药薄荷清爽牙膏120g",
                        url="https://item.jd.com/1.html",
                        price=19.9,
                        sales_text="2.1万",
                        popularity=21000,
                        rank=1,
                    )
                ]
            return []

        crawl_platform.side_effect = fake_crawl
        snapshot = build_market_snapshot_for_sku(self.existing_skus[0], cookies={}, timeout=3, limit_per_platform=6)
        self.assertEqual(snapshot["market_sample_status"], "跨平台替代")
        self.assertGreater(snapshot["taobao_avg_price"], 0)
        self.assertTrue(snapshot["query_logs"])

    @patch("backend.crawlers._crawl_platform")
    @patch("backend.crawlers._warm_up_platform")
    @patch("backend.crawlers._cookie_cooldown_pause")
    @patch("backend.crawlers._human_pause")
    def test_cookie_is_only_used_as_fallback(self, _pause, _cookie_pause, _warmup, crawl_platform) -> None:
        def fake_crawl(platform, query, limit, cookie, timeout):
            if not cookie:
                raise RuntimeError("淘宝 返回拦截页，请补充对应平台 Cookie 后重试。")
            return [
                RawHotItem(
                    platform=platform,
                    title="云南白药薄荷清爽牙膏120g",
                    url="https://item.taobao.com/item.htm?id=1",
                    price=19.9,
                    sales_text="月销 6000+",
                    popularity=6000,
                    rank=1,
                )
            ]

        crawl_platform.side_effect = fake_crawl
        result = crawl_hot_products(
            keyword="牙膏",
            keywords=["牙膏"],
            platforms=["taobao"],
            limit_per_platform=5,
            cookies={"taobao": "cookie2=abc"},
        )
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["platform_reports"][0]["used_cookie_fallback"])
        self.assertEqual(crawl_platform.call_count, len(result["keywords_used"]) * 2)

    @patch("backend.crawlers._crawl_platform")
    @patch("backend.crawlers._warm_up_platform")
    @patch("backend.crawlers._human_pause")
    def test_empty_platform_selection_falls_back_to_defaults(self, _pause, _warmup, crawl_platform) -> None:
        crawl_platform.return_value = []
        result = crawl_hot_products(keyword="牙膏", platforms=[], limit_per_platform=5, cookies={})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["keywords_used"][0], "牙膏")
        self.assertGreater(len(result["keywords_used"]), 1)
        self.assertEqual(crawl_platform.call_count, len(DEFAULT_PLATFORMS) * len(result["keywords_used"]))


if __name__ == "__main__":
    unittest.main()
