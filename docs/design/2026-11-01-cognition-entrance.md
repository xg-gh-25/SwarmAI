---
title: 认知统一入口 `cognition` —— 一条命令读整个脑
date: 2026-11-01
created: 2026-11-01
updated: 2026-11-01
status: implemented (r4, native；代码 4fa048665 + 文字提交，见 §11)
owner: SwarmAI backend (knowledge_cli / addressing / prompt_builder)
related:
  - docs/design/2026-10-28-knowledge-addressing.md      # knowledge map|find|get 的来源，本设计在它上面扩
  - docs/design/2026-10-30-recall-that-measures-itself.md  # 查询由推理者写
  - docs/design/2026-10-25-knowledge-delivery-gaps.md   # owner 私聊开召回（G4）
---

# 认知统一入口 `cognition`

## 0. 结论

SwarmAI 的认知分在八个库里：Brain（每个项目的 DDD，和通用 Brain `General` = `Knowledge/Library`）、`Knowledge/` 的全文检索、MEMORY、EVOLUTION、TIMELINE、
其余上下文文件（SOUL / AGENT / SELF / USER / TOOLS …）、过去的会话、代码图谱。今天 Agent 主动去读，
要用两条形状不同的命令（`knowledge` 和 `context_recall_cli`），后者按 `## ` 小节返回 JSON、没有 id、
到不了 EVOLUTION 的归档、TIMELINE 的事件也切不开，隐私靠 Agent 自报（§1）。

本设计把已有的 `knowledge map | find | get` 扩成**整个认知的唯一读入口**，改名 `cognition`
（`knowledge` 保留为同义命令，单库输出与今天逐字相同）：

- **一种单元**：每个库切成整条单元，每条带 `绝对路径:行号` 和 8 位 id，不丢内容；
- **通用 Brain 和项目 DDD 一样对待**：`--project General` 有自己的单元表，能 find、get、引用，`map --projects` 里排第一；
- **一种输出**：所有库同一个文本格式、同一个 `--json` 形状；
- **一个隐私判断**：和推送式 recall 用同一个判断（`session_router._channel_recall_allowed`），由 daemon 在子进程环境里给出；
- **用量照旧记**：CDF 管的文件，读到的条目记进用量。

不新建索引、不新建 skill、不改推送式 recall、不改 CDF 解析器、不加 gate。Agent 照样可以 Read / Grep 原文件。

这件事的价值不在调用量（见 §1.2，主动查上下文文件本来就不多），而在一条**少用但关键**的读法：
回答"我的系统哪里有问题、这类错以前怎么判过"时要查 SELF / EVOLUTION 归档 / TIMELINE 事件，
SELF.md 自己写着这条读法最容易漏（Recall 一节的 trigger (b)）。今天它要靠记住几条 ⚠️ 才走得通。

## 1. 现状（2026-11-01 读代码 + 实测，经一轮零上下文审查核对）

### 1.1 三条命令

| 命令 | 覆盖 | 单元 | 输出 | 隐私 | 用量记录 |
|---|---|---|---|---|---|
| `"$SWARM_BACKEND_BIN" knowledge map\|find\|get`（`core/knowledge_cli.py`） | DDD（四份文档、归档、`knowledge/` 语料、`data-contract/`）、Library | 整条，`path:line` + `unit_id`（`addressing._file_units`，addressing.py:168） | 文本，`--json` 可选 | 非 owner 会话整条拒绝（`RESTRICTED_ENV`，knowledge_cli.py:163；prompt_builder.py:2594-2597，在 `permission_tier != "owner"` 时） | 有（`_record_use`，knowledge_cli.py:114） |
| `py -m scripts.context_recall_cli --file F \| --domains d` | `--file`：单个上下文文件；`--domains`：`recall_multi.DOMAINS` 五个推送域，其中 `context_files` 只读 MEMORY.md（recall_multi.py:883） | `--file` 按 `## ` 小节；`--domains` 各域各自的形状 | 只有 JSON | Agent 自报 `--session-type`（context_recall_cli.py:70-75） | 无 |
| `py -m core.code_intel.impact` | 代码图谱的调用方分析 | 符号 | 文本 | 无 | 无 |

