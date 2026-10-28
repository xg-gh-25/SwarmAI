---
title: 标准寻址 —— 让 SwarmAI 知道"什么信息在哪、怎么取"，recall 只是起步包
date: 2026-10-28
created: 2026-10-28
updated: 2026-10-28
status: proposed (r2)
project: SwarmAI
extends: docs/design/2026-10-26-run-knowledge.md
---

# 标准寻址：让 SwarmAI 知道"什么信息在哪、怎么取"

## 0. 主旨

今天 SwarmAI 拿知识只有一条路：**被动接收**一份有界的 recall。recall 的查询词来自请求、配额按文档切、
DDD 有 2MB，所以它注定不完整；而系统从没告诉过 Agent "全集在哪、长什么样、怎么去取"，于是 recall
成了事实上的边界（§1.2：0/4 个 run、0/10 个专家打开过 DDD 文件）。

本设计引入**标准寻址**：像操作系统的地址空间一样，把"什么信息在哪、用什么方式取、取到的怎么引用"
抽象成一套确定性的、从代码和磁盘生成的描述，按**稳定性**分三层，分别放在该放的地方：

| 层 | 内容 | 稳定性 | 放哪 | 生成自 |
|---|---|---|---|---|
| **L1 寻址模式**（schema） | SwarmWS 布局、DDD 六段标准结构、"什么问题去哪个库"路由表、取用方式、引用格式 | 只随产品版本变 | system prompt 可缓存基段 | 代码常量（`ddd_paths`、`DDD_CANONICAL_DOCS`、`recall_multi.DOMAINS`、Knowledge 目录集） |
| **L2 寻址实例**（instance） | 这台机器上有哪些项目、各自 DDD 根、是否有 DDD、绑定了哪些仓库 | 建/删项目时变 | system prompt 可缓存基段 | `Projects/*/` + `bindings.yaml` |
| **L3 坐标**（coordinates） | 每份文档的大小、`##`/`###` 行号、knowledge/ 文件清单 | 每次写入都变 | **只在使用点生成**：run 的 knowledge 文件、专家 handout、按需命令 | 当场读磁盘 |

**分层原则：** 进 system prompt 的只有 L1+L2——稳定、不含数字快照（R30#4：大小和行号会漂移成谎言，
还会让 prompt 缓存每次失效）；会漂移的 L3 只在用的那一刻生成。三层由**同一个生成器**产出，
每扇门按读者取用，这样各入口对"脑子长什么样"的说法不会再分叉（P8）。

recall 不删，降级为**起步包**：OS 确定性地先给最相关的几条；寻址让 Agent 知道起步包之外还有什么、
怎么取；取到的东西能和起步包一样被引用、被审计、被 REFLECT 和衰减看到。

## 1. 现状（2026-10-28 读代码 + 实测 + 一轮零上下文审查）

**1.1 recall 的触达比例。** SwarmAI 四份文档 2.0MB（TECH 784K / IMPROVEMENT 946K / PRODUCT 107K /
PROJECT 134K），另有两份 archive 184K、`2-understanding/knowledge/` 若干设计稿。每阶段上限
（`recall_multi.DDD_PURPOSES`，:968）：design ≈7.5K、review ≈6K、requirement ≈7K 加最多 3 个不计入预算的
pinned 小节（`PINNED_MAX` 1500，:1003），≈11.5K。都不到 1%。

**1.2 Agent 是否自己去读。** 有 `knowledge.md` 的 4 个 run（SwarmAI，2 个落地、2 个 EVALUATE 放弃）的驱动
transcript + 10 个专家子 agent transcript：对任何 DDD 路径的 Read / Grep / Bash = **0**，按需 recall CLI = 0。
一个驱动只 `cut -c1-400` 读了 knowledge.md 前 400 字符。样本小，但方向明确。

**1.3 已有的"告诉它"基本没送达。** `repo_guidance` 的 `[Project DDD]` 指针排在 AGENTS.md / CLAUDE.md /
lessons 之后（repo_guidance.py:367-372 extras 在文件之后），lessons 块几乎吃满剩余预算（`room = budget-…-120`），
而 `seen["@ddd"]` 在判断放不放得下**之前**就记上了（:419-423），放不下也不会重试。实测 10 个带 lessons 块的
transcript 里 7 个没有 `[Project DDD]`。所以"告诉了路径它仍不读"这件事**其实没被检验过**。

