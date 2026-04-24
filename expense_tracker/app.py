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

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .classifier import Classifier
from .db import DBManager, Transaction
from .export import to_csv, weekly_summary
from .modules import auth as auth_mod
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

# ----- V2.1 feature flags -----

_alias = None
if _feat("merchant_alias"):
    from .modules.merchant_alias import MerchantAlias
    _alias = MerchantAlias(db)

_top_k = _feat("top_k_candidates")
if _top_k:
    from .modules.candidates import top_candidates

_budget_alerts = _feat("budget_alerts")
if _budget_alerts:
    from .modules.budget_alerts import budget_status, detect_anomalies

_digest_cfg = _cfg.get("features", {}).get("daily_digest") or {}
_digest_enabled = bool(_digest_cfg.get("enabled"))
_digest_scheduler = None
_digest_notifier = None
if _digest_enabled:
    from .modules.daily_digest import DigestScheduler, build_digest, run_digest_job
    from .modules.notifier import build as build_notifier
    try:
        _digest_notifier = build_notifier(_digest_cfg)
    except ValueError as exc:
        print(f"[digest] notifier config invalid: {exc}; digest disabled")
        _digest_enabled = False

BUDGETS_MONTHLY = (_cfg.get("budgets") or {}).get("monthly") or {}

_reconcile_enabled = _feat("reconcile")
if _reconcile_enabled:
    from .modules.reconcile import parse_statement, reconcile, bulk_import as reconcile_bulk_import

_cmd_palette = _feat("command_palette")
if _cmd_palette:
    from .modules.query import parse_query, execute as execute_query

# ----- V2.3: auth + native_trigger wiring + desktop popup -----

_auth_cfg = _cfg.get("features", {}).get("auth") or {}
_intake_token = auth_mod.resolve_token(_auth_cfg, db.path)
_allow_localhost = bool(_auth_cfg.get("allow_localhost", True))
require_token = auth_mod.make_dependency(_intake_token, allow_localhost=_allow_localhost)
_auth_deps = [Depends(require_token)] if _intake_token is not None else []

_native_cfg = _cfg.get("features", {}).get("native_trigger") or {}
_native_enabled = bool(_native_cfg.get("enabled"))
_inbox_watcher = None
_trigger_router = None

_popup_enabled = bool(_feat("desktop_popup"))
_popup_notifier = None
if _popup_enabled:
    from .modules.notifier import DesktopNotifier
    _popup_notifier = DesktopNotifier()

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _digest_scheduler, _inbox_watcher, _trigger_router

    if _digest_enabled and _digest_notifier is not None:
        from .modules.daily_digest import DigestScheduler, run_digest_job
        raw_time = (_digest_cfg.get("time") or "22:00").strip()
        try:
            hh, mm = (int(x) for x in raw_time.split(":", 1))
        except ValueError:
            hh, mm = 22, 0
        _digest_scheduler = DigestScheduler(
            hh, mm, lambda: run_digest_job(db, _digest_notifier)
        )
        _digest_scheduler.start()
        print(f"[digest] scheduled daily at {hh:02d}:{mm:02d}")

    if _native_enabled:
        from .modules.native_trigger import InboxWatcher, TriggerRouter
        inbox_dir = os.path.expanduser(
            _native_cfg.get("inbox_dir") or str(db.path.parent / "inbox")
        )
        archive_dir = _native_cfg.get("archive_dir")
        if archive_dir:
            archive_dir = os.path.expanduser(archive_dir)
        poll = float(_native_cfg.get("poll_interval", 1.0))
        delete_after = bool(_native_cfg.get("delete_after_process", True))
        auto_confirm = bool(_native_cfg.get("auto_confirm", True))

        def _route(event):
            _ingest_text(event.text, source=event.source, auto_confirm=auto_confirm)

        _trigger_router = TriggerRouter(on_event=_route)
        _trigger_router.start()
        _inbox_watcher = InboxWatcher(
            inbox_dir=inbox_dir, sink=_trigger_router.queue,
            poll_interval=poll, delete_after_process=delete_after,
            archive_dir=archive_dir,
        )
        _inbox_watcher.start()
        print(f"[native] inbox watcher running on {inbox_dir} (poll {poll}s)")

    try:
        yield
    finally:
        if _digest_scheduler is not None:
            _digest_scheduler.stop()
        if _inbox_watcher is not None:
            _inbox_watcher.stop()
        if _trigger_router is not None:
            _trigger_router.stop()


