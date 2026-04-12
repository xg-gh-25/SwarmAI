---
name: weekly-revenue-report
description: >
  Generate weekly Revenue & Usage reports for CMHK leadership.
  Supports two scopes: GCR overall (CEO level) and per-BU (GM level, any sh_l3).
  TRIGGER: "weekly report", "CEO report", "GM report", "周报", "revenue report".
---

# Weekly Revenue & Usage Report

为 CMHK 领导层生成周度 Revenue & Usage 报告。**当前版本：v4**（2026-03-31）。

## 能力范围

| Scope | 说明 | 模板 | 邮件格式 |
|---|---|---|---|
| GCR Overall | CEO 级简易版 | `ceo_lite.py` | **HTML 正文内嵌**，无附件，无寒暄 |
| Per-BU (sh_l3) | GM 级完整版 | `detailed.py` | HTML 附件 + 问候语 |
| All | GCR + 全部 13 个 BU | 自动路由 | 按收件人合并 |

## 两版报告对比

| | CEO 简易版 (ceo_lite) | GM 完整版 (detailed) |
|---|---|---|
| **布局** | 单页滚动，无 Tab | 三 Tab 交互 (Overall→Usage→Revenue) |
| **KPI 卡片** | 2 大卡 + 4 小卡，PNG line chart (base64 内嵌) | 6 卡片，SVG line sparklines |
| **表格 Sparkline** | PNG mini line chart (base64 `<img>`, 56×18px) | SVG bar chart (柱状图) |
| **Top 10 Accounts** | ❌ 无 | ✅ 有 (by Revenue Δ) |
| **WoW Attribution** | ❌ 无 | ✅ 有 (Accel/Decel) |
| **样式** | 全 inline（零 CSS class，零 `<style>`，零 flex，零 SVG） | CSS class + `<style>` block |
| **Sparkline 技术** | SVG → cairosvg → PNG → base64 `<img>` | 原生 SVG（浏览器打开） |
| **邮件兼容性** | ✅ 邮件正文直接渲染（Gmail + Outlook Desktop + OWA） | ❌ 需作为附件下载用浏览器打开 |
| **WoW 阈值** | `abs >= 1%` 红绿箭头，`< 1%` 灰色无箭头无符号 | 同左 |
| **配色** | Usage=蓝系，Revenue=暖色系 | 6 色系统 |
| **依赖** | `cairosvg` (pip) | 无额外依赖 |

## 参数

| 参数 | 必须 | 说明 |
|---|---|---|
| `scope` | ✅ | `gcr`（整体）、sh_l3 BU 名称（如 `"ISV & SUP"`, `"STRATEGIC"`, `"RFHC"` 等）、或 `all` |
| `template` | ❌ | `summary`（旧版 2 卡片）或 `detailed`（v3 三Tab）。默认：gcr→summary, BU→detailed |

## 依赖

| 依赖 | 路径 / 安装 | 用途 |
|---|---|---|
| DataProxy 客户端 | `infra/data-proxy/client.py` | Athena 查询代理 |
| 数据表知识 | `knowledge/data/fact_estimated_revenue/field_dictionary.md` | 字段字典 |
| cairosvg | `pip install cairosvg` | CEO Lite: SVG→PNG 转换 |

## 数据源
- **表**: `fact_estimated_revenue` (Athena, cn-north-1 / rl_quicksight_reporting)
- **筛选**: `fbr_flag = 'Y'`, `sh_l1 = 'GCR'`
- **时间窗口**: 锚定上周四（无数据降级到周三），CW = 7 天，PW = 前 7 天
- **6W Trend**: 从 CW 向前回溯 6 周，每周独立查询 CORE/GenAI 拆分

## 产出位置
```
output/weekly-revenue-report/
├── latest/               最新一期（周一推送用）
│   ├── gcr_detailed.html CEO 简易版（ceo_lite 模板，base64 sparklines）
│   ├── <bu>.html         各 BU（如 rfhc.html）— GM 完整版 三Tab
│   └── ...               共 13 个 BU html
├── midweek/              RFHC 周四独立报告
│   └── rfhc_midweek.html
└── history/              历史归档
    └── YYYY-MM-DD/
```