**1.4 寻址知识散落在四处，没有一处是生成的：**

| 位置 | 说了什么 | 问题 |
|---|---|---|
| `SWARMAI.md` § My Workspace / FAQ | SwarmWS、Projects/、Knowledge/ 各一行；"What projects do I have? Glob…There is no injected index." | 手写散文；不含 DDD 六段结构、路由表、取用方式 |
| `SELF.md` § Recall | `context_recall_cli.py` 的用法 | 从别的 cwd 跑会失败（见 1.6） |
| `TOOLS.md`（本机） | 详尽的 recall 用法和域表 | instance 文件，别的用户没有 |
| `KNOWLEDGE.md` | 用户自己维护的 Knowledge/ 索引 | 种子是空模板；本机那份是我手写的 |

另：2026-08-14 删了 `PROJECTS.md`，理由是"Projects/ 可以通过 recall + Glob 发现"（context_directory_loader.py:390）。
1.2 的实测推翻的正是这个前提——**能被发现 ≠ 会被发现**。但 PROJECTS.md 是**内容索引**（项目摘要，会过期），
本设计放进 prompt 的是**结构和地址**（不含内容、从磁盘生成），两者不是一回事，不是把删掉的东西加回来。

**1.5 各入口说法不一（P8）。** pipeline `knowledge.md` 头部只写 `project X (has a DDD)`，not-shown 行是
`IMPROVEMENT.md#…`（文档名，Agent cwd 是源码 worktree，那里没有这个文件；pinned/pointer 行同样，:1110、:1196）；
correctness 专家同上，其他专家没有；`repo_guidance` 让读完 2MB；聊天 `[DDD:X]` 只给 `**TECH.md** § Architecture`
（那个小节 109K）。

**1.6 按需命令在别的 cwd 下不能用。** `run_py` 把 cwd 插到 `sys.path` 最前（desktop_main.py:70、:77）：在有
`scripts/__init__.py` 的仓库里 `py -m scripts.context_recall_cli` 报 ImportError；在 SwarmAI 自己的 worktree 里会
加载 run 正在改的代码。即使能跑，`--domains ddd` 只返回 `{doc, section, score}`，没有内容、行号和 id。

**1.7 按需取到的无法引用。** `knowledge_refs` 只认召回过的 8 位 id（engine.py:2344）；专家 finding 的
`knowledge_ref` 在 answers.py:107 先过 8 位 hex，再在 hook_bridge.py:260 只留 handout 带过的 id；
REPORT 的引用计数 `& known`（engine.py:2807）也只算召回过的。

**1.8 结构约束（原型实测）。** 单个 `##` 很大（TECH `Conventions` 257K、`Key Subsystems` 214K 含 51 个 `###`；
IMPROVEMENT `What Worked` 340K、几乎没有 `###`），且 IMPROVEMENT 约 1/3 条目是 `- [type] **标题**`，
其余是普通 bullet。所以按需读的真实路径是**按词找到单元，再取整个单元**，而不是"读一个小节"。

**1.9 DDD 自我描述过期。** 7-21 六段编号重构只迁了目录没迁 AGENTS.md。SwarmAI 已手修；AIDLC / CMHK_SalesIntel /
GitHub_Community / IVTHub / PhysicalAI / ai_ready_repo 仍写 `gates/`、`skills/`。模板（swarm_workspace_manager.py:461）是对的。
**教训直接进设计：寻址只能从代码和磁盘生成，不能读手写描述。**

## 2. 设计

### 2.1 一个生成器：`core/addressing.py`

```
schema() -> str                          # L1，纯代码常量，无 I/O
instance(workspace) -> list[Project]     # L2，读 Projects/*/ + bindings.yaml
ddd_map(project, size="full"|"brief")    # L3，当场读磁盘
ddd_units(project) -> [Unit]             # L3 的单元表：path, line, kind, title, text, id
```

- **只用代码常量和磁盘**：目录名取 `ddd_paths`，文档集取 `DDD_CANONICAL_DOCS`，库和域取 `recall_multi.DOMAINS`，
  不读 AGENTS.md / SWARMAI.md 的文字。旧布局通过 `ddd_path` 的 new-then-old 回退同样能生成。
