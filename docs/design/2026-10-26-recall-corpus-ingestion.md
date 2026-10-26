---
title: Recall 语料的写入与入库 — 来源策略、写入者身份、切块规范、读侧折叠
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r1 — 设计，待评审
---

# Recall 语料的写入与入库

## 0. 一句话

读取侧（`2026-10-26-recall-precision-and-coverage.md`）已经按排名、日期、来源过滤；剩下的噪音大多在
**写入时就进了语料**：同一段回复按日期被存成多份 Note，被删除的冒烟会话写进 DailyActivity，
只有 frontmatter 的 chunk 被当成正文召回和注入，chunk 不带文档标题。这些问题分散在多个写入者里，
规则也散落在三个文件中。本设计把它们收成四个结构：

1. **一份来源策略**：每类来源"进不进索引、能不能自动注入、走哪条时间规则、怎么切块"只写在一个模块里。
2. **写入者按生产者身份去重**：一条消息、一个会话只对应一份产出，用 id 判断，不用内容或日期。
3. **一个入库口规范 chunk**：去掉 frontmatter、带上文档标题、过短的段并进上一段。
4. **读侧折叠完全相同的内容**：已有的重复不动数据，在召回时只留一份。

不改、不删任何已有文件和会话数据。

## 1. 现状：谁写什么、怎么进索引

| 来源 | 写入者 | 写入时机 | 进入哪个检索面 |
|---|---|---|---|
| `Knowledge/DailyActivity/<date>.md` | `hooks/daily_activity_hook.py`（会话结束）、`core/runtime_hooks.py`（会话中 checkpoint），都经 `daily_activity_writer.upsert_session_block` | 后台 hook | `knowledge_chunks` / `knowledge_fts`（`context_health_hook._sync_knowledge_library` → `knowledge_store.sync_knowledge_index`）；最近 2 天另外直接注入 prompt |
| `Knowledge/Notes/*-<hash6>.md` | `hooks/knowledge_backflow_hook.py`：会话结束时扫这个会话的全部 assistant 消息，长且像分析的整段存为一页 | 后台 hook | 同上 |
| `Knowledge/Notes/` 其他、`Designs/`、`Reports/`、`Library/`、`Learned/`、`Pollinate/` | 人、skill、各类 job | 不定 | 同上 |
| `Knowledge/Signals/`、`DailyBriefs/` | signal 抓取 job 和由它生成的 brief | 定时 | 同上（读取侧已不自动注入） |
| `.context/*-archive*.md` | MEMORY / EVOLUTION 的归档阀 | 后台 | 同上，来源前缀 `.context/Archives/`，按条目切块 |
| 会话消息 | `messages` 表写入时生成 `content_seg`（`message_search_text`） | 实时 | `messages_fts` |

规则散落的位置：

- 进不进索引：`knowledge_store._SKIP_DIRS` / `_SKIP_NESTED_PREFIXES`
- 能不能自动注入：`recall_filter.NO_INJECT_PREFIXES`
- 归档单独占名额：`recall_filter.ARCHIVE_PREFIX`
- 时间规则：`recall_filter._DAILY_RE`，以及过去会话的 `date` 字段

同一个"这类来源怎么对待"的事实分在两个模块、五个常量里。

## 2. 实测问题和根因

数据：`knowledge_chunks` 全表（21062 个 chunk，只读查询）、10 月 DailyActivity 全部 229 条、
今天三轮盲评打过标签的约 3000 个命中（`2026-10-26-recall-precision-and-coverage.md` §8–§10）。

### W1 backflow 把同一段回复按日期存成多份

| 实测 | |
|---|---|
| Notes 文件 | 568 个，其中 419 个是 backflow 自动写的 |
| 内容完全相同的 chunk 组 | 917 组，515 组来自 Notes；9、10 月仍在新增 |
| 盲评 有用 / 噪音 | backflow 写的 34% / 42%；人写的 42% / 28% |

根因：文件名是 `日期-slug-内容哈希6位`，"已存在就跳过"只看这个文件名。backflow 每次会话结束都重扫
整个会话，一个跨天的长会话每关一次，同一条回复就以当天日期再存一份（例：`werner-vogels-…-1cfc2f`
连续 5 天）。去重用的是"内容 + 日期"，而"同一份产出"本该由**生产者给的 id**（消息 id）决定。

### W2 DailyActivity 记录了被删除的冒烟会话和不属于任何条目的块

