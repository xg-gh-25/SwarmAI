---

# AI Agent 不需要 Neo4j — 知识管理的达尔文主义

> 你的 Agent 知识库会膨胀、过时、自相矛盾。你可能想上 Neo4j。
> 别急。先问一个问题：**你的知识需要的是更好的存储，还是一个能"遗忘"的大脑？**

---

## 30 秒白话版：Ontology 是什么 & 我们怎么轻量采用的

**Ontology 白话解释：** 就是"给知识定规矩"——什么东西算一类，类和类之间什么关系，什么时候该扔。

类比：你搬进新家，100 个箱子要整理。不管 = 全堆一屋靠翻。贴标签 = 能分区找。**Ontology = 不光贴标签，还定义了规则：新东西进来系统自己知道该放哪、跟谁有关、过期了怎么扔。**

**我们怎么轻量采用的（不用 Neo4j，~200 行 Python）：**

| 借了什么 | 怎么做的 | 没借什么 |
|---------|---------|---------|
| 固定分类 | 5 种 MECE 类型：guideline / pitfall / decision / model / process | OWL/RDF 形式化语言 |
| 生命周期 | 被引用 → 续命；90天没人用 → 休眠 → 遗忘 | SPARQL 查询引擎 |
| 关系追踪 | YAML 文件存 `applies_to`, `supersedes` 等 10 种关系 | 图数据库（Neo4j/Neptune） |
| 自动分类 | 信号词匹配，首条命中即归类 | 人工标注 + 训练集 |

**一句话总结：传统 ontology = 给知识建博物馆（精确陈列，永不扔）。我们的 ontology = 给知识装免疫系统（能分类、能记住、能遗忘）。**

> 下面是完整技术文章，详细解释为什么这样设计、怎么实现、踩过什么坑。

---

## 你一定遇到过这个问题

你的 AI Agent 跑了 3 个月。积累了 200+ 条"经验教训"。然后你发现：

- 半年前的 best practice 在新版框架上已经是 anti-pattern
- Agent 引用了一条 2024 年的经验来做 2026 年的决策，导致 bug
- 知识库里有 3 条互相矛盾的条目，没人记得哪条是最新的
- 每次 session 启动注入全部知识，token 费用飙升，但大部分知识没用

**知识膨胀不是存储问题。是生命周期问题。**

---

## 你可能想到的方案：上图数据库

Neo4j、Amazon Neptune、Stardog。OWL 定义本体，SPARQL 查询，Cypher 做 traversal。看起来很完美——结构化存储 + 语义查询 + 关系推理。

我们也想过。然后发现三个致命问题：

**1. 维护税太高。** 每次代码变更都要同步 triple。一个 rename 操作在 Neo4j 里是一连串 MATCH + DELETE + CREATE。在 Markdown 里是 `sed`。

**2. 查询成本 > 注入成本。** 1M context window ≈ 750K 字。你的全部知识可能只有 12K tokens。直接注入 system prompt 的成本，远低于维护 RAG pipeline + embedding + retrieval + re-ranking 的成本。

**3. 图数据库不管"死亡"。** Neo4j 只存，不删。节点一旦创建，永远存在。你得自己写定时任务 + 业务逻辑判断 + 版本化清理。而"什么时候忘记"恰恰是最难的决策。

---

## 核心论点：达尔文主义 vs 百科全书

大多数知识管理系统是**百科全书模式**：存储一切，永不删除，检索时再过滤。

我们选择的是**达尔文模式**：知识有生命周期，被使用则强化，不被使用则衰减，最终死亡。

```
百科全书：知识 → 存储 → 永生（直到人工清理）
达尔文：  知识 → 使用 → 强化 ←→ 不用 → 衰减 → 归档 → 遗忘
```

为什么达尔文模式更适合 AI Agent？

1. **Agent 不需要"所有知识"。** 它需要"此刻相关的知识"。一条 6 个月前关于 Python 3.9 的 workaround，在 3.12 环境下不仅无用，而且有害。

2. **遗忘是一种能力，不是缺陷。** 人脑会遗忘，是因为遗忘释放认知资源给更重要的事。Agent 的 context window 是有限的——占位的无用知识，就是在压缩有用知识的空间。

3. **引用 = 价值的自然选择。** 如果一条知识在 90 天内没有被任何 pipeline、任何决策、任何对话引用过——它可能就是不重要的。不需要人判断，系统自己知道。

