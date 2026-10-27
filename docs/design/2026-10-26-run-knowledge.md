---
title: Pipeline run 怎么用上 DDD 和通用 Knowledge
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r13 — 步骤 0–7、§3.5、§3.6.9 已实施；聊天召回试了三种做法都不合入（§3.6.10、§3.6.11）；§5 的度量待 S5
---

相关设计：`2026-10-26-pipeline-two-ends.md`、`2026-10-25-knowledge-delivery-gaps.md`、
`2026-10-24-swarmai-usable-by-anyone.md`。

# Pipeline run 怎么用上 DDD 和通用 Knowledge

## 0. 主旨

Pipeline 的核心是 DDD 驱动的自主交付（two-ends 设计 §0）。但现在一个 run 从头到尾几乎不读 DDD：
前端（EVALUATE / THINK / PLAN）的卡片不提 DDD，审查专家只拿到仓库的 `.swarmai/review.md`，
只有 REFLECT 往 DDD 里写。本设计让 run 在两头**确定性地**拿到相关知识，召回全部复用平台的
`core/recall_multi.py`，不新造召回。

规则（XG 2026-10-26）：

- 仓库绑定了 DDD 项目：针对性召回 **DDD + 通用 Knowledge/Library**。
- 没有绑定：只召回**通用 Knowledge/Library**。
- 这个仓库的 `Knowledge/Library/pipeline-lessons/<仓库>.md`（knowledge-delivery-gaps G1）两种情况都读。

按 two-ends §0.1 的四个问题：本设计只改前端和后端；不规定 agent 怎么写、怎么测；复用现有召回；
给了哪些知识、谁引用了，都记在事件里，可以核对。

## 1. 现状（2026-10-26 核对代码）

| 途径 | 读到什么 | 问题 |
|---|---|---|
| 会话召回 `session_router._inject_ddd_for_active_project` | 按聊天消息关键词命中的 DDD 小节 | 和 run 无关；项目从消息猜；只有主 agent |
| `repo_guidance`（G2） | DDD 文档**路径** + "改代码前先读" | SwarmAI 的 DDD 有 2.1MB，读不完；读没读无记录 |
| REFLECT `reflect_job.candidates_for` | 去重候选 | 是写回，不是消费 |
| 专家 handout `review.patterns_block` | 仓库 `review.md` 里被触发的条目 | 没有 DDD |

另外发现的问题：

- **G-A 项目靠 `--project` 随手写。** create 不看 `bindings.yaml`。名字写错，run 就被当成没有 DDD，
  前端读不到，REFLECT 的经验写进 Library 而不是项目 DDD，都不报错。
- **G-B workspace 路径有多个来源。** pipeline 用 `SWARM_WORKSPACE` 或 `get_app_data_dir()/SwarmWS`
  （`cli_commands._workspace`、`hook_bridge`、`artifact_cli`）；召回和项目注册用 `SWARMWS` 或写死的
  `~/.swarm-ai/SwarmWS`（`project_registry.get_swarmws`）；另有约 15 处直接写
  `Path.home() / ".swarm-ai" / "SwarmWS"`（`session_unit`、`runtime_hooks`、`security_hooks`、
  `session_router`、`eval_service`、`jobs/handlers/*` 等）。生产上它们恰好指向同一个目录；测试 rig 和
  干净环境里，pipeline 用临时 workspace，召回却读真实的 DDD（REFLECT 的去重候选现在就是这样）。
- **G-C 旧的专家 DDD 召回成了死代码。** `skills/s_autonomous-pipeline/scripts/project_knowledge.py`
  和 `spawn_prompt.py`（10-17）按改动文件召回 DDD 条目给专家，有数据标签、三种状态、字数上限。
  v1 重写 handout 时没接进来，也没有记录原因；现在没有任何代码调用它们，但仍随包发出。

## 2. 复用的平台能力

| 能力 | 用在哪 |
|---|---|
| `recall_multi.recall_ddd_entries(query, project, top_n)` | DDD 条目**带正文**，过滤 superseded（实测 SwarmAI 8 条 54ms） |
| `recall_multi.recall_all(query, domains=("library",))` | 通用 Knowledge（FTS5 索引） |
| `recall_filter.filter_recall` 的相关性规则 | 过滤，pipeline 用自己的上限 |
| `recall_multi.EXTERNAL_SOURCE_PREFIXES` / `render_recall_body` | Signals 等外部内容单独成块标 UNTRUSTED |
| `reflect_library.find` | 直接读 pipeline-lessons 文件（不等索引，G5） |
| `repo_changes.bound_projects` | 仓库 → DDD 项目 |
| `session_router._extract_query_keywords` | 关键词提取；挪到 `recall_filter`，聊天和 pipeline 共用 |

不复用 `recall_all` 的 ddd 那一路：它返回小节指针，正文最多 1 条（实测 3 个指针、82 字符），
专家和前端都没法顺着指针去读 2MB 的文档。

## 3. 设计

### 3.0 召回的条目边界和截断：全系统一套规则（步骤 0，r2）

> **r15 起，本节的 R2（截到句末加"全文 N 字符"尾巴）、R3 中"放不下就截"和 R4 的单条上限
> `HIT_CHAR_CAP` 已作废**，由 §3.6.13 的"整条或点名"取代；R1（条目边界）和"排名不倒挂"保留。下文按 r2
> 时的实现记录，不按这里的旧说法实现。

这一步改的是平台召回本身。聊天注入、repo_guidance 和 pipeline 都用同一套规则，pipeline 不另写。

**实测问题（SwarmAI 的 DDD，2900 个条目）**

1. **条目边界切错了。** `recall_multi._ddd_entry_hits` 用 `re.split(r"(?m)^(?=- )")` 切条目，
   一个 bullet 会把后面所有不以 `- ` 开头的内容都吞进来，包括 `###` 小节、表格和段落。最长的
   一个"条目"有 16981 字符（TECH.md#Key Subsystems：83 行正文、19 行表格、2 个 `###`）。
   命中可能落在被吞进来的无关正文上，展示出来的却是那个 bullet。
   平台其实已经有一个读条目的函数：`ddd_entry_lifecycle._scan_entry`。按设计
   2026-10-25-memory-write-entry-dedup §3.1，它是"一个条目怎么读"的唯一定义，写入、去重和衰减
   都在用，只有召回没用。用它重新切：

   | 切法 | 中位数 | p90 | p99 | 最长 | >900 字符 | >2500 字符 |
   |---|---|---|---|---|---|---|
   | 现在的正则 | 388 | 799 | 3887 | 16981 | 252 | 52 |
   | `_scan_entry` | 370 | 654 | 2135 | 5479 | 180 | 10 |

   另外还有两处各自切条目，规则不一样：`recall_multi._memory_outside_prompt`（MEMORY 条目）
   和 `repo_guidance._entries`（pipeline-lessons）。

2. **截断会切在句子中间，而且不告诉读者原文在哪。** `recall_filter._truncate` 在字符上限处硬截，
   只有最后一个换行落在上限的 60% 以后才退到换行，然后加一个 `…`，不带出处。
   `_budget_one` 把排在后面的命中截到剩余预算，所以后面的条目常常只剩半句。

3. **（pipeline 设计 r1 自己的问题）** "放不下就跳过，后面的继续放"会让排名倒挂：实测排第 3 的
   条目（1364 字符）被跳过，排第 4、第 5 的放了进去；超过上限的条目永远出不来。

**统一规则**

- **R1 条目边界：只有一个读法。** 所有召回切条目都调 `_scan_entry`：`_ddd_entry_hits`、
  `_memory_outside_prompt`、`repo_guidance._entries`。条目就是 bullet 行、它的续行、
  嵌套的子条目和元数据行，遇到标题、column-0 的 `- `、空行就结束。refines 追加的子条目
  属于父条目。结构测试：召回代码里不许再出现按 `^- ` 切条目的正则。
- **R2 截断：截在句末，并带上出处。** `recall_filter` 只留一个 `clip(text, cap, pointer)`，
  `_truncate` 删掉。超过上限时，从开头截到上限内最后一个句末（`。！？.!?` 后接空白或行尾），
  没有句末就退到行末，两者都没有才截到最后一个空白。后面补
  `…（全文 N 字符：<出处>）`。出处：DDD 是 `doc#section`，Library 是源文件路径，
  session 是会话 id。DDD 条目通常第一句就是规则，截到句末不会丢掉这句。
- **R3 预算：按排名，不跳过。** 按排名依次放。下一条放不下完整内容时，用 R2 截到剩余预算；
  剩余预算连一句都放不下（少于 120 字符）时就停，不再跳过它去放后面的条目。
