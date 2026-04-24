# iPhone 一次配置：付款 → 自动记账

目标：付款后 3 秒内电脑/手机弹一张确认卡，按 Enter 或 1/2/3 落库。**不再复制粘贴。**

总流程：
```
微信/支付宝付款 → iOS 通知 → 快捷指令拦截 → HTTP POST 到 Mac 的 /intake
                                       ↓
                     有 Wi-Fi → 直接进库 + 弹桌面通知
                     无 Wi-Fi → 写到 iCloud Drive，回家自动同步入库
```

---

## 一、先把 Mac 准备好（10 分钟，一次性）

### 1. 让服务监听局域网
默认 uvicorn 只监听 `127.0.0.1`，手机连不上。改成：
```bash
uvicorn expense_tracker.app:app --host 0.0.0.0 --port 8000 --reload
```
开机自启可以用 launchd（macOS）或写到 `~/.zshrc` 别名里。

### 2. 打开 `expense_tracker/config.yaml`，启用三项功能
```yaml
features:
  auth:
    enabled: true            # 防止同 Wi-Fi 的人乱发
  native_trigger:
    enabled: true
    inbox_dir: ~/Library/Mobile Documents/com~apple~CloudDocs/expense_inbox
    auto_confirm: true       # 高置信度直接入库；低置信度仅弹确认
  desktop_popup: true        # Mac 桌面通知"待确认: 瑞幸咖啡 ¥15"
```
保存即生效（`config_loader` 监听 mtime）。

### 3. 拿 Token
首次启动会在 `~/.expense_tracker/.token` 生成一个随机 Token（24 字节 base64）：
```bash
cat ~/.expense_tracker/.token
# 例如: 7Q9A...kZpL
```
也可以自己设：在 `auth.token: "..."` 填入；或导出环境变量 `EXPENSE_INTAKE_TOKEN=...`。

### 4. 找到 Mac 的局域网地址
```bash
ipconfig getifaddr en0      # 例如 192.168.1.42
```
建议在路由器把这个 IP 设成静态绑定，免得每天换。或者用 mDNS 域名（macOS 自动开启）：在"系统设置 → 通用 → 关于 → 名称"看到的 `xxx.local`，例如 `chenmac.local`。

### 5. 验证 Mac 端通了
在 Mac 上跑：
```bash
TOKEN=$(cat ~/.expense_tracker/.token)
curl -X POST http://127.0.0.1:8000/intake \
     -H "Content-Type: application/json" \
     -H "X-Intake-Token: $TOKEN" \
     -d '{"text":"微信支付 -15.00元 商户：瑞幸咖啡","source":"smoke-test"}'
```
返回 200 + JSON 即 OK。再换成局域网 IP 测一遍：
```bash
curl -X POST http://192.168.1.42:8000/intake -H ... -d ...
```

---

## 二、iPhone 上配置快捷指令（10 分钟）

iOS 17+ 步骤（旧版本菜单文字略有差异，思路相同）。

### 1. 打开"快捷指令" App → 底部"自动化" → 右上 +

### 2. 触发条件：选 **"App"**（"App is opened or closed"），其实"通知"触发更准
- iOS 16/17 没有"收到通知时"原生触发。**变通做法**：使用"打开 App"触发——选**支付宝/微信付款页面**作为触发——但这需要主动开 App。
- **更好的做法**：用"快捷指令" App 内置的"基于通知"动作非常有限。**实际可行的两条路**：

#### 路线 A（推荐）：付款后手动按一次"分享 → 记账" Shortcut
- 微信/支付宝有个"分享账单截图"的入口，分享菜单里能跑你写的 Shortcut
- 一次操作 = 付款后从分享面板点一下 → Shortcut 自动取截图、OCR、POST

#### 路线 B（无障碍 / 通知监听 App 中转）
- 装一个第三方应用如 **Spike**、**LookUp** 或 **AutoHelper**，把它当作"通知监听器"，再让快捷指令通过 Webhook 触发
- 这条路有效但要装额外 App

