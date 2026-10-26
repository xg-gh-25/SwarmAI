---
title: Recall 实测评估与改进 — 精度、follow-up 覆盖、DDD 项目识别、context_files 空转
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r1 — 设计，待评审
---

# Recall 实测评估与改进

## 0. 一句话

用 100 个真实会话的 1771 条用户消息回放 recall，再抽 250 条做相关性判定：首消息 recall 基本够用，
问题集中在四处，同一优先级处理——
**D1** 注入内容有一半以上不是有用的（库命中排名靠后的、过去会话里一周前的，基本是噪音）；
**D2** follow-up 的"每会话 6 次"上限把长会话后半段的 recall 全挡掉；
**D3** DDD 项目识别几乎总是失败（100 次里 4 次识别成功），因为名字匹配用了通用词、并且没用"会话碰过哪个绑定仓库"这个现成事实；
**D4** context_files 这一路在会话路径上永远只返回已在 prompt 里的 MEMORY，是空转。

每条改动都有回放数据支撑；验收用一份**没参与调参**的新样本重测（§5）。

## 1. 怎么测的

- **回放**：最近 100 个会话的全部 1771 条用户消息，逐条走真实代码
  `_extract_query_keywords` → `_unified_recall_body`（`recall_all` + `recall_filter`）
  → `_inject_ddd_for_active_project`，并按真实的 first / follow-up 状态机（6 次上限、Jaccard 0.2
  话题判定、关键词缺失计数）决定这条消息会不会 recall。另外对**每一条**有关键词的消息都跑一次，
  得到"如果 recall 了会注入什么"的反事实。
- **只读**：所有 sqlite 连接强制 `mode=ro`；过去会话那一路用临时 view 只看这条消息**之前**创建的消息；
  用量记录（`record_ddd_hit`）和 `recall_metrics` 关闭。消息通过 daemon 的只读 HTTP 读取。
- **判定**：从回放里抽 250 条（100 条首消息、100 条 follow-up、50 条被 6 次上限挡掉的），
  10 个没有本会话上下文的子 agent 逐条标注：每个命中 有用 / 沾边 / 噪音；这条消息需不需要 recall；
  若需要但没拿到，缺的是什么。
- **已知偏差**：Library 索引无法按时间截断，约 10% 带日期的库命中晚于提问时间，库的有用率略偏高；
  判定者看不到当前对话，"沾边"与"噪音"有主观成分；§3 的阈值是在这 250 条上选的（样本内），
  所以 §5 要求用新样本验收。

## 2. 结果

### 2.1 做得好的

| 项 | 实测 |
|---|---|
| 首消息覆盖 | 需要 recall 的 83 条里 76 条拿到至少一条有用命中 |
| 首消息注入量 | 中位 4.3K 字符，最大 4.9K（10-22 的上限守住了） |
| 延迟 | 首消息中位 152ms / p90 488ms；follow-up 中位 125ms；8 秒灾难超时 0 次 |
| 单词消息 | 312 条只有一个普通词的消息全部正确不 recall |
| 有用内容来源 | DailyActivity 命中有用率 50%，是最有价值的一路 |

### 2.2 问题

| # | 问题 | 实测 |
|---|---|---|
| G1 | 精度低 | 1385 个被判定的命中：有用 33%、沾边 29%、噪音 37% |
| G2 | follow-up 被次数挡掉 | 1066 条消息因 6 次上限跳过，其中 693 条本会注入；抽样 50 条里 33 条本能拿到有用命中 |
| G3 | DDD 项目识别失败 | 首消息 74 次 `no_signal`、11 次 `ambiguous`、15 次识别出项目，其中 6 次是通用词误判（"agent"→Agent-DLC、"repo"→ai_ready_repo、github 链接→GitHub_Community）；而 100 个会话里 79 个碰了一个已绑定的仓库，其中 74 个在前两轮就碰了 |
| G4 | context_files 空转 | 原始 931 个命中，过滤后剩 6 个 |
| G5 | 不需要 recall 的首消息也注入 | 17 条不需要的首消息里 15 条注入了约 4K 字符 |
| G6 | 关键词搜不到的问法 | "有什么新 update"、"找下我们的 design"、"最新的 Bio" |