Brain Hub 本身不存知识，是所有 DDD 的投影，已由 `knowledge map --projects` 和 `--project` 覆盖。

### 1.2 实测：14 天的 Agent 记录

两次独立计数（我一次，审查员一次）结果在同一量级，但不完全相同，下面写区间：

| 读法 | 次数 |
|---|---|
| `knowledge find\|get\|map` | 91–116 |
| `context_recall_cli` | 20–27，其中 5 次是 `--domains ddd`：已有 `knowledge find` 却仍走旧路（指向它的旧说明还在 TOOLS.md） |
| Bash 的 grep / sed / cat 读 `.context/{MEMORY,TIMELINE,SELF,EVOLUTION}*.md` | 118–141，其中只有约 32 次是命令的第一步 |
| 对 `knowledge find` 的文本输出直接 `json.loads`、却没加 `--json` | 11 |

怎么读这组数：MEMORY 和 EVOLUTION 每轮整份注入，TIMELINE 注入月度汇总，所以直接 grep 这几个文件
**大多是在调试 CDF 本身，不是查知识**。"Agent 绕过入口"不成立，不作为本设计的理由。
能成立的是：两条命令形状不同造成的误用（走旧路 5 次、当 JSON 解析 11 次），以及 §1.3 的几处读不到。
另外，Read / Grep 工具读这些文件本来就有用量观察者（`hook_builder.py:688-695`，`usage.create_usage_observer`），
没有记录的只是 Bash 里的 grep。

### 1.3 具体缺陷（逐条实测）

1. **TIMELINE 不能按事件读。** `context_recall_cli --file TIMELINE.md --query "pipeline landing"` 把整个
   `## Not injected` 小节当一个单元返回，十几条事件一次吐出，没有行号，也不能单独引用（本次实跑）。
   这一节的事件不注入 prompt，只能主动查。
2. **EVOLUTION 归档读不全。** `context_recall_cli --file` 按 `## ` 切，`EVOLUTION-archive-<月>` 是 `### ` 块，
   SELF.md 自己写着这类分片"no sections parsed"。换 CDF 解析器也不行：`parse_units` 读老分片
   `EVOLUTION-archive.md` 只得到 6 个单元，而文件有 18 个 `### ` 块（本次实跑），因为它按文件名选解析器
   （units.py:224-227），归档走 bullet 分支，不是 bullet 的块就丢了。
3. **两套说明。** SELF.md 和 TOOLS.md 里有好几处 ⚠️（"`--domains` 到不了 SELF / EVOLUTION""要用 `--file`"），
   Agent 得先记住哪扇门通哪里。
4. **隐私靠自报。** `context_recall_cli` 只信 Agent 传入的 `--session-type`。推送式 recall 已经有一个明确的判断：
   channel 里只有 owner 的私聊能召回，"别处的回复别人能看到"（session_router.py:1252-1261）。主动查没有用它。
5. **s_self-evolution 的重叠检查**（SKILL.md:57、INSTRUCTIONS.md:51）用 `--file SOUL.md` 按小节查，
   得到整节，而不是能逐条对照的那一条。
6. **通用 Brain 查不到。** `Knowledge/Library` 是自带的通用 Brain（`project_agent.GENERAL_BRAIN`、`general_brain_dir`，
   project_agent.py:58、:123）：CDF 把跨项目的经验写进它的顶层文档（`brain_knowledge.brain_files`，brain_knowledge.py:51-58），
   派单、同步、教训范围也都把它当一个 Brain。但 `knowledge find --project General` 今天报 "no project named 'General'"
   （本次实跑），因为 `addressing.project_root` 只认 `Projects/<名字>`（addressing.py:76-91）。它只能经 `--in library`
   的全文检索读到：按 FTS 分块，没有单元表，不能 get，也不能被 run 引用。64 个 Markdown 文件、约 0.9 MB。

## 2. 名字：`cognition`

