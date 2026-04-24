# 牙膏类目本地选品与定价工具

一个只在本机运行的本地网页工具，用于管理门店牙膏类目的现有 SKU、候选新品、结构分析与定价建议。

## 功能
- 导入现有牙膏 SKU 的 `Excel/CSV`
- 本地维护候选新品库
- 一键抓取京东 / 天猫 / 小红书 / 淘宝热销牙膏并自动入候选池
- 自动匹配候选新品与门店现有 SKU 做对比
- 自动生成新品优先上新/替换/观察清单
- 输出价格带、品牌、功效、角色定位的结构分析
- 给出售价、毛利率、淘汰/观察/上新建议
- 生成本地 SQLite + JSON 备份

## 启动
在当前目录运行：

```powershell
.\run.ps1
```

启动后访问：

```text
http://127.0.0.1:8765
```

## 数据位置
- SQLite 数据库：`data/toothpaste_tool.sqlite3`
- 导入原文件留存：`data/imports/`
- 备份文件：`data/backups/`

## 自动抓取说明
- 在“候选新品库”页面使用“热销抓取器”
- 默认抓取：京东、天猫、小红书、淘宝
- 如平台未登录页面返回结果较少，可粘贴对应浏览器 Cookie 提高抓取成功率

## 模板
- `samples/现有牙膏SKU导入模板.csv`
- `samples/候选牙膏新品模板.csv`

## 测试
使用工作区自带 Python 运行：

```powershell
& 'C:\Users\a1987\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests
```
