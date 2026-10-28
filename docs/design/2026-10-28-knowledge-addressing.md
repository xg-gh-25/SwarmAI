---
title: 知识寻址（Knowledge Addressing）—— "什么知识在哪、怎么取、怎么引用" 做成一项标准产品能力
date: 2026-10-28
created: 2026-10-28
updated: 2026-10-28
status: implemented (r5) — XG approved 2026-10-28; implemented 2026-10-29
project: SwarmAI
supersedes-scope-of: docs/design/2026-10-28-ddd-map-and-on-demand-knowledge.md
---

# 知识寻址：一项标准产品能力，所有入口共用

## 0. 主旨

SwarmAI 的知识分在很多库里：DDD 的四份文档和归档、DDD 的 `knowledge/` 语料、Library、pipeline 经验……
今天"去哪找、一条是什么、怎么告诉读者还有更多、取到的怎么引用"由每个入口自己决定：同一个脑，各扇门说法不一，
有的已经指向不存在的路径；Agent 也从没被告诉过怎么自己去取，于是一次性推送的 recall 成了事实上的边界。

知识进入 Agent 有两条路，各管一类知识，缺一不可：

| | 推送（recall） | 主动查（on-demand） |
|---|---|---|
| 谁发起 | OS，确定性，在固定时机 | Agent，在想清楚之后 |
| 擅长 | Agent 不知道要问的：始终有效的约束（Vision、Non-Goals、Success Criteria），以及"这块有东西"的提醒 | 只有方案成形后才知道要什么的：用方案里的词才找得到的那条（§1.4 实测） |
| 现状 | 有，但粒度太粗（聊天只给小节名），推送的条目从没被引用 | 工具名义上有、实际坏（§1.3），没人告诉 Agent，取到的不能引用 |

本设计不新造一层：平台已有布局单一来源（`ddd_paths` / `DDD_CANONICAL_DOCS`）、统一召回（`recall_multi`、
`recall_ddd` 按用途配置）、用量单一入口（`ddd_usage`）、写入去重闸口（`content_signature`）。只补五样，并把各扇门接上：

1. **一种单元身份** `unit_id`（§2.1）；
2. **结构说明**：SWARMAI.md 里改正后的几行，靠测试和布局常量钉死，不新增 prompt 段（§2.2）；
3. **使用点的地图**：只在 pipeline / 专家用的那一刻生成（§2.3）；
4. **一个能用的按需入口** `knowledge map | find | get` 和**一个引用入口**（§2.4、§2.5）；
5. **在决定点触发**主动查（§2.7）。

## 1. 现状（2026-10-28 读代码 + 实测 + 两轮零上下文审查核实）

### 1.1 各扇门

| 门 | 怎么找 | 问题 |
|---|---|---|
| 聊天注入 `session_router._inject_ddd_for_active_project` | `recall_all(ddd)` + 覆盖率过滤 | 只给 `**TECH.md** § Architecture`（约 112K 字符的小节），无路径无行号；用量按条目内容记，但条目内容没有展示（:1862-1896） |
| pipeline `run_knowledge`（knowledge.md / knowledge_design.md / correctness 专家块） | `recall_ddd(purpose)` | 无地图；not-shown / pinned / pointer 行只有文档名，Agent cwd 是源码 worktree，那里没有这些文件；按需取到的不能引用（answers.py:107、hook_bridge.py:260 丢弃） |
| REFLECT `reflect_job` | 每条经验按全项目 `recall_ddd(reflect)` 给判官找候选 | 没问题，保持 |
| 培育写入 `ddd_cultivation` | 全文档 `content_signature` 精确去重 | 没问题，保持（43K 重复的根治闸口） |
| 衰减用量 `ddd_usage.record_ddd_hit` | 归一化文本前 120 字作 anchor | 只有聊天一扇门喂（:1896）；pipeline、专家用过的条目照样衰减 |
| Brain Hub（`routers/ddd_brain.py` :1760 搜索、:1388 提案） | `recall_all` | 预览截到 400 字，没有去处看全文 |
| `repo_guidance._ddd_block` | 4 份文档路径 | 让读 2MB；排在 lessons 后被挤掉，`seen["@ddd"]` 先记后判断、不重试（10 个带 lessons 的会话里 7 个没有它） |
| `archive_browse.search_archive`（eval.py:705） | 自己的检索 | 单元和身份与 recall 不一致 |
| `jobs/handlers/ddd_weekly_report.py:39` | 自带一份 `ddd_path` 回退实现 | 第二份布局实现 |
| 技能散文 | 手写路径 | `s_qa` 读 `Projects/<project>/TECH.md`，对已迁移到编号布局的 DDD 失效；`s_internal-crux-cr` 写死具体项目名（P7） |
| native `s_expert-review`、Swarm Out 任务包 | — | 不带任何 DDD 知识 |
| 非 owner 渠道 | `prompt_builder._collect_shareable_ddd_paths`（`shareable` + 文档白名单 + 软链接守卫） | 规则正确，新入口复用它 |
| 可分发包 | Kiro 已出 `knowledgeBase`（ddd_packager:1448）；Claude Code 读不到 cwd 外（AIM 标准 §5.5） | 本设计不动 |
| 自身描述 | SWARMAI.md § My Workspace 只有三行，没有编号布局、文档职责和取用方式；6 个 DDD 的 AGENTS.md 仍写 7-21 前的 `gates/`、`skills/` | 布局知识被复制成散文，没有任何测试管散文 |