app = FastAPI(title="Expense Tracker", version="0.3.0", lifespan=_lifespan)
UI_DIR = Path(__file__).parent / "ui"


# --------------------------------------------------------------- schemas

class IntakeRequest(BaseModel):
    text: str
    source: str = "clipboard"


class CandidateOut(BaseModel):
    category: str
    subcategory: Optional[str] = None
    confidence: float
    source: str


class AliasSuggestionOut(BaseModel):
    alias: str
    canonical: str
    score: float
    reason: str


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
    candidates: list[CandidateOut] = []
    alias_suggestion: Optional[AliasSuggestionOut] = None


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


@app.post("/intake", response_model=IntakeResponse, dependencies=_auth_deps)
def intake(req: IntakeRequest) -> IntakeResponse:
    return _parse_and_classify(req.text)


def _parse_and_classify(text: str) -> IntakeResponse:
    parsed = parse(text)
    if not parsed.is_valid:
        raise HTTPException(status_code=422, detail=f"unparseable: {parsed.reason}")

    merchant = parsed.merchant
    alias_sugg = None
    if _alias is not None and merchant:
        canonical = _alias.normalize(merchant)
        if canonical != merchant:
            merchant = canonical
        else:
            sugg = _alias.suggest(merchant)
            if sugg:
                alias_sugg = AliasSuggestionOut(**sugg.__dict__)

    cls = classifier.classify(merchant, parsed.raw_text)
    needs_confirm = (parsed.confidence < 0.7) or (cls.confidence < 0.6)

    candidates_out: list[CandidateOut] = []
    if _top_k:
        cands = top_candidates(classifier, merchant, parsed.raw_text, k=3)
        candidates_out = [
            CandidateOut(category=c.category, subcategory=c.subcategory,
                         confidence=c.confidence, source=c.source)
            for c in cands
        ]

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
        merchant=merchant,
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
        candidates=candidates_out,
        alias_suggestion=alias_sugg,
    )


@app.post("/transactions", dependencies=_auth_deps)
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

    merchant = tx_in.merchant
    if _alias is not None and merchant:
        merchant = _alias.normalize(merchant)

    tx = Transaction(
        amount=amount_base,
        merchant=merchant,
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
    if merchant and tx_in.category:
        classifier.remember(merchant, tx_in.category, tx_in.subcategory)

    if fx_attached and _fx is not None:
        _fx.attach(new_id, fx_attached["currency"], fx_attached["original_amount"])

    if _subs is not None:
        cadence = tx_in.subscription_cadence
        if not cadence and raw_text:
            hint = detect_subscription(raw_text)
            if hint:
                cadence = hint.cadence
        if cadence and merchant:
            _subs.record(merchant, amount_base, cadence,
                         occurred_at=tx.occurred_at, tx_id=new_id)

    return {"id": new_id, "status": "created", "base_amount": amount_base}


@app.get("/transactions")
def list_transactions(limit: int = 200, since_days: Optional[int] = None) -> list[dict]:
    return db.list_transactions(limit=limit, since_days=since_days)


@app.patch("/transactions/{tx_id}", dependencies=_auth_deps)
def update_tx(tx_id: int, payload: CategoryUpdate) -> dict:
    if not db.update_category(tx_id, payload.category, payload.subcategory):
        raise HTTPException(status_code=404, detail="not found")
    rows = db.list_transactions(limit=1)
    for r in rows:
        if r["id"] == tx_id and r.get("merchant"):
            classifier.remember(r["merchant"], payload.category, payload.subcategory)
            break
    return {"id": tx_id, "status": "updated"}


@app.delete("/transactions/{tx_id}", dependencies=_auth_deps)
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
    @app.post("/transactions/{tx_id}/split", dependencies=_auth_deps)
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
    @app.post("/rules/snapshot", dependencies=_auth_deps)
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


class AliasIn(BaseModel):
    alias: str
    canonical: str
    merge_history: bool = False


if _alias is not None:
    @app.get("/aliases")
    def list_aliases() -> list[dict]:
        return _alias.list_all()

    @app.post("/aliases", dependencies=_auth_deps)
    def add_alias(payload: AliasIn) -> dict:
        if payload.merge_history:
            _alias.merge_history(payload.alias, payload.canonical)
        else:
            _alias.add(payload.alias, payload.canonical)
        return {"status": "ok"}

    @app.delete("/aliases/{alias}", dependencies=_auth_deps)
    def delete_alias(alias: str) -> dict:
        if not _alias.remove(alias):
            raise HTTPException(status_code=404, detail="not found")
        return {"status": "deleted"}


if _budget_alerts:
    @app.get("/budgets/status")
    def get_budget_status() -> list[dict]:
        return budget_status(db, BUDGETS_MONTHLY)

    @app.get("/alerts")
    def get_alerts(sigma: float = 2.0) -> list[dict]:
        return detect_anomalies(db, sigma=sigma)


if _digest_enabled:
    from .modules.daily_digest import build_digest as _build_digest, run_digest_job as _run_digest

    @app.get("/digest/today")
    def digest_today() -> dict:
        title, body = _build_digest(db)
        return {"title": title, "body": body}

    @app.post("/digest/test", dependencies=_auth_deps)
    def digest_test() -> dict:
        """Send the digest right now; useful to confirm the Server Chan key."""
        ok = _run_digest(db, _digest_notifier)
        return {"sent": ok}


class ReconcileIn(BaseModel):
    text: str
    channel: Optional[str] = None


class ReconcileImportIn(BaseModel):
    entries: list[dict]
    default_account: Optional[str] = None


if _reconcile_enabled:
    @app.post("/reconcile", dependencies=_auth_deps)
    def reconcile_endpoint(req: ReconcileIn) -> dict:
        entries = parse_statement(req.text, channel=req.channel)
        rows = db.list_transactions(limit=100_000)
        report = reconcile(rows, entries)
        return report.to_dict()

    @app.post("/reconcile/import", dependencies=_auth_deps)
    def reconcile_import_endpoint(req: ReconcileImportIn) -> dict:
        result = reconcile_bulk_import(db, req.entries, default_account=req.default_account)
        return result


if _cmd_palette:
    @app.get("/search")
    def search_endpoint(q: str = "", limit: int = 50) -> dict:
        filt = parse_query(db, q)
        return execute_query(db, filt, limit=limit).to_dict()


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
        "top_k_candidates": _top_k,
        "merchant_alias": _alias is not None,
        "budget_alerts": _budget_alerts,
        "daily_digest": _digest_enabled,
        "reconcile": _reconcile_enabled,
        "command_palette": _cmd_palette,
        "auth": _intake_token is not None,
        "native_trigger": _native_enabled,
        "desktop_popup": _popup_enabled,
    }


