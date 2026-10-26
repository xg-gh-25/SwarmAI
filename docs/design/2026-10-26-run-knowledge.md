---
title: Pipeline run 怎么用上 DDD 和通用 Knowledge
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: DRAFT r1 — 待 XG 定 §7
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

### 3.1 一个 workspace 解析函数（步骤 1）

`config.get_swarmws()` 是唯一来源，优先级：`SWARM_WORKSPACE` → `SWARMWS` → `get_app_data_dir() / "SwarmWS"`。

- 两个环境变量都没设、`SWARM_DATA_DIR` 也没设时，结果仍是 `~/.swarm-ai/SwarmWS`，生产不变。
  动手前核实 daemon（launchd plist）、Hive（systemd unit）、Tauri subprocess 各自设了哪个变量。
- `project_registry.get_swarmws`、`jobs.paths.SWARMWS`、`cli_commands._workspace`、`hook_bridge` 和
  §1 G-B 列出的写死路径都改为调它。
- 结构测试：产品代码里除了 `config.get_swarmws` 不许再出现 `"SwarmWS"` 路径拼接或读取
  `SWARMWS` / `SWARM_WORKSPACE`。用一个只许缩小的 KNOWN 集合起步，本步结束时为空。
  在子进程里单独运行的 skill 脚本走同一个函数；做不到 import 的，列进 KNOWN 并写明原因。

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
- 总长上限 6000 字符；放不下的条目**整条**丢掉，并写明丢了几条、完整来源在哪。
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
- 最多 5 条、2500 字符，整条截断，数据标签，三种状态，嵌在 handout 里（不占专家 24 次调用）。
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
- 不改聊天的召回和发送路径。
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
| 1 | workspace 解析统一 | 结构测试 KNOWN 为空；rig 设 `SWARM_WORKSPACE` 后召回读的是临时 workspace（在 main 上是红的）；不设任何变量时解析结果不变 |
| 2 | create 按绑定确定项目，`has_ddd` 进 contract | 名字不一致被拒；未绑定仓库照常；REFLECT 按 contract 路由 |
| 3 | `knowledge.md` + `knowledge_refs` | 有 DDD / 无 DDD / 召回出错三种 fixture 各自状态正确；无效引用被拒；Signals 标 UNTRUSTED；REPORT 不含召回内容 |
| 4 | correctness 专家 DDD 块，删旧脚本 | handout 含块且有上限；无 DDD 时没有块；无效 `knowledge_ref` 不生效；旧脚本无引用 |

每步跑 pipeline / run_protocol / reflect / recall 相关测试，串行分块。不 push、不打包。

## 7. 要 XG 定的

1. §3.1 的优先级：`SWARM_WORKSPACE` 高于 `SWARMWS`。
2. §3.3 和 §3.4 的条数和字数上限（6 + 3 条 / 6000 字符；专家 5 条 / 2500 字符）。
3. §5 的"30 天几乎没有引用就删"。