- `ddd_units` 用 recall 自己的切分（`split_blocks` / `_scan_entry`），id 用 `run_knowledge.item_id(原文)`——
  **和 recall 起步包里的 id 是同一个函数算的**，所以起步包里的条目和按需取到的条目可以共用一种引用。
- 安全（R32 B 类）：只列 DDD 根内、`resolve()` 后仍在根内的 `.md` 文件；跳过 `.artifacts/`、`*.lock`、
  隐藏文件、数据库；标题和内容放进 `<recalled>` 围栏（照 `_FENCE` 去标记），注明是数据不是指令。
- 不抛异常：无 DDD / 缺文档 / 非 UTF-8 / 解析失败，只少掉那一部分，并在输出里说明（接 run-knowledge H5 矩阵）。

### 2.2 L1 + L2 进 system prompt：`## Workspace Addressing`

在 `prompt_builder` 的**可缓存基段**里、11 个 context 文件之后加一个生成段（不是 context 文件：不可手改、
不归任何人编辑），约 3K 字符：

1. **SwarmWS 布局**：`.context/`、`Projects/`、`Knowledge/<子目录>` 各是什么（目录集取自代码）。
2. **DDD 六段标准结构**：`AGENTS.md`、`2-understanding/{PRODUCT,TECH,IMPROVEMENT,PROJECT}.md` 各装什么、
   `*-archive.md`、`2-understanding/knowledge/`、`3-gates/`、`4-capabilities/`、`bindings.yaml`、`REFRESHER.md`、
   `assets/<kind>/`。
3. **路由表——什么问题去哪找**：产品意图/非目标 → PRODUCT；架构/约定/陷阱 → TECH；踩过的坑/教训 →
   IMPROVEMENT；当前焦点/决定 → PROJECT；深度参考 → `2-understanding/knowledge/`；跨项目参考 →
   `Knowledge/Library/`；跨会话判断 → MEMORY；纠正 → EVOLUTION；某仓库的 pipeline 经验 →
   `Knowledge/Library/pipeline-lessons/<repo>.md`。
4. **怎么取**：`"$SWARM_BACKEND_BIN" knowledge map / find`（2.3），以及 Grep / Read；"大文档不要整份读"。
5. **怎么引用**：8 位 id（2.4）。
6. **L2 项目表**：每个项目一行——名字、DDD 根（绝对路径）、有无 DDD、绑定的仓库根。不含大小、条目数、摘要。

接入细节：
- **失败隔离**：生成段在自己的 try 里，失败只丢这一段并 ERROR 日志，核心文件不连坐（8-11 那次 34h 静默降级的教训）。
- **缓存**：内容只在产品升级或建/删项目时变，可缓存基段不会因它频繁失效。
- **会话类型**：群聊 / 非 owner 渠道只给 L1，不给 L2（项目名可能是客户名；和 `WHOLE_FILE_PRIVATE` 同一条规则）。
- **删重复散文**（R25：好的修复删代码）：`SWARMAI.md` 的 § My Workspace 和 FAQ 里 "What projects do I have…
  There is no injected index" 改成一句"见 Workspace Addressing"；`SELF.md` 的 recall 用法改指 `knowledge` 命令。
  `KNOWLEDGE.md` 仍是用户自己的 Knowledge/ 注释，不动。

### 2.3 L3 按需取：`"$SWARM_BACKEND_BIN" knowledge …`（顶层子命令，修 1.6）

和 `pipeline-v1` 一样在 `desktop_main.main` 里分派，**不走 `py -m`**，所以不受 cwd 影响，也不会加载 worktree 里的代码：

- `knowledge map --project P [--brief]`：打印 `ddd_map`。
- `knowledge find --project P --query "<词>" [--in ddd|library] [--doc TECH.md]`：在 `ddd_units`（和 Library）里按 BM25 找，
  返回**整个单元**（CDF：整条或不给）+ `path:line` + 8 位 id；放不下的列出位置和标题。
- `knowledge show --project P <id>|<path>:<line>`：取单元全文和它的 id（Grep 找到行号后用它拿 id）。

