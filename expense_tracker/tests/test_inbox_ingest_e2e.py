"""End-to-end: drop a JSON file into the inbox dir while the FastAPI
lifespan is running and verify a transaction lands in the DB."""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture()
def app_with_inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    db_path = tmp_path / "data.db"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"""
features:
  desensitize: true
  top_k_candidates: true
  merchant_alias: true
  budget_alerts: false
  reconcile: false
  command_palette: false
  daily_digest:
    enabled: false
  auth:
    enabled: false
  desktop_popup: false
  native_trigger:
    enabled: true
    inbox_dir: {inbox}
    poll_interval: 0.2
    delete_after_process: true
    auto_confirm: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXPENSE_DB_PATH", str(db_path))
    # Point the loader at our temp config and force a fresh import of app.py.
    monkeypatch.setattr(
        "expense_tracker.modules.config_loader.DEFAULT_CFG_PATH",
        cfg_path,
    )
    sys.modules.pop("expense_tracker.app", None)
    app_mod = importlib.import_module("expense_tracker.app")
    return app_mod, inbox, db_path


def test_inbox_json_is_ingested(app_with_inbox):
    app_mod, inbox, db_path = app_with_inbox
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app) as client:
        # Drop a clearly-classifiable payload.
        (inbox / "drop1.json").write_text(json.dumps({
            "text": "微信支付 -15.00元 商户：瑞幸咖啡",
            "source": "ios-shortcut",
        }), encoding="utf-8")

        # Wait up to ~3s for the watcher (poll 0.2s) to see + ingest.
        deadline = time.time() + 3
        rows: list[dict] = []
        while time.time() < deadline:
            rows = client.get("/transactions").json()
            if rows:
                break
            time.sleep(0.1)

    assert rows, "expected at least one row after inbox drop"
    top = rows[0]
    assert top["merchant"] == "瑞幸咖啡"
    assert top["amount"] == 15.0
    assert top["category"] == "餐饮"
    assert top["source"] == "ios-shortcut"
    # File should have been retired by the watcher.
    assert not (inbox / "drop1.json").exists()


def test_inbox_dedup_blocks_repeat(app_with_inbox):
    app_mod, inbox, db_path = app_with_inbox
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app) as client:
        for i in range(2):
            (inbox / f"drop{i}.json").write_text(json.dumps({
                "text": "微信支付 -8.00元 商户：星巴克",
                "source": "ios-shortcut",
            }), encoding="utf-8")
            time.sleep(0.5)

        rows = client.get("/transactions").json()
        starbucks = [r for r in rows if r["merchant"] == "星巴克"]
    # Two identical payloads → V1 dedup_hash collapses them into one row.
    assert len(starbucks) == 1
