"""PII/noise scrubber for ``raw_text`` before it is persisted.

Masks bank-card middle digits, phone numbers, 18-digit order numbers,
email addresses, and strings shaped like ID/passport numbers. The
original text is never logged -- only the cleaned version is kept.
"""

from __future__ import annotations

import re

# Ordered: more specific patterns first so they don't get eaten by generic ones.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bank card: 16-19 digits, possibly grouped
    (re.compile(r"\b(\d{4})[\s-]?\d{4,11}[\s-]?(\d{4})\b"), r"\1********\2"),
    # CN mobile (kept prefix + last 4)
    (re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)"), r"\1****\2"),
    # Email
    (re.compile(r"([\w.+-]{1,3})[\w.+-]*@([\w.-]+)"), r"\1***@\2"),
    # CN ID card (18 digits, last may be X)
    (re.compile(r"\b(\d{4})\d{10}(\w{4})\b"), r"\1**********\2"),
    # Long order numbers (12+ digits or alphanum)
    (re.compile(r"(订单号|流水号|交易号|单号)[：: ]*([A-Za-z0-9]{8,})"),
     lambda m: f"{m.group(1)}：{m.group(2)[:4]}****"),
]


def clean(text: str | None) -> str:
    """Return ``text`` with PII masked. ``None``/empty input returns ''."""
    if not text:
        return ""
    out = text
    for pattern, replace in _PATTERNS:
        out = pattern.sub(replace, out)
    return out


def contains_pii(text: str | None) -> bool:
    if not text:
        return False
    for pattern, _ in _PATTERNS:
        if pattern.search(text):
            return True
    return False