| 候选 | 判断 |
|---|---|
| **`cognition`** | 和 UI 的 Cognition 区一致（C&M · Library · Brain Hub 三扇门进一个脑，SWARMAI.md § My Brain），读的正是这个区的全部内容。**选它。** |
| `knowledge` | SOUL 的说法是"认知是 OS，知识是磁盘"。新入口也读 SOUL / AGENT / SELF / EVOLUTION，再叫 knowledge 名不副实 |
| `brain` | 已被占用：一个 Brain = 一个项目的 DDD（Brain Hub、New Brain） |
| `recall` | 已被占用：OS 主动推送的那条路 |

`knowledge` 保留为同义命令，不设下线时间：pipeline stage 文档、专家提示、`[DDD:]` 提示块、`repo_guidance`、
`task_spec` 和运行记录里都写着它。

## 3. 设计

### 3.1 命令

```
"$SWARM_BACKEND_BIN" cognition map                                       # 有哪些库、各有什么、怎么查（现场生成）
"$SWARM_BACKEND_BIN" cognition map --projects | --project P [--brief]       # 不变
"$SWARM_BACKEND_BIN" cognition find --query "<词>" [--in <库>[,<库>…]|all] [--project P] [--doc F] [--top N] [--json]
"$SWARM_BACKEND_BIN" cognition get <id>|<path>:<line> [--project P] [--json]
```

- `--project P` 里的 P 可以是任何项目，也可以是 `General`（通用 Brain，大小写按 `project_agent.is_general_name`）。
- `--in` 默认：给了 `--project` 是 `ddd`（和今天一样）；没给是 `all`。
- `all` = 全部库。通用 Brain 总在里面（它不属于某个项目）；没给 `--project` 时项目 DDD 和 `code` 不查，
  输出里写一行"没查：要 `--project`"。
- `--doc` 只对文件类的库有意义（`TECH.md`、`MEMORY-archive-2026-10.md`、`SOUL.md`）。

### 3.2 库表

`core/cognition.py: STORES`，每个库：名字、一句话说明、文件集合、怎么切、对应的推送域。

| 库 | 文件 / 来源 | 单元怎么切 | 对应 `recall_multi.DOMAINS` |
|---|---|---|---|
| `ddd` | 项目 DDD：`addressing.ddd_units(project)`，不变。通用 Brain：`Knowledge/Library` 的顶层 `*.md` 为正文（`doc`，和 CDF 的 `brain_files` 同一集合），子目录的 `**/*.md` 为语料（`corpus`），同样的读取规则（不读隐藏文件、锁文件、符号链接，大小上限照旧） | `addressing._file_units` | `ddd` |
| `library` | `run_knowledge._library_leg`（FTS5，整个 `Knowledge/`，含 DailyActivity），不变。多库查询时，落在 `Knowledge/Library/` 里的命中不再列出（通用 Brain 已按整条单元给出），单查 `--in library` 时照旧 | 原样 | `library` |
| `memory` | `.context/MEMORY.md` + `MEMORY-archive-*.md` | `addressing._file_units` | `context_files` |
| `evolution` | `EVOLUTION.md` + `EVOLUTION-archive*.md` | 正文：`parse_units`（`### ` 块）；归档：`addressing._file_units` | — |
| `timeline` | `TIMELINE.md`（含 `## Not injected`） | `parse_units`（`### ` 事件） | — |
| `context` | SWARMAI、IDENTITY、SOUL、SELF、AGENT、USER、STEERING、TOOLS、KNOWLEDGE | `addressing._file_units` | — |
| `session` | `recall_multi._recall_session`（FTS5），只能 find | 每个会话的命中消息 | `session` |
| `code` | `recall_multi._codeintel_recall`（符号搜索），只能 find；调用方分析仍是 `core.code_intel.impact`，那是分析工具 | 符号 + `path:line` | `codeintel` |

**不是第二个登记表。** `DOMAINS` 是推送的几条腿，`STORES` 是主动查的文件集合，粒度不同（`context_files` 只有 MEMORY）。
两者用 `STORES` 里的"对应推送域"一列连起来，测试钉住：`DOMAINS` 里每个域都有一个库对应（§7.2 第 7 条）。
以后加一条推送腿而没给主动查一个去处，测试红。