### 2.3 根因（每条都由数据指出）

**G1 精度。** 现有的相关性门槛是"命中覆盖约 30%、至少 2 个查询词"。实测这个量**分不开**有用和噪音：
有用、沾边、噪音三类命中的覆盖比例中位数都是 0.4–0.5。真正能分开的是另外两个现成的量：

| 信号 | 分组 | 有用 | 噪音 |
|---|---|---|---|
| 库命中在本域内的排名 | 第 1 | 62% | 20% |
| | 第 2 | 37% | 30% |
| | 第 3–4 | 29% | 39% |
| | 第 5 及以后 | 15% | 56% |
| 过去会话命中的日期 | 1 天内 | 60% | 13% |
| | 7 天内 | 44% | 20% |
| | 7 天以前 | 17% | 52% |
| 库命中来源 | Signals / Archives/Signals / DailyBriefs | 0% | 60–100% |

过去会话那一路在 run_78bd708f 把新鲜度从排序里拿掉了（为了让 `LIMIT` 不改变排序结果），
于是一个月前的会话只要 BM25 分高就排在最前面。单纯把"至少 3 个词"提上去不行：噪音从 37% 降到 22%，
但需要 recall 的消息里拿到有用命中的从 168 降到 127，丢的太多。

**G2 follow-up。** 成本由一个固定次数（6）控制，而不是由"这次值不值得注入"控制。
话题判定（Jaccard < 0.2）几乎总是判为新话题（只有 27 次判为同话题），所以 6 次额度很快用完，
之后整个会话不再 recall。直接去掉上限也不行：会话累计注入的 p90 从 16K 涨到 64K 字符，最大 163K。

**G3 DDD 识别。** 两个原因叠加：
1. `_project_matches_query` 把项目名拆成 ≥3 字符的词逐个匹配，`Agent-DLC` 拆出 `agent`，
   `ai_ready_repo` 拆出 `ready`、`repo`，`GitHub_Community` 拆出 `github`、`community`——都是日常词。
   1771 条消息里 `Agent-DLC` 被匹配 55 次，大多因为句子里有 "agent"。另一方面 `\b` 在 Python 里把
   中文当作单词字符，"SwarmAI看下" 不算命中 —— SwarmAI 被少认了 15 次。
2. 识别只在首消息做一次，且只看编辑器路径和消息文字。**会话实际碰了哪个绑定仓库**这个事实
   `repo_guidance` 已经有（`_STATE[session]["repos"]` + `bindings.yaml` 的 `worktree`），
   recall 没用。79 个会话碰过绑定仓库，但项目识别只在首消息之前做。

**G4 context_files。** `_recall_context_files` 只读 live `MEMORY.md`。MEMORY 从 2026-08-14 起总是
整份注入，`recall_filter` 的"已在 prompt 里"检查于是把它的命中全部丢掉。这一路设计时是为了取回
**被排除**的 section（channel 隐私排除、预算排除），而能拿到 recall 的会话（桌面 tab、owner 私聊）
恰好什么都不排除。MEMORY 的归档（`.context/Archives/`）和较早的 DailyActivity 都已经在 Library 索引里，
由库那一路覆盖。会话路径上这一路没有可能返回新东西。

**G5 首消息的"不需要"。** 不需要 recall 的 17 条里，大部分是"看下这个 <URL/路径>"、"测一下这个 ‘<一大段粘贴>’"、
"Reply with exactly the word pong"这类。关键词从粘贴内容或 URL 旁边的几个词里抽出来，跟过去的东西碰巧重合。

**G6。** 64 条"缺东西"的判定里，约一半指的是当前对话里刚说过的内容（"选 B"、"第 4 项"、"#1"）——
模型本来就看得到，不是 recall 的缺口。剩下真正跨会话的约 10 条，分三类：问状态（会话开头的
Proactive Intelligence briefing 已经给 open threads 和 continue hints）、按概念找文档、要"最新"的版本。
量小，本设计不处理（§6）。

## 3. 设计