### 1.2 单元身份：至少 6 种，最常用的不稳定

pipeline 的 `item_id` 对单元原文取哈希，而 `split_blocks` 把条目下的注释一并算进单元（ddd_entry_lifecycle.py:771）。
SwarmAI 的 IMPROVEMENT.md 1712 条条目里 1481 条的单元文本含 `<!-- … -->` 注释——衰减每改写一次 `ref:` / `last:`，
这些条目的 id 就变，没人编辑也对不上。其他身份：用量 anchor、小节名、`content_signature`、`(title, section)`、REFLECT 的 `doc#section: head[:120]`。

### 1.3 按需入口是坏的

`py -m scripts.context_recall_cli` 把 cwd 放到 `sys.path` 最前（desktop_main.py:70、:77）：别的仓库里 ImportError，
SwarmAI worktree 里加载 run 正在改的代码；`--domains ddd` 返回小节名和分数外加一个最佳条目，没有行号和身份。

### 1.4 行为实测

| 观察 | 结果 |
|---|---|
| 有 knowledge.md 的 4 个 run（驱动 + 10 个专家）打开 DDD 文件 | 0 次；一个驱动只 `cut -c1-400` 读了 knowledge.md |
| 落地的 2 个 run 的 `knowledge_refs` | 都为空：推送的条目从没被引用 |
| 近 14 天 288 个会话，聊天 `[DDD:X]` 小节指针注入后 15 个工具调用内读 DDD | 12 次注入里 5 次——指针会引发去读，但指向的是 100K 级的整节 |
| 同一 `recall_ddd(design)`，用请求原话查 | 7 条，**没有**决定本设计的"活/冷二分"总纲（TECH.md:119） |
| 用设计里才出现的词再查 | 命中该总纲 |
| 写设计时我自己（规则在 prompt、路径已知） | 没有去查，被问"对你有没有用"才查 |

结论：推送是**提醒**，不是**答案**；能改变方案的知识要在方案成形后用方案的词去查；瓶颈在"决定点会不会去查"，不在"知不知道地址"。

### 1.5 和 08-14 活/冷总纲的关系

总纲（TECH.md「活/冷二分」+ `Knowledge/Designs/2026-08-14-unified-retrieval-no-index.md`）禁止 in-prompt index：从内容派生、
要和正文同步维护、随内容线性增长、拿来做关键词匹配面的东西。本设计**不往 system prompt 加任何生成段、任何清单**：
结构说明是对 SWARMAI.md 现有散文的改正（总量不增），不含项目、文件、条目、数字；地图只出现在 run 的产物和 handout 里。

## 2. 设计

### 2.1 单元身份 `unit_id(text)`（放在单元切分的家 `ddd_entry_lifecycle`）

- `unit_id = sha256(归一化文本)[:8]`。归一化：去掉 `<!-- … -->` 注释（复用 `ddd_usage._META_LINE_RE` 的口径，扩到 maturity 注释）
  和行尾日期戳（`_TRAILING_STAMP_RE`）；条目再按 `content_signature` 的规则去格式差异。
- **不改任何 DDD 文件**、不回填。衰减改写元数据不改变身份；同一条经验换写法身份相同，和写入去重闸口对"同一条"的定义一致。
- `run_knowledge.item_id` 改调它。旧 run 只对自己记录过的集合校验，不受影响。
- 不选"生产者把 id 写进元数据注释"：`_META_RE` 锚定、定序（:324-331），`to_comment()` 每次重建注释会丢新字段；大部分条目没有元数据注释，
  自动给散文或无标题条目加注释被禁止（:233-241）；写入方 8 处以上，移动 = 复制后退役还会重发 id。