| 实测（10 月） | |
|---|---|
| 测试探针条目 | 23 条（`Reply with exactly one word: SMOKE_OK`、`Count slowly from 1 to 40`），都来自 `scripts/smoke_e2e.py` |
| 不是条目头的 `##` 块 | 例：`2026-10-14.md` 里一个独立的 `## Session Resume` 块（`context_injector` 的段标题），切块后单独成一个 chunk |

根因：
- `smoke_e2e.py` 跑完会删除自己的会话（`DELETE /api/chat/sessions/{id}`），但会话结束的 hook
  仍然拿到了消息、写了条目。写入时没有检查"这个会话还在不在"。
- DailyActivity 文件里的块格式只靠写入者自觉。切块按任意 `##`/`###` 切，所以一个混进来的 `##` 块
  会变成独立 chunk。这个 `## Session Resume` 块是哪个写入者写的，代码里还没找到，实施时先追到
  写入点，再决定修法，不按内容猜。

另外看了只有"What happened"（用户原话）、没有 LLM 提炼的条目：7 天内的有用率 79%，
有提炼的 98%；7 天以前的都低（11% / 28%）。原话条目对"刚才在干嘛"有用，**不删也不改写**；
旧条目的噪音由读取侧的时间规则处理。

### W3 切块把 frontmatter 当正文，chunk 不带文档标题

| 实测 | |
|---|---|
| 以 YAML frontmatter 开头的 chunk | 897 个；抽查 400 个，约 40% 除了 frontmatter 没有正文 |
| 盲评 有用 / 噪音 | frontmatter 开头的 28% / 38%；其余 44% / 28% |
| 少于 200 字的库命中 | 有用 14%，噪音 55%；少于 80 字的 chunk 有 507 个 |

根因：`chunk_markdown` 把第一个标题之前的内容（frontmatter + H1）整段作为一个 chunk；其余 chunk
只带自己的 `##`/`###` 小标题，不带文档标题。于是 "### Summary" 这种 chunk 被召回后，模型看不出
它属于哪份文档；只有 frontmatter 的 chunk 匹配上 `title`/`related` 里的词就被注入。

### W4 规则散落，同一类来源在两处各自判断

见 §1 末尾。新增一类来源（比如以后的 `Meetings/`）要记得改两个模块；改漏一处就是不一致。

### 不属于本设计的观察

- 过去会话命中里常出现 `Running: grep …` 这类工具摘要，是因为会话内消息按"命中词数"排序。
  盲评中工具摘要占多数的命中有用率（37%）和其余持平，先不改。
- Pollinate（有用 21%、噪音 57%）、`Knowledge/Archives/`（不含 `.context`；11% / 74%）样本都很少
  （28 个、19 个）。按"不因为样本噪音就排除整类来源"的原则，不排除，交给读取侧规则，继续观察。

## 3. 设计

### S1 来源策略：一个模块，一张表

新建 `backend/core/recall_sources.py`，是"这类来源怎么对待"的唯一来源：

```python
@dataclass(frozen=True)
class SourcePolicy:
    prefix: str             # 相对 Knowledge 索引根的路径前缀，最长前缀优先
    index: bool             # 进不进 knowledge_fts
    auto_inject: bool       # 会话 recall 能不能自动注入（主动搜索不受影响）
    age_rule: bool          # 是否走"N 天以前要命中更多词"的时间规则
    date_from: str          # 日期来源："filename" | "frontmatter" | "none"
    own_slots: bool         # 是否单独占名额和预算（归档）
    chunk_mode: str         # "section" | "entry"（按条目头切）

def policy_for(source_file: str) -> SourcePolicy: ...
```

表里的行（初值 = 今天已经生效的行为，搬过来不改语义）：

| prefix | index | auto_inject | age_rule | own_slots | chunk_mode |
|---|---|---|---|---|---|
| `DailyActivity/` | 是 | 是 | 是（文件名日期） | 否 | entry |
| `Archives/DailyActivity/` | 是 | 是 | 是 | 否 | entry |
| `.context/Archives/` | 是 | 是 | 否 | 是 | entry |
| `Signals/`、`Archives/Signals/`、`DailyBriefs/` | 是 | 否 | 否 | 否 | section |
| `JobResults*`（任意层） | 否 | — | — | — | — |
| 其余 | 是 | 是 | 否 | 否 | section |

消费者：
- `knowledge_store.sync_knowledge_index`：用 `index` 决定扫不扫；用 `chunk_mode` 选切法。
  `_SKIP_DIRS`（`__pycache__`、`.git`、`.artifacts`）是文件系统层面的跳过，留在原处。