D1–D4 同一优先级，一起做、一起验收。D5 是实验项。

### D1 精度：用排名和新鲜度过滤，不再只看词覆盖

在 `recall_filter.filter_recall` 里，现有的覆盖门槛保持不变，再加三条规则：

1. **库命中每次最多保留前 3 个**（按 `recall_all` 已经给出的顺序）。排名第 5 以后的命中有用率只有 15%。
2. **Signals（包括 `Archives/Signals`）和 DailyBriefs 这两类来源不进会话 recall**。实测有用率是 0。
   Signals 本来就是标为 UNTRUSTED 的抓取内容（4fc9315a0），DailyBriefs 是从 Signals 生成的。
   MEMORY 的归档（`.context/Archives/`）不在此列，它的有用率是 26%，保留。
   被排除的文档仍然留在索引里，`s_library` 搜索和 CLI 都照常能查到，只是不再自动注入。
3. **过去会话的命中：7 天以内照常保留；7 天以前的，至少要命中 3 个查询词才保留。**
   这里需要命中消息的 `created_at`。`SessionRecall.recall_about` 现在只返回拼好的文本，日期只在
   粗体标题里，要改成同时返回结构化的 `(session_id, date, text)`。这条规则在 `LIMIT` 取回结果之后才过滤，
   不改变 BM25 排序本身，所以 run_78bd708f 修好的"`LIMIT` 改变排序结果"不会回来。

在 250 条样本上：

| | 命中数 | 有用 | 噪音 | 需要 recall 的消息拿到有用命中 | 注入字符 |
|---|---|---|---|---|---|
| 现在 | 1385 | 33% | 37% | 168 / 231 | 100% |
| D1 | 840 | 43% | 26% | 162 / 231 | 64% |

D1 用 4% 的覆盖换掉了 36% 的注入量，省下来的量在 D2 里用掉。

### D2 follow-up：去掉固定次数，改成"有相关命中才注入"加一个会话预算

- 删掉 `_RECALL_MAX_FOLLOWUPS = 6`。
- 话题判定仍然用 Jaccard < 0.2，判为新话题时照常跑 recall。D1 过滤后**一个命中都不剩**就不注入，
  这一点现在就是这样（`if not body`）。
- 新增**会话累计注入预算** `_RECALL_SESSION_CHARS = 24_000`：首消息加上所有 follow-up，累计注入到这个量
  就停。这是资源上限，不是判断条件。到达预算时打一条 INFO 日志，并计入 `recall_metrics` 的 `budget_stopped`。
- 预算记录在 unit 上，和 `_recall_history` 在同一处维护，并在 `SessionUnit._cleanup_internal` 里跟其他
  recall 状态一起重置。`_recall_history` 现在只留最后 60K 字符做去重，这一点不变。

回放（D1 已生效；回放没有模拟 `_recall_history` 的去重，所以实际值小于等于下表）：

| | 注入次数 | 会话累计中位 | p90 | 最大 |
|---|---|---|---|---|
| 现在 | 466 | 10.7K | 16.3K | 17.5K |
| D1 + 去掉上限 + 24K 预算 | 759 | 7.3K | 23.8K | 24.0K |

多出来的 recall 都落在长会话的后半段。每次 recall 中位 125ms，跑在 `io` 线程池上，不占 chat 路径
用的默认线程池。

### D3 DDD 项目识别：先用会话碰过的绑定仓库，名字匹配只认全名

识别顺序（先命中先用）：

1. **signal-1**，编辑器路径落在 `Projects/<X>/` 或 `s_<x>-*` 下，不变。
2. **signal-2（新）**，会话碰过的已绑定仓库。直接读 `repo_guidance` 现有的 `_STATE[session_id]["repos"]`
   和 `_bound_projects()`，不再扫一遍目录。会话只碰过一个绑定项目时用它；碰过多个时取最近一次碰的那个。
   为此 `repo_guidance` 需要记录每个 repo 的最后触碰顺序，并暴露一个只读函数
   `session_bound_project(session_id) -> str | None`。会话关闭时 `forget()` 已经会清掉 `_STATE`。