---

## 怎么实现：三层架构（~1000 行 Python）

别被名字吓到。核心就是：**一个 schema + 一堆带生命周期的条目 + 一个关系文件。**

### 第一层：定义知识怎么组织（Schema）

你需要回答：知识分几类？每类长什么样？

我们用了 5 种类型（MECE——互斥且穷尽）：

| 类型 | 什么时候用 | 例子 |
|------|-----------|------|
| **guideline** | "这样做是对的" | "Atomic commits: one logical change per commit" |
| **pitfall** | "这样做会出问题" | "fcntl.flock on symlinked path = arbitrary file lock" |
| **decision** | "我们选了 A 不选 B" | "Chose Amazon Transcribe over Whisper: existing SSO" |
| **model** | "这个东西的结构是" | "Session states: COLD→STREAMING→IDLE→DEAD" |
| **process** | "这件事的步骤是" | "Release: build→verify→tag→publish" |

**为什么不用自由标签？** 因为标签没有查询合约。你没法说"BUILD 阶段需要所有 guideline + pitfall"——如果每条知识的标签都是自由文本，你怎么写这个 filter？固定类型 = 可编程的查询接口。

Schema 不需要 OWL。一个 Markdown 文件的 section 结构就是 schema。Agent 直接 `Read` 就理解。

### 第二层：每条知识是一个有生命的实体

```markdown
- [pitfall] **Lock fd leaked on exception path** — ... (2026-05-19)
  <!-- ref:3 | last:2026-05-19 | decay:active -->
```

三个字段决定生死：

| 字段 | 含义 | 谁更新 |
|------|------|--------|
| `ref:N` | 被引用次数 | 系统自动（title match in pipeline output） |
| `last:date` | 最后被引用日期 | 系统自动 |
| `decay:state` | active / dormant / archived | 系统自动（每日评估） |

衰减规则：

```
ref:0 + 90天无引用 → dormant（标记⚠️，仍可见但不主动注入）
ref:0 + 180天无引用 → archived（移到归档文件，从活跃集消失）
ref:≥10 → 双倍宽限（180天才 dormant）—— 经典知识更耐衰减
新建 < 30天 → 免疫 —— 给新知识证明自己的时间
```

**关键：这不需要人参与。** 衰减引擎每天自动跑。你不需要"每季度 review 知识库"——系统自己知道谁该活、谁该死。

### 第三层：知识之间的关系

```yaml
# .knowledge-graph.yaml (142 条关系，自动增长)
- s: "Lock fd leaked on exception path"
  p: applies_to
  o: "ddd_orchestrator.py"
  c: "2026-05-19"
  u: "2026-05-19"
```

10 种关系类型（`motivated_by`, `supersedes`, `extends`, `applies_to`, `conflicts_with` 等），全存在一个 YAML 文件里。

**最重要的特性：关系自动生长。** 当系统在处理某个文件时引用了某条知识，自动创建 `applies_to` 关系。下次处理同一个文件时，这条知识自动获得优先级 boost。

用得越多 → 关系越丰富 → 推荐越精准 → 用得越多。飞轮。

---

## 我们踩过的坑（你可以跳过的）

**坑 1：类型分类器的信号词不能用通用词。**

第一版用 "pipeline"、"step"、"→" 作为 `process` 类型的信号词。结果 37/179 条被错分——因为这些词在所有类型的技术文本中都出现。修复：信号词必须是该类型**独有的**（"race condition" → pitfall，"workflow:" → process）。

**坑 2：标题匹配做引用追踪会有 false positive。**

标题 "Build" 会匹配所有包含 "Build" 的文本。修复：8 字符以下跳过，8-20 字符用 word boundary regex，20+ 字符用 substring。

**坑 3：不能让衰减系统有"豁免路径"。**

第一版：如果一条知识没有日期信息，就跳过衰减评估。结果：手动添加的知识永不衰减。修复：无日期 = 视为无限老 → 立即触发衰减。

**坑 4（最贵的）：如果一个 gate 可以因为"数据缺失"而通过，它等于不存在。**

我们的 pipeline validator 检查 "adversarial review 的 tier 是否正确"。但如果整个字段不存在呢？`None` 既不是错误的值，也不是正确的值——它跳过了整个检查。**四次同类 bug** 才意识到：缺失 = 违规，不是豁免。

