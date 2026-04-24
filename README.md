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

### 工程结构（V2 增量）
```
expense_tracker/
├── __init__.py
├── app.py                # FastAPI + Web UI；按 config.yaml 动态挂载 V2 端点
├── db.py                 # V1 SQLite 持久层（dedup_hash / direction / account / source ...）
├── parser.py             # 微信/支付宝/银行卡 文本解析（V1 已固定，V2 不动）
├── classifier.py         # V1 规则 + 历史 + 可插拔 AI 兜底（V2 不动）
├── clipboard_monitor.py  # V1 剪贴板守护 → 自动降级为 native_trigger 的 fallback
├── export.py             # V1 CSV + 周度统计
├── ui/index.html         # Tailwind + 原生 JS UI
├── config.yaml           # V2 总开关（默认轻量/关闭）
├── diagnose.sh           # 一键诊断（权限/依赖/解析示例）
├── modules/              # ★ V2 新增，全部为 opt-in
│   ├── __init__.py
│   ├── config_loader.py       # YAML/JSON 加载 + mtime 热更新
│   ├── desensitize.py         # 卡号/手机号/订单号脱敏
│   ├── time_decay.py          # merchant_history 时间衰减包装器
│   ├── multi_currency.py      # 原币 + 当日 FX → 本币（额外侧表）
│   ├── split_transaction.py   # 单笔拆分（100 → 餐60 + 交40）
│   ├── subscription.py        # 自动续费/月付/年费 → 订阅日历
│   ├── rules_versioning.py    # category_map/merchant_history 快照 + 回滚
│   ├── exporters.py           # Beancount / GnuCash CSV / 隐私统计
│   ├── native_trigger.py      # 平台原生通道（inbox 文件夹协议）
│   ├── webdav_sync.py         # 同步插件接口（默认 NullSync）
│   ├── sqlcipher_adapter.py   # 可选 SQLCipher
│   └── diagnose.py            # diagnose.sh 后端
└── tests/                # V1 + V2 测试，共 51 个用例全部通过
```

### 快速开始
```bash
pip install -r requirements.txt
uvicorn expense_tracker.app:app --reload   # 启动 Web 服务
# 浏览器打开 http://127.0.0.1:8000

# 可选：开启剪贴板守护，账单复制后自动弹回主窗口
python -m expense_tracker.clipboard_monitor --endpoint http://127.0.0.1:8000/intake

# V2：编辑 config.yaml 打开你需要的功能（默认只开了 desensitize 和 extended_exporters）
```

环境变量 `EXPENSE_DB_PATH` 可指定 SQLite 文件位置；默认在 `~/.expense_tracker/data.db`。

### 核心 API
| Method | Path | 说明 | 启用条件 |
| --- | --- | --- | --- |
| `POST` | `/intake` | 解析 → 分类 → 返回结构化预览 + 置信度 | V1 |
| `POST` | `/transactions` | 写入一笔流水（dedup 命中返回 409） | V1 |
| `GET`  | `/transactions` | 列表 | V1 |
| `PATCH`| `/transactions/{id}` | 修正分类 → 自动写 `merchant_history`（学习闭环） | V1 |
| `DELETE`| `/transactions/{id}` | 软删除 | V1 |
| `GET`  | `/stats/category` | 近 N 天分类汇总（仅 expense） | V1 |
| `GET`  | `/stats/weekly` | 近 N 周收支趋势 | V1 |
| `GET`  | `/export.csv` | CSV 导出 | V1 |
| `GET`  | `/healthz` | 健康检查 | V1 |
| `GET`  | `/features` | 当前已启用模块 | V2 |
| `POST` | `/transactions/{id}/split` | 拆分单笔流水 | `split_transactions: true` |
| `GET`  | `/subscriptions/upcoming` | 近 N 天到期订阅 | `subscription_calendar: true` |
| `POST` | `/rules/snapshot` | 给当前规则打快照 | `rules_versioning: true` |
| `GET`  | `/rules/versions` | 列出快照 | `rules_versioning: true` |
| `GET`  | `/export.beancount` | Beancount 文本 | `extended_exporters: true` |
| `GET`  | `/export.gnucash.csv` | GnuCash 导入用 CSV | `extended_exporters: true` |
| `GET`  | `/export.privacy` | 桶化匿名统计（无商户/无金额明细） | `extended_exporters: true` |

