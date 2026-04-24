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
            "manual_market_overrides": self.list_manual_market_overrides(),
            "crawl_observations": self.list_crawl_observations(),
        }
        json_copy.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"sqlite_path": str(sqlite_copy), "json_path": str(json_copy)}
