---
title: "DDD 漂移：确定的自动修"
created: 2026-10-27
updated: 2026-10-27
status: r4
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

## 3. 设计（r4）

### 3.0 先修的前提：审查 agent 原来并不只读

命令只给了 `--allowedTools Read,Grep,Glob`。在 bypassPermissions 下它只决定"哪些工具自动放行"，不决定"哪些工具存在"，agent 其实一直能用 Bash / Write / Edit（`scripts/scenario_runner.py` 记过同一个教训）。已修：命令改由纯函数 `_build_review_cmd` 生成，同时给 `--tools Read,Grep,Glob`（`dbb6ac272`）。r3 再补两处（security 审查）：不加载任何 MCP（原来传了全部 MCP 配置，Slack / 邮件工具都自动放行，是把读到的东西送出本机的通道）；改用 `--restricted --permission-mode dontAsk`，文件工具只能读工作区和绑定仓库（实测：读这两处之外的文件被拒）。

### 3.1 r4：只有三种修法，新文字由 OS 算出来

r3 允许"任意删一段，或者把一段换成证据里出现过的整词"。两轮审查（correctness + security）都构造出了能过全部检查的坏修改：删掉"不 / not"把意思反过来、从证据里借一个无关的"不"字插进去、从 `node_modules` 的注释里借一句话写进文档。结论：只要模型能提交自由文本，检查就封不住。XG 选了 A：只留三种窄修法，模型只说"是哪一种、改哪里"，新的一行由 OS 按固定语法计算。

| 修法 | agent 给的字段 | OS 核对 | OS 算出的新行 |
|---|---|---|---|
| `drop_condition` 删掉等待条件 | `line`（这一行里的一段原文）、`span`、`done` | `span` 整个匹配固定语法：once / after / until / when `` `X` `` exists / lands / ships / is built / done / ready / available / implemented / shipped / merged，或 等 / 待 `` `X` `` 存在 / 上线 / 完成 / 就绪 / 落地 / 建好（后）；`done` 是本项目 DDD 文档里一行 `- [x]`，且用反引号点名同一个 `X`（相等，或一方是另一方加前缀，如 `s_x` / `pkg/x`，名字至少 6 个字符）；这一行不能是它自己的证据 | 原行删掉 `span`，整理空格和标点 |
| `swap_superseded_path` 换成取代它的文件 | `path_old`、`path_new` | 两个都是路径；`path_old` 在文档里按整词只出现一次；`path_old` 指向的文件（本项目、工作区或绑定仓库内，不在 `.artifacts`）前 10 行里有一行以 SUPERSEDED / status: superseded / 已被取代 开头，且这一行里写着 `path_new`；`path_new` 存在 | 原行把 `path_old` 换成 `path_new` |
| `swap_code_value` 换成代码里的值 | `line`、`identifier`、`value_old`、`value_new`、`code_file` | 两个值都是数字或版本号；`code_file` 是绑定仓库里被 git 跟踪的源码文件（按扩展名；不在 test / fixture / vendor / node_modules / dist / build / .venv 下，不是 `test_*`、`*_test.*`）；它里面有 `identifier = value_new`（或 `:`）这样的赋值；文档这一行点名了 `identifier`，`value_old` 在这一行恰好出现一次 | 原行把 `value_old` 换成 `value_new` |