- **R4 单条上限和总上限是两件事。** 单条用 `HIT_CHAR_CAP`（900），总量用 `DOMAIN_CHAR_CAPS`
  等现有的值；pipeline 只是另一组总量上限，规则相同。中文一个字大约是一个 token，英文大约 4 个
  字符一个 token，所以字符上限只是粗略预算；先不做 token 计数。

**对聊天的影响**

- 注入的字数上限不变。内容会变：同样的预算里，以前的半句加 `…` 变成截到句末加出处，
  被错误合并的整节变成真正的条目。
- 开销：`_scan_entry` 是逐行扫描，和现在的正则同一量级。合入前用 `recall_suite`（固定语料）
  分别测 `recall_all` 的 ddd 那一路和 context_files 那一路，耗时变化要在 ±10% 以内。
- 召回质量：用 `recall_suite` 的 recall@K、MRR，加上 recall-precision-and-coverage 设计 §1 的
  回放样本，对比改动前后，不能变差。
- 这一步和 `2026-10-26-recall-precision-and-coverage.md`（r5，今天刚实施）改的是同两个文件。
  D1–D4 的过滤规则不动，本步只改条目边界和截断。动手前 rebase 到最新的 main。

### 3.0.1 步骤 0 的实施和实测（r3）

**和 §3.0 不一样的地方**
- R2 的回退顺序加了一级：句末 → 行末 → **分句号**（，；、：,;:）→ 空格。每一级都要保留至少一半的
  可用长度才采用。原因是中文没有空格，只按空格回退会截在词中间。
- R3 的停止线是 120 字符（`recall_filter.MIN_CLIP_ROOM`）：剩余预算不到 120 字符，而且下一条放不下
  完整内容时，这一路就停。
- R1 的实现是 `ddd_entry_lifecycle.split_entries(body)`，内部用 `_scan_entry`。召回、
  `_memory_outside_prompt`、`repo_guidance._entries` 都调它。
- pipeline-lessons 的预算也按 R3 走：最新一条放不下时截断它，并给"更早的经验没显示"那句提示留出位置。

**实测**：固定语料是 SwarmAI DDD 和 MEMORY.md 的一份拷贝；查询是 41 个，`recall_suite` 的 33 个
种子加 8 个日常问题。只测了 ddd 和 context_files 两路，没有碰 data.db，所以没测 Library 和 session。

| 指标 | 改前 | 改后 |
|---|---|---|
| `recall_suite` recall@5 / MRR（小节排名） | 0.485 / 0.379 | 0.485 / 0.379 |
| 条目命中数（前 5，41 个查询） | 199 | 197 |
| 条目命中里含 `#` 标题或表格（吞进来的内容） | 6 | 0 |
| 条目命中正文长度中位数 / 最长 | 344 / 6378 | 308 / 2239 |
| 注入块里被截断的命中：截在句子中间（`…` 结尾） | 35 | 0 |
| 注入块里被截断的命中：截在句末 / 分句号 / 行末，带出处 | 0 | 36 |
| ddd 那一路耗时中位数 | 64 ms | 53 ms |
| context_files 那一路耗时中位数 | 3.1 ms | 3.0 ms |

小节排名不受影响，这也符合预期：本步不改小节打分。条目层面，吞进来的内容没有了；截断全部落在句末、
分句号或行末，而且都带出处。耗时没有变慢，ddd 那一路快了约 17%，因为每个条目变短了。

**真实消息回放**（XG 同意后手动跑，`scripts/recall_replay.py`，data.db 只读）：最近 30 个会话
的 543 条用户消息，前后两边代码只差本步的两个提交（核对过召回相关文件在两个点之间没有别的改动）。

| 指标 | 改前 | 改后 |
|---|---|---|
| 触发召回的消息 | 206 | 206 |
| 注入块里被截断的命中 | 233，都只加 `…`，不带出处 | 237，都带"全文 N 字符：出处" |
| 其中截在句末或分句号 | 不区分 | 149（63%） |
| 其中截在行末或空格（不在词中间） | 不区分 | 88（37%） |
| Library 命中数 / session 命中数 | 350 / 172 | 355 / 173 |
| 注入字符数中位数 | 2216 | 2191 |
| 召回耗时中位数 | 146 ms | 138 ms |

有命中的消息里，166 条两边召回的来源完全相同，13 条不同：Library 8 条、session 7 条（有的消息两路都变了）。
差别主要来自 R3：预算不再把一条截成不到 60 字符的残句、再因为残句停下，所以后面放得下的条目能放进来
（Library 多 5 条）。逐条核对了来源减少的那一次（会话 a878efb9 第 28 条消息）：改前命中被记下但注入
0 字符（会话召回总预算 24000 已用完），改后这条直接没触发召回，模型看到的内容两边一样。触发召回的消息
有两条互换（一条改后不再触发，即上面这条；另一条改后多触发一次）。30 个会话注入总量：410710 → 405486 字符。

### 3.1 一个 workspace 解析函数（步骤 1）

`config.get_swarmws()` 是唯一来源，优先级：`SWARM_WORKSPACE` → `SWARMWS` → `get_app_data_dir() / "SwarmWS"`。

- 两个环境变量都没设、`SWARM_DATA_DIR` 也没设时，结果仍是 `~/.swarm-ai/SwarmWS`，生产不变。
  动手前核实 daemon（launchd plist）、Hive（systemd unit）、Tauri subprocess 各自设了哪个变量。
- `project_registry.get_swarmws`、`jobs.paths.SWARMWS`、`cli_commands._workspace`、`hook_bridge` 和
  §1 G-B 列出的写死路径都改为调它。
- 结构测试：产品代码里除了 `config.get_swarmws` 不许再出现 `"SwarmWS"` 路径拼接或读取
  `SWARMWS` / `SWARM_WORKSPACE`。用一个只许缩小的 KNOWN 集合起步，本步结束时为空。
  在子进程里单独运行的 skill 脚本走同一个函数；做不到 import 的，列进 KNOWN 并写明原因。

### 3.1.1 步骤 1 的实施（r4）

- `config.get_swarmws()` 是唯一的解析函数，按 `SWARM_WORKSPACE` → `SWARMWS` → `get_app_data_dir()/SwarmWS`
  的顺序取，每次调用都重新读，不冻结在模块常量里。三个都没设时仍是 `~/.swarm-ai/SwarmWS`，生产不变。
  核对过 launchd plist、Hive 的 systemd unit、Tauri subprocess：它们都不设这两个变量。
- 改了 30 个文件，约 30 处：`project_registry.get_swarmws` 委托给它；`swarm_workspace_manager` 的默认
  workspace、pipeline CLI、hook_bridge、artifact_cli、security_hooks 扫 run 的根目录（测试仍可钉
  `_ACTIVE_RUN_GLOB_ROOT`）、session_unit、runtime_hooks、session_router、eval、jobs、routers、Slack 都用它。
- 和设计不一样的两处：
  - `signal_digest` 原来还读一个 `SWARMWS_DIR` 变量（只有它和一个 skill 脚本读），已删。
  - 旧目录清理（`database/sqlite.py`）同时保留当前 workspace 和默认的 `<app data>/SwarmWS`：
    workspace 被重定位时，绝不删默认的那个。
- 结构测试 `tests/test_swarmws_resolver.py` 用 AST 检查产品代码（core、jobs、routers、hooks、channels、
  database、middleware、utils、main.py、artifact_cli）：不许再拼 `"SwarmWS"` 路径、不许把
  `~/.swarm-ai/SwarmWS` 当路径用、不许读这两个变量。例外列在 `ALLOWED`（4 处，各写原因，不再需要时测试会红）。
  `KNOWN` 为空。
- **范围外**：`backend/scripts/` 下手动运行的开发和维护脚本（约 20 个，如 recall_suite、eval_runner）、
  skill 脚本仍各自写死路径，它们不在产品运行路径上，本步不改。
- 改完后有两个旧测试变红，都是因为它们依赖真实 workspace：`test_runtime_hooks` 靠改 `Path.home` 移动
  MEMORY.md；`test_recall_shared_detection` 靠真实 workspace 里有 SwarmAI 项目才通过。两者都改成在
  sandbox workspace 里自己建。

### 3.2 项目由绑定确定（步骤 2）

create 对每个 `--repo` 取它的主 checkout，查 `bound_projects`：

| 情况 | 行为 |
|---|---|
| 所有仓库绑定同一个项目 P，`--project` 是 P 或没给 | run 归 P，`has_ddd = true` |
| `--project` 和绑定不一致 | 拒绝，报错里写出绑定的项目名 |
| 仓库绑定到不同的项目 | 拒绝（一个 run 只属于一个 DDD） |
| 仓库都没绑定 | `--project` 照旧必填，`has_ddd = false` |
| research（没有仓库） | `--project` 存在且有 DDD 文档时 `has_ddd = true`，否则 false |