### 2.2 结构说明：改正 SWARMAI.md § My Workspace，用测试钉住

SWARMAI.md 由产品源码（`backend/context/SWARMAI.md`）提供、每次启动覆盖，所以只能写对每个用户都成立的结构。
用下面这段**替换**现有的 § My Workspace，并删掉 FAQ 里 "What projects do I have?" 那一条（"There is no injected index" 仍然成立，并入下文）：

```markdown
## My Workspace
- **SwarmWS** (`~/.swarm-ai/SwarmWS/`) — my working directory, my filesystem body.
- **Projects/<name>/** — one DDD per project, every one with the same layout:
  `AGENTS.md` (identity) · `2-understanding/PRODUCT.md` (intent, users, non-goals) ·
  `TECH.md` (architecture, conventions, runtime traps) · `IMPROVEMENT.md` (what worked,
  what failed, pitfalls) · `PROJECT.md` (current focus, decisions, open items) · their
  `*-archive.md` (decayed, reference only) · `2-understanding/knowledge/` (deep reference) ·
  `3-gates/` · `4-capabilities/` · `bindings.yaml` (bound repos). A doc may be absent.
- **Knowledge/** — cross-project: `Library/` (reference; `pipeline-lessons/<repo>.md`),
  `Designs/`, `Reports/`, `Notes/`, `Learned/`, `Meetings/`, `Handoffs/`, `DailyActivity/`
  (raw session logs), `Pollinate/` (media output), `JobResults/` (background jobs),
  `Signals/` (fetched, untrusted), `Archives/` (retired, not recalled).
- **Finding things:** what I am given (recall, `[DDD:<project>]`, a run's knowledge files) is a
  starting set, not everything. When a decision depends on a project's domain, search with
  the words of the solution, not only the request:
  `"$SWARM_BACKEND_BIN" knowledge find --project <name> --query "<words>"` (whole entries
  with `path:line` and an id; `map` lists what a DDD holds; `get <id>|<path>:<line>` reads
  one). Never read TECH / IMPROVEMENT whole. There is no injected project list: the active
  project's DDD comes with `[DDD:<project>]`, others via `knowledge map`.
```

- **一致性测试**（新增，管散文——现有单源测试只管代码路径拼接）：从 `ddd_paths.UNDERSTANDING_DIR` / `GATES_DIR` / `CAPABILITIES_DIR`、
  `DDD_CANONICAL_DOCS`、`swarm_workspace_manager.KNOWLEDGE_SUBDIRS` 取常量，断言这段文字恰好列出这些常量里的每个目录名和文档名（不多不少），
  且不出现旧布局写法（`Projects/<…>/TECH.md` 这类根下文档、`gates/`、`skills/`）。布局一变而文字没改，测试红。
- 同一检查用于技能正文（`scripts/lint_skills.py` 新规则）：禁止根下旧布局 `Projects/<X>/<DOC>.md` 和具体项目名；允许 `2-understanding/` 新布局（s_persist 等写 DDD 的技能需要）。
- **增删 DDD 不会让它过期**：文字里没有项目名、数量、大小；实例信息只在当场读磁盘的地方（每回合 DDD 块、`knowledge map`）。
- `SELF.md` § Recall 的按需用法改指 `knowledge` 命令（MEMORY / EVOLUTION 仍是 `context_recall_cli`）。`KNOWLEDGE.md` 不动（用户自己的注释）。

### 2.3 使用点的地图 `core/addressing.py: ddd_map(project, size)`

只读 `ddd_paths` 常量和磁盘（不读 AGENTS.md 文字），旧布局经 `ddd_path` 回退同样能出：

- `full`（pipeline knowledge 文件、correctness 专家）：每份文档和归档的绝对路径、大小、`##` 行号；超过 20K 的 `##` 列 `###`；
  `knowledge/` 文件清单；`assets/<kind>/` 下的治理资产（含 `data-contract/`）；gates / capabilities 名；`bindings.yaml`、`REFRESHER.md` 是否存在；整行取舍，预算 8000 字符，放不下的合成一行 grep 提示。
- `brief`（其他专家、native expert-review、Swarm Out、repo_guidance）：根 + 每份文档路径和大小 + 取用命令，约 600 字符。
- 安全：只列根内、`resolve()` 后仍在根内的 `.md`；跳过 `.artifacts/`、锁文件、隐藏文件、数据库；内容放 `<recalled>` 围栏；不抛异常
  （无 DDD / 缺文档 / 非 UTF-8 只少那一部分并说明，接 run-knowledge H5 矩阵）。