---

## Compound Loop：为什么知识会越来越好

```
Pipeline 执行 → 产生 lessons → 标记 type → 引用旧知识（ref+1）
                                              ↓
                              自动创建 applies_to 关系
                                              ↓
                              下次处理同模块时 → 相关知识优先
                                              ↓
                              更精准的建议 → 更少的 bug → 更好的 lessons
                                              ↓
                              长期不用的知识 → 自动衰减 → 知识库保持精简
```

**数字证据：**
- 179 条活跃知识（2 个月积累），预计 steady state 80-100 条
- 142 条关系（自动提取，覆盖率 53%，每次 pipeline ~2 条新关系）
- Token 开销：12K（1M context 的 1.2%）
- 人工维护成本：0

---

## 你今天就能做的 3 步

不需要 SwarmAI。核心思想可以 10 分钟内用在任何 Agent 上：

### Step 1: 给你的 lessons 加类型标签（10 分钟）

```markdown
# 之前
- 不要在 async 函数里直接调 subprocess.run

# 之后
- [pitfall] **不要在 async 函数里直接调 subprocess.run** — 会阻塞 event loop (2026-05-02)
```

只需要 `[guideline]` / `[pitfall]` / `[decision]` 三种就够启动。

### Step 2: 加引用计数 + 最后引用时间（30 分钟）

在 Agent 的 post-execution hook 里加一行：当 output 中出现某条知识的标题，`ref += 1`。

```python
for entry in knowledge_entries:
    if entry.title.lower() in agent_output.lower():
        entry.ref_count += 1
        entry.last_referenced = today
```

### Step 3: 写一个 daily cron 做衰减（1 小时）

```python
for entry in entries:
    days_since_ref = (today - entry.last_referenced).days
    if days_since_ref > 90 and entry.state == "active":
        entry.state = "dormant"  # 不再主动注入，但仍可搜索
    if days_since_ref > 180 and entry.state == "dormant":
        archive(entry)  # 移走
```

**这三步就够了。** 关系层（Layer 3）是 advanced——等你有 100+ 条知识且发现"全局热门 ≠ 当前相关"时再加。

---

## 什么时候该用真正的 KG

诚实边界。以下任一条件满足，考虑 Neo4j/Neptune：

| 条件 | 为什么轻量方案不够 |
|------|-----------------|
| 知识量 > 10,000 条 | 超出 context window 有效注入范围，需要 embedding + retrieval |
| 需要 3-hop+ 查询 | "间接影响 module X 的所有决策" 在 list comprehension 里是 O(n³) |
| 多人同时写入 | 文件锁不够，需要事务隔离 |
| 需要自动推理 | "如果 A applies_to B 且 B requires C，则 A 间接 requires C" |

对于 1-3 人 + AI 的团队，这些条件在可预见的未来不会满足。当 179 条知识在 0.1ms 内就能全量扫描时，你不需要索引。

---

## 设计原则（带走这些，忘掉实现细节）

1. **遗忘是功能，不是 bug。** 设计知识系统时，先想"怎么删"再想"怎么存"。
2. **引用 = 自然选择。** 被用的知识自然留存，不用的自然消亡。不需要人做"知识审计"。
3. **固定类型 > 自由标签。** 3-5 种 MECE 类型给你可编程的查询合约。
4. **Schema 应该是 Agent-readable 的。** 如果 Agent 需要专门的 parser 才能读你的知识结构，这个结构太复杂了。
5. **Context window IS your database。** 100K tokens 以下，全量注入 > RAG。
6. **Mechanical > Aspirational。** "每季度 review 知识库" 会被忽略。"每天自动衰减"不会。

---

## 结语

> 对于 AI Agent 的知识管理，问题不是"怎么存更多"——是"怎么确保活着的知识都是有用的"。

一个 90 天没人引用的 best practice，跟一条已删除的知识，对 Agent 的价值是一样的——都是零。区别是前者还在占 token、污染 context、消耗注意力。

把它杀掉。让活着的知识呼吸。

**~1000 行 Python + Markdown + YAML。无外部依赖。今天就能开始。**

---

*作者：XG | SwarmAI — Human directs, AI delivers*
*代码：github.com/xg-gh-25/SwarmAI*
*讨论：github.com/xg-gh-25/SwarmAI/discussions/19*