`has_ddd` 和项目名写进 contract（不可改）。REFLECT 的路由读 contract，不再自己猜。

### 3.2.1 步骤 2 的实施（r5）

- 新模块 `core/run_protocol/project_binding.py`：`resolve(project, repos)` 先按 `repo_changes.bound_projects`
  查每个 `--repo` 的主 checkout（linked worktree 换算成它的主 checkout），再决定项目；create 在写任何东西之前
  调它。contract 新增 `has_ddd` 和 `bound_repos`，create 的输出带 `project`、`has_ddd`，有未绑定的仓库时带 `notes`。
- 和 §3.2 表格不一样的两处：
  - **`--project` 仍然必填。** `run_owner_gate` 要求每个会改状态的 pipeline 调用都带明确的 `--project`，
    create 也不例外；放开它要改 gate，不在本步范围。所以"没给 `--project`"那一行不存在，绑定的作用是
    "名字必须和绑定一致"。
  - **未绑定的仓库：`has_ddd` 看所命名项目有没有 DDD 文档，不是一律 false。** 一律 false 会和 REFLECT 冲突：
    REFLECT 按项目目录是否有 DDD 文档来写经验，项目有 DDD 时经验仍会写进去。两处用同一条规则。
    create 的 `notes` 会提示"这个仓库没绑定，去 bindings.yaml 绑上"。
  - 一个 run 里同时有绑定和未绑定的仓库：归绑定的那个项目，`notes` 列出未绑定的仓库。
- REFLECT 的路由没改：它本来就按 run 的项目写经验，项目由绑定确定后，经验就进了对的 DDD。
- INSTRUCTIONS 的 create 一节加了一句：`<P>` 是仓库绑定的项目。
- 测试 `tests/test_pipeline_v1_project_binding.py`（6 个，通过真实 CLI 和真实 bindings.yaml）在 main 上全红；
  四处变异（不比对名字、不拒绝两个项目、不换算 linked worktree、`has_ddd` 恒为 false）各自让对应测试变红。
  linked worktree 的测试第一版只看项目名，名字是 `--project` 传进去的，变异测不出来；已改成同时断言
  `bound_repos`。

### 3.3 前端：`artifacts/knowledge.md`（步骤 3）

create 时由 CLI 算一次，写进 run 的 artifacts（在 SwarmWS 里，**不进用户仓库**）：

- 查询：需求原文经 `_extract_query_keywords`。
- `has_ddd`：DDD 条目最多 6 条 + Library 最多 3 条 + pipeline-lessons 文件里最新的条目；否则 Library + pipeline-lessons。
- 截断和预算按 §3.0 的 R1–R4，和聊天召回同一套函数。pipeline 只是另一组总量上限：DDD 3000、
  Library 2000、pipeline-lessons 1000 字符，单条仍是 `HIT_CHAR_CAP`。截过的条目带"全文 N 字符：出处"，
  主 agent 需要时按出处读全文。
- 每条带出处（`doc#section` 或 Library 路径）和一个 8 位条目 hash。
- 外部来源（Signals 等）单独成块，标 UNTRUSTED；所有召回文本包在 `<recalled>` 数据标签里，
  条目里的同名标签先删掉（旧 `project_knowledge` 的做法）。
- 状态三选一，写在文件头：`recalled` / `none`（没有相关条目，或没有知识源）/ `unavailable`
  （召回出错，错误写进文件，不当成"没有知识"）。
- `create` 的事件记下状态和条目 hash 列表；EVALUATE 卡片加一句"先读 `knowledge.md`，它是线索，要对照源码核实"。

requirements 和 DESIGN_SPEC 加**可选**字段 `knowledge_refs`（条目 hash 列表）。写了的必须在
create 事件记下的列表里，否则 `stage-complete` 拒绝（和测试引用一样是确定性检查）。不强制写：
强制会逼出凑数的引用。

### 3.3.1 步骤 3 的实施（r6）

- 新模块 `core/run_protocol/run_knowledge.py`。create 时先由 `project_binding` 定项目，再调
  `build(--requirement 原文, …)` 召回一次：
  - `has_ddd` 时 `recall_ddd_entries`（最多 6 条，DDD 总量 3000）；
  - Library 走 `recall_all(domains=("library",))` 和聊天同一个 `filter_recall`（给 `filter_recall` 加了
    `caps` / `slots` 参数：Library 2000、最多 3 条，MEMORY 归档 600、最多 1 条）；
  - 这个仓库的 pipeline-lessons 文件直接读（"经验"一节，从新到旧，1000 字符）。
  三路都按 §3.0 的 `clip` 和预算规则截。每条带 8 位 id（全文去空白后的 sha256 前 8 位）。
- 状态和 id 列表写进 **contract**（`knowledge: {status, items}`），不是事件：contract 是 create 时写一次、
  agent 改不了的记录，用来核对 `knowledge_refs` 正合适，省掉一种新事件。`artifacts/knowledge.md` 是给人和
  agent 读的副本。create 的输出带 `knowledge: {file, status, entries}`。
- `knowledge_refs`：REQUIREMENTS（full）和 DESIGN_SPEC（两种都可）的可选字段，8 位 id 列表。schema 只查
  形状；engine 在 EVALUATE / PLAN 的 `stage-complete` 和 `gate0-start` 写文档之前，核对每个 id 都在 contract
  里，否则按 RQ-5 / DS-5 拒绝。
- **删了 `prior_knowledge`。** REQUIREMENTS 里原来有一个 `prior_knowledge: [{entry, store}]` 字段，自由文本、
  不核对、卡片和产品代码都没提过，只有测试 fixture 传空列表。它和 `knowledge_refs` 是同一件事，留着就是两个
  来源，所以由 `knowledge_refs` 取代。
- 关键词提取 `extract_query_keywords` 从 `session_router` 挪到 `recall_filter`（连同停用词表），聊天和
  pipeline 共用；`session_router` 保留原来的名字，转引用这一份。
- 和 §3.3 不一样的地方：
  - 外部抓取内容（Signals、DailyBriefs）不是"单独成块标 UNTRUSTED"，而是和聊天一样**直接不收**
    （`filter_recall` 的 `NO_INJECT_PREFIXES`）。渲染时仍会把 `is_external_source` 的条目单独成块，作为第二道。
  - 没有 data.db 时 Library 这一路直接跳过：召回会打开、也就会创建一个空的数据库。
  - INSTRUCTIONS 改为每个 profile 都传 `--requirement`；没传时状态是 `none`，原因写进文件。
- 实测（真实 SwarmAI DDD 和 Library，只读）：三个查询 152–180 ms，召回 6 条 DDD 加 0–3 条 Library，
  knowledge.md 3–5.5K 字符。create 是 CLI 里的一次性调用，不在聊天路径上。
- 测试 `tests/test_pipeline_v1_run_knowledge.py`（9 个）：三种状态、没有 DDD 时不调 DDD、栅栏不能被
  召回文本提前关闭、长条目截断带出处、真实 CLI 写出 knowledge.md 和 contract、`knowledge_refs` 的核对。
  五处变异（不核对 refs、无视 has_ddd、出错算 none、不清栅栏、不判断 data.db）各自让测试变红。判断 data.db
  的那一处，端到端测试测不出来（rig 的 CLI 子进程不会建库），改成直接测 `_library_leg`。
- INSTRUCTIONS 有 10000 字符上限（`lint_skills`），步骤 2、3 加的说明一起压缩到上限以内。

### 3.4 后端：correctness 专家的 DDD 块（步骤 4）

只在 `has_ddd` 时有，只给 correctness 专家（security 有自己的声明来源，先不加）。

- review-start 时由 CLI 算：查询 = 改动文件的路径词（文件名第一个点之前的部分 + 两级父目录，
  去掉 `src`、`tests` 这类结构词，沿用旧 `project_knowledge.derive_query`）+ 需求关键词。
- 只召回 DDD（`recall_ddd_entries`），**不给 Library**：个人笔记和外部内容和审这段代码关系不大，
  还多一个注入入口。
- 最多 5 条、总量 2500 字符，截断按 §3.0 的 R1–R4；数据标签，三种状态，嵌在 handout 里。不占专家
  24 次调用的任何一次；专家要看某条全文时，按出处读，多用 1 次调用。
- handout 事件记下条目 hash。expert finding 可以带 `knowledge_ref`；不在这个 handout 列表里的引用
  按无效处理（不影响 finding 本身）。