- `recall_filter`：`auto_inject` 替代 `NO_INJECT_PREFIXES`；`age_rule` + `date_from` 替代 `_DAILY_RE`；
  `own_slots` 替代 `ARCHIVE_PREFIX`。

删除那些常量后，搜索全仓库确认没有别的读者。新增一类来源只改这张表，加一行测试。

### S2 写入者按生产者身份去重

**backflow**（W1）：同一条 assistant 消息只产出一个 Note。
- 文件名改为 `<首次写入日期>-<slug>-<消息 id 前 8 位>.md`；frontmatter 增加 `source_message: <消息 id>`。
- 写之前用 `glob("*-<id8>.md")` 查这条消息是否已经存过，存过就跳过，不管是哪天写的。
- 内容哈希不再参与判断。同一条消息之后被编辑过，仍然算同一份，不重复写。
- 已有的 419 个旧文件不改名、不删除。它们之间的重复由 S4 在读侧折叠。

**DailyActivity**（W2）：
- 会话结束 hook（DailyActivity 和 backflow 两个）开头先查 `sessions` 表：会话已被删除就不写，
  并打一条 INFO 日志。冒烟测试删除自己的会话，所以它不会再产生条目。这是按"会话是否存在"这个事实判断，
  不靠消息内容（"SMOKE_OK"）识别。
- `upsert_session_block` 是 DailyActivity 的唯一写入口：它断言写入的块以条目头
  `## HH:MM | <sid8> | <标题>` 开头，块内不能再有 `## ` 行（只允许 `###` 及以下），否则拒绝并打 WARNING。
  这样文件里的每个 `##` 都是一个条目头，S3 才能按条目切块。
- 追查 `## Session Resume` 独立块的写入者：如果它不经过 `upsert_session_block`，就让它改走这个入口；
  如果经过，断言会在测试里把它暴露出来。

### S3 一个入库口规范 chunk

`knowledge_store.chunk_markdown` 是所有 Library 文件变成 chunk 的唯一入口，规范在这里做：

1. **frontmatter 不进正文。** 开头的 `---…---` 块解析出来，放进 chunk 的 `metadata`（已有字段），
   不放进 `content`；`title` 用作下一步的文档标题。
2. **每个 chunk 带文档标题。** 文档标题取 frontmatter 的 `title`，没有就取第一个 `# H1`，再没有就用文件名。
   chunk 的 `content` 以一行 `# <文档标题>` 开头（DailyActivity 用 `# <日期>`），后面是原来的小标题和正文。
   这一行也进 FTS，所以按文档名搜能命中它的段。
3. **过短的段并进上一段。** 正文少于 `_MIN_CHUNK_CHARS`（初值 200，按 W3 的盲评数据）的段，
   并入同一文件的上一个 chunk，不单独成块；文件只有这一段时照常成块。不丢内容。
4. **`chunk_mode="entry"` 只在条目头切。** DailyActivity 按 `## HH:MM |` 切，归档按已有的
   `_ARCHIVE_ENTRY_RE` 切；条目内部的 `##`/`###` 不再切开。

这四条改变了 chunk 内容，所以内容哈希会变。`sync_knowledge_index` 按哈希做增量同步，部署后的第一次同步
会把全部 chunk 重写一遍。这是派生索引（来源是磁盘上的 Markdown 文件，文件本身不动），在后台 hook 里跑，
不在 chat 路径上。重写耗时先在 scratch 里对同一份文件建一次索引来测，写进 §8。

### S4 读侧折叠完全相同的内容

`recall_filter` 处理库那一路时，把正文（去掉 S3 加的标题行、空白规整后）完全相同的命中折叠成一个，
保留排名最高的那条，来源列表里注明"另有 N 份相同"。已有的 917 组重复不需要改任何文件就不再重复注入；
S2 生效后新的重复不再产生。

折叠只看"内容完全相同"，不做近似去重：近似判断就是猜，会把不同版本的设计稿错合成一个。

## 4. 每个事实在哪、谁写谁读

| 事实 | 唯一来源 | 写 | 读 |
|---|---|---|---|
| 一类来源怎么对待 | `recall_sources.SOURCE_POLICIES` | 人改表 | `knowledge_store`、`recall_filter` |
| 一个 Note 来自哪条消息 | Note 的 frontmatter `source_message` + 文件名里的 id8 | backflow hook | backflow hook（查重） |
| 会话还在不在 | `sessions` 表 | 会话增删 | 两个会话结束 hook |
| 一个 DailyActivity 条目属于哪个会话 | 条目头里的 sid8 + `da:summary:<sid>` 标记（已有） | `upsert_session_block` | 切块、读侧时间规则 |
| chunk 的文档标题、frontmatter | `knowledge_chunks.content` 首行 / `metadata` | `chunk_markdown` | FTS、渲染 |