- 地图的价值受 DDD 自身组织限制（IMPROVEMENT 只有 340K / 237K / 270K 三个大节）：它回答"有什么、在哪"，条目级的事交给 `find`。

### 2.4 按需入口 `"$SWARM_BACKEND_BIN" knowledge map | find | get`

- 顶层子命令，和 `pipeline-v1` 一样在 `desktop_main.main` 分派，**不走 `py -m`**，不受 cwd 影响、不加载 cwd 里的代码。
- `map --project P [--brief] | --projects`；`find --project P --query "<词>" [--doc TECH.md] [--in ddd|library]`；`get <unit_id>|<path>:<line>`：一条全文 + 身份。
- **`find --in ddd` 的范围 = 和引用校验同一个单元表**（`ddd_units(project)`）：四份文档 + 各自 `*-archive.md` + `2-understanding/knowledge/` +
  治理资产的 `data-contract/`（今天 `_recall_ddd` 已经召回它，recall_multi.py:1284）。只包 `recall_ddd` 不够：它只加载四份文档
  （`_load_ddd_docs_text`），查不到归档和语料，和"引用含归档"自相矛盾。排序复用 `recall_ddd` 的按文档 BM25 和整条取舍；
  `ddd_units` 在切分时记下每个单元的起始行号（`recall_ddd` 的命中今天不带行号，`_parse_docs_sections` 只返回文本）。
  返回**整条**单元 + `绝对路径:行号` + `unit_id`，放不下的用 `not_shown_note`。`--in library` 包 `recall_all(library)`，同样整条 + 路径 + 身份。
- 只读，只到 DDD 根和 Library；MEMORY / EVOLUTION 仍走带隐私门的 `context_recall_cli`。
- 会话类型：必填 `--session-type`、默认拒绝；渠道类型只放行 shareable 项目。run 1 第一步先核实非 owner 渠道会话是否有 Bash、
  daemon 是否给子进程设了可信的会话类型环境变量；有就用它，不靠 Agent 自报。

### 2.5 引用入口

- 已知身份集合 = 本 run 召回过的 ∪ 项目 DDD（含 `*-archive.md`）里现存单元的 `unit_id`（项目取自 contract）。
- REQUIREMENTS / DESIGN_SPEC 的 `knowledge_refs`：未知仍拒绝，提示用 `knowledge get` 重新取。专家 finding 的 `knowledge_ref`：`answers.py` 形状不变，
  `hook_bridge._occ` 的 `carried` 换成已知集合，未知仍丢弃、finding 照样算。
- R27 全部读者：`schemas` 文案、`_check_knowledge_refs`、engine.py:2807 计数（拆"推送 / 主动查"）、`audit.py`、`events.py:137`、
  `stages/evaluate.md`、`test_pipeline_v1_expert_knowledge.py:95-112`。REPORT 只出计数。
- 被引用的单元调 `ddd_usage.record_ddd_hit`（anchor 仍由 `entry_anchor_text` 算，`.ddd-usage.json` 不迁移）；聊天门只在内容真的展示时才记用量。

### 2.6 推送保留，粒度改对

- **pipeline recall 保留**。固定带上的 Vision、Success Criteria、Target Users、Non-Goals 是推送最有价值的部分（和查询词无关，Agent 自己想不到要查），不动；
  按查询排序的部分保持现有配额。knowledge 文件 = 起步包 + `map(full)`，not-shown / pinned / pointer 行改为绝对路径 + 行号。
- **聊天 recall 保留，改为条目级**：给命中条目的**标题行 + `绝对路径:行号`**，不再给小节名；无条目命中时只给一行项目指针（含 DDD 根绝对路径）。
  这是聊天里唯一的提醒（12 次里 5 次被跟进），改的是它指向的精度。

### 2.7 在决定点触发

- pipeline：DESIGN_SPEC（full 和 lite 共用的字段集 `common`，schemas.py:493；THINK 没有自己的产物，它的结论进 DESIGN_SPEC）加可选字段
  `knowledge_checked: [{query, result_ids}]`；`direct` 没有 PLAN，不适用；lite REQUIREMENTS 不受影响——定方案前用方案里的词跑 2–3 次 `knowledge find`，
  记下查了什么、用了哪些；没查到写 `[]`。stage 文档在 "Read knowledge_design.md first" 后加一句做法。**不设 gate**（P5）；REPORT 和 §5 只统计是否为空。
- 聊天和 native 工作：不加机制，靠 §2.2 那段的 "search with the words of the solution"。

### 2.8 通用 Knowledge

