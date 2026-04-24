from __future__ import annotations

import html
import json
import math
import random
import re
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from .logic import is_snapshot_fresh, normalize_efficacy, normalize_text, now_iso, parse_spec

PLATFORM_LABELS = {
    "jd": "京东",
    "tmall": "天猫",
    "xiaohongshu": "小红书",
    "taobao": "淘宝",
    "douyin": "抖音",
}

DEFAULT_PLATFORMS = ["jd", "tmall", "xiaohongshu", "taobao"]
MARKET_SNAPSHOT_PLATFORMS = ["jd", "tmall", "taobao", "xiaohongshu"]
DEFAULT_HOT_KEYWORDS = ["牙膏", "美白牙膏", "抗敏牙膏", "儿童牙膏", "草本牙膏", "口气清新牙膏"]

KNOWN_BRANDS = [
    "云南白药",
    "高露洁",
    "佳洁士",
    "舒适达",
    "黑人",
    "DARLIE",
    "狮王",
    "冷酸灵",
    "中华",
    "两面针",
    "Ora2",
    "玛尔斯",
    "elmex",
    "花王",
    "片仔癀",
    "参半",
    "BOP",
    "PAP",
]

EFFICACY_KEYWORDS = {
    "防蛀": ["防蛀", "护龈", "含氟", "氟化"],
    "美白": ["美白", "亮白", "炫白", "去黄", "洁白"],
    "抗敏": ["抗敏", "敏感", "修护", "舒缓"],
    "清新口气": ["清新", "口气", "薄荷", "清凉", "净味"],
    "草本": ["草本", "中草药", "竹盐", "天然"],
    "儿童": ["儿童", "宝宝", "小孩", "幼儿", "kid"],
}

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

WARM_UP_URLS = {
    "jd": "https://www.jd.com/",
    "tmall": "https://www.tmall.com/",
    "taobao": "https://www.taobao.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "douyin": "https://www.douyin.com/",
}

COOKIE_FALLBACK_LABEL = "Cookie兜底"
ANONYMOUS_LABEL = "匿名"


@dataclass
class RawHotItem:
    platform: str
    title: str
    url: str
    price: float
    sales_text: str
    popularity: float
    rank: int
    keyword: str = ""


def crawl_hot_products(
    *,
    keyword: str = "牙膏",
    keywords: list[str] | None = None,
    platforms: list[str] | None = None,
    limit_per_platform: int = 20,
    cookies: dict[str, str] | None = None,
    timeout: int = 16,
    slow_mode: bool = True,
) -> dict[str, Any]:
    selected_platforms = [item for item in (platforms or DEFAULT_PLATFORMS) if item in PLATFORM_LABELS]
    if not selected_platforms:
        selected_platforms = DEFAULT_PLATFORMS
    cookie_map = cookies or {}
    keyword_variants = _expand_hot_keywords(keyword, keywords)
    per_keyword_limit = max(4, min(12, math.ceil(limit_per_platform / max(len(keyword_variants), 1)) + 1))

    results: list[RawHotItem] = []
    errors: dict[str, str] = {}
    platform_reports: list[dict[str, Any]] = []

    for index, platform in enumerate(selected_platforms):
        platform_cookie = cookie_map.get(platform, "")
        platform_report = {
            "platform": platform,
            "platform_label": PLATFORM_LABELS.get(platform, platform),
            "used_cookie_fallback": False,
            "queries_attempted": 0,
            "success_count": 0,
            "blocked": False,
            "stopped_early": False,
            "message": "",
        }
        try:
            if slow_mode:
                _warm_up_platform(platform, "", timeout)
                _human_pause(index)
            for keyword_index, current_keyword in enumerate(keyword_variants):
                platform_report["queries_attempted"] += 1
                items, query_report = _crawl_platform_with_cookie_fallback(
                    platform,
                    current_keyword,
                    per_keyword_limit,
                    platform_cookie,
                    timeout,
                    pause_index=index + keyword_index + 1,
                    slow_mode=slow_mode,
                )
                platform_report["used_cookie_fallback"] = platform_report["used_cookie_fallback"] or query_report["used_cookie_fallback"]
                platform_report["success_count"] += len(items)
                if query_report["blocked"]:
                    platform_report["blocked"] = True
                    platform_report["stopped_early"] = True
                    platform_report["message"] = query_report["message"] or "平台触发拦截，已停止继续请求。"
                    break
                results.extend(
                    [
                        RawHotItem(**{**item.__dict__, "keyword": current_keyword})
                        for item in items[:per_keyword_limit]
                    ]
                )
        except Exception as exc:  # pragma: no cover - network/runtime boundary
            errors[platform] = str(exc)
            platform_report["message"] = str(exc)
        if not platform_report["message"]:
            if platform_report["success_count"] > 0:
                mode = COOKIE_FALLBACK_LABEL if platform_report["used_cookie_fallback"] else ANONYMOUS_LABEL
                platform_report["message"] = f"{mode}模式完成，抓到 {platform_report['success_count']} 条原始结果。"
            elif platform_cookie:
                platform_report["message"] = "匿名和 Cookie 兜底都没有拿到可用结果。"
            else:
                platform_report["message"] = "匿名请求没有拿到可用结果，可改用 Cookie 兜底或浏览器辅助采集。"
        platform_reports.append(platform_report)

    deduped = _dedupe_raw_items(results)
    return {
        "items": [item.__dict__ for item in deduped],
        "errors": errors,
        "keywords_used": keyword_variants,
        "platform_reports": platform_reports,
    }


def refresh_market_snapshots(
    *,
    skus: list[dict[str, Any]],
    cookies: dict[str, str] | None = None,
    force: bool = False,
    timeout: int = 16,
    limit_per_platform: int = 10,
) -> dict[str, Any]:
    cookie_map = cookies or {}
    snapshots: list[dict[str, Any]] = []
    skipped = 0
    errors: dict[str, str] = {}

    for index, sku in enumerate(skus):
        sample_status = normalize_text(sku.get("market_sample_status"))
        has_reliable_samples = sample_status in {"有效淘宝样本", "人工补样本"} or (
            not sample_status and int(sku.get("taobao_sample_count") or 0) > 0
        )
        if not force and is_snapshot_fresh(sku.get("market_snapshot_at")) and has_reliable_samples:
            skipped += 1
            continue
        try:
            snapshots.append(
                build_market_snapshot_for_sku(
                    sku,
                    cookies=cookie_map,
                    timeout=timeout,
                    limit_per_platform=limit_per_platform,
                    crawl_index=index,
                )
            )
        except Exception as exc:  # pragma: no cover - network/runtime boundary
            sku_key = normalize_text(sku.get("sku_code")) or normalize_text(sku.get("product_name")) or f"sku-{index + 1}"
            errors[sku_key] = str(exc)
    return {
        "snapshots": snapshots,
        "refreshed": len(snapshots),
        "skipped": skipped,
        "errors": errors,
    }