只读、只限 DDD 根和 Library，不碰 MEMORY / 会话（那些仍走带隐私门的 `context_recall_cli`）。

### 2.4 引用：仍是 8 位 id，"认得的 id"从"召回过的"扩成"DDD 里现存的"（修 1.7）

不加位置引用（r1 的方案：路径解析、大小写、前缀撞车、条目没标题都有洞），**契约形状不变**：

- **已知 id 集合** = 本 run 召回过的 ∪ `ddd_units(project)` 里现存单元的 id（含 `*-archive.md`，所以 run 中途被归档的条目仍认得）。
  项目和 DDD 根取自 contract（Agent 改不了）。
- REQUIREMENTS / DESIGN_SPEC：未知 id 仍拒绝，提示用 `knowledge show` 重新取（条目在 run 中途被改写时会这样）。
- 专家 finding：`answers.py` 不动；`hook_bridge._occ` 的 `carried` 换成上面的已知集合；**未知仍丢弃、finding 照样算**（run-knowledge §3.4.1）。
- 读者全改（R27）：`schemas`（文案）、`_check_knowledge_refs`、`_knowledge_summary` 和 engine.py:2807 的计数（拆成"起步包 / 按需"）、
  `audit.py:522-526,709`、`hook_bridge`、`events.py:137`、`stages/evaluate.md` 的"an id not in that file is refused"、
  以及断言旧行为的 `test_pipeline_v1_expert_knowledge.py:95-112`。REPORT 只出计数，不出条目标题（REPORT 会提交进用户仓库）。
- **按需引用也进反馈回路**：被引用的按需单元喂给 `record_ddd_hit`（否则衰减会归档 Agent 正在用的条目）和 REFLECT 去重候选。

### 2.5 各扇门（P8）

| 门 | 拿到什么 |
|---|---|
| 主会话 / 聊天 / Hive 上的 SwarmAI | system prompt 的 L1+L2；聊天 `[DDD:X]` 命中项改为 `绝对路径:行号 § 小节`，无命中时的指针删掉（L2 已经有） |
| pipeline `knowledge.md` / `knowledge_design.md` | 起步包 + `full` 地图（L3）；not-shown / pinned / pointer 行改成绝对路径 + 行号；stage 文档改写"读 knowledge 文件"那句 |
| correctness 专家 | 起步包 + `full` 地图 |
| 其他专家 | `brief` 地图（约 600 字符，只是指针；run-knowledge §4 的"不给 security 加块"指召回内容）。按需读会花专家的调用预算，handout 写明"只在 finding 需要时查" |
| `repo_guidance` | DDD 那一行改为"绑定到项目 P，DDD 根 …，见 Workspace Addressing"，**放在 lessons 之前**，`seen["@ddd"]` 只在真正放进去后才记 |
| 渠道（群聊） | 只有 L1 |
| 子 agent / 无 SwarmAI host 的 Claude Code | 拿不到 system prompt：靠 handout 和 knowledge 文件里的 L3（所以 L3 必须自足，写明 `knowledge` 命令） |

### 2.6 1.9 的 6 个过期 AGENTS.md

寻址只读代码和磁盘，AGENTS.md 不再是 Agent 获取布局的途径，所以不建产品迁移（P9）。这 6 份是本机项目文件，
单独修一次：备份后只替换三类已知旧写法，diff 核对。

## 3. 考虑过的其他方案

| 方案 | 为什么不选 |
|---|---|
| 加大 recall 配额 | 仍受查询词限制，用词对不上照样漏；Agent 依然不知道全集；上下文变胖 |
| 把 PROJECTS.md（内容索引）加回来 | 内容摘要会过期，08-14 删它的理由对内容索引仍成立；本设计只放结构和地址 |
| 位置引用 `path#section`（r1） | 审查发现：大多数条目没标题、40 字前缀会撞、路径大小写和别名都要规范化；改为 id + 扩已知集合，契约形状不变 |
| hook 观察 run 内对 DDD 的 Read 并写事件 | 确定性观察最好，但给 run 事件加一个 hook 写入方动到 run 完整性；先用 transcript 脚本量（§4） |
| 继续写散文规则 "先去读 DDD" | 1.3：现有的指针连送达都没做到；散文规则已经证明不 hold |

## 4. 怎么量

