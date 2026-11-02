---
title: New Brain setup — extraction first: the knowledge standard reaches whoever writes
created: 2026-11-02
updated: 2026-11-02
status: r1 — design, for XG to read
principle: SWARMAI.md "What a DDD is for, and how it stays true" — quality is made at extraction; checks stay light
related:
  - docs/design/2026-10-27-new-brain-initial-ddd.md      # bootstrap-apply, survey, the offline eval
  - docs/design/2026-11-02-new-brain-setup-fixing-spec.md  # G1–G8 (placement, survey reading order, runner)
  - backend/core/ingestion/sources/s7_review.py           # CDF's session-review extraction standard
---

# New Brain setup — extraction first

## 0. 一句话

一个新 Brain 的首批知识，质量来自**提取**：提取的人在写每一条时，眼前就有"什么算知识、这一节从哪里取、什么不该写"的标准，并且原文是完整读过的。本设计只改**标准送到哪里、送什么**，不加任何审查、核对或模型判定（准则见 SWARMAI.md，2026-11-02 由 XG 定）。

## 1. 问题（读代码确认）

gbrain 建 Brain（2026-11-02）时，三类错误写进了首批知识，P6 全部通过：

| 错误 | 例子 | 标准其实写着 | 写的人当时眼前有没有 |
|---|---|---|---|
| 快照 | "持久化相关文件最常返工"（来自热点列表） | SKILL.md P2b 表 Runtime Traps 行："写热点反映的陷阱，never the list itself" | 没有 |
| 推断 | 把安装验收步骤写成 PRODUCT Success Criteria | SKILL.md P2b："A PRODUCT section holds only what the material states as that thing"；Success Criteria 行"Not a test or review rule" | 没有 |
| 没读全 | TESTING.md、RELEASING.md 等只读了标题 | SKILL.md Step 4："read a tier-2/3 document in full with Read when you write the section it serves" | 没有 |

**根因：标准在，但没送到写的那一刻。**

- 写条目时，agent 照着读的是 `entry-contract.json`（`apply.entry_contract()`，`backend/core/brain_bootstrap/apply.py:279`）。它只有**格式**：字段、类型、各节收哪些类型（`sections.append`，`:308`）、出处写法、长度上限。"这一节从哪里取、什么不写"、正反例，只在 SKILL.md 正文里（`skills/s_project-manager/SKILL.md:330-388`），流程开头读过一遍，写到第 40 条时不在眼前。这正是北极星"有但没送到"那一类。
- CDF 的另一扇门已经有一套成熟的提取标准：`s7_review.py:291-322`（"三点都成立才留""此刻的状态不是知识""看一眼代码就能答的不是教训""版本号、id 是证据不是知识"）。建 Brain 这扇门一句都没有：**同一个脑，两扇门，两套标准**（SOUL P8）。
- Project Agent 草稿：`_default_commands`（`backend/core/project_agent_draft.py:353`）按 `(command, cwd)` 去重（`:369-373`），`npm test` 和 `npm run test` 不算同一条。实测 slugify（2026-11-02，部署的 2.4.1）：草稿里两条都在，Hive 每次检查跑两遍测试。草稿的命令合同里也没有说 validation 该是"每次改动要跑的检查"，于是全量 CI 步骤成了每次改动的检查（gbrain：8 分钟测试 + 90 项 verify，typecheck 单进程 6.3 GB）。

## 2. 准则与不做的事

- **做：** 改提取时读到的东西——标准随合同送达；两扇门一套标准；原文完整读。
- **不做：** 不加逐条审查、逐字引用核对、模型支持判定、覆盖检查，不加新的 P6 行，不提高 tier-1 阅读预算（160K 字符，`docs.py:60`，没有观测到它不够的证据）。`bootstrap-apply` 现有的确定性检查（格式、章节、出处是否存在）保持原样。
- **衡量：** 按结果衡量提取质量，用已有的 New Brain 离线 eval（`scripts/eval_fixture_brains.py`，用例 `OFF_BRAIN_FIRST_ENTRIES_*`），不在流程里加检查。

## 3. 改动

### E1 一套知识标准，两扇门共用