### 3.3 单元和身份

- **切法只有两种，按一条规则选：** 单元是 `### ` 块的文件（EVOLUTION 及其归档、TIMELINE）→ 在每个 `### ` 标题处切，
  标题和它下面直到下一个 `### ` 或 `## ` 的每一行是一条（`cognition._h3_units`；边界和 CDF 的 `### ` 块相同，
  但不丢块与块之间的散文，`parse_units` 会丢）；其余（MEMORY 及其归档、其余上下文文件）→ `addressing._file_units`，
  和 DDD 文档同一种切法。空行之间的内容一个不落；文件末尾仍未闭合的代码围栏不算围栏（与 addressing 同一规则）。
- **不改 `parse_units`。** r1 想给它加一个 `layout` 参数，但它的缓存键不含布局（units.py:196-205），会把两种结果混着返回，
  而且 CDF 自己的读者（usage、writer、health、gaps）读归档时都用 bullet 布局。归档换成 `_file_units` 就不需要碰它。
- 每条单元：`store · path · line · section · title · text · id`。`id = ddd_entry_lifecycle.unit_id(text)`，和 DDD、
  Library（`run_knowledge.item_id` 就是它，run_knowledge.py:86-91）同一个函数，一条经验从 MEMORY 搬进某个 DDD，id 不变。
- **同一 id 指多条时**（正文和它的归档副本、MEMORY 和某个 DDD 里同一句话），沿用 `addressing.matches` 的做法：
  先给正文、再语料、再归档，其余的列在"The same id also names: …"。
- 外部来源的单元（条目带 `taint:external`，或 `recall_multi.is_external_source / is_repo_doc_source` 判为外部）
  在位置行后标 `(external)`。所有正文照旧放进 `<recalled>` 围栏（`addressing.fence`）。

### 3.4 隐私：和推送式 recall 用同一个判断

| 会话 | 今天 | 之后 |
|---|---|---|
| 桌面聊天、channel 里 owner 的私聊 | 全部可读 | 不变 |
| channel 里的其他任何会话（群聊，含 owner 在群里发话；非 owner 私聊） | 非 owner：`knowledge` 整条拒绝；owner 在群里：`knowledge` 可读 DDD 和 Library，`context_recall_cli` 靠自报 | **整条拒绝**，两个名字都是 |

- 信号：spawn 时 daemon 计算 `not _channel_recall_allowed(channel_context)`，为真就在子进程环境里设
  `SWARM_COGNITION_OFF=1`；CLI 见到它退出码 3。判断函数只有一个，推送和主动查共用（必要时把它移到两边都能 import 的地方）。
- **不复用 `RESTRICTED_ENV`**：它还挡着 Swarm Out（scripts/swarm_out.py:375），扩大它会把群里 owner 的派单也关掉。
  非 owner 会话里两个变量都会设上。
- **为什么不是"群里只拒私人库"**（r1 的做法）：审查发现两处漏。r1 只按 `GROUP_CHANNEL_EXCLUDE` 拒，
  漏了只能分节注入的 STEERING.md（`CHANNEL_SECTION_FILTERED`，context_directory_loader.py:247），会让群里读到整份；
  `Knowledge/` 的全文检索又含 DailyActivity、Meetings、Handoffs，正是 MEMORY 的原料。逐库划线划不干净，推送那边已经得出同样的结论。
- **子进程环境在 spawn 时固定**：群聊的 `is_group`、私聊的发送人都是会话级的，所以 spawn 时的判断就是整个会话的判断。
  实施第一步核实 channel 子进程不会被私聊和群聊共用；若会，这条和今天的 `RESTRICTED_ENV` 一样需要改到按消息判断。
- 这是可用性保护，不是保密边界（与 `knowledge_cli` 文档同一说法）：Agent 控制自己的命令行。它比今天好在
  判断来自 daemon，而且和推送一致。代价：owner 在群里不能再用 `knowledge find` 查 DDD（§9 第 2 条）。