- 增量审查和 R2 用同一份块（按当次 diff 重算）。
- 删除 `project_knowledge.py`、`spawn_prompt.py` 和它们的测试；`security_hooks.py:5896` 那句注释同步。

这改掉了 knowledge-delivery-gaps 里 G3 "等 S5 有数据再看"的结论：不给专家，就永远量不到。
留不留由 §5 的数据决定。

### 3.4.1 步骤 4 的实施（r7）

- `run_knowledge.expert_block(project, has_ddd, paths, requirement)`：只召回 DDD，查询词 = 改动文件的
  路径词（`path_terms`，沿用旧 `project_knowledge.derive_query` 的规则和结构词表）+ 需求关键词。
  最多 5 条、共 2500 字符，按 §3.0 截断；和前端 `knowledge.md` 共用 `_ddd_rows`。
  三种状态都写进块里：recalled（带 id 的条目）、none（"DDD 里没有这些文件的条目"）、unavailable
  （"召回失败，不带它审"）。项目没有 DDD 时整块不出现。
- 只给 correctness 专家：第一轮审查和增量审查都加；R2 不加（R2 判断的是"修复是否被证明"，DDD 条目
  帮不上）。块放在 review.md 模式之前。
- handout 事件新增可选字段 `knowledge_items`（块里条目的 id）。finding 行可以带 `"knowledge_ref":"<id>"`：
  解析器接受它（格式不对就丢掉这个引用，finding 照样算），hook 只在 id 属于该 handout 的
  `knowledge_items` 时把它写进 `finding_occ`。OUTPUT CONTRACT 不变，说明写在块里，没有 DDD 的专家
  prompt 一个字不变。
- 删了 `scripts/project_knowledge.py`、`scripts/spawn_prompt.py` 和它们的两个测试文件（其中
  `test_spawn_prompt_emitter` 在 main 上本来就是红的），`spawn_budget.py` 里只为它们服务的
  `KNOWLEDGE_SLOT` / `requires_knowledge` 也删了。`data/gate-prompts/` 还在，Gate-0 在用。
- 实测（真实 SwarmAI DDD，只读）：三组改动 43–127 ms，召回 4–5 条，块 2.7–3.1K 字符。
- 测试 `tests/test_pipeline_v1_expert_knowledge.py`（7 个，含真实 CLI 走到 REVIEW）。五处变异（不加块、
  任何引用都收、解析器拒收引用字段、无视 has_ddd、不清栅栏）各自让测试变红。没有 DDD 的测试第一版只查
  `knowledge_items`，无视 has_ddd 的变异测不出来（块里写的是"没有条目"，不带 id）；已改成同时检查
  prompt 里没有这个块。

### 3.5 REPORT 和 REFLECT

- REPORT 只加一行：`Knowledge: <状态> · N items · cited M`（M = 需求、设计、finding 引用到的条目数）。
  **不**列召回内容和 Library 路径：REPORT 会提交进用户仓库。完整列表在 `run_audit.json`。
- REFLECT 的去重候选改为读 `knowledge.md` 里的 DDD 条目加原有的按经验召回，合并去重。
  `reinforces` / `refines` 自然就能指向这次真正用过的条目。

### 3.5.1 §3.5 的实施（r8）

- **REPORT 一行**：`Knowledge: <状态> · N entries · cited M`，放在审计表下面。N 是 knowledge.md 的条目数；
  M 是被引用的不同条目数，来源有两处：REQUIREMENTS / DESIGN_SPEC 的 `knowledge_refs`（只算 contract
  里有的 id），以及 finding 的 `knowledge_ref`（hook 写入时已核对过属于那次 handout）。完整的 id 列表在
  `run_audit.json` 的 `details.knowledge`。REPORT 不含任何召回正文。步骤 3 之前建的 run 没有这一行。
- **REFLECT 候选**：`candidates_for` 合并两路，去重：
  1. create 时召回进 knowledge.md 的 DDD 条目。从 **contract** 读（新增 `knowledge.ddd: [{id, source, head}]`），
     不读 agent 能改的 knowledge.md，所以仍然满足 RF-4"候选由 CLI 算、agent 选不了"；
  2. 原有的、按经验原文的关键词召回。
- **顺带修了一个已有的 bug**：原来的候选写成 `doc#section: - **标题**…`，带着条目开头的 `- `；
  `annotate_entry` 按"去掉 `- ` 之后的文本"去找条目，所以永远找不到，`reinforces` / `refines` 全部变成
  `entry_missing`。run_bb7b1850 的两条 refines 就是这样丢的。现在候选统一用 `entry_head`
  （条目第一行、不带 bullet），和 `annotate_entry` 的找法一致。旧的测试都是手写的候选字符串，没有经过
  `candidates_for`，所以没发现。
- 测试 `tests/test_pipeline_v1_knowledge_report.py`（6 个），其中一个端到端：真实 CLI 建 run → REQUIREMENTS
  引用条目 → REFLECT 用一条和条目没有共同词的经验去 reinforce（只有 knowledge 候选能提供它）→ 落地 →
  REPORT 里有 `cited 1` 且没有召回正文 → DDD 条目下多了一行 reinforced 注记。四处变异（候选带回 bullet、
  不传 knowledge 候选、不渲染这一行、不统计 knowledge_refs）各自让测试变红。

### 3.6 DDD 按用途召回（r9，步骤 5–8）

#### 3.6.1 问题（2026-10-26 实测，run_85e81059）

步骤 3 在 create 时只召回一次，把 4 份 DDD 放进同一个 BM25 语料一起排名。这次 run 是需求分析
（Eval overlay 放行判定），knowledge.md 里 6 条 DDD 全部来自 TECH 和 IMPROVEMENT，PRODUCT 和
PROJECT 一条都没有。三个原因：

1. **数量压倒相关性。** TECH + IMPROVEMENT 共 3101 条，占 4 份文档条目总数的 89%。单独在 PROJECT.md
   里查同一串查询，第一名就是 `[decision] eval OS needs a 'live'/non-gate tier for deterministic golden
   cases ... excluded from the gate tiers`，正好是这次"档位"要参考的决定，但它在混合排名里没进前 9。
2. **PRODUCT 主要是成段文字和表格。** Vision、Success Criteria、Non-Goals 不是 `- ` 条目，按条目
   召回看不见；单独查 PRODUCT 的前几条都是 Design Philosophy 里不相关的原则。
3. **DDD 的结构没用上。** 文档分工、条目类型（`[decision]` / `[pitfall]` / ...）、`- [x]` 已完成
   标记，召回都当普通文字处理。PROJECT 里 53 条 `- [x]` 照样参与排名。

另外 THINK 卡片完全没提知识，PLAN 卡片只提了 `knowledge_refs` 字段。THINK / PLAN 需要的是
TECH / IMPROVEMENT，而且该用 EVALUATE 定下的需求去查，不该只用用户原话。

结论：DDD 召回需要按 DDD 的结构单独定制。这是平台召回的能力，pipeline 各阶段和聊天都用它，不在
pipeline 里另写一套。

#### 3.6.2 平台入口：`recall_multi.recall_ddd(query, project, purpose)`

一个函数，按 `purpose` 取一组配置。配置是数据（模块里一张表），不是分支代码：

| purpose | 文档和名额（每份单独排名） | 类型加权 | 每次都带（和查询无关） |
|---|---|---|---|
| `requirement`（EVALUATE） | PRODUCT 按 `##` 小节召回 2 节 + PROJECT 按条目召回 4 条 | decision、principle | PRODUCT 的 Vision、Success Criteria、Target Users 全文；Non-Goals 只给小节标题和出处 |
| `design`（THINK / PLAN） | TECH 4 条 + IMPROVEMENT 3 条 | guideline、pitfall、risk、decision | 无 |
| `review`（correctness 专家） | TECH 3 条 + IMPROVEMENT 2 条，用改动的文件路径查 | pitfall | 无 |
| `chat`（聊天注入，§3.6.4） | 4 份各自排名，各给名额 | 不加权 | 无 |

所有配置共用的规则：

- **每份文档单独排名，按名额取。** 不再让 4 份文档在一个语料里比分数。一份文档名额没用完，不让给
  别的文档，避免"数量压倒相关性"从后门回来。
- **`- [x]` 条目不召回。** 已完成的事项对判断没有用。
- **类型加权是软加权**，乘一个系数，不过滤没写类型的条目。TECH / IMPROVEMENT 约六成条目没写类型，
  过滤会丢掉大半内容。系数由 §3.6.6 的样例集定，不拍脑袋。
- **每次都带的小节按 DDD 模板（`templates/ddd/PRODUCT.md`）的小节名匹配。** 项目里有就带，没有或改了
  名就跳过，不写死 SwarmAI 的小节。总量有上限，计入这一路的预算。
