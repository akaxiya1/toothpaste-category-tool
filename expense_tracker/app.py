"""FastAPI surface for the expense tracker.

Endpoints:
    POST /intake               -> parse & stage clipboard text (returns parsed preview)
    POST /transactions         -> persist a confirmed transaction
    GET  /transactions         -> list recent transactions
    PATCH /transactions/{id}   -> update category (also feeds learning loop)
    DELETE /transactions/{id}  -> soft delete
    GET  /stats/category       -> spend per category in window
    GET  /stats/weekly         -> weekly trend
    GET  /export.csv           -> download
    GET  /healthz              -> liveness
    GET  /                     -> embedded Web UI
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .classifier import Classifier
from .db import DBManager, Transaction
from .export import to_csv, weekly_summary
from .parser import parse

DB_PATH = os.environ.get("EXPENSE_DB_PATH")
db = DBManager(DB_PATH) if DB_PATH else DBManager()
classifier = Classifier(db)

app = FastAPI(title="Expense Tracker", version="0.1.0")

UI_DIR = Path(__file__).parent / "ui"


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


class CategoryUpdate(BaseModel):
    category: str
    subcategory: Optional[str] = None


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.post("/intake", response_model=IntakeResponse)
def intake(req: IntakeRequest) -> IntakeResponse:
    parsed = parse(req.text)
    if not parsed.is_valid:
        raise HTTPException(status_code=422, detail=f"unparseable: {parsed.reason}")
    cls = classifier.classify(parsed.merchant, parsed.raw_text)
    needs_confirm = (parsed.confidence < 0.7) or (cls.confidence < 0.6)
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
        raw_text=parsed.raw_text,
        needs_confirmation=needs_confirm,
        reason=parsed.reason,
    )


@app.post("/transactions")
def create_transaction(tx_in: TransactionIn) -> dict:
    tx = Transaction(
        amount=tx_in.amount,
        merchant=tx_in.merchant,
        raw_text=tx_in.raw_text,
        category=tx_in.category,
        subcategory=tx_in.subcategory,
        direction=tx_in.direction,
        account=tx_in.account,
        confidence=tx_in.confidence,
        source=tx_in.source,
        note=tx_in.note,
        occurred_at=tx_in.occurred_at or Transaction(amount=tx_in.amount).occurred_at,
    )
    new_id = db.insert_transaction(tx)
    if new_id is None:
        raise HTTPException(status_code=409, detail="duplicate transaction")
    if tx_in.merchant and tx_in.category:
        classifier.remember(tx_in.merchant, tx_in.category, tx_in.subcategory)
    return {"id": new_id, "status": "created"}


@app.get("/transactions")
def list_transactions(limit: int = 200, since_days: Optional[int] = None) -> list[dict]:
    return db.list_transactions(limit=limit, since_days=since_days)


@app.patch("/transactions/{tx_id}")
def update_tx(tx_id: int, payload: CategoryUpdate) -> dict:
    if not db.update_category(tx_id, payload.category, payload.subcategory):
        raise HTTPException(status_code=404, detail="not found")
    # feed learning loop
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