### 测试 / 诊断
```bash
python -m pytest expense_tracker/tests -q          # 全部测试
bash expense_tracker/diagnose.sh                   # 权限/依赖/解析快照
python -m expense_tracker.modules.diagnose         # 仅诊断 JSON
```

---

## V2 模块清单（变更文件 / 测试 / 权限说明）

> 全部模块都遵守约束：
> 1. 只放 `expense_tracker/modules/`，通过 `config.yaml` 开关控制；默认轻量或关闭。
> 2. 不修改 V1 的 `dedup_hash` / `direction` / `confidence_threshold` / `pluggable_ai` 核心逻辑。
> 3. 任意一个模块关闭，剩余系统行为与 V1 完全一致。

| 模块 | 变更文件 | 测试 | 权限 / 部署说明 |
| --- | --- | --- | --- |
| `desensitize` | `modules/desensitize.py`，`app.py` 在写入前调用 | `tests/test_modules_desensitize.py` (6) | 无系统权限；纯函数，可在 CI 跑 |
| `time_decay` | `modules/time_decay.py`（`Classifier` 子类，不动 V1） | `tests/test_modules_time_decay.py` (3) | 无 |
| `multi_currency` | `modules/multi_currency.py` + 新增 `transaction_fx` 侧表；`app.py` 在 create 前换算 | `tests/test_modules_multi_currency.py` (6) | 需在 data dir 放 `fx_rates.json`（不联网） |
| `split_transactions` | `modules/split_transaction.py` + 新增 `transaction_splits` 表；`POST /transactions/{id}/split` | `tests/test_modules_split.py` (3) | 无 |
| `subscription_calendar` | `modules/subscription.py` + 新增 `subscription_calendar` 表；`/subscriptions/upcoming` | `tests/test_modules_subscription.py` (5) | 无；预警走桌面通知（OS notify 由前端实现） |
| `rules_versioning` | `modules/rules_versioning.py`；`/rules/snapshot` `/rules/versions` | `tests/test_modules_rules_versioning.py` (3) | 需对 `<data>/rules/` 有写权限 |
| `extended_exporters` | `modules/exporters.py`；`/export.beancount` `/export.gnucash.csv` `/export.privacy` | `tests/test_modules_exporters.py` (3) | 无 |
| `native_trigger` | `modules/native_trigger.py`（`InboxWatcher` + `TriggerRouter`） | `tests/test_modules_native_trigger.py` (3) | **macOS**: Shortcuts 自动化 → "Append to file" 写入 inbox；首次需「完全磁盘访问」<br>**Windows**: PowerShell 计划任务读 `UserNotificationListener` → 写 inbox；首次需「通知访问」<br>**Android**: Tasker / AutoNotification → 写 inbox；需「通知访问」<br>**iOS**: Shortcuts 自动化 → 写 iCloud 文件 → 同步到 inbox |
| `clipboard_monitor`（V1，被降级为 fallback） | 不动 | 已有 V1 测试 | macOS 需「辅助功能」；Linux 需 `xclip` |
| `sqlcipher` | `modules/sqlcipher_adapter.py` | 默认禁用，无单测；启用前 `pip install pysqlcipher3` 并设 `EXPENSE_DB_KEY` | 系统需有 OpenSSL/SQLCipher 库 |
| `webdav_sync` | `modules/webdav_sync.py` (`NullSync` 占位) | 接口本身无网络调用，无单测 | 默认关闭；接 WebDAV/Nextcloud 时再装 `webdavclient3` |
| `config_hot_reload` | `modules/config_loader.py`（`ConfigWatcher`） | `tests/test_modules_config.py` (3) | 无 |
| `diagnose` | `modules/diagnose.py` + `diagnose.sh` | 无独立测试（被 `bash diagnose.sh` 覆盖） | 无 |

---

## 对原 V1 方案的反思与优化（保留）