## 使用

```bash
# 生成 CEO 简易版（ceo_lite 模板，邮件正文内嵌用）
python3 skills/weekly-revenue-report/generator.py --scope gcr --template detailed

# 生成单个 BU 的 GM 级周报
python3 skills/weekly-revenue-report/generator.py --scope "RFHC"

# 生成所有（GCR + 13 BU），用于 cron 预生成
python3 skills/weekly-revenue-report/generator.py --scope all

# RFHC Mid-Week 独立报告（自然周 Mon-Sun）
python3 skills/weekly-revenue-report/midweek_generator.py              # 只生成
python3 skills/weekly-revenue-report/midweek_generator.py --preview    # 生成 + 发预览给 fuxin+qiuyac
python3 skills/weekly-revenue-report/midweek_generator.py --send       # 生成 + 发给 kenshen
```

## 定时任务

| 任务 | Cron ID | 时间 (UTC) | 北京时间 | 说明 |
|---|---|---|---|---|
| 周报预生成 | `weekly-report-prepare` | 周日 10:00 | 周日 18:00 | `--scope all` 生成全部 → 发预览 |
| 周报正式发送 | `weekly-report-send` | 周日 22:30 | 周一 06:30 | `sender.py` 发给 10 位收件人 |
| RFHC Mid-Week 预生成 | `midweek-rfhc-prepare` | 周三 10:00 | 周三 18:00 | `midweek_generator.py --preview` → 发预览给 fuxin+qiuyac |
| RFHC Mid-Week 发送 | `midweek-rfhc-send` | 周三 22:30 | 周四 06:30 | `midweek_generator.py --send` → 发给 kenshen |

### Cron 执行流程
**预生成 (weekly-report-prepare)**:
1. `cd skills/weekly-revenue-report && python3 generator.py --scope all`
2. 失败则等 30s 重试一次
3. 仍然失败 → 跨账号 Lambda 发告警邮件给 fuxin@amazon.com
4. 成功 → 发预览邮件到 dataretriever-receiver@amazon.com（14 个 HTML 附件）
5. Slack 通知 #data-retriever-receiver (C0ALRH3JCJD)

**正式发送 (weekly-report-send)**:
1. `python3 skills/weekly-revenue-report/sender.py`
2. sender.py 自动读取 latest/ 目录，解析 week number，按 RECIPIENTS 合并发送
3. 失败自动重试一次，仍然失败 → 告警邮件
4. Slack 通知发送状态

## 邮件发送（v3，2026-03-31）

### 发送方式
```bash
# Dry run（验证，不实际发送）
python3 skills/weekly-revenue-report/sender.py --dry-run

# 正式发送（含自动重试）
python3 skills/weekly-revenue-report/sender.py
```

### 两种邮件模式

**CEO 模式（arobchu）— 正文内嵌**
- HTML 报告直接平铺在邮件正文，无附件
- 无问候语（"Hi Rob"）、无签名（"Best, DataRetriever"）
- 纯报告内容，打开邮件即看
- 使用 `ceo_lite.py` 模板（全 inline CSS，邮件客户端友好）

**GM 模式（其他 9 位）— 附件**
- 邮件正文：`Hi {first_name}, Your weekly Usage & Revenue Report is ready. Please open the attached HTML file...`
- 附件：`{BU}_Weekly_Report_W{week}.html`（三 Tab 交互，需浏览器打开）

### 邮件参数

| 字段 | CEO 模式 | GM 模式 |
|---|---|---|
| Subject | `CMHK Weekly Usage & Revenue Report — W{week} (Mon DD-DD)` | 同左 |
| Sender | `DataRetriever <dataretriever@amazonaws.cn>` | 同左 |
| Body | 报告 HTML 全文 | 简短问候 + 引导 |
| 附件 | ❌ 无 | `{BU}_Weekly_Report_W{week}.html` |
| CC | ❌ | ❌ |

