---
title: "SwarmAI Recall 架构全景 —— 一个自进化 Agent OS 的 READ 路径"
created: 2026-06-26
updated: 2026-06-27
status: published
---
<!-- GitHub Discussion #80: https://github.com/xg-gh-25/SwarmAI/discussions/80 -->
# SwarmAI Recall 架构全景 —— 一个自进化 Agent OS 的 READ 路径

> _深入剖析 SwarmAI 如何记忆、检索、决定"浮现什么":5 套 recall 子系统、Memory/Knowledge/DDD 的定位、"从硬盘到 OS"背后的设计哲学,以及塑造它的外部工作。_
>
> 配套阅读:[Discussion #59 —— DDD 知识治理(7 类 MECE + 达尔文式衰减)](https://github.com/xg-gh-25/SwarmAI/discussions/59)。
> 已对照 live 代码核实于 2026-06-27(run_1d198980 后续)。**全文标注 Built vs Designed —— 我们不把路线图当成品卖。**
>
> ⚠️ **verified-at 是一个时间戳,不是一个不变量。** 本文初版核实于 2026-06-27 凌晨;同日中午一个 `R2-real` epic(run_e50621b6 / run_77504e11)直接改写了下面 §3.2 关于 ref/decay 的核心叙述。**更新于 2026-06-27 中午**,改动标在 §3.2、§2.1、§6 并以 `[UPDATED 06-27]` 注明。这件事本身就是 §4.4「测量即现实」的活教材:文档把 live 代码核实那一刻是真的,然后代码动了 —— §2 警告的"公式≠接线"陷阱,在本文发布 19 小时后就被它自己的 Gate 套在了自己头上。

---

## TL;DR

1. **Recall 不是一个系统。** 它是 **5 套独立的 recall 子系统**(Knowledge/Library、Memory、Session、Transcript、CodeIntel)—— 每套各自拥有自己的存储 + 检索算法 —— 外加 **1 个只读聚合器**(`recall_multi`)。它们在**两个不同时刻**注入,不是一个。
2. **代码里的公式 ≠ 生产里跑的行为。** Knowledge/Library 真跑 hybrid(vector+keyword)且 live;Memory 有一份一模一样的 hybrid 公式,但**没接线**—— 生产里是纯 keyword。我们把这点说出来,因为把两者混为一谈,正是我们自己规则(R16b)警告的那类 bug。
3. **Memory ≠ Knowledge ≠ DDD。** 三层,一个路由问题:*"换一个项目,这条经验还有用吗?"* 跨项目 → MEMORY;参考事实 → KNOWLEDGE;项目判断 → DDD。
4. **哲学是达尔文式的,不是百科全书式的。** *"能遗忘的系统比只能记住的系统更强。"* 知识有衰减、有 `dormant→archived` 生命周期,闲置 = 自动退场。(**[UPDATED 06-27]** decay 现在是诚实的 `age + evergreen + grace`,**不**再依赖 `ref_count` —— ref 是死信号被解耦了;ref 另接到了 reclaim 保护 + 注入排序。见 §3.2。)
5. **难题不是存储,是 APPLY(应用)。** *"这是一块我很少读的硬盘,配了一个测错东西的自测。"* 整套 recall 架构存在的意义,是闭合"记下了一条教训"和"那个错误停止了"之间的回路。

---

## 1. 心智模型纠正:Recall 不是一个系统

最常见的误解 —— 包括我们自己,直到把代码 trace 了一遍 —— 是以为 agent 有"一个记忆"。SwarmAI 有**五个**,每个有自己的存储、自己的检索算法、自己的注入时机。把"recall 系统"当成单体来推理,会产出修错层的 fix(我们曾为另一个复发 bug 在错误的层打了 33 个补丁;这个教训可以泛化)。

```
                    SwarmAI Recall 架构(READ 路径)

  ┌──────────────── 5 套独立子系统 ────────────────┐   ┌─ 1 个聚合器 ─┐
  │ Knowledge/Library   Memory     Session   Transcript  CodeIntel │   recall_multi   │
  │ FTS5+vector hybrid  hybrid*    msgs_fts  trans_fts   符号图     │   (只读,         │
  │ (LIVE)              (*未接线)  BM25      +vec        +1hop      │    分桶)         │
  └───────────────────────────────────────────────────────────────┘   └──────────────┘
            ▲                                                    ▲
            │ 在两个时刻注入(不是一个):                          │
   ① session 启动(组装好的 prompt)              ② 第一条 user message 之后
      • 11 个 context files(MEMORY 选择性)          (异步, 150ms, desktop)
      • Resume context 20–150K                        • Knowledge hybrid 召回 (8K)
        └ 不调任何 recall 子系统 ——                     • 基于真实 query,而非
          纯机械地从 DB 抽取                              启动时预测的关键词
```

把"两个注入时刻"单独展开,这是最容易被误解的一处:

```
                   ┌─────────────────────────────────────────────┐
                   │           RECALL 的两个注入时刻               │
                   └─────────────────────────────────────────────┘

 Session 启动(system prompt 组装时)        第一条 user message 之后(150ms 异步)
 ────────────────────────────────          ──────────────────────────────────
 • 11 个 context files                      • Knowledge Library 召回 (8K)
   └ MEMORY.md selective injection            └ KnowledgeStore (FTS5+vector)
 • Resume context (20-150K)                   └ TranscriptStore (逐字对话)
   └ checkpoint+结论+工具结果+近30轮            └ Knowledge Graph 实体扩展
   (注意:resume 不调任何 recall 子系统,         (基于真实 query 召回,而非预测关键词)
    纯从 DB messages 机械抽取)
```

---

## 2. 架构全景

### 2.1 五套子系统(以及 wiring 真相)

| 子系统 | 存储 | 算法(代码里) | **Wiring 真相(生产里)** | 拥有文件 |
|---|---|---|---|---|
| **Knowledge/Library** | `knowledge_chunks` + `knowledge_fts`(FTS5 external-content)+ `knowledge_vec`(sqlite-vec, 1024 维 Titan v2) | Hybrid `0.6·vector + 0.4·BM25`,阈值 0.05 | ✅ **Hybrid LIVE** —— `session_router` 传入 `embed_fn`;Bedrock 在线时 vector 激活,挂了优雅降级纯 FTS5 | `knowledge_store.py`, `recall_engine.py`, `embedding_client.py` |
| **Memory(MEMORY.md)** | `memory_entries` + `memory_vec`(sqlite-vec)+ 行内 HTML 注释衰减元数据 | Hybrid `0.6·vector + 0.4·BM25` + 衰减加权,阈值 0.10 | ⚠️ **vector hybrid 纯 keyword** —— 调用方省略 `memory_embeddings`(默认 `False`),hybrid vector 腿*建好但未接线*;且 MEMORY.md ~15K < 30K 阈值 → **全量注入**,选择性打分器今天根本不跑。**[UPDATED 06-27] `ref_count` 信号本身现已接线**(R2-real,run_77504e11)—— 不是接到 decay(见 §3.2),而是接到 (1) `_is_reclaimable_noise` 物理保护 + (2) `get_stage_knowledge` 注入排序,信号源 `.memory-usage.json`(507 keys 真实计数) | `memory_index.py`, `memory_embeddings.py`, `context_recall.py`, `memory_decay.py` |
| **Session(消息)** | `messages_fts`(FTS5 external-content) | FTS5 BM25 + 混合排序 `density·0.4 + recency·0.35 + richness·0.25`,±10 消息窗口 | ✅ Live;过滤 `sent=0`,未 drain 的 pending 消息绝不会作为幻影 context 注入 | `session_recall.py` |
| **Transcript(逐字)** | `transcript_chunks` + `transcript_fts`(FTS5)+ `transcript_vec`(sqlite-vec) | FTS5 + 可选 vector,按 `content_hash` 增量同步 | ✅ FTS5 live;传入 `embed_fn` 时 vector 激活 | `transcript_indexer.py` |
| **CodeIntel** | code graph(符号 FTS) | 符号 FTS + 1-hop caller 扩展 | ✅ graph live | 经 `recall_multi._codeintel_recall` |

**索引清单:** 4 张独立 FTS5 表(`knowledge_fts`、`messages_fts`、`transcript_fts`、code-symbol FTS)+ 3 张 sqlite-vec 表(`knowledge_vec`、`memory_vec`、`transcript_vec`)。

### 2.2 聚合器:`recall_multi`

一个**只读**门面,跨五个域扇出,返回*分桶*结果:

```python
recall_all(query, domains=["context_files","ddd","library","session","codeintel"])
  → BucketedRecall{ buckets, hit_layers }
```

两条承重的安全属性:
- **`allow_embed=False` 默认** → 零 Bedrock embed、零写入。(所以经聚合器到达时,Library 退化为纯 FTS5 —— hybrid 路径是 `session_router` 的直连路由。)
- **`policy_excluded_files` 隐私门贯穿所有域** —— 这堵住了一个真实漏洞:`--domains` 曾能绕过 `--file` 强制的隐私(被一个 adversarial 门抓到,run_4358cc95)。

### 2.3 让人意外的注入不对称

**Resume context 不调任何 recall 子系统。** session 恢复时(20–150K tokens 的 context),`context_injector.build_resume_context()` 纯机械地从 DB `messages` 表抽取 checkpoint / 助手结论 / 关键工具结果 / 近 30 轮。它**不调** `session_recall`、`knowledge_store` 或 `memory_index`。Recall 是*增强*,绝不在 resume 关键路径上。这是刻意的:resume 必须确定性且离线安全;recall 是尽力而为,可以失败而不破坏连续性。

---

## 3. 定位:Memory vs Knowledge vs DDD

三层,一个路由问题。出自 `s_persist` 的路由树,决定性测试是:

> **"换一个项目,这条经验还有用吗?"**
> **YES → MEMORY.md**(跨项目的认知知识) · **NO → Projects/<X>/...**(项目内的 DDD)

| 层 | 是什么 | 在哪 | 用途 |
|---|---|---|---|
| **MEMORY** | 跨 session 召回 —— *"我做了什么、用户说了什么、上次什么管用"* | `MEMORY.md`、`DailyActivity/`、`EVOLUTION.md` | 工作记忆、会话连续性、模式检测 |
| **KNOWLEDGE** | 参考事实 —— *"东西怎么工作、我该知道什么"*(非行为特定) | `KNOWLEDGE.md` + `Knowledge/` 库 | 教科书:架构参考、领域事实、学到的外部材料 |
| **DDD** | 项目专业知识 —— *"对这个项目什么重要"* | 每项目 `PRODUCT.md` / `TECH.md` / `IMPROVEMENT.md` / `PROJECT.md` | 判断("该不该做?")、设计("怎么做?")、上下文("这里什么独特?") |

### 3.1 7 类知识治理(MECE + 达尔文式)

每条存储的条目都是 **7 个互斥且穷尽(MECE)的类型**之一([PRI01, Discussion #59](https://github.com/xg-gh-25/SwarmAI/discussions/59)):

`principle` · `correction` · `decision` · `guideline` · `pitfall` · `process` · `model`

这个分类不是装饰 —— 它驱动*路由*(新条目落在哪)和*生命周期*(怎么衰减)。关键在于,**WHERE(去哪)和 WHAT/寿命 是刻意分开的**:

> *"`persist_routing` 和 `ddd_entry_lifecycle` 是两个正确分离的关注点……`persist_routing` = 新知识去哪。`ddd_entry_lifecycle` = 一条存储条目是什么(它的 7 类)以及活多久(ref_count → 衰减)。把它们分开是正确的设计,不是缺陷。"_ —— Ingestion Governance 设计(2026-06-26)

### 3.2 达尔文式衰减:知识必须自己淘汰自己

> **知識必須自己淘汰自己。積累不是智慧。** 達爾文進化的核心不是"記住更多",是"淘汰不適應的"。我們的知識有 ref_count、有 decay、有 dormant→archived 生命週期。**90 天不被引用 = 自動退場。不靠人 maintain,靠使用頻率自然選擇。能遺忘的系統比只能記住的系統更強。** —— PRODUCT.md, 设计哲学

机制上:条目携带 `<!-- ref:N | last:DATE | decay:STATE -->`。闲置条目滑向 `active → dormant → archived`;被取代的条目在选择中打 `0.1×` 分而非删除。

> **[UPDATED 06-27] 重要纠正 —— `ref_count` 已与 decay 解耦。** 本文初版描述"衰减遵循艾宾浩斯曲线 + 赫布式增强,`ref_count` 延长稳定性"。同日 Gate-2 adversarial(run_e50621b6)**实证证伪**了这条:`memory_decay.bump_entry_references` 写的是 5-field `sessions:N` 注释,而 decay 引擎的 `_META_RE` 只解析 body 条目上的 4-field 注释 —— **body decay 的 `ref_count` 是个死输入**,零生产 producer 喂它,被冻结在历史 prose 残值上(`DISCUSSION ref:1010` 还在白拿 2× grace)。因此 `e129569c` 把 ref 从衰减判定里**彻底移除**:删掉 `assess_decay` 的 HIGH_REF 2×-grace 分支、`is_keep_class` 的 `ref>=2` keep-leg。**现在 decay 是诚实的 = `age + evergreen-section + grace`,全部可观测** —— 不再依赖任何隐式信号。这恰恰是本文 §2/§4.4 反复强调的原则,只是这次轮到我们自己被它纠正。

让它运转的设计原则:

> *"机械的 > 理想的。'每季度审查知识库'会被无视。'每天自动衰减'不会。"_ —— Lightweight Ontology 文章

---

## 4. 设计哲学

### 4.1 从硬盘到 OS

组织整个系统的框架:

> **認知是操作系統,知識是硬盤。硬盤滿了但 OS 有 bug = 輸出仍然錯。我們打的是 OS 補丁。** —— PRODUCT.md

以及驱动 recall 工作的诚实诊断:

> *"我有一个丰富的自我知识语料库……但语料库到不了我的工作记忆,eval 打 100 分而我重复犯错,'记下了教训'和'错误停止了'之间没有东西闭合回路。**这是一块我很少读的硬盘,配了一个测错东西的自测。**"_ —— Self-Knowledge Loop 设计(2026-06-25)

### 4.2 6 链回路 —— recall 是一环,不是终点

Recall(第 ④ 环)只有当电流绕完整圈、且*纠错计数下降*时才有意义:

```
 ① 抽取  ─→ ② 清洗  ─→ ③ 注入  ─→ ④ 召回  ─→ ⑤ 应用
 (工作→教训)(剪枝/    (→ prompt)  (对的那条, (改变下一个
              归档)                对的时机)   动作)
     ▲                                            │
     └──────────── ⑥ 测量(仪表) ◄────────────────┘

 定律:
 • 测不了的环,默认是坏的。
 • 机械信号是主;LLM 判断是辅;人类锚点校准两者。
 • "我用了知识"的证明是机械的(门触发日志、下降的纠错计数、
   回放用例通过)—— 绝不是口头承诺。
```

经验上最坏的一环是 **⑤ 应用** —— 不是存储,不是检索。一条教训可以在 context 里却仍不改变行为。这就是为什么 SwarmAI 依赖*机械门*而非提醒。

### 4.3 可逆,绝不丢弃

我们明确采纳的一条原则,以及我们看着别人犯了又退役的一个错误:

> *"可逆,绝不丢弃:Headroom 退役了 score-and-drop,因为静默丢失侵蚀信任 + 破坏缓存。我们把原文留在 store 里;模型拿到一个 handle,可以按需召回完整输出。"_ —— Context Economy 设计(2026-06-26)

检索质量的推论:**召回绝不能摘要** —— *"不做摘要 —— 那是静默降智,禁止。"* 召回返回一个**可逆的精确切片**,以 `## section` 为粒度,按内容查询(绝不按会被蒸馏失效的过期偏移量)。

### 4.4 测量即现实

> **測量不了的,等於沒造。** 沒有度量的"自我改進"是故事。不聲稱收斂——用 git 裡的數據證明。 —— PRODUCT.md

---

## 5. 方法论 —— 我们如何决定召回什么

### 5.1 召回链(目标架构,部分已建)

```
1. INDEX-FIRST   — 读常驻的 cache+index(Karpathy LLM-wiki 模式)
2. DRILL         — 渐进、按需、多域并行;单元 = ## section
3. RETRIEVE      — 可逆的精确切片(逐字;绝不释义/摘要)
4. PRESENT       — 按域分桶;标注跨域关联
5. EMIT HIT-LOG  — {query, hit-layer(hot|index|drill), section, domain, drilled?}
```

### 5.2 跨域排序是分桶的,绝不全局混排

> *"各域的相关性分数不可比(量级/语料不同)—— 全局混排会让一个关键词密集的域淹没一个更相关的域……每域等额配额(ceiling / N_active),桶按激活顺序排列,绝不按任何跨域分数。"_ —— E2E Recall 设计

### 5.3 keyword 和 vector 是互补的 —— 杠杆是可比性,不是比例

调任何东西之前,我们先跑了一个 spike(12 个同义词/CJK 偏移 query 打 live `memory_vec`):

> *"决定性结论:keyword 和 vector 是互补的,谁都不占优。精确词 query → keyword 赢;概念/CJK query → vector 赢;没有单条腿是够的。**杠杆是让两条腿可比,而不是调比例。**"_ —— E2E Recall 设计

以及一个 cargo-cult 守卫,因为不带机制照搬一个魔数是没意义的:

> *"⚠️ 0.6/0.4 这个数字只有和 MemPalace 验证它时的三样东西捆绑才有效 —— 三样全移植,否则比例无意义:(1) 真正的 Okapi-BM25+IDF keyword 腿,(2) 只对 BM25 腿做 min-max 归一,vector 腿保持绝对值,(3) vector 缺失 → 重归一到可用腿,而不是打 0 分。"_

最后那条规则 —— **vector 缺失重归一到可用腿,绝不打 0 分** —— 正是让*惰性 embedding* 安全的关键。

### 5.4 惰性按面 embedding,而非大爆炸式索引

> *"读路径上惰性按面 embedding……一个 DDD section 或 KNOWLEDGE 条目,在召回第一次 drill/命中它时才被 embed(信号 = hit-log)—— 不靠批量 index-everything job。已 embed 单元的语料沿访问前沿生长,所以语义覆盖恰好在 query 真正落地处扩展,绝不做前置全扫。这和 L1 cache 是同一个达尔文式'用什么暖什么'原则。"_ —— E2E Recall 设计

### 5.5 理由是能力,不是省 context

我们明确表态*不*去解一个我们没有的问题:

> *"❌ 不是省 context window。我们跑 1M context @ ~50% 利用率;truncation 在生产里从没触发过。任何'压缩省 context'的理由都是把[某工具]的解决方案套到我们没有的问题上。✅ 真正的理由:跨域能力(今天缺失)、检索质量天花板(纯 keyword 没有同义/语义召回)、DDD 可提取性('被用的时候就应该很容易提取')。"_ —— E2E Recall 设计

### 5.6 在产出时压缩,绝不回溯压缩

对工具输出压缩(一个兄弟 READ-路径关注点),缓存安全规则:

> *"PostToolUse 压缩发生在输出被缓存之前 —— 压缩后的形态就是第一次被写入的。没有缓存前缀需要失效 → 零缓存代价。设计必须在产出时(PostToolUse)压缩,绝不回溯。"_ —— Context Economy 设计

---

## 6. Built vs Designed(诚实章节)

我们拒绝把路线图当成品来呈现。

| 能力 | 状态 |
|---|---|
| Knowledge/Library hybrid 召回(FTS5 + vector),live `embed_fn`,优雅降级 | ✅ **已建 + 已部署** |
| `knowledge_fts` external-content 损坏根因修复 + 自愈探针 | ✅ **已建 + 已部署**(run_1d198980,正是这次触发了本文) |
| Session / Transcript FTS5 召回;CodeIntel 符号图 | ✅ **已建** |
| `recall_multi` 只读 5 域聚合器 + 隐私门 | ✅ **已建** |
| `recall_context` 可逆的 section 粒度召回(用于被排除的 MEMORY sections) | ✅ **已建** |
| Memory 选择性注入的 **hybrid vector 腿** | ⚠️ **建好但未接线** —— 生产纯 keyword;且因 MEMORY.md < 30K → 全量注入而休眠 |
| **[UPDATED 06-27] Memory `ref_count` 作为活的使用信号**(usage → reclaim 保护 + 注入排序) | ✅ **已建**(R2-real,run_77504e11)—— `.memory-usage.json` 接到 `_is_reclaimable_noise` + `get_stage_knowledge`;**注意:已提交,部署待确认**(二进制 mtime 11:46 早于 bridge commit 12:08) |
| 处处跨域*语义*召回;惰性按面 embedding;持久化 hit-log 驱动达尔文衰减 | 📐 **已设计,部分** —— e2e 设计存在;完整接线是开放工作 |
| 工具输出压缩(Read+Bash,产出时,可逆) | 📐 **已设计**(spike 完成,BUILD 结论,尚未 ship) |

**已知债务,标注而非隐藏:**
1. ~~`transcript_indexer.upsert_chunk` 携带与损坏 `knowledge_fts` 同一类的 external-content 写 bug。~~ **[UPDATED 06-27] 已修 + 加 heal path**(`ccd9258c`,run_f2ae50b3)—— FTS5 `'delete'` 现绑旧值,与 `knowledge_fts` 同源修复。✅
2. **同一个 `0.6·vector + 0.4·keyword` hybrid + min-max 重归一有两份独立实现**(Knowledge 在 `recall_engine.py`,Memory 在 `memory_embeddings.py`)。重复逻辑 → 合并候选。**仍开放**(见 §8 开放问题 2)。
3. **[UPDATED 06-27] `ref_count` 使用信号是全时段累计的单向棘轮** —— 一旦热过就永久受保护。`ff848626` 用 `(section,title)` 键修了 title-collision 的急性风险,但 recency-windowing 是独立的信号质量 epic(进行中)。

---

## 7. 参考 Sources —— 什么塑造了这套架构

| Source | 是什么 | 链接 | 我们借了 | 我们拒绝/不同 |
|---|---|---|---|---|
| **Karpathy —— LLM Wiki** | 增量构建并维护一个持久、互链的 wiki,而非每次 query RAG。三层:raw sources → wiki → schema。血脉:Vannevar Bush 的 **Memex**(1945)。 | [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | "index-first → drill-on-demand → file-back";持久复利 > RAG;三层映射(Knowledge/ → MEMORY+EVOLUTION+DDD → AGENT/SOUL/STEERING) | —— |
| **MemPalace** | 验证过的 hybrid 排序:`0.6 vector / 0.4 keyword`,配 Okapi-BM25+IDF、只对 BM25 腿 min-max、vector 缺失重归一。 | _(配方已移植)_ | **完整配方**,不只是数字(见 §5.3) | 不带 3 件机制照搬比例(显式 cargo-cult 守卫) |
| **Headroom** | 本地优先的 context 压缩层;熵保留;cache-aligner;**退役了 score-and-drop**,改为可逆的 live-zone 压缩。 | [github](https://github.com/headroomlabs-ai/headroom) | "可逆,绝不丢弃";熵保留(绝不切分 `run_*` / SHA / 路径);缓存稳定前缀 | 压缩本身 —— 我们 1M @ ~50%,无 context 压力(套用它的 fix = 解一个我们没有的问题) |
| **Lightweight Ontology**(内部) | 达尔文 vs 百科全书的知识模型;MECE 类型 schema + 衰减生命周期 + 关系层,~1000 行,无图数据库。 | _(内部文章)_ | 类型 schema + 衰减生命周期规则;"机械的 > 理想的" | Neo4j/Neptune 开销 —— 改用 YAML 关系 + Markdown schema |
| **Ontology vs Knowledge Graph** | Ontology = schema/规则层;KG = 数据/实例层(DDL vs DML 类比)。 | [文章](https://www.toutiao.com/article/7618030452531610164/) | schema-before-data;DDD 4 文档 ≈ 轻量本体 | 正式 OWL / SPARQL 复杂度 |
| **Amazon Quick —— Desktop KG** | 从 Slack/Email/文件构建个人知识图,存本地 SQLite(非 Neo4j);10 种实体类型;PageRank 排层级。 | [aws docs](https://docs.aws.amazon.com/quick/latest/userguide/knowledge-graph-desktop.html) | 小规模下 SQLite 优于图数据库;"Defined Term"自动术语表想法 | 多人组织图 / 被动摄取 —— 不同问题类(只读增强 vs 读-写-进化) |

---

## 8. 开放问题(真心想听听意见)

1. **纯 keyword 召回到底有没有漏掉真实 query?** 我们自己的门允许返回 NO:只有当 ≥20% 的真实 query 出现 keyword 假阴性、而 hybrid 能正确捕获、且 hybrid 假阳性 < 10% 时,才建"处处语义"的路径。低于这个 → keyword 够用,别建。你会怎么在自己的语料上测这件事?
2. **两份 hybrid 实现该在语义接线之前还是之后合并?**(先合更干净,但要动一条已部署路径。)
3. **hit-log 该住在哪**,以及一个驱动衰减的持久化 hit-log 会不会造成反馈回路(热门条目被更多 embed → 更多召回 → 更热门)?

---

_在开放中构建。上面是 READ 路径;WRITE 路径(摄取治理、7 类路由、达尔文衰减)是 [Discussion #59](https://github.com/xg-gh-25/SwarmAI/discussions/59)。Recall 是 6 链回路中的一环 —— 而一个你测不了的环,默认是坏的。_

🐝 SwarmAI —— Your AI Team, 24/7。Human directs, AI delivers.