### 1. 加入 `dedup_hash`，避免剪贴板重复触发
SHA1(`occurred_at(分钟级)+amount+direction+merchant`) 写在 UNIQUE 约束上，
SQLite 直接拦截重复写入。

### 2. 显式 `direction`：支出 / 收入 / 退款 / 转账
parser 通过 `+/-`、`退款 / 到账 / 转入 / 转出` 等关键词推断；统计接口仅计 expense。

### 3. `account` 字段（微信/支付宝/银行卡）
为后续信用卡还款提醒、对账留出维度。

### 4. 学习闭环
`PATCH /transactions/{id}` → `classifier.remember()` → `merchant_history`，下一次直接命中。

### 5. 置信度 + 强制确认
`needs_confirmation = parsed.conf<0.7 or cls.conf<0.6`，避免脏数据静默入库。

### 6. 审计字段
`source / created_at / updated_at` 让"谁改的、什么时候、来源是什么"可追溯。

### 7. 金额解析评分启发
`¥/￥/元` 加分，超大整数（疑似单号）减分；避免把 `1234567` 当成金额。

### 8. AI 兜底"可插拔 + 默认关闭"
`Classifier(ai_fallback=callable)`，不注入就不依赖网络。

### 9. 测试可移植性
`DBManager(path)` 全量参数化，所有测试用 `tmp_path` 隔离。

### 10. V1 时未覆盖、V2 已落实 / 仍待办
- ✅ **加密**：`sqlcipher_adapter.py` 提供开关。
- ✅ **隐私脱敏**：`desensitize.py` 在落库前抹掉卡号/手机号/订单号。
- ✅ **多币种**：`multi_currency.py` + `transaction_fx` 侧表。
- ✅ **拆分账单**：`split_transaction.py`。
- ✅ **订阅 / 自动续费**：`subscription.py` + `/subscriptions/upcoming`。
- ✅ **规则回滚**：`rules_versioning.py` 快照。
- ✅ **多种导出**：Beancount / GnuCash / 隐私统计。
- ⏸️ **OCR 兜底**：`native_trigger` 的 inbox 协议预留了入口，但 OCR 二进制不打包。
- ⏸️ **预算预警**：`budgets` 表已建、`config.yaml` 给了样例，端点尚未挂。
- ⏸️ **内部转账配对**：仍依赖手动；可在 `direction='transfer'` + 同金额近时 + 反向账户上做匹配。

---

## V2 之后还能怎么改善（结构性思维分析）

下面按"对实际体验的影响 × 工程成本"两个维度盘点，**Top 3 是当下最值得做的**。

### A. 触发链路（决定"3 秒"能不能持续兑现）
1. **★ 用 OS 级"快捷指令"取代轮询** —— 现在 `clipboard_monitor` 0.8s 轮询、
   `native_trigger.InboxWatcher` 1s 轮询，叠加起来在低电量场景会被系统 throttle。
   把 macOS Shortcuts 改成"通知到达 → HTTP POST"直推 `/intake`，去掉中间文件落地，
   端到端延迟从 ~1s 降到 <100ms。
2. **iOS Share Sheet / Android Quick Settings 磁贴** —— inbox 协议是兜底，
   但 native 推送才能真正做到"按一下就完成"。
3. **错峰输入合并** —— 一笔交易的"扣款通知 + 商户回单短信"可能同分钟到达；
   现在只去重不合并。可以在 dedup 命中时把第二条 raw_text 追加到原记录的 note，
   提升 OCR / 后续核对的信息密度。

### B. 数据模型 / 引擎
4. **账户与流水分离** —— 当前 `account` 只是字符串。一旦做信用卡账单核对就需要
   `accounts(id, type, currency, opening_balance)` 主表，否则跨账户余额无法计算。
5. **真正的双账法** —— Beancount 导出已经按双账法生成，本地存储却仍是单账。
   下一步把 `transactions` 改成事件流，按规则 fold 出账户余额。这一步前置好后
   GnuCash 同步、对账、余额预测都能省事。
6. **分类引擎的"不确定性可视化"** —— `needs_confirmation` 是布尔；其实更像光谱。
   Top-3 候选 + 概率给出，让用户按 1/2/3 数字键秒选，比下拉框快得多。
