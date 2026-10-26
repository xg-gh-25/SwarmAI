---
title: Pipeline run 怎么用上 DDD 和通用 Knowledge
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r4 — 步骤 0、1 已实施（§3.0.1、§3.1.1）；步骤 2–4 待做
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

### 3.5 REPORT 和 REFLECT

- REPORT 只加一行：`Knowledge: <状态> · N items · cited M`（M = 需求、设计、finding 引用到的条目数）。
  **不**列召回内容和 Library 路径：REPORT 会提交进用户仓库。完整列表在 `run_audit.json`。
- REFLECT 的去重候选改为读 `knowledge.md` 里的 DDD 条目加原有的按经验召回，合并去重。
  `reinforces` / `refines` 自然就能指向这次真正用过的条目。

## 4. 不做的

- 不在 THINK、PLAN、BUILD 再召回。agent 在 SwarmAI 里本来就有按需检索的工具，native 优先。
- 除 §3.0 外不改聊天的召回；不改发送路径。§3.0 的字数上限不变，只改切法和截法。
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

每步跑 pipeline / run_protocol / reflect / recall 相关测试，串行分块。不 push、不打包。

## 7. 要 XG 定的

1. §3.1 的优先级：`SWARM_WORKSPACE` 高于 `SWARMWS`。
2. §3.3 和 §3.4 的总量上限（pipeline 前端：DDD 3000 / Library 2000 / lessons 1000 字符；专家 5 条 / 2500 字符）。
3. §5 的"30 天几乎没有引用就删"。
4. §3.0 R2 的截法：截到句末加"全文 N 字符：出处"（备选：只给出处不给正文，会丢掉第一句规则）。

## 修订

- r1（2026-10-26）：初稿，四步。
- r2（2026-10-26）：XG 审查指出按字数截会截断语义。实测发现两层问题：条目边界切错（平台召回的
  bug），以及截断在句子中间、预算会让排名倒挂。新增步骤 0，并按 XG 的要求让系统级召回（聊天注入、
  repo_guidance）和 pipeline 用同一套 R1–R4；§3.3、§3.4 不再自定截断规则。
- r3（2026-10-26）：步骤 0 实施；记录和设计不同的三处，以及改前改后的实测（§3.0.1），含真实消息回放。
- r4（2026-10-26）：步骤 1 实施（§3.1.1）。
