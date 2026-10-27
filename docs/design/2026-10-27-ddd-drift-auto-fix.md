---
title: "DDD 漂移：确定的自动修"
created: 2026-10-27
updated: 2026-10-27
---

# DDD 漂移：确定的自动修，报告写"发现了什么、修好了什么"

2026-10-27 · native · 起因：Eval overlay 去掉 Context Health tab 后，每周 `ddd_self_audit` 的结果没有任何读者。

## 1. 现状（2026-10-27 实测）

- `jobs/handlers/ddd_self_audit.py`：每周给每个 DDD 项目起一个只读 agent（Read/Grep/Glob），找文档自相矛盾、和代码不符、过时的说法，结果写成 `Knowledge/JobResults/<日期>-ddd-self-audit.md`。只检测不修改，修复靠人跑 s_persist。
- 读者只有 `core/ddd_drift_signal.get_semantic_drift`（`GET /api/eval/context-health`），现在没有界面读它。不建 ToDo（run_50db230a）。
- 抽查 2026-10-26 报告里的 10 条（每隔 4 条取 1 条，覆盖 7 个项目），逐条对照原文：**9 条属实且有价值，1 条是把带日期的经验记录当成矛盾**。
- 同一批问题每周重报：PhysicalAI 的"5 月中—6 月中"、IVTHub 的"42 个全部映射"连续 6 周都在；条数从 14 涨到 39。标题每周不同，是模型措辞不同，底下是同一个问题。
- 结论：信号是真的，缺的是"修"这一步。

## 2. 目标

XG 2026-10-27："确定的都自动修，而不是报出来；报告应该是发现了什么问题、自动解决了什么。"

- 每条发现要么被自动修好，要么进"需要你"并写明为什么没自动修。
- 报告和任务摘要第一句是"发现 N · 自动修好 M · 需要你 K"。
- 修改可以完整撤回。

## 3. 设计

### 3.1 谁来改：agent 只提议，OS 决定和写入

审查 agent 继续只读（`_AUDIT_TOOLS` 不加 Write/Edit/Bash）。它在每条发现里多给一个修法：

```json
{"title": "...", "description": "...",
 "fix": {"doc": "TECH.md",
         "old": "<文档里要换掉的原文，逐字>",
         "new": "<换成什么>",
         "evidence": [{"file": "<相对路径>", "quote": "<证明 new 正确的原文，逐字>"}]}}
```

正确文字不能完全由证据决定时（比如"当前阶段"该写什么只有主人知道），agent 不给 `fix`。Python 判断能不能修、负责写入。这样"模型不能直接改文件"的结构保证还在，决定权在确定性检查上（SOUL P5）。

### 3.2 "确定的" = 下面每一条都过

| 检查 | 规则 | 不过时 |
|---|---|---|
| 位置 | `doc` 经 `ddd_cultivation._confined_doc_path` 解析在项目目录内，是普通文件 | 需要你：位置不合法 |
| 原文 | `old` 至少 12 个字符，在该文档里**恰好出现一次** | 需要你：原文找不到 / 出现多次 |
| 证据 | 每条 `quote` 逐字出现在它的 `file` 里；`file` 限定在本项目目录、`Knowledge/`、或项目 bindings 声明的源码仓库内；至少一条证据 | 需要你：证据找不到 |
| 改动形状 | `new` 非空、不同于 `old`；是一段（无空行）、不含标题行、不含 `<!--` / `-->`（条目元数据）；长度 ≤ max(2×len(old), len(old)+300) | 需要你：改动太大或会破坏结构 |
| 准入 | `ingestion_gate.self_adversarial_judge(new, 段落标题, 证据原文)` 返回 pass | 需要你：judge 未通过（附理由） |

第五条是 P8：所有写进大脑的门共用 `self_adversarial_judge` 这一个关口（cultivation 的每条自动写入都过它），漂移修复也走它，不另起一套标准。前四条是这扇门比 cultivation 多出来的：一条修复带着可以逐字核对的原文和证据，核对比 judge 更硬，所以先查。