# --------------------------------------------------------------- ingest helper

def _ingest_text(text: str, source: str = "native",
                 auto_confirm: bool = True) -> dict:
    """Parse + classify ``text`` and -- if confidence is high enough --
    persist it. Used by the InboxWatcher native-trigger callback.

    Returns ``{"status": "...", "id": int|None, "preview": IntakeResponse}``.
    Failures are swallowed-with-log instead of raising so the inbox
    poller doesn't crash on a single bad payload.
    """
    try:
        preview = _parse_and_classify(text)
    except HTTPException as exc:
        print(f"[ingest] unparseable from {source}: {exc.detail}")
        return {"status": "skipped", "id": None, "preview": None}

    new_id: Optional[int] = None
    if auto_confirm and not preview.needs_confirmation and preview.candidates:
        top = preview.candidates[0]
        try:
            tx_in = TransactionIn(
                amount=preview.amount or 0.0,
                direction=preview.direction,
                merchant=preview.merchant,
                account=preview.account,
                category=top.category,
                subcategory=top.subcategory,
                occurred_at=preview.occurred_at,
                raw_text=preview.raw_text,
                confidence=preview.confidence,
                source=source,
            )
            result = create_transaction(tx_in)
            new_id = result.get("id")
        except HTTPException as exc:
            if exc.status_code == 409:
                # Dedup hit -- treat as success-ish so retries don't pile up.
                return {"status": "duplicate", "id": None, "preview": preview.model_dump()}
            print(f"[ingest] auto-create failed: {exc.detail}")

    if _popup_notifier is not None:
        cands = preview.candidates[:3] if preview.candidates else []
        bullets = "\n".join(
            f"{i+1}. {c.category}{'/' + c.subcategory if c.subcategory else ''} ({int(c.confidence*100)}%)"
            for i, c in enumerate(cands)
        ) or "(no candidates)"
        title = f"待确认: {preview.merchant or '?'} ¥{preview.amount or 0:.2f}"
        try:
            _popup_notifier.send(title, bullets)
        except Exception as exc:  # pragma: no cover
            print(f"[ingest] popup failed: {exc}")

    return {
        "status": "auto_confirmed" if new_id else "needs_confirm",
        "id": new_id,
        "preview": preview.model_dump(),
    }