- 结构由 §2.2 那段覆盖（子目录名来自 `KNOWLEDGE_SUBDIRS`，测试钉住）。
- 取用：`knowledge find --in library`（包现有 FTS5 library 域，已覆盖 `Knowledge/`），同样返回整条 + 路径 + 身份。
- 不出地图：约 1100 个文件、150MB、每天新增，没有固定结构，清单正是总纲禁止的东西。

### 2.9 构建时机、实效性、延迟（2026-10-28 实测，SwarmAI）

| 部分 | 何时构建 | 怎么保证不过期 | 延迟 |
|---|---|---|---|
| 结构说明 | 随产品发布（SWARMAI.md 源码） | 一致性测试钉在布局常量上；不含实例信息 | 0（静态文字，不破 prompt 缓存） |
| `map` | 使用点：run create、EVALUATE 完成、发 handout | 当场读磁盘；行号注明"本 run 开始时，对不上就 grep 标题"；`get <unit_id>` 按身份找当前位置 | 50–80ms（含 Python 启动） |
| `find` / `get` | Agent 每次调用 | 每次现读 | `recall_ddd` 37–110ms，`recall_all(ddd)` 73–205ms |
| `unit_id` | 现算 | 不受元数据改写影响 | 与切分同时 |

## 3. 各扇门接法

| 门 | 改动 |
|---|---|
| 聊天 | §2.6 条目级；用量只记展示过的 |
| pipeline | §2.6 起步包 + `map(full)` + 绝对路径；§2.5 引用；§2.7 `knowledge_checked`；stage 文档改写"读 knowledge 文件"那句 |
| 专家 | correctness：起步包 + `map(full)`；其他专家：`map(brief)`，handout 写明"只在 finding 需要时查"（按需读花专家调用预算） |
| native `s_expert-review` | 专家提示带 `map(brief)` |
| Swarm Out | 任务包带被派往项目的 `map(brief)`（Hive 上有该 DDD 时） |
| `repo_guidance` | 一行"绑定到项目 P，DDD 根 <绝对路径>"，放在 lessons 之前，放进去才记 `seen` |
| Brain Hub | 预览保留，旁边给 `path:line`，点开看全文 |
| `ddd_weekly_report` | 删自带的 `ddd_path` 回退，改用 `ddd_paths` |
| 技能 | `s_qa` 改用 `knowledge find --doc TECH.md --query 'Dev Commands'`；`s_internal-crux-cr` 去掉项目名；`lint_skills.py` 新规则 |
| REFLECT / 培育 / 分发包 | 不动（REFLECT 每条经验已按全项目召回候选，主动查到的条目同样在候选范围内） |
| `archive_browse.search_archive`、eval 的 DDD 扫描（eval.py:516）、`ddd_refresh` 的陈旧检查 | 不动：读者是人看的界面和健康检查，不是把知识交给 Agent 的门 |
| 本机 TOOLS.md § On-Demand Recall | 不是产品文件：run 3 里把 DDD / Library 的按需用法改指 `knowledge` 命令 |
| 本机 6 份过期 AGENTS.md | 不是产品代码：备份后只替换三类旧写法，diff 核对（地图不读 AGENTS.md，所以不建产品迁移） |

## 4. 不做的

- 不往 system prompt 加生成段或任何清单（项目、文件、条目），不加回 PROJECTS.md。
- 不新建知识库登记表，不包一层新 API 替代 `recall_multi`。
- 不改 DDD 文件形状、不回填 id。
- 不动写入去重和 REFLECT 候选。
- 分发包不加离线检索（Kiro 已有 `knowledgeBase`；Claude Code 读不到 cwd 外的安装目录，绝对路径还会触发打包的宿主路径检查）。
- 不加"必须先查"的 gate；不加向量检索。

## 5. 怎么量、怎么判

transcript 统计脚本收进 `backend/scripts/knowledge_usage_report.py`，按 run / 会话统计：`knowledge` 调用次数、对 DDD 路径的 Read/Grep 次数、
`knowledge_checked` 是否为空、引用里推送 id 和主动查 id 各多少、聊天指针被跟进的比例。

| 指标 | 基线（今天） | run 1+2 上线两周后看 |
|---|---|---|
| pipeline run 主动查（`knowledge` 调用或 DDD 读取）> 0 的比例 | 0/4 | 仍为 0 → 先改 stage 文档和 §2.2 的写法，不往下建 |
| 引用：推送 id / 主动查 id | 0 / 不可能 | 推送始终 0、主动查 > 0 → 削减推送的排序部分（固定小节保留） |
| 聊天指针被跟进率 | 5/12 | 条目级后不升反降 → 回看指针写法 |
| 被引用条目进入归档候选 | 会（只有聊天喂用量） | 0（回归测试） |

