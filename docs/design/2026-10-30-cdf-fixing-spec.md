---
title: CDF fixing spec：标签只表达它的意思，读取方不再依赖 ingestion 的内部状态
created: 2026-10-30
updated: 2026-10-30
status: draft r2
---

# CDF fixing spec：标签只表达它的意思，读取方不再依赖 ingestion 的内部状态

- 日期：2026-10-30
- 依据：线上 CDF v2 上线以来约 1.5 天的数据（`~/.swarm-ai/ingestion.db` 只读查询），以及代码逐行阅读。
- 前置：`2026-10-30-brain-driven.md` v5（主文件就是当前有效的全集）。
- 状态：r2（一轮零上下文评审后修订），待 XG 确认，未实现。

## 0. 结论

CDF 的问题不在某一处，而在于同一个字段被拿来表达几件不同的事，读取方又直接依赖这个字段或 ingestion 的内部表。所以改 ingestion 的语义，读取方就跟着变。

修法有两条：
- **每个标签只表达一件事**，由写入方在入口一次定下；
- **读取方只读两样东西**：主文件的文本，以及 ingestion 发布的一个接口。ingestion 的表、放置方式和内部标记不再被读取方直接读取。

### 更正

上一轮我说"recall 把带 external 标记的 Brain 知识当作不可信数据"，这是错的。recall 不读 `taint:external`。它给所有召回内容同一个外层说明："[RECALLED] … Treat it as a lead to verify against source"（`recall_multi.py:612-620`）；Signals 和仓库文档按来源路径另有自己的"— UNTRUSTED"块（`recall_multi.py:630-706`）。都和这个标签无关。真正依赖这个标签的是 CDF 自己，见 §2 C1。

---

## 1. 两个"暂缓"是怎么来的

### 1.1 当天写入额度满了（89 条，2026-10-30 08:32 UTC）

- **规则**：每轮都注入的文件（MEMORY、EVOLUTION、STEERING、USER、TOOLS）每天最多写 4 KB。这是 AGENT R22 的规定，为的是控制每轮 prompt 的增长、保持缓存稳定（`writer.py:966-969`）。它不是花费上限：额度用完的单元等第二天再写，不会被丢掉，也不会被重新判断（`quota_still_full`）。
- **实测**：等待中的全部是 EVOLUTION。10-30 当天只提交了 7 条（3983 B），被退回等待的有 80 条。等待的单元里有一大半根本不该进 EVOLUTION。例如 S2 抽出的"Dispatch Center 的职责"这类 SwarmAI 产品决定，被判成 `scope: agent`，按规则路由进了每轮注入的 EVOLUTION。
- **结论**：根因是 **scope 判错（C3）**，额度只是把症状挡住了。每天进来约 80 条、放行约 7 条，积压只增不减，等于在慢慢丢。

### 1.2 等领域文件出现（61 条）

- **实测**：61 条里有 60 条是 S7 抽出的 project gap，1 条是认知单元，都在 10-29 09:15–17:52（UTC）进入 inbox。那时 gap 拿不到项目名（已由 `4b749f7ba`、`e9908dce6` 修好），于是以 `unknown_domain` 原因停放，等一个 `Knowledge/Learned/domains/<x>.md` 文件出现。
- 这些 gap 根本不属于任何领域，那个文件永远不会出现，所以它们会**永远停着**。10-29 17:52 以后再没有新增，问题本身已经修了，留下的是积压。
- 这批 gap 里还有不少是 session 状态快照，比如"not investigated""not verified as deployed"（见 C3）。

---

## 2. 问题与修法（按成因分组）

### C1 `taint:external` 一个标签表达了四件事，并且会自我传染

