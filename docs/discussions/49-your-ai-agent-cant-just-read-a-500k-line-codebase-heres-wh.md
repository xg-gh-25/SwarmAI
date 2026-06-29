---
title: "Your AI Agent Can't \"Just Read\" a 500K-Line Codebase — Here's What Actually Works"
created: 2026-05-27
updated: 2026-05-27
status: published
---
<!-- GitHub Discussion #49: https://github.com/xg-gh-25/SwarmAI/discussions/49 -->

### Your AI Agent Can't "Just Read" a 500K-Line Codebase — Here's What Actually Works

Every week I see the same issue on Claude Code / Cursor / Aider GitHub issues:

> "Agent modified `shared-lib/auth.py` and broke 3 downstream packages"
> "It removed a function that was still being called from another module"
> "AI generated code that compiles but breaks integration tests"

The root cause is always the same: **your agent has no dependency graph.** It reads files one at a time, sees definitions but not callers, and has no concept of blast radius.

"Just give it more context" doesn't scale. A 500K-line codebase at ~4 tokens/line = 2M tokens. Even with 1M context, you can't load everything — and even if you could, attention quality degrades past ~200K tokens.

This post describes how we solved this with a **pre-computed code graph** that injects exactly the right context at exactly the right time.

---

### The Problem, Precisely

| Codebase Size | What agent knows when reading a file | What it needs to know |
|--------------|--------------------------------------|----------------------|
| <50K LOC | Probably "seen" most files in context | Comfortable |
| 50-200K LOC | Maybe 30% loaded, can grep the rest | Starting to miss things |
| 200K-1M LOC | <10% in context, grep returns too many results | **Regularly breaks things** |
| 1M+ / multi-repo | Completely blind outside current file | **Dangerous without tooling** |

The failure mode isn't "agent writes bad code." It's **"agent writes locally correct code that is globally wrong."** Function compiles, tests pass for that file, but 5 other modules call it with the old signature.

---

### The Design Decision: Pre-Computed Graph, Not Runtime Grep

We evaluated 3 approaches:

| Approach | How it works | Why it fails at scale |
|----------|-------------|----------------------|
| **Runtime grep** | `grep -r "function_name"` every time | 3-5s per query, noisy results, no semantic understanding of call vs definition vs comment |
| **Embeddings/RAG** | Chunk code → vector search | Semantic similarity ≠ dependency. "Similar code" is not "code that calls you" |
| **Code graph** (what we built) | Parse once → SQLite → query at tool-call time | <50ms per injection, precise callers/callees, blast radius via CTE traversal |

**Key insight:** dependency relationships are **structural facts**, not semantic similarity. "Who calls this function?" is answered by AST analysis, not embedding distance. RAG is wrong-tool-for-the-job here.

---

### Architecture: 4 Layers

```
Layer 1: INDEXING (offline, ~30s for 170K LOC)
  tree-sitter parse → extract definitions + call sites
  3-layer name resolution: per-file → cross-file → global
  Output: SQLite DB (nodes + edges + FTS5)

Layer 2: FRESHNESS (per-session, <1s)
  Compare last_indexed_commit vs git HEAD
  <50 files changed → incremental update (re-parse only changed files)
  ≥50 files changed → emit background full reindex job

Layer 3: INJECTION (per-tool-call, <50ms)
  PreToolUse hook fires when agent reads a file
  Query: "who calls functions in this file?" + "any dead exports?"
  Inject ~100 tokens of dependency context BEFORE the file content

Layer 4: PLANNING (on-demand, ~200ms)
  blast_radius(changed_nodes, depth=2): bidirectional CTE traversal
  "If I change X, what breaks?" → list of affected files/functions
  Used by Pipeline REVIEW stage for change impact analysis
```

---

### What the Agent Actually Sees

**Without Code Intelligence:**
```
Agent reads: backend/core/session_unit.py
Context: [file content, 850 lines]
Agent's knowledge: "spawn() is defined here"
Risk: Doesn't know 5 other files call spawn() with specific arg expectations
```

**With Code Intelligence:**
```
Agent reads: backend/core/session_unit.py
Injected context (100 tokens):
  "⚡ session_unit.py: spawn() has 5 callers (session_router.py, 
   lifecycle_manager.py, ...), 2 exports have zero callers (potential dead code)"
Context: [file content, 850 lines]
Agent's knowledge: "spawn() is critical — 5 callers across 3 modules. Cannot change signature without updating all."
```

