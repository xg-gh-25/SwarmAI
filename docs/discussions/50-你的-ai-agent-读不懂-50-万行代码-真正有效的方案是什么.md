---
title: "你的 AI Agent 读不懂 50 万行代码——真正有效的方案是什么"
created: 2026-05-27
updated: 2026-05-27
status: published
---
<!-- GitHub Discussion #50: https://github.com/xg-gh-25/SwarmAI/discussions/50 -->

### 你的 AI Agent 读不懂 50 万行代码——真正有效的方案是什么

每周都能在 Claude Code / Cursor / Aider 的 issue 里看到同一类问题：

> "Agent 改了 `shared-lib/auth.py`，下游 3 个 service 挂了"
> "它删了一个函数，但另一个模块还在调用"
> "AI 写的代码编译过了，集成测试全红"

根因永远是同一个：**你的 agent 没有依赖图。** 它一次读一个文件，看得见定义但看不见调用者，对"改了这里会 break 什么"完全没有概念。

"给它更多 context" 不 scale。50 万行代码 ≈ 200 万 token。就算你有 1M context window，也塞不下全部——而且就算塞下了，注意力在 200K 之后就开始衰减。

这篇文章讲的是：**我们怎么用预计算的代码依赖图，在正确的时间注入正确的上下文。**

---

### 问题，精确定义

| 代码库规模 | Agent 读文件时知道什么 | 它需要知道什么 |
|-----------|---------------------|--------------|
| <5 万行 | 大概率"见过"多数文件 | 舒服 |
| 5-20 万行 | 可能 30% 在 context 里，其余靠 grep | 开始漏 |
| 20-100 万行 | <10% 在 context，grep 结果太多 | **经常改坏东西** |
| 100 万+ / 多仓库 | 当前文件之外完全盲 | **没有工具 = 危险** |

失败模式不是"Agent 写烂代码"，是**"局部正确、全局错误"**。函数编译通过，单测 pass，但另外 5 个模块用旧签名调它——全 break。

---

### 设计抉择：预计算图 vs 实时 Grep vs RAG

| 方案 | 怎么做 | 为什么在大项目失败 |
|------|--------|-----------------|
| **实时 grep** | 每次改代码前 `grep -r "函数名"` | 3-5 秒/次，结果噪音大，分不清调用 vs 定义 vs 注释 |
| **Embeddings/RAG** | 代码切片 → 向量搜索 | 语义相似 ≠ 依赖关系。"相似的代码"不是"会因为你改了而 break 的代码" |
| **代码图** (我们的方案) | 解析一次 → SQLite → 每次读文件时 <50ms 查询 | 精确的 callers/callees，CTE 递归遍历 blast radius |

**核心洞察：** 依赖关系是**结构事实**，不是语义相似度。"谁在调用这个函数？"的答案来自 AST 分析，不是 embedding 距离。RAG 在这里是用错了工具。

---

### 架构：4 层

```
第 1 层：索引 (离线，~30s / 17 万行)
  tree-sitter 解析 → 提取定义 + 调用点
  3 层名称解析：文件内 → 跨文件 → 全局
  输出：SQLite DB (nodes + edges + FTS5)

第 2 层：保鲜 (每个 session，<1s)
  对比 last_indexed_commit vs git HEAD
  <50 个文件变更 → 增量更新 (只重新解析变更文件)
  ≥50 个文件变更 → 后台全量重建

第 3 层：注入 (每次 tool call，<50ms)
  PreToolUse hook：agent 读文件时自动触发
  查询："这个文件里的函数被谁调用了？" + "有没有 dead export？"
  在文件内容之前注入 ~100 token 的依赖上下文

第 4 层：规划 (按需，~200ms)
  blast_radius(changed_nodes, depth=2): 双向 CTE 递归遍历
  "如果我改了 X，什么会 break？" → 影响文件/函数列表
  用于 Pipeline REVIEW 阶段的变更影响分析
```

---

### Agent 实际看到什么

**没有 Code Intelligence:**
```
Agent 读: backend/core/session_unit.py
上下文: [文件内容，850 行]
Agent 的认知: "spawn() 定义在这里"
风险: 不知道还有 5 个文件在调 spawn()，改签名 = 全 break
```

**有 Code Intelligence:**
```
Agent 读: backend/core/session_unit.py
注入上下文 (100 tokens):
  "⚡ session_unit.py: spawn() 有 5 个 callers (session_router.py, 
   lifecycle_manager.py, ...), 2 个 export 零调用 (潜在 dead code)"
文件内容: [850 行]
Agent 的认知: "spawn() 是关键路径——5 个 caller 分布在 3 个模块。
  不能改签名，除非同时更新所有调用方。"
```