没给 `fix` 的发现：需要你，理由写"正确内容只有你知道"。

### 3.3 写入：先留底，再改

- 用所有 DDD 写入方共用的锁 `utils.file_lock.md_lock`（`<doc>.md.lock`，非阻塞；拿不到锁这条记为"需要你：文档正被写入，下次再试"）。
- 写之前：把整份文档复制到 `Projects/<p>/.artifacts/drift-fix-backups/<doc>.<时间戳>.md`，并在 `Projects/<p>/.artifacts/ddd-changelog.jsonl` 追加一行 `{"action": "drift-fix", "target_doc", "old"(全文), "new", "evidence", "report", "timestamp"}`。备份或日志写不成功就不改。
- 写入时在锁内重读文档，`old` 仍只出现一次才替换（防止两条修复撞在同一处）。
- DDD 周报（`s_ddd-weekly-report`）本来就读 `ddd-changelog.jsonl`，自动修复会出现在那里，不用改它。

### 3.4 报告

```
# DDD Self-Audit — 2026-11-02
发现 31 · 自动修好 24 · 需要你 7（9 个项目，全部审完）

## 自动修好
- [IVTHub/TECH.md] "All 42 mapped" → "..."  证据：TECH.md "grown well past the old 42"
## 需要你
- [PhysicalAI/PROJECT.md] 当前重点还是 5–6 月  · 没自动修：正确内容只有你知道 · 首次出现 2026-09-21
## 各项目原始审查
...
```

- "首次出现"：在以往报告里逐字搜这条的 `old` 原文（没有 `old` 时搜描述里引号内的原文），取最早的日期。不新增存储，报告本身就是记录。
- 每个项目段落末尾写一个机器可读块 `<!-- DRIFT_OUTCOMES [...] -->`（每条：title、doc、结果 fixed/open、理由）。`ddd_drift_signal` 改为只把 open 的算作漂移；读旧报告（没有这个块）时行为不变。
- 任务摘要（Jobs & Runs 里看到的）= 第一行。

### 3.5 试运行

`config: {"dry_run": true}`：照常审查、照常检查，只写报告不改文档，报告标题标"试运行"。第一次上线先跑一次试运行，看清会改什么再正式跑。

## 4. 数据形状变更与读者

| 写入方 | 新形状 | 读者 |
|---|---|---|
| 审查 prompt | 发现带 `fix` | `_parse_fix`（新）；旧格式没有 `fix` → 需要你 |
| 报告 | 首行摘要、两节、`DRIFT_OUTCOMES` 块 | `ddd_drift_signal`（只数 open）；人 |
| `ddd-changelog.jsonl` | `action: drift-fix` | `s_ddd-weekly-report`（已读该文件）|

## 5. 不做

- 界面（Brain Hub 显示"需要你"的条目）：下一步单独做。
- ToDo：不写（run_50db230a 的决定不变）。
- 修代码：只改 DDD 文档，代码不对的情况只报告。

## 6. 验证计划（现在定）

- 每条检查一个会失败的测试，修完做变异检查。
- 写入：备份和日志先于修改；备份失败不改；锁内 `old` 变成两次时不改；撤回（用备份还原）后文档逐字节一致。
- 报告：摘要行的计数、两节内容、`DRIFT_OUTCOMES` 块；`ddd_drift_signal` 新旧报告都能读。
- 最终测试集：`test_ddd_self_audit*`、`test_ddd_drift_signal*`、`test_ddd_cultivation` 里锁和路径相关的测试，加本次新增测试。
- 审查：correctness + security（模型输出决定写哪个文件、写什么；文档内容可能含提示注入）。
- 端到端：本机跑一次试运行，看报告；再正式跑一次，抽查改动和备份。