The injection happens **before** the agent processes the file. It's not asking for context — it ALREADY HAS the context when it starts reasoning about the code.

---

### E2E Flow: Complete Lifecycle of a Single Read Call

From the moment an agent issues `Read("session_unit.py")` to the injected context arriving — here's every step:

```
┌─────────────────────────────────────────────────────────────────────┐
│  SESSION START                                                       │
│                                                                      │
│  1. context_health_hook fires (automatic, every session)             │
│     └─ _refresh_code_intel()                                         │
│        ├─ freshness.py: reads last_indexed_commit from graph_meta     │
│        ├─ git rev-parse HEAD → compare                               │
│        ├─ Same → skip (most common path, <10ms)                      │
│        ├─ Different + <50 files changed → incremental update          │
│        │   └─ git diff --name-only <old>..<new> → re-parse only those │
│        └─ Different + ≥50 files → background full reindex            │
│                                                                      │
│  2. proactive_intelligence builds session briefing                    │
│     └─ get_codebase_summary() → ~100 tok overview in session start   │
│        "📦 SwarmAI (Python 94%, TS 6%) 11,682 symbols, 14,743 edges" │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  DURING SESSION — Agent decides to read a file                       │
│                                                                      │
│  3. Agent: Read("backend/core/session_unit.py")                       │
│     ↓                                                                │
│  4. Claude Agent SDK fires PreToolUse hook chain BEFORE executing     │
│     ↓                                                                │
│  5. code_intel_hook is called:                                        │
│     │                                                                │
│     ├─ tool_name == "Read"? ✅                                        │
│     ├─ file_path exists? ✅                                           │
│     │                                                                │
│     ├─ detect_project_from_path(file_path)                            │
│     │   └─ Infer which project owns this path → "SwarmAI"            │
│     │                                                                │
│     ├─ _get_or_load_graph("SwarmAI")                                  │
│     │   ├─ First time: load_project_graph() → open code_intel.db      │
│     │   └─ Subsequent: return from session-level memory cache (0ms)   │
│     │                                                                │
│     ├─ _build_context(graph, file_path, project):                     │
│     │   ├─ Convert to relative path: /abs/path → "backend/core/..."   │
│     │   ├─ graph.get_nodes_by_file(rel_path) → all symbols in file    │
│     │   ├─ graph.count_callers_by_file(rel_path) → caller count/node  │
│     │   └─ Assemble context string (~100 tokens)                      │
│     │                                                                │
│     └─ Returns:                                                       │
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
│  6. SDK injects additionalContext into agent's message stream         │
│     → Agent sees dependency info BEFORE seeing file content           │
│     → Makes decisions with blast radius already known                 │
│                                                                      │
│  7. Agent reasons with full context:                                  │
│     "spawn() has 5 callers → cannot change signature"                 │
│     "2 dead exports → safe to remove"                                 │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  SESSION END                                                          │
│                                                                      │
│  8. Agent commits new code                                            │
│     ↓                                                                │
│  9. code_change_feed hook fires                                       │
│     └─ Analyze commit → detect arch changes → generate DDD proposals  │
│        (e.g. "new module detected → propose TECH.md update")          │
│                                                                      │
│  10. Next session start returns to step 1 → graph incrementally       │
│      updated → new code's dependencies immediately queryable          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key design points:**
- Hook registration happens in `hook_builder.py` — conditional on `code_intel_enabled=True` (default on)
- Graph is **lazy-loaded per session** (first Read triggers load, subsequent calls hit memory cache) — not per-tool-call DB open
- Entire injection chain < 50ms (SQLite indexed query + memory cache)
- Hook ALWAYS returns `"approve"` — it only adds information, never blocks operations
- If Code Intel fails to load, DB doesn't exist, or file isn't in any project → silent skip, zero impact on agent

---

### Design Decisions That Mattered

#### 1. SQLite, not a graph database

Neo4j, Dgraph, etc. are overkill. Code graphs are read-heavy, write-rare (only on commits), and fit in 30-50MB for a 200K LOC project. SQLite + WAL mode gives:
- Zero-config deployment (file-based, no server)
- Concurrent reads during indexing
- Recursive CTE for graph traversal (blast radius)
- FTS5 for symbol search

**Decision: operational simplicity > theoretical graph optimality.**

#### 2. Tree-sitter with regex fallback

Tree-sitter provides accurate AST parsing for Python, TypeScript, Java, Go. But:
- It requires native bindings (platform-specific)
- Some edge cases (decorators, dynamic imports) are hard

Solution: try tree-sitter first, fall back to regex-based extraction. Regex misses ~15% of edges but catches all definitions. **Partial graph > no graph.**

#### 3. Inject 100 tokens, not 1000

The agent doesn't need the full dependency tree. It needs:
- How many callers (risk indicator)
- Which modules depend on this file (blast radius hint)
- Any dead code (cleanup opportunity)

More context = attention dilution. We tested 50/100/200/500 token injections. **100 tokens was the sweet spot** — enough to influence decisions, not enough to overwhelm.

#### 4. Per-file atomic updates

When you modify one file, only that file's nodes/edges are deleted and re-inserted. Uses `BEGIN IMMEDIATE` transaction so readers never see partial state. This means:
- Incremental indexing is O(files_changed), not O(total_files)
- Even during reindex, agents can still query the graph
- No "indexing in progress, please wait"

#### 5. Freshness by git SHA, not mtime

mtime is unreliable (builds, touch, editor temp files). Git SHA comparison is:
- Deterministic
- Works across machines (same SHA = same content)
- Free (single `git rev-parse HEAD` call)

---

### For Multi-Package / Monorepo Projects

The per-project graph works for a single repo. For monorepos or multi-package setups, the key extension is **cross-project edges**:

```
packages/shared-lib/     → code_intel_shared.db
packages/billing/        → code_intel_billing.db  
packages/api-gateway/    → code_intel_gateway.db