def build_market_snapshot_for_sku(
    sku: dict[str, Any],
    *,
    cookies: dict[str, str] | None = None,
    timeout: int = 16,
    limit_per_platform: int = 10,
    crawl_index: int = 0,
) -> dict[str, Any]:
    cookie_map = cookies or {}
    keyword = _build_market_query(sku)
    taobao_prices: list[float] = []
    fallback_prices: list[float] = []
    platform_heat_scores: list[float] = []
    platform_errors: dict[str, str] = {}
    taobao_limit = max(6, limit_per_platform)
    taobao_queries = _build_market_queries(sku)
    query_logs: list[dict[str, Any]] = []
    matched_titles: list[str] = []
    taobao_selected_quality = "none"

    for offset, platform in enumerate(MARKET_SNAPSHOT_PLATFORMS):
        try:
            _warm_up_platform(platform, "", timeout)
            if platform == "taobao":
                taobao_items: list[RawHotItem] = []
                for query_index, query in enumerate(taobao_queries):
                    items, attempt_report = _crawl_platform_with_cookie_fallback(
                        platform,
                        query,
                        taobao_limit,
                        cookie_map.get(platform, ""),
                        timeout,
                        pause_index=crawl_index + offset + query_index,
                        slow_mode=True,
                    )
                    filter_result = _filter_relevant_market_items(items, sku, target_count=taobao_limit)
                    relevant_items = filter_result["items"]
                    if _quality_rank(filter_result["quality"]) > _quality_rank(taobao_selected_quality):
                        taobao_selected_quality = filter_result["quality"]
                    query_logs.append(
                        {
                            "platform": "淘宝",
                            "query": query,
                            "raw_count": len(items),
                            "selected_count": len(relevant_items),
                            "quality": filter_result["quality"],
                            "mode": COOKIE_FALLBACK_LABEL if attempt_report["used_cookie_fallback"] else ANONYMOUS_LABEL,
                        }
                    )
                    if attempt_report["blocked"]:
                        platform_errors[platform] = attempt_report["message"] or "淘宝返回拦截页"
                        break
                    if relevant_items:
                        taobao_items.extend(relevant_items)
                        taobao_items = _dedupe_market_items(taobao_items)
                        taobao_prices = _extract_prices_from_items(taobao_items)
                        matched_titles.extend(item.title for item in relevant_items[:3])
                    if len(taobao_prices) >= min(6, taobao_limit):
                        break
                if taobao_items:
                    platform_heat_scores.append(_aggregate_platform_heat(taobao_items[: min(len(taobao_items), 6)]))
                continue
            items, attempt_report = _crawl_platform_with_cookie_fallback(
                platform,
                keyword,
                limit_per_platform,
                cookie_map.get(platform, ""),
                timeout,
                pause_index=crawl_index + offset,
                slow_mode=True,
            )
            filter_result = _filter_relevant_market_items(items, sku, target_count=limit_per_platform)
            relevant_items = filter_result["items"]
            query_logs.append(
                {
                    "platform": PLATFORM_LABELS.get(platform, platform),
                    "query": keyword,
                    "raw_count": len(items),
                    "selected_count": len(relevant_items),
                    "quality": filter_result["quality"],
                    "mode": COOKIE_FALLBACK_LABEL if attempt_report["used_cookie_fallback"] else ANONYMOUS_LABEL,
                }
            )
            if attempt_report["blocked"]:
                platform_errors[platform] = attempt_report["message"] or "平台返回拦截页"
                continue
            if not relevant_items:
                continue
            platform_heat_scores.append(_aggregate_platform_heat(relevant_items))
            fallback_prices.extend(_extract_prices_from_items(relevant_items[: min(len(relevant_items), 6)]))
        except Exception as exc:  # pragma: no cover - network/runtime boundary
            platform_errors[platform] = str(exc)

    sample_source_prices = taobao_prices
    market_source_mode = "淘宝有效样本"
    market_sample_status = "有效淘宝样本"
    diagnostic_summary = "已拿到可用淘宝样本。"
    fallback_note = ""

    if not taobao_prices and fallback_prices:
        sample_source_prices = fallback_prices
        market_source_mode = "跨平台替代"
        market_sample_status = "跨平台替代"
        diagnostic_summary = "淘宝没有形成有效样本，当前改用跨平台公开价格作临时参考。"
        fallback_note = "当前价格锚点来自京东/天猫/小红书等替代样本。"
    elif taobao_prices:
        if len(taobao_prices) < 3:
            market_source_mode = "淘宝样本不足"
            market_sample_status = "样本不足"
            diagnostic_summary = "淘宝样本数量不足，建议继续补抓；如果公开页始终抓不到，可以先人工补样本。"
            if taobao_selected_quality == "approximate":
                market_source_mode = "淘宝近似样本/样本不足"
                diagnostic_summary = "淘宝只抓到少量近似样本，建议优先人工复核或补样本。"
        elif taobao_selected_quality == "approximate":
            market_source_mode = "淘宝近似样本"
            market_sample_status = "近似样本"
            diagnostic_summary = "已抓到淘宝样本，但主要来自近似匹配结果，建议人工复核。"
    elif any("拦截" in message or "验证" in message for message in platform_errors.values()):
        market_source_mode = "抓取被拦截"
        market_sample_status = "被拦截"
        diagnostic_summary = "抓取过程中遇到平台拦截或验证，没有形成有效样本。"
    else:
        market_source_mode = "无有效结果"
        market_sample_status = "无结果"
        diagnostic_summary = "公开页面没有查到足够接近的价格样本。"

    avg_price, min_price, max_price, sample_count, disorder_flag = _summarize_price_samples(sample_source_prices)
    online_heat_score = round(_combine_platform_heat(platform_heat_scores), 1)
    return {
        "id": sku.get("id"),
        "sku_code": normalize_text(sku.get("sku_code")),
        "keyword": keyword,
        "taobao_avg_price": avg_price,
        "taobao_min_price": min_price,
        "taobao_max_price": max_price,
        "taobao_sample_count": sample_count,
        "price_disorder_flag": disorder_flag,
        "online_heat_score": online_heat_score,
        "market_snapshot_at": now_iso(),
        "market_sample_status": market_sample_status,
        "market_source_mode": market_source_mode,
        "diagnostic_summary": diagnostic_summary,
        "query_logs": query_logs,
        "blocked_platforms": [
            PLATFORM_LABELS.get(platform, platform)
            for platform, message in platform_errors.items()
            if "拦截" in message or "验证" in message
        ],
        "fallback_note": fallback_note,
        "matched_titles": list(dict.fromkeys(matched_titles))[:6],
        "errors": {
            PLATFORM_LABELS.get(platform, platform): message
            for platform, message in platform_errors.items()
        },
    }