- 新建 `backend/core/knowledge_standard.py`，放**与来源无关**的那几条（从 `s7_review.py:291-322` 原样移出，不改措辞）：
  - 三点都成立才留：别的任务里有用；不在场的能干的人没有它会出错或浪费时间；能指出证据。
  - 写成能独立成立的一条：情境（可识别的触发条件）、判断、理由、证据；没有"上面""这个"。
  - 写的是一直成立的东西：此刻的状态（未推送、待 build、一个会变的数）不是知识；看一眼代码或数据就能回答的（签名、常量值、文件布局）不是教训；commit、run id、版本号、日期是证据，不写进正文。
- `s7_review.py` 改为引用它，S7 专属的句子（"本记录里修好的 bug 是事件"等）留在 S7。S7 提示词的文字不变，只是来源变了（测试：渲染出的提示词逐字不变）。
- `entry_contract()` 输出新字段 `knowledge_standard`，内容就是这些句子。

### E2 逐节提取标准进合同，SKILL.md 只留一句

- SKILL.md P2b 的"Section | Take it from"表（`:367-382`）和正反例（`:384-388`）移进代码常量 `SECTION_GUIDE`，和 `SECTION_TYPES` 放在一起（`backend/core/ddd_skeleton.py:165`），每节两项：`take_from`、`not`（表里已有的"Not …"句子）。
- `entry_contract()` 的每个 section 带上它们；顶层带 `examples`（好、坏各几条，即现在的 Good/Bad）。
- 有 survey 时，合同带 `read_whole`：tier-1 文档的路径（已内嵌的写"已内嵌"，没内嵌的写"写之前完整读"），取自 survey 的 `docs`，不另做判断。
- SKILL.md P2b 那张表换成一句："每一节从哪里取、不写什么，都在合同里，写条目时照它"，删掉重复（P9：同一标准只有一个家）。
- 这样合同就是写条目时唯一要看的东西：格式和标准一起到。

### E3 Project Agent 命令：去掉等价重复，说清 validation 是什么

- `_default_commands` 的去重键改为规范化后的命令：npm / pnpm / yarn 的 `X test`、`X start`、`X stop`、`X restart` 与 `X run <同名>` 视为同一条。`bun test` 不在内（它是 bun 自带的测试运行器，不是 `bun run test`）。同样的规范化用于 `--apply` 时：提案里两个 id 指向等价命令，留第一个，`dropped` 里写明原因。
- `project-agent-draft` 打印的 `contract` 加一句 validation 的标准：validation 是仓库要求贡献者**每次改动前**跑的检查（贡献指南、agent 指南里写的）；只为发版或夜间跑的 CI 步骤（端到端矩阵、打包）不是。这只是提取标准，不加检查。

### E4 按结果衡量：把这次的两类错误放进离线 eval

- 在现有一个 fixture 仓库的 README 里加两处真实会出现的内容：一段"安装后用 X 验证"，一行会变的数（"当前版本 3.4，已有 1,200 个测试"）。答案键 `traps` 加两条：写成 PRODUCT Success Criteria 的安装步骤；写进任何一节的版本号或数量。
- `eval_fixture_brains.py --write` 重新生成用例。eval 按它自己的触发方式跑（部署后、定时、面板），不在编码任务里跑。

## 4. 验证计划（定在这里，做完逐行报告）

| 改动 | 测试（先红后绿） |
|---|---|
| E1 | 合同含 `knowledge_standard`，内容逐字等于 `knowledge_standard.py`；S7 渲染出的提示词与改动前逐字相同 |
| E2 | 合同的每个收类型的 section 都有 `take_from` 和 `not`；有 survey 时 `read_whole` 列出全部 tier-1 文档；SKILL.md 不再含那张表（skill 命令测试仍过） |
| E3 | `npm test` 与 `npm run test` 只留一条；`bun test` 与 `bun run test` 各留各的；`--apply` 提案里的等价重复进 `dropped` |
| E4 | `eval_fixture_brains --check` 无漂移；新 trap 出现在生成的用例里 |

收尾：上述测试 + 被改函数的直接调用方（`test_brain_bootstrap*`、`test_project_agent_draft*`、`test_cdf_s7_*`、`test_eval_bootstrap_entries`），各跑一次。每条修复做 mutation check。不跑全量。

## 5. 顺序与大小

E3（小，独立）→ E1（小，S7 提示词逐字不变是关键）→ E2（中，要重新生成 golden set 和 fixture 合同）→ E4（小）。E1、E2 改的是产品认知的来源，按 R1 直接改即可；E2 动 golden set，和 G7c 一样要在同一个提交里重新生成。