3. **signal-3**，消息文字匹配项目名，改成**只认整个名字**：把 `Agent-DLC` 规范化成
   `agent-dlc` / `agent_dlc` / `agentdlc` / `agent dlc`，前后不能紧挨英文字母或数字（不用 `\b`，
   这样后面跟中文时也算命中）。项目可以在 `aim.json` 里写一个可选的 `aliases` 列表，额外认几个名字。
   写法跟 `name` 一样只是展示用，不新建第二份注册表。

触发时机：

- 现在 DDD 那一路只在首消息跑一次（`_ddd_injected`）。改成：**第一次识别出项目时跑一次**。
  首消息识别不出时，`_ddd_injected` 保持 False；之后每条用户消息都检查 signal-2，这是一次内存读，
  拿到项目就跑 DDD 这一路，然后置 True。
- `repo_guidance` 第一次碰绑定仓库时已经会注入一个 DDD **指针**（"read its DDD documents"）。
  D3 补上的是**和当前消息相关的 section**，两者不重复。所以通过 signal-2 识别出项目、但没有 section
  匹配时，DDD 那一路不再输出它自己的指针。
- **注入位置**：首消息之后的轮次一般复用已经在跑的子进程，`options.system_prompt` 会被丢弃
  （见 `_maybe_inject_recall` 的 `should_mutate_system_prompt`）。现在的 DDD 那一路无条件写
  `options.system_prompt`，以前只在首消息（冷启动）跑，所以没出过问题；D3 让它在后面的轮次也会跑，
  必须和 recall 块走同一个分支：复用进程的轮次写到 `unit._recall_query_block`，跟 recall 块拼在一起。
  缺了这一步，D3 在后续轮次的注入会静默丢失。要有一个测试覆盖这种情况。

回放：全名匹配让首消息误判从 6 次降到 0 次，SwarmAI 的首消息识别从 4 次升到 10 次。signal-2 在 79 个
会话里能给出项目，其中 74 个从第二条用户消息开始就有。

### D4 context_files：从会话路径上拿掉

- `_unified_recall_body` 的 domain 列表里去掉 `context_files`，`_maybe_inject_recall_inner` 记 metrics 时
  的 `_sp_domains` 也同步去掉。
- 失败时的回退路径 `_recall_for_query` 里的 `MemoryRecallStore` 也是同样的空转，一并去掉。
- `recall_all` 本身**保留** `context_files` 这个 domain：`context_recall_cli.py`（agent 按需取被排除的
  section，并对隐私排除做硬拒绝）和 `recall_suite.py` 基准还在用它，它们的场景是真的有内容被排除。
- 以后如果能拿到 recall 的会话开始排除 MEMORY 的 section，再把它加回来。加回来的条件写进
  `_unified_recall_body` 的注释，不写成一个开关。

每次 recall 省掉一次 MEMORY.md 读取和 BM25（约 13ms），注入内容不变（过滤后本来就是 0）。

### D5（实验，验收通过才合入）：粘贴和链接不抽关键词

`_extract_query_keywords` 跳过引号（‘…’、“…”、```…```）里的大段粘贴（超过 200 字符的那段）
和 URL 旁边的"看下这个"这类词，只从用户自己说的话里抽。这个改动有风险：粘贴的报错信息有可能正好
应该去 recall。所以 D5 只在 §5 的新样本上验证，同时满足两个条件才合入：
"不需要却注入了"的数量下降，"需要且拿到有用命中"的数量不降。

## 4. 每个事实在哪、谁写谁读、热路径成本

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 会话碰过哪个绑定仓库、绑定到哪个项目 | `repo_guidance._STATE` + `bindings.yaml` 的 `worktree` | `repo_guidance` 的 Pre/PostToolUse hook（已有） | D3 的 `session_bound_project`（新，只读） |
| 过去会话命中的日期 | `messages.created_at` | 消息写入（已有） | `SessionRecall` 结构化返回（改） |
| 库命中排名 | `recall_all` 的结果顺序 | — | `filter_recall`（改） |
| 会话累计注入量 | `SessionUnit` 上的计数，和 `_recall_history` 放在一起 | 每次注入 | D2 判断 |
| 项目别名 | `Projects/<X>/aim.json` 的可选 `aliases` | 人写 | signal-3 |