注入发生在 agent 处理文件**之前**。它不是"问了才告诉你"——是"你还没开始想，依赖关系已经在面前了"。

---

### E2E Flow：一次 Read 调用的完整生命周期

从 agent 发起 `Read("session_unit.py")` 到 context 注入完成，经过这些步骤：

```
┌─────────────────────────────────────────────────────────────────────┐
│  SESSION START                                                       │
│                                                                      │
│  1. context_health_hook 触发（每个 session 自动执行）                    │
│     └─ _refresh_code_intel()                                         │
│        ├─ freshness.py: 读 graph_meta 表的 last_indexed_commit        │
│        ├─ git rev-parse HEAD → 对比                                   │
│        ├─ 相同 → 跳过（最常见路径，<10ms）                              │
│        ├─ 不同 + <50 files changed → 增量更新                          │
│        │   └─ git diff --name-only <old>..<new> → 只重新 parse 变更文件  │
│        └─ 不同 + ≥50 files → 后台全量 reindex                          │
│                                                                      │
│  2. proactive_intelligence 生成 session briefing                      │
│     └─ get_codebase_summary() → 注入 ~100 tok 概览到 session 开场       │
│        "📦 SwarmAI (Python 94%, TS 6%) 11,682 symbols, 14,743 edges"   │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  DURING SESSION — Agent 决定读一个文件                                  │
│                                                                      │
│  3. Agent: Read("backend/core/session_unit.py")                       │
│     ↓                                                                │
│  4. Claude Agent SDK 在执行 Read 之前，触发 PreToolUse hook 链           │
│     ↓                                                                │
│  5. code_intel_hook 被调用：                                           │
│     │                                                                │
│     ├─ tool_name == "Read"? ✅                                        │
│     ├─ file_path 存在? ✅                                             │
│     │                                                                │
│     ├─ detect_project_from_path(file_path)                            │
│     │   └─ 从路径推断属于哪个 project → "SwarmAI"                       │
│     │                                                                │
│     ├─ _get_or_load_graph("SwarmAI")                                  │
│     │   ├─ 首次: load_project_graph() → 打开 code_intel.db (SQLite)    │
│     │   └─ 后续: 从 session 内存 cache 取（0ms）                        │
│     │                                                                │
│     ├─ _build_context(graph, file_path, project):                     │
│     │   ├─ 相对路径转换: /abs/path → "backend/core/session_unit.py"    │
│     │   ├─ graph.get_nodes_by_file(rel_path) → 该文件的所有 symbols      │
│     │   ├─ graph.count_callers_by_file(rel_path) → 每个 symbol 被调次数  │
│     │   └─ 组装 context string (~100 tokens)                          │
│     │                                                                │
│     └─ 返回:                                                          │
│         {                                                             │
│           "decision": "approve",                                      │
│           "hookSpecificOutput": {                                     │
│             "additionalContext": "📊 Code Intel: session_unit.py\n     │
│               Symbols: 47 (1 class, 38 methods, 8 functions)\n        │
│               Incoming edges: 23 callers on 12/47 symbols\n           │
│               Module: core"                                           │
│           }                                                           │
│         }                                                             │
│                                                                      │
│  6. SDK 将 additionalContext 注入到 agent 的消息流中                     │
│     → agent 看到依赖信息 BEFORE 看到文件内容                             │
│     → 决策时已经知道 blast radius                                      │
│                                                                      │
│  7. Agent 基于 context 做决策：                                         │
│     "spawn() 有 5 个 callers → 不能改签名"                              │
│     "2 个 dead exports → 可以安全删除"                                  │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  SESSION END                                                          │
│                                                                      │
│  8. Agent commit 新代码                                                │
│     ↓                                                                │
│  9. code_change_feed hook 触发                                         │
│     └─ 分析 commit → 检测架构变更 → 生成 DDD 提案                       │
│        (e.g. "new module detected → propose TECH.md update")          │
│                                                                      │
│  10. 下次 session start 回到步骤 1 → graph 增量更新                      │
│      → 新代码的依赖关系 immediately 可查                                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**关键设计点：**
- Hook 注册发生在 `hook_builder.py` — 条件是 `code_intel_enabled=True`（默认开启）
- Graph 是 **per-session lazy-loaded**（第一次 Read 触发加载，后续复用），不是每次 tool call 都开 DB
- 整个注入链 < 50ms（SQLite 索引查询 + 内存 cache）
- Hook 永远返回 `"approve"` — 它只加信息，不阻止任何操作
- 如果 Code Intel 加载失败、DB 不存在、或文件不在任何 project 内 → 静默跳过，零影响

---

### 关键 Design Decisions

#### 1. SQLite，不用图数据库

Neo4j、Dgraph 等对这个场景 overkill。代码图的特点是：读多写少（只有 commit 时写），20 万行项目只需 30-50MB。SQLite + WAL 模式给你：

- 零配置部署（一个文件，不需要服务器进程）
- 索引期间并发读
- 递归 CTE 做图遍历（blast radius）
- FTS5 做符号搜索

**决策：运维简单 > 理论最优。**

#### 2. tree-sitter + regex 兜底

tree-sitter 对 Python/TypeScript/Java/Go 提供精确 AST。但：
- 需要 native binding（平台相关）
- 某些 edge case（装饰器、动态 import）难处理

方案：tree-sitter 优先，失败回退到 regex。Regex 漏掉 ~15% 的边，但定义 100% 准确。**不完美的图 > 没有图。**

#### 3. 注入 100 token，不是 1000

Agent 不需要完整依赖树。它需要：
- 多少人调我（风险指标）
- 哪些模块依赖这个文件（blast radius 提示）
- 有没有 dead code（清理机会）

上下文越多 = 注意力越稀释。我们测了 50/100/200/500 token 注入。**100 是 sweet spot**——够影响决策，不至于淹没原始信息。

#### 4. 按文件原子更新

改了一个文件 → 只删除+重插这个文件的 nodes/edges。用 `BEGIN IMMEDIATE` 事务保证读者永远看不到中间状态：

- 增量索引 O(changed_files)，不是 O(total_files)
- 索引进行中 agent 仍可查询
- 没有"正在索引，请稍等"

#### 5. 用 git SHA 判断新鲜度，不用 mtime

mtime 不可靠（build、touch、编辑器临时文件都会改 mtime）。git SHA 对比：
- 确定性（同一个 SHA = 同一个内容）
- 跨机器一致
- 免费（一次 `git rev-parse HEAD` 调用）

---

### 多包 / Monorepo 场景

单 repo 的按项目图已经很有用。Monorepo 或多包场景的关键扩展是**跨项目边**：

```
packages/shared-lib/     → code_intel_shared.db
packages/billing/        → code_intel_billing.db  
packages/api-gateway/    → code_intel_gateway.db