## 6. 实施与验证计划

### 6.1 三个 run（每个独立可验证，建议走 pipeline：跨 prompt 源、run_protocol、recall、CLI 分派）

| run | 内容 |
|---|---|
| 1 能力本体 | `unit_id`；`core/addressing.py: ddd_map`；`knowledge` 子命令；SWARMAI.md / SELF.md 改正 + 一致性测试 |
| 2 接门 | pipeline（knowledge 文件、绝对路径、引用、`knowledge_checked`、stage 文档）、专家、native expert-review、Swarm Out、repo_guidance、聊天条目级、Brain Hub 链接、用量 |
| 3 清理 | `ddd_weekly_report` 去重复实现；s_qa、crux-cr；`lint_skills.py` 规则；`knowledge_usage_report.py`；本机 6 份 AGENTS.md（非产品代码） |

run 2 依赖 run 1 的 `unit_id`、`ddd_map`、`knowledge`；run 3 的 linter 规则依赖 run 1 的一致性检查函数。

### 6.2 谁读这次写下的东西（读者先于写者设计）

| 写下的 | 读者 | 在哪个 run 一起改 |
|---|---|---|
| `unit_id`（新算法的 id） | `_check_knowledge_refs`、`hook_bridge._occ`、engine 计数、`audit.py`、`events.py`、REFLECT 的 contract 记录、聊天用量 | 1（函数）+ 2（全部读者） |
| `knowledge find/get` 的输出格式 | 主 Agent、专家子 agent、stage 文档、SWARMAI.md 那段 | 1 |
| `knowledge_checked` 字段 | `schemas`、REPORT、`knowledge_usage_report.py` | 2 + 3 |
| `map` 文本 | 主 Agent、专家 | 1（生成）+ 2（放进各处） |

### 6.3 最终测试集（每条先在 main 上红，做变异检查）

1. 衰减改写元数据后 `unit_id` 不变；同一经验两种写法 `unit_id` 相同；`recall_ddd` 起步包和 `knowledge find` 对同一单元给出同一 id。
2. SWARMAI.md 那段与布局常量一致；改一个常量测试红；文字里出现旧布局写法测试红。
3. `lint_skills.py` 在当前 s_qa 上红、改后绿；不误报 s_persist 的 `2-understanding/` 写法。
4. `map`：只列根内 `.md`；软链接指向根外不列；`##` 行号指向标题行；超 20K 列 `###`；超预算整行取舍；旧布局能出；无 DDD / 缺文档 / 非 UTF-8 不抛异常。
5. 从别的仓库 cwd 调 `knowledge` 能用，且不加载该 cwd 里的同名模块。
6. 引用：主动查到的 `unit_id` 被 REQUIREMENTS 接受、被专家 finding 保留；编造的 id 被拒（文档）/被丢（finding）；run 中途被归档的条目 id 仍认得；计数拆两类。
7. 被引用的条目进 `record_ddd_hit`；聊天不为未展示的内容记用量。
8. pipeline knowledge 文件：有 DDD 时含 `map(full)`、无 DDD 时不含；not-shown / pinned / pointer 行是存在的绝对路径。
9. repo_guidance：lessons 吃满预算时 DDD 那一行仍送达；放不下时不记 `seen`、下次再试。
10. 聊天：命中给条目标题 + `绝对路径:行号`；无条目命中时一行项目指针。
11. 非 owner 渠道：`knowledge` 只放行 shareable 项目。
12. `knowledge find --in ddd` 能命中只在 `*-archive.md`、`2-understanding/knowledge/`、`data-contract/` 里的条目；返回的行号指向该单元第一行。
13. DESIGN_SPEC full 和 lite 都接受 `knowledge_checked`、形状错误报 DS-5；REPORT 只出"是否为空"的计数。

现有测试一并跑：`run_knowledge`、`recall_multi`（recall_ddd）、engine / hook_bridge / answers 的 knowledge_ref、`repo_guidance`、
`session_router` DDD 注入、`prompt_builder` 基段、两个单源测试、run-knowledge H5 矩阵。

### 6.4 审查

- run 1：correctness + security（路径边界、CLI 的会话类型和 shareable、prompt 源文字）+ api-contract（`unit_id`、CLI 输出格式）。
- run 2：correctness + api-contract（引用契约的全部读者）+ state-machine（repo_guidance 的 `seen` 逻辑）。
- run 3：correctness。

