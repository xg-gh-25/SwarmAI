---
title: "DDD 漂移：确定的自动修"
created: 2026-10-27
updated: 2026-10-27
status: r3
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

## 3. 设计（r2：零上下文审查后重写）

### 3.0 先修的前提：审查 agent 原来并不只读

命令只给了 `--allowedTools Read,Grep,Glob`。在 bypassPermissions 下它只决定"哪些工具自动放行"，不决定"哪些工具存在"，agent 其实一直能用 Bash / Write / Edit（`scripts/scenario_runner.py` 记过同一个教训）。已修：命令改由纯函数 `_build_review_cmd` 生成，同时给 `--tools Read,Grep,Glob`（`dbb6ac272`）。r3 再补两处（security 审查）：不加载任何 MCP（原来传了全部 MCP 配置，Slack / 邮件工具都自动放行，是把读到的东西送出本机的通道）；改用 `--restricted --permission-mode dontAsk`，文件工具只能读工作区和绑定仓库（实测：读这两处之外的文件被拒）。

### 3.1 谁来改：agent 只提议，OS 核对后写入

agent 在每条发现里可以多给一个修法：

```json
"fix": {"doc": "PROJECT.md",
        "old": "<文档里要换掉的一行里的原文，逐字>",
        "new": "<换成什么>",
        "evidence": [{"kind": "done|superseded|exists|code", "file": "<相对路径>", "quote": "<原文，逐字>"}]}
```

正确内容不能由证据完全决定时不给 `fix`。Python 核对、写入；模型从头到尾碰不到文件（SOUL P5）。

### 3.2 "确定的"：证据本身决定答案

"文档里两段话互相矛盾"本身不能说明哪段是对的：证据那一段也可能才是过时的（抽查里 AIDLC 的 Gate 0 就分不清）。所以"确定"的标准不是"找到一段相反的话"，而是**证据属于下面四类之一，且 OS 能逐字核对**：

| kind | 证据是什么 | OS 核对 |
|---|---|---|
| `done` | 同一项目里该事项已勾掉 | quote 逐字在 file 里，且是一行 `- [x]` 清单 |
| `superseded` | 被引用的文档自己写着已被取代 | quote 逐字在 file 里，且含 `SUPERSEDED` / `superseded by` / `已被取代` |
| `exists` | `new` 里换上的路径真实存在 | 该路径在工作区或绑定仓库里是文件 |
| `code` | 代码与文档不符（只限有源码仓库的项目） | quote 逐字在绑定仓库的文件里，且该文件不在 test / fixture 目录 |

再加上下面全部：