跨包引用表:
  billing::process_payment() --calls--> shared-lib::validate_token()
  gateway::auth_middleware() --calls--> shared-lib::validate_token()
```

当 agent 要改 `validate_token()` 时：
```
注入: "⚠️ validate_token() 被 2 个其他包调用:
  - billing/process_payment.py:45
  - gateway/middleware/auth.py:23
  Blast radius: HIGH (跨包影响)"
```

这就是价值爆发点。**没有人记得住所有跨包依赖。grep 不可靠。只有 graph 能做到。**

---

### 避坑指南

#### ❌ 不要索引所有文件

跳过：测试文件、生成代码、vendored 依赖、node_modules。它们增加噪音（假 caller），膨胀 DB，且没有架构价值。

```python
_SKIP_PATTERNS = [
    "tests/", "test_", "__pycache__/", 
    "node_modules/", ".venv/", "vendor/",
    "generated/", "*.pb.go", "*_generated.*"
]
```

#### ❌ 不要在每个 tool call 都注入

只对 `Read` 和 `Grep` 注入——这是 agent 即将推理代码的时刻。对 `Write` 或 `Bash` 注入 = 白花延迟。

#### ❌ 不要用 embeddings 回答依赖问题

"哪些文件跟这个语义相似？" ≠ "哪些文件会因为我改了这里而 break。"

一个测试文件和它的实现，embedding 距离最近。它们确实有依赖关系，但那是 `calls`，不是 `is_similar_to`。

#### ❌ 不要在图里存代码内容

诱惑：把函数体存进去做"上下文注入"。别。Agent 会自己 Read 文件——你在重复内容。图存的是**关系**（谁调谁，edges），不是**内容**（函数做什么）。

#### ❌ 不要过度设计 parser

我们最初试图解析所有动态分发、metaclass、装饰器修改的签名。结果：实现时间 ×2，多了 10% 的边，索引慢了 30%。先出 85% 方案。那缺失的 15% 边在 blast radius 分析中极少 matter。

#### ✅ 必须自动保鲜

如果图落后于 HEAD（过时了），它给的答案是错的——比没有图更危险。每次 session start 自动检查 freshness 是非协商的。小改动增量更新，大改动后台全量重建。

#### ✅ 必须让用户看得见状态

我们的底栏显示：`🧠 11,682 | today`——symbol 数 + 最后索引时间。用户一眼知道 Code Intelligence 是否在工作、是否新鲜。如果显示"3d ago" → 点 Reindex。

---

### 生产数据

| 指标 | 值 | 说明 |
|------|---|------|
| 代码库 | 17 万行 (Python 94%, TypeScript 6%) | SwarmAI 项目 |
| 索引 symbols | 11,682 (5660 方法, 4298 函数, 1724 类) | |
| 依赖边 | 14,743 (全部是 `calls` 类型) | |
| DB 大小 | 38 MB | SQLite + WAL + FTS5 |
| 全量索引时间 | ~30s | tree-sitter + 3 层解析 |
| 增量更新 | <5s (≤10 个文件) | 按文件原子替换 |
| 查询延迟 | <50ms | 索引好的 SQLite，连接缓存 |
| 注入大小 | ~100 tokens | 每次 tool call |
| 保鲜检查 | <1s | `git rev-parse HEAD` 对比 |
| 发现的 dead code | 2,090 个未使用 export (18%) | 真实清理机会 |

---

### 什么时候需要这个

**需要:**
- 代码 >20 万行
- 3+ 包/模块互相依赖
- Agent 改过共享接口导致下游 break
- `grep` 返回结果太多反而没用
- 你说过"谁在调这个函数？"但没有立即得到答案

**不需要:**
- 项目 <5 万行（agent 能全部装进 context）
- 单文件脚本或 notebook
- 代码写完就不动了（无维护需求）

---

### 复合效应

Code Intelligence 单独用是个不错的优化。和 **DDD（领域驱动设计文档）** + **自治 Pipeline** 组合，变成结构性能力：

```
Pipeline EVALUATE: 读 code_intel → 知道 blast radius → 正确界定改动范围
Pipeline REVIEW:   用 blast_radius() → 检查所有受影响的 callers → 捕获跨模块 break
Pipeline REFLECT:  写 lesson 到 IMPROVEMENT.md → "下次改名前先查 callers"
DDD Cultivation:   code_change_feed 检测新模块 → 自动提案更新 TECH.md
```

每一层让其他层更有效。图提供结构事实。DDD 提供判断上下文。Pipeline 提供执行纪律。组合起来：agent 把 codebase 当系统理解，不是当文件集合。

---

### 术语表

| 术语 | 全称 | 一句话解释 |
|------|------|-----------|
| **AST** | Abstract Syntax Tree（抽象语法树） | 编译器/解析器把源代码转成的树形结构。每个节点代表一个语法元素（函数定义、变量声明、调用表达式等）。我们用 tree-sitter 生成 AST，从中提取"谁定义了什么"和"谁调用了谁"。 |
| **WAL** | Write-Ahead Logging（预写日志） | SQLite 的并发模式。写操作先追加到日志文件，读操作不阻塞。对我们的场景完美：agent 随时查图（读），只有 commit 时才写入新解析结果。读写互不阻塞。 |
| **CTE** | Common Table Expression（公用表表达式） | SQL 的 `WITH RECURSIVE` 语法，允许一个查询引用自身——用来做图遍历。我们的 `blast_radius()` 用递归 CTE 从一个被修改的节点出发，沿着 edges 表逐层扩展，直到 depth=N，找到所有可能被影响的下游调用者。 |
| **FTS5** | Full-Text Search 5（全文搜索引擎 v5） | SQLite 内置的全文索引扩展。我们用它做符号名模糊搜索——输入 `validate`，瞬间返回所有包含这个词的函数/类/方法名。比 `LIKE '%validate%'` 快 100x+，因为走的是倒排索引。 |

---

### 代码可用性

本文描述的代码图实现是 SwarmAI 核心引擎的一部分。关键文件：
- `parser.py` — tree-sitter AST 提取 + 3 层名称解析
- `graph_store.py` — SQLite 图 + CTE 遍历 + FTS5 + 原子更新
- `freshness.py` — git SHA 保鲜检测
- `code_intel_hook.py` — PreToolUse 注入 (<50ms)
- `codebase_map.py` — session briefing 生成 (~100 tokens)

模式可适配到任何支持 tool-use hooks 的 agent 框架。

---

---

### 扩展阅读（本系列其他 Discussion）

| 主题 | 链接 | 和本文的关系 |
|------|------|-------------|
| **DDD Cultivation — 从工作中生长的领域知识** | [Discussion #9](https://github.com/xg-gh-25/SwarmAI/discussions/9) | Code Intel 是即时视力，DDD 是长期记忆——两者通过 code_change_feed 打通 |
| **Coding as Black Box — 需求进去，交付出来** | [Discussion #4](https://github.com/xg-gh-25/SwarmAI/discussions/4) | Pipeline 的 EVALUATE/REVIEW 阶段依赖 Code Intel 的 blast_radius 做影响评估 |
| **多专家对抗性审查系统** | [Discussion #30](https://github.com/xg-gh-25/SwarmAI/discussions/30) | 对抗性审查 sub-agent 使用 Code Intel 验证"改了这里还有谁会 break" |
| **AI Agent 不需要 Neo4j — 知识管理的达尔文主义** | [Discussion #19](https://github.com/xg-gh-25/SwarmAI/discussions/19) | 为什么选 SQLite 不选图数据库——同一个 design philosophy |
| **SwarmAI 设计哲学 — 自改进系统的六根支柱** | [Discussion #38](https://github.com/xg-gh-25/SwarmAI/discussions/38) | Code Intel 如何作为"第六柱"融入整体自改进架构 |

---

### 附录：graph_store 实际数据样本

以下是从生产环境 `code_intel.db` 导出的真实记录，让你直观感受数据长什么样。

#### code_nodes 表（符号定义）

```sql
-- 一个类
INSERT INTO code_nodes VALUES (
  'backend/core/session_unit.py::SessionUnit',     -- id (file::name)
  'backend/core/session_unit.py',                   -- file_path
  'class',                                          -- node_type
  'SessionUnit',                                    -- name
  338,                                              -- line_start
  3206,                                             -- line_end
  'python',                                         -- language
  1,                                                -- is_export
  0                                                 -- is_entry_point
);