- 切条目、截断、按排名放不跳过，沿用 §3.0（`split_entries`、`clip`、R3）。
- 文档集合取自 `DDD_CANONICAL_DOCS`，小节名只出现在配置表里。

`recall_ddd_entries` 在步骤 7 把调用方都换成 `recall_ddd` 之后，没有其他调用方就删掉，不留两个入口。

#### 3.6.3 pipeline 接入

| 阶段 | 文件 | 内容 | 什么时候算 |
|---|---|---|---|
| EVALUATE | `artifacts/knowledge.md`（不变） | `recall_ddd(原话, purpose=requirement)` + Library | create |
| THINK / PLAN | `artifacts/knowledge_design.md`（新） | `recall_ddd(原话 + REQUIREMENTS 正文, purpose=design)` + 这个仓库的 pipeline-lessons | EVALUATE 的 `stage-complete` 写入 REQUIREMENTS 之后，由 CLI 算 |
| REVIEW | handout 里的块（不变） | `recall_ddd(路径词 + 需求关键词, purpose=review)` | review-start |

- pipeline-lessons 从 knowledge.md 移到 knowledge_design.md：它记的是这个仓库的编码坑，属于设计。
- **没有 EVALUATE 的 profile**：direct 在 create 时直接按 `design` 生成 knowledge.md（它马上进 BUILD）。
  trivial 没有 THINK，EVALUATE 完成时照样生成 knowledge_design.md 给 PLAN 用。
- **id 的登记**：knowledge.md 的 id 仍在 contract 里。knowledge_design.md 的 id 写进一条 CLI 事件
  `knowledge_recalled`（字段：stage、ids、heads、status），agent 写不了。`knowledge_refs` 的核对改为认
  这两处的并集；REFLECT 的去重候选也取并集，仍然不读 agent 能改的文件（RF-4 不变）。
- **卡片**：THINK 和 PLAN 卡片加一句"先读 `artifacts/knowledge_design.md`"。EVALUATE 之后 `next` 给的
  说明里带上这个路径。`knowledge_refs` 仍然可选。
- REPORT 那一行改成：`Knowledge: requirement <状态> · N · design <状态> · M · cited K`。

#### 3.6.4 聊天

聊天 `_inject_ddd_for_active_project` 改用 `recall_ddd(..., purpose="chat")`。聊天的问题五花八门，改了
不一定更好，所以单独一步（步骤 8）。合入前用真实消息回放（`scripts/recall_replay.py`，要 XG 同意读
data.db）量三个数：注入条目里 PRODUCT / PROJECT 的占比、注入字符数、耗时。注入字符数或耗时上升超过
10% 就不合入，只记录结果。

#### 3.6.5 硬性要求：没有 DDD、DDD 不完整或损坏时都不能 crash

现状（2026-10-26 实测）：没有项目、项目名不存在、文档里没有条目、`has_ddd=false` 都正常返回空。
但**一份 DDD 文档不是 UTF-8 时，`_load_ddd_docs_text` 抛 `UnicodeDecodeError`**（它只捕获 `OSError`），
整个项目的 DDD 召回全部失效。pipeline、聊天、REFLECT 的调用方各自兜住了，所以没有 crash，但一份坏
文档会拖垮另外三份，和加载器注释里"读不了的文档当作不存在"不一致。

要求：

- **H1 加载器只跳过坏的那一份。** 编码错误、权限、读到一半出错，都跳过这份文档并记一行 warning，
  其他文档照常召回。
- **H2 配置需要的文档不存在时，这一路返回空。** 不报错，也不改去查别的文档。"一份都没有"和
  "缺几份"走同一条规则。
- **H3 每个调用方都不抛异常。** `recall_ddd` 对单份文档的解析错误做局部兜底；pipeline 的 knowledge.md、
  knowledge_design.md、专家块、REFLECT 候选，以及聊天注入，各自再有一层兜底。失败记成 `unavailable`
  并写明原因，不能当成 `none`，也不能是空白文件。
- **H4 没有 DDD 的 run 不少任何东西。** Library 和 pipeline-lessons 两路照常召回，REFLECT 照常把经验
  写进 Library，REPORT 照常生成。
- **H5 测试矩阵**（先写；标"main 上红"的那格在 main 上必须是红的）：

  | 情况 | 期望 |
  |---|---|
  | 没有项目 / 项目名不存在 | 状态 `none`，不调 DDD |
  | 项目目录存在但没有 DDD 文档 | `has_ddd=false`，状态 `none` |
  | 只有 TECH.md（缺 PRODUCT / PROJECT） | requirement 那一份 `none`，design 照常 |
  | 只有 PRODUCT.md（缺 TECH / IMPROVEMENT） | requirement 照常，design 和专家块 `none` |
  | 一份文档不是 UTF-8 | 其他文档照常召回（main 上红） |
  | 一份文档里只有标题没有条目 | 这一份为空，不报错 |
  | `recall_ddd` 本身抛异常（打桩） | 状态 `unavailable`，run 照常往下走 |

  每一行都用真实 CLI 把 full、bugfix、direct 三个 profile 跑到落地，检查：run 走完；knowledge 文件的
  状态正确且非空白；REPORT 生成。聊天这边同样几种情况：回复照常发出，注入记录写明原因。

#### 3.6.6 怎么量

两层：

1. **确定性测试**（随代码发布，不依赖任何人的 DDD）：fixture DDD 里埋好"该在 requirement 里出现的
   PROJECT 决定"、"已完成的 `- [x]`"、"每次都带的模板小节"，逐条检查配置表的规则。
2. **样例集**（只在 XG 机器上跑，不随包发布）：`backend/scripts/ddd_recall_eval.py --cases <file>`，只读
   SwarmAI 的真实 DDD。样例文件放在 `Projects/SwarmAI/.artifacts/ddd_recall_cases.json`，每条是
   "一条需求 + 一个 purpose + 应该召回的条目第一行"。从最近 15 个真实 run 的需求里标 15–20 条，
   run_85e81059 那条 live-tier 决定是第一条。

   合入标准：
   - 每个 purpose 的期望条目命中数不少于现在；
   - requirement 里 PRODUCT / PROJECT 的期望条目至少过半命中（现在是 0）；
   - 每次召回耗时增加不超过 30%（每份文档单独排名会多几次打分）。

   类型加权的系数在这组样例上从 1.0 / 1.2 / 1.5 里选命中最好的；分不出差别就取 1.0，也就是不加权。

#### 3.6.7 步骤 5、6 的实施和实测（r10）

**步骤 5**（`b88de6020`）：`_load_ddd_docs_text` 跳过读不了的那一份（`OSError`、`UnicodeDecodeError`），
记一行 warning。矩阵 `tests/test_ddd_absent_or_broken.py` 38 个测试：在 main 上只有非 UTF-8 那一种状态的
7 个红；把捕获改回只有 `OSError`，同样 7 个变红。

**步骤 6**：`recall_multi.recall_ddd` + `DDD_PURPOSES`，`tests/test_recall_ddd_purpose.py`（9 个），
`scripts/ddd_recall_eval.py`，样例集 `Projects/SwarmAI/.artifacts/ddd_recall_cases.json`（16 条：7 个真实
run 的需求 + 9 条常见任务）。

和 §3.6.2 不同的地方：

- **小节匹配按前缀**：模板的小节名后面常带后缀（`Design Philosophy — When Beliefs ...`），按"名字开头"匹配。
- **`clipped` 去掉元数据注释**（`<!-- maturity ... -->`、`<!-- ref:0 ... -->`）再算预算；`content` 保留原文，
  id 仍然取自原文。实测 PRODUCT 的固定小节原来一半预算花在注释上。
- **review 名额 TECH 从 3 改成 4，加权类型加上 decision、guideline**：样例集里 `nav-pool-review` 的期望
  条目在 TECH 里单独排第 4，名额 3 时漏掉；改了以后两条都命中。
- **`TYPE_WEIGHT` 取 1.5**：design 那一组 1.0 / 1.2 是 7/11，1.5 是 8/11；其余两组三个系数一样。

样例集结果（只读 SwarmAI 的真实 DDD；before = 原来 4 份混排的 `recall_ddd_entries`，top 6，review top 5）：

| purpose | before | after（1.5） |
|---|---|---|
| requirement（全部是 PRODUCT / PROJECT 的期望条目） | 4/7 | 7/7 |
| design | 7/11 | 8/11 |
| review | 2/2 | 2/2 |
| 召回耗时中位数 | 46.8 ms | 38.8 ms |

