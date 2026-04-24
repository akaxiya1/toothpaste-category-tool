from __future__ import annotations

PRICE_BANDS = [
    {"label": "<=9.9", "min": None, "max": 9.9},
    {"label": "10-14.9", "min": 10.0, "max": 14.9},
    {"label": "15-19.9", "min": 15.0, "max": 19.9},
    {"label": "20-29.9", "min": 20.0, "max": 29.9},
    {"label": "30-39.9", "min": 30.0, "max": 39.9},
    {"label": ">=40", "min": 40.0, "max": None},
]

ROLES = ["引流品", "常规品", "利润品"]

ROLE_MARGIN_TARGETS = {
    "引流品": (0.18, 0.25),
    "常规品": (0.25, 0.32),
    "利润品": (0.32, 0.40),
}

PLATFORMS = ["京东", "天猫", "抖音", "淘宝", "小红书", "其他"]
TARGET_GROUPS = ["成人", "儿童", "家庭"]
PROMO_TYPES = ["常规款", "活动款"]
EFFICACY_OPTIONS = ["防蛀", "美白", "抗敏", "清新口气", "草本", "儿童", "其他"]

SKU_IMPORT_FIELDS = [
    {
        "key": "sku_code",
        "label": "条码/SKU编码",
        "required": True,
        "aliases": ["sku", "sku编码", "sku code", "条码", "条形码", "商品编码", "编码"],
    },
    {
        "key": "brand",
        "label": "品牌",
        "required": True,
        "aliases": ["品牌名"],
    },
    {
        "key": "product_name",
        "label": "商品名称",
        "required": True,
        "aliases": ["名称", "商品名", "牙膏名称", "品名"],
    },
    {
        "key": "spec_text",
        "label": "规格净含量",
        "required": True,
        "aliases": ["规格", "净含量", "规格容量", "容量"],
    },
    {
        "key": "efficacy_tags",
        "label": "功效标签",
        "required": True,
        "aliases": ["功效", "卖点功效", "主功效"],
    },
    {
        "key": "current_price",
        "label": "当前售价",
        "required": True,
        "aliases": ["售价", "零售价", "门店售价"],
    },
    {
        "key": "purchase_price",
        "label": "进价",
        "required": True,
        "aliases": ["采购价", "成本", "成本价"],
    },
    {
        "key": "six_month_sales",
        "label": "近6个月总销量",
        "required": True,
        "aliases": ["半年销量", "近半年销量", "六个月销量", "销量"],
    },
    {
        "key": "supplier",
        "label": "供应商",
        "required": False,
        "aliases": ["供应商名称"],
    },
    {
        "key": "case_pack",
        "label": "箱规/起订量",
        "required": False,
        "aliases": ["箱规", "起订量"],
    },
    {
        "key": "shelf_risk",
        "label": "保质期或临期风险",
        "required": False,
        "aliases": ["保质期", "临期风险", "库存风险"],
    },
    {
        "key": "current_role",
        "label": "当前角色定位",
        "required": False,
        "aliases": ["定位", "角色定位", "商品定位"],
    },
    {
        "key": "notes",
        "label": "备注",
        "required": False,
        "aliases": ["说明", "补充说明"],
    },
    {
        "key": "fluoride",
        "label": "是否含氟",
        "required": False,
        "aliases": ["含氟", "氟"],
    },
    {
        "key": "target_group",
        "label": "适用人群",
        "required": False,
        "aliases": ["人群", "适用对象", "消费人群"],
    },
    {
        "key": "promo_type",
        "label": "是否活动款/常规款",
        "required": False,
        "aliases": ["活动属性", "活动款", "常规款"],
    },
    {
        "key": "must_keep",
        "label": "是否门店必保留基础款",
        "required": False,
        "aliases": ["必保留", "基础保留款", "必留"],
    },
    {
        "key": "substitute_relation",
        "label": "替代关系",
        "required": False,
        "aliases": ["替代", "替代关系说明", "替代备注"],
    },
]

CANDIDATE_IMPORT_FIELDS = [
    {
        "key": "brand",
        "label": "品牌",
        "required": True,
        "aliases": ["品牌名"],
    },
    {
        "key": "product_name",
        "label": "商品名称",
        "required": True,
        "aliases": ["名称", "商品名", "牙膏名称", "品名"],
    },
    {
        "key": "spec_text",
        "label": "规格净含量",
        "required": True,
        "aliases": ["规格", "净含量", "规格容量", "容量"],
    },
    {
        "key": "efficacy_tags",
        "label": "功效标签",
        "required": True,
        "aliases": ["功效", "卖点功效", "主功效"],
    },
    {
        "key": "online_reference_price",
        "label": "线上参考价",
        "required": True,
        "aliases": ["线上价", "参考价", "线上售价"],
    },
    {
        "key": "expected_purchase_price",
        "label": "预计进价",
        "required": True,
        "aliases": ["预计采购价", "预计成本", "预计成本价"],
    },
    {
        "key": "source_platform",
        "label": "来源平台",
        "required": False,
        "aliases": ["平台", "来源", "线上平台"],
    },
    {
        "key": "product_url",
        "label": "商品链接",
        "required": False,
        "aliases": ["链接", "商品地址", "url"],
    },
    {
        "key": "heat_score",
        "label": "热度分",
        "required": False,
        "aliases": ["热度", "热度值", "流行度"],
    },
    {
        "key": "differentiation",
        "label": "差异化卖点",
        "required": False,
        "aliases": ["卖点", "差异化", "核心卖点"],
    },
    {
        "key": "intended_replace_sku",
        "label": "拟替代SKU",
        "required": False,
        "aliases": ["拟替代", "替代sku", "替代编码"],
    },
    {
        "key": "notes",
        "label": "备注",
        "required": False,
        "aliases": ["说明", "补充说明"],
    },
    {
        "key": "fluoride",
        "label": "是否含氟",
        "required": False,
        "aliases": ["含氟", "氟"],
    },
    {
        "key": "target_group",
        "label": "适用人群",
        "required": False,
        "aliases": ["人群", "适用对象", "消费人群"],
    },
    {
        "key": "promo_type",
        "label": "是否活动款/常规款",
        "required": False,
        "aliases": ["活动属性", "活动款", "常规款"],
    },
    {
        "key": "must_keep",
        "label": "是否门店必保留基础款",
        "required": False,
        "aliases": ["必保留", "基础保留款", "必留"],
    },
    {
        "key": "substitute_relation",
        "label": "替代关系",
        "required": False,
        "aliases": ["替代", "替代关系说明", "替代备注"],
    },
]

FIELD_SET_BY_KIND = {
    "sku": SKU_IMPORT_FIELDS,
    "candidate": CANDIDATE_IMPORT_FIELDS,
}

DEFAULT_REQUIRED_EFFICACY = ["防蛀", "美白", "抗敏", "儿童"]
