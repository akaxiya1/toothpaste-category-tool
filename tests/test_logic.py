from __future__ import annotations

import unittest

from backend.logic import (
    auto_select_candidates,
    build_dashboard,
    consumer_efficacy_label,
    enrich_candidate,
    enrich_sku,
    market_sample_quality,
    match_candidate_against_catalog,
    recommend_brand_missing_hits,
    recommend_existing_skus,
    simulate_batch_pricing,
)


def make_sku(index: int, **overrides):
    base = {
        "id": index,
        "sku_code": f"A{index:03d}",
        "brand": "云南白药" if index % 3 == 0 else "高露洁" if index % 3 == 1 else "佳洁士",
        "product_name": f"测试牙膏{index}",
        "spec_text": "120g",
        "efficacy_tags": "防蛀" if index % 2 == 0 else "美白",
        "current_price": 12 + index,
        "purchase_price": round((12 + index) * 0.7, 2),
        "six_month_sales": 40 + index * 12,
        "target_group": "成人",
        "taobao_avg_price": 12 + index,
        "taobao_min_price": 11 + index,
        "taobao_max_price": 13 + index,
        "taobao_sample_count": 6,
        "price_disorder_flag": 0,
        "online_heat_score": 50 + index,
    }
    base.update(overrides)
    return enrich_sku(base)


class LogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skus = [
            make_sku(
                1,
                brand="云南白药",
                product_name="云南白药薄荷清爽牙膏",
                efficacy_tags="防蛀",
                current_price=19.9,
                purchase_price=12.5,
                six_month_sales=320,
                taobao_avg_price=18.9,
                taobao_min_price=17.9,
                taobao_max_price=20.5,
                online_heat_score=78,
            ),
            make_sku(
                2,
                brand="高露洁",
                product_name="高露洁美白牙膏",
                efficacy_tags="美白",
                current_price=22.9,
                purchase_price=14.2,
                six_month_sales=180,
                taobao_avg_price=22.4,
                taobao_min_price=20.9,
                taobao_max_price=24.5,
                online_heat_score=72,
            ),
            make_sku(
                3,
                brand="杂牌",
                product_name="低效牙膏",
                efficacy_tags="防蛀",
                current_price=15.0,
                purchase_price=12.8,
                six_month_sales=10,
                taobao_avg_price=12.8,
                taobao_min_price=9.9,
                taobao_max_price=18.8,
                price_disorder_flag=1,
                online_heat_score=35,
            ),
        ]

    def test_enrich_sku_calculates_fields(self) -> None:
        sku = self.skus[0]
        self.assertEqual(sku["price_band"], "15-19.9")
        self.assertAlmostEqual(sku["gross_margin"], round((19.9 - 12.5) / 19.9, 4))
        self.assertAlmostEqual(sku["half_year_gross_profit"], round((19.9 - 12.5) * 320, 2))
        self.assertEqual(sku["market_sample_quality"], "需刷新")
        self.assertEqual(sku["market_snapshot_status"], "待更新")

    def test_candidate_matching_and_scoring(self) -> None:
        candidate = enrich_candidate(
            {
                "brand": "高露洁",
                "product_name": "高露洁3D美白牙膏",
                "spec_text": "120g",
                "efficacy_tags": "美白",
                "online_reference_price": 24.9,
                "expected_purchase_price": 14.8,
                "heat_score": 84,
                "differentiation": "线上热销的美白主打款，适合补强美白价格带。",
                "target_group": "成人",
            },
            recommend_existing_skus(self.skus),
        )
        self.assertGreaterEqual(candidate["recommendation_score"], 50)
        self.assertEqual(len(candidate["comparison_rows"]), 3)
        self.assertIn(candidate["proposed_role"], {"引流品", "常规品", "利润品"})

    def test_structural_role_allocation_uses_25_60_15(self) -> None:
        many_skus = [make_sku(index + 1, online_heat_score=45 + index, price_disorder_flag=0) for index in range(20)]
        recommendations = recommend_existing_skus(many_skus)
        counts = {}
        for role in {"引流品", "常规品", "利润品"}:
          counts[role] = sum(1 for item in recommendations if item["structural_role"] == role)
        self.assertEqual(counts["引流品"], 5)
        self.assertEqual(counts["常规品"], 12)
        self.assertEqual(counts["利润品"], 3)

    def test_structural_role_allocation_keeps_regular_band_coverage(self) -> None:
        band_skus = [
            make_sku(1, current_price=9.9, purchase_price=6.4, online_heat_score=52, efficacy_tags="防蛀"),
            make_sku(2, current_price=10.9, purchase_price=7.0, online_heat_score=51, efficacy_tags="美白"),
            make_sku(3, current_price=14.9, purchase_price=9.8, online_heat_score=54, efficacy_tags="防蛀"),
            make_sku(4, current_price=16.9, purchase_price=11.2, online_heat_score=58, efficacy_tags="抗敏"),
            make_sku(5, current_price=19.9, purchase_price=13.1, online_heat_score=61, efficacy_tags="美白"),
            make_sku(6, current_price=24.9, purchase_price=16.6, online_heat_score=63, efficacy_tags="抗敏"),
            make_sku(7, current_price=27.9, purchase_price=18.7, online_heat_score=66, efficacy_tags="儿童", target_group="儿童"),
            make_sku(8, current_price=33.9, purchase_price=22.0, online_heat_score=69, efficacy_tags="草本"),
            make_sku(9, current_price=36.9, purchase_price=23.8, online_heat_score=71, efficacy_tags="美白"),
            make_sku(10, current_price=42.0, purchase_price=27.2, online_heat_score=74, efficacy_tags="抗敏"),
            make_sku(11, current_price=45.0, purchase_price=28.8, online_heat_score=76, efficacy_tags="儿童", target_group="儿童"),
            make_sku(12, current_price=22.9, purchase_price=14.3, online_heat_score=57, efficacy_tags="防蛀"),
        ]
        recommendations = recommend_existing_skus(band_skus)
        active_bands = {item["price_band"] for item in recommendations}
        regular_bands = {item["price_band"] for item in recommendations if item["structural_role"] == "常规品"}
        self.assertTrue(active_bands.issubset(regular_bands))

    def test_price_disorder_hot_item_becomes_lead(self) -> None:
        recommendations = recommend_existing_skus(
            [
                make_sku(1, price_disorder_flag=1, online_heat_score=88, taobao_min_price=9.9, taobao_avg_price=14.9, taobao_max_price=23.9),
                make_sku(2),
                make_sku(3),
                make_sku(4),
            ]
        )
        hot = next(item for item in recommendations if item["sku_code"] == "A001")
        self.assertEqual(hot["structural_role"], "引流品")
        self.assertEqual(hot["action"], "建议低价引流")

    def test_price_disorder_cold_item_can_be_delisted(self) -> None:
        recommendations = recommend_existing_skus(self.skus)
        low_efficiency = next(item for item in recommendations if item["sku_code"] == "A003")
        self.assertEqual(low_efficiency["action"], "建议下架")

    def test_recommendations_include_profit_contribution_and_price_range(self) -> None:
        recommendations = recommend_existing_skus(self.skus)
        target = next(item for item in recommendations if item["sku_code"] == "A001")
        self.assertGreater(target["half_year_gross_profit"], 0)
        self.assertGreater(target["profit_contribution_share"], 0)
        self.assertLessEqual(target["suggested_price_floor"], target["suggested_price"])
        self.assertLessEqual(target["suggested_price"], target["suggested_price_ceiling"])
        self.assertTrue(target["suggested_price_range_label"])
        self.assertTrue(target["recommendation_basis"])
        self.assertAlmostEqual(sum(item["profit_contribution_share"] for item in recommendations), 1.0, places=3)

    def test_market_sample_quality_labels(self) -> None:
        self.assertEqual(market_sample_quality(0, False, "已刷新无样本"), "无样本")
        self.assertEqual(market_sample_quality(3, False, "待刷新"), "需刷新")
        self.assertEqual(market_sample_quality(5, True, "已刷新"), "中")
        self.assertEqual(market_sample_quality(9, True, "已刷新"), "高")

    def test_batch_pricing_simulation_returns_summary_and_changes(self) -> None:
        simulation = simulate_batch_pricing(
            self.skus,
            brand="云南白药",
            strategy="adjust_by_amount",
            amount=-1.0,
        )
        self.assertEqual(simulation["summary"]["affected_count"], 1)
        self.assertTrue(simulation["items"])
        row = simulation["items"][0]
        self.assertAlmostEqual(row["after_price"], row["before_price"] - 1.0, places=2)
        self.assertIn("avg_margin_after", simulation["summary"])

    def test_dashboard_summary_and_band_details(self) -> None:
        structured_skus = recommend_existing_skus(self.skus)
        candidate = enrich_candidate(
            {
                "brand": "舒适达",
                "product_name": "舒适达抗敏牙膏",
                "spec_text": "100g",
                "efficacy_tags": "抗敏",
                "online_reference_price": 29.9,
                "expected_purchase_price": 18.0,
            },
            structured_skus,
        )
        dashboard = build_dashboard(structured_skus, [candidate])
        self.assertEqual(dashboard["summary"]["sku_count"], 3)
        self.assertEqual(dashboard["summary"]["candidate_count"], 1)
        self.assertTrue(isinstance(dashboard["price_band_details"], dict))
        self.assertTrue(isinstance(dashboard["brand_details"], dict))
        self.assertTrue(isinstance(dashboard["efficacy_details"], dict))
        self.assertTrue(any(dashboard["price_band_details"].values()))
        self.assertTrue(any(dashboard["brand_details"].values()))
        self.assertTrue(any(dashboard["efficacy_details"].values()))
        self.assertTrue(any(item["efficacy"] == "敏感修护/牙龈护理" for item in dashboard["efficacy_distribution"]))

    def test_consumer_efficacy_label_uses_shopper_language(self) -> None:
        anti_sensitive = consumer_efficacy_label(
            {
                "product_name": "舒适达抗敏修护牙膏",
                "efficacy_tags": "抗敏",
                "target_group": "成人",
            }
        )
        whitening = consumer_efficacy_label(
            {
                "product_name": "佳洁士3D炫白牙膏",
                "efficacy_tags": "美白",
                "target_group": "成人",
            }
        )
        child = consumer_efficacy_label(
            {
                "product_name": "狮王儿童木糖醇牙膏",
                "efficacy_tags": "儿童",
                "target_group": "儿童",
            }
        )
        self.assertEqual(anti_sensitive, "敏感修护/牙龈护理")
        self.assertEqual(whitening, "美白亮白")
        self.assertEqual(child, "儿童专用护理")

    def test_auto_select_candidates_builds_shortlist(self) -> None:
        structured_skus = recommend_existing_skus(self.skus)
        candidates = [
            enrich_candidate(
                {
                    "brand": "佳洁士",
                    "product_name": "佳洁士3D炫白双效牙膏",
                    "spec_text": "116g",
                    "efficacy_tags": "美白",
                    "online_reference_price": 24.9,
                    "expected_purchase_price": 14.8,
                    "heat_score": 82,
                    "differentiation": "补强中端美白价格带，线上热度高。",
                    "target_group": "成人",
                },
                structured_skus,
            ),
            enrich_candidate(
                {
                    "brand": "狮王",
                    "product_name": "狮王儿童木糖醇牙膏",
                    "spec_text": "90g",
                    "efficacy_tags": "儿童",
                    "online_reference_price": 16.9,
                    "expected_purchase_price": 9.5,
                    "heat_score": 75,
                    "differentiation": "补儿童功效缺口。",
                    "target_group": "儿童",
                },
                structured_skus,
            ),
        ]
        auto_selection = auto_select_candidates(candidates, structured_skus, limit=3)
        self.assertGreaterEqual(auto_selection["summary"]["selected_count"], 1)
        self.assertTrue(auto_selection["selected"])
        self.assertIn("优先", auto_selection["selected"][0]["auto_pick_decision"])

    def test_match_candidate_against_catalog(self) -> None:
        structured_skus = recommend_existing_skus(self.skus)
        matches = match_candidate_against_catalog(
            {
                "brand": "高露洁",
                "product_name": "高露洁美白牙膏",
                "spec_text": "120g",
                "efficacy_tags": "美白",
                "online_reference_price": 23.9,
                "target_group": "成人",
            },
            structured_skus,
        )
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["brand"], "高露洁")

    def test_brand_missing_hits_prioritize_same_brand_gap(self) -> None:
        brand_skus = recommend_existing_skus(
            [
                make_sku(
                    1,
                    brand="狮王",
                    product_name="狮王清新薄荷牙膏",
                    efficacy_tags="防蛀",
                    current_price=18.9,
                    purchase_price=11.6,
                    six_month_sales=120,
                    online_heat_score=66,
                ),
                make_sku(
                    2,
                    brand="佳洁士",
                    product_name="佳洁士美白牙膏",
                    efficacy_tags="美白",
                    current_price=23.9,
                    purchase_price=15.1,
                    six_month_sales=200,
                    online_heat_score=74,
                ),
            ]
        )
        candidates = [
            enrich_candidate(
                {
                    "brand": "狮王",
                    "product_name": "狮王亮白修护牙膏",
                    "spec_text": "120g",
                    "efficacy_tags": "美白",
                    "online_reference_price": 24.9,
                    "expected_purchase_price": 14.3,
                    "heat_score": 88,
                    "differentiation": "补狮王品牌的美白爆款",
                },
                brand_skus,
            ),
            enrich_candidate(
                {
                    "brand": "佳洁士",
                    "product_name": "佳洁士双效美白牙膏",
                    "spec_text": "120g",
                    "efficacy_tags": "美白",
                    "online_reference_price": 26.9,
                    "expected_purchase_price": 15.8,
                    "heat_score": 89,
                },
                brand_skus,
            ),
        ]

        recommendations = recommend_brand_missing_hits(
            brand="狮王",
            current_brand_skus=[item for item in brand_skus if item["brand"] == "狮王"],
            all_skus=brand_skus,
            candidates=candidates,
        )
        self.assertTrue(recommendations)
        self.assertEqual(recommendations[0]["brand"], "狮王")
        self.assertIn(recommendations[0]["recommendation_action"], {"建议补这个品牌功效款", "建议上这个品牌爆款"})

    def test_brand_missing_hits_skip_same_brand_duplicate(self) -> None:
        brand_skus = recommend_existing_skus(
            [
                make_sku(
                    1,
                    brand="狮王",
                    product_name="狮王清新薄荷牙膏",
                    efficacy_tags="防蛀",
                    current_price=18.9,
                    purchase_price=11.6,
                    six_month_sales=120,
                    online_heat_score=66,
                ),
                make_sku(
                    2,
                    brand="云南白药",
                    product_name="云南白药修护牙膏",
                    efficacy_tags="抗敏",
                    current_price=25.9,
                    purchase_price=16.0,
                    six_month_sales=180,
                    online_heat_score=72,
                ),
            ]
        )
        duplicate_candidate = enrich_candidate(
            {
                "brand": "狮王",
                "product_name": "狮王清新薄荷牙膏",
                "spec_text": "120g",
                "efficacy_tags": "防蛀",
                "online_reference_price": 19.9,
                "expected_purchase_price": 12.0,
                "heat_score": 82,
            },
            brand_skus,
        )
        fresh_candidate = enrich_candidate(
            {
                "brand": "狮王",
                "product_name": "狮王儿童木糖醇牙膏",
                "spec_text": "90g",
                "efficacy_tags": "儿童",
                "online_reference_price": 16.9,
                "expected_purchase_price": 9.2,
                "heat_score": 79,
                "target_group": "儿童",
            },
            brand_skus,
        )

        recommendations = recommend_brand_missing_hits(
            brand="狮王",
            current_brand_skus=[item for item in brand_skus if item["brand"] == "狮王"],
            all_skus=brand_skus,
            candidates=[duplicate_candidate, fresh_candidate],
        )
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["product_name"], "狮王儿童木糖醇牙膏")


if __name__ == "__main__":
    unittest.main()