### 6.5 端到端

run 1 部署后：新开一个聊天会话，确认 SWARMAI.md 那段在 prompt 里，从别的仓库目录调一次 `knowledge find`。
run 2 部署后：跑一个真实 trivial run 和一个 full run，确认 knowledge 文件有地图和绝对路径、`knowledge_checked` 被填写，
用 `knowledge_usage_report.py` 出第一份数，和 §5 基线对照。

## 7. 本次讨论中提出的已知问题，逐条去向

| 问题（出处） | 去向 |
|---|---|
| knowledge.md 头部没有 DDD 路径 | §2.3 `map(full)` |
| not-shown / pinned / pointer 行只有文档名 | §2.6 绝对路径 + 行号 |
| 主动查到的不能引用（answers.py:107、hook_bridge.py:260、engine.py:2807） | §2.5 |
| `knowledge_refs` 的全部读者（R27） | §2.5 列全 + 测试 6 |
| `py -m` 受 cwd 影响、`--domains ddd` 没有行号和身份 | §2.4 顶层子命令 + 测试 5 |
| repo_guidance 指针被 lessons 挤掉、`seen` 先记 | §3 + 测试 9 |
| 聊天只给 100K 级小节名、为未展示内容记用量 | §2.6 条目级 + 测试 7、10 |
| 只有聊天喂衰减用量 | §2.5 被引用即记 + 测试 7 |
| `item_id` 含元数据注释、衰减一改写就变 | §2.1 `unit_id` + 测试 1 |
| 生产者赋 id 会被 `_META_RE` 丢、覆盖不全 | §2.1 不采用 |
| REFLECT / 培育去重不能换成 BM25 | §3 不动 |
| 行号在 run 中途漂移、条目被归档 | §2.9 + `get <unit_id>`；已知集合含归档 + 测试 6 |
| 专家按需读花调用预算 | §3 handout 写明"只在 finding 需要时查" |
| 非 owner 渠道 / 群聊的隐私 | §2.4 只放行 shareable + 测试 11；prompt 里无任何项目信息 |
| 活/冷总纲（零 in-prompt index） | §1.5、§4：不加生成段和清单 |
| SWARMAI.md 增删 DDD 会不会过期 | §2.2 只写结构 + 一致性测试（测试 2） |
| `s_qa` 旧路径、`s_internal-crux-cr` 项目名 | §3 + linter 规则（测试 3） |
| `ddd_weekly_report` 第二份布局实现 | §3 run 3 |
| Brain Hub 预览截 400 字 | §3 给 `path:line` 链接 |
| 6 个 DDD 的 AGENTS.md 过期 | §3 run 3（本机，备份后改） |
| 推送从未被引用 / 主动查是否真的发生 | §5 指标和判定 |
| 指针以前没送达过，"告诉了也不读"未被检验 | §5 先量再往下建 |
| 分发包离线检索与 AIM §5.5 冲突 | §4 不做 |
| r4 核对时新发现：`find` 只包 `recall_ddd` 查不到归档、语料、data-contract | §2.4 改为同一单元表 |
| r4 核对时新发现：`recall_ddd` 命中不带行号 | §2.4 `ddd_units` 记行号 |
| r4 核对时新发现：`knowledge_checked` 放在哪个产物、哪些 profile 有 | §2.7 DESIGN_SPEC 的 `common`，direct 不适用 |
| r4 核对时新发现：地图漏了治理资产、`REFRESHER.md`、`bindings.yaml` | §2.3 |
| BM25 归一化丢掉最弱匹配（10-27 记录） | 已在 `_normalize_bm25_scores` 修复（memory_index.py:472），无需再做 |
| 本对话之外的 P0（streaming 卡顿、Interrupted） | 与本设计无关，不在范围 |

## 8. 实施记录（2026-10-29，native，goal-driven）

**落地内容**（session worktree，按 run 1 → 3 分提交）：`memory_index.section_spans` + `parse_memory_sections` 同名合并；
`ddd_entry_lifecycle.unit_id` + `split_blocks_at`；`ddd_paths.data_contract_dir` / `stale_layout_refs`；`core/addressing.py`；
`core/knowledge_cli.py` + `desktop_main` 的 `knowledge` 子命令；SWARMAI.md § My Workspace / SELF.md + 一致性测试；pipeline
knowledge 文件地图与绝对路径、引用扩展、`knowledge_checked`、专家 handout 地图与 `knowledge_project`、引用回馈衰减、REPORT 计数；
聊天条目级指针；repo_guidance 一行 DDD + 只标记已展示；native expert-review 带精简地图；技能文字、linter 两条规则、
`scripts/knowledge_usage_report.py`。

