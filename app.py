from __future__ import annotations

import cgi
import json
import mimetypes
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from backend.config import BACKUP_DIR, DB_PATH, HOST, PORT, SAMPLES_DIR, STATIC_DIR, ensure_directories
from backend.constants import CANDIDATE_IMPORT_FIELDS, EFFICACY_OPTIONS, PLATFORMS, PROMO_TYPES, ROLES, SKU_IMPORT_FIELDS, TARGET_GROUPS
from backend.crawlers import (
    DEFAULT_HOT_KEYWORDS,
    DEFAULT_PLATFORMS,
    PLATFORM_LABELS,
    build_manual_source_snapshot,
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
    now_iso,
    parse_float,
    parse_int,
    recommend_brand_missing_hits,
    recommend_existing_skus,
    simulate_batch_pricing,
)
from backend.storage import Database


LAUNCH_STATUS_OPTIONS = [
    {"key": "planned", "label": "计划中"},
    {"key": "launched", "label": "已上新"},
    {"key": "observing", "label": "观察中"},
    {"key": "replenished", "label": "已补货"},
    {"key": "delisted", "label": "已下架"},
]

REVIEW_DECISION_OPTIONS = [
    {"key": "observe", "label": "继续观察"},
    {"key": "replenish", "label": "继续补货"},
    {"key": "reprice", "label": "调整售价"},
    {"key": "delist", "label": "建议下架"},
]


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

    def _launch_status_label(self, status: str) -> str:
        for item in LAUNCH_STATUS_OPTIONS:
            if item["key"] == status:
                return item["label"]
        return "计划中"

    def _review_decision_label(self, decision: str) -> str:
        for item in REVIEW_DECISION_OPTIONS:
            if item["key"] == decision:
                return item["label"]
        return ""

    def _suggest_first_order_qty(self, candidate: dict[str, Any]) -> int:
        role = normalize_text(candidate.get("proposed_role"))
        heat = parse_float(candidate.get("heat_score"))
        quantity = 10
        if "引流" in role:
            quantity = 24
        elif "常规" in role or "主销" in role:
            quantity = 16
        elif "利润" in role:
            quantity = 10
        elif "儿童" in role or "补位" in role:
            quantity = 8
        if heat >= 85:
            quantity += 4
        elif heat < 50:
            quantity -= 2
        if candidate.get("replacement_targets"):
            quantity += 2
        return max(4, int(round(quantity / 2)) * 2)

    def _suggest_review_cycle_days(self, candidate: dict[str, Any]) -> int:
        if candidate.get("replacement_targets"):
            return 14
        role = normalize_text(candidate.get("proposed_role"))
        if "引流" in role:
            return 7
        if parse_float(candidate.get("heat_score")) >= 80:
            return 10
        return 21

    def _derive_launch_action(self, candidate: dict[str, Any]) -> str:
        auto_decision = normalize_text(candidate.get("auto_pick_decision"))
        if "替换" in auto_decision:
            return "建议替换上新"
        if auto_decision:
            return "建议上新"
        suggestion_status = normalize_text(candidate.get("suggestion_status"))
        if suggestion_status == "建议替换现有SKU":
            return "建议替换上新"
        if suggestion_status in {"建议上新", "建议观察"}:
            return suggestion_status
        return "建议上新"

    def _parse_date_text(self, value: str) -> date | None:
        text = normalize_text(value)
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            return None

    def _build_procurement_state(
        self,
        structured_skus: list[dict[str, Any]],
        candidate_recommendations: list[dict[str, Any]],
        auto_selection: dict[str, Any],
    ) -> dict[str, Any]:
        plan_map = {
            int(item["candidate_id"]): item
            for item in self.db.list_candidate_launch_plans()
            if str(item.get("candidate_id", "")).isdigit()
        }
        review_logs_by_candidate: dict[int, list[dict[str, Any]]] = {}
        for row in self.db.list_candidate_review_logs():
            candidate_id = int(row.get("candidate_id") or 0)
            if not candidate_id:
                continue
            review_logs_by_candidate.setdefault(candidate_id, []).append(row)

        selected_map = {
            int(item["id"]): item
            for item in auto_selection.get("selected", [])
            if str(item.get("id", "")).isdigit()
        }
        candidate_map = {
            int(item["id"]): item
            for item in candidate_recommendations
            if str(item.get("id", "")).isdigit()
        }
        tracked_ids = list(
            dict.fromkeys(
                list(selected_map.keys())
                + list(plan_map.keys())
                + list(review_logs_by_candidate.keys())
            )
        )

        launch_queue: list[dict[str, Any]] = []
        for candidate_id in tracked_ids:
            candidate = candidate_map.get(candidate_id)
            if not candidate:
                continue
            selected = selected_map.get(candidate_id) or {}
            plan = plan_map.get(candidate_id, {})
            review_logs = review_logs_by_candidate.get(candidate_id, [])
            suggested_first_order_qty = self._suggest_first_order_qty(selected or candidate)
            suggested_review_cycle_days = self._suggest_review_cycle_days(selected or candidate)
            first_order_qty = parse_int(plan.get("first_order_qty")) or suggested_first_order_qty
            actual_launch_qty = parse_int(plan.get("actual_launch_qty"))
            launch_status = normalize_text(plan.get("launch_status")) or "planned"
            actual_launch_date = normalize_text(plan.get("actual_launch_date"))
            actual_launch_price = round(
                parse_float(plan.get("actual_launch_price")) or parse_float(candidate.get("suggested_price")),
                2,
            )
            review_cycle_days = parse_int(plan.get("review_cycle_days")) or suggested_review_cycle_days
            latest_review = review_logs[0] if review_logs else None
            anchor_date = self._parse_date_text(latest_review.get("review_date")) if latest_review else None
            if not anchor_date:
                anchor_date = self._parse_date_text(actual_launch_date)
            next_review_date = ""
            if anchor_date and review_cycle_days > 0:
                next_review_date = (anchor_date + timedelta(days=review_cycle_days)).isoformat()
            review_due = bool(next_review_date and next_review_date <= date.today().isoformat())
            unit_cost = parse_float(candidate.get("expected_purchase_price"))
            suggested_action = self._derive_launch_action(selected or candidate)

            launch_queue.append(
                {
                    "candidate_id": candidate_id,
                    "brand": candidate.get("brand", ""),
                    "product_name": candidate.get("product_name", ""),
                    "spec_text": candidate.get("spec_text", ""),
                    "source_platform": candidate.get("source_platform", ""),
                    "proposed_role": candidate.get("proposed_role", ""),
                    "suggested_action": suggested_action,
                    "suggested_price": candidate.get("suggested_price", 0),
                    "expected_purchase_price": candidate.get("expected_purchase_price", 0),
                    "expected_margin": candidate.get("expected_margin", 0),
                    "heat_score": candidate.get("heat_score", 0),
                    "replacement_targets": selected.get("replacement_targets") or [],
                    "reason": (selected.get("auto_select_reasons") or candidate.get("recommendation_basis") or [])[:3],
                    "suggested_first_order_qty": suggested_first_order_qty,
                    "first_order_qty": first_order_qty,
                    "actual_launch_qty": actual_launch_qty,
                    "actual_launch_date": actual_launch_date,
                    "actual_launch_price": actual_launch_price,
                    "review_cycle_days": review_cycle_days,
                    "suggested_review_cycle_days": suggested_review_cycle_days,
                    "launch_status": launch_status,
                    "launch_status_label": self._launch_status_label(launch_status),
                    "launch_notes": normalize_text(plan.get("launch_notes")),
                    "planned_budget": round(unit_cost * max(first_order_qty, 0), 2),
                    "review_log_count": len(review_logs),
                    "latest_review": {
                        **latest_review,
                        "decision_label": self._review_decision_label(latest_review.get("decision", "")),
                    }
                    if latest_review
                    else None,
                    "review_logs": [
                        {
                            **row,
                            "decision_label": self._review_decision_label(row.get("decision", "")),
                        }
                        for row in review_logs
                    ],
                    "next_review_date": next_review_date,
                    "review_due": review_due,
                }
            )

        launch_queue.sort(
            key=lambda item: (
                item.get("launch_status") not in {"planned", "launched", "observing"},
                not item.get("review_due"),
                item.get("brand", ""),
                item.get("product_name", ""),
            )
        )
        return {
            "launch_queue": launch_queue,
            "summary": {
                "planned_count": sum(1 for item in launch_queue if item["launch_status"] == "planned"),
                "launched_count": sum(1 for item in launch_queue if item["launch_status"] in {"launched", "observing", "replenished"}),
                "review_due_count": sum(1 for item in launch_queue if item["review_due"]),
                "tracked_count": len(launch_queue),
            },
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


def _tool_meta(self: ToolApplication) -> dict[str, Any]:
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
        "crawler_cookie_notice": "Cookie 不是必填。建议先匿名抓取，只有公开页面被拦截时，再临时补对应平台 Cookie 作为兜底；Cookie 不写入数据库。",
        "crawler_browser_helper_notice": "浏览器辅助采集不依赖 Cookie。建议先打开目标平台搜索页或榜单页，再运行辅助脚本，把 JSON 结果粘贴回来导入。",
        "backup_dir": str(BACKUP_DIR),
        "market_snapshot_ttl_hours": MARKET_SNAPSHOT_TTL_HOURS,
        "crawler_default_keywords": DEFAULT_HOT_KEYWORDS,
        "pricing_simulation_strategies": [
            {"key": "adjust_by_amount", "label": "每个SKU统一加减金额"},
            {"key": "to_taobao_avg", "label": "统一对齐淘宝均价"},
            {"key": "to_system_suggested", "label": "统一对齐系统建议价"},
        ],
        "launch_status_options": LAUNCH_STATUS_OPTIONS,
        "review_decision_options": REVIEW_DECISION_OPTIONS,
        "review_cycle_options": [7, 10, 14, 21, 30],
    }


def _tool_state(self: ToolApplication) -> dict[str, Any]:
    structured_skus = self._structured_skus()
    candidates = self._enriched_candidates(structured_skus)
    dashboard = build_dashboard(structured_skus, candidates)
    candidate_recommendations = sorted(
        candidates,
        key=lambda item: (-item.get("recommendation_score", 0), item.get("brand", ""), item.get("product_name", "")),
    )
    auto_selection = auto_select_candidates(candidate_recommendations, structured_skus)
    procurement = self._build_procurement_state(structured_skus, candidate_recommendations, auto_selection)
    return {
        "skus": structured_skus,
        "candidates": candidates,
        "dashboard": dashboard,
        "market_tools": {
            "diagnostics": [
                item
                for item in structured_skus
                if item.get("market_sample_status") in {"无结果", "被拦截", "样本不足", "近似样本", "跨平台替代", "人工补样本"}
            ],
            "manual_overrides": list(self._manual_override_map().values()),
        },
        "recommendations": {
            "existing": structured_skus,
            "candidate": candidate_recommendations,
            "auto_selection": auto_selection,
        },
        "procurement": procurement,
    }