### 3.5 输出

- **单库输出不变。** `--in ddd` / `--in library` 的文本和 `--json` 与今天的 `knowledge find` 逐字相同，
  只有提示里的程序名换成实际调用的名字（`cognition find: …` / `knowledge find: …`）。
- 多库（`all` 或 `--in a,b`）按库分组，每组一行标题（`MEMORY — 3 条，最相关在前`），然后每条
  `` - `id` · 绝对路径:行号 `` + 围栏正文；放不下的用 `recall_filter.not_shown_note` 列出位置、标题、长度；读不了的列在 `(not read: …)`。
- `--json`：所有库同一个形状 `{"units":[{store,id,path,line,section,title,text}], "omitted":[…], "skipped":[…]}`，
  就是今天的形状；外部来源多一个 `"external": true` 字段，只在为真时出现。
- 文本头一行写明"加 `--json` 得到 JSON"（11 次误解析）。
- **多库不合并分数。** 每个库在自己的语料里排 BM25（和 `addressing.find` 一样），各取 `--top`（多库时默认每库 3 条）。
  跨库的 BM25 分数不可比（recall_multi.py:~113 记过这个坑）。

### 3.6 用量

`_record_use`（knowledge_cli.py:114）对所有文件类单元都调用。它经 `usage.units_in_content` 只对 CDF 管的文件生效
（`usage.py:167-176` 的 `is_governed`，即 `materializer._CONTEXT_NAMES` 里的 MEMORY / EVOLUTION / STEERING / USER / TOOLS / TIMELINE
加 DDD 和 Library 的正文）。归档和 SOUL / AGENT / SELF 等不记，这是对的：CDF 不管它们，记了也没人读。

### 3.7 `cognition map`

不带参数时现场生成一张表：每个库的名字、一句话说明（来自 `STORES`）、文件和单元数、一条示例命令；
在不能读的会话里只输出那一行拒绝。现场生成、不存、不进 system prompt（08-14 活/冷总纲）。
Agent 不需要记住任何门，一条 `map` 就知道整个脑有什么。

## 4. 改什么（R27：所有消费方）

**代码（一个提交）**

| 位置 | 改法 |
|---|---|
| `desktop_main.py:483` | 加 `cognition`，`knowledge` 同指一个 `main`，`main` 收到被调用的名字 |
| `core/knowledge_cli.py` | 两个名字共用的入口；`prog` 和所有提示里的名字用被调用的名字；新的库读取放进 `core/cognition.py` |
| `core/addressing.py` | `project_root` 认 `General` → `general_brain_dir`；`_all_files` 对通用 Brain 用 §3.2 的文件集合；`projects()` 把 `General` 排第一。其余函数不变 |
| 因此行为会变的 `addressing` 调用方 | `jobs/handlers/brain_mistake_audit.py:150`：通用 Brain 的经验之后也能被找到。`ingestion/overrides.py` 不受影响：它的项目来自来源（会话或 run 的项目，overrides.py:121-128），从不是 `General`，所以 r3 写的"之后能点中通用 Brain 的条目"不成立，已删；通用 Brain 的替换声明是另一件事。`run_knowledge.ddd_map`、`engine` / `hook_bridge` 的 `unit_ids`、`brain_eval`：项目都在 `Projects/` 下，不受影响 |
| `prompt_builder.py`（spawn 环境，`RESTRICTED_ENV` 旁边） | 按 `_channel_recall_allowed` 设 `SWARM_COGNITION_OFF` |
| `security_hooks._KNOWLEDGE_WORD_RE`（:6423） | 加 `cognition` |
| `scripts/knowledge_usage_report.py` | 匹配两个名字，按库统计；加一列 Bash 直接读上下文文件 |
| `scripts/verify_build.py:88`（打包模块清单） | 加 `core.cognition` |

**文字（第二个提交，因为有同义命令，可以晚于代码）**