def build_candidates_from_crawled_items(
    items: list[dict[str, Any]],
    existing_skus: list[dict[str, Any]],
    *,
    keyword: str = "牙膏",
    crawl_history: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    existing_brands = {normalize_text(item.get("brand")) for item in existing_skus if normalize_text(item.get("brand"))}
    merged: dict[str, dict[str, Any]] = {}
    history_map = crawl_history or {}

    for index, raw in enumerate(items):
        title = normalize_text(raw.get("title"))
        if not title:
            continue
        normalized_key = _normalize_title_key(title, keyword)
        if not normalized_key:
            continue
        online_price = _safe_positive_float(raw.get("price"))
        if online_price <= 0:
            online_price = _extract_price_from_text(title)

        platform_key = normalize_text(raw.get("platform"))
        platform_label = PLATFORM_LABELS.get(platform_key, platform_key or "其他")
        sales_text = normalize_text(raw.get("sales_text"))
        popularity = _safe_positive_float(raw.get("popularity"))
        rank = int(raw.get("rank") or (index + 1))
        brand = _infer_brand(title, existing_brands)
        efficacy = _infer_efficacy(title)
        spec_text = _extract_spec(title) or "120g"
        target_group = "儿童" if efficacy == "儿童" else "成人"
        if online_price <= 0:
            online_price = _estimate_reference_price(existing_skus, brand, efficacy)
        heat_score = _compute_heat_score(sales_text, popularity, rank)
        history_entry = history_map.get(normalized_key, {})
        continuity_bonus = min(
            18.0,
            int(history_entry.get("consecutive_runs") or 0) * 4
            + min(int(history_entry.get("seen_count") or 0), 5) * 2
            + (3 if normalize_text(raw.get("keyword")) and normalize_text(raw.get("keyword")) != keyword else 0),
        )
        heat_score = min(100.0, heat_score + continuity_bonus)
        expected_purchase_price = round(max(online_price * 0.58, online_price * 0.35), 2)
        notes = _build_notes(platform_label, sales_text)
        if _safe_positive_float(raw.get("price")) <= 0 and _extract_price_from_text(title) <= 0:
            notes = f"{notes}；参考价为估算值"
        if continuity_bonus:
            notes = f"{notes}；连续出现加权 +{continuity_bonus:.0f}"

        payload = {
            "brand": brand,
            "product_name": title[:120],
            "spec_text": spec_text,
            "efficacy_tags": efficacy,
            "online_reference_price": round(online_price, 2),
            "expected_purchase_price": expected_purchase_price,
            "source_platform": platform_label,
            "product_url": normalize_text(raw.get("url")),
            "heat_score": round(heat_score, 1),
            "differentiation": _build_differentiation(title, platform_label, sales_text),
            "intended_replace_sku": "",
            "notes": notes,
            "fluoride": 1 if any(token in title for token in ["含氟", "氟"]) else 0,
            "target_group": target_group,
            "promo_type": "常规款",
            "must_keep": 0,
            "substitute_relation": "",
        }

        existing = merged.get(normalized_key)
        if not existing:
            merged[normalized_key] = payload
            continue
        existing_platforms = set(existing["source_platform"].split("/"))
        existing_platforms.add(platform_label)
        existing["source_platform"] = "/".join(sorted(existing_platforms))
        if payload["online_reference_price"] > 0:
            current_price = existing["online_reference_price"]
            if current_price <= 0:
                existing["online_reference_price"] = payload["online_reference_price"]
            else:
                existing["online_reference_price"] = round(min(current_price, payload["online_reference_price"]), 2)
                existing["expected_purchase_price"] = round(existing["online_reference_price"] * 0.58, 2)
        existing["heat_score"] = round(min(100.0, max(existing["heat_score"], payload["heat_score"]) + 3), 1)
        if payload["product_url"] and not existing["product_url"]:
            existing["product_url"] = payload["product_url"]
        existing["notes"] = f"{existing['notes']} | {payload['notes']}".strip(" |")
        if "多平台热销" not in existing["differentiation"]:
            existing["differentiation"] = f"{existing['differentiation']}；多平台热销".strip("；")

    return sorted(
        merged.values(),
        key=lambda item: (-item["heat_score"], item["brand"], item["product_name"]),
    )


def _crawl_platform(platform: str, keyword: str, limit: int, cookie: str, timeout: int) -> list[RawHotItem]:
    if platform == "jd":
        return _crawl_jd(keyword, limit, cookie, timeout)
    if platform == "taobao":
        return _crawl_taobao_like(keyword, limit, cookie, timeout, target_platform="taobao")
    if platform == "tmall":
        return _crawl_taobao_like(keyword, limit, cookie, timeout, target_platform="tmall")
    if platform == "xiaohongshu":
        return _crawl_xiaohongshu(keyword, limit, cookie, timeout)
    if platform == "douyin":
        return _crawl_douyin(keyword, limit, cookie, timeout)
    return []


def _crawl_platform_with_cookie_fallback(
    platform: str,
    keyword: str,
    limit: int,
    cookie: str,
    timeout: int,
    *,
    pause_index: int,
    slow_mode: bool,
) -> tuple[list[RawHotItem], dict[str, Any]]:
    report = {
        "used_cookie_fallback": False,
        "blocked": False,
        "message": "",
    }
    if slow_mode:
        _human_pause(pause_index)
    try:
        items = _crawl_platform(platform, keyword, limit, "", timeout)
        if items:
            return items, report
    except Exception as exc:  # pragma: no cover - network/runtime boundary
        if _is_block_message(str(exc)):
            report["blocked"] = True
            report["message"] = str(exc)
        elif not report["message"]:
            report["message"] = str(exc)

    if not cookie:
        return [], report

    if slow_mode:
        _cookie_cooldown_pause(pause_index)
    try:
        _warm_up_platform(platform, cookie, timeout)
        if slow_mode:
            _cookie_cooldown_pause(pause_index + 1)
        items = _crawl_platform(platform, keyword, limit, cookie, timeout)
        report["used_cookie_fallback"] = True
        report["blocked"] = False
        report["message"] = ""
        return items, report
    except Exception as exc:  # pragma: no cover - network/runtime boundary
        report["used_cookie_fallback"] = True
        report["blocked"] = _is_block_message(str(exc))
        report["message"] = str(exc)
        return [], report


def _warm_up_platform(platform: str, cookie: str, timeout: int) -> None:
    warm_up_url = WARM_UP_URLS.get(platform)
    if not warm_up_url:
        return
    try:
        _fetch_text(warm_up_url, cookie, min(timeout, 8), validate=False)
    except Exception:  # pragma: no cover - best effort only
        return


def _human_pause(index: int) -> None:
    base = 0.7 + (index % 3) * 0.15
    time.sleep(base + random.uniform(0.45, 1.15))


def _cookie_cooldown_pause(index: int) -> None:
    base = 2.2 + (index % 2) * 0.35
    time.sleep(base + random.uniform(0.8, 1.7))


def _expand_hot_keywords(keyword: str, keywords: list[str] | None = None) -> list[str]:
    explicit_keywords = [normalize_text(item) for item in (keywords or []) if normalize_text(item)]
    inline_keywords = [part.strip() for part in normalize_text(keyword).split(",") if part.strip()]
    base_keywords = explicit_keywords or inline_keywords or DEFAULT_HOT_KEYWORDS
    results: list[str] = []
    for item in [*base_keywords, *DEFAULT_HOT_KEYWORDS]:
        if item and item not in results:
            results.append(item)
    return results[:6]


def _crawl_jd(keyword: str, limit: int, cookie: str, timeout: int) -> list[RawHotItem]:
    ranking_urls = [
        "https://www.jd.com/phb/key_16750d55eb3cd1fe34e69.html",
        "https://www.jd.com/phb/key_167506ffff3185cbab044.html",
        "https://www.jd.com/hprm/16750a0d9a9a9a4032686.html",
    ]
    items: list[RawHotItem] = []
    for url in ranking_urls:
        text = _fetch_text(url, cookie, timeout)
        items.extend(_extract_jd_items(text, limit))
        if len(items) >= limit:
            break
    if items:
        return _dedupe_raw_items(items)[:limit]

    encoded = urllib.parse.quote(keyword)
    search_url = f"https://search.jd.com/Search?keyword={encoded}&psort=3&stock=1"
    return _extract_jd_items(_fetch_text(search_url, cookie, timeout), limit)[:limit]


def _crawl_taobao_like(keyword: str, limit: int, cookie: str, timeout: int, *, target_platform: str) -> list[RawHotItem]:
    encoded = urllib.parse.quote(keyword)
    page_size = 44
    max_page = max(1, min(3, math.ceil(limit / 10)))
    items: list[RawHotItem] = []
    for page_index in range(max_page):
        url = f"https://s.taobao.com/search?q={encoded}&sort=sale-desc&s={page_index * page_size}"
        text = _fetch_text(url, cookie, timeout)
        items.extend(_extract_taobao_items(text, limit, target_platform=target_platform))
        if len(items) >= limit:
            break
    return items[:limit]


def _crawl_xiaohongshu(keyword: str, limit: int, cookie: str, timeout: int) -> list[RawHotItem]:
    encoded = urllib.parse.quote(keyword)
    url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_notes"
    text = _fetch_text(url, cookie, timeout)
    items = _extract_xiaohongshu_items(text, limit)
    if items:
        return items[:limit]
    fallback_url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}"
    return _extract_xiaohongshu_items(_fetch_text(fallback_url, cookie, timeout), limit)[:limit]


