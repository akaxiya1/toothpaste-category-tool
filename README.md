# beauty-link-mvp

This repo currently hosts two unrelated MVPs:

1. `app.py` – the original 县域美妆智选 Streamlit demo (kept untouched).
2. `expense_tracker/` – a personal **秒级记账工具**：粘贴账单 → 解析 → 分类 → 落库，
   全程 ≤ 3 秒。本文档主要描述这一部分。

---

## 秒级记账工具

### 设计目标
在微信 / 支付宝付款后，用 **复制 → 全局热键 → 回车** 三步完成记账。明确接受
"无法直接调用官方实时 API" 的现实，走合规、轻量、可本地化的路线。

### 工程结构
```
expense_tracker/
├── __init__.py
├── app.py                # FastAPI 服务 + Web UI 路由
├── db.py                 # SQLite 持久层（含 dedup / direction / account 等扩展字段）
├── parser.py             # 微信/支付宝/银行卡 文本解析
├── classifier.py         # 规则 + 历史 + 可插拔 AI 兜底
├── clipboard_monitor.py  # 可选后台守护，把剪贴板 POST 到 /intake
├── export.py             # CSV 导出 + 周度统计
├── ui/index.html         # Tailwind + 原生 JS 单页 UI
└── tests/                # parser & classifier & DB 测试 (16 个用例全部通过)
```

### 快速开始
```bash
pip install -r requirements.txt
uvicorn expense_tracker.app:app --reload   # 启动 Web 服务
# 浏览器打开 http://127.0.0.1:8000

# 可选：开启剪贴板守护，账单复制后自动弹回主窗口
python -m expense_tracker.clipboard_monitor --endpoint http://127.0.0.1:8000/intake
```

环境变量 `EXPENSE_DB_PATH` 可指定 SQLite 文件位置；默认在 `~/.expense_tracker/data.db`。

### 核心 API
| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/intake` | 解析剪贴板原文，返回结构化预览 + 置信度 |
| `POST` | `/transactions` | 写入一笔确认后的流水（dedup 命中返回 409） |
| `GET`  | `/transactions` | 列表，支持 `since_days` |
| `PATCH`| `/transactions/{id}` | 修正分类，自动写入 `merchant_history`（学习闭环） |
| `DELETE`| `/transactions/{id}` | 软删除 |
| `GET`  | `/stats/category` | 近 N 天分类汇总 |
| `GET`  | `/stats/weekly` | 近 N 周收入/支出趋势 |
| `GET`  | `/export.csv` | 全量导出 |
| `GET`  | `/healthz` | 健康检查 |

### 测试
```bash
python -m pytest expense_tracker/tests -q
```

---

## 对原方案的反思与优化

> 原始需求清晰、易落地，但若直接照搬几个常见坑会立刻冒头。下面列出 **我在实现过程中
> 主动调整或补充的点**，并在代码里做了对应处理。

### 1. 加入 `dedup_hash`，避免剪贴板重复触发
原 schema 没有去重机制，剪贴板守护只要触发两次（用户切到别处再回来）就会写入两条
完全一样的流水。我加了 `(occurred_at(分钟级) + amount + direction + merchant)` 的
SHA1，在 `transactions.dedup_hash` 上建 UNIQUE 约束，由 SQLite 拦截重复写入。

### 2. 显式 `direction` 字段：支出 / 收入 / 退款 / 转账
原 schema 默认所有记录都是支出，会把 **退款 +15 元** 当成新支出，统计 100% 失真；
也会把 **余额宝转入** 算成消费。`parser.py` 通过文本里的 `+/-` 号、`退款 / 到账 /
转入 / 转出` 等关键词识别 direction，统计接口 (`/stats/category`) 也只把 `expense`
计入消费。

### 3. 增加 `account` 字段（微信/支付宝/银行卡）
同一个商户在不同账户的现金流意义完全不同（信用卡 vs 余额）。补上后未来做信用卡
还款提醒、对账单核对都不需要再改 schema。

### 4. 学习闭环：用户修正 = 训练数据
原 spec 列了 `merchant_history` 但没说"什么时候写"。`PATCH /transactions/{id}` 在
更新分类后会自动调 `classifier.remember(...)`，下一次同商户直接命中 history（
比关键词权重更高），**用得越久越准**。`hit_count` 还会反哺置信度。

### 5. 置信度 + 强制确认策略
原方案让用户"按 Enter 确认"，但没有"什么时候不该自动通过"。`/intake` 返回
`needs_confirmation`：解析置信度 < 0.7 或分类置信度 < 0.6 就提示用户必须修正。
避免把脏数据静默落库后，月底统计时一脸懵。

### 6. 审计字段：`source` / `created_at` / `updated_at`
原 schema 不知道一条数据是手动录的、剪贴板抓的还是 CSV 导入的，更不知道分类被
改过几次。补上这几列后排查异常成本骤降。

### 7. 解析器只信号最强的金额
账单里经常出现订单号、卡尾号、余额等"看起来像金额"的数字。`_extract_amount` 给带
`¥/￥/元` 的候选高分，给"超大整数（疑似单号）"打负分，最终只取分数最高的，
避免把 `1234567` 当成 1234567 元入库。

### 8. AI 兜底是"可插拔 + 默认关闭"
原方案提到"可关闭的 AI 兜底"。我让 `Classifier(ai_fallback=callable)` 接受一个
回调，业务方按需注入；不注入就完全不依赖网络 / API key，符合"零配置即可跑"的
MVP 调性。

### 9. 测试可移植性
`DBManager` 接受路径参数，所有测试用 `tmp_path` 隔离，不会污染用户主目录的
真实数据；这条在剪贴板/系统级工具里尤其重要。

### 10. 仍未覆盖、值得后续做的
- **OCR 兜底**：iOS 通知栏文本不可复制时，可以截图 → OCR → /intake。
- **预算预警**：`budgets` 表已建，但还没暴露端点；下一步加 `GET /budgets/status`
  返回"本月某分类已用 80%"。
- **账户内部转账识别**：当短时间内有一进一出且金额相同，应自动配对，避免双计。
- **加密**：SQLite 是明文；本地敏感场景可加 `sqlcipher` 或对 `raw_text` 字段单独加密。
- **iOS / Android 分享扩展**：长期看比剪贴板体验更好，但 MVP 阶段守护进程足够。

---

如需在本仓库继续迭代，开发分支为 `claude/expense-tracker-clipboard-nyc9M`。