| 位置 | 改法 |
|---|---|
| `backend/context/SWARMAI.md` § My Workspace "Finding things" | 改指 `cognition`，一句话说 `map` 能列出全部库 |
| `backend/context/SELF.md` Recall 和 Memory management 两段 | 删掉"哪扇门到哪"的 ⚠️，改成"主动查一律 `cognition`" |
| `.context/TOOLS.md` § On-Demand Recall（本机实例文件，先备份） | 改写成指向 `cognition`，删掉 Mode A / Mode B |
| `skills/s_self-evolution` SKILL.md:57、INSTRUCTIONS.md:51 | 重叠检查改为 `cognition find --in context,evolution --query "<拟议文字>"`，逐条对照 |
| 给 Agent 看的提示：pipeline stage 文档（think / plan / evaluate / deliver）、s_expert-review、s_qa、design-judgment.md、`session_router` 的 `[DDD:]` 提示、`repo_guidance.py:268`、`task_spec.py:347,351`、run_protocol 的 engine / hook_bridge / events / run_knowledge | `knowledge` → `cognition` |
| 文档字符串和注释：`memory_index.py:24`、`session_router.py:1032`、`brain_sync.py:10`、`ddd_format.py:17`、`ingestion/contracts_v2.py:145`、`relations_v2.py:16`、`status.py:101`、`recall_multi.py:1103` | 同上（R7：描述机制的注释跟着改） |

`desktop/src` 没有消费方。`scripts/context_recall_cli.py` **不删**：eval 探针（`recall_chain_probe.py`、
`golden_case_validator_test.py`）和 4 个测试文件还用它；它只是不再出现在任何 Agent 读的文字里。探针迁走后再议，记成已知项。

## 5. 不做

- **不做 skill。** skill 是说明书，靠触发词加载；查认知发生在别的工作中途，没有触发词。
  用法写在每轮都注入的 SWARMAI.md 一段里，10-28 的设计已经证明这样有效。
- 不做 MCP 工具：本机所有 MCP 都因握手慢降成 ondemand；CLI 在 Claude Code、Kiro、Hive 上都能跑。
- 不改推送式 recall，不改 CDF 解析器，不加写能力（写仍走 s_persist / CDF）。
- 不新建索引，不加向量检索，不把清单放进 system prompt，不合并跨库排序。
- 不加"必须先查"的 gate，不拦直接 Read / Grep。
- `get` 不支持 `library`（`Knowledge/` 其余目录的 FTS 分块，约 1100 个文件，没有单元表）、`session`、`code`（没有稳定单元）。
  通用 Brain 不在此列：它有单元表，能 get、能引用。

## 6. 考虑过的其他方案

| 方案 | 为什么不选 |
|---|---|
| 新建 `s_cognition` skill，教 Agent 什么时候用哪条命令 | 命令还是两条，skill 只是把 SELF.md 里的 ⚠️ 换个地方放；触发时机也不对（§5） |
| 让 `context_recall_cli --domains all` 当统一入口（它已经覆盖五个推送域） | 它只读 MEMORY 的正文，到不了 EVOLUTION / TIMELINE / SELF 和归档；按小节、只有 JSON、没有 id、隐私靠自报。修完这些，它就成了第二个 `knowledge` |
| 保留名字 `knowledge`，只加 `--in` | 少改一次名，但名字和内容对不上（§2） |
| 群里只拒私人库（r1） | 划不干净（§3.4） |
| 什么都不做 | §1.3 的五处照旧读不到或读不对 |

## 7. 验证计划（定在这里，不在做的过程中加）

### 7.1 读者先于写者

新形状只有两个：`--json` 的 `external` 字段（读者是 Agent）和 `SWARM_COGNITION_OFF`（读者是 `cognition` 自己）。
代码里没有解析 `knowledge --json` 的读者（grep `run_protocol` 和 stage 文档为空）。

### 7.2 最终测试集（每条先在 main 上红，再用 `scripts.mutation_check` 撤回修复看它变红）

1. **不丢内容**：对 `memory / evolution / timeline / context` 的每个真实文件和 fixture，每一行非空、非注释、非纯标题的正文都落在某个单元里；
   每个单元都能用 `get <path>:<line>` 原样取回。包括老分片 `EVOLUTION-archive.md` 的 18 个块。