§3.6.6 的三条合入标准都满足：没有一组变差；requirement 里 PRODUCT / PROJECT 的期望条目从 0 提高到全部
命中（run_85e81059 那条 live-tier 决定也在里面）；耗时下降。

要说清楚的：

- **样例只有 16 条，而且名额和系数是在同一组样例上调的**，有过拟合的风险。以后每多一个真实 run，就把它
  的需求补进样例集再跑一次，数字变差就回来调。
- design 还有 3 条漏掉：`run_3fad89b4` 的两条（需求是英文，DDD 条目是中英混写的 "run-commit git-adds"，
  关键词对不上）和 `approve-into-void`（在 IMPROVEMENT 里单独排第 10，名额 3）。这是关键词召回的限制，
  名额加大会把噪音也带进来，这一步不改。

#### 3.6.8 步骤 7 的实施（r11）

pipeline 分阶段接入 `recall_ddd`：

- **knowledge.md**（create）：`recall_ddd(purpose="requirement")` + Library。pipeline-lessons 移到 design 那一份。
  direct 没有 EVALUATE，create 时直接按 design 召回，contract 里记 `purpose: design`。
- **knowledge_design.md**（EVALUATE 的 `stage-complete` 通过后）：`run_knowledge.build(purpose="design")`，
  查询词是用户原话的前 10 个词，加上 REQUIREMENTS 正文里新出现的词（`query_for`，最多 18 个，原话的词
  永远在前）。id 写进 CLI 事件 `knowledge_recalled`（每个 purpose 只有一条，EVALUATE 重做也不会替换；
  lifecycle 忽略它）。召回出错时文件和事件都写 `unavailable` 和原因，EVALUATE 照常完成。`stage-complete`
  的输出多一个 `knowledge_design`（文件、状态、条数）。
- **专家块**：`recall_ddd(purpose="review")`。
- **`knowledge_refs`** 认 contract 和 `knowledge_recalled` 事件的并集；**REFLECT 去重候选**同样取两处的
  DDD 条目，另一路改用新 purpose `reflect`（4 份文档每份 1 条，只取条目）。
- **REPORT**：`Knowledge: requirement recalled · 11 · design recalled · 4 · cited 1`。
- **卡片**：EVALUATE 说 knowledge.md 里是 PRODUCT / PROJECT 和 Library；THINK、PLAN 先读 knowledge_design.md。
  `next` 在阶段切换时本来就带卡片全文，所以没有另改 `next`。INSTRUCTIONS 改后 9924 字符。
- **删掉 `recall_ddd_entries`**：调用方都换成了 `recall_ddd`；样例集脚本的 before 改为直接用一个混合语料
  排名，效果和原来相同。

实测（只读 SwarmAI 的真实 DDD，run_85e81059 的需求）：knowledge.md 11 条（PRODUCT 5、PROJECT 3、Library 3），
83 ms；knowledge_design.md 4 条（TECH 2、IMPROVEMENT 2），108 ms。两次召回都在 CLI 里，不在聊天路径上。

验证：

- 新增和改写的测试：requirement / design 各自读哪些来源、查询词顺序、EVALUATE 写出 knowledge_design.md 和
  事件、PLAN 能引用 design 条目、direct 在 create 时拿到 design、design 召回出错仍能完成 EVALUATE、REPORT
  两段、REFLECT 能 reinforce 只在 design 那一份里出现的 TECH 条目。
- §3.6.5 的不 crash 矩阵改为按 purpose 断言：full、bugfix 还要检查 knowledge_design.md 和事件的状态。
- 8 处变异检查都让测试变红：requirement 改读 design、EVALUATE 不召回、`knowledge_refs` 只认 contract、
  REFLECT 候选只取 contract、direct 改用 requirement、REPORT 不分两段、design 召回出错时抛出、专家块不用
  review。

#### 3.6.9 PRODUCT 按段落召回（r12）

问题：步骤 6 让 PRODUCT 按 `##` 小节召回，但 SwarmAI 的 PRODUCT 有几个大杂烩小节（Design Philosophy 44K
字符、Competitive Positioning 17K），几乎任何查询都能匹配上，一旦排到前面就把整节截到 900 字，读的人
看到的是小节开头，不是匹配上的那一段。

改法：新增 `ddd_entry_lifecycle.split_blocks(body)`，把一节切成召回单位：每个 `- ` 条目（和
`split_entries` 读法一样）是一个单位，条目之间的文字按空行切成段落、表格、`###` 小标题块。新 mode
`block`：一份文档内这些单位放在一个语料里排名，已完成的 `[x]`、superseded 条目、只有标题和注释的块
不参与；条目按类型加权。requirement 里 PRODUCT 改为 `("block", 3, 1800)`。

样例集脚本改了计分口径：期望条目只有出现在**读的人看得到的文本**里才算命中（after 用 `clipped`，
before 用每条截到单条上限后的文本）。原来按完整原文算，一个 44K 的小节因为深处有匹配就算命中，但这段
在截断后根本看不到。按新口径，main 上（按小节）和改后（按段落）requirement 都是 6/7，design 8/11、
review 2/2 不变；`memory-provider-req` 的期望条目在两边都看不到（main 是被截掉，改后是 Memory
sovereignty 那张表排在第 11）。召回耗时中位数 41.5 ms。

测试：`test_product_is_recalled_by_paragraph_not_by_section`，命中的是匹配的那一段，不是它所在的整节；
把 `split_blocks` 换回整节，这个测试变红。

#### 3.6.10 步骤 8：聊天改用 `recall_ddd` 的回放结果（r12，未合入）

试过的改法：`_inject_ddd_for_active_project` 里 4 份 canonical 文档改由 `recall_ddd(purpose="chat")` 给出
（每份文档 2 个单位，`block` mode），ddd 桶里其他来源（项目的 Knowledge/、数据契约、code-intel 领域、
spec 块）保持不变，然后照旧做覆盖率过滤，注入小节指针。

回放：最近 30 个会话的 519 条真实消息，data.db 只读，同一批消息在 main 和改后各跑一遍。

| | main | 改后 |
|---|---|---|
| 有 DDD 块的消息 | 21 | 18 |
| 其中只有项目指针、没有小节 | 0 | 5 |
| 有小节的消息 | 21 | 13 |
| 每个有小节的块里的小节数（平均） | 1.57 | 2.54 |
| 小节来自哪份文档 | TECH 16 · PROJECT 7 · IMPROVEMENT 4 · PRODUCT 4 | TECH 9 · PRODUCT 9 · IMPROVEMENT 8 · PROJECT 5 |
| DDD 块合计字符 | 5431 | 5284 |
| 召回耗时中位数 / p90 | 167 / 445 ms | 146 / 427 ms |

§3.6.4 写的合入标准（注入字符数和耗时上升都不超过 10%）满足，但看逐条变化，质量是好坏参半：

- 变好的：调研 talkstream 包装成 App 的消息拿到了 PRODUCT 的定位小节；PRODUCT 的出现次数从 4 到 9。
- 变差的：8 条消息原来有小节、改后只剩指针或什么都没有，例如"给 SwarmAI 加 Hive - Always on 的固定 Tab"
  原来指向 TECH 的 Key Subsystems，改后没有。原因是覆盖率过滤原来作用在整节文字上，改后作用在一个
  段落或条目上，单位小了，能覆盖到的查询词少，被过滤掉的多。
- 变吵的："修正 SELF.md 和 STEERING 里过时的 gate 清单"一条消息改后注入了 7 个小节指针。

结论：不合入。成本标准没有说明质量，而质量证据是混合的；聊天注入每条消息都会用到，不能用一组好坏
参半的回放去换。`DDD_PURPOSES["chat"]` 留在配置表里（测试用它验证按文档分名额），但聊天没有接它。
要再做的话，先解决覆盖率过滤和小单位不匹配的问题（比如按小节聚合各单位再过滤），并用回放加人工判读
一起看质量，不只看字数和耗时。

#### 3.6.11 聊天召回重做：先按小节聚合再过滤（r13，未合入）

在 §3.6.10 的基础上试了两种"先聚合到小节、再做覆盖率过滤"的做法。两种都一样：4 份 canonical 文档由
`recall_ddd(purpose="chat")` 给出候选单位，按所属小节聚合；ddd 桶里其他来源照旧；每轮最多 3 个小节
（和 `recall_all` 的 `max_sections` 一致）。区别在覆盖率按什么文字算：

- **A. 按整节文字算**（和 main 一样），候选小节按覆盖率、再按单位得分排序。
- **B. 按召回到的那些单位拼起来算**，候选按单位得分排序；每份文档的名额从 2 提到 4。

同一批 519 条真实消息（30 个会话，data.db 只读）：

