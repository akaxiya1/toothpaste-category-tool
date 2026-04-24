from __future__ import annotations

import cgi
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from backend.config import BACKUP_DIR, DB_PATH, HOST, PORT, SAMPLES_DIR, STATIC_DIR, ensure_directories
from backend.constants import CANDIDATE_IMPORT_FIELDS, EFFICACY_OPTIONS, PLATFORMS, PROMO_TYPES, ROLES, SKU_IMPORT_FIELDS, TARGET_GROUPS
from backend.crawlers import (
    DEFAULT_HOT_KEYWORDS,
    DEFAULT_PLATFORMS,
    PLATFORM_LABELS,
    build_candidates_from_crawled_items,
    build_crawl_observations,
    crawl_hot_products,
    parse_browser_capture_text,
    parse_pasted_capture_text,
    refresh_market_snapshots,
)
from backend.importer import commit_import, persist_import_copy, preview_import, save_upload
from backend.logic import (
    MARKET_SNAPSHOT_TTL_HOURS,
    auto_select_candidates,
    build_dashboard,
    enrich_candidate,
    enrich_sku,
    is_snapshot_fresh,
    normalize_text,
    recommend_brand_missing_hits,
    recommend_existing_skus,
    simulate_batch_pricing,
)
from backend.storage import Database