7. **新词在线学习** —— 现在 `merchant_history` 仅在用户修正时写入；可以在解析失败/
   置信度过低时把 raw_text 入"待标注"队列，UI 旁开「批量标注模式」。

### C. 工程 / 运维
8. **真正的迁移机制** —— 现在每个模块在 `__init__` 里 `executescript(SCHEMA)` 是
   "幂等 CREATE IF NOT EXISTS"。一旦字段需要演化（例如 `transaction_fx` 加 `note`）
   就会无声失败。引入 `schema_migrations` 表 + 编号脚本是廉价但必要的。
9. **可观测性** —— 现在出错都靠 print。最少应该做：
   - `/healthz` 返回 db / inbox / 解析三件套的最近状态码与延迟；
   - parser 错误率/分类置信度做成 `/metrics` 暴露给本地 Grafana / Datadog。
10. **数据备份的"3-2-1"** —— SQLite 单文件 + 用户主目录是单点风险。
    `webdav_sync` 占位已在；要做出"加密上传 + 周度自动 snapshot + 一键恢复"
    的端到端流程，本地工具才算真正完整。

### D. 隐私 / 合规（个人产品同样要看）
11. **脱敏发生在写入 `raw_text` 之前还是之后？** 当前是 V2 在 app.py 入库前调用，
    但 V1 里的 `parser` 取 merchant 时仍然能看到原文。把脱敏前置到 `/intake`
    的最早一步、与 parser 串行（先脱敏再解析），可以保证 SQLite 永不接触明文。
12. **敏感字段独立加密** —— SQLCipher 是"全库加密"。更细粒度做法：把
    `raw_text` 用对称密钥单独加密，列出 `*_aes` 字段；普通查询不用解密就能跑。
13. **导出权限分级** —— `/export.csv` 含商户、`/export.privacy` 不含。
    可以再加 `/export.audit`（自己看的，含全部 PII）和 `/export.share`（外发的，
    与 `privacy` 类似但含分类比例图）。

### E. 可用性
14. **跨设备 UX 一致性** —— Web UI 在桌面已经够 1.5 秒；手机端要么靠原生 Share
    要么靠 PWA + Service Worker 离线缓存，否则在弱网络下"3 秒承诺"无法兑现。
15. **键盘热键不只是 Enter** —— 现在 UI 只支持 Enter / Esc。把方向键、数字键
    1-9 (top-3 候选 + 数字直选 subcategory) 全部上线，能再砍 1 秒。
16. **预算软预警可视化** —— `config.yaml` 里的 `budgets.monthly` 已经定义；
    但 UI 没有"本月某分类已用 80%"的胶囊提示。一两小时的活，价值很高。

### F. 长期方向
17. **本地 LLM 兜底**（llama.cpp + 量化 1B 模型）跑商户归类，断网仍能 fallback。
18. **跨用户 CSV → 个人模型**：把全用户的"raw_text → category"对收集起来训一个
    100MB 的小模型，再以 LoRA 形式下发到每台设备做本地微调。
19. **协同（家庭账本）**：多人共享 SQLite via WebDAV/CRDT，冲突按 dedup_hash 收敛。

---

## V2.1 增量（便利性 + 粘性）

四个聚焦"日常便利 + 粘性"的模块，全部默认开（除了微信推送要你贴 SENDKEY）。

| 模块 | 文件 | 端点 | 配置入口 |
| --- | --- | --- | --- |
| **Top-3 候选 + 数字键直选** | `modules/candidates.py` | `/intake` 响应新增 `candidates[]` | `features.top_k_candidates: true` |
| **商户别名归一** | `modules/merchant_alias.py` | `GET/POST/DELETE /aliases` | `features.merchant_alias: true` |
| **预算可视化 + 异常预警** | `modules/budget_alerts.py` | `/budgets/status`、`/alerts` | `features.budget_alerts: true` + `budgets.monthly.*` |
| **每日 22:00 总结（微信推送）** | `modules/daily_digest.py` + `modules/notifier.py` | `/digest/today`、`/digest/test` | `features.daily_digest` 子节点 |