def _save_candidate_launch_plan(self: ToolApplication, candidate_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    candidate = self.db.get_candidate(candidate_id)
    if not candidate:
        raise ValueError("未找到要记录首单和上新的候选商品。")
    structured_skus = self._structured_skus()
    enriched = enrich_candidate(candidate, structured_skus)
    default_qty = self._suggest_first_order_qty(enriched)
    default_cycle = self._suggest_review_cycle_days(enriched)
    launch_status = normalize_text(payload.get("launch_status")) or "planned"
    actual_launch_date = normalize_text(payload.get("actual_launch_date"))
    if launch_status in {"launched", "observing", "replenished"} and not actual_launch_date:
        actual_launch_date = date.today().isoformat()
    saved = self.db.save_candidate_launch_plan(
        candidate_id=candidate_id,
        planned_action=normalize_text(payload.get("planned_action")) or self._derive_launch_action(enriched),
        first_order_qty=max(0, parse_int(payload.get("first_order_qty")) or default_qty),
        actual_launch_qty=max(0, parse_int(payload.get("actual_launch_qty"))),
        actual_launch_date=actual_launch_date,
        actual_launch_price=round(parse_float(payload.get("actual_launch_price")) or parse_float(enriched.get("suggested_price")), 2),
        review_cycle_days=max(1, parse_int(payload.get("review_cycle_days")) or default_cycle),
        launch_status=launch_status,
        launch_notes=normalize_text(payload.get("launch_notes")),
    )
    latest_state = self.state()["procurement"]
    item = next((row for row in latest_state["launch_queue"] if int(row.get("candidate_id") or 0) == candidate_id), None)
    return {"ok": True, "plan": saved, "item": item}


def _add_candidate_review_log(self: ToolApplication, candidate_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    candidate = self.db.get_candidate(candidate_id)
    if not candidate:
        raise ValueError("未找到要复盘的候选商品。")
    review_date = normalize_text(payload.get("review_date")) or date.today().isoformat()
    gross_margin_rate = parse_float(payload.get("gross_margin_rate"))
    if gross_margin_rate > 1:
        gross_margin_rate = gross_margin_rate / 100
    saved = self.db.add_candidate_review_log(
        candidate_id=candidate_id,
        review_date=review_date,
        cycle_label=normalize_text(payload.get("cycle_label")) or "周期复盘",
        sales_units=max(0, parse_int(payload.get("sales_units"))),
        sales_amount=round(parse_float(payload.get("sales_amount")), 2),
        gross_margin_rate=round(gross_margin_rate, 4),
        decision=normalize_text(payload.get("decision")) or "observe",
        notes=normalize_text(payload.get("notes")),
    )
    existing_plan = self.db.get_candidate_launch_plan(candidate_id) or {}
    if saved.get("decision") == "delist":
        self.db.save_candidate_launch_plan(
            candidate_id=candidate_id,
            planned_action=normalize_text(existing_plan.get("planned_action")) or "建议下架",
            first_order_qty=max(0, parse_int(existing_plan.get("first_order_qty"))),
            actual_launch_qty=max(0, parse_int(existing_plan.get("actual_launch_qty"))),
            actual_launch_date=normalize_text(existing_plan.get("actual_launch_date")) or review_date,
            actual_launch_price=round(parse_float(existing_plan.get("actual_launch_price")), 2),
            review_cycle_days=max(1, parse_int(existing_plan.get("review_cycle_days")) or 14),
            launch_status="delisted",
            launch_notes=normalize_text(existing_plan.get("launch_notes")),
        )
    elif existing_plan:
        self.db.save_candidate_launch_plan(
            candidate_id=candidate_id,
            planned_action=normalize_text(existing_plan.get("planned_action")) or "建议上新",
            first_order_qty=max(0, parse_int(existing_plan.get("first_order_qty"))),
            actual_launch_qty=max(0, parse_int(existing_plan.get("actual_launch_qty"))),
            actual_launch_date=normalize_text(existing_plan.get("actual_launch_date")) or review_date,
            actual_launch_price=round(parse_float(existing_plan.get("actual_launch_price")), 2),
            review_cycle_days=max(1, parse_int(existing_plan.get("review_cycle_days")) or 14),
            launch_status="observing" if normalize_text(existing_plan.get("launch_status")) == "planned" else normalize_text(existing_plan.get("launch_status")) or "observing",
            launch_notes=normalize_text(existing_plan.get("launch_notes")),
        )
    else:
        self.db.save_candidate_launch_plan(
            candidate_id=candidate_id,
            planned_action="建议上新",
            first_order_qty=0,
            actual_launch_qty=0,
            actual_launch_date=review_date,
            actual_launch_price=0,
            review_cycle_days=14,
            launch_status="observing",
            launch_notes="",
        )
    latest_state = self.state()["procurement"]
    item = next((row for row in latest_state["launch_queue"] if int(row.get("candidate_id") or 0) == candidate_id), None)
    return {"ok": True, "review": saved, "item": item}


def _update_candidate_review_log(self: ToolApplication, candidate_id: int, review_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    review = self.db.get_candidate_review_log(review_id)
    if not review or int(review.get("candidate_id") or 0) != candidate_id:
        raise ValueError("未找到要编辑的复盘记录。")
    gross_margin_rate = parse_float(payload.get("gross_margin_rate"))
    if gross_margin_rate > 1:
        gross_margin_rate = gross_margin_rate / 100
    saved = self.db.update_candidate_review_log(
        review_id=review_id,
        review_date=normalize_text(payload.get("review_date")) or normalize_text(review.get("review_date")) or date.today().isoformat(),
        cycle_label=normalize_text(payload.get("cycle_label")) or normalize_text(review.get("cycle_label")) or "周期复盘",
        sales_units=max(0, parse_int(payload.get("sales_units"))),
        sales_amount=round(parse_float(payload.get("sales_amount")), 2),
        gross_margin_rate=round(gross_margin_rate, 4),
        decision=normalize_text(payload.get("decision")) or normalize_text(review.get("decision")) or "observe",
        notes=normalize_text(payload.get("notes")),
    )
    latest_state = self.state()["procurement"]
    item = next((row for row in latest_state["launch_queue"] if int(row.get("candidate_id") or 0) == candidate_id), None)
    return {"ok": True, "review": saved, "item": item}


def _delete_candidate_review_log(self: ToolApplication, candidate_id: int, review_id: int) -> dict[str, Any]:
    review = self.db.get_candidate_review_log(review_id)
    if not review or int(review.get("candidate_id") or 0) != candidate_id:
        raise ValueError("未找到要删除的复盘记录。")
    self.db.delete_candidate_review_log(review_id)
    latest_state = self.state()["procurement"]
    item = next((row for row in latest_state["launch_queue"] if int(row.get("candidate_id") or 0) == candidate_id), None)
    return {"ok": True, "item": item}


ToolApplication.meta = _tool_meta
ToolApplication.state = _tool_state
ToolApplication.save_candidate_launch_plan = _save_candidate_launch_plan
ToolApplication.add_candidate_review_log = _add_candidate_review_log
ToolApplication.update_candidate_review_log = _update_candidate_review_log
ToolApplication.delete_candidate_review_log = _delete_candidate_review_log


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
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/review-logs"):
                candidate_id = int(parsed.path.split("/")[3])
                payload = self._read_json()
                self._send_json(APP.add_candidate_review_log(candidate_id, payload), status=HTTPStatus.CREATED)
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
            if parsed.path.startswith("/api/candidates/") and parsed.path.endswith("/launch-plan"):
                candidate_id = int(parsed.path.split("/")[3])
                payload = self._read_json()
                self._send_json(APP.save_candidate_launch_plan(candidate_id, payload))
                return
            if parsed.path.startswith("/api/candidates/") and "/review-logs/" in parsed.path:
                parts = parsed.path.split("/")
                candidate_id = int(parts[3])
                review_id = int(parts[5])
                payload = self._read_json()
                self._send_json(APP.update_candidate_review_log(candidate_id, review_id, payload))
                return
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
            if parsed.path.startswith("/api/candidates/") and "/review-logs/" in parsed.path:
                parts = parsed.path.split("/")
                candidate_id = int(parts[3])
                review_id = int(parts[5])
                self._send_json(APP.delete_candidate_review_log(candidate_id, review_id))
                return
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


def _json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = normalize_text(value)
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _market_confidence_level(score: Any) -> str:
    value = parse_float(score)
    if value >= 80:
        return "高"
    if value >= 60:
        return "中"
    if value >= 40:
        return "低"
    return "弱"


def _platform_key(value: Any) -> str:
    text = normalize_text(value).lower()
    mapping = {
        "淘宝": "taobao",
        "taobao": "taobao",
        "天猫": "tmall",
        "tmall": "tmall",
        "京东": "jd",
        "jd": "jd",
        "小红书": "xiaohongshu",
        "xiaohongshu": "xiaohongshu",
        "抖音": "douyin",
        "douyin": "douyin",
        "人工": "manual",
        "manual": "manual",
    }
    return mapping.get(text, text)


def _platform_label_from_key(value: Any) -> str:
    key = _platform_key(value)
    return PLATFORM_LABELS.get(key, normalize_text(value) or "其他")


def _decode_snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    details = _json_value(row.get("details_json"), {})
    sample_prices = _json_value(row.get("sample_prices_json"), [])
    matched_titles = _json_value(row.get("matched_titles_json"), [])
    decoded = {
        **row,
        "sample_prices": sample_prices if isinstance(sample_prices, list) else [],
        "matched_titles": matched_titles if isinstance(matched_titles, list) else [],
        "details": details if isinstance(details, dict) else {},
    }
    decoded["source_platform"] = normalize_text(row.get("source_platform"))
    decoded["confidence_level"] = _market_confidence_level(row.get("confidence_score"))
    decoded["reference_price"] = round(parse_float(row.get("median_price")), 2)
    decoded["reference_low"] = round(parse_float(row.get("p10_price")), 2)
    decoded["reference_high"] = round(parse_float(row.get("p90_price")), 2)
    decoded["price_disorder_flag"] = 1 if (
        parse_float(row.get("p10_price")) > 0
        and parse_float(row.get("p90_price")) / max(parse_float(row.get("p10_price")), 0.01) >= 1.5
    ) else 0
    return decoded


def _nearest_review_cycle(days: int) -> int:
    if days <= 10:
        return 7
    if days <= 22:
        return 14
    return 30


REVIEW_TARGETS = {
    "引流品": {7: 0.30, 14: 0.50, 30: 0.80},
    "常规品": {7: 0.20, 14: 0.40, 30: 0.70},
    "利润品": {7: 0.15, 14: 0.30, 30: 0.55},
}


def _review_target(role: str, cycle_days: int) -> float:
    normalized_role = normalize_text(role)
    targets = REVIEW_TARGETS.get(normalized_role) or REVIEW_TARGETS["常规品"]
    return targets.get(_nearest_review_cycle(cycle_days), targets[14])


def _review_result(role: str, cycle_days: int, sell_through: float) -> str:
    target = _review_target(role, cycle_days)
    if sell_through >= target + 0.15:
        return "优于预期"
    if sell_through >= target:
        return "达标"
    if sell_through >= max(target * 0.6, target - 0.18):
        return "偏弱"
    return "失败"


def _extract_case_pack_units(text: Any) -> int:
    values = [int(round(float(item))) for item in re.findall(r"\d+(?:\.\d+)?", normalize_text(text))]
    positives = [value for value in values if value > 0]
    return max(positives) if positives else 0


def _action_key(item_type: str, item_id: Any) -> str:
    return f"{item_type}:{item_id}"


def _join_reason(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(normalize_text(item) for item in value if normalize_text(item))
    return normalize_text(value)


def _editable_action_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "recommended_price": round(parse_float(row.get("recommended_price")), 2),
        "suggested_first_order_qty": max(0, parse_int(row.get("suggested_first_order_qty"))),
        "planned_budget": round(parse_float(row.get("planned_budget")), 2),
        "review_cycle_days": max(0, parse_int(row.get("review_cycle_days"))),
        "status": normalize_text(row.get("status")) or "待确认",
        "notes": normalize_text(row.get("notes")),
    }


def _tool_meta_v3(self: ToolApplication) -> dict[str, Any]:
    meta = _tool_meta(self)
    meta.update(
        {
            "operating_defaults": {
                "first_order_model": "平衡铺货",
                "market_anchor_mode": "多平台均衡",
                "feedback_mode": "半自动建议",
            },
            "proposal_decisions": [
                {"key": "accepted", "label": "确认生效"},
                {"key": "rejected", "label": "暂不采纳"},
            ],
            "review_template_days": [7, 14, 30],
        }
    )
    return meta


def _tool_merge_market_context_v3(self: ToolApplication, raw_skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    snapshots_by_sku_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in self.db.list_market_source_snapshots():
        sku_id = int(row.get("sku_id") or 0)
        if not sku_id:
            continue
        snapshots_by_sku_id[sku_id].append(_decode_snapshot_row(row))

    merged_rows: list[dict[str, Any]] = []
    for raw in raw_skus:
        sku_id = int(raw.get("id") or 0)
        merged = {**raw}
        source_rows = list(snapshots_by_sku_id.get(sku_id, []))
        aggregate = next((row for row in source_rows if row.get("source_platform") == "aggregate"), None)
        diagnostic = diagnostics_by_sku_id.get(sku_id)
        manual_override = manual_by_sku_id.get(sku_id)
        manual_snapshot = None

        if manual_override:
            sample_prices = [float(value) for value in _json_value(manual_override.get("sample_prices_json"), []) if parse_float(value) > 0]
            source_urls = [normalize_text(value) for value in _json_value(manual_override.get("source_urls_json"), []) if normalize_text(value)]
            manual_snapshot = build_manual_source_snapshot(
                sku_id=sku_id,
                sku_code=normalize_text(raw.get("sku_code")),
                source_platform=_platform_key(manual_override.get("source_platform")) or "manual",
                sample_prices=sample_prices,
                source_urls=source_urls,
                note=normalize_text(manual_override.get("note")),
                confirmed=True,
            )
            manual_snapshot["source_platform_label"] = _platform_label_from_key(manual_override.get("source_platform"))
            source_rows.append(manual_snapshot)

        if aggregate:
            merged.update(
                {
                    "taobao_avg_price": aggregate.get("reference_price", aggregate.get("median_price", 0)),
                    "taobao_min_price": aggregate.get("reference_low", aggregate.get("p10_price", 0)),
                    "taobao_max_price": aggregate.get("reference_high", aggregate.get("p90_price", 0)),
                    "taobao_sample_count": aggregate.get("sample_count", 0),
                    "price_disorder_flag": aggregate.get("price_disorder_flag", 0),
                    "online_heat_score": aggregate.get("heat_score", raw.get("online_heat_score", 0)),
                    "market_snapshot_at": aggregate.get("captured_at", raw.get("market_snapshot_at", "")),
                    "market_sample_status": aggregate.get("status", ""),
                    "market_source_mode": aggregate.get("details", {}).get("anchor_source") or aggregate.get("details", {}).get("source_mode", ""),
                    "market_diagnostic_summary": aggregate.get("details", {}).get("diagnostic_summary", ""),
                    "market_anchor_source": aggregate.get("details", {}).get("anchor_source", ""),
                    "market_confidence_score": aggregate.get("confidence_score", 0),
                    "market_confidence_level": aggregate.get("confidence_level", _market_confidence_level(aggregate.get("confidence_score"))),
                    "market_reference_price": aggregate.get("reference_price", 0),
                    "market_reference_low": aggregate.get("reference_low", 0),
                    "market_reference_high": aggregate.get("reference_high", 0),
                }
            )

        if diagnostic:
            merged.update(
                {
                    "market_query_logs_json": diagnostic.get("query_logs_json", "[]"),
                    "market_blocked_platforms_json": diagnostic.get("blocked_platforms_json", "[]"),
                    "market_fallback_note": diagnostic.get("fallback_note", ""),
                    "market_matched_titles_json": diagnostic.get("matched_titles_json", "[]"),
                }
            )
            if not normalize_text(merged.get("market_diagnostic_summary")):
                merged["market_diagnostic_summary"] = diagnostic.get("diagnostic_summary", "")

        if manual_override:
            prices = [float(value) for value in _json_value(manual_override.get("sample_prices_json"), []) if parse_float(value) > 0]
            prices.sort()
            if prices:
                ratio = prices[-1] / max(prices[0], 0.01)
                merged.update(
                    {
                        "manual_sample_prices_json": manual_override.get("sample_prices_json", "[]"),
                        "manual_sample_urls_json": manual_override.get("source_urls_json", "[]"),
                        "manual_sample_source_platform": manual_override.get("source_platform", ""),
                        "manual_sample_note": manual_override.get("note", ""),
                    }
                )
                weak_aggregate = not aggregate or parse_float(aggregate.get("confidence_score")) < 50 or normalize_text(aggregate.get("status")) in {"被拦截", "无结果", "样本不足"}
                if weak_aggregate:
                    merged.update(
                        {
                            "taobao_avg_price": round(sum(prices) / len(prices), 2),
                            "taobao_min_price": round(prices[0], 2),
                            "taobao_max_price": round(prices[-1], 2),
                            "taobao_sample_count": len(prices),
                            "price_disorder_flag": 1 if ratio >= 1.5 else 0,
                            "market_snapshot_at": manual_override.get("updated_at", ""),
                            "market_sample_status": "人工补样本",
                            "market_source_mode": f"人工补样本/{manual_override.get('source_platform', '人工')}",
                            "market_diagnostic_summary": normalize_text(manual_override.get("note")) or "当前以人工补样本作为市场参考。",
                            "market_anchor_source": "人工补样本",
                            "market_confidence_score": min(58.0, parse_float((manual_snapshot or {}).get("confidence_score"))),
                            "market_confidence_level": _market_confidence_level(min(58.0, parse_float((manual_snapshot or {}).get("confidence_score")))),
                            "market_reference_price": round(sum(prices) / len(prices), 2),
                            "market_reference_low": round(prices[0], 2),
                            "market_reference_high": round(prices[-1], 2),
                        }
                    )

        merged["market_source_snapshots"] = source_rows
        merged_rows.append(merged)
    return merged_rows


def _strategy_override_rows(self: ToolApplication) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in self.db.list_strategy_overrides():
        value = _json_value(row.get("value_json"), {})
        decoded = {**row, "value": value if isinstance(value, dict) else {}}
        if decoded["value"].get("active", 1):
            rows.append(decoded)
    return rows


def _strategy_override_map(self: ToolApplication) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (normalize_text(item.get("override_type")), normalize_text(item.get("scope_key"))): item
        for item in _strategy_override_rows(self)
    }


def _candidate_market_confidence(candidate: dict[str, Any]) -> dict[str, Any]:
    platform_key = _platform_key(candidate.get("source_platform"))
    has_price = parse_float(candidate.get("online_reference_price")) > 0
    has_url = bool(normalize_text(candidate.get("product_url")))
    score = 35.0
    if platform_key in {"taobao", "tmall", "jd"} and has_price and has_url:
        score = 78.0
    elif platform_key in {"taobao", "tmall", "jd"} and has_price:
        score = 68.0
    elif platform_key in {"xiaohongshu", "douyin"} and has_price:
        score = 52.0
    elif has_price:
        score = 45.0
    return {"score": score, "level": _market_confidence_level(score), "platform_key": platform_key}


def _candidate_gap_type(candidate: dict[str, Any], structured_skus: list[dict[str, Any]]) -> str:
    if candidate.get("replacement_targets") or normalize_text(candidate.get("intended_replace_sku")):
        return "替换型"
    brand = normalize_text(candidate.get("brand"))
    efficacy = normalize_text(candidate.get("efficacy_tags"))
    band = normalize_text(candidate.get("price_band"))
    brand_count = sum(1 for item in structured_skus if normalize_text(item.get("brand")) == brand)
    efficacy_count = sum(1 for item in structured_skus if normalize_text(item.get("efficacy_tags")) == efficacy)
    band_count = sum(1 for item in structured_skus if normalize_text(item.get("price_band")) == band)
    if brand_count and parse_float(candidate.get("heat_score")) >= 85 and brand_count <= 1:
        return "品牌爆款补位"
    if band_count == 0:
        return "价格带补位"
    if efficacy_count == 0:
        return "功效补位"
    if brand_count and parse_float(candidate.get("heat_score")) >= 80:
        return "品牌爆款补位"
    return "常规观察"


def _candidate_review_cycle_days(candidate: dict[str, Any], gap_type: str) -> int:
    if gap_type == "替换型":
        return 14
    if normalize_text(candidate.get("proposed_role")) == "引流品":
        return 7
    if normalize_text(candidate.get("proposed_role")) == "利润品":
        return 30
    if normalize_text(candidate.get("target_group")) == "儿童":
        return 30
    return 14


def _candidate_first_order_qty(candidate: dict[str, Any], structured_skus: list[dict[str, Any]], strategy_map: dict[tuple[str, str], dict[str, Any]]) -> int:
    gap_type = _candidate_gap_type(candidate, structured_skus)
    role = normalize_text(candidate.get("proposed_role"))
    if gap_type == "替换型":
        qty = 16
    elif normalize_text(candidate.get("target_group")) == "儿童":
        qty = 8
    elif role == "引流品":
        qty = 18
    elif role == "利润品":
        qty = 8
    else:
        qty = 12

    qty += int(round(parse_float(strategy_map.get(("first_order_base_qty", role), {}).get("value", {}).get("delta_units"))))

    heat = parse_float(candidate.get("heat_score"))
    confidence = _candidate_market_confidence(candidate)
    if heat >= 85:
        qty += 4
    elif heat >= 70:
        qty += 2
    if confidence["level"] == "高":
        qty += 2
    elif confidence["level"] in {"低", "弱"}:
        qty -= 2
    if parse_float(candidate.get("suggested_price") or candidate.get("online_reference_price")) >= 30:
        qty -= 2
    if gap_type == "品牌爆款补位":
        qty += 2
    comparison_rows = candidate.get("comparison_rows") or []
    if any(normalize_text(item.get("cannibalization_risk")) == "高" for item in comparison_rows):
        qty -= 4

    qty = max(6, min(30, qty))
    qty = int(((qty + 1) // 2) * 2)
    case_pack = _extract_case_pack_units(candidate.get("case_pack") or candidate.get("supplier_case_pack"))
    if case_pack > 0:
        qty = int(((qty + case_pack - 1) // case_pack) * case_pack)
    return max(6, min(30 if case_pack <= 0 else max(30, qty), qty))


def _existing_action_priority(item: dict[str, Any]) -> float:
    action = normalize_text(item.get("action"))
    structure_score = 25 if action in {"建议低价引流", "建议下架"} else 18 if action in {"建议调整售价", "建议利润定价"} else 10
    low, high = 0.0, 0.0
    role = normalize_text(item.get("structural_role"))
    if role == "引流品":
        low, high = 0.18, 0.25
    elif role == "利润品":
        low, high = 0.32, 0.40
    else:
        low, high = 0.25, 0.32
    margin = parse_float(item.get("gross_margin"))
    margin_fit = 20 if low <= margin <= high else 12 if abs(margin - low) <= 0.04 or abs(margin - high) <= 0.04 else 6
    market_score = min(parse_float(item.get("market_confidence_score")), 100) * 0.15
    heat_score = min(parse_float(item.get("online_heat_score")), 100) * 0.15
    replacement_score = 15 if action == "建议下架" and parse_int(item.get("six_month_sales")) <= 20 else 8 if action == "建议低价引流" else 4
    capital_score = min(parse_float(item.get("profit_contribution_share")) * 100, 10)
    return round(structure_score + margin_fit + market_score + heat_score + replacement_score + capital_score, 1)


def _candidate_action_priority(candidate: dict[str, Any], structured_skus: list[dict[str, Any]], strategy_map: dict[tuple[str, str], dict[str, Any]]) -> float:
    gap_type = _candidate_gap_type(candidate, structured_skus)
    structure_score = 25 if gap_type in {"价格带补位", "功效补位"} else 22 if gap_type == "品牌爆款补位" else 18 if gap_type == "替换型" else 10
    role = normalize_text(candidate.get("proposed_role"))
    low, high = (0.18, 0.25) if role == "引流品" else (0.32, 0.40) if role == "利润品" else (0.25, 0.32)
    expected_margin = parse_float(candidate.get("expected_margin"))
    margin_fit = 20 if low <= expected_margin <= high else 12 if abs(expected_margin - low) <= 0.04 or abs(expected_margin - high) <= 0.04 else 6
    confidence = _candidate_market_confidence(candidate)
    market_score = confidence["score"] * 0.15
    heat_score = min(parse_float(candidate.get("heat_score")), 100) * 0.15
    replacement_score = 15 if gap_type == "替换型" else 10 if candidate.get("replacement_targets") else 4
    unit_profit = max(0.0, parse_float(candidate.get("suggested_price")) - parse_float(candidate.get("expected_purchase_price")))
    capital_score = min((unit_profit / max(parse_float(candidate.get("expected_purchase_price")), 1)) * 10, 10)
    total = structure_score + margin_fit + market_score + heat_score + replacement_score + capital_score
    total += parse_float(strategy_map.get(("price_band_priority", normalize_text(candidate.get("price_band"))), {}).get("value", {}).get("delta_points"))
    total += parse_float(strategy_map.get(("efficacy_score", normalize_text(candidate.get("efficacy_tags"))), {}).get("value", {}).get("delta_points"))
    total += parse_float(strategy_map.get(("platform_heat_weight", confidence["platform_key"]), {}).get("value", {}).get("delta_points"))
    return round(total, 1)


def _build_procurement_actions_v3(
    self: ToolApplication,
    structured_skus: list[dict[str, Any]],
    candidate_recommendations: list[dict[str, Any]],
    auto_selection: dict[str, Any],
    procurement: dict[str, Any],
) -> dict[str, Any]:
    strategy_map = _strategy_override_map(self)
    saved_action_rows = {
        normalize_text(row.get("action_key")): {
            **row,
            "payload": _json_value(row.get("payload_json"), {}),
        }
        for row in self.db.list_procurement_action_items()
    }
    selected_map = {
        int(item["id"]): item
        for item in auto_selection.get("selected", [])
        if str(item.get("id", "")).isdigit()
    }
    launch_plan_map = {
        int(item["candidate_id"]): item
        for item in self.db.list_candidate_launch_plans()
        if str(item.get("candidate_id", "")).isdigit()
    }

    existing_actions: list[dict[str, Any]] = []
    for item in structured_skus:
        action_key = _action_key("sku", item.get("id"))
        saved = saved_action_rows.get(action_key, {})
        payload = saved.get("payload", {})
        row = {
            "action_key": action_key,
            "item_type": "sku",
            "item_id": str(item.get("id")),
            "sku_code": item.get("sku_code"),
            "brand": item.get("brand"),
            "product_name": item.get("product_name"),
            "spec_text": item.get("spec_text"),
            "structural_role": item.get("structural_role"),
            "action_type": item.get("action") or "保留",
            "priority_score": _existing_action_priority(item),
            "reason_summary": normalize_text(item.get("reason")) or _join_reason(item.get("recommendation_basis")),
            "target_price_range": {
                "low": parse_float(item.get("suggested_price_floor")),
                "high": parse_float(item.get("suggested_price_ceiling")),
                "label": normalize_text(item.get("suggested_price_range_label")),
            },
            "recommended_price": round(parse_float(payload.get("recommended_price")) or parse_float(item.get("suggested_price")), 2),
            "suggested_first_order_qty": 0,
            "planned_budget": 0.0,
            "review_cycle_days": parse_int(payload.get("review_cycle_days")) or (7 if normalize_text(item.get("action")) == "建议低价引流" else 14),
            "confidence_level": normalize_text(payload.get("confidence_level")) or normalize_text(item.get("market_confidence_level")) or _market_confidence_level(item.get("market_confidence_score")),
            "confidence_score": parse_float(item.get("market_confidence_score")),
            "replace_target_ids": [],
            "status": normalize_text(saved.get("status")) or normalize_text(payload.get("status")) or "待确认",
            "notes": normalize_text(saved.get("notes")) or normalize_text(payload.get("notes")),
            "market_anchor_source": normalize_text(item.get("market_anchor_source")),
            "market_reference_price": parse_float(item.get("market_reference_price") or item.get("taobao_avg_price")),
            "market_reference_range": [parse_float(item.get("market_reference_low") or item.get("taobao_min_price")), parse_float(item.get("market_reference_high") or item.get("taobao_max_price"))],
            "market_status": normalize_text(item.get("market_sample_status")),
            "price_disorder_flag": parse_int(item.get("price_disorder_flag")),
            "half_year_gross_profit": parse_float(item.get("half_year_gross_profit")),
        }
        existing_actions.append(row)

    candidate_actions: list[dict[str, Any]] = []
    for candidate in candidate_recommendations:
        candidate_id = int(candidate.get("id") or 0)
        if not candidate_id:
            continue
        selected = selected_map.get(candidate_id, {})
        plan = launch_plan_map.get(candidate_id, {})
        confidence = _candidate_market_confidence(candidate)
        gap_type = _candidate_gap_type(candidate, structured_skus)
        suggested_qty = _candidate_first_order_qty({**candidate, **selected}, structured_skus, strategy_map)
        first_order_qty = parse_int(plan.get("first_order_qty")) or suggested_qty
        review_cycle_days = parse_int(plan.get("review_cycle_days")) or _candidate_review_cycle_days(candidate, gap_type)
        suggested_action = normalize_text(selected.get("auto_pick_decision"))
        if "替换" in suggested_action or normalize_text(candidate.get("suggestion_status")) == "建议替换现有SKU":
            action_type = "建议替换"
        elif "上新" in suggested_action or normalize_text(candidate.get("suggestion_status")) == "建议上新":
            action_type = "建议上新"
        elif normalize_text(candidate.get("suggestion_status")) == "建议观察":
            action_type = "建议观察"
        else:
            action_type = "不建议上"
        action_key = _action_key("candidate", candidate_id)
        saved = saved_action_rows.get(action_key, {})
        payload = saved.get("payload", {})
        recommended_price = round(parse_float(payload.get("recommended_price")) or parse_float(candidate.get("suggested_price")), 2)
        effective_qty = parse_int(payload.get("suggested_first_order_qty")) or first_order_qty
        planned_budget = round(parse_float(candidate.get("expected_purchase_price")) * max(effective_qty, 0), 2)
        single_profit = round(max(0.0, recommended_price - parse_float(candidate.get("expected_purchase_price"))), 2)
        first_order_profit = round(single_profit * max(effective_qty, 0), 2)
        candidate_actions.append(
            {
                "action_key": action_key,
                "item_type": "candidate",
                "item_id": str(candidate_id),
                "candidate_id": candidate_id,
                "brand": candidate.get("brand"),
                "product_name": candidate.get("product_name"),
                "spec_text": candidate.get("spec_text"),
                "source_platform": candidate.get("source_platform"),
                "expected_purchase_price": parse_float(candidate.get("expected_purchase_price")),
                "gap_type": gap_type,
                "structural_role": candidate.get("proposed_role"),
                "action_type": action_type,
                "priority_score": _candidate_action_priority({**candidate, **selected}, structured_skus, strategy_map),
                "reason_summary": _join_reason(selected.get("auto_select_reasons") or candidate.get("recommendation_basis")),
                "target_price_range": {
                    "low": round(max(parse_float(candidate.get("expected_purchase_price")) / (1 - 0.18), parse_float(candidate.get("suggested_price")) - 2), 2),
                    "high": round(parse_float(candidate.get("suggested_price")) + 2, 2),
                    "label": f"{round(max(parse_float(candidate.get('expected_purchase_price')) / (1 - 0.18), parse_float(candidate.get('suggested_price')) - 2), 2)} - {round(parse_float(candidate.get('suggested_price')) + 2, 2)}",
                },
                "recommended_price": recommended_price,
                "suggested_first_order_qty": effective_qty,
                "suggested_first_order_qty_base": suggested_qty,
                "planned_budget": planned_budget,
                "review_cycle_days": parse_int(payload.get("review_cycle_days")) or review_cycle_days,
                "confidence_level": normalize_text(payload.get("confidence_level")) or confidence["level"],
                "confidence_score": confidence["score"],
                "replace_target_ids": selected.get("replacement_targets") or ([] if not normalize_text(candidate.get("intended_replace_sku")) else [normalize_text(candidate.get("intended_replace_sku"))]),
                "status": normalize_text(saved.get("status")) or normalize_text(payload.get("status")) or "待确认",
                "notes": normalize_text(saved.get("notes")) or normalize_text(payload.get("notes")),
                "expected_margin": parse_float(candidate.get("expected_margin")),
                "heat_score": parse_float(candidate.get("heat_score")),
                "expected_single_profit": single_profit,
                "expected_first_order_profit": first_order_profit,
                "expected_sell_through_14d": _review_target(normalize_text(candidate.get("proposed_role")) or "常规品", 14),
                "market_anchor_source": _platform_label_from_key(candidate.get("source_platform")),
            }
        )

    existing_actions.sort(key=lambda item: (-parse_float(item.get("priority_score")), item.get("brand", ""), item.get("product_name", "")))
    candidate_actions.sort(key=lambda item: (-parse_float(item.get("priority_score")), item.get("brand", ""), item.get("product_name", "")))
    all_actions = sorted(existing_actions + candidate_actions, key=lambda item: (-parse_float(item.get("priority_score")), item.get("item_type"), item.get("brand", "")))
    total_budget = round(sum(parse_float(item.get("planned_budget")) for item in candidate_actions if normalize_text(item.get("action_type")) in {"建议上新", "建议替换"}), 2)
    total_profit = round(sum(parse_float(item.get("expected_first_order_profit")) for item in candidate_actions if normalize_text(item.get("action_type")) in {"建议上新", "建议替换"}), 2)
    high_priority_brands = [item.get("brand") for item in candidate_actions[:3] if normalize_text(item.get("brand"))]
    weak_confidence_count = sum(1 for item in existing_actions if normalize_text(item.get("confidence_level")) in {"低", "弱"})
    return {
        "existing": existing_actions,
        "candidates": candidate_actions,
        "all": all_actions,
        "summary": {
            "new_count": sum(1 for item in candidate_actions if item.get("action_type") == "建议上新"),
            "replace_count": sum(1 for item in candidate_actions if item.get("action_type") == "建议替换"),
            "delist_count": sum(1 for item in existing_actions if item.get("action_type") == "建议下架"),
            "total_budget": total_budget,
            "expected_first_order_profit": total_profit,
            "high_priority_brands": list(dict.fromkeys(high_priority_brands)),
            "weak_confidence_count": weak_confidence_count,
            "tracked_review_count": procurement.get("summary", {}).get("tracked_count", 0),
        },
    }


def _augment_procurement_reviews_v3(self: ToolApplication, procurement: dict[str, Any], candidate_map: dict[int, dict[str, Any]]) -> dict[str, Any]:
    queue = []
    for item in procurement.get("launch_queue", []):
        candidate_id = int(item.get("candidate_id") or 0)
        candidate = candidate_map.get(candidate_id, {})
        role = normalize_text(item.get("proposed_role") or candidate.get("proposed_role")) or "常规品"
        launch_qty = max(parse_int(item.get("actual_launch_qty")), parse_int(item.get("first_order_qty")), 1)
        actual_launch_date = normalize_text(item.get("actual_launch_date"))
        updated_logs = []
        for log in item.get("review_logs", []):
            cycle_days = 0
            match = re.search(r"(\d+)", normalize_text(log.get("cycle_label")))
            if match:
                cycle_days = int(match.group(1))
            elif actual_launch_date and normalize_text(log.get("review_date")):
                try:
                    cycle_days = (datetime.fromisoformat(normalize_text(log.get("review_date"))).date() - datetime.fromisoformat(actual_launch_date).date()).days
                except ValueError:
                    cycle_days = parse_int(item.get("review_cycle_days")) or 14
            cycle_days = _nearest_review_cycle(max(cycle_days, 1))
            sell_through = round(parse_int(log.get("sales_units")) / max(launch_qty, 1), 4)
            target_sell_through = _review_target(role, cycle_days)
            review_result = _review_result(role, cycle_days, sell_through)
            updated_logs.append(
                {
                    **log,
                    "cycle_days": cycle_days,
                    "sell_through": sell_through,
                    "target_sell_through": target_sell_through,
                    "review_result": review_result,
                }
            )
        latest_review = updated_logs[0] if updated_logs else None
        queue.append(
            {
                **item,
                "gap_type": _candidate_gap_type(candidate, self._structured_skus()) if candidate else "",
                "price_band": candidate.get("price_band", ""),
                "efficacy_tags": candidate.get("efficacy_tags", ""),
                "review_logs": updated_logs,
                "latest_review": latest_review,
                "latest_review_result": latest_review.get("review_result") if latest_review else "",
                "execution_card": {
                    "candidate_id": candidate_id,
                    "brand": item.get("brand"),
                    "product_name": item.get("product_name"),
                    "structural_role": role,
                    "first_order_qty": item.get("first_order_qty"),
                    "actual_launch_qty": item.get("actual_launch_qty"),
                    "actual_launch_date": item.get("actual_launch_date"),
                    "actual_launch_price": item.get("actual_launch_price"),
                    "launch_status": item.get("launch_status"),
                    "latest_review_result": latest_review.get("review_result") if latest_review else "",
                    "next_review_date": item.get("next_review_date"),
                },
            }
        )
    procurement["launch_queue"] = queue
    procurement["execution_cards"] = [item["execution_card"] for item in queue]
    procurement["summary"] = {
        **(procurement.get("summary") or {}),
        "excellent_count": sum(1 for item in queue if normalize_text(item.get("latest_review_result")) == "优于预期"),
        "weak_count": sum(1 for item in queue if normalize_text(item.get("latest_review_result")) in {"偏弱", "失败"}),
    }
    return procurement


def _proposal_payload(proposal_type: str, scope_type: str, scope_key: str, positive: bool) -> dict[str, Any]:
    if proposal_type == "platform_heat_weight":
        delta = 4 if positive else -4
        title = f"建议{'上调' if positive else '下调'}{_platform_label_from_key(scope_key)}热度权重"
        impact = "将影响该平台来源候选品的优先级分。"
    elif proposal_type == "price_band_priority":
        delta = 5 if positive else -5
        title = f"建议{'上调' if positive else '下调'}{scope_key}价格带优先级"
        impact = "将影响该价格带补位候选的结构分。"
    elif proposal_type == "efficacy_score":
        delta = 5 if positive else -5
        title = f"建议{'上调' if positive else '下调'}{scope_key}功效推荐分"
        impact = "将影响该功效候选的结构补位分。"
    else:
        delta = 2 if positive else -2
        title = f"建议{'上调' if positive else '下调'}{scope_key}首单基础量"
        impact = "将影响该结构角色新品的首单数量模型。"
    return {
        "proposal_key": f"{proposal_type}:{scope_key}",
        "proposal_type": proposal_type,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "title": title,
        "impact_summary": impact,
        "suggested_value": {
            "delta_points": delta if proposal_type != "first_order_base_qty" else 0,
            "delta_units": delta if proposal_type == "first_order_base_qty" else 0,
            "active": True,
            "direction": "up" if positive else "down",
        },
    }


def _generate_feedback_proposals_v3(self: ToolApplication, procurement: dict[str, Any]) -> list[dict[str, Any]]:
    queue = [item for item in procurement.get("launch_queue", []) if normalize_text(item.get("latest_review_result")) in {"优于预期", "偏弱", "失败"}]
    groups: list[tuple[str, str, str, list[dict[str, Any]]]] = []
    for proposal_type, scope_type, extractor in [
        ("platform_heat_weight", "platform", lambda item: _platform_key(item.get("source_platform"))),
        ("price_band_priority", "price_band", lambda item: normalize_text(item.get("price_band"))),
        ("efficacy_score", "efficacy", lambda item: normalize_text(item.get("efficacy_tags"))),
        ("first_order_base_qty", "structural_role", lambda item: normalize_text(item.get("proposed_role"))),
    ]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in queue:
            key = extractor(item)
            if key:
                grouped[key].append(item)
        for key, items in grouped.items():
            items.sort(key=lambda row: normalize_text((row.get("latest_review") or {}).get("review_date")), reverse=True)
            latest_three = items[:3]
            if len(latest_three) < 3:
                continue
            results = [normalize_text(item.get("latest_review_result")) for item in latest_three]
            if all(result in {"偏弱", "失败"} for result in results):
                groups.append((proposal_type, scope_type, key, latest_three))
            elif all(result == "优于预期" for result in results):
                groups.append((proposal_type, scope_type, key, latest_three))

    built: list[dict[str, Any]] = []
    existing_map = {normalize_text(row.get("proposal_key")): row for row in self.db.list_review_feedback_proposals()}
    for proposal_type, scope_type, scope_key, items in groups:
        positive = all(normalize_text(item.get("latest_review_result")) == "优于预期" for item in items)
        payload = _proposal_payload(proposal_type, scope_type, scope_key, positive)
        evidence_summary = "；".join(
            f"{normalize_text(item.get('brand'))}-{normalize_text(item.get('product_name'))} {normalize_text(item.get('latest_review_result'))}"
            for item in items
        )
        existing = existing_map.get(payload["proposal_key"], {})
        saved = self.db.save_review_feedback_proposal(
            proposal_key=payload["proposal_key"],
            proposal_type=proposal_type,
            scope_type=scope_type,
            scope_key=scope_key,
            title=payload["title"],
            evidence_summary=evidence_summary,
            suggested_value=payload["suggested_value"],
            impact_summary=payload["impact_summary"],
            decision_status=normalize_text(existing.get("decision_status")) or "pending",
            decision_note=normalize_text(existing.get("decision_note")),
        )
        built.append(
            {
                **saved,
                "suggested_value": _json_value(saved.get("suggested_value_json"), {}),
                "evidence_summary": evidence_summary,
            }
        )
    built.sort(key=lambda item: (normalize_text(item.get("decision_status")) != "pending", normalize_text(item.get("proposal_key"))))
    return built


def _strategy_overrides_summary_v3(self: ToolApplication) -> dict[str, Any]:
    rows = _strategy_override_rows(self)
    parsed_rows = [
        {
            **row,
            "value": row.get("value", {}),
        }
        for row in rows
    ]
    return {
        "active_count": len(parsed_rows),
        "rows": parsed_rows,
    }


def _tool_state_v3(self: ToolApplication) -> dict[str, Any]:
    structured_skus = self._structured_skus()
    candidates = self._enriched_candidates(structured_skus)
    dashboard = build_dashboard(structured_skus, candidates)
    candidate_recommendations = sorted(
        candidates,
        key=lambda item: (-item.get("recommendation_score", 0), item.get("brand", ""), item.get("product_name", "")),
    )
    auto_selection = auto_select_candidates(candidate_recommendations, structured_skus)
    procurement = self._build_procurement_state(structured_skus, candidate_recommendations, auto_selection)
    candidate_map = {
        int(item["id"]): item
        for item in candidate_recommendations
        if str(item.get("id", "")).isdigit()
    }
    procurement = _augment_procurement_reviews_v3(self, procurement, candidate_map)
    procurement_actions = _build_procurement_actions_v3(self, structured_skus, candidate_recommendations, auto_selection, procurement)
    feedback_proposals = _generate_feedback_proposals_v3(self, procurement)
    strategy_overrides_summary = _strategy_overrides_summary_v3(self)
    market_reference_rows = [
        {
            "sku_id": item.get("id"),
            "sku_code": item.get("sku_code"),
            "brand": item.get("brand"),
            "product_name": item.get("product_name"),
            "structural_role": item.get("structural_role"),
            "action": item.get("action"),
            "anchor_source": item.get("market_anchor_source"),
            "confidence_score": parse_float(item.get("market_confidence_score")),
            "confidence_level": normalize_text(item.get("market_confidence_level")) or _market_confidence_level(item.get("market_confidence_score")),
            "reference_price": parse_float(item.get("market_reference_price") or item.get("taobao_avg_price")),
            "reference_low": parse_float(item.get("market_reference_low") or item.get("taobao_min_price")),
            "reference_high": parse_float(item.get("market_reference_high") or item.get("taobao_max_price")),
            "sample_status": item.get("market_sample_status"),
            "snapshot_at": item.get("market_snapshot_at"),
            "price_disorder_flag": item.get("price_disorder_flag"),
            "source_snapshots": item.get("market_source_snapshots", []),
        }
        for item in structured_skus
    ]
    return {
        "skus": structured_skus,
        "candidates": candidates,
        "dashboard": dashboard,
        "market_tools": {
            "diagnostics": [
                item
                for item in structured_skus
                if normalize_text(item.get("market_sample_status")) in {"无结果", "被拦截", "样本不足", "近似样本", "跨平台替代", "人工补样本"}
            ],
            "manual_overrides": list(self._manual_override_map().values()),
        },
        "recommendations": {
            "existing": structured_skus,
            "candidate": candidate_recommendations,
            "auto_selection": auto_selection,
        },
        "procurement": procurement,
        "market_reference": {
            "rows": market_reference_rows,
            "summary": {
                "high_confidence_count": sum(1 for row in market_reference_rows if normalize_text(row.get("confidence_level")) == "高"),
                "weak_confidence_count": sum(1 for row in market_reference_rows if normalize_text(row.get("confidence_level")) in {"低", "弱"}),
                "disorder_count": sum(1 for row in market_reference_rows if parse_int(row.get("price_disorder_flag"))),
            },
        },
        "procurement_actions": procurement_actions,
        "feedback_proposals": feedback_proposals,
        "strategy_overrides_summary": strategy_overrides_summary,
    }


def _tool_refresh_market_v3(self: ToolApplication, payload: dict[str, Any]) -> dict[str, Any]:
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
    self.db.save_market_source_snapshots(result["snapshots"])
    structured_lookup = {
        int(item["id"]): item
        for item in self._structured_skus(self.db.list_skus())
        if str(item.get("id", "")).isdigit()
    }
    preview = []
    for snapshot in result["snapshots"][:12]:
        structured = structured_lookup.get(int(snapshot.get("id") or 0), {})
        preview.append(
            {
                "id": snapshot.get("id"),
                "sku_code": snapshot.get("sku_code"),
                "brand": structured.get("brand"),
                "product_name": structured.get("product_name"),
                "structural_role": structured.get("structural_role"),
                "action": structured.get("action"),
                "market_sample_status": snapshot.get("market_sample_status"),
                "market_source_mode": snapshot.get("market_source_mode"),
                "market_anchor_source": snapshot.get("market_anchor_source"),
                "market_confidence_score": snapshot.get("market_confidence_score"),
                "market_confidence_level": snapshot.get("market_confidence_level"),
                "market_reference_price": snapshot.get("market_reference_price"),
                "market_reference_low": snapshot.get("market_reference_low"),
                "market_reference_high": snapshot.get("market_reference_high"),
                "source_snapshots": snapshot.get("source_snapshots", []),
            }
        )
    return {
        "refreshed": result["refreshed"],
        "skipped": result["skipped"],
        "errors": result["errors"],
        "preview": preview,
        "with_samples": sum(1 for item in result["snapshots"] if parse_int(item.get("taobao_sample_count")) > 0),
        "without_samples": sum(1 for item in result["snapshots"] if parse_int(item.get("taobao_sample_count")) <= 0),
        "confidence_breakdown": Counter(normalize_text(item.get("market_confidence_level")) or "弱" for item in result["snapshots"]),
    }


def _tool_update_procurement_action_v3(self: ToolApplication, action_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    state = self.state()
    current = next((item for item in state["procurement_actions"]["all"] if normalize_text(item.get("action_key")) == action_key), None)
    if not current:
        raise ValueError("未找到要更新的采购动作。")
    merged_payload = {
        **_editable_action_payload(current),
        "recommended_price": round(parse_float(payload.get("recommended_price")) or parse_float(current.get("recommended_price")), 2),
        "suggested_first_order_qty": max(0, parse_int(payload.get("suggested_first_order_qty")) or parse_int(current.get("suggested_first_order_qty"))),
        "review_cycle_days": max(1, parse_int(payload.get("review_cycle_days")) or parse_int(current.get("review_cycle_days")) or 14),
        "status": normalize_text(payload.get("status")) or normalize_text(current.get("status")) or "待确认",
        "notes": normalize_text(payload.get("notes")) or normalize_text(current.get("notes")),
    }
    merged_payload["planned_budget"] = round(parse_float(current.get("expected_purchase_price")) * max(parse_int(merged_payload.get("suggested_first_order_qty")), 0), 2) if normalize_text(current.get("item_type")) == "candidate" else 0.0
    saved = self.db.save_procurement_action_item(
        action_key=action_key,
        item_type=normalize_text(current.get("item_type")),
        item_id=normalize_text(current.get("item_id")),
        payload=merged_payload,
        status=normalize_text(merged_payload.get("status")) or "待确认",
        notes=normalize_text(merged_payload.get("notes")),
    )
    refreshed = self.state()
    updated = next((item for item in refreshed["procurement_actions"]["all"] if normalize_text(item.get("action_key")) == action_key), None)
    return {"ok": True, "saved": saved, "action": updated}


def _tool_decide_feedback_proposal_v3(self: ToolApplication, proposal_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = self.db.get_review_feedback_proposal(proposal_key)
    if not current:
        self.state()
        current = self.db.get_review_feedback_proposal(proposal_key)
    if not current:
        raise ValueError("未找到要处理的规则修正提案。")
    decision = normalize_text(payload.get("decision")) or "rejected"
    decision = "accepted" if decision in {"accepted", "confirm", "确认", "确认生效"} else "rejected"
    note = normalize_text(payload.get("note"))
    suggested_value = _json_value(current.get("suggested_value_json"), {})
    saved = self.db.save_review_feedback_proposal(
        proposal_key=proposal_key,
        proposal_type=normalize_text(current.get("proposal_type")),
        scope_type=normalize_text(current.get("scope_type")),
        scope_key=normalize_text(current.get("scope_key")),
        title=normalize_text(current.get("title")),
        evidence_summary=normalize_text(current.get("evidence_summary")),
        suggested_value=suggested_value if isinstance(suggested_value, dict) else {},
        impact_summary=normalize_text(current.get("impact_summary")),
        decision_status=decision,
        decision_note=note,
    )
    if decision == "accepted":
        self.db.save_strategy_override(
            override_key=proposal_key,
            override_type=normalize_text(current.get("proposal_type")),
            scope_type=normalize_text(current.get("scope_type")),
            scope_key=normalize_text(current.get("scope_key")),
            value={**(suggested_value if isinstance(suggested_value, dict) else {}), "active": True},
            source_proposal_key=proposal_key,
        )
    else:
        self.db.save_strategy_override(
            override_key=proposal_key,
            override_type=normalize_text(current.get("proposal_type")),
            scope_type=normalize_text(current.get("scope_type")),
            scope_key=normalize_text(current.get("scope_key")),
            value={**(suggested_value if isinstance(suggested_value, dict) else {}), "active": False},
            source_proposal_key=proposal_key,
        )
    refreshed = self.state()
    proposal = next((item for item in refreshed["feedback_proposals"] if normalize_text(item.get("proposal_key")) == proposal_key), None)
    return {"ok": True, "proposal": proposal, "strategy_overrides_summary": refreshed["strategy_overrides_summary"]}


ToolApplication.meta = _tool_meta_v3
ToolApplication._merge_market_context = _tool_merge_market_context_v3
ToolApplication.state = _tool_state_v3
ToolApplication.refresh_market = _tool_refresh_market_v3
ToolApplication.update_procurement_action = _tool_update_procurement_action_v3
ToolApplication.decide_feedback_proposal = _tool_decide_feedback_proposal_v3


_REQUEST_HANDLER_DO_POST = RequestHandler.do_POST
_REQUEST_HANDLER_DO_PUT = RequestHandler.do_PUT


def _request_handler_do_post_v3(self: RequestHandler) -> None:
    parsed = urlparse(self.path)
    try:
        if parsed.path.startswith("/api/review-feedback/proposals/") and parsed.path.endswith("/decision"):
            proposal_key = parsed.path.split("/")[4]
            payload = self._read_json()
            self._send_json(APP.decide_feedback_proposal(proposal_key, payload), status=HTTPStatus.CREATED)
            return
    except Exception as exc:  # pragma: no cover - HTTP boundary
        self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return
    _REQUEST_HANDLER_DO_POST(self)


def _request_handler_do_put_v3(self: RequestHandler) -> None:
    parsed = urlparse(self.path)
    try:
        if parsed.path.startswith("/api/procurement-actions/"):
            action_key = parsed.path.split("/")[3]
            payload = self._read_json()
            self._send_json(APP.update_procurement_action(action_key, payload))
            return
    except Exception as exc:  # pragma: no cover - HTTP boundary
        self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return
    _REQUEST_HANDLER_DO_PUT(self)


RequestHandler.do_POST = _request_handler_do_post_v3
RequestHandler.do_PUT = _request_handler_do_put_v3


STRATEGIC_REVIEW_POSITIVE = {"优于预期", "达标"}
STRATEGIC_REVIEW_NEGATIVE = {"偏弱", "失败"}
DEFAULT_ROLE_TARGETS_V4 = {"引流品": 0.25, "常规品": 0.60, "利润品": 0.15}


def _safe_iso_date_v4(value: Any) -> date | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _days_since_v4(value: Any) -> int | None:
    parsed = _safe_iso_date_v4(value)
    if not parsed:
        return None
    return (date.today() - parsed).days


def _snapshot_price_v4(snapshot: dict[str, Any]) -> float:
    for key in ("reference_price", "median_price", "trimmed_mean_price", "p90_price", "p10_price"):
        number = parse_float(snapshot.get(key))
        if number > 0:
            return number
    return 0.0


def _evidence_tier_v4(snapshot: dict[str, Any]) -> str:
    platform_key = _platform_key(snapshot.get("source_platform"))
    capture_mode = normalize_text(snapshot.get("capture_mode"))
    status = normalize_text(snapshot.get("status"))
    details = snapshot.get("details") if isinstance(snapshot.get("details"), dict) else {}
    if capture_mode in {"external_feed", "verified_api"} or details.get("verified_source"):
        return "一级证据"
    if status == "人工补样本" or capture_mode in {"browser_assisted", "manual_override", "manual"} or details.get("confirmed"):
        return "二级证据"
    if platform_key in {"taobao", "tmall", "jd"} and parse_float(snapshot.get("confidence_score")) >= 75:
        return "一级证据"
    return "三级证据"


def _evidence_rank_v4(label: str) -> int:
    return {"一级证据": 3, "二级证据": 2, "三级证据": 1}.get(normalize_text(label), 0)


def _evidence_reason_v4(snapshot: dict[str, Any]) -> str:
    confidence = parse_float(snapshot.get("confidence_score"))
    status = normalize_text(snapshot.get("status")) or "无结果"
    sample_count = parse_int(snapshot.get("sample_count"))
    platform_label = _platform_label_from_key(snapshot.get("source_platform"))
    if normalize_text(_evidence_tier_v4(snapshot)) == "一级证据":
        return f"{platform_label} 样本 {sample_count} 个，匹配度较高，当前可信度 {confidence:.0f} 分。"
    if normalize_text(_evidence_tier_v4(snapshot)) == "二级证据":
        return f"{platform_label} 采用浏览器辅助或人工确认样本，状态为“{status}”，可信度 {confidence:.0f} 分。"
    return f"{platform_label} 主要来自公开页抓取或降级匹配，状态为“{status}”，暂只作参考。"


def _current_market_snapshot_v4(sku: dict[str, Any]) -> dict[str, Any]:
    snapshots = [
        item
        for item in (sku.get("market_source_snapshots") or [])
        if _platform_key(item.get("source_platform")) not in {"aggregate"}
    ]
    if not snapshots:
        return {}
    snapshots.sort(
        key=lambda item: (
            -_evidence_rank_v4(_evidence_tier_v4(item)),
            -parse_float(item.get("confidence_score")),
            normalize_text(item.get("captured_at")),
        )
    )
    return snapshots[0]


def _market_reference_rows_v4(structured_skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sku in structured_skus:
        snapshot = _current_market_snapshot_v4(sku)
        evidence_tier = _evidence_tier_v4(snapshot) if snapshot else "三级证据"
        evidence_reason = _evidence_reason_v4(snapshot) if snapshot else "当前还没有稳定的市场快照，只能先按门店结构和人工补样本判断。"
        price_points = [
            _snapshot_price_v4(item)
            for item in (sku.get("market_source_snapshots") or [])
            if _platform_key(item.get("source_platform")) not in {"aggregate"} and _snapshot_price_v4(item) > 0
        ]
        conflict_ratio = (max(price_points) / max(min(price_points), 0.01)) if len(price_points) >= 2 else 1.0
        rows.append(
            {
                "sku_id": sku.get("id"),
                "sku_code": sku.get("sku_code"),
                "brand": sku.get("brand"),
                "product_name": sku.get("product_name"),
                "structural_role": sku.get("structural_role"),
                "action": sku.get("action"),
                "anchor_source": sku.get("market_anchor_source") or _platform_label_from_key(snapshot.get("source_platform")) if snapshot else "",
                "confidence_score": parse_float(sku.get("market_confidence_score")),
                "confidence_level": normalize_text(sku.get("market_confidence_level")) or _market_confidence_level(sku.get("market_confidence_score")),
                "reference_price": parse_float(sku.get("market_reference_price") or sku.get("taobao_avg_price")),
                "reference_low": parse_float(sku.get("market_reference_low") or sku.get("taobao_min_price")),
                "reference_high": parse_float(sku.get("market_reference_high") or sku.get("taobao_max_price")),
                "sample_status": normalize_text(sku.get("market_sample_status")),
                "snapshot_at": normalize_text(sku.get("market_snapshot_at")),
                "price_disorder_flag": parse_int(sku.get("price_disorder_flag")),
                "source_snapshots": sku.get("market_source_snapshots", []),
                "evidence_tier": evidence_tier,
                "evidence_reason": evidence_reason,
                "source_platform": _platform_key(snapshot.get("source_platform")) if snapshot else "",
                "source_platform_label": _platform_label_from_key(snapshot.get("source_platform")) if snapshot else "",
                "conflict_ratio": round(conflict_ratio, 2),
                "conflict_note": "多来源价格存在明显分歧，已按证据等级和新鲜度选择主锚点。" if conflict_ratio >= 1.18 else "多来源价格基本一致。",
            }
        )
    return rows


def _maybe_save_competitor_series_v4(self: ToolApplication, *, item_key: str, brand: str, source_platform: str, price: float, heat_score: float) -> None:
    today_text = date.today().isoformat()
    if price > 0:
        existing_price_rows = self.db.list_market_price_series(item_key=item_key)
        if not existing_price_rows or normalize_text(existing_price_rows[0].get("captured_at"))[:10] != today_text:
            self.db.add_market_price_point(
                item_key=item_key,
                brand=brand,
                source_platform=source_platform,
                price=price,
                confidence_score=78.0 if source_platform in {"taobao", "tmall", "jd"} else 55.0,
                evidence_tier="一级证据" if source_platform in {"taobao", "tmall", "jd"} else "三级证据",
                captured_at=now_iso(),
            )
    if heat_score > 0:
        existing_heat_rows = self.db.list_market_heat_series(item_key=item_key)
        if not existing_heat_rows or normalize_text(existing_heat_rows[0].get("captured_at"))[:10] != today_text:
            self.db.add_market_heat_point(
                item_key=item_key,
                brand=brand,
                source_platform=source_platform,
                heat_score=heat_score,
                keyword="牙膏",
                captured_at=now_iso(),
            )


def _upsert_competitor_items_v4(self: ToolApplication, candidates: list[dict[str, Any]], brand_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_brand_card = {normalize_text(item.get("brand")): item for item in brand_cards}
    saved_items: list[dict[str, Any]] = []
    for candidate in candidates[:120]:
        brand = normalize_text(candidate.get("brand"))
        product_name = normalize_text(candidate.get("product_name"))
        if not brand or not product_name:
            continue
        item_key = f"candidate:{normalize_text(candidate.get('id')) or brand + ':' + product_name}"
        gap_type = normalize_text(candidate.get("gap_type")) or normalize_text(candidate.get("recommendation_action"))
        card = by_brand_card.get(brand, {})
        saved = self.db.save_competitor_item(
            item_key=item_key,
            brand=brand,
            product_name=product_name,
            spec_text=normalize_text(candidate.get("spec_text")),
            source_platform=_platform_key(candidate.get("source_platform")),
            product_url=normalize_text(candidate.get("product_url")),
            online_price=parse_float(candidate.get("online_reference_price")),
            heat_score=parse_float(candidate.get("heat_score")),
            evidence_tier="一级证据" if _platform_key(candidate.get("source_platform")) in {"taobao", "tmall", "jd"} else "三级证据",
            status="active",
            details={
                "gap_type": gap_type,
                "brand_action": card.get("recommended_action", ""),
                "recommendation_score": parse_float(candidate.get("recommendation_score")),
            },
        )
        saved_items.append(saved)
        _maybe_save_competitor_series_v4(
            self,
            item_key=item_key,
            brand=brand,
            source_platform=_platform_key(candidate.get("source_platform")),
            price=parse_float(candidate.get("online_reference_price")),
            heat_score=parse_float(candidate.get("heat_score")),
        )
    return saved_items


def _build_brand_strategy_cards_v4(
    self: ToolApplication,
    structured_skus: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    overall_avg_sales = (
        sum(parse_int(item.get("six_month_sales")) for item in structured_skus) / max(len(structured_skus), 1)
        if structured_skus
        else 0
    )
    by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sku in structured_skus:
        brand = normalize_text(sku.get("brand"))
        if brand:
            by_brand[brand].append(sku)

    cards: list[dict[str, Any]] = []
    for brand, brand_skus in sorted(by_brand.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        sku_count = len(brand_skus)
        share = round(sku_count / max(len(structured_skus), 1), 4)
        avg_price = round(sum(parse_float(item.get("current_price")) for item in brand_skus) / max(sku_count, 1), 2)
        avg_margin = round(sum(parse_float(item.get("gross_margin")) for item in brand_skus) / max(sku_count, 1), 4)
        avg_sales = round(sum(parse_int(item.get("six_month_sales")) for item in brand_skus) / max(sku_count, 1), 1)
        role_counter = Counter(normalize_text(item.get("structural_role")) for item in brand_skus)
        missing_hits = recommend_brand_missing_hits(
            brand=brand,
            current_brand_skus=brand_skus,
            all_skus=structured_skus,
            candidates=candidates,
            limit=3,
        )
        recent_heat = round(
            sum(parse_float(item.get("heat_score")) for item in missing_hits) / max(len(missing_hits), 1),
            1,
        ) if missing_hits else 0.0

        if sku_count == 1 and share <= 0.1:
            current_role = "补位品牌"
        elif role_counter.get("引流品", 0) >= max(role_counter.get("常规品", 0), role_counter.get("利润品", 0)):
            current_role = "引流品牌"
        elif role_counter.get("利润品", 0) >= max(role_counter.get("常规品", 0), role_counter.get("引流品", 0)):
            current_role = "利润品牌"
        else:
            current_role = "常规品牌"

        overstocked = share >= 0.35 or (sku_count >= 4 and avg_sales < overall_avg_sales)
        depth_gap = max(0, 3 - sku_count)
        if overstocked and not missing_hits:
            recommended_action = "建议收缩"
        elif missing_hits and recent_heat >= 78:
            recommended_action = "建议扩品"
        elif current_role == "引流品牌" and avg_margin >= 0.34:
            recommended_action = "建议转定位"
        else:
            recommended_action = "建议维持"

        card = {
            "brand": brand,
            "current_role": current_role,
            "sku_count": sku_count,
            "share": share,
            "avg_price": avg_price,
            "avg_margin": avg_margin,
            "avg_sales": avg_sales,
            "target_depth": max(2, sku_count + min(depth_gap, len(missing_hits))),
            "depth_gap": depth_gap,
            "missing_hits": missing_hits,
            "missing_hit_count": len(missing_hits),
            "recent_heat": recent_heat,
            "overstocked": overstocked,
            "recommended_action": recommended_action,
            "strategy_note": (
                f"{brand} 当前在店 {sku_count} 个 SKU，角色偏向{current_role}；"
                f"{'有同品牌缺失爆款，建议扩品。' if missing_hits and recommended_action == '建议扩品' else ''}"
                f"{'铺货偏深且动销一般，建议收缩。' if overstocked and recommended_action == '建议收缩' else ''}"
                f"{'当前适合维持现有深度。' if recommended_action == '建议维持' else ''}"
            ),
        }
        self.db.save_brand_strategy_profile(
            brand=brand,
            role=current_role,
            recommended_action=recommended_action,
            target_depth=card["target_depth"],
            notes=card["strategy_note"],
            payload=card,
        )
        cards.append(card)

    cards.sort(
        key=lambda item: (
            item.get("recommended_action") != "建议扩品",
            -parse_float(item.get("recent_heat")),
            -parse_int(item.get("missing_hit_count")),
            -parse_float(item.get("share")),
            item.get("brand", ""),
        )
    )
    return cards


def _category_targets_v4(self: ToolApplication) -> dict[str, Any]:
    targets: dict[str, Any] = {
        "role_mix": DEFAULT_ROLE_TARGETS_V4,
        "brand_max_share": 0.35,
        "price_band_min_count": 1,
    }
    for row in self.db.list_category_strategy_targets():
        target_type = normalize_text(row.get("target_type"))
        value = _json_value(row.get("target_value_json"), {})
        if target_type == "role_mix" and isinstance(value, dict):
            targets["role_mix"] = {**targets["role_mix"], **value}
        elif target_type == "brand_max_share" and isinstance(value, dict):
            targets["brand_max_share"] = parse_float(value.get("value")) or targets["brand_max_share"]
        elif target_type == "price_band_min_count" and isinstance(value, dict):
            targets["price_band_min_count"] = parse_int(value.get("value")) or targets["price_band_min_count"]
    return targets


def _build_category_strategy_v4(
    self: ToolApplication,
    structured_skus: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    dashboard: dict[str, Any],
    brand_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    targets = _category_targets_v4(self)
    role_counts = Counter(normalize_text(item.get("structural_role")) for item in structured_skus)
    role_mix = []
    total_skus = max(len(structured_skus), 1)
    for role, target_share in targets["role_mix"].items():
        current_count = role_counts.get(role, 0)
        current_share = round(current_count / total_skus, 4)
        target_count = round(target_share * total_skus, 2)
        role_mix.append(
            {
                "role": role,
                "current_count": current_count,
                "current_share": current_share,
                "target_share": target_share,
                "target_count": target_count,
                "gap": round(current_share - target_share, 4),
            }
        )

    market_band_counter = Counter(
        normalize_text(item.get("price_band"))
        for item in candidates
        if normalize_text(item.get("price_band"))
    )
    current_band_rows = dashboard.get("price_band_distribution", [])
    price_architecture = []
    for row in current_band_rows:
        band = normalize_text(row.get("label"))
        current_count = parse_int(row.get("count"))
        market_count = parse_int(market_band_counter.get(band))
        price_architecture.append(
            {
                "band": band,
                "current_count": current_count,
                "market_count": market_count,
                "gap_count": market_count - current_count,
                "disorder_count": parse_int(row.get("disorder_count")),
                "decision_role": "打价格" if band in {"<=9.9", "10-14.9"} else "守利润" if band in {">=40", "30-39.9"} else "常规承接",
            }
        )

    brand_distribution = dashboard.get("brand_distribution", [])
    top_brand = brand_distribution[0] if brand_distribution else {}
    strategic_actions = {
        "expand_brands": [item for item in brand_cards if item.get("recommended_action") == "建议扩品"][:3],
        "shrink_brands": [item for item in brand_cards if item.get("recommended_action") == "建议收缩"][:3],
        "focus_efficacies": [
            {"label": gap.replace("缺口明显", "").replace("覆盖不足", "").strip("，。 "), "reason": gap}
            for gap in dashboard.get("structure_gaps", [])
            if any(keyword in gap for keyword in ("功效", "护理", "儿童", "美白", "抗敏", "清新"))
        ][:4],
        "monitor_price_bands": [item for item in price_architecture if parse_int(item.get("current_count")) <= targets["price_band_min_count"] or parse_int(item.get("disorder_count")) > 0][:4],
    }
    strategic_actions_flat = []
    strategic_actions_flat.extend(
        {
            "type": "品牌扩张",
            "label": item.get("brand"),
            "summary": item.get("strategy_note"),
        }
        for item in strategic_actions["expand_brands"]
    )
    strategic_actions_flat.extend(
        {
            "type": "品牌收缩",
            "label": item.get("brand"),
            "summary": item.get("strategy_note"),
        }
        for item in strategic_actions["shrink_brands"]
    )
    strategic_actions_flat.extend(
        {
            "type": "重点补位功效",
            "label": item.get("label"),
            "summary": item.get("reason"),
        }
        for item in strategic_actions["focus_efficacies"]
    )
    strategic_actions_flat.extend(
        {
            "type": "重点监控价格带",
            "label": item.get("band"),
            "summary": f"当前 {item.get('current_count')} 个，市场侧 {item.get('market_count')} 个候选，建议关注 {item.get('decision_role')}。",
        }
        for item in strategic_actions["monitor_price_bands"]
    )
    return {
        "summary": {
            "sku_count": len(structured_skus),
            "brand_count": len(brand_distribution),
            "top_brand": top_brand.get("brand", ""),
            "top_brand_share": top_brand.get("share", 0),
            "structure_gap_count": len(dashboard.get("structure_gaps", [])),
            "target_brand_max_share": targets["brand_max_share"],
            "brand_concentration_risk": bool(brand_distribution and parse_float(top_brand.get("share")) > targets["brand_max_share"]),
        },
        "targets": targets,
        "role_mix": role_mix,
        "price_architecture": price_architecture,
        "efficacy_map": dashboard.get("efficacy_distribution", []),
        "brand_distribution": brand_distribution,
        "structure_gaps": dashboard.get("structure_gaps", []),
        "strategic_actions": strategic_actions,
        "strategic_actions_flat": strategic_actions_flat,
    }


def _build_market_events_v4(
    self: ToolApplication,
    structured_skus: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    brand_cards: list[dict[str, Any]],
    market_reference_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    today_text = date.today().isoformat()
    built: list[dict[str, Any]] = []
    for card in brand_cards[:12]:
        brand = normalize_text(card.get("brand"))
        if parse_int(card.get("missing_hit_count")) > 0 and parse_float(card.get("recent_heat")) >= 80:
            built.append(
                self.db.save_market_event(
                    event_key=f"brand-hot:{brand}",
                    event_type="brand_hot_gap",
                    brand=brand,
                    title=f"{brand} 存在同品牌缺失爆款",
                    severity="high",
                    summary=f"{brand} 当前缺 {card.get('missing_hit_count')} 个重点爆款，建议优先补同品牌热销款。",
                    details={"brand": brand, "recent_heat": card.get("recent_heat"), "missing_hits": card.get("missing_hits", [])[:2]},
                    event_date=today_text,
                )
            )
        if normalize_text(card.get("recommended_action")) == "建议收缩":
            built.append(
                self.db.save_market_event(
                    event_key=f"brand-shrink:{brand}",
                    event_type="brand_concentration",
                    brand=brand,
                    title=f"{brand} 铺货偏深",
                    severity="medium",
                    summary=f"{brand} 当前铺货深度偏高，建议复核是否有收缩空间。",
                    details={"brand": brand, "share": card.get("share"), "sku_count": card.get("sku_count")},
                    event_date=today_text,
                )
            )

    for row in market_reference_rows:
        if parse_int(row.get("price_disorder_flag")) and parse_float(row.get("confidence_score")) >= 60:
            built.append(
                self.db.save_market_event(
                    event_key=f"price:{normalize_text(row.get('sku_code'))}",
                    event_type="price_disorder",
                    brand=normalize_text(row.get("brand")),
                    title=f"{normalize_text(row.get('brand'))} 价格异动",
                    severity="medium",
                    summary=f"{normalize_text(row.get('product_name'))} 线上价格带较乱，建议谨慎定价。",
                    details={"sku_code": row.get("sku_code"), "reference_low": row.get("reference_low"), "reference_high": row.get("reference_high")},
                    event_date=today_text,
                )
            )

    efficacy_heat = defaultdict(list)
    for candidate in candidates:
        efficacy_key = normalize_text(candidate.get("efficacy_tags"))
        if efficacy_key:
            efficacy_heat[efficacy_key].append(parse_float(candidate.get("heat_score")))
    for efficacy_key, scores in efficacy_heat.items():
        if len(scores) >= 2 and sum(scores) / len(scores) >= 82:
            built.append(
                self.db.save_market_event(
                    event_key=f"efficacy:{efficacy_key}",
                    event_type="efficacy_rising",
                    brand="",
                    title=f"{efficacy_key} 赛道升温",
                    severity="high",
                    summary=f"{efficacy_key} 相关候选近期热度较高，建议纳入重点监控。",
                    details={"efficacy": efficacy_key, "avg_heat": round(sum(scores) / len(scores), 1)},
                    event_date=today_text,
                )
            )
    events = self.db.list_market_events()
    events.sort(key=lambda item: (normalize_text(item.get("event_date")), normalize_text(item.get("updated_at"))), reverse=True)
    return events


def _build_competitor_watch_v4(
    self: ToolApplication,
    structured_skus: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    brand_cards: list[dict[str, Any]],
    market_reference_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    saved_items = _upsert_competitor_items_v4(self, candidates, brand_cards)
    events = _build_market_events_v4(self, structured_skus, candidates, brand_cards, market_reference_rows)
    raw_watchlists = self.db.list_brand_watchlists()
    watchlists = [
        {
            **row,
            "source_platforms": _json_value(row.get("source_platforms_json"), []),
            "active": bool(parse_int(row.get("active"))),
        }
        for row in raw_watchlists
    ]
    suggested_watch_brands = [
        {
            "brand": item.get("brand"),
            "reason": item.get("strategy_note"),
        }
        for item in brand_cards
        if item.get("recommended_action") in {"建议扩品", "建议收缩"}
    ][:5]

    def _period_count(days: int) -> int:
        lower = date.today() - timedelta(days=days)
        return sum(1 for row in events if (_safe_iso_date_v4(row.get("event_date")) or date.min) >= lower)

    return {
        "summary": {
            "tracked_brand_count": sum(1 for item in watchlists if item.get("active")),
            "event_count": len(events),
            "new_hot_count": sum(1 for item in events if normalize_text(item.get("event_type")) == "brand_hot_gap"),
            "price_move_count": sum(1 for item in events if normalize_text(item.get("event_type")) == "price_disorder"),
            "rising_efficacy_count": sum(1 for item in events if normalize_text(item.get("event_type")) == "efficacy_rising"),
        },
        "watchlists": watchlists,
        "suggested_watch_brands": suggested_watch_brands,
        "events": events[:30],
        "period_views": {
            "7d": {"event_count": _period_count(7)},
            "30d": {"event_count": _period_count(30)},
            "90d": {"event_count": _period_count(90)},
        },
        "items": saved_items[:40],
    }


def _sync_review_evidence_pool_v4(self: ToolApplication, procurement: dict[str, Any]) -> list[dict[str, Any]]:
    queue = procurement.get("launch_queue", [])
    for item in queue:
        candidate_id = parse_int(item.get("candidate_id"))
        for log in item.get("review_logs", []) or []:
            review_id = parse_int(log.get("id"))
            if not review_id or not candidate_id:
                continue
            evidence_key = f"candidate-review:{review_id}"
            self.db.upsert_review_evidence(
                evidence_key=evidence_key,
                candidate_id=candidate_id,
                item_type="candidate",
                item_id=str(candidate_id),
                brand=normalize_text(item.get("brand")),
                product_name=normalize_text(item.get("product_name")),
                source_platform=_platform_key(item.get("source_platform")),
                price_band=normalize_text(item.get("price_band")),
                efficacy_tags=normalize_text(item.get("efficacy_tags")),
                structural_role=normalize_text(item.get("proposed_role")),
                action_type=normalize_text(item.get("suggested_action")),
                review_cycle_days=parse_int(log.get("cycle_days") or item.get("review_cycle_days")),
                review_result=normalize_text(log.get("review_result")),
                sell_through=parse_float(log.get("sell_through")),
                sales_units=parse_int(log.get("sales_units")),
                sales_amount=parse_float(log.get("sales_amount")),
                gross_margin_rate=parse_float(log.get("gross_margin_rate")),
                evidence_date=normalize_text(log.get("review_date")),
                details={
                    "target_sell_through": parse_float(log.get("target_sell_through")),
                    "decision": normalize_text(log.get("decision")),
                    "launch_status": normalize_text(item.get("launch_status")),
                },
            )
    rows = self.db.list_review_evidence_pool()
    parsed_rows = []
    for row in rows:
        parsed_rows.append({**row, "details": _json_value(row.get("details_json"), {})})
    return parsed_rows


def _build_strategic_learning_proposals_v4(self: ToolApplication, evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_specs = [
        ("brand_strategy_adjustment", "brand", lambda row: normalize_text(row.get("brand"))),
        ("price_band_width_adjustment", "price_band", lambda row: normalize_text(row.get("price_band"))),
        ("efficacy_priority_adjustment", "efficacy", lambda row: normalize_text(row.get("efficacy_tags"))),
        ("competitor_watch_adjustment", "platform", lambda row: normalize_text(row.get("source_platform"))),
    ]
    proposals: list[dict[str, Any]] = []
    for proposal_type, scope_type, extractor in grouped_specs:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in evidence_rows:
            key = extractor(row)
            if key:
                grouped[key].append(row)
        for key, rows in grouped.items():
            rows = sorted(rows, key=lambda item: normalize_text(item.get("evidence_date")), reverse=True)
            if len(rows) < 3:
                continue
            latest = rows[:3]
            results = [normalize_text(item.get("review_result")) for item in latest]
            if all(result in STRATEGIC_REVIEW_NEGATIVE for result in results):
                direction = "down"
            elif all(result in STRATEGIC_REVIEW_POSITIVE for result in results):
                direction = "up"
            else:
                continue
            title_map = {
                "brand_strategy_adjustment": f"建议{'扩张' if direction == 'up' else '收缩'} {key} 品牌策略",
                "price_band_width_adjustment": f"建议{'加宽' if direction == 'up' else '收窄'} {key} 价格带策略",
                "efficacy_priority_adjustment": f"建议{'上调' if direction == 'up' else '下调'} {key} 功效优先级",
                "competitor_watch_adjustment": f"建议{'加强' if direction == 'up' else '放缓'} {key} 监控名单权重",
            }
            suggested_value = {
                "direction": direction,
                "active": True,
                "scope_key": key,
                "proposal_family": proposal_type,
            }
            proposal_key = f"{proposal_type}:{key}"
            saved = self.db.save_review_feedback_proposal(
                proposal_key=proposal_key,
                proposal_type=proposal_type,
                scope_type=scope_type,
                scope_key=key,
                title=title_map[proposal_type],
                evidence_summary="；".join(
                    f"{normalize_text(item.get('brand'))}-{normalize_text(item.get('product_name'))} {normalize_text(item.get('review_result'))}"
                    for item in latest
                ),
                suggested_value=suggested_value,
                impact_summary="该提案来自连续 3 条同范围复盘结果，用来修正更长期的品牌/价格带/功效/监控策略。",
                decision_status=(self.db.get_review_feedback_proposal(proposal_key) or {}).get("decision_status", "pending"),
                decision_note=(self.db.get_review_feedback_proposal(proposal_key) or {}).get("decision_note", ""),
            )
            proposals.append({**saved, "suggested_value": suggested_value})
    return proposals


def _build_learning_summary_v4(
    self: ToolApplication,
    procurement: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    feedback_proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    def _window_rows(days: int) -> list[dict[str, Any]]:
        lower = date.today() - timedelta(days=days)
        return [row for row in evidence_rows if (_safe_iso_date_v4(row.get("evidence_date")) or date.min) >= lower]

    window_30 = _window_rows(30)
    window_90 = _window_rows(90)
    success_rate_30 = round(sum(1 for row in window_30 if normalize_text(row.get("review_result")) in STRATEGIC_REVIEW_POSITIVE) / max(len(window_30), 1), 4) if window_30 else 0
    by_platform = defaultdict(list)
    by_role = defaultdict(list)
    by_efficacy = defaultdict(list)
    by_brand = defaultdict(list)
    for row in evidence_rows:
        result = normalize_text(row.get("review_result"))
        by_platform[normalize_text(row.get("source_platform"))].append(result)
        by_role[normalize_text(row.get("structural_role"))].append(result)
        by_efficacy[normalize_text(row.get("efficacy_tags"))].append(result)
        by_brand[normalize_text(row.get("brand"))].append(result)

    def _success_summary(grouped: dict[str, list[str]]) -> list[dict[str, Any]]:
        rows = []
        for key, values in grouped.items():
            if not key:
                continue
            rows.append(
                {
                    "label": key,
                    "sample_count": len(values),
                    "success_rate": round(sum(1 for item in values if item in STRATEGIC_REVIEW_POSITIVE) / max(len(values), 1), 4),
                }
            )
        rows.sort(key=lambda item: (-item["success_rate"], -item["sample_count"], item["label"]))
        return rows

    launch_queue = procurement.get("launch_queue", [])
    launched_recent_90 = [
        item
        for item in launch_queue
        if (_safe_iso_date_v4(item.get("actual_launch_date")) or date.min) >= date.today() - timedelta(days=90)
    ]
    survival_90 = round(
        sum(1 for item in launched_recent_90 if normalize_text(item.get("launch_status")) in {"launched", "observing", "replenished"}) / max(len(launched_recent_90), 1),
        4,
    ) if launched_recent_90 else 0

    strategic_feedback = _build_strategic_learning_proposals_v4(self, evidence_rows)
    combined_proposals = {normalize_text(item.get("proposal_key")): item for item in feedback_proposals}
    for item in strategic_feedback:
        combined_proposals[normalize_text(item.get("proposal_key"))] = item

    return {
        "summary": {
            "evidence_count": len(evidence_rows),
            "success_rate_30d": success_rate_30,
            "survival_rate_90d": survival_90,
            "pending_proposal_count": sum(1 for item in combined_proposals.values() if normalize_text(item.get("decision_status")) == "pending"),
        },
        "platform_effectiveness": _success_summary(by_platform),
        "role_performance": _success_summary(by_role),
        "efficacy_performance": _success_summary(by_efficacy),
        "brand_performance": _success_summary(by_brand)[:8],
        "long_term_dashboard": {
            "window_30_count": len(window_30),
            "window_90_count": len(window_90),
            "success_rate_30d": success_rate_30,
            "survival_rate_90d": survival_90,
        },
        "feedback_proposals": list(combined_proposals.values()),
    }


def _build_market_intelligence_v4(
    market_reference_rows: list[dict[str, Any]],
    competitor_watch: dict[str, Any],
) -> dict[str, Any]:
    weak_rows = [row for row in market_reference_rows if normalize_text(row.get("confidence_level")) in {"低", "弱"}]
    conflict_rows = [row for row in market_reference_rows if parse_float(row.get("conflict_ratio")) >= 1.18]
    return {
        "summary": {
            "row_count": len(market_reference_rows),
            "weak_confidence_count": len(weak_rows),
            "conflict_count": len(conflict_rows),
            "event_count": parse_int((competitor_watch.get("summary") or {}).get("event_count")),
        },
        "highlights": {
            "weekly_new_hits": parse_int((competitor_watch.get("summary") or {}).get("new_hot_count")),
            "weekly_price_moves": parse_int((competitor_watch.get("summary") or {}).get("price_move_count")),
            "rising_brands": parse_int((competitor_watch.get("summary") or {}).get("rising_efficacy_count")),
            "data_holes": len(weak_rows),
        },
        "rows": market_reference_rows,
        "weak_rows": weak_rows[:20],
        "conflict_rows": conflict_rows[:20],
    }


def _merge_feedback_proposals_v4(
    self: ToolApplication,
    procurement: dict[str, Any],
    structured_skus: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base_proposals = _generate_feedback_proposals_v3(self, procurement)
    evidence_rows = _sync_review_evidence_pool_v4(self, procurement)
    learning_summary = _build_learning_summary_v4(self, procurement, evidence_rows, base_proposals)
    merged = {normalize_text(item.get("proposal_key")): item for item in base_proposals}
    for item in learning_summary.get("feedback_proposals", []):
        merged[normalize_text(item.get("proposal_key"))] = item
    return list(merged.values())


def _tool_meta_v4(self: ToolApplication) -> dict[str, Any]:
    meta = _tool_meta_v3(self)
    meta.update(
        {
            "workbench_nav": [
                {"target": "overviewModule", "label": "今日决策"},
                {"target": "dashboardModule", "label": "类目战略"},
                {"target": "candidateModule", "label": "竞品情报"},
                {"target": "reviewModule", "label": "复盘学习"},
            ],
            "evidence_tiers": ["一级证据", "二级证据", "三级证据"],
        }
    )
    return meta


def _tool_state_v4(self: ToolApplication) -> dict[str, Any]:
    base = _tool_state_v3(self)
    structured_skus = base["skus"]
    candidates = base["recommendations"]["candidate"]
    dashboard = base["dashboard"]
    procurement = base["procurement"]
    market_reference_rows = _market_reference_rows_v4(structured_skus)
    brand_strategy_cards = _build_brand_strategy_cards_v4(self, structured_skus, candidates)
    category_strategy = _build_category_strategy_v4(self, structured_skus, candidates, dashboard, brand_strategy_cards)
    competitor_watch = _build_competitor_watch_v4(self, structured_skus, candidates, brand_strategy_cards, market_reference_rows)
    market_intelligence = _build_market_intelligence_v4(market_reference_rows, competitor_watch)
    feedback_proposals = _merge_feedback_proposals_v4(self, procurement, structured_skus, candidates)
    evidence_rows = _sync_review_evidence_pool_v4(self, procurement)
    learning_summary = _build_learning_summary_v4(self, procurement, evidence_rows, feedback_proposals)
    base["market_reference"] = {
        "rows": market_reference_rows,
        "summary": {
            "high_confidence_count": sum(1 for row in market_reference_rows if normalize_text(row.get("confidence_level")) == "高"),
            "weak_confidence_count": sum(1 for row in market_reference_rows if normalize_text(row.get("confidence_level")) in {"低", "弱"}),
            "disorder_count": sum(1 for row in market_reference_rows if parse_int(row.get("price_disorder_flag"))),
        },
    }
    base["feedback_proposals"] = feedback_proposals
    base["market_intelligence"] = market_intelligence
    base["category_strategy"] = category_strategy
    base["brand_strategy_cards"] = brand_strategy_cards
    base["competitor_watch"] = competitor_watch
    base["learning_summary"] = learning_summary
    return base


def _tool_save_brand_watchlist_v4(self: ToolApplication, payload: dict[str, Any]) -> dict[str, Any]:
    brand = normalize_text(payload.get("brand"))
    if not brand:
        raise ValueError("请选择要加入观察名单的品牌。")
    source_platforms = payload.get("source_platforms")
    if not isinstance(source_platforms, list) or not source_platforms:
        source_platforms = ["taobao", "tmall", "jd"]
    saved = self.db.save_brand_watchlist(
        brand=brand,
        notes=normalize_text(payload.get("notes")),
        source_platforms=[_platform_key(item) or normalize_text(item) for item in source_platforms if normalize_text(item)],
        active=bool(payload.get("active", True)),
    )
    state = self.state()
    return {"ok": True, "watchlist": {**saved, "source_platforms": _json_value(saved.get("source_platforms_json"), [])}, "competitor_watch": state["competitor_watch"]}


def _tool_competitor_events_v4(self: ToolApplication) -> dict[str, Any]:
    state = self.state()
    return state["competitor_watch"]


def _tool_brand_strategy_detail_v4(self: ToolApplication, brand: str) -> dict[str, Any]:
    normalized_brand = normalize_text(brand)
    if not normalized_brand:
        raise ValueError("请选择要查看的品牌。")
    state = self.state()
    card = next((item for item in state["brand_strategy_cards"] if normalize_text(item.get("brand")) == normalized_brand), None)
    structured_skus = state["skus"]
    current_brand_skus = [item for item in structured_skus if normalize_text(item.get("brand")) == normalized_brand]
    recent_events = [item for item in state["competitor_watch"]["events"] if normalize_text(item.get("brand")) == normalized_brand][:10]
    price_series = self.db.list_market_price_series(brand=normalized_brand)[:20]
    heat_series = self.db.list_market_heat_series(brand=normalized_brand)[:20]
    return {
        "brand": normalized_brand,
        "card": card or {},
        "current_brand_skus": current_brand_skus,
        "missing_brand_hits": (card or {}).get("missing_hits", []),
        "recent_events": recent_events,
        "price_series": price_series,
        "heat_series": heat_series,
    }


def _tool_strategy_rebalance_v4(self: ToolApplication, payload: dict[str, Any]) -> dict[str, Any]:
    saved_targets = []
    if isinstance(payload.get("targets"), list):
        for item in payload.get("targets", []):
            target_key = normalize_text(item.get("target_key")) or f"{normalize_text(item.get('target_type'))}:{normalize_text(item.get('scope_key'))}"
            if not target_key:
                continue
            saved_targets.append(
                self.db.save_category_strategy_target(
                    target_key=target_key,
                    target_type=normalize_text(item.get("target_type")),
                    scope_key=normalize_text(item.get("scope_key")),
                    target_value=item.get("target_value") if isinstance(item.get("target_value"), dict) else {},
                    notes=normalize_text(item.get("notes")),
                )
            )
    elif normalize_text(payload.get("target_type")):
        saved_targets.append(
            self.db.save_category_strategy_target(
                target_key=normalize_text(payload.get("target_key")) or f"{normalize_text(payload.get('target_type'))}:{normalize_text(payload.get('scope_key'))}",
                target_type=normalize_text(payload.get("target_type")),
                scope_key=normalize_text(payload.get("scope_key")),
                target_value=payload.get("target_value") if isinstance(payload.get("target_value"), dict) else {},
                notes=normalize_text(payload.get("notes")),
            )
        )
    state = self.state()
    return {"ok": True, "saved_targets": saved_targets, "category_strategy": state["category_strategy"], "brand_strategy_cards": state["brand_strategy_cards"]}


ToolApplication.meta = _tool_meta_v4
ToolApplication.state = _tool_state_v4
ToolApplication.save_brand_watchlist = _tool_save_brand_watchlist_v4
ToolApplication.competitor_events = _tool_competitor_events_v4
ToolApplication.brand_strategy_detail = _tool_brand_strategy_detail_v4
ToolApplication.strategy_rebalance = _tool_strategy_rebalance_v4


_REQUEST_HANDLER_DO_GET_V4 = RequestHandler.do_GET
_REQUEST_HANDLER_DO_POST_V4 = RequestHandler.do_POST


def _request_handler_do_get_v4(self: RequestHandler) -> None:
    parsed = urlparse(self.path)
    try:
        if parsed.path == "/api/competitor/events":
            self._send_json(APP.competitor_events())
            return
        if parsed.path.startswith("/api/brand-strategy/"):
            brand = unquote(parsed.path.split("/")[3])
            self._send_json(APP.brand_strategy_detail(brand))
            return
    except Exception as exc:  # pragma: no cover - HTTP boundary
        self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return
    _REQUEST_HANDLER_DO_GET_V4(self)


def _request_handler_do_post_v4(self: RequestHandler) -> None:
    parsed = urlparse(self.path)
    try:
        if parsed.path == "/api/competitor/watchlists":
            payload = self._read_json()
            self._send_json(APP.save_brand_watchlist(payload), status=HTTPStatus.CREATED)
            return
        if parsed.path == "/api/strategy/rebalance":
            payload = self._read_json()
            self._send_json(APP.strategy_rebalance(payload), status=HTTPStatus.CREATED)
            return
    except Exception as exc:  # pragma: no cover - HTTP boundary
        self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return
    _REQUEST_HANDLER_DO_POST_V4(self)


RequestHandler.do_GET = _request_handler_do_get_v4
RequestHandler.do_POST = _request_handler_do_post_v4


def run() -> None:
    ensure_directories()
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    print(f"Toothpaste tool running at http://{HOST}:{PORT}")
    print(f"SQLite database: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    run()
