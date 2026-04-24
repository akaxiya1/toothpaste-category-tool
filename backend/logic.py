from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from statistics import median
from typing import Any

from .constants import (
    DEFAULT_REQUIRED_EFFICACY,
    EFFICACY_OPTIONS,
    PRICE_BANDS,
    ROLE_MARGIN_TARGETS,
    ROLES,
)

STRUCTURAL_ROLE_RATIOS = {
    "引流品": 0.25,
    "常规品": 0.60,
    "利润品": 0.15,
}

ACTION_PRIORITY = {
    "建议下架": 0,
    "建议低价引流": 1,
    "建议调整售价": 2,
    "建议利润定价": 3,
    "建议维持常规价": 4,
}

ROLE_SCORE_KEY = {
    "引流品": "lead",
    "常规品": "regular",
    "利润品": "profit",
}

PRICE_BAND_ORDER = {band["label"]: index for index, band in enumerate(PRICE_BANDS)}

HIGH_HEAT_THRESHOLD = 70
LOW_HEAT_THRESHOLD = 55
MARKET_SNAPSHOT_TTL_HOURS = 24

CONSUMER_EFFICACY_BUCKETS = [
    {
        "label": "基础防蛀/日常清洁",
        "description": "消费者买基础牙膏时最常看的日常清洁、防蛀和含氟护理。",
        "tokens": {"防蛀", "含氟", "护齿", "清洁", "净白", "洁净"},
    },
    {
        "label": "美白亮白",
        "description": "面向有亮白、去黄和外观改善诉求的消费者。",
        "tokens": {"美白", "亮白", "炫白", "去黄", "去渍", "洁白"},
    },
    {
        "label": "敏感修护/牙龈护理",
        "description": "针对牙齿敏感、牙龈不适和修护护理诉求。",
        "tokens": {"抗敏", "敏感", "修护", "舒缓", "牙龈", "护龈"},
    },
    {
        "label": "口气清新",
        "description": "面向薄荷清爽、异味改善和口气清新诉求。",
        "tokens": {"清新", "口气", "薄荷", "清凉", "净味", "留香"},
    },
    {
        "label": "草本天然护理",
        "description": "偏草本、植物和天然护理偏好的消费者会重点看这一类。",
        "tokens": {"草本", "竹盐", "中草药", "天然", "植物", "本草"},
    },
    {
        "label": "儿童专用护理",
        "description": "针对儿童安全感、口味和低刺激诉求的专用品类。",
        "tokens": {"儿童", "宝宝", "小孩", "幼儿", "木糖醇"},
    },
    {
        "label": "综合护理/其他",
        "description": "没有明显单一功效标签，更多承担综合补位作用。",
        "tokens": set(),
    },
]