- **证据来源**（r3 收紧）：`done` 只认本项目的 DDD 文档；`code` 只认绑定仓库里的源码文件（按扩展名，排除 test / fixture 目录和 `test_*`、`*_test.*` 文件）；`superseded` / `exists` 只认本项目目录和绑定仓库。都不认 `.artifacts/`（大小写不敏感），不认多重硬链接的文件。r2 允许整个工作区，任何笔记都能当证据。
- **证据得和这条说法有关**（r3 新增）：每条证据和 `old` 至少共享一段 8 个字符的原文；`superseded` 还要求 `old` 里点名了那份文档。r2 时一条无关的 `- [x] Write the README` 就能给任何修改作证。
- **只能说证据说的话**（r3 改写）：修改只允许两种形状：从 `old` 里删掉连续的一段；或者把一段换成在某条证据里**按整词逐字出现**的文字。其它任何词都不能加。r2 只限制"像事实的词"，普通词可以随便加，一段注入的指令能整句写进文档。
- **只改一行里的一段**：`doc` 必须是 `DDD_CANONICAL_DOCS` 之一（经 `_confined_doc_path` 解析在项目内）；`old` 至少 12 个字符、在文档里恰好出现一次；`old` 和 `new` 都不含任何控制字符或行/段分隔符（`\r`、`\u2028` 等，`splitlines` 会在这些字符处断行）、不含 ```` ``` ```` / `~~~`、`<!--` / `-->`；表格的 `|` 数和复选框状态不变；行首的 markdown 标记不变；不在标题（含 setext）、front matter、代码块、元数据注释里。文档里本身有异常行分隔符的不自动改。
- **不改带日期的记录**：`old` 落在一条生命周期条目里（`ddd_entry_lifecycle.parse_entries` 能解析出的 `- **标题** …` 条目）就不改。这种条目是"当时为真"的历史记录，抽查里唯一的误报（CMHK）就是这类；以后要改用已有但还没人调用的 `mark_superseded`（保留原条目、标记被取代），不在这次范围。
- **不来回改**：改完的文档会重新出现以前某次自动修复删掉的原文，就不改；你撤回过的修复不再重做。

任何一条不过：进"需要你"，理由写出来（"证据只是另一段说法，分不出哪边对""正确内容只有你知道""这是一条带日期的记录"……）。

**不用 `self_adversarial_judge`。** 它的 prompt 写明自己是"噪音过滤器，不是事实核查"，看不到 `old`，会放过任何具体、自信的句子。漂移修复这扇门的准入是上面的逐字核对，比 judge 硬；cultivation 的门之所以用 judge，是因为一条经验没有可以逐字核对的证据（P8：两扇门因为证据形态不同而不同，写明原因）。

按这个标准，抽查的 9 条真问题里大约 1–3 条能自动修（SecDLC 的"等它存在"、ai_ready_repo 的 M1、指向已被取代的设计文档），其余进"需要你"。少修是对的：宁可不修，不能改错。

### 3.3 写入和撤回

- 顺序（r3，correctness 审查）：锁内重新核对 → 备份 → 记录 → 写入前确认文件的 mtime 和大小与核对时相同 → 原子替换。备份或记录写不成：不改。替换失败：追加一条 `failed`，读者不把它算作修复。
- 加锁 `utils.file_lock.md_lock`（cultivation、orchestrator、retire 共用；s_persist 和人手改不加锁，所以锁内要重读）。拿不到锁：需要你，"文档正被写入，下次再试"。
- 锁内重读文档，`old` 仍恰好一次、文件 mtime 和大小与核对时相同，才写；写法是同目录临时文件 + `os.replace`。
- 写之前追加一行到 `Projects/<p>/.artifacts/ddd-drift-fixes.jsonl`：`{run, target_doc, old, new, evidence, finding, timestamp}`，写不成就不改。**单独一个文件，不进 `ddd-changelog.jsonl`**：那份日志有好几个读者（周报的"已应用"计数、Brain Hub 的 sediments、maturity、health）都按"追加了一条经验"来数，修复不是经验。
- 每次运行、每份文档在第一次修改前整份备份一次到 `.artifacts/drift-fix-backups/<doc>.<运行id>.md`，每份文档只留最近 10 个。这只是灾难备份。
- **撤回单条**：`py -m scripts.ddd_drift_fix_cli list <项目>` / `undo <项目> <序号>`（`undo_drift_fix`）把 `new` 换回 `old`，要求 `new` 当前恰好出现一次，否则拒绝（之后又被人改过）；撤回记一行 `undone`，下次审查不会再把它改回去。不用整份备份回滚：那会冲掉之后所有别的写入。备份保留最早一份（第一次自动修改之前）和最近 9 份。
- 说明：除 SwarmAI 外的项目目录不在 git 里（SwarmWS `.gitignore` 的 `Projects/*`），git 不是恢复途径，撤回只靠上面这份记录。

### 3.4 报告

```
# DDD Self-Audit — 2026-11-02
发现 31 · 自动修好 3 · 需要你 28 · 审完 9/9 个项目

**自动修好**
- [SecDLC/PROJECT.md] "once `s_security-cr-review` exists" → "now that `s_security-cr-review` exists"（证据 done：PROJECT.md "- [x] **P5 (REVIEW)** …"）
**需要你**
- [PhysicalAI/PROJECT.md] 当前重点还是 5–6 月 · 正确内容只有你知道 · 首次出现 2026-09-21

## PhysicalAI (non-code) — 3 finding(s)
...（各项目原始审查，和现在一样）
<!-- DRIFT_OUTCOMES [{"title": "...", "doc": "...", "outcome": "fixed|open", "reason": "..."}] -->
```

- 摘要部分用加粗标签，不用 `##`：`ddd_drift_signal._SECTION_RE` 按每个 `##` 切项目，摘要用 `##` 会变成假项目。各项目段落保持 `## <项目> (kind) — N finding(s)`。
- "首次出现"：在以往报告（不含试运行报告）里逐字搜 `old`，没有 `old` 时搜描述里第一段引号内的原文，取最早的日期。
- `ddd_drift_signal` 改为只把 `open` 的算作漂移，按发现在列表里的位置对应（标题可能重复），读每段最后一个 `DRIFT_OUTCOMES` 块；agent 原文里出现的同名标记写报告前会被改掉，块里的 `-->` 会转义，agent 伪造不了"已修"。没有这个块的旧报告照旧全算。
- 任务摘要 = 报告第二行。

### 3.5 试运行

`config: {"dry_run": true}`：照常审查、照常核对，不写文档、不写修复记录。报告文件名用 `ddd-self-audit-dryrun`（不被 `*-ddd-self-audit.md` 匹配，所以不会盖掉当天的正式报告，也不会被漂移信号读到），结果写 `would-fix`。

## 4. 数据形状变更与读者

| 写入方 | 新形状 | 读者 |
|---|---|---|
| 审查 prompt | 发现可带 `fix` | `_parse_fix`（新）；没有 `fix` → 需要你 |
| 报告 | 摘要两段（加粗标签）、`DRIFT_OUTCOMES` 块 | `ddd_drift_signal`（只数 open）；首次出现的查找（读以往报告）；人 |
| `ddd-drift-fixes.jsonl`（新） | 每次修复一行 | `undo_drift_fix`；"不来回改"的检查；`s_ddd-weekly-report`（加一节"自动修复"，读它） |
| `ddd-changelog.jsonl` | 不变 | 不受影响 |

## 5. 不做

- 界面（Brain Hub 显示"需要你"）：下一步单独做。
- 带日期条目的修正（`mark_superseded`）、代码不对的修正：只报告。
- ToDo：不写（run_50db230a 不变）。
- 已知、不在范围：`scripts/artifact_cli.py` 的 `ddd-retire` 提示还说"写了 .bak"，其实已经不写。

## 6. 验证计划（现在定）

- 每条检查一个测试（过和不过各一），做变异检查。
- 写入：记录先于修改，记录写不成不改；锁内 `old` 变两次 / 文件被改过时不改；撤回后逐字还原，`new` 已被人改过时撤回拒绝；备份每份文档每次运行一个、只留 10 个。
- 报告：计数、两段、`DRIFT_OUTCOMES`；`ddd_drift_signal` 读新旧报告，摘要不被当成项目；试运行文件名不被漂移信号读到。
- 周报：自动修复一节读到新文件，已应用计数不变。
- 最终测试集：`test_ddd_self_audit`、`test_ddd_drift_signal`、`test_ddd_weekly_report`（或其所在文件），加本次新增测试。
- 审查：correctness + security。
- 端到端：本机试运行一次看报告，再正式跑一次，逐条核对改动和撤回记录。