-- 一个方法
INSERT INTO code_nodes VALUES (
  'backend/core/code_intel/graph_store.py::GraphStore.blast_radius',
  'backend/core/code_intel/graph_store.py',
  'method',
  'blast_radius',
  384, 438, 'python', 1, 0
);

-- 一个顶层函数
INSERT INTO code_nodes VALUES (
  'backend/core/prompt_builder.py::PromptBuilder.build_system_prompt',
  'backend/core/prompt_builder.py',
  'method',
  'build_system_prompt',
  533, 898, 'python', 1, 0
);
```

#### code_edges 表（调用关系）

```sql
-- "谁在调用 GraphStore 的方法？" — 这就是 blast_radius 的原材料
INSERT INTO code_edges VALUES (
  'backend/routers/code_intel.py::_run_reindex',                    -- source (调用方)
  'backend/core/code_intel/graph_store.py::GraphStore.set_meta',    -- target (被调方)
  'calls',                                                           -- edge_type
  0.8,                                                               -- confidence
  NULL                                                               -- line_number
);

INSERT INTO code_edges VALUES (
  'backend/core/code_intel_feed.py::detect_tech_drift',
  'backend/core/code_intel/graph_store.py::GraphStore.get_module_map',
  'calls', 0.8, NULL
);

-- 方法内部调用链 — _ensure_spawned() 调用了哪些其他方法
INSERT INTO code_edges VALUES (
  'backend/core/session_unit.py::SessionUnit._ensure_spawned',
  '_spawn',
  'calls', 0.5, 1042    -- line 1042 发起的调用
);