def _crawl_douyin(keyword: str, limit: int, cookie: str, timeout: int) -> list[RawHotItem]:
    encoded = urllib.parse.quote(keyword)
    url = f"https://www.douyin.com/search/{encoded}?type=commodity"
    text = _fetch_text(url, cookie, timeout)
    items = _extract_douyin_items(text, limit)
    if items:
        return items[:limit]
    fallback_url = (
        "https://www.douyin.com/aweme/v1/web/general/search/single/"
        f"?keyword={encoded}&search_channel=aweme_general&sort_type=2&count={max(10, min(limit, 30))}&offset=0"
    )
    return _extract_douyin_items(_fetch_text(fallback_url, cookie, timeout), limit)[:limit]


def _fetch_text(url: str, cookie: str, timeout: int, *, validate: bool = True) -> str:
    headers = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/json,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.baidu.com/",
        "Cache-Control": "no-cache",
    }
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        raw = response.read()
    text = raw.decode("utf-8", errors="ignore")
    if validate:
        _raise_if_blocked(url, text)
    return text


def _extract_jd_items(text: str, limit: int) -> list[RawHotItem]:
    items: list[RawHotItem] = []
    patterns = [
        re.compile(
            r'<li[^>]*data-sku="(?P<sku>\d+)"[^>]*>.*?(?:title|data-title)="(?P<title>[^"]*牙膏[^"]*)".{0,1200}?class="p-price"[^>]*>.*?(?P<price>\d+(?:\.\d+)?).*?class="p-commit"[^>]*>.*?(?P<sales>\d+(?:\.\d+)?万?)',
            re.S,
        ),
        re.compile(
            r"(?:TOP|top)\s*(?P<rank>\d+).{0,120}(?P<title>[^<\n]{8,220}牙膏[^<\n]{0,120}).{0,180}(?:已有|评论)\s*(?P<sales>\d+(?:\.\d+)?万?)",
            re.S,
        ),
        re.compile(
            r'"skuId":"(?P<sku>\d+)".{0,220}?"wname":"(?P<title>[^"]{6,220}牙膏[^"]*)".{0,220}?"jdPrice":"(?P<price>\d+(?:\.\d+)?)"',
            re.S,
        ),
        re.compile(
            r'(?P<title>[^<\n]{8,220}牙膏[^<\n]{0,120}).{0,160}(?:已有|评论)\s*(?P<sales>\d+(?:\.\d+)?万?)',
            re.S,
        ),
    ]

    for pattern in patterns:
        for index, match in enumerate(pattern.finditer(text)):
            title = _decode_text_fragment(match.group("title"))
            if "牙膏" not in title:
                continue
            sku = normalize_text(match.groupdict().get("sku", ""))
            rank = int(match.groupdict().get("rank") or (len(items) + 1))
            sales_text = normalize_text(match.groupdict().get("sales", ""))
            price = _safe_positive_float(match.groupdict().get("price", ""))
            url = f"https://item.jd.com/{sku}.html" if sku else ""
            items.append(
                RawHotItem(
                    platform="jd",
                    title=title,
                    price=price,
                    sales_text=sales_text,
                    url=url,
                    popularity=_popularity_from_text(sales_text),
                    rank=rank if rank > 0 else index + 1,
                )
            )
            if len(items) >= limit:
                return _dedupe_raw_items(items)[:limit]
        if items:
            return _dedupe_raw_items(items)[:limit]
    return items[:limit]