### 怎么把日报推到你微信
1. 打开 https://sct.ftqq.com/ ，用 GitHub 登录，拿到你的 `SENDKEY`（`SCT` 开头）。
2. 编辑 `expense_tracker/config.yaml`：
   ```yaml
   features:
     daily_digest:
       enabled: true
       time: "22:00"
       notifier: wechat
       wechat_sendkey: SCTxxxxxxxxxxxxxxxx
   ```
3. 重启服务，首次推送会在手机上弹出"确认绑定" → 同意即可。
4. 在 UI 右上角"设置 → 每日总结 → 推送测试" 也能立刻触发一次，确认通了再等 22:00。

### UI 变化（仍是 Tailwind + 原生 JS，不依赖框架）
- 粘贴 → 解析 → **按 `1` / `2` / `3`** 直接落库。
- 顶部出现 **环形进度条**（本月预算使用率，低/正常/警告/超支四色）。
- 出现"咖啡支出 +80% / 某新商户单笔 ¥500"时顶部弹 **琥珀色 alert 胶囊**。
- 落库后 30 秒内按 `Ctrl+Z` **一键撤销**（复用 V1 软删除）。
- 解析时若检测到新旧商户疑似同一家，弹 **"一键合并"** 提示。
- 右上角 **设置** 抽屉：管理别名 / 预览今日总结 / 推送测试。

### 测试 / 规模
- V2.1 新增 30 个测试，合计 **81 个用例全部通过**。
- 不碰 V1 核心（`dedup_hash` / `direction` / `confidence_threshold` / `pluggable_ai`），
  不碰 V2 已验证的 `time_decay` / `split` / `fx` / `rules_versioning` / `desensitize`。

---

## V2.2 增量（对账 + 命令面板）

| 模块 | 文件 | 端点 | 开关 |
| --- | --- | --- | --- |
| **对账校验** | `modules/reconcile.py` | `POST /reconcile`、`POST /reconcile/import` | `features.reconcile: true` |
| **命令面板 Ctrl+K（自然语言查询）** | `modules/query.py` | `GET /search?q=...` | `features.command_palette: true` |

### 对账流程（月初 3 分钟）
1. 在微信/支付宝 App 导出账单 CSV（微信: 我 → 服务 → 账单；支付宝: 账单 → 右上 → 导出明细）。
2. 打开记账页 → **设置** 抽屉 → "对账校验"。
3. 粘贴完整 CSV（头部元信息也可以一起粘）→ 点"解析对账"。
4. 页面高亮：
   - **漏记**（红）：账单里有但本地没记 —— 勾选后一键导入，V1 `dedup_hash` 会自动去重。
   - **金额不符**（黄）：同商户同时刻但金额对不上 —— 列出差额，人工判断。
   - **本地多**（灰）：本地有但账单里没有 —— 通常是现金或手动录入。

支持格式：**微信账单 CSV**、**支付宝账单 CSV**（自动识别头部）、带有 `时间+金额+收/支` 列的通用 CSV。

匹配算法：双轮贪心。第一轮严格（金额 ±0.01、时间 48h 内、方向一致），第二轮放宽金额但要求商户名子串或完全匹配，用来识别金额差异。

### 命令面板用法（随时 Ctrl+K / Cmd+K）
输入框支持自然语言（纯规则，不调 LLM，<10ms 返回）：

| 查询 | 解析 |
| --- | --- |
| `上周咖啡 超过50` | 上周 & 子分类=咖啡 & 金额 ≥ 50 |
| `本月 打车` | 本月 & 子分类=打车 |
| `瑞幸 >100` | 商户含"瑞幸" & 金额 ≥ 100 |
| `2026-04 京东` | 2026 年 4 月 & 商户含"京东" |
| `昨天` | 昨天 00:00–23:59 |
| `餐饮 10~100` | 分类=餐饮 & 金额 10–100 |
| `上月 支出 超过200` | 上月 & direction=expense & 金额 ≥ 200 |

顶部会显示**匹配条数和合计金额**，列表按时间倒序。命令面板在任何页面都能打开，Esc 关闭。V2.2 合计 **100 个单测通过**。

---

如需在本仓库继续迭代，开发分支为 `claude/expense-tracker-clipboard-nyc9M`。