Cross-reference table:
  billing::process_payment() --calls--> shared-lib::validate_token()
  gateway::auth_middleware() --calls--> shared-lib::validate_token()
```

When agent modifies `validate_token()`:
```
Injected: "⚠️ validate_token() is called by 2 other packages:
  - billing/process_payment.py:45
  - gateway/middleware/auth.py:23
  Blast radius: HIGH (cross-package)"
```

This is where the value explodes. **No human remembers all cross-package dependencies. No grep catches them reliably. Only a graph does.**

---

### Lessons Learned (Avoid These Mistakes)

#### ❌ Don't index everything

Skip: test files, generated code, vendored deps, node_modules. They add noise (false callers), bloat the DB, and never contain architecturally interesting signals.

```python
_SKIP_PATTERNS = [
    "tests/", "test_", "__pycache__/", 
    "node_modules/", ".venv/", "vendor/",
    "generated/", "*.pb.go", "*_generated.*"
]
```

#### ❌ Don't inject on every tool call

Only inject on `Read` and `Grep` — these are when the agent is about to reason about code. Injecting on `Write` or `Bash` adds latency for zero value.

#### ❌ Don't rely on embeddings for dependency questions

"Which files are semantically similar to this one?" ≠ "Which files will break if I change this."
A test file and its implementation are maximally similar by embedding distance. They have a dependency relationship but it's `calls`, not `is_similar_to`.

#### ❌ Don't store line-level content in the graph

Tempting to store function bodies for "context injection." Don't. The agent will Read the file anyway — you're duplicating content. The graph stores **relationships** (who calls whom, edges), not **content** (what the function does).

#### ❌ Don't over-engineer the parser

Our initial parser tried to resolve all dynamic dispatch, metaclasses, and decorator-modified signatures. Result: 2x implementation time, 10% more edges, 30% slower indexing. Ship the 85% solution. The missing 15% of edges rarely matters for blast radius analysis.

#### ✅ Do track freshness automatically

If the graph is stale (behind HEAD), it gives wrong answers — worse than no graph at all. Auto-freshness check on session start is non-negotiable. Incremental update for small changes, background full reindex for large ones.

#### ✅ Do make it observable

Our BottomBar shows: `🧠 11,682 | today` — symbol count + last indexed time. User can see at a glance whether Code Intelligence is working and fresh. If it says "3d ago" → click Reindex.

---

### Numbers From Production

| Metric | Value | Notes |
|--------|-------|-------|
| Codebase | 170K LOC (Python 94%, TypeScript 6%) | SwarmAI project |
| Symbols indexed | 11,682 (5660 methods, 4298 functions, 1724 classes) | |
| Dependency edges | 14,743 (all `calls` type) | |
| DB size | 38 MB | SQLite + WAL + FTS5 |
| Full index time | ~30s | tree-sitter + 3-layer resolution |
| Incremental update | <5s for ≤10 files | Per-file atomic replace |
| Query latency | <50ms | Indexed SQLite, cached connection |
| Injection size | ~100 tokens | Per tool call |
| Freshness check | <1s | `git rev-parse HEAD` comparison |
| Dead code found | 2,090 unused exports (18%) | Genuine cleanup opportunities |

---

### When You Need This

**You need a code graph when:**
- Your codebase is >200K LOC
- You have >3 packages/modules that depend on each other
- Your agent has broken downstream code by changing shared interfaces
- `grep` returns too many results to be useful
- You've ever said "who calls this function?" and didn't get an immediate answer

**You don't need this when:**
- Your project is <50K LOC (agent can hold it in context)
- Single-file scripts or notebooks
- The codebase is write-once (no ongoing maintenance)

---

### The Compound Effect

Code Intelligence alone is a nice optimization. Combined with **DDD (Domain-Driven Design documents)** and an **autonomous pipeline**, it becomes structural:

```
Pipeline EVALUATE: reads code_intel → knows blast radius → scopes the change correctly
Pipeline REVIEW: uses blast_radius() → checks all affected callers → catches cross-module breaks
Pipeline REFLECT: writes lesson to IMPROVEMENT.md → "next time, check callers before renaming"
DDD Cultivation: code_change_feed detects new module → proposes TECH.md update automatically
```

Each layer makes the other layers more effective. The graph provides the structural facts. DDD provides the judgment context. The pipeline provides the execution discipline. Together: agent that understands the codebase as a system, not a collection of files.

---

### Glossary

| Term | Full Name | What It Does Here |
|------|-----------|-------------------|
| **AST** | Abstract Syntax Tree | A tree representation of source code produced by a parser. Each node = a syntax element (function def, variable, call expression). We use tree-sitter to generate the AST and extract "who defines what" and "who calls whom" — the raw material for our dependency graph. |
| **WAL** | Write-Ahead Logging | A SQLite concurrency mode where writes go to a journal file first, and readers are never blocked. Perfect for our access pattern: agents query the graph constantly (reads), but we only write when new commits are parsed. Zero read/write contention. |
| **CTE** | Common Table Expression | SQL's `WITH RECURSIVE` syntax that lets a query reference itself — enabling graph traversal in pure SQL. Our `blast_radius()` starts from a changed node, walks edges recursively up to depth=N, and returns all downstream callers that might break. No application-level BFS needed. |
| **FTS5** | Full-Text Search 5 | SQLite's built-in full-text indexing extension. We use it for fuzzy symbol search — type `validate` and instantly get every function/class/method containing that token. ~100x faster than `LIKE '%validate%'` because it uses an inverted index under the hood. |

---

### Open Source Status

The code graph implementation described here is part of SwarmAI's core engine. Key files:
- `parser.py` — tree-sitter AST extraction + 3-layer name resolution
- `graph_store.py` — SQLite graph with CTE traversal, FTS5, atomic updates
- `freshness.py` — git SHA-based staleness detection
- `code_intel_hook.py` — PreToolUse injection (<50ms)
- `codebase_map.py` — session briefing generation (~100 tokens)

Pattern is adaptable to any agent framework that supports tool-use hooks.

---

---

### Further Reading (Related Discussions in This Series)

| Topic | Link | Relationship to This Post |
|-------|------|--------------------------|
| **DDD Cultivation — Domain Knowledge That Grows From Work** | [Discussion #9](https://github.com/xg-gh-25/SwarmAI/discussions/9) | Code Intel = instant vision (sees deps while reading). DDD = long-term memory (accumulates across sessions). Connected via code_change_feed. |
| **Coding as Black Box — Requirement In, Delivery Out** | [Discussion #4](https://github.com/xg-gh-25/SwarmAI/discussions/4) | Pipeline's EVALUATE/REVIEW stages use Code Intel's blast_radius for impact assessment before coding starts. |
| **Adversarial Review — Multi-Specialist Quality Gate** | [Discussion #29](https://github.com/xg-gh-25/SwarmAI/discussions/29) | The adversarial sub-agent uses Code Intel to verify "who else breaks if I change this?" |
| **No Neo4j — Lightweight Ontology in Practice** | [Discussion #20](https://github.com/xg-gh-25/SwarmAI/discussions/20) | Why we chose SQLite over graph databases — same design philosophy applies to Code Intel. |
| **Design Philosophy — Six Pillars of a Self-Improving System** | [Discussion #38](https://github.com/xg-gh-25/SwarmAI/discussions/38) | How Code Intel fits as the "structural awareness" layer in the compound self-improving architecture. |

---

### Appendix: Real graph_store Records

Below are actual records exported from our production `code_intel.db` — so you can see exactly what the data looks like.

#### code_nodes table (symbol definitions)

```sql
-- A class
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