def _raise_if_blocked(url: str, text: str) -> None:
    lower_text = text.lower()
    if "deny_h5" in lower_text or '"action":"deny"' in lower_text or '"action": "deny"' in lower_text:
        raise RuntimeError(f"{_platform_name_from_url(url)} 返回拦截页，请补充对应平台 Cookie 后重试。")
    if "captcha" in lower_text or "verify" in lower_text:
        if "xiaohongshu" in lower_text or "taobao" in lower_text or "tmall" in lower_text or "douyin" in lower_text:
            raise RuntimeError(f"{_platform_name_from_url(url)} 触发了验证，请补充对应平台 Cookie 后重试。")


def _is_block_message(message: str) -> bool:
    normalized = normalize_text(message).lower()
    return any(token in normalized for token in ["拦截", "验证", "captcha", "deny"])


def _platform_name_from_url(url: str) -> str:
    lower_url = url.lower()
    if "jd.com" in lower_url:
        return "京东"
    if "tmall.com" in lower_url:
        return "天猫"
    if "taobao.com" in lower_url:
        return "淘宝"
    if "xiaohongshu.com" in lower_url:
        return "小红书"
    if "douyin.com" in lower_url:
        return "抖音"
    return "目标平台"


def _extract_taobao_items(text: str, limit: int, *, target_platform: str) -> list[RawHotItem]:
    pattern = re.compile(
        r'"raw_title":"(?P<title>.*?)".{0,280}?"view_price":"(?P<price>\d+(?:\.\d+)?)".{0,220}?"view_sales":"(?P<sales>.*?)".{0,280}?"nid":"(?P<nid>\d+)".{0,280}?(?:"user_type":"(?P<user_type>\d)"|"isTmall":(?P<is_tmall>true|false))',
        re.S,
    )
    items: list[RawHotItem] = []
    for index, match in enumerate(pattern.finditer(text)):
        title = _decode_text_fragment(match.group("title"))
        if "牙膏" not in title:
            continue
        user_type = normalize_text(match.groupdict().get("user_type"))
        is_tmall = normalize_text(match.groupdict().get("is_tmall")).lower() == "true"
        is_tmall_item = user_type == "1" or is_tmall
        if target_platform == "tmall" and not is_tmall_item:
            continue
        if target_platform == "taobao" and is_tmall_item:
            continue
        nid = normalize_text(match.group("nid"))
        item_url = (
            f"https://detail.tmall.com/item.htm?id={nid}"
            if target_platform == "tmall"
            else f"https://item.taobao.com/item.htm?id={nid}"
        )
        items.append(
            RawHotItem(
                platform=target_platform,
                title=title,
                price=_safe_positive_float(match.group("price")),
                sales_text=_decode_text_fragment(match.group("sales")),
                url=item_url if nid else "",
                popularity=_popularity_from_text(match.group("sales")),
                rank=index + 1,
            )
        )
        if len(items) >= limit:
            break
    return items


def _extract_xiaohongshu_items(text: str, limit: int) -> list[RawHotItem]:
    pattern = re.compile(
        r'"note_id":"(?P<note_id>[a-zA-Z0-9]+)".{0,700}?"title":"(?P<title>[^"]{2,180})".{0,500}?"liked_count":"?(?P<liked>[^",}]*)"?',
        re.S,
    )
    items: list[RawHotItem] = []
    for index, match in enumerate(pattern.finditer(text)):
        title = _decode_text_fragment(match.group("title"))
        if "牙膏" not in title:
            continue
        note_id = normalize_text(match.group("note_id"))
        sales_text = normalize_text(match.group("liked"))
        items.append(
            RawHotItem(
                platform="xiaohongshu",
                title=title,
                price=_extract_price_from_text(title),
                sales_text=sales_text,
                url=f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
                popularity=_popularity_from_text(sales_text),
                rank=index + 1,
            )
        )
        if len(items) >= limit:
            break

    if items:
        return items

    line_based: list[RawHotItem] = []
    for line_index, line in enumerate(text.splitlines()):
        decoded_line = _decode_text_fragment(line)
        if "牙膏" not in decoded_line:
            continue
        if len(decoded_line) < 6:
            continue
        line_based.append(
            RawHotItem(
                platform="xiaohongshu",
                title=decoded_line[:120],
                price=_extract_price_from_text(decoded_line),
                sales_text="",
                url="",
                popularity=0.0,
                rank=line_index + 1,
            )
        )
        if len(line_based) >= limit:
            break
    return line_based


def _extract_douyin_items(text: str, limit: int) -> list[RawHotItem]:
    patterns = [
        re.compile(
            r'"product_id":"(?P<pid>\d+)".{0,280}?"title":"(?P<title>[^"]{2,180}牙膏[^"]*)".{0,260}?"price":"?(?P<price>\d+(?:\.\d+)?)"?.{0,260}?"sales":"(?P<sales>[^"]*)"',
            re.S,
        ),
        re.compile(
            r'"title":"(?P<title>[^"]{2,180}牙膏[^"]*)".{0,240}?"promotion_price":"?(?P<price>\d+(?:\.\d+)?)"?.{0,260}?"volume":"(?P<sales>[^"]*)"',
            re.S,
        ),
    ]
    items: list[RawHotItem] = []
    for pattern in patterns:
        for index, match in enumerate(pattern.finditer(text)):
            title = _decode_text_fragment(match.group("title"))
            if "牙膏" not in title:
                continue
            pid = normalize_text(match.groupdict().get("pid", ""))
            sales_text = _decode_text_fragment(match.group("sales"))
            url = f"https://haohuo.jinritemai.com/views/product/item?id={pid}" if pid else ""
            items.append(
                RawHotItem(
                    platform="douyin",
                    title=title,
                    price=_safe_positive_float(match.group("price")),
                    sales_text=sales_text,
                    url=url,
                    popularity=_popularity_from_text(sales_text),
                    rank=len(items) + 1,
                )
            )
            if len(items) >= limit:
                return items[:limit]

    if items:
        return items[:limit]

    line_based: list[RawHotItem] = []
    for line_index, line in enumerate(text.splitlines()):
        decoded_line = _decode_text_fragment(line)
        if "牙膏" not in decoded_line:
            continue
        price = _extract_price_from_text(decoded_line)
        line_based.append(
            RawHotItem(
                platform="douyin",
                title=decoded_line[:120],
                price=price,
                sales_text="",
                url="",
                popularity=0.0,
                rank=line_index + 1,
            )
        )
        if len(line_based) >= limit:
            break
    return line_based