三种都还要满足：
- **只改一行**：文档是四份 DDD 规范文档之一，经 `_confined_doc_path` 解析在项目内；那一行不在 front matter、标题（含 setext，`=` 或 `-` 下划线）、代码块（```` ``` ```` 或 `~~~`）里，不带 `<!-- -->` 元数据，不在带日期的生命周期条目里。
- **文档本身正常**：文档里有 `\n` / `\r\n` 以外的换行符，或零宽、方向控制这类不可见字符，就不自动改。
- **不来回改**：你撤回过的修复（同一份文档，同样的旧行和新行）不再重做；新行等于以前某次自动修复的旧行时不改。

其它一律"需要你"，写明原因。不再有 `self_adversarial_judge`、`exists` / `done` / `code` 这类证据种类、"共享 8 个字符"之类的近似判断：每种修法都是精确语法。

### 3.2 审查 agent

只能读：`--tools Read,Grep,Glob`，不加载任何 MCP，`--restricted --permission-mode dontAsk`（文件工具只能读工作区和绑定仓库，实测越界读被拒）。这部分已经单独合进 main（`74613f367`、`6f59e62b1`）。

按这个标准，抽查的 9 条真问题里能自动修的大约 1–2 条（SecDLC 的"等 `s_security-cr-review` 存在后"，ai_ready_repo 指向被取代的设计文档），其余都需要你。少修是对的：宁可不修，不能改错。

### 3.3 写入和撤回

- 顺序（r3，correctness 审查）：锁内重新核对 → 备份 → 记录 → 写入前确认文件的 mtime 和大小与核对时相同 → 原子替换。备份或记录写不成：不改。替换失败：追加一条 `failed`，读者不把它算作修复。
- 加锁 `utils.file_lock.md_lock`（cultivation、orchestrator、retire 共用；s_persist 和人手改不加锁，所以锁内要重读）。拿不到锁：需要你，"文档正被写入，下次再试"。
- 锁内重读文档，旧行仍恰好一行、文件 mtime 和大小与核对时相同（核对前先取 stat），才替换那一行；写法是同目录临时文件 + `os.replace`，保留原文件权限。
- 写之前追加一行到 `Projects/<p>/.artifacts/ddd-drift-fixes.jsonl`：`{fix_id, status: applied, type, run, target_doc, old_line, new_line, evidence, finding, timestamp}`，写不成就不改。**单独一个文件，不进 `ddd-changelog.jsonl`**：那份日志有好几个读者（周报的"已应用"计数、Brain Hub 的 sediments、maturity、health）都按"追加了一条经验"来数，修复不是经验。
- 每次运行、每份文档在第一次修改前整份备份一次到 `.artifacts/drift-fix-backups/<doc>.<运行id>.md`，每份文档只留最近 10 个。这只是灾难备份。
- **撤回单条**：`py -m scripts.ddd_drift_fix_cli list <项目>` / `undo <项目> <序号>`（`undo_drift_fix`）把新行换回旧行，要求新行当前恰好出现一次，否则拒绝（之后又被人改过）；**先**记一行 `undone` 再改文档（记不成就不改；改失败追加 `undo_failed`，修复仍算有效），下次审查不会再把它改回去。不用整份备份回滚：那会冲掉之后所有别的写入。备份保留最早一份（第一次自动修改之前）和最近 9 份。
- 说明：除 SwarmAI 外的项目目录不在 git 里（SwarmWS `.gitignore` 的 `Projects/*`），git 不是恢复途径，撤回只靠上面这份记录。

### 3.4 报告

```
# DDD Self-Audit — 2026-11-02
发现 31 · 自动修好 3 · 需要你 28 · 审完 9/9 个项目

**自动修好**
- [SecDLC/PROJECT.md] (drop_condition) "…}`) once `s_security-cr-review` exists — grades the reviewer" → "…}`) — grades the reviewer"
**需要你**
- [PhysicalAI/PROJECT.md] 当前重点还是 5–6 月 · 正确内容只有你知道 · 首次出现 2026-09-21

（以下是各项目原始审查，每个项目一段 `## <项目> (kind) — N finding(s)`）
<!-- DRIFT_OUTCOMES [{"title": "...", "doc": "...", "outcome": "fixed|open", "reason": "..."}] -->
```

- 摘要部分用加粗标签，不用 `##`：`ddd_drift_signal._SECTION_RE` 按每个 `##` 切项目，摘要用 `##` 会变成假项目。各项目段落保持 `## <项目> (kind) — N finding(s)`。
- "首次出现"：在以往报告（不含试运行报告）里逐字搜修法的 `line`（或 `path_old`），没有修法时搜描述里第一段引号内的原文，取最早的日期。
- `ddd_drift_signal` 改为只把 `open` 的算作漂移，按发现在列表里的位置对应（标题可能重复），读每段最后一个 `DRIFT_OUTCOMES` 块；agent 原文里出现的同名标记和行首的 `#` 标题写报告前都会被改掉，块里的 `-->` 会转义，agent 伪造不了"已修"，也没法把块挤到别的段落。没有这个块的旧报告照旧全算。
- 任务摘要 = 报告第二行。

### 3.5 试运行

`config: {"dry_run": true}`：照常审查、照常核对，不写文档、不写修复记录。报告文件名用 `ddd-self-audit-dryrun`（不被 `*-ddd-self-audit.md` 匹配，所以不会盖掉当天的正式报告，也不会被漂移信号读到），结果写 `would-fix`。

## 4. 数据形状变更与读者

| 写入方 | 新形状 | 读者 |
|---|---|---|
| 审查 prompt | 发现可带 `fix`：三种类型之一和它的字段 | `parse_fix`（不是三种之一或字段不全 → 需要你） |
| 报告 | 摘要两段（加粗标签）、`DRIFT_OUTCOMES` 块 | `ddd_drift_signal`（只数 open）；首次出现的查找（读以往报告）；人 |
| `ddd-drift-fixes.jsonl`（新） | applied / failed / undone / undo_failed 行 | `past_fixes`（`undo_drift_fix`、CLI、"不来回改"）；`s_ddd-weekly-report`（"Drift Fixed Automatically" 一节，撤回的单独计数） |
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