-- A method
INSERT INTO code_nodes VALUES (
  'backend/core/code_intel/graph_store.py::GraphStore.blast_radius',
  'backend/core/code_intel/graph_store.py',
  'method',
  'blast_radius',
  384, 438, 'python', 1, 0
);

-- A top-level method
INSERT INTO code_nodes VALUES (
  'backend/core/prompt_builder.py::PromptBuilder.build_system_prompt',
  'backend/core/prompt_builder.py',
  'method',
  'build_system_prompt',
  533, 898, 'python', 1, 0
);
```

#### code_edges table (call relationships)

```sql
-- "Who calls GraphStore methods?" — this is the raw material for blast_radius
INSERT INTO code_edges VALUES (
  'backend/routers/code_intel.py::_run_reindex',                    -- source (caller)
  'backend/core/code_intel/graph_store.py::GraphStore.set_meta',    -- target (callee)
  'calls',                                                           -- edge_type
  0.8,                                                               -- confidence
  NULL                                                               -- line_number
);

INSERT INTO code_edges VALUES (
  'backend/core/code_intel_feed.py::detect_tech_drift',
  'backend/core/code_intel/graph_store.py::GraphStore.get_module_map',
  'calls', 0.8, NULL
);

-- Internal call chain — what does _ensure_spawned() call?
INSERT INTO code_edges VALUES (
  'backend/core/session_unit.py::SessionUnit._ensure_spawned',
  '_spawn',
  'calls', 0.5, 1042    -- call originates at line 1042
);