没有新增存储，也没有新增 endpoint 和 hook。

热路径成本：
- D1：在内存里过滤已经取回的命中，增加的时间可以忽略。
- D2：多出的 recall 每次中位 125ms，跑在 `io` 线程池上，只发生在"判为新话题"的消息上。
- D3：每条消息多一次 dict 读取，项目被识别出来后不再读。
- D4：每次 recall 少约 13ms。

所有改动都不碰事件循环，也不碰 chat 路径等待的线程池。

## 5. 验收

1. **回放工具进仓库**：`backend/scripts/recall_replay.py`，就是这次用的 harness。它只读
   （sqlite `mode=ro`，按消息时间截断过去会话，关闭用量和 metrics 写入），只能手动运行，不进 CI。
   它会读 `~/.swarm-ai/data.db`，所以每次运行前要先得到 Gawan 同意。
2. **新样本**：取另外 100 个会话（不和这次的 100 个重叠），回放后按同样的方法抽 250 条，由没有上下文的
   子 agent 判定。调参用的那 250 条不能拿来验收。
3. **通过条件**（新样本上与"现在"对比）：
   - 噪音比例降低至少 8 个百分点，有用比例提高至少 6 个百分点；
   - "需要 recall 且拿到有用命中"的数量下降不超过 5%；
   - 被上限或预算挡掉、但本可拿到有用命中的消息减少一半以上；
   - 首消息 DDD 误判为 0；有 signal-2 的会话从第二条用户消息起能识别出项目；
   - 会话累计注入不超过 24K；首消息单次注入不超过 4.9K；首消息 recall p90 不变差。
4. **单测**：每条规则一个测试，并做一次 mutation 检查——把对应的改动还原，测试必须变红：
   - 库命中只保留前 3 个；
   - Signals 不注入；
   - 7 天前的会话命中只匹配 2 个词时被丢掉；
   - 项目名里的 "agent" 不再命中 Agent-DLC，"SwarmAI看下" 能命中；
   - 首消息之后才碰绑定仓库的会话，下一条消息能注入 DDD section；
   - 会话 recall 的结果里不再有 `context_files`，CLI 的 `recall_all(domains=("context_files",))` 照常工作；
   - 预算到了就停。

## 6. 不做

- **G6 那三类问法。** 跨会话真正缺的约 10 条，其中问状态的已经有 briefing 覆盖。按概念找文档、
  找"最新"的版本，由 recall 块末尾的提示交给 agent 自己再搜（`RECALL_HINT`）。等真的出现 agent
  搜了也没找到的情况，再按证据单独设计。
- **向量检索。** 这次的数据里分开有用和噪音的是排名和新鲜度，不是语义。纯 FTS5 的决定不变。
- **调话题判定的阈值。** Jaccard 判定几乎总是判为新话题，但有了 D1 的过滤和 D2 的预算之后，
  它判错只会多跑一次 125ms 的查询，不会多注入。不单独调。
- **历史数据。** 不改任何已有的会话、消息或索引内容。

## 7. 实施顺序

都在自己的 worktree 里做，每一步一个 ff-merge 的 commit，不 push，不 rebuild：

1. 把回放工具放进仓库（§5.1）。先在"现在"的代码上跑一遍新样本，得到基线。
2. D4（只删东西，最简单）。
3. D1，包括 `SessionRecall` 返回结构化日期。
4. D2，依赖 D1 先把每次的注入量降下来。
5. D3：`repo_guidance.session_bound_project`、全名匹配、触发时机。
6. 跑新样本验收（§5.3），结果写回本文 §8。
7. D5 单独验证，通过了才合入。

## 8. 验收结果

（实施后填写。）

## 待确认

- 回放工具要读 `data.db`（只读）。同意把它放进仓库、每次手动运行吗？还是只放在 scratch、每次临时用？
- 会话预算 24K：按现在的回放，会让 p90 会话的累计注入从 16K 涨到约 24K。可以接受吗？