CONSUMER_REQUIRED_BUCKETS = ["基础防蛀/日常清洁", "美白亮白", "敏感修护/牙龈护理", "儿童专用护理"]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_header(value: Any) -> str:
    text = normalize_text(value).lower()
    return re.sub(r"[\s/_\-（）()]+", "", text)


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = normalize_text(value)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = normalize_text(value)
    if not text:
        return 0.0
    text = text.replace(",", "").replace("￥", "").replace("元", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def parse_int(value: Any) -> int:
    return int(round(parse_float(value)))


def parse_bool(value: Any) -> int:
    text = normalize_text(value).lower()
    if text in {"1", "y", "yes", "true", "是", "有", "需", "保留"}:
        return 1
    if text in {"0", "n", "no", "false", "否", "无", "不保留"}:
        return 0
    return 0


def safe_ratio(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return numerator / denominator


def calculate_margin(sell_price: float, cost: float) -> float:
    if sell_price <= 0:
        return 0.0
    return round((sell_price - cost) / sell_price, 4)


def calculate_unit_profit(sell_price: float, cost: float) -> float:
    return round(sell_price - cost, 2)


def parse_spec(spec_text: str) -> tuple[float | None, str | None]:
    text = normalize_text(spec_text).lower().replace(" ", "")
    if not text:
        return None, None
    multiplier = 1.0
    multi_match = re.search(r"[x×*](\d+(?:\.\d+)?)", text)
    if multi_match:
        multiplier = float(multi_match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)(kg|g|克|ml|l|毫升)", text)
    if not match:
        return None, None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "kg":
        amount *= 1000
        unit = "g"
    elif unit == "l":
        amount *= 1000
        unit = "ml"
    elif unit == "克":
        unit = "g"
    elif unit == "毫升":
        unit = "ml"
    return amount * multiplier, unit


def calculate_unit_price(price: float, spec_text: str) -> float:
    amount, _ = parse_spec(spec_text)
    if not amount:
        return 0.0
    return round(price / amount, 4)


def determine_price_band(price: float) -> str:
    for band in PRICE_BANDS:
        min_value = band["min"]
        max_value = band["max"]
        if min_value is None and price <= max_value:
            return band["label"]
        if max_value is None and price >= min_value:
            return band["label"]
        if min_value is not None and max_value is not None and min_value <= price <= max_value:
            return band["label"]
    return "未定价"


def normalize_efficacy(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return "其他"
    candidates = re.split(r"[、,，/|]+", text)
    for item in candidates:
        if item in EFFICACY_OPTIONS:
            return item
    return candidates[0] if candidates and candidates[0] else "其他"


def normalize_target_group(value: Any, efficacy: str) -> str:
    text = normalize_text(value)
    if text in {"成人", "儿童", "家庭"}:
        return text
    if efficacy == "儿童":
        return "儿童"
    return "成人"


def normalize_promo_type(value: Any) -> str:
    text = normalize_text(value)
    if text in {"活动款", "常规款"}:
        return text
    return "常规款"


def is_snapshot_fresh(snapshot_at: Any, ttl_hours: int = MARKET_SNAPSHOT_TTL_HOURS) -> bool:
    text = normalize_text(snapshot_at)
    if not text:
        return False
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return False
    return moment >= datetime.now() - timedelta(hours=ttl_hours)


def infer_role(
    *,
    current_role: str,
    price: float,
    efficacy: str,
    target_group: str,
    margin: float,
    sales: int,
    heat_score: float = 0.0,
) -> str:
    if current_role in ROLES:
        return current_role
    if price <= 12 or heat_score >= 80 or sales >= 180:
        return "引流品"
    if price >= 30 or margin >= 0.33 or (efficacy in {"美白", "抗敏"} and price >= 22):
        return "利润品"
    if target_group in {"儿童", "家庭"} and price <= 20:
        return "常规品"
    return "常规品"


def market_sample_quality(sample_count: int, snapshot_fresh: bool, snapshot_status: str) -> str:
    if snapshot_status in {"被拦截", "无结果"}:
        return "无样本"
    if snapshot_status == "跨平台替代":
        return "替代"
    if snapshot_status == "人工补样本":
        return "人工"
    if sample_count <= 0:
        return "无样本"
    if snapshot_status == "待刷新" or not snapshot_fresh:
        return "需刷新"
    if sample_count >= 8:
        return "高"
    if sample_count >= 4:
        return "中"
    return "低"


def margin_target(role: str) -> tuple[float, float]:
    return ROLE_MARGIN_TARGETS.get(role, ROLE_MARGIN_TARGETS["常规品"])


def margin_zone(role: str, margin: float) -> str:
    low, high = margin_target(role)
    if margin < low:
        return "below"
    if margin > high:
        return "above"
    return "within"


def round_retail_price(value: float) -> float:
    if value <= 0:
        return 0.0
    if value < 10:
        return round(math.ceil(value * 10) / 10, 1)
    if value < 30:
        return round(math.ceil(value * 2) / 2, 1)
    return round(math.ceil(value), 1)


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def low_sales_threshold(skus: list[dict[str, Any]]) -> int:
    if not skus:
        return 20
    sales_values = sorted(parse_int(item.get("six_month_sales")) for item in skus)
    pivot_index = max(0, int(len(sales_values) * 0.25) - 1)
    return max(15, sales_values[pivot_index])


def price_for_margin(cost: float, margin: float) -> float:
    if cost <= 0 or margin >= 0.999:
        return 0.0
    return cost / max(1 - margin, 0.01)


def clamp_price_to_margin_range(anchor: float, cost: float, role: str) -> float:
    low, high = margin_target(role)
    floor_price = price_for_margin(cost, low)
    ceiling_price = price_for_margin(cost, high)
    if floor_price <= 0:
        return round_retail_price(max(anchor, 0))
    value = anchor if anchor > 0 else floor_price
    value = max(value, floor_price)
    if ceiling_price > 0:
        value = min(value, ceiling_price)
    return round_retail_price(value)


def allocate_role_counts(total: int) -> dict[str, int]:
    if total <= 0:
        return {role: 0 for role in ROLES}
    if total == 1:
        return {"引流品": 0, "常规品": 1, "利润品": 0}

    raw_targets = {role: total * ratio for role, ratio in STRUCTURAL_ROLE_RATIOS.items()}
    counts = {role: int(math.floor(value)) for role, value in raw_targets.items()}
    remainder = total - sum(counts.values())
    ordered_remainders = sorted(
        ((raw_targets[role] - counts[role], role) for role in ROLES),
        key=lambda item: (-item[0], ROLES.index(item[1])),
    )
    for _, role in ordered_remainders[:remainder]:
        counts[role] += 1

    if total >= 2:
        for must_role in ("引流品", "利润品"):
            if counts[must_role] > 0:
                continue
            donors = ["常规品", "引流品", "利润品"]
            donors = [role for role in donors if role != must_role]
            donor = next((role for role in donors if counts[role] > 1), None)
            donor = donor or next((role for role in donors if counts[role] > 0), None)
            if donor:
                counts[donor] -= 1
                counts[must_role] += 1
    return counts


def enrich_sku(raw: dict[str, Any]) -> dict[str, Any]:
    price = round(parse_float(raw.get("current_price")), 2)
    purchase_price = round(parse_float(raw.get("purchase_price")), 2)
    efficacy = normalize_efficacy(raw.get("efficacy_tags"))
    target_group = normalize_target_group(raw.get("target_group"), efficacy)
    margin = calculate_margin(price, purchase_price)
    sales = parse_int(raw.get("six_month_sales"))
    current_role = normalize_text(raw.get("current_role"))
    structural_role = normalize_text(raw.get("structural_role"))
    heat_score = round(parse_float(raw.get("online_heat_score")), 1)
    snapshot_at = normalize_text(raw.get("market_snapshot_at"))
    resolved_role = structural_role if structural_role in ROLES else infer_role(
        current_role=current_role,
        price=price,
        efficacy=efficacy,
        target_group=target_group,
        margin=margin,
        sales=sales,
        heat_score=heat_score,
    )
    disorder_flag = parse_bool(raw.get("price_disorder_flag"))
    sample_count = parse_int(raw.get("taobao_sample_count"))
    sample_status = normalize_text(raw.get("market_sample_status"))
    snapshot_status = sample_status or "待更新"
    if snapshot_at:
        snapshot_status = "已刷新" if is_snapshot_fresh(snapshot_at) else "待刷新"
    if sample_count <= 0 and snapshot_at:
        snapshot_status = "已刷新无样本"
    if sample_status:
        snapshot_status = sample_status
    snapshot_fresh = 1 if is_snapshot_fresh(snapshot_at) else 0
    return {
        "id": raw.get("id"),
        "sku_code": normalize_text(raw.get("sku_code")),
        "brand": normalize_text(raw.get("brand")),
        "product_name": normalize_text(raw.get("product_name")),
        "spec_text": normalize_text(raw.get("spec_text")),
        "efficacy_tags": efficacy,
        "current_price": price,
        "purchase_price": purchase_price,
        "gross_margin": margin,
        "unit_gross_profit": calculate_unit_profit(price, purchase_price),
        "six_month_sales": sales,
        "half_year_gross_profit": round(calculate_unit_profit(price, purchase_price) * sales, 2),
        "profit_contribution_share": round(parse_float(raw.get("profit_contribution_share")), 4),
        "supplier": normalize_text(raw.get("supplier")),
        "case_pack": normalize_text(raw.get("case_pack")),
        "shelf_risk": normalize_text(raw.get("shelf_risk")),
        "current_role": current_role,
        "notes": normalize_text(raw.get("notes")),
        "unit_price": calculate_unit_price(price, normalize_text(raw.get("spec_text"))),
        "fluoride": parse_bool(raw.get("fluoride")),
        "target_group": target_group,
        "promo_type": normalize_promo_type(raw.get("promo_type")),
        "must_keep": parse_bool(raw.get("must_keep")),
        "substitute_relation": normalize_text(raw.get("substitute_relation")),
        "price_band": determine_price_band(price),
        "margin_zone": margin_zone(resolved_role, margin),
        "structural_role": resolved_role,
        "taobao_avg_price": round(parse_float(raw.get("taobao_avg_price")), 2),
        "taobao_min_price": round(parse_float(raw.get("taobao_min_price")), 2),
        "taobao_max_price": round(parse_float(raw.get("taobao_max_price")), 2),
        "taobao_sample_count": sample_count,
        "price_disorder_flag": disorder_flag,
        "online_heat_score": heat_score,
        "market_snapshot_at": snapshot_at,
        "market_snapshot_status": snapshot_status,
        "market_snapshot_fresh": snapshot_fresh,
        "market_sample_quality": market_sample_quality(sample_count, bool(snapshot_fresh), snapshot_status),
        "market_sample_status": snapshot_status,
        "market_source_mode": normalize_text(raw.get("market_source_mode")),
        "market_diagnostic_summary": normalize_text(raw.get("market_diagnostic_summary")),
        "market_query_logs": parse_json_list(raw.get("market_query_logs_json")),
        "market_blocked_platforms": parse_json_list(raw.get("market_blocked_platforms_json")),
        "market_fallback_note": normalize_text(raw.get("market_fallback_note")),
        "market_matched_titles": parse_json_list(raw.get("market_matched_titles_json")),
        "manual_sample_prices": parse_json_list(raw.get("manual_sample_prices_json")),
        "manual_sample_urls": parse_json_list(raw.get("manual_sample_urls_json")),
        "manual_sample_source_platform": normalize_text(raw.get("manual_sample_source_platform")),
        "manual_sample_note": normalize_text(raw.get("manual_sample_note")),
        "price_disorder_label": "价格乱" if disorder_flag else ("价格稳定" if sample_count > 0 else "待更新"),
        "created_at": normalize_text(raw.get("created_at")),
        "updated_at": normalize_text(raw.get("updated_at")),
    }


def _rank_percentile(value: float, sorted_values: list[float]) -> float:
    if not sorted_values:
        return 0.0
    position = 0
    for position, candidate in enumerate(sorted_values):
        if value <= candidate:
            break
    return safe_ratio(position + 1, len(sorted_values))


def _adjust_counts_for_forced(counts: dict[str, int], forced_role: str, forced_count: int) -> dict[str, int]:
    adjusted = counts.copy()
    if forced_count <= adjusted.get(forced_role, 0):
        return adjusted
    diff = forced_count - adjusted.get(forced_role, 0)
    adjusted[forced_role] = forced_count
    donors = ["常规品", "利润品", "引流品"] if forced_role == "引流品" else ["常规品", "引流品", "利润品"]
    donors = [role for role in donors if role != forced_role]
    for donor in donors:
        while diff > 0 and adjusted[donor] > 0:
            adjusted[donor] -= 1
            diff -= 1
    return adjusted


def _positive_min(values: list[float], default: float = 0.0) -> float:
    positives = [value for value in values if value > 0]
    return min(positives) if positives else default


def _positive_max(values: list[float], default: float = 0.0) -> float:
    positives = [value for value in values if value > 0]
    return max(positives) if positives else default


def _positive_min_at_least(values: list[float], floor_value: float, default: float = 0.0) -> float:
    positives = [value for value in values if value > 0 and value >= floor_value]
    return min(positives) if positives else default


def _dedupe_texts(items: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        text = normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results


def consumer_efficacy_label(sku: dict[str, Any]) -> str:
    efficacy = normalize_efficacy(sku.get("efficacy_tags"))
    target_group = normalize_target_group(sku.get("target_group"), efficacy)
    text = " ".join(
        [
            normalize_text(sku.get("product_name")),
            normalize_text(sku.get("efficacy_tags")),
            normalize_text(sku.get("notes")),
            target_group,
        ]
    ).lower()

    if target_group == "儿童" or efficacy == "儿童" or any(token in text for token in ["儿童", "宝宝", "小孩", "幼儿"]):
        return "儿童专用护理"
    if efficacy == "抗敏" or any(token in text for token in ["抗敏", "敏感", "修护", "舒缓", "牙龈", "护龈"]):
        return "敏感修护/牙龈护理"
    if efficacy == "美白" or any(token in text for token in ["美白", "亮白", "炫白", "去黄", "去渍", "洁白"]):
        return "美白亮白"
    if efficacy == "清新口气" or any(token in text for token in ["清新", "口气", "薄荷", "清凉", "净味", "留香"]):
        return "口气清新"
    if efficacy == "草本" or any(token in text for token in ["草本", "竹盐", "中草药", "天然", "植物", "本草"]):
        return "草本天然护理"
    if efficacy == "防蛀" or any(token in text for token in ["防蛀", "含氟", "护齿", "清洁", "洁净"]):
        return "基础防蛀/日常清洁"
    return "综合护理/其他"


def _attach_profit_metrics(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[float]]:
    total_profit = sum(max(parse_float(row.get("half_year_gross_profit")), 0.0) for row in rows)
    enriched_rows: list[dict[str, Any]] = []
    gross_profit_values: list[float] = []
    for row in rows:
        gross_profit = round(parse_float(row.get("half_year_gross_profit")), 2)
        contribution = round(safe_ratio(gross_profit, total_profit), 4) if total_profit > 0 else 0.0
        enriched_rows.append(
            {
                **row,
                "half_year_gross_profit": gross_profit,
                "profit_contribution_share": contribution,
            }
        )
        gross_profit_values.append(gross_profit)
    return enriched_rows, sorted(gross_profit_values)


def _signal_scores(
    sku: dict[str, Any],
    sales_values: list[float],
    unit_prices: list[float],
    gross_profit_values: list[float],
) -> dict[str, float]:
    sales = parse_float(sku.get("six_month_sales"))
    price = parse_float(sku.get("current_price"))
    margin = parse_float(sku.get("gross_margin"))
    heat = parse_float(sku.get("online_heat_score"))
    taobao_avg = parse_float(sku.get("taobao_avg_price"))
    disorder = bool(parse_bool(sku.get("price_disorder_flag")))
    efficacy = normalize_efficacy(sku.get("efficacy_tags"))
    profit_contribution = parse_float(sku.get("profit_contribution_share"))
    half_year_profit = parse_float(sku.get("half_year_gross_profit"))
    sales_rank = _rank_percentile(sales, sales_values)
    unit_rank = _rank_percentile(parse_float(sku.get("unit_price")), unit_prices)
    profit_rank = _rank_percentile(half_year_profit, gross_profit_values)

    lead_score = 0.0
    if disorder and heat >= HIGH_HEAT_THRESHOLD:
        lead_score += 200
    lead_score += 28 if price <= 14.9 else 10 if price <= 19.9 else 0
    lead_score += sales_rank * 22
    lead_score += profit_rank * 12
    lead_score += 12 if taobao_avg > 0 and price <= taobao_avg else 0
    lead_score += 6 if parse_bool(sku.get("must_keep")) else 0
    lead_score += profit_contribution * 16

    profit_score = 0.0
    profit_score += 26 if price >= 30 else 16 if price >= 22 else 0
    profit_score += 14 if efficacy in {"美白", "抗敏"} else 6 if efficacy == "草本" else 0
    profit_score += 12 if margin >= 0.32 else 5 if margin >= 0.28 else 0
    profit_score += 10 if taobao_avg > 0 and price >= taobao_avg * 1.05 else 0
    profit_score += unit_rank * 14
    profit_score += profit_rank * 24
    profit_score += profit_contribution * 42

    regular_score = 0.0
    regular_score += 22 if 15 <= price <= 29.9 else 8
    regular_score += 15 if 0.25 <= margin <= 0.32 else 6
    regular_score += 12 if taobao_avg > 0 and abs(price - taobao_avg) / taobao_avg <= 0.08 else 0
    regular_score += 8 if efficacy in DEFAULT_REQUIRED_EFFICACY else 4
    regular_score += (1 - abs(sales_rank - 0.55)) * 12
    regular_score += profit_rank * 16
    regular_score += profit_contribution * 24

    return {
        "lead": round(lead_score, 2),
        "regular": round(regular_score, 2),
        "profit": round(profit_score, 2),
    }


def _recommend_structural_price_plan(sku: dict[str, Any], role: str) -> dict[str, Any]:
    cost = parse_float(sku.get("purchase_price"))
    current_price = parse_float(sku.get("current_price"))
    taobao_avg = parse_float(sku.get("taobao_avg_price"))
    taobao_min = parse_float(sku.get("taobao_min_price"))
    taobao_max = parse_float(sku.get("taobao_max_price"))
    low, high = margin_target(role)
    margin_floor = round_retail_price(price_for_margin(cost, low))
    margin_ceiling = round_retail_price(price_for_margin(cost, high))

    if role == "引流品":
        anchor_options = [
            ("淘宝低位", taobao_min),
            ("淘宝均价下沿", taobao_avg * 0.96 if taobao_avg else 0.0),
            ("当前售价微调", current_price * 0.98 if current_price else 0.0),
        ]
        candidates = [(label, value) for label, value in anchor_options if value > 0]
        anchor_label, anchor_price = min(candidates, key=lambda item: item[1]) if candidates else ("毛利底线", margin_floor)
        floor_price = round_retail_price(
            max(
                margin_floor,
                _positive_min(
                    [
                        taobao_min,
                        taobao_avg * 0.92 if taobao_avg else 0.0,
                        current_price * 0.9 if current_price else 0.0,
                    ],
                    default=margin_floor,
                ),
            )
        )
        ceiling_reference = _positive_min_at_least(
            [
                margin_ceiling,
                taobao_avg,
                taobao_min * 1.08 if taobao_min else 0.0,
                current_price,
            ],
            floor_price,
            default=max(floor_price, margin_ceiling),
        )
        ceiling_price = round_retail_price(max(floor_price, ceiling_reference))
    elif role == "利润品":
        anchor_options = [
            ("淘宝高位", taobao_max * 0.98 if taobao_max else 0.0),
            ("淘宝均价上沿", taobao_avg * 1.06 if taobao_avg else 0.0),
            ("当前售价", current_price),
        ]
        candidates = [(label, value) for label, value in anchor_options if value > 0]
        anchor_label, anchor_price = max(candidates, key=lambda item: item[1]) if candidates else ("毛利中枢", price_for_margin(cost, 0.34))
        floor_price = round_retail_price(
            _positive_max(
                [
                    margin_floor,
                    taobao_avg * 1.02 if taobao_avg else 0.0,
                    current_price * 0.98 if current_price else 0.0,
                ],
                default=margin_floor,
            )
        )
        ceiling_reference = _positive_min_at_least(
            [
                margin_ceiling,
                taobao_max * 1.08 if taobao_max else 0.0,
                taobao_avg * 1.15 if taobao_avg else 0.0,
                current_price * 1.12 if current_price else 0.0,
            ],
            floor_price,
            default=max(floor_price, margin_ceiling),
        )
        ceiling_price = round_retail_price(max(floor_price, ceiling_reference))
    else:
        anchor_options = [
            ("淘宝均价", taobao_avg),
            ("当前售价", current_price),
            ("淘宝中位区间", median([value for value in [taobao_min, taobao_max] if value > 0]) if taobao_min or taobao_max else 0.0),
        ]
        candidates = [(label, value) for label, value in anchor_options if value > 0]
        anchor_label, anchor_price = candidates[0] if candidates else ("毛利中枢", price_for_margin(cost, (low + high) / 2))
        floor_price = round_retail_price(
            _positive_max(
                [
                    margin_floor,
                    taobao_avg * 0.95 if taobao_avg else 0.0,
                    taobao_min * 1.02 if taobao_min else 0.0,
                ],
                default=margin_floor,
            )
        )
        ceiling_reference = _positive_min_at_least(
            [
                margin_ceiling,
                taobao_avg * 1.05 if taobao_avg else 0.0,
                taobao_max * 0.98 if taobao_max else 0.0,
                current_price * 1.03 if current_price else 0.0,
            ],
            floor_price,
            default=max(floor_price, margin_ceiling),
        )
        ceiling_price = round_retail_price(max(floor_price, ceiling_reference))

    if ceiling_price <= 0:
        ceiling_price = max(floor_price, margin_ceiling, current_price, anchor_price)
        ceiling_price = round_retail_price(ceiling_price)
    if floor_price <= 0:
        floor_price = round_retail_price(max(margin_floor, current_price, anchor_price))
    if ceiling_price < floor_price:
        ceiling_price = floor_price

    recommended_price = clamp_price_to_margin_range(anchor_price, cost, role)
    recommended_price = round_retail_price(max(floor_price, recommended_price))
    if ceiling_price > 0:
        recommended_price = round_retail_price(min(recommended_price, ceiling_price))

    range_label = f"{floor_price:.2f}-{ceiling_price:.2f} 元" if ceiling_price > floor_price else f"{floor_price:.2f} 元"
    return {
        "floor_price": floor_price,
        "recommended_price": recommended_price,
        "ceiling_price": ceiling_price,
        "range_label": range_label,
        "anchor_price": round(anchor_price, 2),
        "anchor_label": anchor_label,
    }


def _market_gap_description(sku: dict[str, Any]) -> str:
    taobao_avg = parse_float(sku.get("taobao_avg_price"))
    current_price = parse_float(sku.get("current_price"))
    if taobao_avg <= 0:
        return "暂无可用的淘宝均价样本，先按类目结构和毛利目标判断。"
    delta_ratio = safe_ratio(current_price - taobao_avg, taobao_avg)
    if abs(delta_ratio) < 0.05:
        return f"当前售价与淘宝均价 {taobao_avg:.2f} 元接近。"
    if delta_ratio > 0:
        return f"当前售价高于淘宝均价 {taobao_avg:.2f} 元，偏高约 {delta_ratio:.0%}。"
    return f"当前售价低于淘宝均价 {taobao_avg:.2f} 元，偏低约 {abs(delta_ratio):.0%}。"


def _build_action(role: str, sku: dict[str, Any], price_plan: dict[str, Any]) -> tuple[str, str]:
    margin = parse_float(sku.get("gross_margin"))
    low, high = margin_target(role)
    heat = parse_float(sku.get("online_heat_score"))
    disorder = bool(parse_bool(sku.get("price_disorder_flag")))
    must_keep = bool(parse_bool(sku.get("must_keep")))
    current_price = parse_float(sku.get("current_price"))
    taobao_avg = parse_float(sku.get("taobao_avg_price"))
    profit_share = parse_float(sku.get("profit_contribution_share"))
    suggested_price = parse_float(price_plan.get("recommended_price"))
    range_label = normalize_text(price_plan.get("range_label"))
    floor_price = parse_float(price_plan.get("floor_price"))
    ceiling_price = parse_float(price_plan.get("ceiling_price"))

    if disorder and heat >= HIGH_HEAT_THRESHOLD:
        return (
            "建议低价引流",
            f"价格带较乱且综合热度 {heat:.0f} 分偏高，建议把售价收敛到 {range_label}，优先落在 {suggested_price:.2f} 元附近做低价引流。",
        )
    if disorder and heat < LOW_HEAT_THRESHOLD and not must_keep and profit_share < 0.12:
        return (
            "建议下架",
            f"价格带较乱且综合热度仅 {heat:.0f} 分，继续保留会拉低陈列效率，可优先考虑下架或替换。",
        )
    if disorder and heat < LOW_HEAT_THRESHOLD and profit_share >= 0.12:
        return (
            "建议调整售价",
            f"价格虽然较乱且热度不高，但半年利润贡献仍有 {profit_share:.1%}，建议先把售价收敛到 {range_label} 再观察。",
        )
    if role == "利润品":
        if margin < low or current_price < floor_price:
            return (
                "建议利润定价",
                f"该商品更适合作为利润品，建议售价抬到 {range_label}，优先执行 {suggested_price:.2f} 元，把毛利拉回 {low:.0%}-{high:.0%}。",
            )
        return (
            "建议利润定价",
            f"当前更适合做利润品，建议把售价稳定在 {range_label} 内承接高毛利。{_market_gap_description(sku)}",
        )
    if role == "引流品":
        if current_price > ceiling_price or margin > high:
            return (
                "建议低价引流",
                f"当前商品被分到引流层，建议售价压到 {range_label}，优先落在 {suggested_price:.2f} 元附近，同时把毛利控制在 {low:.0%}-{high:.0%}。",
            )
        return (
            "建议维持常规价",
            f"当前售价已经接近引流位，建议继续承担基础引流职责。{_market_gap_description(sku)}",
        )
    if margin < low or margin > high:
        return (
            "建议调整售价",
            f"当前毛利率 {margin:.1%} 不在常规品目标区间 {low:.0%}-{high:.0%}，建议把售价调整到 {range_label}，优先执行 {suggested_price:.2f} 元。",
        )
    if taobao_avg > 0 and abs(current_price - taobao_avg) / taobao_avg >= 0.12:
        return (
            "建议调整售价",
            f"{_market_gap_description(sku)} 建议向 {range_label} 回归，减少价格带割裂。",
        )
    return (
        "建议维持常规价",
        f"当前售价、市场锚点和毛利区间基本匹配，适合继续做常规主销。{_market_gap_description(sku)}",
    )


def _reserve_regular_coverage(
    scored_rows: list[dict[str, Any]],
    assigned_codes: set[str],
    regular_quota: int,
) -> tuple[list[str], dict[str, list[str]]]:
    if regular_quota <= 0:
        return [], {}

    lookup = {row["sku_code"]: row for row in scored_rows}
    available = [row for row in scored_rows if row["sku_code"] not in assigned_codes]
    reserved_codes: list[str] = []
    notes: defaultdict[str, list[str]] = defaultdict(list)

    band_counts = Counter(normalize_text(row.get("price_band")) for row in available if normalize_text(row.get("price_band")))
    for band, _ in sorted(
        band_counts.items(),
        key=lambda item: (-item[1], PRICE_BAND_ORDER.get(item[0], 99)),
    ):
        if len(reserved_codes) >= regular_quota:
            break
        candidates = [row for row in available if normalize_text(row.get("price_band")) == band and row["sku_code"] not in reserved_codes]
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda row: (
                row["_scores"]["regular"],
                parse_float(row.get("half_year_gross_profit")),
                parse_int(row.get("six_month_sales")),
                -PRICE_BAND_ORDER.get(normalize_text(row.get("price_band")), 99),
            ),
        )
        reserved_codes.append(best["sku_code"])
        notes[best["sku_code"]].append(f"保留 {band} 价格带的常规承接。")

    covered_efficacies = {normalize_efficacy(lookup[code].get("efficacy_tags")) for code in reserved_codes if code in lookup}
    efficacy_counts = Counter(normalize_efficacy(row.get("efficacy_tags")) for row in available)
    for efficacy in DEFAULT_REQUIRED_EFFICACY:
        if len(reserved_codes) >= regular_quota or efficacy_counts.get(efficacy, 0) == 0 or efficacy in covered_efficacies:
            continue
        candidates = [
            row
            for row in available
            if normalize_efficacy(row.get("efficacy_tags")) == efficacy and row["sku_code"] not in reserved_codes
        ]
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda row: (
                row["_scores"]["regular"],
                parse_float(row.get("half_year_gross_profit")),
                parse_int(row.get("six_month_sales")),
            ),
        )
        reserved_codes.append(best["sku_code"])
        covered_efficacies.add(efficacy)
        notes[best["sku_code"]].append(f"补足 {efficacy} 功效的常规承接。")

    return reserved_codes, dict(notes)


def _role_selection_bonus(
    row: dict[str, Any],
    role: str,
    quota_for_role: int,
    band_counts: Counter[str],
    brand_counts: Counter[str],
    efficacy_counts: Counter[str],
) -> tuple[float, list[str]]:
    band = normalize_text(row.get("price_band"))
    brand = normalize_text(row.get("brand")) or "未填品牌"
    efficacy = normalize_efficacy(row.get("efficacy_tags"))
    profit_share = parse_float(row.get("profit_contribution_share"))
    must_keep = bool(parse_bool(row.get("must_keep")))

    bonus = 0.0
    notes: list[str] = []
    brand_cap = max(1, math.ceil(quota_for_role * 0.5)) if quota_for_role else 1
    band_cap = max(1, math.ceil(quota_for_role * 0.55)) if quota_for_role else 1

    if band and band_counts[band] == 0:
        bonus += 6
        notes.append(f"补足 {band} 价格带的{role}层。")
    if brand_counts[brand] >= brand_cap:
        bonus -= 18
    if band and band_counts[band] >= band_cap:
        bonus -= 10

    if role == "利润品":
        bonus += profit_share * 42
        if efficacy in {"美白", "抗敏", "草本"} and efficacy_counts[efficacy] == 0:
            bonus += 4
        if parse_float(row.get("gross_margin")) >= 0.32:
            bonus += 6
        if profit_share >= 0.12:
            notes.append("半年利润贡献较高，优先承接利润角色。")
    elif role == "引流品":
        bonus += profit_share * 15
        if band in {"<=9.9", "10-14.9", "15-19.9"}:
            bonus += 4
        if must_keep:
            bonus += 4
        if parse_bool(row.get("price_disorder_flag")) and parse_float(row.get("online_heat_score")) >= HIGH_HEAT_THRESHOLD:
            notes.append("价格较乱且热度高，优先承担引流职责。")
    else:
        bonus += profit_share * 18
        if efficacy in DEFAULT_REQUIRED_EFFICACY and efficacy_counts[efficacy] == 0:
            bonus += 4

    return bonus, notes


def _pick_role_candidates(
    scored_rows: list[dict[str, Any]],
    *,
    role: str,
    quota_for_role: int,
    assigned: dict[str, str],
    reserved_regular_codes: set[str],
    assignment_notes: dict[str, list[str]],
) -> None:
    if quota_for_role <= 0:
        return

    lookup = {row["sku_code"]: row for row in scored_rows}
    score_key = ROLE_SCORE_KEY[role]

    def current_count() -> int:
        return sum(1 for assigned_role in assigned.values() if assigned_role == role)

    for allow_reserved_regular in (False, True):
        while current_count() < quota_for_role:
            role_codes = [code for code, assigned_role in assigned.items() if assigned_role == role]
            band_counts = Counter(normalize_text(lookup[code].get("price_band")) for code in role_codes if code in lookup)
            brand_counts = Counter((normalize_text(lookup[code].get("brand")) or "未填品牌") for code in role_codes if code in lookup)
            efficacy_counts = Counter(normalize_efficacy(lookup[code].get("efficacy_tags")) for code in role_codes if code in lookup)

            candidates = [
                row
                for row in scored_rows
                if row["sku_code"] not in assigned
                and (allow_reserved_regular or row["sku_code"] not in reserved_regular_codes)
            ]
            if not candidates:
                break

            best_row: dict[str, Any] | None = None
            best_notes: list[str] = []
            best_key: tuple[float, float, int, float] | None = None
            for row in candidates:
                bonus, notes = _role_selection_bonus(row, role, quota_for_role, band_counts, brand_counts, efficacy_counts)
                score = row["_scores"][score_key] + bonus
                candidate_key = (
                    score,
                    parse_float(row.get("half_year_gross_profit")),
                    parse_int(row.get("six_month_sales")),
                    -float(PRICE_BAND_ORDER.get(normalize_text(row.get("price_band")), 99)),
                )
                if best_key is None or candidate_key > best_key:
                    best_key = candidate_key
                    best_row = row
                    best_notes = notes

            if not best_row:
                break
            assigned[best_row["sku_code"]] = role
            if best_notes:
                assignment_notes.setdefault(best_row["sku_code"], []).extend(best_notes)
        if current_count() >= quota_for_role:
            break


def _build_recommendation_basis(
    sku: dict[str, Any],
    role: str,
    price_plan: dict[str, Any],
    assignment_notes: list[str],
) -> list[str]:
    low, high = margin_target(role)
    taobao_avg = parse_float(sku.get("taobao_avg_price"))
    taobao_min = parse_float(sku.get("taobao_min_price"))
    taobao_max = parse_float(sku.get("taobao_max_price"))
    sample_count = parse_int(sku.get("taobao_sample_count"))
    profit_share = parse_float(sku.get("profit_contribution_share"))
    half_year_profit = parse_float(sku.get("half_year_gross_profit"))
    heat = parse_float(sku.get("online_heat_score"))
    disorder = bool(parse_bool(sku.get("price_disorder_flag")))

    basis = list(assignment_notes)
    basis.append(f"半年毛利额约 {half_year_profit:.2f} 元，利润贡献 {profit_share:.1%}。")
    if taobao_avg > 0:
        basis.append(
            f"淘宝样本 {sample_count} 条，均价 {taobao_avg:.2f} 元，区间 {taobao_min:.2f}-{taobao_max:.2f} 元。"
        )
    else:
        basis.append("暂无稳定淘宝样本，建议价更多依据毛利目标与结构定位。")
    basis.append(
        f"{role} 目标毛利 {low:.0%}-{high:.0%}，建议价区间 {price_plan['range_label']}，推荐先落在 {price_plan['recommended_price']:.2f} 元。"
    )
    basis.append(
        f"{'价格较乱' if disorder else '价格相对稳定'}，综合热度 {heat:.0f} 分，市场锚点为 {price_plan['anchor_label']} {price_plan['anchor_price']:.2f} 元。"
    )
    return _dedupe_texts(basis)[:4]


def _assign_structural_roles(skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [enrich_sku(item) for item in skus]
    if not normalized:
        return []

    sales_values = sorted(parse_float(item.get("six_month_sales")) for item in normalized)
    unit_prices = sorted(parse_float(item.get("unit_price")) for item in normalized if parse_float(item.get("unit_price")) > 0)
    if not unit_prices:
        unit_prices = [0.0 for _ in normalized]
    normalized, gross_profit_values = _attach_profit_metrics(normalized)
    quota = allocate_role_counts(len(normalized))

    forced_leads = [
        item for item in normalized
        if parse_bool(item.get("price_disorder_flag")) and parse_float(item.get("online_heat_score")) >= HIGH_HEAT_THRESHOLD
    ]
    quota = _adjust_counts_for_forced(quota, "引流品", len(forced_leads))

    scored_rows = []
    for item in normalized:
        scores = _signal_scores(item, sales_values, unit_prices, gross_profit_values)
        scored_rows.append({**item, "_scores": scores})

    assigned: dict[str, str] = {}
    assignment_notes: dict[str, list[str]] = {}
    for item in forced_leads:
        assigned[item["sku_code"]] = "引流品"
        assignment_notes.setdefault(item["sku_code"], []).append("价格较乱且热度高，优先放入引流层。")

    regular_reservations, reservation_notes = _reserve_regular_coverage(scored_rows, set(assigned), quota["常规品"])
    for sku_code, notes in reservation_notes.items():
        assignment_notes.setdefault(sku_code, []).extend(notes)

    _pick_role_candidates(
        scored_rows,
        role="利润品",
        quota_for_role=max(0, quota["利润品"]),
        assigned=assigned,
        reserved_regular_codes=set(regular_reservations),
        assignment_notes=assignment_notes,
    )
    _pick_role_candidates(
        scored_rows,
        role="引流品",
        quota_for_role=max(0, quota["引流品"]),
        assigned=assigned,
        reserved_regular_codes=set(regular_reservations),
        assignment_notes=assignment_notes,
    )

    for sku_code in regular_reservations:
        if sku_code not in assigned and sum(1 for role in assigned.values() if role == "常规品") < quota["常规品"]:
            assigned[sku_code] = "常规品"

    for item in sorted(
        scored_rows,
        key=lambda row: (
            -row["_scores"]["regular"],
            -parse_float(row.get("half_year_gross_profit")),
            -parse_int(row.get("six_month_sales")),
            row.get("sku_code", ""),
        ),
    ):
        if item["sku_code"] in assigned:
            continue
        assigned[item["sku_code"]] = "常规品"

    recommendations: list[dict[str, Any]] = []
    for item in scored_rows:
        structural_role = assigned[item["sku_code"]]
        price_plan = _recommend_structural_price_plan(item, structural_role)
        suggested_price = price_plan["recommended_price"]
        action, reason = _build_action(structural_role, item, price_plan)
        low, high = margin_target(structural_role)
        recommendation_basis = _build_recommendation_basis(
            item,
            structural_role,
            price_plan,
            assignment_notes.get(item["sku_code"], []),
        )
        recommendations.append(
            {
                **item,
                "structural_role": structural_role,
                "margin_zone": margin_zone(structural_role, parse_float(item.get("gross_margin"))),
                "target_margin_range": f"{low:.0%}-{high:.0%}",
                "suggested_price": suggested_price,
                "suggested_price_floor": price_plan["floor_price"],
                "suggested_price_ceiling": price_plan["ceiling_price"],
                "suggested_price_range_label": price_plan["range_label"],
                "suggested_price_anchor": price_plan["anchor_price"],
                "suggested_price_anchor_label": price_plan["anchor_label"],
                "suggested_margin": calculate_margin(suggested_price, parse_float(item.get("purchase_price"))),
                "action": action,
                "reason": reason,
                "recommendation_basis": recommendation_basis,
                "role_scores": item["_scores"],
            }
        )
    recommendations.sort(
        key=lambda item: (
            ACTION_PRIORITY.get(item["action"], 99),
            item["structural_role"] != "引流品",
            item["brand"],
            item["product_name"],
        )
    )
    return recommendations


def recommend_existing_skus(skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _assign_structural_roles(skus)


def simulate_batch_pricing(
    skus: list[dict[str, Any]],
    *,
    brand: str = "",
    structural_role: str = "",
    price_band: str = "",
    strategy: str = "adjust_by_amount",
    amount: float = 0.0,
) -> dict[str, Any]:
    structured_before = recommend_existing_skus(skus)
    selected_before = [
        item
        for item in structured_before
        if (not brand or normalize_text(item.get("brand")) == brand)
        and (not structural_role or normalize_text(item.get("structural_role")) == structural_role)
        and (not price_band or normalize_text(item.get("price_band")) == price_band)
    ]
    if not selected_before:
        return {
            "filters": {
                "brand": brand,
                "structural_role": structural_role,
                "price_band": price_band,
                "strategy": strategy,
                "amount": round(amount, 2),
            },
            "summary": {
                "affected_count": 0,
                "avg_margin_before": 0.0,
                "avg_margin_after": 0.0,
                "total_half_year_profit_before": 0.0,
                "total_half_year_profit_after": 0.0,
                "profit_delta": 0.0,
                "action_changed_count": 0,
                "role_changed_count": 0,
            },
            "items": [],
        }

    selected_codes = {item["sku_code"] for item in selected_before}
    before_map = {item["sku_code"]: item for item in structured_before}
    updated_raw_skus: list[dict[str, Any]] = []
    for raw in skus:
        enriched = before_map.get(normalize_text(raw.get("sku_code")))
        if not enriched or enriched["sku_code"] not in selected_codes:
            updated_raw_skus.append(raw)
            continue

        target_price = parse_float(enriched.get("current_price"))
        if strategy == "to_taobao_avg":
            target_price = parse_float(enriched.get("taobao_avg_price")) or target_price
        elif strategy == "to_system_suggested":
            target_price = parse_float(enriched.get("suggested_price")) or target_price
        else:
            target_price = parse_float(enriched.get("current_price")) + amount

        updated_raw_skus.append({**raw, "current_price": round(max(target_price, 0.1), 2)})

    structured_after = recommend_existing_skus(updated_raw_skus)
    after_map = {item["sku_code"]: item for item in structured_after}

    items: list[dict[str, Any]] = []
    for sku_code in selected_codes:
        before_item = before_map[sku_code]
        after_item = after_map[sku_code]
        items.append(
            {
                "sku_code": sku_code,
                "brand": before_item.get("brand"),
                "product_name": before_item.get("product_name"),
                "before_price": before_item.get("current_price"),
                "after_price": after_item.get("current_price"),
                "before_margin": before_item.get("gross_margin"),
                "after_margin": after_item.get("gross_margin"),
                "before_profit": before_item.get("half_year_gross_profit"),
                "after_profit": after_item.get("half_year_gross_profit"),
                "before_action": before_item.get("action"),
                "after_action": after_item.get("action"),
                "before_role": before_item.get("structural_role"),
                "after_role": after_item.get("structural_role"),
                "before_price_band": before_item.get("price_band"),
                "after_price_band": after_item.get("price_band"),
            }
        )
    items.sort(
        key=lambda item: (
            item["before_action"] != item["after_action"],
            item["before_role"] != item["after_role"],
            parse_float(item["after_profit"]) - parse_float(item["before_profit"]),
        ),
        reverse=True,
    )

    return {
        "filters": {
            "brand": brand,
            "structural_role": structural_role,
            "price_band": price_band,
            "strategy": strategy,
            "amount": round(amount, 2),
        },
        "summary": {
            "affected_count": len(items),
            "avg_margin_before": round(
                safe_ratio(sum(parse_float(item["before_margin"]) for item in items), len(items)),
                4,
            ),
            "avg_margin_after": round(
                safe_ratio(sum(parse_float(item["after_margin"]) for item in items), len(items)),
                4,
            ),
            "total_half_year_profit_before": round(sum(parse_float(item["before_profit"]) for item in items), 2),
            "total_half_year_profit_after": round(sum(parse_float(item["after_profit"]) for item in items), 2),
            "profit_delta": round(
                sum(parse_float(item["after_profit"]) - parse_float(item["before_profit"]) for item in items),
                2,
            ),
            "action_changed_count": sum(1 for item in items if item["before_action"] != item["after_action"]),
            "role_changed_count": sum(1 for item in items if item["before_role"] != item["after_role"]),
        },
        "items": items[:24],
    }


def enrich_candidate(raw: dict[str, Any], skus: list[dict[str, Any]]) -> dict[str, Any]:
    efficacy = normalize_efficacy(raw.get("efficacy_tags"))
    online_price = round(parse_float(raw.get("online_reference_price")), 2)
    expected_purchase_price = round(parse_float(raw.get("expected_purchase_price")), 2)
    heat_score = round(parse_float(raw.get("heat_score")), 1)
    target_group = normalize_target_group(raw.get("target_group"), efficacy)
    provisional_role = infer_role(
        current_role="",
        price=online_price,
        efficacy=efficacy,
        target_group=target_group,
        margin=calculate_margin(online_price, expected_purchase_price),
        sales=0,
        heat_score=heat_score,
    )
    comparison_rows = match_candidate_against_catalog(
        {
            "brand": normalize_text(raw.get("brand")),
            "product_name": normalize_text(raw.get("product_name")),
            "spec_text": normalize_text(raw.get("spec_text")),
            "efficacy_tags": efficacy,
            "online_reference_price": online_price,
            "target_group": target_group,
        },
        skus,
    )
    suggested_price = clamp_price_to_margin_range(
        median([parse_float(item.get("current_price")) for item in comparison_rows if parse_float(item.get("current_price")) > 0]) if comparison_rows else online_price,
        expected_purchase_price,
        provisional_role,
    )
    if suggested_price <= 0:
        suggested_price = _recommend_candidate_price(expected_purchase_price, provisional_role, online_price)
    expected_margin = calculate_margin(suggested_price, expected_purchase_price)
    score = score_candidate(
        candidate={
            **raw,
            "brand": normalize_text(raw.get("brand")),
            "product_name": normalize_text(raw.get("product_name")),
            "spec_text": normalize_text(raw.get("spec_text")),
            "efficacy_tags": efficacy,
            "online_reference_price": online_price,
            "expected_purchase_price": expected_purchase_price,
            "target_group": target_group,
            "heat_score": heat_score,
            "differentiation": normalize_text(raw.get("differentiation")),
        },
        skus=skus,
        comparisons=comparison_rows,
        expected_margin=expected_margin,
        role=provisional_role,
    )
    return {
        "id": raw.get("id"),
        "brand": normalize_text(raw.get("brand")),
        "product_name": normalize_text(raw.get("product_name")),
        "spec_text": normalize_text(raw.get("spec_text")),
        "efficacy_tags": efficacy,
        "online_reference_price": online_price,
        "expected_purchase_price": expected_purchase_price,
        "source_platform": normalize_text(raw.get("source_platform")) or "其他",
        "product_url": normalize_text(raw.get("product_url")),
        "heat_score": heat_score,
        "differentiation": normalize_text(raw.get("differentiation")),
        "intended_replace_sku": normalize_text(raw.get("intended_replace_sku")),
        "notes": normalize_text(raw.get("notes")),
        "fluoride": parse_bool(raw.get("fluoride")),
        "target_group": target_group,
        "promo_type": normalize_promo_type(raw.get("promo_type")),
        "must_keep": parse_bool(raw.get("must_keep")),
        "substitute_relation": normalize_text(raw.get("substitute_relation")),
        "proposed_role": provisional_role,
        "suggested_price": suggested_price,
        "expected_margin": expected_margin,
        "unit_price": calculate_unit_price(suggested_price, normalize_text(raw.get("spec_text"))),
        "price_band": determine_price_band(suggested_price or online_price),
        "recommendation_score": score["total_score"],
        "score_breakdown": score["breakdown"],
        "suggestion_status": score["suggestion_status"],
        "comparison_rows": comparison_rows,
    }


def _recommend_candidate_price(cost: float, role: str, online_price: float) -> float:
    low, high = margin_target(role)
    midpoint = (low + high) / 2
    margin_based_price = price_for_margin(cost, midpoint)
    anchor = online_price or margin_based_price
    return clamp_price_to_margin_range(anchor, cost, role)


def match_candidate_against_catalog(candidate: dict[str, Any], skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_price = parse_float(candidate.get("online_reference_price") or candidate.get("suggested_price"))
    candidate_band = determine_price_band(candidate_price)
    candidate_amount, candidate_unit = parse_spec(normalize_text(candidate.get("spec_text")))
    candidate_name = normalize_text(candidate.get("product_name"))
    candidate_brand = normalize_text(candidate.get("brand"))
    candidate_target_group = normalize_text(candidate.get("target_group"))
    candidate_efficacy = normalize_efficacy(candidate.get("efficacy_tags"))

    matches: list[dict[str, Any]] = []
    for sku in skus:
        sku_price = parse_float(sku.get("current_price"))
        sku_band = determine_price_band(sku_price)
        sku_amount, sku_unit = parse_spec(normalize_text(sku.get("spec_text")))
        spec_score = 0.0
        if candidate_amount and sku_amount and candidate_unit == sku_unit:
            spec_score = 20 * max(0.0, 1 - abs(candidate_amount - sku_amount) / max(candidate_amount, sku_amount, 1))
        score = 0.0
        if normalize_efficacy(sku.get("efficacy_tags")) == candidate_efficacy:
            score += 35
        if sku_band == candidate_band:
            score += 20
        if normalize_text(sku.get("brand")) == candidate_brand:
            score += 15
        if normalize_text(sku.get("target_group")) == candidate_target_group:
            score += 10
        score += spec_score
        score += 10 * similarity(candidate_name, normalize_text(sku.get("product_name")))
        risk = cannibalization_risk(candidate, sku)
        matches.append(
            {
                **sku,
                "match_score": round(score, 1),
                "cannibalization_risk": risk,
            }
        )
    matches.sort(key=lambda item: (-item["match_score"], -parse_float(item.get("six_month_sales")), item.get("product_name", "")))
    return matches[:3]


def cannibalization_risk(candidate: dict[str, Any], sku: dict[str, Any]) -> str:
    candidate_price = parse_float(candidate.get("online_reference_price") or candidate.get("suggested_price"))
    sku_price = parse_float(sku.get("current_price"))
    candidate_amount, candidate_unit = parse_spec(normalize_text(candidate.get("spec_text")))
    sku_amount, sku_unit = parse_spec(normalize_text(sku.get("spec_text")))
    same_spec = bool(
        candidate_amount
        and sku_amount
        and candidate_unit == sku_unit
        and safe_ratio(min(candidate_amount, sku_amount), max(candidate_amount, sku_amount)) >= 0.85
    )
    same_brand = normalize_text(candidate.get("brand")) == normalize_text(sku.get("brand"))
    same_efficacy = normalize_efficacy(candidate.get("efficacy_tags")) == normalize_efficacy(sku.get("efficacy_tags"))
    close_price = abs(candidate_price - sku_price) <= 3
    if same_brand and same_efficacy and close_price and same_spec:
        return "高"
    if (same_efficacy and determine_price_band(candidate_price) == determine_price_band(sku_price)) or (same_brand and abs(candidate_price - sku_price) <= 5):
        return "中"
    return "低"


def score_candidate(
    *,
    candidate: dict[str, Any],
    skus: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    expected_margin: float,
    role: str,
) -> dict[str, Any]:
    band = determine_price_band(parse_float(candidate.get("online_reference_price") or candidate.get("suggested_price")))
    efficacy = normalize_efficacy(candidate.get("efficacy_tags"))
    band_count = sum(1 for sku in skus if determine_price_band(parse_float(sku.get("current_price"))) == band)
    efficacy_count = sum(1 for sku in skus if normalize_efficacy(sku.get("efficacy_tags")) == efficacy)
    role_count = sum(1 for sku in skus if normalize_text(sku.get("structural_role") or sku.get("current_role")) == role)
    structure_gap = 25 if min(band_count, efficacy_count, role_count) == 0 else 18 if min(band_count, efficacy_count, role_count) == 1 else 10

    low, high = margin_target(role)
    distance = 0.0
    if expected_margin < low:
        distance = low - expected_margin
    elif expected_margin > high:
        distance = expected_margin - high
    margin_fit = 25 if distance == 0 else 18 if distance <= 0.03 else 10 if distance <= 0.06 else 4

    heat_score = min(max(parse_float(candidate.get("heat_score")), 0), 100) / 100 * 20

    band_average = safe_ratio(len(skus), len(PRICE_BANDS)) if skus else 1
    if band_count < band_average * 0.6:
        price_band_score = 15
    elif band_count < band_average * 1.2:
        price_band_score = 10
    else:
        price_band_score = 5

    differentiation_text = normalize_text(candidate.get("differentiation"))
    if efficacy_count == 0:
        differentiation_score = 10
    elif len(differentiation_text) >= 20:
        differentiation_score = 9
    elif len(differentiation_text) >= 8:
        differentiation_score = 7
    else:
        differentiation_score = 3

    brand = normalize_text(candidate.get("brand"))
    existing_brands = {normalize_text(sku.get("brand")) for sku in skus}
    brand_synergy = 5 if brand in existing_brands else 3 if len(existing_brands) < 8 else 2

    risks = [item.get("cannibalization_risk") for item in comparisons]
    penalty = 20 if "高" in risks else 10 if "中" in risks else 0

    total = round(structure_gap + margin_fit + heat_score + price_band_score + differentiation_score + brand_synergy - penalty, 1)

    if total >= 70 and any(
        parse_int(item.get("six_month_sales")) <= 20 and item.get("action") == "建议下架"
        for item in comparisons
    ):
        suggestion_status = "建议替换现有SKU"
    elif total >= 75:
        suggestion_status = "建议上新"
    else:
        suggestion_status = "建议观察"

    return {
        "total_score": max(total, 0),
        "breakdown": {
            "结构补位": structure_gap,
            "毛利适配": margin_fit,
            "线上热度": round(heat_score, 1),
            "价格带合理性": price_band_score,
            "差异化程度": differentiation_score,
            "品牌协同": brand_synergy,
            "蚕食风险扣分": penalty,
        },
        "suggestion_status": suggestion_status,
    }


def auto_select_candidates(candidates: list[dict[str, Any]], skus: list[dict[str, Any]], limit: int = 6) -> dict[str, Any]:
    if not candidates:
        return {
            "selected": [],
            "waitlist": [],
            "principles": [
                "先补价格带和功效缺口",
                "优先替换低效或建议下架的SKU",
                "确保建议售价能落在目标毛利区间",
                "控制与现有SKU的蚕食风险",
            ],
            "summary": {
                "selected_count": 0,
                "waitlist_count": 0,
                "replacement_count": 0,
                "gap_fill_count": 0,
            },
        }

    existing_recommendations = recommend_existing_skus(skus)
    delist_codes = {
        item["sku_code"] for item in existing_recommendations if item["action"] == "建议下架"
    }
    band_count = Counter(determine_price_band(parse_float(item.get("current_price"))) for item in skus)
    efficacy_count = Counter(normalize_efficacy(item.get("efficacy_tags")) for item in skus)
    brand_count = Counter(normalize_text(item.get("brand")) for item in skus if normalize_text(item.get("brand")))
    dominant_brands = {
        brand for brand, count in brand_count.items() if skus and safe_ratio(count, len(skus)) >= 0.45
    }
    missing_bands = {band["label"] for band in PRICE_BANDS if band_count.get(band["label"], 0) == 0}
    weak_bands = {band["label"] for band in PRICE_BANDS if band_count.get(band["label"], 0) <= 1}
    missing_efficacies = {efficacy for efficacy in DEFAULT_REQUIRED_EFFICACY if efficacy_count.get(efficacy, 0) == 0}
    weak_efficacies = {efficacy for efficacy in EFFICACY_OPTIONS if efficacy_count.get(efficacy, 0) <= 1}

    ranked_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_band = determine_price_band(
            parse_float(candidate.get("suggested_price") or candidate.get("online_reference_price"))
        )
        candidate_efficacy = normalize_efficacy(candidate.get("efficacy_tags"))
        candidate_brand = normalize_text(candidate.get("brand"))
        comparison_rows = candidate.get("comparison_rows") or []
        risk_level = "低"
        if any(item.get("cannibalization_risk") == "高" for item in comparison_rows):
            risk_level = "高"
        elif any(item.get("cannibalization_risk") == "中" for item in comparison_rows):
            risk_level = "中"

        auto_score = parse_float(candidate.get("recommendation_score"))
        reasons: list[str] = []
        fills_gap = False
        replacement_targets = [
            item.get("sku_code")
            for item in comparison_rows
            if normalize_text(item.get("sku_code")) in delist_codes
        ]

        if candidate_band in missing_bands:
            auto_score += 15
            fills_gap = True
            reasons.append(f"补足 {candidate_band} 价格带空缺")
        elif candidate_band in weak_bands:
            auto_score += 8
            reasons.append(f"增强 {candidate_band} 价格带厚度")

        if candidate_efficacy in missing_efficacies:
            auto_score += 15
            fills_gap = True
            reasons.append(f"补位 {candidate_efficacy} 功效缺口")
        elif candidate_efficacy in weak_efficacies:
            auto_score += 8
            reasons.append(f"增强 {candidate_efficacy} 功效层级")

        if replacement_targets:
            auto_score += 12
            reasons.append(f"可替换低效SKU：{' / '.join(replacement_targets[:2])}")
        elif candidate.get("suggestion_status") == "建议替换现有SKU":
            auto_score += 8
            reasons.append("适合替代现有低效商品")

        role = normalize_text(candidate.get("proposed_role"))
        expected_margin = parse_float(candidate.get("expected_margin"))
        low, high = margin_target(role)
        if low <= expected_margin <= high:
            auto_score += 10
            reasons.append(f"预计毛利率 {expected_margin:.0%} 达标")
        elif abs(expected_margin - low) <= 0.03 or abs(expected_margin - high) <= 0.03:
            auto_score += 5
            reasons.append("毛利率接近目标区间")

        heat_score = parse_float(candidate.get("heat_score"))
        if heat_score >= 80:
            reasons.append(f"线上热度高（{heat_score:.0f}分）")
        elif heat_score >= 60:
            reasons.append(f"线上热度中高（{heat_score:.0f}分）")

        if risk_level == "高":
            auto_score -= 12
            reasons.append("与现有SKU蚕食风险高")
        elif risk_level == "中":
            auto_score -= 6
            reasons.append("与现有SKU存在中等蚕食风险")
        else:
            reasons.append("与现有SKU蚕食风险低")

        if candidate_brand and candidate_brand not in dominant_brands and brand_count.get(candidate_brand, 0) == 0:
            auto_score += 4
            reasons.append("有助于降低品牌过度集中")

        ranked_candidates.append(
            {
                **candidate,
                "price_band": candidate_band,
                "auto_select_score": round(auto_score, 1),
                "auto_select_reasons": reasons[:5],
                "replacement_targets": replacement_targets,
                "cannibalization_level": risk_level,
                "fills_gap": fills_gap,
            }
        )

    ranked_candidates.sort(
        key=lambda item: (
            -item["auto_select_score"],
            item["cannibalization_level"] == "高",
            item["brand"],
            item["product_name"],
        )
    )

    selected: list[dict[str, Any]] = []
    waitlist: list[dict[str, Any]] = []
    selected_band_count = Counter()
    selected_efficacy_count = Counter()
    selected_brand_count = Counter()

    for item in ranked_candidates:
        brand = normalize_text(item.get("brand"))
        efficacy = normalize_efficacy(item.get("efficacy_tags"))
        band = normalize_text(item.get("price_band"))
        force_select = bool(item["replacement_targets"]) or item["fills_gap"]

        if item["auto_select_score"] < 65 and item.get("suggestion_status") == "建议观察":
            waitlist.append({**item, "auto_pick_decision": "继续观察"})
            continue
        if not force_select and item["cannibalization_level"] == "高":
            waitlist.append({**item, "auto_pick_decision": "暂不推荐"})
            continue
        if not force_select and selected_band_count[band] >= 2:
            waitlist.append({**item, "auto_pick_decision": "价格带已覆盖"})
            continue
        if not force_select and selected_efficacy_count[efficacy] >= 2:
            waitlist.append({**item, "auto_pick_decision": "同功效已足够"})
            continue
        if not force_select and selected_brand_count[brand] >= 2:
            waitlist.append({**item, "auto_pick_decision": "同品牌候选过多"})
            continue

        if len(selected) < limit:
            selected_band_count[band] += 1
            selected_efficacy_count[efficacy] += 1
            selected_brand_count[brand] += 1
            selected.append(
                {
                    **item,
                    "auto_pick_decision": "优先替换"
                    if item["replacement_targets"] or item.get("suggestion_status") == "建议替换现有SKU"
                    else "优先上新",
                }
            )
        else:
            waitlist.append({**item, "auto_pick_decision": "候补观察"})

    if not selected and ranked_candidates:
        fallback = ranked_candidates[0]
        selected = [{**fallback, "auto_pick_decision": "优先观察"}]
        waitlist = ranked_candidates[1:6]

    return {
        "selected": selected,
        "waitlist": waitlist[:6],
        "principles": [
            "先补价格带和功效缺口",
            "优先替换低效或建议下架的SKU",
            "确保建议售价能落在目标毛利区间",
            "控制与现有SKU的蚕食风险",
        ],
        "summary": {
            "selected_count": len(selected),
            "waitlist_count": len(waitlist[:6]),
            "replacement_count": sum(1 for item in selected if item["auto_pick_decision"] == "优先替换"),
            "gap_fill_count": sum(1 for item in selected if item.get("fills_gap")),
        },
    }


def recommend_brand_missing_hits(
    *,
    brand: str,
    current_brand_skus: list[dict[str, Any]],
    all_skus: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    normalized_brand = normalize_text(brand)
    brand_key = normalized_brand.lower()
    if not brand_key:
        return []

    current_rows = [item for item in current_brand_skus if normalize_text(item.get("brand")).lower() == brand_key]
    same_brand_candidates = [
        item for item in candidates
        if normalize_text(item.get("brand")).lower() == brand_key
    ]
    if not same_brand_candidates:
        return []

    current_codes = {normalize_text(item.get("sku_code")) for item in current_rows}
    current_names = [normalize_text(item.get("product_name")) for item in current_rows]
    current_specs = [normalize_text(item.get("spec_text")) for item in current_rows]
    current_band_count = Counter(determine_price_band(parse_float(item.get("current_price"))) for item in current_rows)
    current_efficacy_count = Counter(normalize_efficacy(item.get("efficacy_tags")) for item in current_rows)
    all_band_count = Counter(determine_price_band(parse_float(item.get("current_price"))) for item in all_skus)
    all_efficacy_count = Counter(normalize_efficacy(item.get("efficacy_tags")) for item in all_skus)
    weak_bands = {band["label"] for band in PRICE_BANDS if all_band_count.get(band["label"], 0) <= 1}
    weak_efficacies = {efficacy for efficacy in EFFICACY_OPTIONS if all_efficacy_count.get(efficacy, 0) <= 1}

    recommendations: list[dict[str, Any]] = []
    for candidate in same_brand_candidates:
        candidate_id = normalize_text(candidate.get("id"))
        if candidate_id and candidate_id in current_codes:
            continue

        candidate_name = normalize_text(candidate.get("product_name"))
        candidate_spec = normalize_text(candidate.get("spec_text"))
        candidate_band = determine_price_band(parse_float(candidate.get("suggested_price") or candidate.get("online_reference_price")))
        candidate_efficacy = normalize_efficacy(candidate.get("efficacy_tags"))
        comparison_rows = [
            item for item in (candidate.get("comparison_rows") or [])
            if normalize_text(item.get("brand")) == normalized_brand
        ]

        high_overlap = any(
            item.get("cannibalization_risk") == "高"
            and similarity(candidate_name, normalize_text(item.get("product_name"))) >= 0.78
            for item in comparison_rows
        )
        if high_overlap:
            continue
        if any(
            similarity(candidate_name, current_name) >= 0.86
            and candidate_spec
            and candidate_spec == current_spec
            for current_name, current_spec in zip(current_names, current_specs)
        ):
            continue

        brand_gap_score = 0.0
        category_gap_score = 0.0
        reasons: list[str] = []

        if current_band_count.get(candidate_band, 0) == 0:
            brand_gap_score += 18
            reasons.append(f"补这个品牌的 {candidate_band} 价格带")
        elif current_band_count.get(candidate_band, 0) == 1:
            brand_gap_score += 8
            reasons.append(f"增强这个品牌的 {candidate_band} 价格带厚度")

        if current_efficacy_count.get(candidate_efficacy, 0) == 0:
            brand_gap_score += 18
            reasons.append(f"补这个品牌的 {candidate_efficacy} 功效款")
        elif current_efficacy_count.get(candidate_efficacy, 0) == 1:
            brand_gap_score += 8
            reasons.append(f"增强这个品牌的 {candidate_efficacy} 功效层级")

        if candidate_band in weak_bands:
            category_gap_score += 8
            reasons.append(f"也能补全类目的 {candidate_band} 价格带")
        if candidate_efficacy in weak_efficacies:
            category_gap_score += 8
            reasons.append(f"也能补全类目的 {candidate_efficacy} 功效")

        role = normalize_text(candidate.get("proposed_role"))
        expected_margin = parse_float(candidate.get("expected_margin"))
        low, high = margin_target(role)
        margin_fit = 0.0
        if low <= expected_margin <= high:
            margin_fit = 14
            reasons.append(f"预计毛利率 {expected_margin:.0%} 可落在 {role} 目标区间")
        elif abs(expected_margin - low) <= 0.03 or abs(expected_margin - high) <= 0.03:
            margin_fit = 7
            reasons.append("预计毛利率接近可做区间")

        heat_score = min(max(parse_float(candidate.get("heat_score")), 0.0), 100.0)
        brand_heat_score = round(heat_score * 0.28, 1)
        if heat_score >= 85:
            reasons.append(f"这个品牌线上热度很高（{heat_score:.0f}分）")
        elif heat_score >= 70:
            reasons.append(f"这个品牌线上热度较高（{heat_score:.0f}分）")

        risk_level = "低"
        if any(item.get("cannibalization_risk") == "高" for item in comparison_rows):
            risk_level = "高"
        elif any(item.get("cannibalization_risk") == "中" for item in comparison_rows):
            risk_level = "中"

        penalty = 0.0
        if risk_level == "高":
            penalty = 16
            reasons.append("和现有同品牌商品过近，建议谨慎观察")
        elif risk_level == "中":
            penalty = 8
            reasons.append("和现有同品牌商品存在一定蚕食风险")
        else:
            reasons.append("和现有同品牌商品重叠风险较低")

        base_score = parse_float(candidate.get("recommendation_score")) * 0.25
        total_score = round(base_score + brand_heat_score + brand_gap_score + category_gap_score + margin_fit - penalty, 1)

        recommendation_action = "建议上这个品牌爆款"
        if risk_level == "高":
            recommendation_action = "已有同类商品过近，建议观察"
        elif current_band_count.get(candidate_band, 0) == 0:
            recommendation_action = "建议补这个品牌价格带"
        elif current_efficacy_count.get(candidate_efficacy, 0) == 0:
            recommendation_action = "建议补这个品牌功效款"

        gap_reason = reasons[0] if reasons else "建议补这个品牌爆款"
        recommendations.append(
            {
                **candidate,
                "brand_gap_reason": gap_reason,
                "brand_recommendation_score": total_score,
                "recommendation_action": recommendation_action,
                "brand_recommendation_reasons": _dedupe_texts(reasons)[:4],
                "brand_cannibalization_level": risk_level,
            }
        )

    recommendations.sort(
        key=lambda item: (
            -parse_float(item.get("brand_recommendation_score")),
            item.get("recommendation_action") == "已有同类商品过近，建议观察",
            -parse_float(item.get("heat_score")),
            item.get("product_name", ""),
        )
    )
    return recommendations[:limit]


def build_dashboard(skus: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    recommendations = recommend_existing_skus(skus)
    sku_map = {item["sku_code"]: item for item in recommendations}
    structured_skus = [sku_map[item["sku_code"]] for item in recommendations]

    summary = {
        "sku_count": len(structured_skus),
        "candidate_count": len(candidates),
        "brand_count": len({normalize_text(item.get("brand")) for item in structured_skus if normalize_text(item.get("brand"))}),
        "sales_total": sum(parse_int(item.get("six_month_sales")) for item in structured_skus),
        "average_margin": round(safe_ratio(sum(parse_float(item.get("gross_margin")) for item in structured_skus), len(structured_skus)), 4) if structured_skus else 0,
        "delist_count": sum(1 for item in recommendations if item["action"] == "建议下架"),
        "pricing_issue_count": sum(1 for item in recommendations if item["action"] in {"建议调整售价", "建议低价引流", "建议利润定价"}),
        "market_pending_count": sum(1 for item in structured_skus if item.get("market_snapshot_status") == "待更新"),
    }

    price_band_sales = defaultdict(int)
    price_band_count = Counter()
    price_band_margin_total = defaultdict(float)
    price_band_details: dict[str, list[dict[str, Any]]] = {band["label"]: [] for band in PRICE_BANDS}
    for sku in recommendations:
        band = determine_price_band(parse_float(sku.get("current_price")))
        price_band_count[band] += 1
        price_band_sales[band] += parse_int(sku.get("six_month_sales"))
        price_band_margin_total[band] += parse_float(sku.get("gross_margin"))
        price_band_details.setdefault(band, []).append(sku)

    price_band_distribution = [
        {
            "label": band["label"],
            "count": price_band_count.get(band["label"], 0),
            "sales": price_band_sales.get(band["label"], 0),
            "average_margin": round(
                safe_ratio(price_band_margin_total.get(band["label"], 0.0), price_band_count.get(band["label"], 0)),
                4,
            ),
            "disorder_count": sum(parse_bool(item.get("price_disorder_flag")) for item in price_band_details.get(band["label"], [])),
        }
        for band in PRICE_BANDS
    ]

    brand_counter = Counter()
    brand_sales = defaultdict(int)
    brand_details: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sku in structured_skus:
        brand = normalize_text(sku.get("brand")) or "未填品牌"
        brand_counter[brand] += 1
        brand_sales[brand] += parse_int(sku.get("six_month_sales"))
        brand_details[brand].append(sku)
    brand_distribution = [
        {
            "brand": brand,
            "sku_count": count,
            "sales": brand_sales[brand],
            "share": round(safe_ratio(count, len(structured_skus)), 4) if structured_skus else 0,
        }
        for brand, count in brand_counter.most_common()
    ]

    consumer_efficacy_counter = Counter()
    efficacy_details: dict[str, list[dict[str, Any]]] = {bucket["label"]: [] for bucket in CONSUMER_EFFICACY_BUCKETS}
    for sku in structured_skus:
        consumer_label = consumer_efficacy_label(sku)
        consumer_efficacy_counter[consumer_label] += 1
        efficacy_details.setdefault(consumer_label, []).append(sku)
    efficacy_distribution = [
        {
            "efficacy": bucket["label"],
            "count": consumer_efficacy_counter.get(bucket["label"], 0),
            "description": bucket["description"],
        }
        for bucket in CONSUMER_EFFICACY_BUCKETS
        if consumer_efficacy_counter.get(bucket["label"], 0) or bucket["label"] in CONSUMER_REQUIRED_BUCKETS
    ]

    role_counter = Counter(normalize_text(item.get("structural_role")) for item in recommendations)
    role_distribution = [{"role": role, "count": role_counter.get(role, 0)} for role in ROLES]

    margin_health = {"below": 0, "within": 0, "above": 0}
    for sku in recommendations:
        zone = margin_zone(normalize_text(sku.get("structural_role")), parse_float(sku.get("gross_margin")))
        margin_health[zone] += 1

    structure_gaps: list[str] = []
    for band in PRICE_BANDS[:4]:
        if price_band_count.get(band["label"], 0) == 0:
            structure_gaps.append(f"价格带 {band['label']} 暂无SKU，基础覆盖不完整。")
    for efficacy in CONSUMER_REQUIRED_BUCKETS:
        if consumer_efficacy_counter.get(efficacy, 0) == 0:
            structure_gaps.append(f"{efficacy} 缺口明显，消费者核心需求覆盖不足。")
    if role_counter.get("引流品", 0) == 0:
        structure_gaps.append("当前缺少明确的引流品，低价入口不够清晰。")
    if role_counter.get("利润品", 0) == 0:
        structure_gaps.append("当前缺少利润品，高毛利承接不足。")
    if brand_distribution and brand_distribution[0]["share"] >= 0.5:
        structure_gaps.append(f"品牌集中度偏高，{brand_distribution[0]['brand']} SKU 占比已超过 50%。")

    candidate_status = Counter(item.get("suggestion_status") for item in candidates)

    return {
        "summary": summary,
        "price_band_distribution": price_band_distribution,
        "price_band_details": price_band_details,
        "brand_distribution": brand_distribution,
        "brand_details": dict(brand_details),
        "efficacy_distribution": efficacy_distribution,
        "efficacy_details": efficacy_details,
        "role_distribution": role_distribution,
        "margin_health": margin_health,
        "structure_gaps": structure_gaps,
        "candidate_status": dict(candidate_status),
    }
