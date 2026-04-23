"""Hybrid classifier: merchant history -> keyword rules -> optional LLM hook.

The class is deliberately small and dependency-free so it stays testable. The
optional LLM fallback is pluggable via ``ai_fallback`` (a callable) so the
package never imports an HTTP client unless the host app wires it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .db import DBManager

# A pragmatic seed mapping for the cold-start. The user can extend it via the
# ``category_map`` table without touching code.
DEFAULT_KEYWORDS: list[tuple[str, str, Optional[str]]] = [
    # 餐饮
    ("瑞幸", "餐饮", "咖啡"),
    ("星巴克", "餐饮", "咖啡"),
    ("manner", "餐饮", "咖啡"),
    ("咖啡", "餐饮", "咖啡"),
    ("奶茶", "餐饮", "饮品"),
    ("喜茶", "餐饮", "饮品"),
    ("蜜雪冰城", "餐饮", "饮品"),
    ("麦当劳", "餐饮", "快餐"),
    ("肯德基", "餐饮", "快餐"),
    ("汉堡王", "餐饮", "快餐"),
    ("美团", "餐饮", "外卖"),
    ("饿了么", "餐饮", "外卖"),
    ("食堂", "餐饮", "正餐"),
    ("餐厅", "餐饮", "正餐"),
    # 交通
    ("滴滴", "交通", "打车"),
    ("高德打车", "交通", "打车"),
    ("出租车", "交通", "打车"),
    ("地铁", "交通", "公共交通"),
    ("公交", "交通", "公共交通"),
    ("12306", "交通", "火车"),
    ("铁路", "交通", "火车"),
    ("航空", "交通", "机票"),
    ("机场", "交通", "机票"),
    ("加油", "交通", "燃油"),
    # 购物
    ("淘宝", "购物", "电商"),
    ("天猫", "购物", "电商"),
    ("京东", "购物", "电商"),
    ("拼多多", "购物", "电商"),
    ("唯品会", "购物", "电商"),
    ("超市", "购物", "日用"),
    ("便利店", "购物", "日用"),
    ("罗森", "购物", "便利店"),
    ("711", "购物", "便利店"),
    ("全家", "购物", "便利店"),
    # 居住
    ("房租", "居住", "租金"),
    ("水费", "居住", "水电"),
    ("电费", "居住", "水电"),
    ("燃气", "居住", "水电"),
    ("物业", "居住", "物业"),
    # 娱乐
    ("电影", "娱乐", "观影"),
    ("ktv", "娱乐", "ktv"),
    ("steam", "娱乐", "游戏"),
    ("网易云", "娱乐", "音乐"),
    ("爱奇艺", "娱乐", "视频"),
    ("腾讯视频", "娱乐", "视频"),
    ("b站", "娱乐", "视频"),
    # 医疗
    ("医院", "医疗", "诊疗"),
    ("药店", "医疗", "药品"),
    ("诊所", "医疗", "诊疗"),
    # 教育
    ("书店", "教育", "图书"),
    ("当当", "教育", "图书"),
    ("课程", "教育", "培训"),
    ("学费", "教育", "学费"),
    # 办公
    ("阿里云", "办公", "云服务"),
    ("腾讯云", "办公", "云服务"),
    ("github", "办公", "订阅"),
    ("openai", "办公", "订阅"),
    ("anthropic", "办公", "订阅"),
]

DEFAULT_CATEGORY = "其他"


@dataclass
class ClassificationResult:
    category: str
    subcategory: Optional[str] = None
    confidence: float = 0.0
    source: str = "fallback"   # history / keyword / ai / fallback


class Classifier:
    def __init__(
        self,
        db: DBManager,
        ai_fallback: Optional[Callable[[str, Optional[str]], Optional[ClassificationResult]]] = None,
        ai_threshold: float = 0.5,
    ):
        self.db = db
        self.ai_fallback = ai_fallback
        self.ai_threshold = ai_threshold
        self._keywords_cache: list[tuple[str, str, Optional[str]]] = []
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        existing = self.db.list_category_map()
        if not existing:
            self.db.seed_category_map(DEFAULT_KEYWORDS)
        self._keywords_cache = [
            (row["keyword"].lower(), row["category"], row["subcategory"])
            for row in self.db.list_category_map()
        ]

    def reload(self) -> None:
        self._keywords_cache = [
            (row["keyword"].lower(), row["category"], row["subcategory"])
            for row in self.db.list_category_map()
        ]

    def classify(self, merchant: Optional[str], raw_text: Optional[str] = None) -> ClassificationResult:
        # 1. merchant history -> highest priority, this is the learning loop
        if merchant:
            hist = self.db.lookup_merchant_history(merchant)
            if hist and hist.get("category"):
                return ClassificationResult(
                    category=hist["category"],
                    subcategory=hist.get("subcategory"),
                    confidence=min(0.6 + 0.05 * hist.get("hit_count", 1), 0.99),
                    source="history",
                )

        # 2. keyword rules
        haystack = " ".join(filter(None, [merchant, raw_text])).lower()
        if haystack:
            best: Optional[tuple[int, str, Optional[str], str]] = None
            for kw, cat, sub in self._keywords_cache:
                if kw and kw in haystack:
                    score = len(kw)  # longer keyword wins ties
                    if best is None or score > best[0]:
                        best = (score, cat, sub, kw)
            if best is not None:
                return ClassificationResult(
                    category=best[1],
                    subcategory=best[2],
                    confidence=0.75,
                    source="keyword",
                )

        # 3. optional AI fallback (off by default)
        if self.ai_fallback is not None:
            try:
                result = self.ai_fallback(merchant or "", raw_text)
            except Exception:
                result = None
            if result is not None and result.confidence >= self.ai_threshold:
                result.source = "ai"
                return result

        return ClassificationResult(category=DEFAULT_CATEGORY, confidence=0.2, source="fallback")

    def remember(self, merchant: str, category: str, subcategory: Optional[str]) -> None:
        """Persist a user correction so we don't ask twice."""
        self.db.upsert_merchant_history(merchant, category, subcategory)
