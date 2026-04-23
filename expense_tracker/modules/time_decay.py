"""Time-decay wrapper around the V1 ``Classifier``.

V1 ``merchant_history`` always returned the stored category. That is
wrong for merchants the user visited once six months ago and never
touched again. We wrap the classifier and discount history entries by
``exp(-ln(2) * days_since_updated / half_life_days)``, then multiply by
``hit_count``.  Below ``min_weight`` we discard the history match and
let keyword rules take over.

V1 core logic (dedup_hash, direction, confidence threshold, pluggable
AI) is untouched; we only subclass ``Classifier.classify``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from ..classifier import Classifier, ClassificationResult


class DecayingClassifier(Classifier):
    """Drop-in replacement that honours time-decay on history lookups."""

    def __init__(
        self,
        *args,
        half_life_days: float = 30.0,
        min_weight: float = 0.15,
        new_boost_days: float = 7.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.half_life_days = max(1.0, half_life_days)
        self.min_weight = min_weight
        self.new_boost_days = new_boost_days

    # ----- public API compatible with Classifier -----

    def classify(self, merchant: Optional[str], raw_text: Optional[str] = None) -> ClassificationResult:
        if merchant:
            hist = self.db.lookup_merchant_history(merchant)
            if hist and hist.get("category"):
                weight = self._weight(hist)
                if weight >= self.min_weight:
                    confidence = round(min(0.55 + 0.4 * weight, 0.99), 2)
                    return ClassificationResult(
                        category=hist["category"],
                        subcategory=hist.get("subcategory"),
                        confidence=confidence,
                        source="history_decay",
                    )
                # weight too low: fall through, as if we had no history
        # Delegate to V1 keyword/AI/fallback path. Pass merchant=None so the
        # parent skips the history branch, but fold the merchant text into
        # raw_text so keyword rules still see it.
        combined = " ".join(filter(None, [merchant, raw_text])) or None
        return super().classify(None, raw_text=combined)

    # ----- helpers -----

    def _weight(self, hist: dict) -> float:
        try:
            updated_at = datetime.fromisoformat(hist.get("updated_at", ""))
        except ValueError:
            updated_at = datetime.now()
        age_days = max(0.0, (datetime.now() - updated_at).total_seconds() / 86400)
        hit_count = max(1, int(hist.get("hit_count", 1)))
        decay = math.exp(-math.log(2) * age_days / self.half_life_days)
        boost = 1.25 if age_days <= self.new_boost_days else 1.0
        return min(1.0, (0.3 + 0.7 * math.log1p(hit_count) / math.log(10)) * decay * boost)
