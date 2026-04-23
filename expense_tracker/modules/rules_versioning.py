"""Snapshot classification rules (category_map + merchant_history) to a
versioned JSON file so the user can roll back after a bad edit.

Stored under ``<data_dir>/rules/rules_v{N}.json``. ``snapshot()`` is
idempotent-ish: it never overwrites, always bumps to the next N.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..db import DBManager

FILE_RE = re.compile(r"rules_v(\d+)\.json$")


class RulesVersioning:
    def __init__(self, db: DBManager, dir_path: Optional[Path | str] = None):
        self.db = db
        self.dir = Path(dir_path) if dir_path else self.db.path.parent / "rules"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _next_version(self) -> int:
        existing = [
            int(m.group(1)) for p in self.dir.glob("rules_v*.json")
            if (m := FILE_RE.search(p.name))
        ]
        return (max(existing) + 1) if existing else 1

    def snapshot(self, note: Optional[str] = None) -> Path:
        version = self._next_version()
        data = {
            "version": version,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "note": note,
            "category_map": self.db.list_category_map(),
            "merchant_history": self._dump_history(),
        }
        path = self.dir / f"rules_v{version}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _dump_history(self) -> list[dict]:
        import sqlite3
        with sqlite3.connect(self.db.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM merchant_history").fetchall()
        return [dict(r) for r in rows]

    def list_versions(self) -> list[dict]:
        out: list[dict] = []
        for path in sorted(self.dir.glob("rules_v*.json")):
            m = FILE_RE.search(path.name)
            if not m:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            out.append({
                "version": int(m.group(1)),
                "path": str(path),
                "created_at": payload.get("created_at"),
                "note": payload.get("note"),
            })
        return out

    def rollback(self, version: int) -> None:
        path = self.dir / f"rules_v{version}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        import sqlite3
        with sqlite3.connect(self.db.path) as conn:
            conn.execute("DELETE FROM category_map")
            conn.executemany(
                "INSERT INTO category_map(keyword, category, subcategory) VALUES (?, ?, ?)",
                [(r["keyword"], r["category"], r.get("subcategory")) for r in payload.get("category_map", [])],
            )
            # merchant_history rollback is opt-in via caller; skipped here to preserve
            # user corrections between versions. Callers that want a hard reset can
            # call ``rollback_history(version)``.

    def rollback_history(self, version: int) -> None:
        path = self.dir / f"rules_v{version}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        import sqlite3
        with sqlite3.connect(self.db.path) as conn:
            conn.execute("DELETE FROM merchant_history")
            conn.executemany(
                """
                INSERT INTO merchant_history(merchant, category, subcategory, hit_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (r["merchant"], r.get("category"), r.get("subcategory"),
                     r.get("hit_count", 1), r.get("updated_at") or datetime.now().isoformat())
                    for r in payload.get("merchant_history", [])
                ],
            )
