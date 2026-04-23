"""Parse a copied payment notification into structured fields.

Supports common WeChat / Alipay / bank-card / refund patterns. The parser is
intentionally conservative -- when in doubt it sets ``confidence`` low so the
UI can prompt the user to confirm rather than silently inserting bad data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Platforms / accounts -------------------------------------------------------

PLATFORM_PATTERNS: list[tuple[str, str]] = [
    (r"微信支付|微信收款|零钱通", "微信"),
    (r"支付宝|余额宝|花呗", "支付宝"),
    (r"招商银行|工商银行|建设银行|农业银行|交通银行|中国银行|招行|工行|建行|农行|交行|中行", "银行卡"),
    (r"云闪付|银联", "银联"),
]

# Direction ------------------------------------------------------------------

REFUND_HINTS = ("退款", "退回", "已退", "refund")
INCOME_HINTS = ("收款", "到账", "转入", "工资", "收入", "红包")
TRANSFER_HINTS = ("转入余额宝", "转出到", "提现", "充值")

# Amount: matches "¥15.00" / "￥15" / "15.00 元" / "-15.00元" / "+ ￥ 3.5"
AMOUNT_RE = re.compile(
    r"(?P<sign>[+\-]?)\s*[¥￥]?\s*(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*元?"
)

# Merchant heuristics
MERCHANT_LABELS = ("商户", "商家", "收款方", "向", "付款给", "支付给", "对方")
MERCHANT_RE = re.compile(
    r"(?:" + "|".join(MERCHANT_LABELS) + r")[:：\s]*([^\n,，。.()（）¥￥]{2,40})"
)
TRAILING_NOISE = re.compile(r"(付款|支付|消费|收款|的订单|订单|账单)$")

# Time inside the notification, e.g. "2026-04-23 12:34" or "04/23 12:34"
TIME_RE = re.compile(
    r"(?P<full>\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    r"|(?P<short>\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2})"
)


@dataclass
class ParsedTransaction:
    amount: Optional[float]
    direction: str = "expense"          # expense / income / refund / transfer
    merchant: Optional[str] = None
    account: Optional[str] = None       # 微信 / 支付宝 / 银行卡 ...
    occurred_at: Optional[str] = None
    raw_text: str = ""
    confidence: float = 0.0
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.amount is not None and self.amount > 0


def _detect_account(text: str) -> Optional[str]:
    for pattern, label in PLATFORM_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def _detect_direction(text: str, sign: str) -> str:
    lowered = text.lower()
    if any(h in text or h in lowered for h in REFUND_HINTS):
        return "refund"
    if any(h in text for h in TRANSFER_HINTS):
        return "transfer"
    if sign == "+" or any(h in text for h in INCOME_HINTS):
        return "income"
    return "expense"


def _extract_amount(text: str) -> tuple[Optional[float], str]:
    """Return ``(amount, sign)`` for the most plausible amount in ``text``.

    Heuristics: prefer the number that sits closest to a currency symbol or
    "元" / "¥". Falls back to the largest numeric token.
    """
    candidates: list[tuple[float, str, int]] = []  # (amount, sign, score)
    for m in AMOUNT_RE.finditer(text):
        raw = m.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value <= 0:
            continue
        sign = m.group("sign") or ""
        ctx = text[max(0, m.start() - 4): m.end() + 2]
        score = 0
        if "¥" in ctx or "￥" in ctx:
            score += 3
        if "元" in ctx:
            score += 2
        if sign in ("+", "-"):
            score += 1
        # Standalone integers like a phone fragment look noisy: penalize
        if "." not in raw and value > 10_000:
            score -= 2
        candidates.append((value, sign, score))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda x: (x[2], x[0]), reverse=True)
    amount, sign, _ = candidates[0]
    return amount, sign


def _extract_merchant(text: str) -> Optional[str]:
    m = MERCHANT_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip(" \t-—")
    name = TRAILING_NOISE.sub("", name).strip()
    return name or None


def _extract_time(text: str) -> Optional[str]:
    m = TIME_RE.search(text)
    if not m:
        return None
    raw = (m.group("full") or m.group("short")).replace("/", "-").replace(".", "-")
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M")
    for fmt in fmts:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == "%m-%d %H:%M":
            dt = dt.replace(year=datetime.now().year)
        return dt.isoformat(timespec="seconds")
    return None


def parse(text: str) -> ParsedTransaction:
    """Parse a chunk of clipboard / notification text."""
    text = (text or "").strip()
    if not text:
        return ParsedTransaction(amount=None, raw_text="", reason="empty")

    amount, sign = _extract_amount(text)
    if amount is None:
        return ParsedTransaction(amount=None, raw_text=text, reason="no-amount")

    direction = _detect_direction(text, sign)
    merchant = _extract_merchant(text)
    account = _detect_account(text)
    occurred_at = _extract_time(text)

    # Confidence model: each signal contributes a chunk; capped at 1.0
    confidence = 0.4
    if account:
        confidence += 0.2
    if merchant:
        confidence += 0.25
    if occurred_at:
        confidence += 0.1
    if sign:
        confidence += 0.05
    confidence = round(min(confidence, 1.0), 2)

    return ParsedTransaction(
        amount=amount,
        direction=direction,
        merchant=merchant,
        account=account,
        occurred_at=occurred_at or datetime.now().isoformat(timespec="seconds"),
        raw_text=text,
        confidence=confidence,
        reason="ok",
    )


def looks_like_transaction(text: str) -> bool:
    """Cheap pre-filter for the clipboard daemon."""
    if not text or len(text) > 600:
        return False
    if not AMOUNT_RE.search(text):
        return False
    keywords = ("微信", "支付宝", "支付", "付款", "消费", "退款", "收款",
                "￥", "¥", "元", "到账", "转入", "转出")
    return any(k in text for k in keywords)