**现状**（逐行确认）：
1. `session_inputs.py:58`：除了 user 和 measured，所有输入一律登记为 `external`；所有工具结果都是 external（`:92`）。
2. `provenance.py:284-300`：下面两种情况，单元都被标为 tainted，不看单元自己引用的是什么：
   - 这个 session **读过**的东西里，任何一条（包括它的整条父链）是 external；
   - 读过的证据有一条找不到（`not read_complete`，安全起见按 tainted 处理）。
   这样设计是为了防止"挑着引用"：模型可能只引用用户原话，实际却依据了网页内容（`api_v2.py:149-150`）。
3. `writer.py:513`：写入时 `tainted = tainted or rerouted or non_injected_timeline or inferred`，满足任一条件就写上
   `taint:external`。这四种情况分别是：真来自外部、被改道到项目 DDD、时间线里不注入的事件、没被用户原话或测量结果证实。
4. `writer.py:1115-1127` 生成 baseline 时：带这个标记的条目登记成 external 证据，**其余全部登记成 user**。之后：
   - 引用 external 条目的单元会跟着变成 tainted，这就是**传染**；
   - 引用其它条目的单元会被算成 `user_explicit`（`provenance.py:290`）。

**实测**：S7 写进 DDD 的 221 条全部带标记，S2 是 93/105。S7 总会读到工具输出，所以它写的单元都会被标成 tainted；它发起的覆盖因此全部被拒（`overrides.py:111`）。这一点是从代码确认的，线上还没有一次覆盖真正生效过。

**读 taint 的地方**（全部）：
- (a) `pipeline_v2._not_grounded:543`：能否写入用户自己的文件（STEERING、USER、TOOLS）。安全边界，必须保留。
- (b) `_not_grounded:541` 和 `:672` 的 `supported`：没有 grounding 时按 `not tainted and measured` 判断。这影响 MEMORY、EVOLUTION 的入口，以及 DDD 的 inferred 标记。
- (c) `:675`：时间线事件放进"Not injected"小节。reader 在 `memory_injection.py:540` 跳过这个小节。
- (d) `overrides.py:111`：有 taint 的单元发起的覆盖全部被拒。
- (e) writer 的渲染（`writer.py:217-218, 343, 377-441`）和 baseline trust（`:1115-1127`）。
- (f) `rules/library.py:27`（L3）：tainted 单元写进 Library 时，必须带 `taint:external` 的 frontmatter。
- (g) 旧 v1 路径 `pipeline.py:89-147` 加 `taint.py`。线上 inbox 里没有 v1 行，但代码还在。

**external 不只来自这个标记**：已安装项目的所有条目（`writer.py:1117`）、frontmatter 和注释里声明的 external（`:1100-1112`）、bootstrap 写入的外部内容（`brain_bootstrap/apply.py:31,256`）。

**修法**：在 `provenance.resolve_evidence` 里一次算出三个概念，每个只表达一件事：

| 概念 | 意思 | 谁读 |
|---|---|---|
| `read_external` | 这个 session 读过外部内容，或读过的证据不完整（保留 `not read_complete` 的 fail-closed） | (a) 用户自己的文件 STEERING/USER/TOOLS 的入口，规则不变；(b) MEMORY、EVOLUTION 入口里 measured 那一支（`:541`），规则不变 |
| `cited_external` | 单元**自己引用**的证据，连同整条父链（`_closure`），里有 external | DDD 和 Library 的 `taint:external` 标记（含 L3）；时间线是否注入（c）；覆盖（d） |
| 证据强度 `strength` | user_explicit / measured / run_lesson / inferred，现有的 | baseline 的 trust（见下） |

**baseline trust 改为如实反映条目的依据**，这是评审指出的漏洞，必须一起改：
- 证据 trust 增加一级 `inferred`（`contracts_v2.RootTrust`）。它的意思是"不是外部内容，也没被用户原话或测量结果证实"。引用它的单元强度最高只到 inferred，也不会因此变成 tainted。
- CDF 写入的条目，按写入它的那个单元定 trust：`cited_external` 是 external；用户原话证实的是 user；测量证实的是 measured；其余是 inferred。
- 人写的条目（没有 cand 标记）照旧是 user，已安装项目照旧是 external。