2. TIMELINE 的 `## Not injected` 下 2 个事件 → 2 个单元，各有自己的行号和 id。
3. 设 `SWARM_COGNITION_OFF`：`cognition` 和 `knowledge` 两个名字的 `map / find / get` 都退出码 3、不读任何文件（打桩断言没有打开文件）。
4. spawn 环境：`channel_context` 为群聊（owner 和非 owner 各一）、非 owner 私聊时设了 `SWARM_COGNITION_OFF`；owner 私聊和桌面没设。
   用的是 `_channel_recall_allowed` 本身（撤掉调用、改成手写条件，测试换一个 chat_type 就红）。
5. `knowledge_command_denial` 拒绝含 `cognition` 的命令。
6. **兼容**：在 main 上对 fixture DDD 录下 `knowledge find --project P --query Q`（文本和 `--json`）与 `knowledge get` 的输出；
   改动后 `knowledge` 的输出与录下的逐字相同，`cognition` 只差程序名。
7. `recall_multi.DOMAINS` 里每个域在 `STORES` 里都有对应；往 `DOMAINS` 加一个假域（monkeypatch），测试红。
8. `find --in memory` 命中一条 MEMORY 条目后，`record_usage` 收到这条的 anchor（spy 断言调用和参数，不在源码里搜函数名）。
9. 多库时，一个只在小库里命中的词出现在那个库的分组下，没有被大库挤掉。
10. **通用 Brain**：fixture 工作区里 `Knowledge/Library/` 有两份顶层文档和一份子目录文档。`find --project General` 返回整条单元、
    带路径和 id；`get <id>` 取回同一条；`map --projects` 第一行是 `General`；`find --project general`（小写）也指它。
    多库查询时，同一段文字不会既以通用 Brain 单元、又以 `library` 分块出现两次。
11. （删：见 §4，overrides 不经过通用 Brain。）

提交前另跑改动直接碰到的已有测试：`test_knowledge_cli*`、`test_addressing*`、`test_reversible_context_recall.py`、
`security_hooks` 里 owner 命令拒绝的测试、prompt_builder 的 channel 环境测试。

### 7.3 审查

- 代码提交（`core/cognition.py`、`knowledge_cli`、prompt_builder 环境、security_hooks）：correctness + security 两位专家（s_expert-review）。
- 文字提交：不请专家，靠 `scripts/lint_skills.py` 和第 6 条测试。
- 本设计：已做一次零上下文审查（§10），本次修完不再审，接下来靠测试。

## 8. 怎么量（上线两周后看）

| 指标 | 今天（14 天） | 期望 | 不达标怎么办 |
|---|---|---|---|
| `context_recall_cli` 的 Agent 调用 | 20–27 | 0 | 找出还在指向它的文字 |
| 把文本输出当 JSON 解析的错误 | 11 | 0 或明显下降 | 改头一行的写法 |
| 主动查 EVOLUTION 归档 / TIMELINE 事件 / SELF 的次数（走入口） | 几乎为 0（读不到或读不对） | > 0 | 回看 SWARMAI.md 那段和 `map` 的输出，不加 gate |
| 回答"系统哪里有问题"类问题时先查了 SELF / EVOLUTION（月度审计里抽查） | 不确定 | 抽查里多数先查 | 这是 trigger (b) 的问题，记进 EVOLUTION，不在本设计里加机制 |

## 9. 要 XG 定的

XG（2026-11-01）："Knowledge/Library 是我们的通用 DDD，你不能把它给拿掉，按你的设计来。"据此：

1. 名字用 `cognition`，`knowledge` 永久保留为同义命令：**按设计**。
2. channel 里除 owner 私聊外一律不能主动查（和推送式 recall 一致）：**按设计**。通用 Brain 和项目 DDD 同等对待，能查的地方都能查。
3. 实施方式：跨 CLI、prompt_builder 的子进程环境和安全 hook，属于有风险的改动。直接做（native，代码一个提交、文字一个提交），还是走 pipeline？

