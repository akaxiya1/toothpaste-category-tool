"""Top-K classifier.

V1 ``Classifier.classify()`` returns a single result. For the "number-
key direct pick" UX we want three candidates. Rather than modifying V1,
this module composes on top: it reuses the history/keyword/fallback
sources but keeps N-best instead of best-of-one.
"""

from __future__ import annotations

from typing import Optional

from ..classifier import Classifier, ClassificationResult

COMMON_FALLBACKS = ("餐饮", "交通", "其他")


def top_candidates(
    classifier: Classifier,
    merchant: Optional[str],
    raw_text: Optional[str] = None,
    k: int = 3,
) -> list[ClassificationResult]:
    """Return up to ``k`` de-duplicated candidates, best first."""
    results: list[ClassificationResult] = []
    seen: set[tuple[str, Optional[str]]] = set()

    def _push(r: ClassificationResult) -> None:
        key = (r.category, r.subcategory)
        if key in seen:
            return
        results.append(r)
        seen.add(key)

    # 1) merchant history (single top match)
    if merchant:
        hist = classifier.db.lookup_merchant_history(merchant)
        if hist and hist.get("category"):
            _push(ClassificationResult(
                category=hist["category"],
                subcategory=hist.get("subcategory"),
                confidence=min(0.6 + 0.05 * hist.get("hit_count", 1), 0.99),
                source="history",
            ))

    # 2) keyword matches, sorted by keyword length (longer = more specific)
    haystack = " ".join(filter(None, [merchant, raw_text])).lower()
    if haystack:
        hits = [
            (len(kw), cat, sub)
            for kw, cat, sub in classifier._keywords_cache
            if kw and kw in haystack
        ]
        hits.sort(reverse=True)
        # Scale confidence: top keyword 0.78, second 0.65, third 0.55
        for i, (_, cat, sub) in enumerate(hits):
            if len(results) >= k:
                break
            conf = max(0.55, 0.78 - 0.13 * i)
            _push(ClassificationResult(
                category=cat, subcategory=sub, confidence=conf, source="keyword",
            ))

    # 3) fill remaining slots with common categories
    for cat in COMMON_FALLBACKS:
        if len(results) >= k:
            break
        _push(ClassificationResult(
            category=cat, subcategory=None, confidence=0.15, source="fallback",
        ))

    return results[:k]