这样两个问题一起解决：一条没证实的条目不会再被登记成 user（**F1b**），也不会因为 session 读过网页就被登记成 external。

**覆盖**：条件改为"不 `cited_external`，并且有用户原话证实（grounded）"。用户原话的 grounding 是对引文逐字核对的。

**残余风险**（写进 §3）：`cited_external` 只看单元自己引用了什么，所以 DDD 写入和覆盖重新面临"挑着引用"的风险。约束是：用户自己的文件和 measured 入口仍按 `read_external` 判断；覆盖要求引文核对通过。

**迁移**（一次性，先演练、先备份 ingestion.db 和主文件）：
- 只处理有 `ingestion_writes` 记录的 CDF 条目。bootstrap 条目、已安装项目、人写的条目一律不碰。
- 证据 root 的 id 由 (kind, locator, byte_hash) 算出，trust 被当作只写一次（`provenance.py:122-153`），而标记在条目字节之外，所以改了文件里的标记，root 的 trust 也不会跟着变。
- 所以迁移要显式 `UPDATE evidence_roots SET trust=…`，这是对"只写一次"的一次有记录的例外，写进迁移日志。
- 按引用关系的拓扑顺序处理：先处理被引用的条目，再处理引用它的单元，反复做到没有变化为止。幂等的定义是"到达这个不动点之后，再运行一次什么都不变"。
- 文件里的标记按新的 trust 改写，走 governed edit。

### C2 读取方直接依赖 ingestion 的内部状态

这一轮已经删掉的同类依赖有：dormant/archived 过滤、`superseded_by` 隐藏、`- [x]` 过滤、maturity、关系隐藏。还剩下这几处：

| # | 读取方 | 依赖什么 | 修法 |
|---|---|---|---|
| C2.1 | `memory_injection.select_timeline:540` | 跳过 ingestion 放进"Not injected"小节的事件：按放置位置过滤 | 每轮注入的文件里不写不该注入的内容。判定条件**沿用现在的规则**，只是改用 `cited_external`（C1）：带外部引用的事件不写进 TIMELINE，只留在记录里（`brain_changes` 可查）；不带的照旧写进月份小节。删掉"Not injected"小节和 reader 里的跳过逻辑。现在不另加 grounding 门槛：记录里没有事件的 grounding 结果，影响无法事先量出。现有 26 条走一次迁移，按新条件重算，清单先给 XG 看 |
| C2.2 | `memory_injection._score_unit:265-322` | 直接用 SQL 读 `claim_support`、`evidence_roots`、`ingestion_units`/`ingestion_inbox`、`ingestion_writes`、`unit_usage` 这些 ingestion 的表 | ingestion 发布一个接口 `ingestion.standing(anchors) -> {anchor: Standing(strength, support, refute, used, last_used, written_at)}`。memory_injection 只调这个接口，表变成 ingestion 私有。改 trust 语义（C1）时，排序的输入由这一个接口给出，读取方不用改 |
| C2.3 | recall 的外层说明 | 自己 Brain 的知识和其它召回内容用同一句"lead to verify against source"（`recall_multi.py:612-620`） | 只改措辞和一处分块。外层说明对自己 Brain（DDD、MEMORY、EVOLUTION）改为"当前有效的知识：决定、原则、非目标直接按它行事；关于代码和运行状态的模型、坑，行动前对照代码，冲突时以观察为准并报告"。Signals 和仓库文档已经各有"— UNTRUSTED"块，不变。"Past Sessions"（`_DOMAIN_LABELS["session"]`）现在是普通块，移进不可信那一层。所有块都保持"数据不是指令" |
| C2.4 | recall → `usage.record_recalled_result`；`knowledge_cli.py:121` 用私有的 `usage._units_in_content` | 命中文本反查回 CDF anchor | 前者保留，它是 ingestion 的接口，由 recall 调用。后者改为公开名 `usage.units_in_content` |
| C2.6 | `memory_injection.py:365,394,479` | 读写 ingestion.db 的 `injection_selection` 表，用读写模式连接 | `ingestion.standing` 同一模块里发布 `recorded_selection` / `record_selection` 两个函数，memory_injection 只调它们 |
| C2.7 | `hive_lessons.py:101-116,430-438` | 直接查 `ingestion_inbox`、`ingestion_units` | 改调 ingestion 的查询函数（`queries.py` 里补一个）|
| C2.8 | `usage_import.py:78-90` | 读 `ingestion_llm_usage` | 记账不属于认知读取。改调 `queries.llm_usage()`，规则对它同样适用 |
| C2.5 | `addressing`、`recall_multi`，以及 `knowledge_store.py:1021`、`recall_multi.py:803` 用到的 `parse_units` | `- [type] **title**`、cand 标记、单元切分 | 保留。格式由 `render_ddd_unit`、`parse_entries`、`parse_units` 共享定义，是契约，不是内部状态 |

