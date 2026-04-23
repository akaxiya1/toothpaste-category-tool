"""FastAPI surface for the expense tracker.

V1 endpoints are kept intact. V2 endpoints (``/splits``,
``/subscriptions``, ``/export.beancount``, etc.) are only mounted when
the matching flag in ``config.yaml`` is on. With no config file the
behaviour is identical to V1.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .classifier import Classifier
from .db import DBManager, Transaction
from .export import to_csv, weekly_summary
from .modules import config_loader
from .parser import parse

DB_PATH = os.environ.get("EXPENSE_DB_PATH")
db = DBManager(DB_PATH) if DB_PATH else DBManager()

# ---------------------------------------------------------------- features
_cfg = config_loader.load()


def _feat(name: str, default=False):
    return config_loader.feature(_cfg, name, default)


if _feat("time_decay"):
    from .modules.time_decay import DecayingClassifier
    _td = _cfg["features"]["time_decay"]
    classifier: Classifier = DecayingClassifier(
        db,
        half_life_days=_td.get("half_life_days", 30),
        min_weight=_td.get("min_weight", 0.15),
        new_boost_days=_td.get("new_boost_days", 7),
    )
else:
    classifier = Classifier(db)

_desensitize = None
if _feat("desensitize"):
    from .modules.desensitize import clean as _desensitize  # noqa: F401

_splits = None
if _feat("split_transactions"):
    from .modules.split_transaction import SplitManager, SplitPart, SplitError
    _splits = SplitManager(db)

_subs = None
if _feat("subscription_calendar"):
    from .modules.subscription import SubscriptionCalendar, detect as detect_subscription
    _subs = SubscriptionCalendar(db)

_fx = None
if _feat("multi_currency"):
    from .modules.multi_currency import MultiCurrency
    rate_file = _cfg["features"]["multi_currency"].get("rate_file")
    rate_path = Path(os.path.expanduser(rate_file)) if rate_file and not Path(rate_file).is_absolute() \
        else (Path(rate_file) if rate_file else None)
    _fx = MultiCurrency(db, rate_file=rate_path,
                        base=_cfg["features"]["multi_currency"].get("base", "CNY"))

_rules = None
if _feat("rules_versioning"):
    from .modules.rules_versioning import RulesVersioning
    _rules = RulesVersioning(db)

_extended_exporters = _feat("extended_exporters")
if _extended_exporters:
    from .modules.exporters import to_beancount, to_gnucash_csv, privacy_stats

app = FastAPI(title="Expense Tracker", version="0.2.0")
UI_DIR = Path(__file__).parent / "ui"


# --------------------------------------------------------------- schemas

class IntakeRequest(BaseModel):
    text: str
    source: str = "clipboard"


class IntakeResponse(BaseModel):
    amount: Optional[float]
    direction: str
    merchant: Optional[str]
    account: Optional[str]
    occurred_at: Optional[str]
    category: str
    subcategory: Optional[str]
    confidence: float
    classifier_source: str
    raw_text: str
    needs_confirmation: bool
    reason: str
    subscription_hint: Optional[str] = None


class TransactionIn(BaseModel):
    amount: float = Field(..., gt=0)
    direction: str = "expense"
    merchant: Optional[str] = None
    account: Optional[str] = None
    category: str = "其他"
    subcategory: Optional[str] = None
    occurred_at: Optional[str] = None
    raw_text: Optional[str] = None
    note: Optional[str] = None
    confidence: float = 1.0
    source: str = "manual"
    currency: Optional[str] = None          # V2: multi-currency
    original_amount: Optional[float] = None
    subscription_cadence: Optional[str] = None  # V2: explicit user flag


class CategoryUpdate(BaseModel):
    category: str
    subcategory: Optional[str] = None


class SplitPartIn(BaseModel):
    amount: float = Field(..., gt=0)
    category: str
    subcategory: Optional[str] = None
    note: Optional[str] = None


class SplitIn(BaseModel):
    parts: list[SplitPartIn]


# --------------------------------------------------------------- core endpoints (V1)

@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.post("/intake", response_model=IntakeResponse)
def intake(req: IntakeRequest) -> IntakeResponse:
    text = req.text
    parsed = parse(text)
    if not parsed.is_valid:
        raise HTTPException(status_code=422, detail=f"unparseable: {parsed.reason}")
    cls = classifier.classify(parsed.merchant, parsed.raw_text)
    needs_confirm = (parsed.confidence < 0.7) or (cls.confidence < 0.6)

    sub_hint = None
    if _subs is not None:
        hint = detect_subscription(parsed.raw_text)
        if hint:
            sub_hint = hint.cadence

    raw_for_response = parsed.raw_text
    if _desensitize is not None:
        raw_for_response = _desensitize(parsed.raw_text)

    return IntakeResponse(
        amount=parsed.amount,
        direction=parsed.direction,
        merchant=parsed.merchant,
        account=parsed.account,
        occurred_at=parsed.occurred_at,
        category=cls.category,
        subcategory=cls.subcategory,
        confidence=round((parsed.confidence + cls.confidence) / 2, 2),
        classifier_source=cls.source,
        raw_text=raw_for_response,
        needs_confirmation=needs_confirm,
        reason=parsed.reason,
        subscription_hint=sub_hint,
    )


@app.post("/transactions")
def create_transaction(tx_in: TransactionIn) -> dict:
    amount_base = tx_in.amount
    fx_attached = None

    if tx_in.currency and _fx is not None and tx_in.currency.upper() != _fx.base:
        original = tx_in.original_amount or tx_in.amount
        try:
            amount_base = _fx.to_base(original, tx_in.currency.upper())
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        fx_attached = {"currency": tx_in.currency.upper(), "original_amount": original}

    raw_text = tx_in.raw_text
    if _desensitize is not None and raw_text:
        raw_text = _desensitize(raw_text)

    tx = Transaction(
        amount=amount_base,
        merchant=tx_in.merchant,
        raw_text=raw_text,
        category=tx_in.category,
        subcategory=tx_in.subcategory,
        direction=tx_in.direction,
        account=tx_in.account,
        confidence=tx_in.confidence,
        source=tx_in.source,
        note=tx_in.note,
        occurred_at=tx_in.occurred_at or Transaction(amount=amount_base).occurred_at,
    )
    new_id = db.insert_transaction(tx)
    if new_id is None:
        raise HTTPException(status_code=409, detail="duplicate transaction")
    if tx_in.merchant and tx_in.category:
        classifier.remember(tx_in.merchant, tx_in.category, tx_in.subcategory)

    if fx_attached and _fx is not None:
        _fx.attach(new_id, fx_attached["currency"], fx_attached["original_amount"])

    if _subs is not None:
        cadence = tx_in.subscription_cadence
        if not cadence and raw_text:
            hint = detect_subscription(raw_text)
            if hint:
                cadence = hint.cadence
        if cadence and tx_in.merchant:
            _subs.record(tx_in.merchant, amount_base, cadence,
                         occurred_at=tx.occurred_at, tx_id=new_id)

    return {"id": new_id, "status": "created", "base_amount": amount_base}


@app.get("/transactions")
def list_transactions(limit: int = 200, since_days: Optional[int] = None) -> list[dict]:
    return db.list_transactions(limit=limit, since_days=since_days)


@app.patch("/transactions/{tx_id}")
def update_tx(tx_id: int, payload: CategoryUpdate) -> dict:
    if not db.update_category(tx_id, payload.category, payload.subcategory):
        raise HTTPException(status_code=404, detail="not found")
    rows = db.list_transactions(limit=1)
    for r in rows:
        if r["id"] == tx_id and r.get("merchant"):
            classifier.remember(r["merchant"], payload.category, payload.subcategory)
            break
    return {"id": tx_id, "status": "updated"}


@app.delete("/transactions/{tx_id}")
def delete_tx(tx_id: int) -> dict:
    if not db.soft_delete(tx_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"id": tx_id, "status": "deleted"}


@app.get("/stats/category")
def stats_category(since_days: int = 30) -> list[dict]:
    return db.stats_by_category(since_days=since_days)


@app.get("/stats/weekly")
def stats_weekly(weeks: int = 8) -> list[dict]:
    return weekly_summary(db, weeks=weeks)


@app.get("/export.csv", response_class=PlainTextResponse)
def export_csv() -> PlainTextResponse:
    rows = db.list_transactions(limit=100_000)
    return PlainTextResponse(to_csv(rows), media_type="text/csv")


# --------------------------------------------------------------- V2 endpoints

if _splits is not None:
    @app.post("/transactions/{tx_id}/split")
    def split_tx(tx_id: int, payload: SplitIn) -> dict:
        try:
            parts = [SplitPart(amount=p.amount, category=p.category,
                               subcategory=p.subcategory, note=p.note)
                     for p in payload.parts]
            new_ids = _splits.split(tx_id, parts)
        except SplitError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"parent_id": tx_id, "children": new_ids}


if _subs is not None:
    @app.get("/subscriptions/upcoming")
    def upcoming_subs(within_days: int = 7) -> list[dict]:
        return _subs.upcoming(within_days=within_days)


if _rules is not None:
    @app.post("/rules/snapshot")
    def snapshot_rules(note: Optional[str] = None) -> dict:
        path = _rules.snapshot(note=note)
        return {"path": str(path)}

    @app.get("/rules/versions")
    def list_rule_versions() -> list[dict]:
        return _rules.list_versions()


if _extended_exporters:
    @app.get("/export.beancount", response_class=PlainTextResponse)
    def export_beancount() -> PlainTextResponse:
        rows = db.list_transactions(limit=100_000)
        return PlainTextResponse(to_beancount(rows), media_type="text/plain")

    @app.get("/export.gnucash.csv", response_class=PlainTextResponse)
    def export_gnucash() -> PlainTextResponse:
        rows = db.list_transactions(limit=100_000)
        return PlainTextResponse(to_gnucash_csv(rows), media_type="text/csv")

    @app.get("/export.privacy")
    def export_privacy_stats() -> JSONResponse:
        rows = db.list_transactions(limit=100_000)
        return JSONResponse(privacy_stats(rows))


@app.get("/features")
def get_features() -> dict:
    """Expose enabled features so the UI can show/hide sections."""
    return {
        "time_decay": bool(_feat("time_decay")),
        "desensitize": bool(_feat("desensitize")),
        "split_transactions": _splits is not None,
        "subscription_calendar": _subs is not None,
        "multi_currency": _fx is not None,
        "rules_versioning": _rules is not None,
        "extended_exporters": _extended_exporters,
    }