没有新增存储，没有新增 endpoint 和 hook。热路径成本：S1–S3 都在后台 hook 和索引同步里；S4 是对已取回的
十几个命中做一次哈希，在内存里完成。都不碰事件循环，也不碰 chat 路径等待的线程池。

## 5. 历史数据

- 不改名、不删除、不改写任何已有的 Note、DailyActivity 文件和会话。
- 已有的重复在读侧折叠（S4）。已有的探针条目留在文件里；它们只和冒烟测试自己的提示词匹配，
  7 天后还要命中 3 个词才会被注入。
- 唯一会重写的是派生的 `knowledge_chunks` / `knowledge_fts`（S3），它由文件重新生成，见 §9 待确认。

## 6. 验收

1. **公平对照**：S3 改的是索引内容，不能拿线上已有的索引和新切法比。做法是用同一份 `Knowledge/` 文件，
   在 scratch 里分别按旧切法和新切法各建一个只含知识表的 sqlite（`knowledge_store` 本身就能对任意连接建表），
   不复制 `data.db`。`recall_replay.py` 增加 `--knowledge-db <path>`，让库那一路读这个 scratch 库，
   过去会话那一路照旧只读 `data.db`。
2. **样本**：第 101–200 个会话（和读取侧验收同一批，便于对照）。
3. **机械指标**（新切法相对旧切法）：
   - 注入里"只有 frontmatter"的 chunk 数降为 0；
   - 注入里内容完全相同的命中数降为 0（S4）；
   - 注入里不带文档标题的库命中数降为 0；
   - 少于 200 字的库命中数明显下降；
   - 注入总量不上升。
4. **盲评**：命中发生变化的消息，改前改后各一份，打乱后由无上下文的子 agent 判定。
   通过条件：噪音比例下降，"需要 recall 且拿到有用命中"不下降。
5. **单测**（每条做 mutation 检查，撤掉修复测试必须变红）：
   - `policy_for` 最长前缀优先；`Archives/Signals/` 不自动注入、`Archives/DailyActivity/` 走时间规则；
   - backflow：同一条消息隔天再跑不产生第二个文件；不同消息即使内容相同也各写一份；
   - 会话已删除时，DailyActivity 和 backflow 都不写；
   - `upsert_session_block` 拒绝不以条目头开头或块内含 `## ` 的内容；
   - `chunk_markdown`：只有 frontmatter 的文件不产生 chunk，frontmatter 进 metadata，每个 chunk 首行是文档标题，
     短段并进上一段，DailyActivity 只在条目头切；
   - 读侧折叠：两个内容相同、来源不同的命中只注入一个。
6. **写入端的线上观察**（部署后，只读）：一周内新增 Note 中同一 `source_message` 出现两次的数量为 0；
   新增 DailyActivity 条目中来自已删除会话的为 0。

## 7. 不做

- **不排除 Pollinate、`Knowledge/Archives/`。** 样本太少（§2），排除整类来源违背"修召回质量，不丢来源"的原则。
- **不改 DailyActivity 的提炼门槛**（现在超过 8 条消息才做 LLM 提炼）。有提炼的条目确实更有用，
  但每个短会话多一次 LLM 调用，值不值得要单独量，不放进这次。
- **不做近似去重。** 只折叠完全相同的内容（S4）。
- **不改过去会话的消息排序**（工具摘要排前，§2 观察）。

## 8. 实施顺序

都在自己的 worktree 里做，每步一个 ff-merge 的 commit，不 push、不 rebuild：

1. S1 `recall_sources` + 把三处常量换成它；行为不变，现有测试全绿即证明。
2. S4 读侧折叠（只动 `recall_filter`，立刻对已有重复生效）。
3. S2 backflow 按消息 id 去重；两个会话结束 hook 检查会话是否存在；`upsert_session_block` 断言；
   追查 `## Session Resume` 块的写入者。
4. S3 切块规范；`recall_replay.py --knowledge-db`；scratch 里建新旧两份索引，量重建耗时。
5. 回放 + 盲评（§6），结果写进 §10。

## 9. 待确认

- S3 部署后第一次索引同步会重写全部派生的 `knowledge_chunks`（由 `Knowledge/` 文件重新生成，文件和会话都不动）。
  这算不算"动历史数据"？如果不接受，S3 只对之后新增或修改的文件生效，旧 chunk 保持原样。

## 10. 验收结果

（实施后填写。）