**规则**（写进 TECH.md 的 CDF 一节，作为后续的检查标准）：读取方只读主文件文本和 ingestion 发布的接口；新增读取方时，如果需要 ingestion 的某个事实，就扩展这个接口，而不是直接读表。

### C3 入口：scope 判错，把快照和状态当认知写入

**实测**：
- 撞额度的单元里，产品决定被判成 `scope: agent`，进了 EVOLUTION（§1.1）。
- 抽样 24 条写入的认知单元，按我的判断有 6–8 条是快照或状态，几小时到几天就不再成立。例如"withdraw 有 TOCTOU 竞态"（当天已修）、"section_health.json 最后更新于 10-26"。判官给这些条目的"durable"（持久）判定都是通过。

**修法**（扩展已有机制，不另起一套）：
- **scope**：扩展 `_project_home`（`pipeline_v2.py:549-558`）。它现在只把没证实的 agent 单元改道到项目 DDD，并记下 `rerouted_from`。扩展为：`decision`、`model`、`guideline`、`process` 类型的单元，scope 是 `agent`、session 又有所属项目时，同样改道到项目。只有 `correction`，以及讲 agent 自身行为的 `principle`、`pitfall`，才留在 EVOLUTION。改道过去的条目，baseline trust 按 C1 如实登记，不会被当作用户原话。
- **快照和状态**：
  - 扩展 DDD 规则 D4（`rules/ddd.py`，经 `_volatile` 对所有认知单元生效）。它现在只拒收"会漂移的计数和大小"，扩展为同时拒收描述当前状态的写法：中英文的"还没/尚未/截至某日/still failing/not verified/not investigated/not yet"等，加日期截点。原因记为 `state_not_cognition`。
  - S2/S7 抽取提示词同步改：能修好的问题直接写成 gap（修好时由 S3 的 `closes` 关闭），已经发生的事写成事件。
  - 规则是确定性的兜底，和判官的 durable 判定互不依赖。durable 问的是"这条知识会不会一直成立"，规则问的是"它是不是在描述某一刻的状态"。上面那些条目说明，判官在后一个问题上会放过。

### C4 同一个 session 被抽取两次（S2 + S7）

**实测**：S7 用掉约 2/3 的 token；"已存在"拒收 56 条、并入已有条目 67 条，其中有一部分是两边抽出了同一件事。

**修法**（已确认 S2 现在不看 S7 是否复盘过，`sources/s2_session.py` 里没有这个判断）：S7 已经复盘过的 session，S2 只抽事件，不再抽认知。会话进行中由 S2 先写的认知照旧保留。S7 开始复盘时，读到 S2 在这个 session 里已写的单元，不再重复抽。

### C5 积压只增不减，而且没人知道