## 11. 实施记录（2026-11-01，native）

- **提交**：代码 `4fa048665`（cognition 入口、addressing 的通用 Brain、spawn 环境、prewarm 只进 owner 私聊、
  拒绝正则、用量报告的命令名、打包清单）；文字一个提交（SWARMAI.md / SELF.md 源文件、技能、阶段文档、
  `[DDD:]` 提示、`repo_guidance`、`addressing` 的用法行）。
- **与设计不同的地方**：
  - `### ` 块不用 `parse_units` 切（§3.3），因为它丢块与块之间的散文；实测真实文件上正文零丢失。
  - worker Hive：`library` 不整库拒绝，只返回 `Knowledge/Library/` 里的命中（同步过去的通用 Brain）。
    派给 Hive 的通用 Brain 任务提示写的是 `--in library`，旧版 Hive 也只认这个写法。
  - `task_spec` 给 Hive 的提示保留 `knowledge`：它在 Hive 上执行，Hive 的版本可能还不认识 `cognition`。
  - `run_protocol/engine.py` 等运行内部的提示文字没改名：另一个会话在 main 上有 engine.py 的未提交改动；同义命令保证能用。
  - `knowledge_usage_report` 只加了命令名，没加"按库统计"和"直接 grep"两列。
- **审查**：correctness + security 各一轮，无 CRITICAL / HIGH；两个 MEDIUM（worker 规则漏了 `get`、多库时误删 Library 命中）和
  七个 LOW 已修，每条有测试并做了变异检查。仍开着：拒绝正则在受限会话里也会挡带 `cognition` 字样的无害命令
  （与 `knowledge`、`addressing` 同样）；会话命中没有 `(external)` 标记；gateway 里 `_adopts_prewarm` 的调用处没有测试。
- **上线前不要做的**：本机 `.context/TOOLS.md` 和 `.context/SELF.md` 的新写法要等 build + 重启之后再换上
  （运行中的程序还不认识 `cognition`）。

## 10. 修订

- **r3（2026-11-01，XG 指正）**
  - 通用 Brain（`Knowledge/Library`）是一个 Brain，不是一个检索索引：`--project General` 有单元表，能 find / get / 引用，
    和项目 DDD 同等对待。r2 只把它当 `Knowledge/` 全文检索的一部分，等于把它从 Brain 里拿掉了；今天的 `knowledge` 也查不到它（§1.3 第 6 条）。
  - 多库查询时 `library` 不再重复列出 `Knowledge/Library/` 里的命中；单查 `--in library` 输出不变。
  - 补上 `addressing` 调用方里行为会变的两处（overrides、brain_mistake_audit）和两条测试。
- **r2（2026-11-01，零上下文审查后一次修完）**
  - 隐私改为和推送式 recall 同一个判断，整条拒绝：r1 的"群里只拒私人库"会漏整份 STEERING 和 `Knowledge/` 全文检索里的 DailyActivity。
  - 新增 `SWARM_COGNITION_OFF`，不复用 `RESTRICTED_ENV`：后者还挡着 Swarm Out。
  - 不改 `parse_units`：它的缓存键不含布局。归档改用 `addressing._file_units`，用"不丢内容"测试代替"边界和 CDF 一致"的说法。
    老分片经 `parse_units` 只剩 6/18 块，已实测。
  - 前提重写：直接 grep 上下文文件大多是调试已注入的文件，不再作为理由；Read / Grep 本来就有用量观察者。
  - 用量：说明归档和非 CDF 文件不记，以及原因。
  - 兼容：单库输出逐字不变，提示里用被调用的名字；兼容测试改成对照 main 上录下的输出（r1 的"两个名字输出相同"测不出东西）。
  - 补齐 R27 消费方：`repo_guidance`、`task_spec`、`verify_build`、多处文档字符串。新增 `STORES` 与 `DOMAINS` 的对应测试。
  - 更正：Library 的 id 就是 `unit_id`；`get` 不支持 Library 的理由改为"没有单元表"。