**实施中改变的决定**（都经专家审查后定，对源码核实）：
- **受限会话**：原设计"非 owner 渠道只放行 shareable 项目"被 security 审查推翻——在 shell 命令上用正则做 allowlist 守不住
  （引号、变量、argparse 缩写），且 shareable 项目会多暴露归档和语料（共享通道只给 4 份文档）。改为 `knowledge` 在非 owner
  渠道一律不可用：CLI 见 daemon 设的 `SWARM_KNOWLEDGE_RESTRICTED` 即拒绝，Bash handler 去掉引号后见到就拒绝。两者是可用性防线，
  不是保密边界——**已有问题（不在本次范围）**：受限会话的 Bash 路径检查不看相对路径、也不看引号内路径（`py -c "open('/x')"`）。
- **代码块（fence）**：第一轮为 0 例的不一致加了 fence 感知切分，第二轮发现它在真实 DDD 上藏掉整节（条目写入时被截断，fence 常不闭合）。
  按"第二轮仍有 HIGH 就问该不该存在"删掉，回到原来不认 fence 的切分；与 main 对比真实 DDD 无任何小节或条目丢失。
  **已知小缺口**：闭合 fence 内的 `- ` 行，recall 的条目腿视为条目、单元表视为块的一部分。
- **同名小节**：`parse_memory_sections` 原来会覆盖同名（去后缀后）小节——SwarmAI IMPROVEMENT 的 "What Worked"（38%）对所有 recall 不可见。
  修后 "What Worked" 9 → 608 条可召回。
- **Swarm Out 不改**：任务派到的 Hive 有自己的 SwarmWS 与 DDD，本机地图路径在那里是错的；Hive 自己的 SWARMAI.md 与 `knowledge` 命令覆盖。
- **Brain Hub 不改**：搜索结果本来就能点开整份文件，400 字只是列表预览。
- **`s_internal-crux-cr` 不改**：它是本机工作区安装的技能，不随产品发布。
- **单元行号**：`_locator` 按单元前几行序列在其所属小节内匹配；SwarmAI 4133 个文档单元中剩 6 个同节完全重复的单元可能指向另一份相同内容。

**审查**：run 1 — correctness / security / api-contract 两轮；run 2 — correctness / api-contract / state-machine 两轮；run 3 — correctness 一轮。
每个修复都做了变异检查（撤掉修复，测试变红）。

**上线前基线**（`knowledge_usage_report.py`，近 14 天，SwarmWS 会话）：78 个会话读过 DDD 文件（539 次），`[DDD:]` 提示 25 次、
14 次之后读了 DDD；pipeline run 7 个、引用知识 0、按需检索 0。§5 的判定以此为基线。

**已有问题，本次未处理**：`test_repo_guidance_lessons.py::test_too_many_lessons_are_cut_newest_first_with_the_path` 与
`test_pipeline_v1_review_tree.py`（引用一个不存在的模块）在 main 上同样失败；`test_ddd_absent_or_broken.py` 整体超过 110 秒（分段全绿）。

## 修订

- r1（2026-10-28）：初稿：库登记表 + 单元模型 + 四操作 + 五种传输 + 6 个 run。
- r2（2026-10-28）：零上下文审查（逐条对源码核实）：生产者赋 id 不可行 → 归一化内容哈希；REFLECT / 培育去重不动；删库登记表、HTTP 层、离线包；
  隐私复用 `shareable`；linter 只禁旧根布局和项目名；补漏掉的门；计数修正。
- r3（2026-10-28）：现场按需查到 08-14 活/冷总纲 → 删 prompt 里的项目表；补触发、构建时机/实效/延迟、通用 Knowledge；聊天条目级。
- r4（2026-10-28，final）：XG 确认。实测推送 vs 主动查（推送从未被引用、聊天指针 5/12 被跟进、决定性条目只有主动查找到）→ 两者都保留、分工写清；
  不新增 system prompt 生成段，改为改正 SWARMAI.md § My Workspace 并用测试钉在布局常量上（增删 DDD 不影响）；补验证计划（读者、最终测试集、审查、端到端、判定标准）。
  定稿后逐条核对已知问题（§7），补 4 处遗漏：`find` 的范围改为与引用同一单元表（含归档、语料、data-contract）、单元带行号、
  `knowledge_checked` 落在 DESIGN_SPEC、地图补治理资产。
- r5（2026-10-29）：实施完成，记录见 §8。