| | main | §3.6.10 | A | B |
|---|---|---|---|---|
| 有小节的消息 | 21 | 13 | 21 | 14 |
| 只有项目指针 | 0 | 5 | 0 | 4 |
| 每块最多 / 平均小节数 | 3 / 1.57 | 7 / 2.54 | 3 / 2.90 | 3 / 2.36 |
| 小节来自 | TECH 16 · PROJECT 7 · PRODUCT 4 · IMPROVEMENT 4 | TECH 9 · PRODUCT 9 · IMPROVEMENT 8 · PROJECT 5 | IMPROVEMENT 22 · TECH 19 · PROJECT 10 · PRODUCT 8 | IMPROVEMENT 10 · TECH 9 · PRODUCT 9 · PROJECT 3 |
| DDD 块合计字符 | 5431 | 5284 | 6433 | 5182 |
| 召回耗时中位数 / p90 | 167 / 445 ms | 146 / 427 ms | 198 / 557 ms | 184 / 602 ms |

逐条看 A：有小节的消息数回到了 21，但新增的指针大多是几个又大又泛的小节——IMPROVEMENT 的 What Failed、
TECH 的 Key Subsystems、PROJECT 的 Recent Decisions，几乎每条消息都带上（"你 dive deep 下我现在感觉我们很慢"
从 TECH 的 Output Format Protocol 变成 Key Subsystems + What Failed + Recent Decisions）。原因：按整节算覆盖率，
几十万字的小节对任何消息都能过线，每份文档单独排名又让它们各自都有名额。B 回到了 §3.6.10 的问题：单位
小，覆盖的词少，过线的少。两种耗时都上升超过 10%（A +19%，B +10% 中位数、+35% p90）。

结论：两种都不合入，聊天保持 main 的做法。

根因判断：聊天注入的是**小节指针**（`- **TECH.md** § Key Subsystems`），不是内容。在 SwarmAI 这种单份文档
几十万字、小节很大的 DDD 里，一个小节指针本身信息很少，读的人不可能去读整个 What Failed。所以怎么排小节、
怎么过滤，都改变不了"注入的东西有没有用"。真正的杠杆是让聊天注入**条目内容**（和 pipeline 一样，带出处、
有字数上限），但这改变了聊天每轮的注入内容和成本，是另一份设计，需要 XG 决定要不要做。

#### 3.6.12 整条取舍，不截断（r14，按 CDF 第一准则）

CDF 设计（`2026-10-26-swarm-cognitive-distillation-flow.md` §5）的第一准则：注入和召回只能以整条单元为单位
取舍，被舍弃的单元要计数、能找回，人和 AI 都要读得懂。§3.0 的 R2（截到句末）和 R3（按排名截剩余预算）
在 `recall_ddd` 这一路改为：

- **整条或不放。** 按排名依次放整条单元（去掉元数据注释后），下一条放不下就停在这里，**不跳过它去放后面
  的**（排名不倒挂，R3 的那一半保留）。停下的那条和排在它后面的，合成一行 `omitted`：逐条写出位置
  （`doc#section`）、作者自己的加粗标题（整条，不超过 200 字符才写）和长度。这是 CDF §5.11 允许的定位字段，
  不是正文的缩略。
- **预算按"整条放得下"重定**：SwarmAI DDD 的 2931 个条目里，中位数 302、p90 614、p99 2104 字符，超过 4500
  的只有 1 条。requirement PRODUCT 4000 / PROJECT 3000，design TECH 4500 / IMPROVEMENT 3000，review TECH 3500 /
  IMPROVEMENT 2500。pinned 小节整节显示，超过 1500 字符的只写出处；pinned 不占排名单元的预算，几个模板小节
  再长也挤不掉和请求相关的单元。
- **omitted 行不是条目**：它没有 id，不进合约、`knowledge_recalled` 事件和专家块的 id 列表，不计入
  "N entries"，不能被 `knowledge_refs` / `knowledge_ref` 引用；knowledge.md 和专家块里显示为 `not shown`。
- **单行引导句不是单元**：一行、不到 200 字符、以冒号结尾（"三条信念……："）的块单独看不出在说什么，不作为
  候选；它引出的单元各自完整。多行段落即使以冒号结尾也照常是单元。
- **REFLECT 四份文档一起排名**（`pooled`，前 6 条）。去重要找的是全库里最像的几条；每份文档各取第 1 名时，
  一条真重复在 TECH 里排第 3，没进候选，同时每份文档都塞进一条无关的 1.00 分候选。
- 字段 `clipped` 改名为 `shown`（`run_knowledge._ddd_rows`、样例集脚本同步）。Library 和 lessons 两路在
  r15 一并改为整条取舍（§3.6.13）。

实测（16 条样例，SwarmAI 真实 DDD，只读）：requirement 6/7、review 2/2 和改前相同；design 8/11 → 7/11。
少的那条是全库最长的条目（6922 字符），整条放不下，现在只以标题出现在 omitted 行里；改前截成第一句也算命中。
样例集脚本现在不把 omitted / pointer 行算作"读者看得到"（它们只给出处）。中位耗时同机同时段比，改后不慢于改前。
REFLECT 四条换了说法的 lesson，改前有一条真重复没进候选，改后四条都在候选里。

**试过、没收的：相关性下限。** 每份文档各自归一，第 1 名总是 1.00，弱匹配也会占满名额。要求一个单元至少命中
查询里 2 个不同的词：110 个单元去掉 21 个，但 requirement 6/7 → 5/7；至少 3 个词：requirement 4/7、design 7/11。
都不收。r15 归一改为除以最高分后又测了相对下限（每份文档第 1 名保留，其余低于最高分的 τ 倍去掉）：τ = 0.4 / 0.5
时 16 条样例的命中不变（展示单元 104 → 98 / 88），τ = 0.6 起 requirement 掉到 5/7。但逐条看被去掉的单元，两档都去掉
了真正相关的条目（例如"召回切条目、截断、找 SwarmWS 各只有一个入口"，对 recall 相关性和 DDD 路径两条请求都对口），
样例集只数期望条目，看不出这种损失。分数下限分不开有用和噪声，和 recall-precision 设计的结论一致，仍不收。

#### 3.6.13 读取侧全部整条取舍（r15）

r14 只改了 `recall_ddd`，其他读取路径仍然各自截断，等于又多了一套规则。r15 把规则放进共享层
`recall_filter`，所有读取路径都调用它：

- **一个规则，三个函数。** `take_whole(units, cap, size)`：按排名整条放，第一条放不下就停，它和后面的都
  算未显示；`not_shown_note(items)`：一行写出未显示单元的位置、自己的标题（整条，200 字符内）和长度，
  最多逐条列 5 个，其余计数；`unit_title(text)`：加粗标题或标题行。`clip`、`HIT_CHAR_CAP`、
  `MIN_CLIP_ROOM` 删除。`recall_ddd` 的 `_take_whole` 改为调用它们。
- **各读取路径的单元。** 聊天召回：每条命中（Library 小节、记忆归档条目、过去会话、代码符号），
  context_files 按 `##` 小节。DailyActivity 注入：会话块（`<!-- da:… -->`）和 `##` 小节，保留最新的，
  保留换行（原来按词留尾部、把换行压成空格）。repo guidance：文件按 `##` 小节（不在代码块内切），
  lessons 按条，多个仓库按仓库块；没显示的仓库恢复注入状态，下次触碰时再注入。`--file` 召回：条目，
  无条目的小节按 `split_blocks` 的块。过去会话：整条消息。pipeline 的 Library 和 lessons 两路同上。
- **未显示的那一行不是命中。** 它没有 id、没有分数，不进合约、不计入 TSCC 的命中列表；来自不可信来源
  （绑定仓库文档、抓取的 Signals）的那一行只写位置、不写标题，并带上来源，渲染时落在该来源的不可信块里。
- **预算按"整条放得下"重定（实测分布）。** Knowledge Library `##` 小节 p50 0.8K、p90 3.9K、p99 11K
  字符：聊天 library 2800 → 4500，follow-up 1400 → 2400；pipeline 的 `CAPS` library 2000 → 4500、
  memory_archive 600 → 1200、lessons 1000 → 2500。过去会话按会话预算分配，每个会话最多 follow-up 的
  会话上限（900），这样一条会话命中在两种预算里都放得下。DailyActivity 仍是每天 2000 tokens。
- **BM25 归一。** 原始分只含真实命中（分数 > 0），min-max 把最弱的那条归成 0，下游按 0 当"没命中"丢掉。
  改为除以最高分，最弱的真实命中也大于 0；排序不变。