INSERT INTO code_edges VALUES (
  'backend/core/session_unit.py::SessionUnit._ensure_spawned',
  '_crash_to_cold_async',
  'calls', 0.5, 1057
);
```

#### Query Example: blast_radius (Recursive CTE)

```sql
-- "If I modify GraphStore.get_module_map(), who gets affected?"
WITH RECURSIVE affected(node_id, depth) AS (
  -- Seed: the changed node
  VALUES ('backend/core/code_intel/graph_store.py::GraphStore.get_module_map', 0)
  UNION ALL
  -- Recurse: walk edges backwards (who calls me?)
  SELECT e.source_id, a.depth + 1
  FROM code_edges e
  JOIN affected a ON e.target_id = a.node_id
  WHERE a.depth < 2  -- max 2 hops
)
SELECT DISTINCT node_id, depth FROM affected WHERE depth > 0;

-- Results:
-- backend/core/code_intel_feed.py::detect_tech_drift              (depth=1)
-- backend/core/code_intel_feed.py::get_code_coverage_for_health   (depth=1)
-- backend/core/code_intel_feed.py::get_test_coverage_for_maturity (depth=1)
```

**This is what the agent sees BEFORE you modify `get_module_map()` — not guessed, computed from the graph.**

---

*What's your experience with AI agents on large codebases? Have you tried graph-based approaches? Drop your observations below.*

---

## 中文摘要

**问题：** AI Agent 在大型代码库（500K+ LOC）改代码时，看不见跨模块依赖 → 改了 shared function → 下游 3 个 package 挂了。

**方案：** 预计算代码依赖图（tree-sitter 解析 → SQLite 存储 → 每次读文件时注入 ~100 token 依赖上下文）。不是 RAG（语义相似 ≠ 依赖关系），不是 runtime grep（太慢太噪）。

**核心设计：** SQLite 不用 Neo4j（部署简单）、100 token 不是 1000（注意力稀释）、git SHA 保鲜（不靠 mtime）、增量更新（O(changed_files)）。

**实战数据：** 11,682 symbols、14,743 edges、38MB DB、<50ms 查询、18% dead code 发现。

**什么时候需要：** 代码 >200K LOC、多包互相依赖、agent 改过共享接口导致下游 break。