#### 路线 C（最简单，立刻能用）：**"在主屏幕加一个一键记账按钮"**
- 创建一个 Shortcut："读取剪贴板 → POST 到 /intake"
- 加到主屏幕和锁屏
- 付款后顶栏下拉看通知 → 长按通知 → 复制文本 → 锁屏一点小图标 → 自动入库
- 这条**今天就能跑**，没有任何依赖

### 3. 推荐先做路线 C 的具体步骤

1. 快捷指令 App → 右上 + → 新建空快捷指令
2. 命名："**一键记账**"
3. 加动作：
   - **"获取剪贴板"**
   - **"获取 URL 内容"**：
     - URL：`http://192.168.1.42:8000/intake`（用你 Mac 的 IP）
     - 方法：`POST`
     - 请求头：
       - `Content-Type` = `application/json`
       - `X-Intake-Token` = `<把 Token 粘进来>`
     - 请求体：选 "JSON"，添加字段：
       - `text` = "剪贴板"（魔法变量）
       - `source` = "ios-shortcut"
   - **"显示通知"**（可选）：标题 = "已记账 ✓"
4. 右上分享图标 → "添加到主屏幕"

之后流程：付款后通知栏长按 → 复制 → 锁屏点"一键记账"图标 → 完成。**比手动打开 Web UI 粘贴快得多。**

### 4. 升级版：自动取通知文本（路线 A 的细化）

iOS 不允许 Shortcut 直接读其他 App 的通知文本。**绕道**：
- 装 **Pushcut** App（免费），它能把任何通知作为触发源
- Pushcut 自动化 → "当收到来自微信的通知" → 取通知文本 → 跑你的"一键记账" Shortcut（但参数改成"传入文本"）
- 现在真正零操作：付款 → 几秒后 Mac 桌面弹"待确认 ¥15 瑞幸咖啡"

---

## 三、离线兜底：写 iCloud Drive

如果你不在家、Mac 没开机：

1. 在快捷指令里加条件：**"获取网络详情"**，如果 SSID ≠ "家里的 Wi-Fi" → 走兜底分支
2. 兜底分支动作：
   - **"文本"**：`{"text": "<剪贴板>", "source": "ios-offline"}`
   - **"存储到文件"**：
     - 服务：`iCloud Drive`
     - 路径：`expense_inbox/<时间戳>.json`
     - 覆盖：否
3. 回家电脑开机 → iCloud 同步 → `InboxWatcher` 1 秒一轮，看到新文件就自动入库

需要在 Mac 端 `config.yaml.features.native_trigger.inbox_dir` 指向同一个 iCloud 目录（默认已经是 `~/Library/Mobile Documents/com~apple~CloudDocs/expense_inbox`）。

---

## 四、确认按键（电脑端）

- 桌面通知弹出后，打开 Web UI（命令面板 `Cmd+K` 也行）
- 顶部"待确认"卡上：
  - `Enter` → 接受默认分类
  - `1` / `2` / `3` → 选另外两个候选
  - `Esc` → 撤销

如果 `auto_confirm: true` 且置信度高，**根本不需要确认**——只看一眼通知就行。

---

## 五、出错排查

- Shortcut 显示 `401`：Token 错了或没填到 Header
- Shortcut 显示 `网络错误`：手机和 Mac 不在同一 Wi-Fi，或 Mac 没绑 0.0.0.0
- Mac 收到了但不入库：在 `~/.expense_tracker/data.db` 旁的日志看 `[ingest]` 行；置信度太低会留作"待确认"
- iCloud 文件不被处理：检查 `inbox_dir` 路径里的中文字符是否被转义；`bash expense_tracker/diagnose.sh` 会报路径权限

---

## 六、安全说明（一句话）

- Token 防的是**同 Wi-Fi 邻居**乱发，不防被入侵的设备
- 数据始终在你 Mac 的 SQLite 文件里，不出局域网
- 想再严一点：把 `auth.allow_localhost` 设为 `false`，本机也要 Token；或者前面挂一层 `caddy` 反向代理 + TLS
