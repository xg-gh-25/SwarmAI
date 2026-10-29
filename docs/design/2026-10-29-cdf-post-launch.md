---
title: CDF 上线后 —— 观测、遗留问题、删除和效果评测
created: 2026-10-29
updated: 2026-10-29
project: SwarmAI
status: 待确认
follows: 2026-10-29-cdf-launch.md（切换前的修复）；2026-10-26-swarm-cognitive-distillation-flow.md §14.1 / §14.2 / 阶段 C
---

# CDF 上线后

CDF 一次切换上线之后要做的事。原则不变：不加开关、不加新存储、不过度设计；能用现成机制（§14.2 的批量评测、`ingest eval-export`、`ingestion.db` 里已有的表）就不新建。每条都对源码核实过（2026-10-29）。

## 0. 建议在 build 之前先修的一条（已修，build 前落地）

**提取不知道卡 0 的长度上限，超长的认知单元整条被丢弃。** `pipeline_v2._card0` 对认知单元做确定性检查：title、what、how、why、applicability、rejected_alternatives 合计超过 600 字符就判 `not_atomic`，整条丢弃，不调模型。可是提取的 prompt（`extract_llm.build_prompt`、`s7_review` 的复盘 prompt）一处都没有提这个上限。写的一方不知道读的一方的约束，所以写得完整的长教训正好被丢掉。回放里 108 条拒绝中有 66 条是 `not_atomic`，但卡 0 和判官各占多少当时没拆开。

**修法**：两处 prompt 都写明「一个单元所有字段合计不超过 600 字符；超过就拆成几个单元，每个单元单独成立」。上限只在 `pipeline_v2` 定义一次，prompt 引用这个常量，不另写数字。再加一条测试：prompt 里的数字等于卡 0 用的常量。

**为什么在 build 前做**：不修的话，上线第一天起每条长教训都会被静默丢掉，而且之后不会被重新判（被拒的单元只留在 eval 记录里）。改动是两处 prompt 加一条测试，提取版本号要再升一次。

## 1. 上线当天到第 7 天：怎么看它是否稳定

重启后我在第 0、1、3、7 天各查一次，都是只读查询，查的是 `~/.swarm-ai/ingestion.db` 和 daemon 日志：

| 看什么 | 怎么查 | 不对的表现 |
|---|---|---|
| owner 起来了 | 日志里有 `CDF ingestion owner worker started` | 只看到 `owner not taken` 而且一直重试 |
| 会话任务在跑 | `ingestion_s2_jobs` 按 state 计数；`extraction_attempts` 按 status 计数 | 有 `running` 超过 1 小时；`failed` 占比持续上升 |
| 会话任务没被饿死 | `ingestion_s2_jobs` 里 queued 且 `next_attempt_at` 早于现在的行，看它等了多久 | 白天一直排着。原因：只要有任何一个 tab 在 streaming，或者内存压力为 warning，S2 就不领任务（`worker.ingestion_admission`）。多 tab 一直开着用的时候，S2 可能要等到全部 tab 都停下才跑。这条看第 1 天的数据再决定要不要改 |
| 没有卡住的单元 | `ingestion_units` 里 state 为 judged / writing、`updated_at` 早于 1 小时前的行；`ingestion_inbox` 里 in_progress 超过 1 小时的行 | 计数不为 0（按 B3 修复，启动恢复和每日维护应当把它们归零） |
| 毒化有没有爆 | `failed_poison` 按 `error_class` 分组 | 同一个 error_class 反复出现：说明是确定性错误，要修代码，不是重试能解决的 |
| 用户冲突 | `ingestion_events` 里的 user_race / writer_unknown / recovery_failed | 有 writer_unknown：说明有人绕过 CDF 写了受管文件 |
| 写进了哪里 | `ingestion_writes` 里 step=done 的行按 target_path 计数；MEMORY / EVOLUTION 当天的 `ingestion_quota` 字节数 | 某个文件每天都顶到 4 KB（上限太低，或者噪音太多） |
| 花了多少 | `ingestion_llm_usage` 按 utc_day、source 汇总 calls 和 tokens | 单日 token 明显高于回放时的单会话量乘以当天会话数 |

第 7 天把这张表的结果写进本文 §6。出现「不对的表现」就当场修，修法走「失败测试 → 修 → mutation」，不攒到后面。

## 2. 第 14 天：效果评测（这次切换没做的验收）

原设计阶段 C 的验收（成对盲评、有记忆和无记忆的消融）在切换前没做，因为用户选择了一次切换。效果到现在没有被证明过。

