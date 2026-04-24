from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import BACKUP_DIR, DB_PATH, ensure_directories
from .logic import now_iso


SKU_EXTRA_COLUMNS = {
    "structural_role": "TEXT NOT NULL DEFAULT ''",
    "taobao_avg_price": "REAL NOT NULL DEFAULT 0",
    "taobao_min_price": "REAL NOT NULL DEFAULT 0",
    "taobao_max_price": "REAL NOT NULL DEFAULT 0",
    "taobao_sample_count": "INTEGER NOT NULL DEFAULT 0",
    "price_disorder_flag": "INTEGER NOT NULL DEFAULT 0",
    "online_heat_score": "REAL NOT NULL DEFAULT 0",
    "market_snapshot_at": "TEXT NOT NULL DEFAULT ''",
}


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        ensure_directories()
        self.db_path = Path(db_path or DB_PATH)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS skus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku_code TEXT NOT NULL UNIQUE,
                    brand TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    spec_text TEXT NOT NULL,
                    efficacy_tags TEXT NOT NULL,
                    current_price REAL NOT NULL DEFAULT 0,
                    purchase_price REAL NOT NULL DEFAULT 0,
                    gross_margin REAL NOT NULL DEFAULT 0,
                    unit_gross_profit REAL NOT NULL DEFAULT 0,
                    six_month_sales INTEGER NOT NULL DEFAULT 0,
                    supplier TEXT NOT NULL DEFAULT '',
                    case_pack TEXT NOT NULL DEFAULT '',
                    shelf_risk TEXT NOT NULL DEFAULT '',
                    current_role TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    unit_price REAL NOT NULL DEFAULT 0,
                    fluoride INTEGER NOT NULL DEFAULT 0,
                    target_group TEXT NOT NULL DEFAULT '成人',
                    promo_type TEXT NOT NULL DEFAULT '常规款',
                    must_keep INTEGER NOT NULL DEFAULT 0,
                    substitute_relation TEXT NOT NULL DEFAULT '',
                    price_band TEXT NOT NULL DEFAULT '',
                    margin_zone TEXT NOT NULL DEFAULT '',
                    structural_role TEXT NOT NULL DEFAULT '',
                    taobao_avg_price REAL NOT NULL DEFAULT 0,
                    taobao_min_price REAL NOT NULL DEFAULT 0,
                    taobao_max_price REAL NOT NULL DEFAULT 0,
                    taobao_sample_count INTEGER NOT NULL DEFAULT 0,
                    price_disorder_flag INTEGER NOT NULL DEFAULT 0,
                    online_heat_score REAL NOT NULL DEFAULT 0,
                    market_snapshot_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    brand TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    spec_text TEXT NOT NULL,
                    efficacy_tags TEXT NOT NULL,
                    online_reference_price REAL NOT NULL DEFAULT 0,
                    expected_purchase_price REAL NOT NULL DEFAULT 0,
                    source_platform TEXT NOT NULL DEFAULT '其他',
                    product_url TEXT NOT NULL DEFAULT '',
                    heat_score REAL NOT NULL DEFAULT 0,
                    differentiation TEXT NOT NULL DEFAULT '',
                    intended_replace_sku TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    fluoride INTEGER NOT NULL DEFAULT 0,
                    target_group TEXT NOT NULL DEFAULT '成人',
                    promo_type TEXT NOT NULL DEFAULT '常规款',
                    must_keep INTEGER NOT NULL DEFAULT 0,
                    substitute_relation TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    mapping_json TEXT NOT NULL DEFAULT '{}',
                    row_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'previewed',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_diagnostics (
                    sku_id INTEGER PRIMARY KEY,
                    sku_code TEXT NOT NULL DEFAULT '',
                    market_sample_status TEXT NOT NULL DEFAULT '',
                    market_source_mode TEXT NOT NULL DEFAULT '',
                    diagnostic_summary TEXT NOT NULL DEFAULT '',
                    query_logs_json TEXT NOT NULL DEFAULT '[]',
                    blocked_platforms_json TEXT NOT NULL DEFAULT '[]',
                    fallback_note TEXT NOT NULL DEFAULT '',
                    matched_titles_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS manual_market_overrides (
                    sku_id INTEGER PRIMARY KEY,
                    sku_code TEXT NOT NULL DEFAULT '',
                    source_platform TEXT NOT NULL DEFAULT '淘宝',
                    sample_prices_json TEXT NOT NULL DEFAULT '[]',
                    source_urls_json TEXT NOT NULL DEFAULT '[]',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crawl_observations (
                    normalized_key TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_runs INTEGER NOT NULL DEFAULT 0,
                    platform_hits_json TEXT NOT NULL DEFAULT '{}',
                    keyword_hits_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS candidate_launch_plans (
                    candidate_id INTEGER PRIMARY KEY,
                    planned_action TEXT NOT NULL DEFAULT '',
                    first_order_qty INTEGER NOT NULL DEFAULT 0,
                    actual_launch_qty INTEGER NOT NULL DEFAULT 0,
                    actual_launch_date TEXT NOT NULL DEFAULT '',
                    actual_launch_price REAL NOT NULL DEFAULT 0,
                    review_cycle_days INTEGER NOT NULL DEFAULT 14,
                    launch_status TEXT NOT NULL DEFAULT 'planned',
                    launch_notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidate_review_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL,
                    review_date TEXT NOT NULL,
                    cycle_label TEXT NOT NULL DEFAULT '',
                    sales_units INTEGER NOT NULL DEFAULT 0,
                    sales_amount REAL NOT NULL DEFAULT 0,
                    gross_margin_rate REAL NOT NULL DEFAULT 0,
                    decision TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_candidate_review_logs_candidate
                ON candidate_review_logs(candidate_id, review_date DESC, id DESC);

                CREATE TABLE IF NOT EXISTS market_source_snapshots (
                    sku_id INTEGER NOT NULL,
                    sku_code TEXT NOT NULL DEFAULT '',
                    source_platform TEXT NOT NULL,
                    capture_mode TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    exact_match_ratio REAL NOT NULL DEFAULT 0,
                    trimmed_mean_price REAL NOT NULL DEFAULT 0,
                    median_price REAL NOT NULL DEFAULT 0,
                    p10_price REAL NOT NULL DEFAULT 0,
                    p90_price REAL NOT NULL DEFAULT 0,
                    confidence_score REAL NOT NULL DEFAULT 0,
                    heat_score REAL NOT NULL DEFAULT 0,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    sample_prices_json TEXT NOT NULL DEFAULT '[]',
                    matched_titles_json TEXT NOT NULL DEFAULT '[]',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (sku_id, source_platform)
                );

                CREATE TABLE IF NOT EXISTS procurement_action_items (
                    action_key TEXT PRIMARY KEY,
                    item_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'suggested',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_feedback_proposals (
                    proposal_key TEXT PRIMARY KEY,
                    proposal_type TEXT NOT NULL,
                    scope_type TEXT NOT NULL DEFAULT '',
                    scope_key TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    evidence_summary TEXT NOT NULL DEFAULT '',
                    suggested_value_json TEXT NOT NULL DEFAULT '{}',
                    impact_summary TEXT NOT NULL DEFAULT '',
                    decision_status TEXT NOT NULL DEFAULT 'pending',
                    decision_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_overrides (
                    override_key TEXT PRIMARY KEY,
                    override_type TEXT NOT NULL,
                    scope_type TEXT NOT NULL DEFAULT '',
                    scope_key TEXT NOT NULL DEFAULT '',
                    value_json TEXT NOT NULL DEFAULT '{}',
                    source_proposal_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS competitor_items (
                    item_key TEXT PRIMARY KEY,
                    brand TEXT NOT NULL DEFAULT '',
                    product_name TEXT NOT NULL DEFAULT '',
                    spec_text TEXT NOT NULL DEFAULT '',
                    source_platform TEXT NOT NULL DEFAULT '',
                    product_url TEXT NOT NULL DEFAULT '',
                    online_price REAL NOT NULL DEFAULT 0,
                    heat_score REAL NOT NULL DEFAULT 0,
                    evidence_tier TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_price_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT NOT NULL,
                    brand TEXT NOT NULL DEFAULT '',
                    source_platform TEXT NOT NULL DEFAULT '',
                    price REAL NOT NULL DEFAULT 0,
                    confidence_score REAL NOT NULL DEFAULT 0,
                    evidence_tier TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_heat_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT NOT NULL,
                    brand TEXT NOT NULL DEFAULT '',
                    source_platform TEXT NOT NULL DEFAULT '',
                    heat_score REAL NOT NULL DEFAULT 0,
                    keyword TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS brand_watchlists (
                    brand TEXT PRIMARY KEY,
                    notes TEXT NOT NULL DEFAULT '',
                    source_platforms_json TEXT NOT NULL DEFAULT '[]',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_events (
                    event_key TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    brand TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    event_date TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS category_strategy_targets (
                    target_key TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL,
                    scope_key TEXT NOT NULL DEFAULT '',
                    target_value_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS brand_strategy_profiles (
                    brand TEXT PRIMARY KEY,
                    role TEXT NOT NULL DEFAULT '',
                    recommended_action TEXT NOT NULL DEFAULT '',
                    target_depth INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_evidence_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evidence_key TEXT NOT NULL UNIQUE,
                    candidate_id INTEGER NOT NULL DEFAULT 0,
                    item_type TEXT NOT NULL DEFAULT '',
                    item_id TEXT NOT NULL DEFAULT '',
                    brand TEXT NOT NULL DEFAULT '',
                    product_name TEXT NOT NULL DEFAULT '',
                    source_platform TEXT NOT NULL DEFAULT '',
                    price_band TEXT NOT NULL DEFAULT '',
                    efficacy_tags TEXT NOT NULL DEFAULT '',
                    structural_role TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL DEFAULT '',
                    review_cycle_days INTEGER NOT NULL DEFAULT 0,
                    review_result TEXT NOT NULL DEFAULT '',
                    sell_through REAL NOT NULL DEFAULT 0,
                    sales_units INTEGER NOT NULL DEFAULT 0,
                    sales_amount REAL NOT NULL DEFAULT 0,
                    gross_margin_rate REAL NOT NULL DEFAULT 0,
                    evidence_date TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_sku_columns(conn)

    def _ensure_sku_columns(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(skus)").fetchall()
        }
        for column_name, column_ddl in SKU_EXTRA_COLUMNS.items():
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE skus ADD COLUMN {column_name} {column_ddl}")

    def list_skus(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM skus ORDER BY brand, product_name").fetchall()
        return [dict(row) for row in rows]

    def get_sku(self, sku_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM skus WHERE id = ?", (sku_id,)).fetchone()
        return dict(row) if row else None

    def list_candidates(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM candidates ORDER BY updated_at DESC, brand, product_name").fetchall()
        return [dict(row) for row in rows]

    def get_candidate_launch_plan(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM candidate_launch_plans WHERE candidate_id = ?", (candidate_id,)).fetchone()
        return dict(row) if row else None

    def list_candidate_launch_plans(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM candidate_launch_plans ORDER BY updated_at DESC, candidate_id DESC").fetchall()
        return [dict(row) for row in rows]

    def save_candidate_launch_plan(
        self,
        *,
        candidate_id: int,
        planned_action: str,
        first_order_qty: int,
        actual_launch_qty: int,
        actual_launch_date: str,
        actual_launch_price: float,
        review_cycle_days: int,
        launch_status: str,
        launch_notes: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute("SELECT created_at FROM candidate_launch_plans WHERE candidate_id = ?", (candidate_id,)).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO candidate_launch_plans (
                    candidate_id, planned_action, first_order_qty, actual_launch_qty, actual_launch_date,
                    actual_launch_price, review_cycle_days, launch_status, launch_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    planned_action=excluded.planned_action,
                    first_order_qty=excluded.first_order_qty,
                    actual_launch_qty=excluded.actual_launch_qty,
                    actual_launch_date=excluded.actual_launch_date,
                    actual_launch_price=excluded.actual_launch_price,
                    review_cycle_days=excluded.review_cycle_days,
                    launch_status=excluded.launch_status,
                    launch_notes=excluded.launch_notes,
                    updated_at=excluded.updated_at
                """,
                (
                    candidate_id,
                    planned_action,
                    first_order_qty,
                    actual_launch_qty,
                    actual_launch_date,
                    actual_launch_price,
                    review_cycle_days,
                    launch_status,
                    launch_notes,
                    created_at,
                    now,
                ),
            )
        return self.get_candidate_launch_plan(candidate_id) or {}

    def add_candidate_review_log(
        self,
        *,
        candidate_id: int,
        review_date: str,
        cycle_label: str,
        sales_units: int,
        sales_amount: float,
        gross_margin_rate: float,
        decision: str,
        notes: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO candidate_review_logs (
                    candidate_id, review_date, cycle_label, sales_units, sales_amount, gross_margin_rate, decision, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    review_date,
                    cycle_label,
                    sales_units,
                    sales_amount,
                    gross_margin_rate,
                    decision,
                    notes,
                    now_iso(),
                ),
            )
            review_id = int(cursor.lastrowid)
            row = conn.execute("SELECT * FROM candidate_review_logs WHERE id = ?", (review_id,)).fetchone()
        return dict(row) if row else {}

    def get_candidate_review_log(self, review_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM candidate_review_logs WHERE id = ?", (review_id,)).fetchone()
        return dict(row) if row else None

    def update_candidate_review_log(
        self,
        *,
        review_id: int,
        review_date: str,
        cycle_label: str,
        sales_units: int,
        sales_amount: float,
        gross_margin_rate: float,
        decision: str,
        notes: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE candidate_review_logs SET
                    review_date=?, cycle_label=?, sales_units=?, sales_amount=?, gross_margin_rate=?, decision=?, notes=?
                WHERE id=?
                """,
                (
                    review_date,
                    cycle_label,
                    sales_units,
                    sales_amount,
                    gross_margin_rate,
                    decision,
                    notes,
                    review_id,
                ),
            )
        return self.get_candidate_review_log(review_id) or {}

    def delete_candidate_review_log(self, review_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM candidate_review_logs WHERE id = ?", (review_id,))

    def list_candidate_review_logs(self, candidate_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if candidate_id is None:
                rows = conn.execute(
                    "SELECT * FROM candidate_review_logs ORDER BY review_date DESC, id DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM candidate_review_logs WHERE candidate_id = ? ORDER BY review_date DESC, id DESC",
                    (candidate_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_market_source_snapshots(self, sku_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if sku_id is None:
                rows = conn.execute(
                    "SELECT * FROM market_source_snapshots ORDER BY captured_at DESC, sku_code, source_platform"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM market_source_snapshots WHERE sku_id = ? ORDER BY captured_at DESC, source_platform",
                    (sku_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def save_market_source_snapshots(self, snapshots: list[dict[str, Any]]) -> int:
        if not snapshots:
            return 0
        saved = 0
        with self._connect() as conn:
            for snapshot in snapshots:
                sku_id = int(snapshot.get("sku_id") or snapshot.get("id") or 0)
                sku_code = str(snapshot.get("sku_code") or "")
                source_rows = list(snapshot.get("source_snapshots") or [])
                aggregate_row = snapshot.get("aggregate_reference")
                if aggregate_row:
                    source_rows.append(aggregate_row)
                for row in source_rows:
                    source_platform = str(row.get("source_platform") or "")
                    if not sku_id or not source_platform:
                        continue
                    conn.execute(
                        """
                        INSERT INTO market_source_snapshots (
                            sku_id, sku_code, source_platform, capture_mode, status, sample_count, exact_match_ratio,
                            trimmed_mean_price, median_price, p10_price, p90_price, confidence_score, heat_score,
                            blocked, sample_prices_json, matched_titles_json, details_json, captured_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(sku_id, source_platform) DO UPDATE SET
                            sku_code=excluded.sku_code,
                            capture_mode=excluded.capture_mode,
                            status=excluded.status,
                            sample_count=excluded.sample_count,
                            exact_match_ratio=excluded.exact_match_ratio,
                            trimmed_mean_price=excluded.trimmed_mean_price,
                            median_price=excluded.median_price,
                            p10_price=excluded.p10_price,
                            p90_price=excluded.p90_price,
                            confidence_score=excluded.confidence_score,
                            heat_score=excluded.heat_score,
                            blocked=excluded.blocked,
                            sample_prices_json=excluded.sample_prices_json,
                            matched_titles_json=excluded.matched_titles_json,
                            details_json=excluded.details_json,
                            captured_at=excluded.captured_at
                        """,
                        (
                            sku_id,
                            sku_code,
                            source_platform,
                            str(row.get("capture_mode") or ""),
                            str(row.get("status") or ""),
                            int(row.get("sample_count") or 0),
                            float(row.get("exact_match_ratio") or 0),
                            float(row.get("trimmed_mean_price") or 0),
                            float(row.get("median_price") or 0),
                            float(row.get("p10_price") or 0),
                            float(row.get("p90_price") or 0),
                            float(row.get("confidence_score") or 0),
                            float(row.get("heat_score") or 0),
                            int(row.get("blocked") or 0),
                            json.dumps(row.get("sample_prices") or [], ensure_ascii=False),
                            json.dumps(row.get("matched_titles") or [], ensure_ascii=False),
                            json.dumps(row.get("details") or {}, ensure_ascii=False),
                            str(row.get("captured_at") or now_iso()),
                        ),
                    )
                    saved += 1
        return saved

    def get_procurement_action_item(self, action_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM procurement_action_items WHERE action_key = ?",
                (action_key,),
            ).fetchone()
        return dict(row) if row else None

    def list_procurement_action_items(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM procurement_action_items ORDER BY updated_at DESC, action_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_procurement_action_item(
        self,
        *,
        action_key: str,
        item_type: str,
        item_id: str,
        payload: dict[str, Any],
        status: str,
        notes: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM procurement_action_items WHERE action_key = ?",
                (action_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO procurement_action_items (
                    action_key, item_type, item_id, payload_json, status, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_key) DO UPDATE SET
                    item_type=excluded.item_type,
                    item_id=excluded.item_id,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    action_key,
                    item_type,
                    item_id,
                    json.dumps(payload, ensure_ascii=False),
                    status,
                    notes,
                    created_at,
                    now,
                ),
            )
        return self.get_procurement_action_item(action_key) or {}

    def get_review_feedback_proposal(self, proposal_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM review_feedback_proposals WHERE proposal_key = ?",
                (proposal_key,),
            ).fetchone()
        return dict(row) if row else None

    def list_review_feedback_proposals(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_feedback_proposals ORDER BY updated_at DESC, proposal_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_review_feedback_proposal(
        self,
        *,
        proposal_key: str,
        proposal_type: str,
        scope_type: str,
        scope_key: str,
        title: str,
        evidence_summary: str,
        suggested_value: dict[str, Any],
        impact_summary: str,
        decision_status: str = "pending",
        decision_note: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM review_feedback_proposals WHERE proposal_key = ?",
                (proposal_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO review_feedback_proposals (
                    proposal_key, proposal_type, scope_type, scope_key, title, evidence_summary,
                    suggested_value_json, impact_summary, decision_status, decision_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_key) DO UPDATE SET
                    proposal_type=excluded.proposal_type,
                    scope_type=excluded.scope_type,
                    scope_key=excluded.scope_key,
                    title=excluded.title,
                    evidence_summary=excluded.evidence_summary,
                    suggested_value_json=excluded.suggested_value_json,
                    impact_summary=excluded.impact_summary,
                    decision_status=excluded.decision_status,
                    decision_note=excluded.decision_note,
                    updated_at=excluded.updated_at
                """,
                (
                    proposal_key,
                    proposal_type,
                    scope_type,
                    scope_key,
                    title,
                    evidence_summary,
                    json.dumps(suggested_value, ensure_ascii=False),
                    impact_summary,
                    decision_status,
                    decision_note,
                    created_at,
                    now,
                ),
            )
        return self.get_review_feedback_proposal(proposal_key) or {}

    def get_strategy_override(self, override_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_overrides WHERE override_key = ?",
                (override_key,),
            ).fetchone()
        return dict(row) if row else None

    def list_strategy_overrides(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_overrides ORDER BY updated_at DESC, override_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_strategy_override(
        self,
        *,
        override_key: str,
        override_type: str,
        scope_type: str,
        scope_key: str,
        value: dict[str, Any],
        source_proposal_key: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM strategy_overrides WHERE override_key = ?",
                (override_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO strategy_overrides (
                    override_key, override_type, scope_type, scope_key, value_json, source_proposal_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(override_key) DO UPDATE SET
                    override_type=excluded.override_type,
                    scope_type=excluded.scope_type,
                    scope_key=excluded.scope_key,
                    value_json=excluded.value_json,
                    source_proposal_key=excluded.source_proposal_key,
                    updated_at=excluded.updated_at
                """,
                (
                    override_key,
                    override_type,
                    scope_type,
                    scope_key,
                    json.dumps(value, ensure_ascii=False),
                    source_proposal_key,
                    created_at,
                    now,
                ),
            )
        return self.get_strategy_override(override_key) or {}

    def list_competitor_items(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM competitor_items ORDER BY updated_at DESC, brand, product_name"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_competitor_item(
        self,
        *,
        item_key: str,
        brand: str,
        product_name: str,
        spec_text: str,
        source_platform: str,
        product_url: str,
        online_price: float,
        heat_score: float,
        evidence_tier: str,
        status: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM competitor_items WHERE item_key = ?",
                (item_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO competitor_items (
                    item_key, brand, product_name, spec_text, source_platform, product_url,
                    online_price, heat_score, evidence_tier, status, details_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    brand=excluded.brand,
                    product_name=excluded.product_name,
                    spec_text=excluded.spec_text,
                    source_platform=excluded.source_platform,
                    product_url=excluded.product_url,
                    online_price=excluded.online_price,
                    heat_score=excluded.heat_score,
                    evidence_tier=excluded.evidence_tier,
                    status=excluded.status,
                    details_json=excluded.details_json,
                    updated_at=excluded.updated_at
                """,
                (
                    item_key,
                    brand,
                    product_name,
                    spec_text,
                    source_platform,
                    product_url,
                    online_price,
                    heat_score,
                    evidence_tier,
                    status,
                    json.dumps(details, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM competitor_items WHERE item_key = ?",
                (item_key,),
            ).fetchone()
        return dict(row) if row else {}

    def add_market_price_point(
        self,
        *,
        item_key: str,
        brand: str,
        source_platform: str,
        price: float,
        confidence_score: float,
        evidence_tier: str,
        captured_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_price_series (
                    item_key, brand, source_platform, price, confidence_score, evidence_tier, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (item_key, brand, source_platform, price, confidence_score, evidence_tier, captured_at),
            )

    def list_market_price_series(self, *, item_key: str | None = None, brand: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if item_key:
                rows = conn.execute(
                    "SELECT * FROM market_price_series WHERE item_key = ? ORDER BY captured_at DESC, id DESC",
                    (item_key,),
                ).fetchall()
            elif brand:
                rows = conn.execute(
                    "SELECT * FROM market_price_series WHERE brand = ? ORDER BY captured_at DESC, id DESC",
                    (brand,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM market_price_series ORDER BY captured_at DESC, id DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def add_market_heat_point(
        self,
        *,
        item_key: str,
        brand: str,
        source_platform: str,
        heat_score: float,
        keyword: str,
        captured_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_heat_series (
                    item_key, brand, source_platform, heat_score, keyword, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (item_key, brand, source_platform, heat_score, keyword, captured_at),
            )

    def list_market_heat_series(self, *, item_key: str | None = None, brand: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if item_key:
                rows = conn.execute(
                    "SELECT * FROM market_heat_series WHERE item_key = ? ORDER BY captured_at DESC, id DESC",
                    (item_key,),
                ).fetchall()
            elif brand:
                rows = conn.execute(
                    "SELECT * FROM market_heat_series WHERE brand = ? ORDER BY captured_at DESC, id DESC",
                    (brand,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM market_heat_series ORDER BY captured_at DESC, id DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def list_brand_watchlists(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM brand_watchlists ORDER BY active DESC, updated_at DESC, brand"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_brand_watchlist(
        self,
        *,
        brand: str,
        notes: str,
        source_platforms: list[str],
        active: bool = True,
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM brand_watchlists WHERE brand = ?",
                (brand,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO brand_watchlists (
                    brand, notes, source_platforms_json, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(brand) DO UPDATE SET
                    notes=excluded.notes,
                    source_platforms_json=excluded.source_platforms_json,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (
                    brand,
                    notes,
                    json.dumps(source_platforms, ensure_ascii=False),
                    1 if active else 0,
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM brand_watchlists WHERE brand = ?",
                (brand,),
            ).fetchone()
        return dict(row) if row else {}

    def list_market_events(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM market_events ORDER BY event_date DESC, updated_at DESC, event_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_market_event(
        self,
        *,
        event_key: str,
        event_type: str,
        brand: str,
        title: str,
        severity: str,
        summary: str,
        details: dict[str, Any],
        event_date: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM market_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO market_events (
                    event_key, event_type, brand, title, severity, summary, details_json, event_date, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    event_type=excluded.event_type,
                    brand=excluded.brand,
                    title=excluded.title,
                    severity=excluded.severity,
                    summary=excluded.summary,
                    details_json=excluded.details_json,
                    event_date=excluded.event_date,
                    updated_at=excluded.updated_at
                """,
                (
                    event_key,
                    event_type,
                    brand,
                    title,
                    severity,
                    summary,
                    json.dumps(details, ensure_ascii=False),
                    event_date,
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM market_events WHERE event_key = ?",
                (event_key,),
            ).fetchone()
        return dict(row) if row else {}

    def list_category_strategy_targets(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM category_strategy_targets ORDER BY updated_at DESC, target_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_category_strategy_target(
        self,
        *,
        target_key: str,
        target_type: str,
        scope_key: str,
        target_value: dict[str, Any],
        notes: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM category_strategy_targets WHERE target_key = ?",
                (target_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO category_strategy_targets (
                    target_key, target_type, scope_key, target_value_json, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    target_type=excluded.target_type,
                    scope_key=excluded.scope_key,
                    target_value_json=excluded.target_value_json,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    target_key,
                    target_type,
                    scope_key,
                    json.dumps(target_value, ensure_ascii=False),
                    notes,
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM category_strategy_targets WHERE target_key = ?",
                (target_key,),
            ).fetchone()
        return dict(row) if row else {}

    def list_brand_strategy_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM brand_strategy_profiles ORDER BY updated_at DESC, brand"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_brand_strategy_profile(
        self,
        *,
        brand: str,
        role: str,
        recommended_action: str,
        target_depth: int,
        notes: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM brand_strategy_profiles WHERE brand = ?",
                (brand,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO brand_strategy_profiles (
                    brand, role, recommended_action, target_depth, notes, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(brand) DO UPDATE SET
                    role=excluded.role,
                    recommended_action=excluded.recommended_action,
                    target_depth=excluded.target_depth,
                    notes=excluded.notes,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    brand,
                    role,
                    recommended_action,
                    target_depth,
                    notes,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM brand_strategy_profiles WHERE brand = ?",
                (brand,),
            ).fetchone()
        return dict(row) if row else {}

    def list_review_evidence_pool(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_evidence_pool ORDER BY evidence_date DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_review_evidence(
        self,
        *,
        evidence_key: str,
        candidate_id: int,
        item_type: str,
        item_id: str,
        brand: str,
        product_name: str,
        source_platform: str,
        price_band: str,
        efficacy_tags: str,
        structural_role: str,
        action_type: str,
        review_cycle_days: int,
        review_result: str,
        sell_through: float,
        sales_units: int,
        sales_amount: float,
        gross_margin_rate: float,
        evidence_date: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM review_evidence_pool WHERE evidence_key = ?",
                (evidence_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now_iso()
            conn.execute(
                """
                INSERT INTO review_evidence_pool (
                    evidence_key, candidate_id, item_type, item_id, brand, product_name, source_platform,
                    price_band, efficacy_tags, structural_role, action_type, review_cycle_days,
                    review_result, sell_through, sales_units, sales_amount, gross_margin_rate,
                    evidence_date, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_key) DO UPDATE SET
                    candidate_id=excluded.candidate_id,
                    item_type=excluded.item_type,
                    item_id=excluded.item_id,
                    brand=excluded.brand,
                    product_name=excluded.product_name,
                    source_platform=excluded.source_platform,
                    price_band=excluded.price_band,
                    efficacy_tags=excluded.efficacy_tags,
                    structural_role=excluded.structural_role,
                    action_type=excluded.action_type,
                    review_cycle_days=excluded.review_cycle_days,
                    review_result=excluded.review_result,
                    sell_through=excluded.sell_through,
                    sales_units=excluded.sales_units,
                    sales_amount=excluded.sales_amount,
                    gross_margin_rate=excluded.gross_margin_rate,
                    evidence_date=excluded.evidence_date,
                    details_json=excluded.details_json
                """,
                (
                    evidence_key,
                    candidate_id,
                    item_type,
                    item_id,
                    brand,
                    product_name,
                    source_platform,
                    price_band,
                    efficacy_tags,
                    structural_role,
                    action_type,
                    review_cycle_days,
                    review_result,
                    sell_through,
                    sales_units,
                    sales_amount,
                    gross_margin_rate,
                    evidence_date,
                    json.dumps(details, ensure_ascii=False),
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM review_evidence_pool WHERE evidence_key = ?",
                (evidence_key,),
            ).fetchone()
        return dict(row) if row else {}

    def upsert_skus(self, items: list[dict[str, Any]]) -> dict[str, int]:
        now = now_iso()
        inserted = 0
        updated = 0
        with self._connect() as conn:
            for item in items:
                existing = conn.execute("SELECT id FROM skus WHERE sku_code = ?", (item["sku_code"],)).fetchone()
                params = (
                    item["sku_code"],
                    item["brand"],
                    item["product_name"],
                    item["spec_text"],
                    item["efficacy_tags"],
                    item["current_price"],
                    item["purchase_price"],
                    item["gross_margin"],
                    item["unit_gross_profit"],
                    item["six_month_sales"],
                    item["supplier"],
                    item["case_pack"],
                    item["shelf_risk"],
                    item["current_role"],
                    item["notes"],
                    item["unit_price"],
                    item["fluoride"],
                    item["target_group"],
                    item["promo_type"],
                    item["must_keep"],
                    item["substitute_relation"],
                    item["price_band"],
                    item["margin_zone"],
                    item.get("structural_role", ""),
                    item.get("taobao_avg_price", 0),
                    item.get("taobao_min_price", 0),
                    item.get("taobao_max_price", 0),
                    item.get("taobao_sample_count", 0),
                    item.get("price_disorder_flag", 0),
                    item.get("online_heat_score", 0),
                    item.get("market_snapshot_at", ""),
                    now,
                    now,
                )
                conn.execute(
                    """
                    INSERT INTO skus (
                        sku_code, brand, product_name, spec_text, efficacy_tags, current_price, purchase_price,
                        gross_margin, unit_gross_profit, six_month_sales, supplier, case_pack, shelf_risk, current_role,
                        notes, unit_price, fluoride, target_group, promo_type, must_keep, substitute_relation, price_band,
                        margin_zone, structural_role, taobao_avg_price, taobao_min_price, taobao_max_price,
                        taobao_sample_count, price_disorder_flag, online_heat_score, market_snapshot_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sku_code) DO UPDATE SET
                        brand=excluded.brand,
                        product_name=excluded.product_name,
                        spec_text=excluded.spec_text,
                        efficacy_tags=excluded.efficacy_tags,
                        current_price=excluded.current_price,
                        purchase_price=excluded.purchase_price,
                        gross_margin=excluded.gross_margin,
                        unit_gross_profit=excluded.unit_gross_profit,
                        six_month_sales=excluded.six_month_sales,
                        supplier=excluded.supplier,
                        case_pack=excluded.case_pack,
                        shelf_risk=excluded.shelf_risk,
                        current_role=excluded.current_role,
                        notes=excluded.notes,
                        unit_price=excluded.unit_price,
                        fluoride=excluded.fluoride,
                        target_group=excluded.target_group,
                        promo_type=excluded.promo_type,
                        must_keep=excluded.must_keep,
                        substitute_relation=excluded.substitute_relation,
                        price_band=excluded.price_band,
                        margin_zone=excluded.margin_zone,
                        structural_role=excluded.structural_role,
                        taobao_avg_price=excluded.taobao_avg_price,
                        taobao_min_price=excluded.taobao_min_price,
                        taobao_max_price=excluded.taobao_max_price,
                        taobao_sample_count=excluded.taobao_sample_count,
                        price_disorder_flag=excluded.price_disorder_flag,
                        online_heat_score=excluded.online_heat_score,
                        market_snapshot_at=excluded.market_snapshot_at,
                        updated_at=excluded.updated_at
                    """,
                    params,
                )
                if existing:
                    updated += 1
                else:
                    inserted += 1
        return {"inserted": inserted, "updated": updated}

    def update_sku(self, sku_id: int, item: dict[str, Any]) -> dict[str, Any] | None:
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE skus SET
                    sku_code=?, brand=?, product_name=?, spec_text=?, efficacy_tags=?, current_price=?, purchase_price=?,
                    gross_margin=?, unit_gross_profit=?, six_month_sales=?, supplier=?, case_pack=?, shelf_risk=?, current_role=?,
                    notes=?, unit_price=?, fluoride=?, target_group=?, promo_type=?, must_keep=?, substitute_relation=?, price_band=?,
                    margin_zone=?, structural_role=?, taobao_avg_price=?, taobao_min_price=?, taobao_max_price=?,
                    taobao_sample_count=?, price_disorder_flag=?, online_heat_score=?, market_snapshot_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    item["sku_code"],
                    item["brand"],
                    item["product_name"],
                    item["spec_text"],
                    item["efficacy_tags"],
                    item["current_price"],
                    item["purchase_price"],
                    item["gross_margin"],
                    item["unit_gross_profit"],
                    item["six_month_sales"],
                    item["supplier"],
                    item["case_pack"],
                    item["shelf_risk"],
                    item["current_role"],
                    item["notes"],
                    item["unit_price"],
                    item["fluoride"],
                    item["target_group"],
                    item["promo_type"],
                    item["must_keep"],
                    item["substitute_relation"],
                    item["price_band"],
                    item["margin_zone"],
                    item.get("structural_role", ""),
                    item.get("taobao_avg_price", 0),
                    item.get("taobao_min_price", 0),
                    item.get("taobao_max_price", 0),
                    item.get("taobao_sample_count", 0),
                    item.get("price_disorder_flag", 0),
                    item.get("online_heat_score", 0),
                    item.get("market_snapshot_at", ""),
                    now,
                    sku_id,
                ),
            )
        return self.get_sku(sku_id)

    def update_market_snapshots(self, snapshots: list[dict[str, Any]]) -> int:
        if not snapshots:
            return 0
        updated = 0
        with self._connect() as conn:
            for snapshot in snapshots:
                if snapshot.get("id"):
                    conn.execute(
                        """
                        UPDATE skus SET
                            taobao_avg_price=?,
                            taobao_min_price=?,
                            taobao_max_price=?,
                            taobao_sample_count=?,
                            price_disorder_flag=?,
                            online_heat_score=?,
                            market_snapshot_at=?,
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            snapshot.get("taobao_avg_price", 0),
                            snapshot.get("taobao_min_price", 0),
                            snapshot.get("taobao_max_price", 0),
                            snapshot.get("taobao_sample_count", 0),
                            snapshot.get("price_disorder_flag", 0),
                            snapshot.get("online_heat_score", 0),
                            snapshot.get("market_snapshot_at", ""),
                            now_iso(),
                            snapshot["id"],
                        ),
                    )
                    updated += 1
                elif snapshot.get("sku_code"):
                    conn.execute(
                        """
                        UPDATE skus SET
                            taobao_avg_price=?,
                            taobao_min_price=?,
                            taobao_max_price=?,
                            taobao_sample_count=?,
                            price_disorder_flag=?,
                            online_heat_score=?,
                            market_snapshot_at=?,
                            updated_at=?
                        WHERE sku_code=?
                        """,
                        (
                            snapshot.get("taobao_avg_price", 0),
                            snapshot.get("taobao_min_price", 0),
                            snapshot.get("taobao_max_price", 0),
                            snapshot.get("taobao_sample_count", 0),
                            snapshot.get("price_disorder_flag", 0),
                            snapshot.get("online_heat_score", 0),
                            snapshot.get("market_snapshot_at", ""),
                            now_iso(),
                            snapshot["sku_code"],
                        ),
                    )
                    updated += 1
        return updated

    def upsert_market_diagnostics(self, snapshots: list[dict[str, Any]]) -> int:
        if not snapshots:
            return 0
        updated = 0
        with self._connect() as conn:
            for snapshot in snapshots:
                sku_id = snapshot.get("id")
                sku_code = snapshot.get("sku_code")
                if not sku_id and not sku_code:
                    continue
                conn.execute(
                    """
                    INSERT INTO market_diagnostics (
                        sku_id, sku_code, market_sample_status, market_source_mode, diagnostic_summary,
                        query_logs_json, blocked_platforms_json, fallback_note, matched_titles_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sku_id) DO UPDATE SET
                        sku_code=excluded.sku_code,
                        market_sample_status=excluded.market_sample_status,
                        market_source_mode=excluded.market_source_mode,
                        diagnostic_summary=excluded.diagnostic_summary,
                        query_logs_json=excluded.query_logs_json,
                        blocked_platforms_json=excluded.blocked_platforms_json,
                        fallback_note=excluded.fallback_note,
                        matched_titles_json=excluded.matched_titles_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        sku_id,
                        sku_code or "",
                        snapshot.get("market_sample_status", ""),
                        snapshot.get("market_source_mode", ""),
                        snapshot.get("diagnostic_summary", ""),
                        json.dumps(snapshot.get("query_logs") or [], ensure_ascii=False),
                        json.dumps(snapshot.get("blocked_platforms") or [], ensure_ascii=False),
                        snapshot.get("fallback_note", ""),
                        json.dumps(snapshot.get("matched_titles") or [], ensure_ascii=False),
                        now_iso(),
                    ),
                )
                updated += 1
        return updated

    def list_market_diagnostics(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM market_diagnostics ORDER BY updated_at DESC, sku_code").fetchall()
        return [dict(row) for row in rows]

    def save_manual_market_override(
        self,
        *,
        sku_id: int,
        sku_code: str,
        source_platform: str,
        sample_prices: list[float],
        source_urls: list[str],
        note: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            existing = conn.execute("SELECT created_at FROM manual_market_overrides WHERE sku_id = ?", (sku_id,)).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO manual_market_overrides (
                    sku_id, sku_code, source_platform, sample_prices_json, source_urls_json, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sku_id) DO UPDATE SET
                    sku_code=excluded.sku_code,
                    source_platform=excluded.source_platform,
                    sample_prices_json=excluded.sample_prices_json,
                    source_urls_json=excluded.source_urls_json,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (
                    sku_id,
                    sku_code,
                    source_platform,
                    json.dumps(sample_prices, ensure_ascii=False),
                    json.dumps(source_urls, ensure_ascii=False),
                    note,
                    created_at,
                    now,
                ),
            )
        return self.get_manual_market_override(sku_id) or {}

    def get_manual_market_override(self, sku_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM manual_market_overrides WHERE sku_id = ?", (sku_id,)).fetchone()
        return dict(row) if row else None

    def list_manual_market_overrides(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM manual_market_overrides ORDER BY updated_at DESC, sku_code").fetchall()
        return [dict(row) for row in rows]

    def delete_manual_market_override(self, sku_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM manual_market_overrides WHERE sku_id = ?", (sku_id,))

    def record_crawl_observations(self, observations: list[dict[str, Any]]) -> int:
        if not observations:
            return 0
        now = now_iso()
        updated = 0
        with self._connect() as conn:
            for observation in observations:
                normalized_key = observation.get("normalized_key")
                if not normalized_key:
                    continue
                existing = conn.execute(
                    "SELECT seen_count, consecutive_runs, last_seen_at, platform_hits_json, keyword_hits_json FROM crawl_observations WHERE normalized_key = ?",
                    (normalized_key,),
                ).fetchone()
                incoming_platform_hits = observation.get("platform_hits") or {}
                incoming_keyword_hits = observation.get("keyword_hits") or {}
                if existing:
                    last_seen_at = existing["last_seen_at"] or ""
                    previous_platform_hits = json.loads(existing["platform_hits_json"] or "{}")
                    previous_keyword_hits = json.loads(existing["keyword_hits_json"] or "{}")
                    for key, value in incoming_platform_hits.items():
                        previous_platform_hits[key] = previous_platform_hits.get(key, 0) + int(value)
                    for key, value in incoming_keyword_hits.items():
                        previous_keyword_hits[key] = previous_keyword_hits.get(key, 0) + int(value)
                    consecutive_runs = 1
                    if last_seen_at:
                        try:
                            last_seen_date = datetime.fromisoformat(last_seen_at).date()
                            current_date = datetime.fromisoformat(now).date()
                            if last_seen_date == current_date:
                                consecutive_runs = int(existing["consecutive_runs"] or 1)
                            elif last_seen_date == current_date - timedelta(days=1):
                                consecutive_runs = int(existing["consecutive_runs"] or 0) + 1
                        except ValueError:
                            consecutive_runs = 1
                    seen_count = int(existing["seen_count"] or 0) + 1
                    conn.execute(
                        """
                        UPDATE crawl_observations SET
                            canonical_title=?,
                            last_seen_at=?,
                            seen_count=?,
                            consecutive_runs=?,
                            platform_hits_json=?,
                            keyword_hits_json=?
                        WHERE normalized_key=?
                        """,
                        (
                            observation.get("canonical_title", ""),
                            now,
                            seen_count,
                            consecutive_runs,
                            json.dumps(previous_platform_hits, ensure_ascii=False),
                            json.dumps(previous_keyword_hits, ensure_ascii=False),
                            normalized_key,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO crawl_observations (
                            normalized_key, canonical_title, first_seen_at, last_seen_at, seen_count, consecutive_runs,
                            platform_hits_json, keyword_hits_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized_key,
                            observation.get("canonical_title", ""),
                            now,
                            now,
                            1,
                            1,
                            json.dumps(incoming_platform_hits, ensure_ascii=False),
                            json.dumps(incoming_keyword_hits, ensure_ascii=False),
                        ),
                    )
                updated += 1
        return updated

    def list_crawl_observations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM crawl_observations ORDER BY last_seen_at DESC, seen_count DESC").fetchall()
        return [dict(row) for row in rows]

    def save_candidate(self, item: dict[str, Any], candidate_id: int | None = None) -> int:
        now = now_iso()
        with self._connect() as conn:
            if candidate_id:
                conn.execute(
                    """
                    UPDATE candidates SET
                        brand=?, product_name=?, spec_text=?, efficacy_tags=?, online_reference_price=?, expected_purchase_price=?,
                        source_platform=?, product_url=?, heat_score=?, differentiation=?, intended_replace_sku=?, notes=?,
                        fluoride=?, target_group=?, promo_type=?, must_keep=?, substitute_relation=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        item["brand"],
                        item["product_name"],
                        item["spec_text"],
                        item["efficacy_tags"],
                        item["online_reference_price"],
                        item["expected_purchase_price"],
                        item["source_platform"],
                        item["product_url"],
                        item["heat_score"],
                        item["differentiation"],
                        item["intended_replace_sku"],
                        item["notes"],
                        item["fluoride"],
                        item["target_group"],
                        item["promo_type"],
                        item["must_keep"],
                        item["substitute_relation"],
                        now,
                        candidate_id,
                    ),
                )
                return candidate_id
            cursor = conn.execute(
                """
                INSERT INTO candidates (
                    brand, product_name, spec_text, efficacy_tags, online_reference_price, expected_purchase_price,
                    source_platform, product_url, heat_score, differentiation, intended_replace_sku, notes, fluoride,
                    target_group, promo_type, must_keep, substitute_relation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["brand"],
                    item["product_name"],
                    item["spec_text"],
                    item["efficacy_tags"],
                    item["online_reference_price"],
                    item["expected_purchase_price"],
                    item["source_platform"],
                    item["product_url"],
                    item["heat_score"],
                    item["differentiation"],
                    item["intended_replace_sku"],
                    item["notes"],
                    item["fluoride"],
                    item["target_group"],
                    item["promo_type"],
                    item["must_keep"],
                    item["substitute_relation"],
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def delete_candidate(self, candidate_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM candidate_launch_plans WHERE candidate_id = ?", (candidate_id,))
            conn.execute("DELETE FROM candidate_review_logs WHERE candidate_id = ?", (candidate_id,))
            conn.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return dict(row) if row else None

    def add_import_batch(self, *, kind: str, file_name: str, stored_path: str, mapping: dict[str, str], row_count: int, status: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO import_batches (kind, file_name, stored_path, mapping_json, row_count, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, file_name, stored_path, json.dumps(mapping, ensure_ascii=False), row_count, status, now_iso()),
            )
            return int(cursor.lastrowid)

    def update_import_status(self, batch_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE import_batches SET status = ? WHERE id = ?", (status, batch_id))

    def create_backup(self) -> dict[str, str]:
        ensure_directories()
        timestamp = now_iso().replace(":", "-")
        sqlite_copy = BACKUP_DIR / f"toothpaste_tool_{timestamp}.sqlite3"
        json_copy = BACKUP_DIR / f"toothpaste_tool_{timestamp}.json"
        shutil.copy2(self.db_path, sqlite_copy)
        payload = {
            "skus": self.list_skus(),
            "candidates": self.list_candidates(),
            "market_diagnostics": self.list_market_diagnostics(),
            "market_source_snapshots": self.list_market_source_snapshots(),
            "manual_market_overrides": self.list_manual_market_overrides(),
            "crawl_observations": self.list_crawl_observations(),
            "candidate_launch_plans": self.list_candidate_launch_plans(),
            "candidate_review_logs": self.list_candidate_review_logs(),
            "procurement_action_items": self.list_procurement_action_items(),
            "review_feedback_proposals": self.list_review_feedback_proposals(),
            "strategy_overrides": self.list_strategy_overrides(),
            "competitor_items": self.list_competitor_items(),
            "market_price_series": self.list_market_price_series(),
            "market_heat_series": self.list_market_heat_series(),
            "brand_watchlists": self.list_brand_watchlists(),
            "market_events": self.list_market_events(),
            "category_strategy_targets": self.list_category_strategy_targets(),
            "brand_strategy_profiles": self.list_brand_strategy_profiles(),
            "review_evidence_pool": self.list_review_evidence_pool(),
        }
        json_copy.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"sqlite_path": str(sqlite_copy), "json_path": str(json_copy)}