- **`split_blocks` 不在代码块内切**：原来代码块里的空行会把一个代码块切成两半。

实测：16 条样例 requirement 6/7、review 2/2、design 7/11，和 r14 相同。DailyActivity 用今天的真实文件：
5 条最新的会话块整条显示（1309 / 2000 tokens），14 条更早的计数并给出文件。repo guidance 用本仓库的真实
文件：AGENTS.md 显示开头和第一节，其余 16 节按标题列出；整块 6.0K，低于 CLI 的 8000 字符上限。

## 4. 不做的

- BUILD 不再召回。THINK / PLAN 只读 EVALUATE 之后算好的 knowledge_design.md，阶段中途不再召回；agent 在
  SwarmAI 里本来就有按需检索的工具，native 优先。（r9：原来写的是"THINK、PLAN、BUILD 都不再召回"，
  §3.6 改为 EVALUATE 完成时算一次给 THINK / PLAN。）
- 不用向量检索。§3.6 解决的是"被挤掉"，解决不了"用词对不上"。
- 聊天召回只改 §3.0（切法和截法）和 §3.6.4（改用 `recall_ddd(purpose="chat")`，单独一步、回放量过才合入）；不改发送路径。字数上限 r15 起按 §3.6.13 重定。
- 不给 security 专家加块；不给专家 Library。
- DDD 的 Gates 节和 `review.md` 是两个质量来源，本设计不合并，只记录问题。
- 不清理已有 run 的记录。

## 5. 怎么量

S5 加两个数，按 run 统计：
1. `knowledge.md` 状态分布，以及 `knowledge_refs` 非空的 run 占比；
2. 引用了 DDD 块条目的 finding 数，以及其中被确认（closed_proven）的数量。

如果 30 天内专家 DDD 块几乎没有被引用，就删掉步骤 4。

## 6. 实施（每步一个提交，先写测试，做变异检查）

| 步骤 | 内容 | 验收 |
|---|---|---|
| 0 | 召回统一用 `_scan_entry` 切条目；`clip` 截到句末带出处；预算按排名不跳过 | 结构测试：召回代码里没有按 `^- ` 切条目的正则、没有 `_truncate`；fixture 里吞了 `###` 小节的条目只剩 bullet 本身；超长条目截在句末且带出处；排第 3 的长条目不会被第 4、5 条挤掉；`recall_suite` recall@K / MRR 不下降、ddd 和 context_files 两路耗时变化在 ±10% 内 |
| 1 | workspace 解析统一 | 结构测试 KNOWN 为空；rig 设 `SWARM_WORKSPACE` 后召回读的是临时 workspace（在 main 上是红的）；不设任何变量时解析结果不变 |
| 2 | create 按绑定确定项目，`has_ddd` 进 contract | 名字不一致被拒；未绑定仓库照常；REFLECT 按 contract 路由 |
| 3 | `knowledge.md` + `knowledge_refs` | 有 DDD / 无 DDD / 召回出错三种 fixture 各自状态正确；无效引用被拒；Signals 标 UNTRUSTED；REPORT 不含召回内容 |
| 4 | correctness 专家 DDD 块，删旧脚本 | handout 含块且有上限；无 DDD 时没有块；无效 `knowledge_ref` 不生效；旧脚本无引用 |
| 5 | §3.6.5 H1：加载器只跳过坏的那一份；建 H5 测试矩阵 | 非 UTF-8 那一格在 main 上红；其余格在 main 上就该绿（证明现状没 crash）；矩阵后面每步都要绿 |
| 6 | §3.6.2 `recall_ddd` + 配置表 + 样例集脚本 | 确定性 fixture 测试；样例集达到 §3.6.6 合入标准；`recall_suite` recall@K / MRR 不下降 |
| 7 | §3.6.3 pipeline 接入：knowledge.md 用 requirement、新增 knowledge_design.md 和 `knowledge_recalled` 事件、专家块用 review、卡片和 `next`、REPORT 一行、删 `recall_ddd_entries` | rig 跑 full / bugfix / trivial / direct；`knowledge_refs` 认两处并集、编造的 id 被拒；REFLECT 候选含 design 那一份；H5 矩阵全绿 |
| 8 | §3.6.4 聊天改用 `recall_ddd(purpose="chat")` | 回放（XG 同意后）满足 §3.6.4；不满足就不合入，只记录 |

每步跑 pipeline / run_protocol / reflect / recall 相关测试，串行分块。不 push、不打包。

## 7. 要 XG 定的

1. §3.1 的优先级：`SWARM_WORKSPACE` 高于 `SWARMWS`。
2. §3.3 和 §3.4 的总量上限（pipeline 前端：DDD 3000 / Library 2000 / lessons 1000 字符；专家 5 条 / 2500 字符）。
3. §5 的"30 天几乎没有引用就删"。
4. §3.0 R2 的截法：截到句末加"全文 N 字符：出处"（备选：只给出处不给正文，会丢掉第一句规则）。
5. §3.6.2 的配置表：各 purpose 的文档、名额和每次都带的小节（r9 新增）。
6. §3.6.6 的合入标准，以及样例集放在 SwarmWS、由 XG 机器跑、不随包发布（r9 新增）。
7. 步骤 8 回放要读 data.db，需要 XG 同意（r9 新增）。

## 修订

- r1（2026-10-26）：初稿，四步。
- r2（2026-10-26）：XG 审查指出按字数截会截断语义。实测发现两层问题：条目边界切错（平台召回的
  bug），以及截断在句子中间、预算会让排名倒挂。新增步骤 0，并按 XG 的要求让系统级召回（聊天注入、
  repo_guidance）和 pipeline 用同一套 R1–R4；§3.3、§3.4 不再自定截断规则。
- r3（2026-10-26）：步骤 0 实施；记录和设计不同的三处，以及改前改后的实测（§3.0.1），含真实消息回放。
- r4（2026-10-26）：步骤 1 实施（§3.1.1）。
- r5（2026-10-26）：步骤 2 实施（§3.2.1）。
- r6（2026-10-26）：步骤 3 实施（§3.3.1）。
- r7（2026-10-26）：步骤 4 实施（§3.4.1）。
- r8（2026-10-26）：§3.5 实施（§3.5.1），并修了 REFLECT 候选带 bullet 导致 reinforces / refines 永远落空的 bug。
- r9（2026-10-26）：XG 指出 EVALUATE 只召回了 TECH / IMPROVEMENT，需求分析该看 PRODUCT / PROJECT，THINK / PLAN 该看 TECH / IMPROVEMENT。实测 run_85e81059 确认是 4 份文档混排、数量压倒相关性。新增 §3.6：平台 `recall_ddd` 按用途分配置，pipeline 分阶段接入，聊天单独一步回放验证；加上没有 DDD / DDD 不完整或损坏时都不能 crash 的硬性要求和测试矩阵（实测发现一份非 UTF-8 文档会让整个项目的 DDD 召回失效）。步骤 5–8 待实施。
- r10（2026-10-26）：步骤 5、6 实施（§3.6.7）：加载器只跳过坏的那一份；`recall_ddd` + 配置表 + 样例集脚本，16 条样例上 requirement 4/7 → 7/7、design 7/11 → 8/11、review 2/2 不变，耗时下降。
- r11（2026-10-26）：步骤 7 实施（§3.6.8）：knowledge.md 按 requirement、EVALUATE 之后 knowledge_design.md 按 design（`knowledge_recalled` 事件）、专家块按 review、REFLECT 候选取两处；删掉 `recall_ddd_entries`。
- r12（2026-10-27）：PRODUCT 按段落召回（§3.6.9，`split_blocks` + `block` mode），样例集改为按读者看得到的文本计分；步骤 8 回放 519 条真实消息，成本标准满足但质量好坏参半（有小节的消息 21 → 13），不合入（§3.6.10）。
- r13（2026-10-27）：聊天召回按小节聚合再过滤，两种做法回放 519 条真实消息都不合入（§3.6.11）：按整节算覆盖率会让几个大而泛的小节几乎每轮都出现，按单位算又过线太少，耗时都上升超过 10%。根因是聊天注入的是小节指针，不是内容。
- r14（2026-10-27）：按 CDF 第一准则，`recall_ddd` 只整条取舍，放不下的写进 omitted 行（位置、标题、长度）；预算按整条放得下重定；单行引导句不作为单元；REFLECT 四份文档一起排名（§3.6.12）。相关性下限实测会少命中，不收。
- r15（2026-10-28）：整条取舍推广到全部读取路径，规则放进共享层 `recall_filter`（§3.6.13），作废 §3.0 的 R2/R4 截断；BM25 归一不再把最弱的真实命中归零；`split_blocks` 不在代码块内切。