def _decode_text_fragment(value: str) -> str:
    text = html.unescape(value or "").replace("\\/", "/")
    if "\\u" in text:
        try:
            text = text.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            pass
    text = text.replace("\\n", " ").replace("\\t", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _popularity_from_text(text: str) -> float:
    normalized = normalize_text(text)
    if not normalized:
        return 0.0
    match_wan = re.search(r"(\d+(?:\.\d+)?)\s*万", normalized)
    if match_wan:
        return float(match_wan.group(1)) * 10000
    match_num = re.search(r"(\d+(?:\.\d+)?)", normalized.replace(",", ""))
    if match_num:
        return float(match_num.group(1))
    return 0.0


def _safe_positive_float(value: Any) -> float:
    text = normalize_text(value)
    if not text:
        return 0.0
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def _extract_price_from_text(text: str) -> float:
    normalized = normalize_text(text)
    if not normalized:
        return 0.0
    match = re.search(r"(?:￥|¥)\s*(\d+(?:\.\d+)?)", normalized)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*元", normalized)
    return float(match.group(1)) if match else 0.0


def _extract_spec(title: str) -> str:
    normalized = normalize_text(title).lower()
    if not normalized:
        return ""
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:g|kg|克|ml|l|毫升)(?:\s*[x×*]\s*\d+(?:\.\d+)?)?)", normalized)
    return match.group(1).replace(" ", "") if match else ""


def _infer_brand(title: str, existing_brands: set[str]) -> str:
    title_text = normalize_text(title)
    brand_candidates = sorted(set(KNOWN_BRANDS) | existing_brands, key=len, reverse=True)
    for brand in brand_candidates:
        if brand and brand.lower() in title_text.lower():
            return brand
    first_word = re.split(r"[·\-\s/|【】（）()]", title_text)[0]
    return first_word[:12] if first_word else "待识别品牌"


def _infer_efficacy(title: str) -> str:
    title_text = normalize_text(title).lower()
    for efficacy, words in EFFICACY_KEYWORDS.items():
        if any(word.lower() in title_text for word in words):
            return efficacy
    return "其他"


def _compute_heat_score(sales_text: str, popularity: float, rank: int) -> float:
    sales_value = max(_popularity_from_text(sales_text), popularity)
    rank_score = max(0.0, 35.0 - min(rank, 35) * 1.1)
    sales_score = min(45.0, math.log10(sales_value + 10) * 11.5) if sales_value > 0 else 12.0
    return max(5.0, min(rank_score + sales_score + 20.0, 100.0))


def _build_differentiation(title: str, platform_label: str, sales_text: str) -> str:
    title_text = normalize_text(title)
    hot_text = f"{platform_label}热销"
    if sales_text:
        hot_text = f"{platform_label}热销（销量/热度：{normalize_text(sales_text)}）"
    tokens = []
    for efficacy, words in EFFICACY_KEYWORDS.items():
        if any(word in title_text for word in words):
            tokens.append(f"{efficacy}方向")
            break
    tokens.append(hot_text)
    return "；".join(tokens)


def _build_notes(platform_label: str, sales_text: str) -> str:
    suffix = f"，热销指标：{sales_text}" if sales_text else ""
    return f"自动抓取来源：{platform_label}{suffix}"