### 收件人（v3，2026-03-31 更新）

| 收件人 | 姓名 | 报告 | 模式 |
|---|---|---|---|
| arobchu | Rob Chu | GCR Overall | **CEO inline** |
| kenshen | Ken Shen | RFHC | 附件 |
| zhangaz | Alfonso Zhang | ISV & SUP | 附件 |
| mzji | Ken Li | MEAGS + DNBP | 附件 |
| tiafeng | Feng Tian | AUTO & MFG | 附件 |
| akchan | Andy Chan | HK | 附件 |
| danffer | Danffer Ni | IND GFD + NWCD + SMB | 附件 |
| chrisso | Chris So | PARTNER + HK | 附件 |
| gufan | Fan Gu | STRATEGIC | 附件 |
| ligxi | Coleman Li | FSI-DNB | 附件 |

---

## v3 报告规格（2026-03-29 上线，当前版本）

v2 的无图版本已备份为 `templates/detailed_v2_legacy.py`。

### Tab 顺序
📋 **Overall**（默认选中）→ ⚡ Usage *Core/GenAI* → 💰 Revenue *Core/GenAI*

### 6W Trend Sparklines（v3 核心新增）
- **数据源**: 最近 6 周的周级 Revenue/Usage，自动计算 CORE/GenAI 拆分
- **Breakdown 表**: 每行 CORE CW/PW/WoW/**6W** | GenAI CW/PW/WoW/**6W** | **Total 6W Trend**
  - CORE/GenAI 6W = mini 柱状图（52×18px）
  - Total 6W = 正常柱状图（80×28px）
  - GenAI Mix 列已移除
- **Top 10 表**: 末尾加 "Total Usage/Revenue 6W Trend" 列
- **WoW Attribution**: 每个 Accel/Decel 账户行加 6W mini sparkline
- **Summary 卡片**: 数字右侧显示 SVG 折线图（淡色填充+半透明线条）
- **Overall 卡片**: 6 个卡片均有折线图
- **页眉**: 显示 6W 周号 + 完整日期范围 + mini 折线图标

### WoW Attribution
- Usage tab: **Total Usage** → CORE Usage → GenAI Usage
- Revenue tab: **Total Revenue** → CORE Revenue → GenAI Revenue

### Overall Tab（CEO vs GM 差异）
- **CEO (GCR)**: Summary Cards + Breakdown 表（无 Top 10）
- **GM (per-BU)**: Summary Cards + Breakdown 表 + **👑 Top 10 Accounts (by Revenue Δ)** — 同时展示 Usage 和 Revenue 的 CW/PW/WoW/6W Trend，按 Revenue delta 降序排名

### 颜色方案（6 色系统）
| | CORE | GenAI | Total |
|---|---|---|---|
| **Usage** | `#0ea5e9` 天蓝 | `#6d28d9` 深紫 | `#1e40af` 深蓝 |
| **Revenue** | `#d97706` 深琥珀 | `#059669` 翡翠绿 | `#1a365d` 深藏蓝 |

### 数据 Bug 修复（v3 同期）
- Q3a 查询（Top 10 accounts）原先 `GROUP BY account, genai_flag` 无 LIMIT，导致 Lambda 9999 行截断 → 修复为 2-step 查询
- `fetch_movers()` / `fetch_total_movers()` / Top 10 查询均加了 `LOWER(TRIM(sfdc_account_name)) <> 'unknown'` 过滤

### ⚠️ 注意事项
- Tab HTML 在邮件客户端不工作（Gmail/Outlook 砍 CSS），只能作为附件下载后浏览器打开
- 邮件正文不嵌入任何报表数据，只有简短问候 + 引导打开附件

---

## 质量验证 (Eval)

修改 `templates/`、`data.py`、`generator.py`、`sender.py` 等核心文件后，**必须**运行 eval 验证：

```bash
# 快速检查产出物（秒出，不调 API）
python3 evals/run_eval.py --json --eval-ids 1,2,3

# 完整 E2E（调 Athena 重新生成 + 验证，~5-10 分钟）
python3 evals/run_eval.py --live --json
```

自动触发机制：
- **Git hook**: `post-commit` 检测核心文件变更 → 自动跑 eval
- **Cron**: 每周日 10:30 UTC（预生成后 30 分钟）→ 自动跑 eval 1/2/3 → 结果通知 Slack

## 代码结构

```
skills/weekly-revenue-report/
├── SKILL.md              本文件
├── generator.py          CLI 入口（--scope / --template）
├── midweek_generator.py  RFHC 周四独立报告（自然周 Mon-Sun）
├── data.py               所有 Athena 查询（数据层）
├── utils.py              工具函数（wow_pct, safe_name 等）
├── html_helpers.py       HTML 工具
├── sender.py             邮件发送（v3，CEO inline + GM 附件 + dry-run + 自动重试）
├── experiment_6w_trend.py  实验脚本（6W trend 原型，不在生产路径）
└── templates/
    ├── __init__.py
    ├── ceo_lite.py           CEO 简易版（单页 inline HTML，SVG→PNG base64 sparklines，邮件正文用）
    ├── detailed.py           GM 完整版（v3 三Tab，附件用）
    ├── detailed_v2_legacy.py v2 备份（无 6W trend）
    └── summary.py            旧版 2 卡片单页
```

### 数据获取函数 (data.py)

| 函数 | 用途 |
|---|---|
| `find_anchor_and_weeks()` | 锚定数据日期，定义 CW/PW 窗口 |
| `fetch_all_bu_names()` | 查询所有 sh_l3 BU 名称 |
| `fetch_gcr_data()` | GCR 整体数据（summary 模板用） |
| `fetch_detailed_data()` | Detailed 模板核心数据：overall、sh_l4 breakdown、Top 10、CORE/GenAI movers |
| `fetch_movers()` | CORE 或 GenAI 的 mover（按 genai_flag 过滤） |
| `fetch_total_movers()` | Total mover（不分 genai_flag） |
| `compute_6_weeks()` | 计算 6 周窗口 |
| `fetch_6w_segment_data_split()` | 6W segment（sh_l3/l4）趋势，CORE/GenAI 拆分 |
| `fetch_6w_account_data_split()` | 6W account 趋势，CORE/GenAI 拆分 |
| `fetch_summary_data()` | Summary 模板数据 |
| `fetch_last_refresh()` | 查最新数据刷新时间 |
| `find_natural_weeks()` | Mid-Week 用：自然周 Mon-Sun 窗口 |
| `compute_6_natural_weeks()` | Mid-Week 用：6 个自然周窗口 |

### HTML 生成函数 (templates/detailed.py)

| 函数 | 用途 |
|---|---|
| `generate_detailed_html()` | v3 主入口：三Tab HTML + 6W Trend sparklines |
| `sparkline_bars()` | SVG 柱状图 sparkline |
| `sparkline_line()` | SVG 折线图 sparkline（Summary 卡片用） |
| `mover_section_6w()` | WoW Attribution 区块（含 6W mini sparkline） |

---

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-03-11 | 初版 CEO 周报上线 |
| v1.5 | 2026-03-24 | GM 周报上线 |
| v2 | 2026-03-26 | CEO/GM 统一 3-Tab HTML 附件格式 + 极简英文邮件 |
| v3 | 2026-03-29 | 6W Trend sparklines、颜色方案 6 色系统、Tab 顺序改为 Overall→Usage→Revenue、WoW Attribution 加 Total、数据 bug 修复 |
| **v4** | **2026-03-31** | **CEO 简易版独立模板 `ceo_lite.py`**：单页白底、KPI 卡片 + line chart（SVG→PNG→base64 `<img>`，Outlook Desktop 兼容）、全 inline CSS。**sender.py v3**：CEO 模式（正文内嵌，无附件无寒暄）vs GM 模式（附件 + 问候）。GM WoW `abs<1%` 去掉正负号。**midweek_generator.py**：RFHC 周四独立报告（自然周 Mon-Sun）。`cairosvg` 依赖新增 |