class ToolApplication:
    def __init__(self) -> None:
        ensure_directories()
        self.db = Database(DB_PATH)
        self.import_sessions: dict[str, dict[str, Any]] = {}

    def _merge_market_context(self, raw_skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
        diagnostics_by_sku_id = {
            int(item["sku_id"]): item
            for item in self.db.list_market_diagnostics()
            if str(item.get("sku_id", "")).isdigit()
        }
        manual_by_sku_id = {
            int(item["sku_id"]): item
            for item in self.db.list_manual_market_overrides()
            if str(item.get("sku_id", "")).isdigit()
        }

        merged_rows: list[dict[str, Any]] = []
        for raw in raw_skus:
            sku_id = int(raw.get("id") or 0)
            merged = {**raw}
            diagnostic = diagnostics_by_sku_id.get(sku_id)
            if diagnostic:
                merged.update(
                    {
                        "market_sample_status": diagnostic.get("market_sample_status", ""),
                        "market_source_mode": diagnostic.get("market_source_mode", ""),
                        "market_diagnostic_summary": diagnostic.get("diagnostic_summary", ""),
                        "market_query_logs_json": diagnostic.get("query_logs_json", "[]"),
                        "market_blocked_platforms_json": diagnostic.get("blocked_platforms_json", "[]"),
                        "market_fallback_note": diagnostic.get("fallback_note", ""),
                        "market_matched_titles_json": diagnostic.get("matched_titles_json", "[]"),
                    }
                )

            manual_override = manual_by_sku_id.get(sku_id)
            if manual_override:
                prices = [float(value) for value in json.loads(manual_override.get("sample_prices_json") or "[]") if float(value) > 0]
                prices.sort()
                if prices:
                    ratio = prices[-1] / max(prices[0], 0.01)
                    merged.update(
                        {
                            "taobao_avg_price": round(sum(prices) / len(prices), 2),
                            "taobao_min_price": round(prices[0], 2),
                            "taobao_max_price": round(prices[-1], 2),
                            "taobao_sample_count": len(prices),
                            "price_disorder_flag": 1 if ratio >= 1.5 else 0,
                            "market_snapshot_at": manual_override.get("updated_at", ""),
                            "market_sample_status": "人工补样本",
                            "market_source_mode": f"人工补样本/{manual_override.get('source_platform', '淘宝')}",
                            "market_diagnostic_summary": manual_override.get("note") or "已使用人工补样本作为当前市场参考。",
                            "manual_sample_prices_json": manual_override.get("sample_prices_json", "[]"),
                            "manual_sample_urls_json": manual_override.get("source_urls_json", "[]"),
                            "manual_sample_source_platform": manual_override.get("source_platform", "淘宝"),
                            "manual_sample_note": manual_override.get("note", ""),
                        }
                    )
            merged_rows.append(merged)
        return merged_rows

    def _structured_skus(self, raw_skus: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        source_rows = raw_skus if raw_skus is not None else self.db.list_skus()
        return recommend_existing_skus(self._merge_market_context(source_rows))

    def _enriched_candidates(self, structured_skus: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        current_skus = structured_skus if structured_skus is not None else self._structured_skus()
        return [{**item, **enrich_candidate(item, current_skus)} for item in self.db.list_candidates()]

    def _brand_hot_keywords(self, brand: str) -> list[str]:
        normalized_brand = normalize_text(brand)
        if not normalized_brand:
            return ["牙膏"]
        return [
            f"{normalized_brand} 牙膏",
            f"{normalized_brand} 美白牙膏",
            f"{normalized_brand} 抗敏牙膏",
            f"{normalized_brand} 儿童牙膏",
        ]

    def _brand_candidates_are_fresh(self, brand: str, candidates: list[dict[str, Any]]) -> bool:
        brand_key = normalize_text(brand).lower()
        if not brand_key:
            return False
        same_brand = [
            item
            for item in candidates
            if normalize_text(item.get("brand")).lower() == brand_key
        ]
        if len(same_brand) < 3:
            return False
        return any(is_snapshot_fresh(item.get("updated_at"), MARKET_SNAPSHOT_TTL_HOURS) for item in same_brand)

    def _manual_override_map(self) -> dict[int, dict[str, Any]]:
        return {
            int(item["sku_id"]): item
            for item in self.db.list_manual_market_overrides()
            if str(item.get("sku_id", "")).isdigit()
        }

    def meta(self) -> dict[str, Any]:
        return {
            "db_path": str(DB_PATH),
            "host": HOST,
            "port": PORT,
            "price_bands": ["<=9.9", "10-14.9", "15-19.9", "20-29.9", "30-39.9", ">=40"],
            "roles": ROLES,
            "efficacy_options": EFFICACY_OPTIONS,
            "platforms": PLATFORMS,
            "target_groups": TARGET_GROUPS,
            "promo_types": PROMO_TYPES,
            "import_fields": {
                "sku": SKU_IMPORT_FIELDS,
                "candidate": CANDIDATE_IMPORT_FIELDS,
            },
            "sample_files": [
                {"label": "现有牙膏SKU导入模板.csv", "path": "/samples/现有牙膏SKU导入模板.csv"},
                {"label": "候选牙膏新品模板.csv", "path": "/samples/候选牙膏新品模板.csv"},
            ],
            "crawler_platforms": [{"key": key, "label": label} for key, label in PLATFORM_LABELS.items()],
            "crawler_default_platforms": DEFAULT_PLATFORMS,
            "crawler_cookie_notice": "Cookie 不是必填，建议先匿名抓取；只有遇到公开页面拦截时，再临时补对应平台 Cookie 作为兜底。即使补了 Cookie，也只会走慢速串行请求，不写入数据库，遇到验证会直接停止该平台抓取。",
            "crawler_browser_helper_notice": "浏览器辅助采集不依赖 Cookie。建议先打开目标平台搜索页或榜单页，再运行辅助脚本，把脚本输出的 JSON 粘贴回这里导入。",
            "backup_dir": str(BACKUP_DIR),
            "market_snapshot_ttl_hours": MARKET_SNAPSHOT_TTL_HOURS,
            "crawler_default_keywords": DEFAULT_HOT_KEYWORDS,
            "pricing_simulation_strategies": [
                {"key": "adjust_by_amount", "label": "每个SKU统一加减金额"},
                {"key": "to_taobao_avg", "label": "统一对齐淘宝均价"},
                {"key": "to_system_suggested", "label": "统一对齐系统建议价"},
            ],
        }

    def state(self) -> dict[str, Any]:
        structured_skus = self._structured_skus()
        candidates = self._enriched_candidates(structured_skus)
        dashboard = build_dashboard(structured_skus, candidates)
        candidate_recommendations = sorted(
            candidates,
            key=lambda item: (-item.get("recommendation_score", 0), item.get("brand", ""), item.get("product_name", "")),
        )
        auto_selection = auto_select_candidates(candidate_recommendations, structured_skus)
        return {
            "skus": structured_skus,
            "candidates": candidates,
            "dashboard": dashboard,
            "market_tools": {
                "diagnostics": [
                    item for item in structured_skus
                    if item.get("market_sample_status") in {"无结果", "被拦截", "样本不足", "近似样本", "跨平台替代", "人工补样本"}
                ],
                "manual_overrides": list(self._manual_override_map().values()),
            },
            "recommendations": {
                "existing": structured_skus,
                "candidate": candidate_recommendations,
                "auto_selection": auto_selection,
            },
        }

    def brand_recommendations(self, payload: dict[str, Any]) -> dict[str, Any]:
        brand = normalize_text(payload.get("brand"))
        if not brand:
            raise ValueError("请选择要分析的品牌。")

        force_refresh = bool(payload.get("force_refresh"))
        cookies = payload.get("cookies") if isinstance(payload.get("cookies"), dict) else {}
        structured_skus = self._structured_skus()
        brand_key = brand.lower()
        current_brand_skus = [
            item
            for item in structured_skus
            if normalize_text(item.get("brand")).lower() == brand_key
        ]

        cached_candidates = [
            item
            for item in self.db.list_candidates()
            if normalize_text(item.get("brand")).lower() == brand_key
        ]
        used_cached_candidates = bool(cached_candidates)
        cache_ready = self._brand_candidates_are_fresh(brand, cached_candidates)
        should_crawl = force_refresh or not cache_ready

        auto_crawl_triggered = False
        crawl_status = "cached_only"
        fallback_mode = "none"
        crawl_errors: dict[str, Any] = {}
        platform_reports: list[dict[str, Any]] = []
        keywords_used: list[str] = []
        fallback_message = ""

        if should_crawl:
            auto_crawl_triggered = True
            keywords = self._brand_hot_keywords(brand)
            keywords_used = keywords
            try:
                crawl_result = crawl_hot_products(
                    keyword=keywords[0],
                    keywords=keywords,
                    platforms=DEFAULT_PLATFORMS,
                    limit_per_platform=12,
                    cookies=cookies,
                )
                raw_items = crawl_result.get("items", [])
                platform_reports = crawl_result.get("platform_reports", [])
                crawl_errors = {
                    PLATFORM_LABELS.get(platform_key, platform_key): message
                    for platform_key, message in (crawl_result.get("errors") or {}).items()
                }
                keywords_used = crawl_result.get("keywords_used", keywords)
                if raw_items:
                    self.db.record_crawl_observations(build_crawl_observations(raw_items, keyword=keywords[0]))
                    crawl_history = {
                        item["normalized_key"]: item
                        for item in self.db.list_crawl_observations()
                        if normalize_text(item.get("normalized_key"))
                    }
                    candidate_payloads = build_candidates_from_crawled_items(
                        raw_items,
                        structured_skus,
                        keyword=keywords[0],
                        crawl_history=crawl_history,
                    )
                    brand_payloads = [
                        item
                        for item in candidate_payloads
                        if normalize_text(item.get("brand")).lower() == brand_key
                    ]
                    if brand_payloads:
                        self._upsert_candidate_payloads(brand_payloads)
                        crawl_status = "crawl_success"
                    else:
                        crawl_status = "crawl_empty"
                else:
                    crawl_status = "crawl_empty"
            except Exception as exc:
                crawl_status = "crawl_failed"
                crawl_errors = {"品牌补抓": str(exc)}

        candidates = self._enriched_candidates(structured_skus)
        missing_brand_hits = recommend_brand_missing_hits(
            brand=brand,
            current_brand_skus=current_brand_skus,
            all_skus=structured_skus,
            candidates=candidates,
            limit=3,
        )

        if crawl_status == "crawl_failed":
            fallback_mode = "local_after_crawl_failure" if used_cached_candidates else "no_candidates"
            fallback_message = (
                "品牌自动补抓失败，当前已回退到本地候选池。"
                if used_cached_candidates
                else "这个品牌暂时没有抓到可用候选，建议改用浏览器辅助采集或批量粘贴采集。"
            )
        elif crawl_status == "crawl_empty":
            fallback_mode = "cached_candidates" if used_cached_candidates else "no_candidates"
            fallback_message = (
                "自动补抓没有拿到新的同品牌爆款，当前先沿用本地候选池。"
                if used_cached_candidates
                else "自动补抓没有拿到新的同品牌爆款，建议改用浏览器辅助采集或批量粘贴采集。"
            )
        elif used_cached_candidates:
            fallback_mode = "cached_candidates"

        if missing_brand_hits and not fallback_message and used_cached_candidates and not auto_crawl_triggered:
            fallback_message = "当前优先使用本地候选池里的同品牌候选结果。"

        return {
            "brand": brand,
            "current_brand_skus": current_brand_skus,
            "missing_brand_hits": missing_brand_hits,
            "used_cached_candidates": used_cached_candidates,
            "auto_crawl_triggered": auto_crawl_triggered,
            "crawl_status": crawl_status,
            "fallback_mode": fallback_mode,
            "fallback_message": fallback_message,
            "platform_reports": platform_reports,
            "errors": crawl_errors,
            "keywords_used": keywords_used,
        }

    def preview_import_upload(self, kind: str, file_name: str, content: bytes) -> dict[str, Any]:
        if kind not in {"sku", "candidate"}:
            raise ValueError("kind must be sku or candidate")
        temp_path = save_upload(file_name, content)
        persisted_path = persist_import_copy(temp_path)
        preview = preview_import(kind, temp_path, self.db.list_skus())
        token = uuid4().hex
        batch_id = self.db.add_import_batch(
            kind=kind,
            file_name=file_name,
            stored_path=str(persisted_path),
            mapping=preview["mapping"],
            row_count=preview["row_count"],
            status="previewed",
        )
        self.import_sessions[token] = {
            "kind": kind,
            "temp_path": temp_path,
            "persisted_path": persisted_path,
            "batch_id": batch_id,
            "file_name": file_name,
        }
        return {"token": token, "kind": kind, **preview}

    def commit_import_upload(self, token: str, mapping: dict[str, str]) -> dict[str, Any]:
        session = self.import_sessions.get(token)
        if not session:
            raise ValueError("导入预览已失效，请重新选择文件。")
        kind = session["kind"]
        items = commit_import(kind, session["temp_path"], mapping, self.db.list_skus())
        if kind == "sku":
            result = self.db.upsert_skus(items)
        else:
            count = 0
            for item in items:
                self.db.save_candidate(item)
                count += 1
            result = {"inserted": count, "updated": 0}
        self.db.update_import_status(session["batch_id"], "imported")
        self.import_sessions.pop(token, None)
        return {"kind": kind, "count": len(items), **result}

    def save_candidate(self, payload: dict[str, Any], candidate_id: int | None = None) -> dict[str, Any]:
        structured_skus = self._structured_skus()
        item = enrich_candidate(payload, structured_skus)
        current_id = self.db.save_candidate(item, candidate_id=candidate_id)
        stored = self.db.get_candidate(current_id)
        if not stored:
            raise ValueError("候选商品保存失败。")
        return {**stored, **enrich_candidate(stored, structured_skus)}

    def delete_candidate(self, candidate_id: int) -> None:
        self.db.delete_candidate(candidate_id)

    def candidate_comparison(self, candidate_id: int) -> dict[str, Any]:
        candidate = self.db.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("未找到候选商品。")
        structured_skus = self._structured_skus()
        enriched = {**candidate, **enrich_candidate(candidate, structured_skus)}
        return {"candidate": enriched, "comparisons": enriched["comparison_rows"]}

    def create_backup(self) -> dict[str, str]:
        return self.db.create_backup()

    def crawl_hot_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        keyword = normalize_text(payload.get("keyword")) or "牙膏"
        keywords = payload.get("keywords") if isinstance(payload.get("keywords"), list) else None
        platforms = payload.get("platforms") or DEFAULT_PLATFORMS
        per_platform_limit = int(payload.get("limit_per_platform") or 20)
        per_platform_limit = max(5, min(per_platform_limit, 50))
        cookies = payload.get("cookies") if isinstance(payload.get("cookies"), dict) else {}

        crawl_result = crawl_hot_products(
            keyword=keyword,
            keywords=keywords,
            platforms=platforms,
            limit_per_platform=per_platform_limit,
            cookies=cookies,
        )
        raw_items = crawl_result["items"]
        self.db.record_crawl_observations(build_crawl_observations(raw_items, keyword=keyword))
        crawl_history = {
            item["normalized_key"]: item
            for item in self.db.list_crawl_observations()
            if normalize_text(item.get("normalized_key"))
        }
        structured_skus = self._structured_skus()
        candidate_payloads = build_candidates_from_crawled_items(raw_items, structured_skus, keyword=keyword, crawl_history=crawl_history)
        inserted, updated = self._upsert_candidate_payloads(candidate_payloads)

        return {
            "keyword": keyword,
            "platforms": platforms or DEFAULT_PLATFORMS,
            "fetched_raw_count": len(raw_items),
            "candidate_payload_count": len(candidate_payloads),
            "inserted": inserted,
            "updated": updated,
            "errors": {
                PLATFORM_LABELS.get(platform_key, platform_key): message
                for platform_key, message in crawl_result.get("errors", {}).items()
            },
            "keywords_used": crawl_result.get("keywords_used", [keyword]),
            "platform_reports": crawl_result.get("platform_reports", []),
            "source_method": "direct_crawl",
            "preview": candidate_payloads[:12],
        }

    def import_browser_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        keyword = normalize_text(payload.get("keyword")) or "牙膏"
        raw_items = parse_browser_capture_text(
            normalize_text(payload.get("capture_text")),
            platform=normalize_text(payload.get("platform")),
            keyword=keyword,
            source_url=normalize_text(payload.get("source_url")),
        )
        if not raw_items:
            raise ValueError("浏览器辅助采集没有解析出可用商品，请重新复制脚本输出结果。")
        self.db.record_crawl_observations(build_crawl_observations(raw_items, keyword=keyword))
        crawl_history = {
            item["normalized_key"]: item
            for item in self.db.list_crawl_observations()
            if normalize_text(item.get("normalized_key"))
        }
        structured_skus = self._structured_skus()
        candidate_payloads = build_candidates_from_crawled_items(raw_items, structured_skus, keyword=keyword, crawl_history=crawl_history)
        inserted, updated = self._upsert_candidate_payloads(candidate_payloads)
        return {
            "keyword": keyword,
            "fetched_raw_count": len(raw_items),
            "candidate_payload_count": len(candidate_payloads),
            "inserted": inserted,
            "updated": updated,
            "errors": {},
            "keywords_used": sorted({normalize_text(item.get("keyword")) or keyword for item in raw_items}),
            "platform_reports": [],
            "source_method": "browser_assisted",
            "preview": candidate_payloads[:12],
        }

    def import_pasted_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        keyword = normalize_text(payload.get("keyword")) or "牙膏"
        raw_items = parse_pasted_capture_text(
            normalize_text(payload.get("raw_text")),
            platform=normalize_text(payload.get("platform")),
            keyword=keyword,
        )
        if not raw_items:
            raise ValueError("批量粘贴内容里没有解析出可用商品，请至少保留商品名和价格。")
        self.db.record_crawl_observations(build_crawl_observations(raw_items, keyword=keyword))
        crawl_history = {
            item["normalized_key"]: item
            for item in self.db.list_crawl_observations()
            if normalize_text(item.get("normalized_key"))
        }
        structured_skus = self._structured_skus()
        candidate_payloads = build_candidates_from_crawled_items(raw_items, structured_skus, keyword=keyword, crawl_history=crawl_history)
        inserted, updated = self._upsert_candidate_payloads(candidate_payloads)
        return {
            "keyword": keyword,
            "fetched_raw_count": len(raw_items),
            "candidate_payload_count": len(candidate_payloads),
            "inserted": inserted,
            "updated": updated,
            "errors": {},
            "keywords_used": [keyword],
            "platform_reports": [],
            "source_method": "bulk_paste",
            "preview": candidate_payloads[:12],
        }

    def refresh_market(self, payload: dict[str, Any]) -> dict[str, Any]:
        cookies = payload.get("cookies") if isinstance(payload.get("cookies"), dict) else {}
        force = bool(payload.get("force"))
        sku_ids = {int(item) for item in payload.get("sku_ids", []) if str(item).isdigit()} if isinstance(payload.get("sku_ids"), list) else set()
        all_skus = self._merge_market_context(self.db.list_skus())
        target_skus = [item for item in all_skus if not sku_ids or int(item.get("id") or 0) in sku_ids]
        if not target_skus:
            return {"refreshed": 0, "skipped": 0, "errors": {}, "preview": []}

        result = refresh_market_snapshots(
            skus=target_skus,
            cookies=cookies,
            force=force,
        )
        self.db.update_market_snapshots(result["snapshots"])
        self.db.upsert_market_diagnostics(result["snapshots"])
        structured_lookup = {
            item["sku_code"]: item
            for item in self._structured_skus(self.db.list_skus())
        }
        preview = []
        for snapshot in result["snapshots"][:12]:
            structured = structured_lookup.get(snapshot["sku_code"], {})
            preview.append(
                {
                    **snapshot,
                    "brand": structured.get("brand"),
                    "product_name": structured.get("product_name"),
                    "structural_role": structured.get("structural_role"),
                    "action": structured.get("action"),
                    "market_sample_status": snapshot.get("market_sample_status"),
                    "market_source_mode": snapshot.get("market_source_mode"),
                }
            )
        with_samples = sum(1 for item in result["snapshots"] if int(item.get("taobao_sample_count") or 0) > 0)
        without_samples = len(result["snapshots"]) - with_samples
        return {
            "refreshed": result["refreshed"],
            "skipped": result["skipped"],
            "errors": result["errors"],
            "preview": preview,
            "with_samples": with_samples,
            "without_samples": without_samples,
        }

    def save_manual_market_override(self, sku_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        stored = self.db.get_sku(sku_id)
        if not stored:
            raise ValueError("未找到要补样本的SKU。")
        sample_prices = [
            round(float(value), 2)
            for value in (payload.get("sample_prices") or [])
            if str(value).strip() and float(value) > 0
        ]
        if not sample_prices:
            raise ValueError("请至少填写一个有效价格样本。")
        source_urls = [normalize_text(value) for value in (payload.get("source_urls") or []) if normalize_text(value)]
        override = self.db.save_manual_market_override(
            sku_id=sku_id,
            sku_code=normalize_text(stored.get("sku_code")),
            source_platform=normalize_text(payload.get("source_platform")) or "淘宝",
            sample_prices=sample_prices,
            source_urls=source_urls,
            note=normalize_text(payload.get("note")),
        )
        updated_item = next((item for item in self._structured_skus(self.db.list_skus()) if int(item.get("id") or 0) == sku_id), None)
        return {"override": override, "item": updated_item}

    def _upsert_candidate_payloads(self, candidate_payloads: list[dict[str, Any]]) -> tuple[int, int]:
        existing_candidates = self.db.list_candidates()
        existing_map = {
            f"{normalize_text(item.get('brand')).lower()}|{normalize_text(item.get('product_name')).lower()}": item
            for item in existing_candidates
        }

        inserted = 0
        updated = 0
        for candidate in candidate_payloads:
            candidate_key = (
                f"{normalize_text(candidate.get('brand')).lower()}|"
                f"{normalize_text(candidate.get('product_name')).lower()}"
            )
            existing = existing_map.get(candidate_key)
            if existing:
                self.save_candidate(candidate, candidate_id=int(existing["id"]))
                updated += 1
            else:
                self.save_candidate(candidate)
                inserted += 1
        return inserted, updated

    def delete_manual_market_override(self, sku_id: int) -> dict[str, Any]:
        self.db.delete_manual_market_override(sku_id)
        updated_item = next((item for item in self._structured_skus(self.db.list_skus()) if int(item.get("id") or 0) == sku_id), None)
        return {"ok": True, "item": updated_item}

    def simulate_pricing(self, payload: dict[str, Any]) -> dict[str, Any]:
        return simulate_batch_pricing(
            self._merge_market_context(self.db.list_skus()),
            brand=normalize_text(payload.get("brand")),
            structural_role=normalize_text(payload.get("structural_role")),
            price_band=normalize_text(payload.get("price_band")),
            strategy=normalize_text(payload.get("strategy")) or "adjust_by_amount",
            amount=float(payload.get("amount") or 0),
        )

    def preview_sku_price(self, sku_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        stored = self.db.get_sku(sku_id)
        if not stored:
            raise ValueError("未找到要预览的SKU。")
        price = float(payload.get("current_price") or 0)
        raw_skus = self._merge_market_context(self.db.list_skus())
        merged_stored = next((item for item in raw_skus if int(item.get("id") or 0) == sku_id), None) or stored
        preview_raw = {**merged_stored, **stored, "current_price": price}
        normalized_preview = enrich_sku(preview_raw)
        merged_raw_skus = [preview_raw if int(item.get("id") or 0) == sku_id else item for item in raw_skus]
        structured = self._structured_skus(merged_raw_skus)
        preview_item = next((item for item in structured if int(item.get("id") or 0) == sku_id), None)
        if not preview_item:
            raise ValueError("无法生成预览结果。")
        original_structured = next((item for item in self._structured_skus(raw_skus) if int(item.get("id") or 0) == sku_id), None)
        return {
            "item": preview_item,
            "changes": {
                "price_band_changed": bool(original_structured and original_structured.get("price_band") != preview_item.get("price_band")),
                "role_changed": bool(original_structured and original_structured.get("structural_role") != preview_item.get("structural_role")),
                "action_changed": bool(original_structured and original_structured.get("action") != preview_item.get("action")),
                "preview_margin": normalized_preview.get("gross_margin", 0),
            },
        }

    def update_sku(self, sku_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        stored = self.db.get_sku(sku_id)
        if not stored:
            raise ValueError("未找到要更新的SKU。")
        updated_raw = {
            **stored,
            **{key: value for key, value in payload.items() if key in {"current_price", "purchase_price", "notes"}},
        }
        normalized = enrich_sku(updated_raw)
        saved = self.db.update_sku(sku_id, normalized)
        if not saved:
            raise ValueError("SKU 更新失败。")
        structured = self._structured_skus(self.db.list_skus())
        updated_item = next((item for item in structured if int(item.get("id") or 0) == sku_id), None)
        if not updated_item:
            raise ValueError("SKU 更新后未找到结果。")
        return updated_item


APP = ToolApplication()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "ToothpasteTool/2.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                self._send_json({"ok": True})
                return
            if path == "/api/meta":
                self._send_json(APP.meta())
                return
            if path == "/api/state":
                self._send_json(APP.state())
                return
            if path.startswith("/api/candidates/") and path.endswith("/comparison"):
                candidate_id = int(path.split("/")[3])
                self._send_json(APP.candidate_comparison(candidate_id))
                return
            self._serve_static(path)
        except Exception as exc:  # pragma: no cover - HTTP boundary
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/import/preview":
                kind, file_name, content = self._parse_multipart_upload()
                self._send_json(APP.preview_import_upload(kind, file_name, content))
                return
            if parsed.path == "/api/import/commit":
                payload = self._read_json()
                token = str(payload.get("token", ""))
                mapping = payload.get("mapping", {})
                self._send_json(APP.commit_import_upload(token, mapping))
                return
            if parsed.path == "/api/candidates":
                payload = self._read_json()
                self._send_json(APP.save_candidate(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/crawl/hot-products":
                payload = self._read_json()
                self._send_json(APP.crawl_hot_candidates(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/crawl/browser-capture":
                payload = self._read_json()
                self._send_json(APP.import_browser_capture(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/crawl/paste-candidates":
                payload = self._read_json()
                self._send_json(APP.import_pasted_candidates(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/market/refresh":
                payload = self._read_json()
                self._send_json(APP.refresh_market(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/dashboard/brand-recommendations":
                payload = self._read_json()
                self._send_json(APP.brand_recommendations(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path.startswith("/api/skus/") and parsed.path.endswith("/manual-market-override"):
                sku_id = int(parsed.path.split("/")[3])
                payload = self._read_json()
                self._send_json(APP.save_manual_market_override(sku_id, payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/pricing/simulate":
                payload = self._read_json()
                self._send_json(APP.simulate_pricing(payload), status=HTTPStatus.CREATED)
                return
            if parsed.path.startswith("/api/skus/") and parsed.path.endswith("/price-preview"):
                sku_id = int(parsed.path.split("/")[3])
                payload = self._read_json()
                self._send_json(APP.preview_sku_price(sku_id, payload), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/backups":
                self._send_json(APP.create_backup(), status=HTTPStatus.CREATED)
                return
            self._send_json({"error": "Unknown endpoint"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover - HTTP boundary
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/candidates/"):
                candidate_id = int(parsed.path.split("/")[3])
                payload = self._read_json()
                self._send_json(APP.save_candidate(payload, candidate_id=candidate_id))
                return
            if parsed.path.startswith("/api/skus/"):
                sku_id = int(parsed.path.split("/")[3])
                payload = self._read_json()
                self._send_json(APP.update_sku(sku_id, payload))
                return
            self._send_json({"error": "Unknown endpoint"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover - HTTP boundary
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/candidates/"):
                candidate_id = int(parsed.path.split("/")[3])
                APP.delete_candidate(candidate_id)
                self._send_json({"ok": True})
                return
            if parsed.path.startswith("/api/skus/") and parsed.path.endswith("/manual-market-override"):
                sku_id = int(parsed.path.split("/")[3])
                self._send_json(APP.delete_manual_market_override(sku_id))
                return
            self._send_json({"error": "Unknown endpoint"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover - HTTP boundary
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _parse_multipart_upload(self) -> tuple[str, str, bytes]:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        kind = form.getvalue("kind", "sku")
        file_item = form["file"]
        if not getattr(file_item, "filename", ""):
            raise ValueError("请选择要导入的 Excel 或 CSV 文件。")
        content = file_item.file.read()
        return str(kind), str(file_item.filename), content

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        if path in {"/", ""}:
            target = STATIC_DIR / "index.html"
            root = STATIC_DIR
        elif path.startswith("/static/"):
            root = STATIC_DIR
            target = root / path.removeprefix("/static/")
        elif path.startswith("/samples/"):
            root = SAMPLES_DIR
            target = root / path.removeprefix("/samples/")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            resolved = target.resolve(strict=True)
            root_resolved = root.resolve(strict=True)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if root_resolved not in resolved.parents and resolved != root_resolved:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if not resolved.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(resolved.name)
        raw = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        if os.getenv("TOOTHPASTE_TOOL_QUIET", "0") == "1":
            return
        super().log_message(format, *args)


def run() -> None:
    ensure_directories()
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    print(f"Toothpaste tool running at http://{HOST}:{PORT}")
    print(f"SQLite database: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    run()