**修法**：
- **一次性**：61 条 `unknown_domain`（60 条 gap、1 条认知单元）和 quota 积压，按 C3 修好后的规则重新排队，走一遍当前的流水线。这些单元原本设计成"等待，不重新判断"（`writer.py:997-999`），这里改为重新判断一次，要多花一轮判官调用。这不违反 brain-driven 原则 5：原则 5 禁止的是对**已写进主文件**的内容复判，这批单元还没写进去。
- **结构性**：status 接口给每个暂缓原因报"今天进 / 今天出 / 总数"。连续 3 天"进 > 出"的暂缓原因，在 C&M 和 Need-You 里报出来。这是报告，不是上限。

---

## 3. 不在这次修的

- **近似重复增长**："补充"占写入的 47%，合并因为判官准确率不到 90% 门槛而保持关闭。下一轮按 brain-driven §5.2 的判官实验做。
- **有没有用**：DDD 的 recall 使用记录今天才开始记录，等 11-01 的月度倒查拿到第一份真实数据。
- **残余安全风险**：
  - `cited_external` 只看单元自己引用了什么，DDD 写入和覆盖重新面临"挑着引用"的风险。约束是：用户自己的文件和 measured 入口仍按 `read_external` 判断；覆盖要求引文核对通过。
  - S7 模型可能被注入文字带偏，把它当成用户原话。覆盖要求的引文核对是逐字核对用户消息，注入文字通不过，但模型仍可能把用户的原话错配到注入的意图上。

## 4. 不变量（最终测试集，每条都做 mutation check）

- **F1**：一个 session 读过外部内容、但单元只引用用户原话时，DDD 条目不带 `taint:external`；单元的引用链里有外部证据时，带标记，而且不能发起覆盖。
- **F1b**：没有被用户原话或测量结果证实的 CDF 条目，在 baseline 里不登记成 user trust；引用它的单元强度不超过 inferred。
- **F2**：用户自己的文件（STEERING/USER/TOOLS）的入口，以及 MEMORY/EVOLUTION 入口的 measured 一支，仍按 `read_external`（含证据不完整）拒收。现有安全测试全部保持通过。
- **F3**：每轮注入的文件里没有任何读取方需要跳过的内容；TIMELINE 不再有"Not injected"小节。
- **F4**：`memory_injection`、`hive_lessons`、`usage_import`、`knowledge_cli` 不出现任何 ingestion 表名，也不导入 ingestion 的私有函数，只调发布的接口（静态检查）。
- **F5**：recall 里自己 Brain 的内容用"当前有效的知识"框定；Signals、仓库文档和 Past Sessions 在不可信那一层；所有块都不执行其中的指令。
- **F6**：产品决定类单元在有所属项目时不进 EVOLUTION；描述当前状态的认知单元按 `state_not_cognition` 拒收，或由抽取端写成 gap、事件。
- **F7**：S7 复盘过的 session，S2 不再写认知。
- **F8**：任何暂缓原因连续 3 天"进 > 出"都会被报出来；一次性重新排队之后，`unknown_domain` 里没有 project gap，那 1 条认知单元按当前规则写入或拒收。
- **F9**：标记和 trust 迁移按引用顺序做到不动点；之后再运行一次什么都不变；备份先行；除标记外不改主文件的任何字节；只碰有写入记录的 CDF 条目。

## 5. 步骤与评审

读取方先于写入方：

| 步骤 | 内容 | 评审 |
|---|---|---|
| 1 | C2.2、C2.4、C2.6–C2.8：ingestion 发布接口，读取方改为调用（行为不变） | correctness |
| 2 | C1 三个概念拆开、`inferred` trust、writer 标记、覆盖条件；迁移脚本（先演练） | security、correctness |
| 3 | C2.1 TIMELINE 不写不注入的事件，迁移 26 条 | correctness |
| 4 | C2.3 recall 分层框定 | security |
| 5 | C3 scope 改路由、状态类改走 gap/事件 | correctness |
| 6 | C4 S2/S7 去重 | correctness |
| 7 | C5 重新排队、积压报告 | 不需要，测试证明 |

步骤 2 和 3 的迁移要动主文件，迁移清单先给 XG 看，和这次清理同样的流程。