INSERT INTO code_edges VALUES (
  'backend/core/session_unit.py::SessionUnit._ensure_spawned',
  '_crash_to_cold_async',
  'calls', 0.5, 1057
);
```

#### 查询示例：blast_radius（递归 CTE）

```sql
-- "如果我改了 GraphStore.get_module_map()，谁会受影响？"
WITH RECURSIVE affected(node_id, depth) AS (
  -- 起点：被改的节点
  VALUES ('backend/core/code_intel/graph_store.py::GraphStore.get_module_map', 0)
  UNION ALL
  -- 递归：沿着 edges 反向遍历（谁调用了我？）
  SELECT e.source_id, a.depth + 1
  FROM code_edges e
  JOIN affected a ON e.target_id = a.node_id
  WHERE a.depth < 2  -- 最多 2 层
)
SELECT DISTINCT node_id, depth FROM affected WHERE depth > 0;

-- 结果：
-- backend/core/code_intel_feed.py::detect_tech_drift          (depth=1)
-- backend/core/code_intel_feed.py::get_code_coverage_for_health (depth=1)
-- backend/core/code_intel_feed.py::get_test_coverage_for_maturity (depth=1)
```

**这就是 agent 在你改 `get_module_map()` 之前看到的信息——不是猜的，是图算出来的。**

---

*你在大型代码库上用 AI Agent 的体验是什么？试过 graph-based 的方案吗？在评论区说说。*