def _estimate_reference_price(existing_skus: list[dict[str, Any]], brand: str, efficacy: str) -> float:
    same_brand_prices = [
        _safe_positive_float(item.get("current_price"))
        for item in existing_skus
        if normalize_text(item.get("brand")) == brand and _safe_positive_float(item.get("current_price")) > 0
    ]
    if same_brand_prices:
        same_brand_prices.sort()
        return round(same_brand_prices[len(same_brand_prices) // 2], 2)

    same_efficacy_prices = [
        _safe_positive_float(item.get("current_price"))
        for item in existing_skus
        if normalize_text(item.get("efficacy_tags")) == efficacy and _safe_positive_float(item.get("current_price")) > 0
    ]
    if same_efficacy_prices:
        same_efficacy_prices.sort()
        return round(same_efficacy_prices[len(same_efficacy_prices) // 2], 2)

    all_prices = sorted(_safe_positive_float(item.get("current_price")) for item in existing_skus if _safe_positive_float(item.get("current_price")) > 0)
    if all_prices:
        return round(all_prices[len(all_prices) // 2], 2)
    return 19.9


def _normalize_title_key(title: str, keyword: str) -> str:
    text = normalize_text(title).lower()
    if keyword:
        text = text.replace(keyword.lower(), "")
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def build_crawl_observations(items: list[dict[str, Any]], *, keyword: str = "牙膏") -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for raw in items:
        title = normalize_text(raw.get("title"))
        if not title:
            continue
        normalized_key = _normalize_title_key(title, keyword)
        if not normalized_key:
            continue
        entry = grouped.setdefault(
            normalized_key,
            {
                "normalized_key": normalized_key,
                "canonical_title": title[:160],
                "platform_hits": {},
                "keyword_hits": {},
            },
        )
        platform = normalize_text(raw.get("platform")) or "unknown"
        current_keyword = normalize_text(raw.get("keyword") or keyword) or keyword
        entry["platform_hits"][platform] = entry["platform_hits"].get(platform, 0) + 1
        entry["keyword_hits"][current_keyword] = entry["keyword_hits"].get(current_keyword, 0) + 1
    return list(grouped.values())


def parse_browser_capture_text(
    capture_text: str,
    *,
    platform: str = "",
    keyword: str = "牙膏",
    source_url: str = "",
) -> list[dict[str, Any]]:
    text = normalize_text(capture_text)
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("浏览器辅助采集内容不是有效 JSON，请重新复制脚本输出结果。") from exc

    if isinstance(payload, dict):
        raw_items = payload.get("items") or []
        platform = normalize_text(payload.get("platform")) or platform
        source_url = normalize_text(payload.get("source_url")) or source_url
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raise ValueError("浏览器辅助采集内容格式不支持，请重新复制。")
    return _coerce_capture_items(raw_items, platform=platform, keyword=keyword, source_url=source_url)


def parse_pasted_capture_text(
    raw_text: str,
    *,
    platform: str = "",
    keyword: str = "牙膏",
) -> list[dict[str, Any]]:
    text = normalize_text(raw_text)
    if not text:
        return []

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        if isinstance(payload, dict):
            return _coerce_capture_items(payload.get("items") or [], platform=normalize_text(payload.get("platform")) or platform, keyword=keyword, source_url=normalize_text(payload.get("source_url")))
        if isinstance(payload, list):
            return _coerce_capture_items(payload, platform=platform, keyword=keyword)

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) == 1:
        blocks = [line.strip("•- \t") for line in text.splitlines() if line.strip()]

    results: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        title_line = ""
        price = 0.0
        sales_text = ""
        url = ""
        for line in [item.strip() for item in block.splitlines() if item.strip()]:
            if not url:
                url_match = re.search(r"https?://\S+", line)
                if url_match:
                    url = url_match.group(0)
            if not title_line and ("牙膏" in line or any(brand in line for brand in KNOWN_BRANDS)):
                title_line = re.sub(r"https?://\S+", "", line).strip(" |，,")
            if price <= 0 and ("¥" in line or "￥" in line or "元" in line):
                price = _extract_price_from_text(line) or _safe_positive_float(line)
            if not sales_text:
                sales_match = re.search(r"(?:月销|销量|已售|售出|付款|热度)[^\n]{0,18}", line)
                if sales_match:
                    sales_text = sales_match.group(0).strip("：: ")
        merged_text = normalize_text(block)
        if not title_line:
            title_line = re.sub(r"https?://\S+", "", merged_text)
            title_line = re.sub(r"(?:¥|￥)\s*\d+(?:\.\d+)?", "", title_line)
            title_line = re.sub(r"\d+(?:\.\d+)?\s*元", "", title_line).strip(" |，,")
        if "牙膏" not in title_line:
            continue
        if price <= 0:
            price = _extract_price_from_text(merged_text)
        results.append(
            {
                "platform": normalize_text(platform) or "other",
                "title": title_line[:180],
                "url": url,
                "price": round(price, 2) if price > 0 else 0.0,
                "sales_text": sales_text,
                "popularity": _popularity_from_text(sales_text),
                "rank": index + 1,
                "keyword": keyword,
            }
        )
    return results


def _coerce_capture_items(
    raw_items: list[Any],
    *,
    platform: str,
    keyword: str,
    source_url: str = "",
) -> list[dict[str, Any]]:
    normalized_platform = normalize_text(platform).lower()
    if normalized_platform not in PLATFORM_LABELS:
        normalized_platform = _platform_key_from_url(source_url) or normalized_platform or "other"
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        title = normalize_text(raw.get("title") or raw.get("name"))
        if "牙膏" not in title:
            continue
        url = normalize_text(raw.get("url") or raw.get("link") or source_url)
        sales_text = normalize_text(raw.get("sales_text") or raw.get("sales") or raw.get("heat_text"))
        price = _safe_positive_float(raw.get("price")) or _extract_price_from_text(title)
        popularity = _safe_positive_float(raw.get("popularity")) or _popularity_from_text(sales_text)
        item_platform = normalize_text(raw.get("platform")).lower() or normalized_platform or _platform_key_from_url(url) or "other"
        results.append(
            {
                "platform": item_platform,
                "title": title[:180],
                "url": url,
                "price": round(price, 2) if price > 0 else 0.0,
                "sales_text": sales_text,
                "popularity": popularity,
                "rank": int(raw.get("rank") or (index + 1)),
                "keyword": normalize_text(raw.get("keyword")) or keyword,
            }
        )
    return results


def _platform_key_from_url(url: str) -> str:
    normalized = normalize_text(url).lower()
    if "jd.com" in normalized:
        return "jd"
    if "tmall.com" in normalized:
        return "tmall"
    if "taobao.com" in normalized:
        return "taobao"
    if "xiaohongshu.com" in normalized:
        return "xiaohongshu"
    if "douyin.com" in normalized:
        return "douyin"
    return ""


def _dedupe_raw_items(items: list[RawHotItem]) -> list[RawHotItem]:
    deduped: dict[str, RawHotItem] = {}
    for item in items:
        key = f"{item.platform}:{_normalize_title_key(item.title, '牙膏')}"
        if key.endswith(":"):
            continue
        existing = deduped.get(key)
        if not existing:
            deduped[key] = item
        elif item.popularity > existing.popularity or (item.price > 0 and existing.price <= 0):
            deduped[key] = item
    return sorted(deduped.values(), key=lambda item: (item.rank, -item.popularity))


def _build_market_query(sku: dict[str, Any]) -> str:
    return _build_market_queries(sku)[0]


def _build_market_queries(sku: dict[str, Any]) -> list[str]:
    brand = normalize_text(sku.get("brand"))
    name = normalize_text(sku.get("product_name"))
    efficacy = normalize_efficacy(sku.get("efficacy_tags"))
    spec = normalize_text(sku.get("spec_text"))
    cleaned_name = re.sub(r"\d+(?:\.\d+)?\s*(?:g|kg|克|ml|l|毫升)(?:\s*[x×*]\s*\d+(?:\.\d+)?)?", "", name, flags=re.I)
    if brand:
        cleaned_name = cleaned_name.replace(brand, " ")
    cleaned_name = cleaned_name.replace("牙膏", " ")
    cleaned_name = re.sub(r"[【】（）()\[\]·/_\-]+", " ", cleaned_name)
    cleaned_name = re.sub(r"\s+", " ", cleaned_name).strip()

    query_candidates = [
        [brand, name, spec],
        [brand, cleaned_name, spec, "牙膏"],
        [brand, cleaned_name, "牙膏"],
        [brand, efficacy if efficacy != "其他" else "", spec, "牙膏"],
        [brand, spec, "牙膏"],
        [brand, efficacy if efficacy != "其他" else "", "牙膏"],
        [brand, "牙膏"],
    ]
    queries: list[str] = []
    for parts in query_candidates:
        query = " ".join(dict.fromkeys(part for part in parts if normalize_text(part))).strip()
        if query and query not in queries:
            queries.append(query)
    return queries or ["牙膏"]


def _extract_market_tokens(text: str) -> list[str]:
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", cleaned):
        normalized_token = token.lower() if token.isascii() else token
        tokens.append(normalized_token)
        if not normalized_token.isascii() and len(normalized_token) >= 4:
            tokens.extend(normalized_token[index:index + 2] for index in range(len(normalized_token) - 1))
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in {"牙膏", "薄荷", "清爽"} and len(token) <= 2:
            continue
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


def _normalized_similarity_text(text: str) -> str:
    normalized = normalize_text(text).lower()
    normalized = re.sub(r"\d+(?:\.\d+)?\s*(?:g|kg|克|ml|l|毫升)(?:\s*[x×*]\s*\d+(?:\.\d+)?)?", "", normalized, flags=re.I)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)


def _score_market_item(item: RawHotItem, sku: dict[str, Any]) -> dict[str, Any]:
    brand = normalize_text(sku.get("brand")).lower()
    product_name = normalize_text(sku.get("product_name"))
    efficacy = normalize_efficacy(sku.get("efficacy_tags"))
    spec_amount, spec_unit = parse_spec(normalize_text(sku.get("spec_text")))
    title = normalize_text(item.title)
    title_lower = title.lower()
    brand_match = bool(brand and brand in title_lower)
    efficacy_match = efficacy == "其他" or efficacy.lower() in title_lower or any(
        word.lower() in title_lower for word in EFFICACY_KEYWORDS.get(efficacy, [])
    )
    sku_tokens = _extract_market_tokens(product_name.replace(normalize_text(sku.get("brand")), " "))
    matched_tokens = [token for token in sku_tokens if token and token in title_lower]
    similarity = SequenceMatcher(None, _normalized_similarity_text(title), _normalized_similarity_text(product_name)).ratio()

    spec_score = 0.0
    spec_match = False
    if spec_amount and spec_unit:
        item_amount, item_unit = parse_spec(_extract_spec(item.title))
        if item_amount and item_unit == spec_unit:
            ratio = min(spec_amount, item_amount) / max(spec_amount, item_amount)
            if ratio >= 0.9:
                spec_score = 18
                spec_match = True
            elif ratio >= 0.75:
                spec_score = 12
                spec_match = True
            elif ratio >= 0.6:
                spec_score = 6
            else:
                spec_score = -8

    score = 0.0
    score += 34 if brand_match else 0
    score += min(len(matched_tokens), 3) * 8
    score += 12 if efficacy_match else 0
    score += spec_score
    score += similarity * 28
    score += 6 if "牙膏" in title else 0

    strict = brand_match and score >= 58 and (spec_match or efficacy_match or len(matched_tokens) >= 1)
    medium = brand_match and score >= 44
    loose = score >= 32 and (brand_match or len(matched_tokens) >= 2 or similarity >= 0.56)
    return {
        "item": item,
        "score": round(score, 2),
        "strict": strict,
        "medium": medium,
        "loose": loose,
    }


def _dedupe_market_items(items: list[RawHotItem]) -> list[RawHotItem]:
    deduped: dict[str, RawHotItem] = {}
    for item in items:
        key = f"{_normalize_title_key(item.title, '牙膏')}|{round(item.price, 2)}"
        existing = deduped.get(key)
        if not existing or item.rank < existing.rank:
            deduped[key] = item
    return list(deduped.values())


def _quality_rank(label: str) -> int:
    order = {
        "none": 0,
        "approximate": 1,
        "exact": 2,
    }
    return order.get(normalize_text(label), 0)


def _filter_relevant_market_items(items: list[RawHotItem], sku: dict[str, Any], *, target_count: int = 6) -> dict[str, Any]:
    scored = [_score_market_item(item, sku) for item in items]
    scored.sort(key=lambda row: (-row["score"], row["item"].rank, -row["item"].popularity))

    selected: list[RawHotItem] = []
    seen: set[str] = set()

    def add_rows(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            key = f"{_normalize_title_key(row['item'].title, '牙膏')}|{round(row['item'].price, 2)}"
            if key in seen:
                continue
            seen.add(key)
            selected.append(row["item"])
            if len(selected) >= target_count:
                return

    add_rows([row for row in scored if row["strict"]])
    if len(selected) < target_count:
        add_rows([row for row in scored if row["medium"]])
    if len(selected) < target_count:
        add_rows([row for row in scored if row["loose"]])
    if not selected:
        add_rows(scored[: min(len(scored), max(4, target_count // 2))])
    selected_scored = [row for row in scored if row["item"] in selected]
    quality = "none"
    if any(row["strict"] for row in selected_scored):
        quality = "exact"
    elif selected:
        quality = "approximate"
    return {
        "items": selected[:target_count],
        "quality": quality,
    }


def _extract_prices_from_items(items: list[RawHotItem]) -> list[float]:
    prices: list[float] = []
    for item in items:
        price = item.price if item.price > 0 else _extract_price_from_text(item.title)
        if price > 0:
            prices.append(round(price, 2))
    return prices


def _summarize_price_samples(prices: list[float]) -> tuple[float, float, float, int, int]:
    cleaned = sorted(price for price in prices if price > 0)
    if not cleaned:
        return 0.0, 0.0, 0.0, 0, 0
    trimmed = cleaned[:]
    if len(trimmed) >= 5:
        trim_count = max(1, int(len(trimmed) * 0.1))
        trimmed = trimmed[trim_count:-trim_count] or cleaned
    avg_price = round(sum(trimmed) / len(trimmed), 2)
    min_price = round(cleaned[0], 2)
    max_price = round(cleaned[-1], 2)
    ratio = max_price / max(min_price, 0.01)
    mean_price = sum(cleaned) / len(cleaned)
    variance = sum((price - mean_price) ** 2 for price in cleaned) / len(cleaned)
    cv = math.sqrt(variance) / mean_price if mean_price else 0
    disorder_flag = 1 if ratio >= 1.5 or cv >= 0.22 else 0
    return avg_price, min_price, max_price, len(cleaned), disorder_flag


def _aggregate_platform_heat(items: list[RawHotItem]) -> float:
    if not items:
        return 0.0
    scores = [_compute_heat_score(item.sales_text, item.popularity, item.rank) for item in items[:5]]
    return round(sum(scores) / len(scores), 1)


def _combine_platform_heat(platform_scores: list[float]) -> float:
    if not platform_scores:
        return 0.0
    best = max(platform_scores)
    average = sum(platform_scores) / len(platform_scores)
    bonus = min(8.0, max(0, len(platform_scores) - 1) * 2.0)
    return min(100.0, best * 0.6 + average * 0.4 + bonus)