- **批量评测**：按 §14.2 跑 `ingest eval-export --since <上线日>`，再跑 `cdf_batch_eval.py` 的 sample → label（3 个非判官模型）→ aggregate → 用户在 `review.html` 只改不同意的条目 → score。得到写入精确率、该写召回率、项目路由、D1 和提取阶段丢失。`failures.md` 是调 prompt 的唯一入口。
- **按 source 看「该写」比例**（§14.1 T6）：低于 5% 的 source 改提取方式或者关掉。S4 / S5 / S6 / TIMELINE 要不要删，就看这个数，不凭印象。
- **消融**：选 10 个固定任务，分别在完整注入 MEMORY / EVOLUTION 和不注入两种条件下各跑一次，盲评后报告差值。这个结果回答一个问题：CDF 维护的注入层到底有没有让判断变好。没有差值，就该重新考虑注入预算和选取规则。
- **MEMORY**：回放里 MEMORY 一条都没写（只有用户原话来源才能进 MEMORY，§5 的表）。第 14 天看真实数据：如果仍然接近 0，就在评测结果里看该进 MEMORY 的条目是不是被路由去了别处。

## 3. 第 7 天后：删除

前 7 天如果没有出现 §1 表里的问题，就删下面这些代码：

| 删什么 | 依据（已核实） |
|---|---|
| v1 引擎：`core/ingestion/pipeline.py`、`card2.py`、worker 的 v1 分支（`_process_candidate` 的非 v2 路径、`_candidate_from_row`、`_upsert_units`）、`scripts/ingestion_replay.py` 和 `tests/test_cdf_pipeline_replay.py` | `pipeline.py` 只被 `__init__`、这一个脚本和这一个测试引用。`card0` / `card3` / `normalize` v2 仍在用，保留 |
| v1 入队链：`api.enqueue` → `inbox.enqueue`，以及 `core/ddd_cultivation.py` 里调用它的 `_cultivate_proposals`、`apply_to_ddd`、`write_proposal`、`cultivate_from_*` | 生产代码里，这条链只被下面两个未注册的 hook 调用，另外还有 `scripts/probe_ddd.py`、`scripts/check_migration_class_test.py`。`ddd_cultivation` 里其他函数（比如 `detect_contradiction`）是否还有活的调用方，删之前逐个 grep，不整文件删 |
| `hooks/knowledge_backflow_hook.py`、`hooks/improvement_writeback_hook.py` 和它们各自的测试 | 两个 hook 都没有注册；别处只在 `routers/cultivation.py`、`core/ddd_cultivation.py` 的注释里提到它们，这些注释一并改掉 |

删的时候先把引用全部 grep 出来（包括测试和注释），每次删一类，跑一次 CDF 测试。

## 4. 遗留问题：处理或者接受

| # | 问题 | 处理 |
|---|---|---|
| L1 | 抽样的主提取失败后不会重跑（复盘水位已经前移） | **接受**。它只是测量样本；会话本身的提取由复盘完成；无法 resume 的会话仍会重跑 |
| L2 | `test_busy_returns_within_bound` 在负载高的机器上超出 1.7 秒（main 上也一样） | **随 §3 删除**：它测的是 v1 的 `inbox.enqueue`（1.5 秒总期限），v1 入队链删掉后这条测试一起删。在删之前不改它 |
| L3 | 跨语言重复：提取已统一用英文，但 DDD 里已有的中文条目，判重仍认不出对应的英文新条目 | **接受，并观测**：§13 规定已写的内容不改。第 14 天的评测里看 already_exists 是否偏低 |
| L4 | 带日期或版本的快照条目（回放里约 5 条）能通过易变数字规则 | **不加规则**。由 §2 的 failures.md 决定要不要改卡 1 的 prompt；规则只认确定性的数字形态，再加会误伤 |
| L5 | 判官放过了带「likely」的推测性条目 | 同 L4，交给评测 |
| L6 | `/api/ingestion/redact` 只靠本地回环地址和固定 header 保护 | **不归 CDF**。整个本地 API 都是这样，要做就单独做产品层的设计 |
| L7 | 旧版 writer 遗留的 deferred 行（next_attempt_at 为空、原因不是停放类）会永远不就绪 | **无需处理**。上线时线上 ingestion.db 一个单元都没有（已核实），新代码不会再产生这种行 |
| L8 | 进程内重启时，owner 获取线程拿到的锁不释放 | **接受**。生产环境不会进程内重启，进程退出时 OS 会释放 flock |
| L9 | recall 不读 supersede 关系 | 沿用上线文档 §5 的「不做」：CDF 自己写的条目在 replace / refine 时已经处理，只剩基线条目被取代这一种情况 |

## 5. 验证计划

- **§0**：先写失败的测试，内容是「prompt 里的上限等于卡 0 的常量」，撤掉修复测试必须变红；跑 `test_cdf_extractor_structured`、`test_cdf_v2_sources_s2`、S7 的测试；不需要专家审查（只改 prompt 文本和一个常量引用）。
- **§1**：只读查询，没有代码改动。
- **§2**：评测数据只放在评测目录，不写回 `ingestion.db`。标注花费有上限（`--max-usd`），消融的花费在跑之前报给用户。
- **§3**：每删一类，跑一次 CDF 测试；跑 `test_cdf_materializer_static`（写入者清单）；做一次 correctness 审查，看有没有遗漏的调用方。
- 结束时按行报告：§0、§1、§2、§3 各是完成还是缺失。

## 6. 结果

（第 7 天、第 14 天填写。）