- 把 1.2 的 transcript 脚本放进 `backend/scripts/`：每个 run 对 DDD 路径的读取次数、`knowledge` 命令调用次数、
  起步包 id 和按需 id 的引用数。
- **分阶段看**（审查 M1：指针以前没送达过，先单独验证"告诉了它会不会去用"）：步骤 A+B 上线后先跑几个真实 run 看数；
  按需读取仍为 0 → 先改 2.2 的写法，而不是继续往下建。

## 5. 实施

| 步骤 | 内容 | 新增测试（每条先在 main 上红，做变异检查） |
|---|---|---|
| A | `core/addressing.py`（schema / instance / ddd_map / ddd_units）+ `knowledge` 子命令 | 目录名和文档集变了（改常量）输出跟着变；旧布局也能出；只列根内 `.md`，软链接指向根外不列；行号指向标题行；超预算整行取舍；`ddd_units` 的 id 和 `recall_ddd` 同一单元的 id 相等；从别的 cwd 调用不加载那里的代码；H5 矩阵不抛异常 |
| B | system prompt `## Workspace Addressing` + 删 SWARMAI/SELF 的重复散文 | 基段含该段、不含任何数字快照；群聊只有 L1；生成失败时核心文件仍在且有 ERROR；建项目后下一次构建出现新行 |
| C | pipeline knowledge 文件 + 专家 handout + repo_guidance 顺序 | has_ddd 时有 full 地图、无 DDD 时没有；not-shown/pinned/pointer 行是存在的绝对路径；`[Project DDD]` 在 lessons 吃满预算时仍送达 |
| D | 引用：已知集合扩展 + 全部读者 + 反馈回路 | 按需 id 被接受（文档和 finding）；编造的 id 被拒/被丢；被归档的条目 id 仍认得；计数分两类；被引用的按需单元进 `record_ddd_hit` 和 REFLECT 候选 |
| E | 聊天指针带路径行号 | 命中项是 `绝对路径:行号`；无命中时不再单独注入指针 |
| F | 本机 6 份 AGENTS.md（非产品代码） | 备份；diff 只含三类替换 |

**最终测试集：** A–E 的新测试 + `run_knowledge`、`recall_multi`（recall_ddd）、engine/hook_bridge/answers 的
knowledge_ref、`repo_guidance`、`session_router` DDD 注入、`prompt_builder` 基段/隔离的现有测试。
**审查：** A+B 完成后一次（correctness + security：路径边界和 prompt 注入面）；C+D 完成后一次（correctness +
api-contract：已知集合和全部读者）。
**端到端：** B、C 部署后跑一个真实 trivial run 和一个聊天会话，确认 prompt 有寻址段、knowledge.md 有地图，用 §4 脚本看 Agent 是否按需取用。

## 6. 要 XG 定的

1. 寻址段进可缓存基段，每个会话常驻约 3K 字符（我倾向进：这是"知道自己身体结构"的常驻认知，不是检索结果）。
2. 群聊只给 L1、不给项目表。
3. 实施方式：A–F 跨 prompt_builder / run_protocol / recall / repo_guidance / session_router / CLI 分派，属于风险改动——
   直接改（按 A→F 分提交，两次专家审查），还是走 pipeline（建议拆成 3 个 run：A+B、C+D、E+F）。

## 修订

- r1（2026-10-28）：初稿，只覆盖 pipeline 的 DDD 地图 + 按需读 + 位置引用。
- r2（2026-10-28）：零上下文审查确认 2 个 HIGH、4 个 MED（均已对源码核实）：
  H1 位置引用在 answers.py:107 / hook_bridge.py:260 被静默丢弃，读者远多于 r1 列的三处 → 改为 id + 扩已知集合，契约形状不变；
  H2 `py -m` 按需命令受 cwd 影响 → 改为顶层 `knowledge` 子命令；
  M1 repo_guidance 指针被 lessons 挤掉且不重试 → 调顺序、放得下才记 seen，并分阶段量；
  M2/M3 位置引用的标题撞车和路径规范化 → 随 H1 一起消失；
  M4 run 中途归档 → 已知集合含 archive。
  同时按 XG 要求上升为系统级"标准寻址"：L1/L2 进 system prompt，L3 只在使用点生成。
