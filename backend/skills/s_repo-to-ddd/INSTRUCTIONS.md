# AI-Ready-Repo Engine — Full Instructions

Generate DDD-structured artifacts that make any codebase genuinely understood by AI agents.

## Overview

**Input:** Repo path + optional signal sources (docs, wikis, Slack exports)
**Output:** `.ai-ready/` directory with 7 files + `AGENTS.md` entry point

**Phases:** INPUT → INGEST → UNDERSTAND → GENERATE

**Output Levels (formal definition):**

| Level | What's Documented | Agent Can | Agent Cannot |
|-------|------------------|-----------|-------------|
| **1: Navigable** | Module map + entry points + build commands | Find correct file, run build/test | Fix bugs, understand patterns |
| **2: Safe** | + conventions with citations + gotchas with evidence + dependency graph | Avoid known mistakes, follow conventions | Modify complex code confidently |
| **3: Modifiable** | + function-level tables for hot zones + data flow diagrams + extension points + honest coverage % | Fix bugs in hot zones, add features following existing patterns | Modify unanalyzed modules without reading source |

**Target: Level 3 for hot-zone files, Level 2 for other key modules, Level 1 for the rest.**
Honest about coverage — never claim 90% confidence on a file you only glanced at.

## Progress Display

Print this briefing at the start, then show each phase landmark as it completes:

**Briefing (print once at start):**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ AI-Ready-Repo Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Making {repo_name} genuinely understood by AI agents.

  Phases:
    1. INPUT     — Collect repo path + optional signals
    2. INGEST    — Parse files, detect stack, gather git history
    3. UNDERSTAND — Read code, map modules, extract patterns
    3.5 ENRICH  — Ask user max 5 questions (what code can't tell)
    4. GENERATE  — Produce DDD artifacts (.ai-ready/ + AGENTS.md)
    5. VERIFY    — Sub-agent test: can it use the output? (3 tasks)
    6. DELIVER   — Present output + next steps to user

  Output:
    AGENTS.md              ← Entry point (≤150 lines)
    .ai-ready/PRODUCT.md   ← Why: purpose, audience, constraints
    .ai-ready/TECH.md      ← How: architecture, conventions
    .ai-ready/IMPROVEMENT.md ← Learned: gotchas, failures, patterns
    .ai-ready/PROJECT.md   ← Now: priorities, decisions, blockers
    .ai-ready/code-intel.json ← Graph: modules, deps, entry points
    .ai-ready/REVIEW-REPORT.md ← For humans: score, gaps, assignments
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Per-phase landmarks (print after each phase completes):**

```
## ✦ INPUT [Repo + Signals]
→ Repo: {path} ({N} files, {primary_language})
  Signals: {list of selected signals or "code-only mode"}
  Expected quality: Level {1|2|3}

## ✦ INGEST [Deterministic Scan]
→ {N} files | {languages} | {N} commits | {N} contributors
  Config: {config_files_found}
  Gotchas: {N} evidence-grounded from git history

## ✦ UNDERSTAND [Code Intelligence]
→ {N} modules | {N} entry points | {N} routes
  Hot zones: {list}
  Conventions: {N} detected
  Framework: {detected_framework or "none"}

## ✦ GENERATE [DDD Artifacts]
→ Score: {X.X}/10 | AGENTS.md: {N} lines
  Output: {output_path}/
  ├── AGENTS.md ({N} lines)
  ├── .ai-ready/PRODUCT.md
  ├── .ai-ready/TECH.md
  ├── .ai-ready/IMPROVEMENT.md ({N} gotchas)
  ├── .ai-ready/PROJECT.md
  ├── .ai-ready/code-intel.json ({N} modules, {M} edges)
  ├── .ai-ready/ai-ready.json
  └── .ai-ready/REVIEW-REPORT.md
```

**Completion (print at end):**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✦ COMPLETE | AI-Ready Score: {X.X}/10
  {project_name}: {N} modules, {M} gotchas, {K} conventions
  Review: .ai-ready/REVIEW-REPORT.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Prerequisites

Helper script: `backend/skills/s_repo-to-ddd/scripts/ai_ready_helpers.py`
- `gather_repo_info(path)` — git stats, file tree, tech stack detection
- `extract_import_graph(path)` — REAL dependency graph from import statements (not guessed)
- `validate_code_intel_json(doc)` — v2 schema enforcement
- `parse_git_gotchas(path)` — evidence-grounded gotchas from git history
- `render_agents_md(data)` — template rendering (≤150 lines guaranteed)
- `build_ai_ready_meta(score, name)` — ai-ready.json metadata
- `resolve_output_path(repo_path, project_name, target)` — deterministic output location
- `gotchas_for_agents_md(raw_gotchas)` — transform parse_git_gotchas output → render_agents_md input

## Workflow

### Phase 1: INPUT (Human Touchpoint #1)

**Start by collecting repo path, then ask what signals the user can provide.**

#### Step 1.1: Get repo path (REQUIRED)

If not already provided, ask:
"What repo do you want to make AI-ready? (local path or git URL)"

**Output language:** Also ask: "Output language? (default: English, or: zh/ja/ko/es/fr/de)"
If user specifies a language, ALL generated text (summaries, conventions, gotchas,
architecture descriptions) must be in that language. Technical terms (function names,
file paths, framework names) stay in English. Store as `output_language` for GENERATE.

#### Step 1.1b: Detect package boundaries (monorepo fan-out decision)

Before ingesting, detect whether this repo is a **monorepo** with multiple package
boundaries. This is a deterministic manifest read — no LLM, no code parsing:

```python
from ai_ready_helpers import detect_package_roots
packages = detect_package_roots(repo_root)   # always >=1 PackageRoot
```

- **Single package** (`len(packages) == 1`, `root == "."`): proceed with the normal
  single-repo flow below. Nothing changes.
- **Monorepo** (`len(packages) >= 2`): switch to the **MONOREPO FAN-OUT** path
  (Phase 4 §4.9). Tell the user: *"Detected N packages ({names}) — I'll generate
  per-package AI-ready artifacts + a cross-package synthesis."* Confirm before fanning
  out (per-package GENERATE is N× the work).

Store `packages` for GENERATE. The boundary set comes ONLY from workspace manifests
(npm/pnpm/lerna workspaces, Cargo `[workspace]`, nested go.mod, subdir pyproject) —
never invented. nx/turbo presence is a signal but members still come from the package
manager's workspace list.

#### Step 1.2: Signal collection (multi-select)

After getting the repo path, present the signal menu. The user picks what they have — more signals = richer output (Level 1 → Level 3).

**Present this to the user:**

```
The more context you provide, the better the output:

  📁 Repo path: {confirmed path} ✓

  What else can you provide? (all optional — pick any that apply)

  □ Design docs / PRDs          — purpose, architecture decisions
  □ Wiki / Confluence pages      — tribal knowledge, ops context  
  □ Slack exports / meeting notes — decisions, known issues
  □ Issue tracker (link or export) — bugs, tech debt patterns
  □ Existing CLAUDE.md / AGENTS.md — baseline to build on
  □ Verbal context               — "we never deploy on Fridays" type rules
  □ Dashboard / runbook URLs     — operational context
  □ Existing DDD docs            — if you already have PRODUCT/TECH/etc.

  Or just say "code only" to proceed with just the repo.
```

**For each signal the user selects, collect the specific input:**

| Signal | What to Ask | How to Ingest |
|--------|------------|---------------|
| Design docs / PRDs | "Drop the file path(s) or paste the content" | Read file directly (MD, PDF, plain text) |
| Wiki / Confluence | "Paste the URL(s)" | WebFetch or ask user to paste content |
| Slack exports | "Drop the JSON export file or paste key messages" | Parse JSON for decisions, issues |
| Issue tracker | "Paste the URL or drop an export" | WebFetch or Read file |
| Existing CLAUDE.md / AGENTS.md | "I'll look for it in the repo" | Read from repo root (auto-detect) |
| Verbal context | "Tell me anything the code doesn't show" | Classify into target DDD file (see below) |
| Dashboard / runbook URLs | "List the URLs" | Store as ops context for PRODUCT.md constraints |
| Existing DDD docs | "Path to the directory" | Read all 4 files as baseline |

#### Step 1.3: Signal classification (which signal → which DDD file)

As signals come in, classify each piece of information by target:

| Content Type | Target DDD File | Signal Patterns |
|---|---|---|
| Purpose, audience, constraints, compliance | **PRODUCT.md** | "users are...", "out of scope", "must comply with", "SLA" |
| Architecture decisions, conventions, patterns | **TECH.md** | "always use...", "never call...", "pattern is", "we chose X because" |
| Failures, postmortems, incidents, gotchas | **IMPROVEMENT.md** | "broke when", "don't touch", "burned by", "reverted" |
| Priorities, blockers, current sprint, decisions | **PROJECT.md** | "this quarter", "blocked by", "decided to", "don't change until" |
| Ops context, deploy process, monitoring | **PRODUCT.md** (constraints) + **TECH.md** (ops section) | "deploy with", "monitor via", "runbook at" |

**Rules for signal processing:**
- NEVER discard user-provided signals — everything gets classified somewhere
- If classification is ambiguous, ask: "Is this a convention (TECH) or a lesson (IMPROVEMENT)?"
- Verbal context gets tagged with `[source: user, {date}]` in the output
- File-sourced content gets tagged with `[source: {filename}, {date}]`
- Existing AGENTS.md / CLAUDE.md → extract rules into TECH.md, extract context into PROJECT.md

#### Step 1.4: Confirm and proceed

After collection, confirm what you have:

```
✓ Repo: /path/to/repo (391 files, Python)
✓ Signals: 2 design docs, 1 verbal context, existing CLAUDE.md
  → Expected output quality: Level 3 (full project understanding)

Proceeding to analysis...
```

If user says "code only" or provides nothing extra:
```
✓ Repo: /path/to/repo (391 files, Python)
  Signals: none (code-only mode)
  → Expected output quality: Level 1-2 (navigation + safety, limited business context)
  → PRODUCT.md and PROJECT.md will be skeletal — enrich later with "ai-ready learn"

Proceeding to analysis...
```

### Phase 2: INGEST

Run the helper script to gather deterministic repo info:

```python
import sys
sys.path.insert(0, "backend/skills/s_repo-to-ddd/scripts")
from ai_ready_helpers import gather_repo_info, parse_git_gotchas

info = gather_repo_info(Path(repo_path))
gotchas = parse_git_gotchas(Path(repo_path))
```

Or via Bash:
```bash
python -c "
import sys, json
sys.path.insert(0, 'backend/skills/s_repo-to-ddd/scripts')
from pathlib import Path
from ai_ready_helpers import gather_repo_info, parse_git_gotchas
info = gather_repo_info(Path('REPO_PATH'))
gotchas = parse_git_gotchas(Path('REPO_PATH'))
print(json.dumps({'info': info, 'gotchas': gotchas}, indent=2, default=str))
"
```

INGEST gives you the structural skeleton: file tree, tech stack, git stats, gotchas.
It does NOT read code — that happens in UNDERSTAND (Phase 3).

Also read any user-provided signal documents (Read them directly).

### Phase 3: UNDERSTAND

> 🚨 **THIS PHASE READS ACTUAL CODE. No shortcuts.**
>
> The entire value of AI-Ready-Repo is that an agent read the code so future
> agents don't have to re-discover it. If you skip reading code, the output
> is worthless — a README paraphrase anyone could write in 2 minutes.

**MANDATORY: Read at minimum these files before proceeding to GENERATE:**

#### Step 3.1: Identify key files to read (from INGEST file_tree)

Pick files using this priority:
1. **Entry points** — `__main__.py`, `cli.py`, `app.py`, `server.py`, `main.ts`, `index.ts`
2. **Core module files** — the largest `.py`/`.ts`/`.rs` files by line count (top 5)
3. **Config/setup** — already parsed in INGEST (`pyproject.toml`, `package.json`, etc.)
4. **Base classes / interfaces** — files named `base.py`, `types.ts`, `interface.go`
5. **Hot zones** — files with most fix/revert commits (from gotchas output)

**Minimum reads: 8 files.** For repos <50 files, read ALL source files.
For repos 50-200 files, read 10-15. For repos >200 files, read 15-20.

**Level 3 depth (MANDATORY for hot-zone files):** For the top 3-5 files by
fix-commit count (from `parse_git_gotchas` output), read the FULL file and
extract function-level knowledge:
- Every public function: name, approximate line range (~N), signature, what it does (1 sentence)
- Callers: which other functions call this one (from grep or import graph)
- Gotchas: function-specific bugs/traps (from git history + code reading)
- Data flow: what does this function receive → transform → return/write

Additionally: for modules that are NOT hot-zone but are key infrastructure
(e.g., config, utils, database layer), document at minimum:
- Public API surface (function names + 1-line purpose)
- Integration point (how other modules use it)
- Known constraints (performance, thread-safety, limits)

**Extension Points:** TECH.md MUST include an "Extension Points" section:
- Where do new cross-cutting features plug in? (hooks, callbacks, post-mine phase?)
- If no plugin system exists, say so explicitly: "No hook/event system — new features added inline at [specific location]."

The output must be specific enough that an agent reading ONLY the DDD output
(not the source) can:
- Identify correct function for a bug fix (hot-zone files: ~85% confidence)
- Add a new feature following existing patterns (~70% confidence)
- Navigate to correct file/module for ANY change (90%+ confidence)

**Honest coverage declaration (MANDATORY in REVIEW-REPORT.md):**
```
Coverage: {N}/{M} source files read ({pct}%)
  Hot zones (function-level): {list of files}
  Module-level only: {list of files}
  Not analyzed: {count} files

Confidence by scenario:
  Bug in hot-zone file: ~85%
  Bug in module-level file: ~50% (will need source reading)
  Bug in unanalyzed file: ~20% (navigation only)
  New feature (existing pattern): ~70%
  New feature (new pattern): ~40%
```

#### Step 3.2: Extract REAL dependencies (from import statements)

**Run the helper function — this is MANDATORY, not optional:**

```python
from ai_ready_helpers import extract_import_graph
graph = extract_import_graph(Path(repo_path))
# graph["modules"] → [{name, path, files, imports_from, imported_by}]
# graph["edges"] → [{from (file), to (module), line, raw}]
# graph["stats"] → {files_scanned, edges_found, primary_language}
```

This scans ALL source files and extracts EVERY import statement with file:line citation.
The `depends_on` and `depended_by` fields in code-intel.json MUST come from
this output — never from guessing.

If `edges_found == 0` for a repo with code, something is wrong. Check the language detection.

**After running the script**, also READ 3-5 key files to understand the imports semantically
(what does module A actually USE from module B — just types? Or runtime calls?).

#### Step 3.3: Extract REAL conventions (from code patterns)

Read 3+ implementation files and look for REPEATED patterns:
- Error handling style (exceptions? error codes? Result types?)
- Naming conventions (snake_case? camelCase? prefixes?)
- File organization (one class per file? barrel exports?)
- Test patterns (fixtures? mocks? factories?)
- Logging approach (structured? print? logger per module?)
- DB access patterns (raw queries? ORM? repository pattern?)

**Rule: every convention you write in TECH.md must cite at least 2 files where you observed it.**
If you saw it in only 1 file, it might be an anomaly, not a convention.

Example — GOOD:
```
ALWAYS use `logger = logging.getLogger(__name__)` per module
  (observed in: palace.py:12, miner.py:8, searcher.py:5, backends/chroma.py:10)
```

Example — BAD:
```
ALWAYS use logging  ← (too vague, could mean anything)
```

#### Step 3.4: Extract REAL architecture (from what code actually does)

For each module/directory:
- Read the top of the file (docstring, first class/function)
- What does it ACTUALLY do? (not what you guess from the filename)
- What public API does it expose? (functions/classes that others import)
- What does it depend on? (imports you verified in Step 3.2)

#### Step 3.5: Detect entry points and routes

- **CLI:** Read the actual CLI file — what commands exist? What do they do?
- **HTTP routes:** Grep for `@app.route`, `@router.`, `app.get(`, `router.post(`
- **MCP tools:** Grep for `@tool`, `@server.tool`, MCP handler registrations
- **Event handlers:** Grep for `@listener`, `on_event`, signal handlers

#### Step 3.6: Validate against running (if possible)

If the project has a test suite or build command, try running it:
```bash
# Try to build/install
cd {repo_path} && {detected_build_command} 2>&1 | tail -10

# Try to run tests (just first few to verify they work)
{detected_test_command} --co -q 2>&1 | tail -5  # collect only, don't run
```

This validates that your detected build/test commands actually work.
If they fail, note the failure in REVIEW-REPORT.md.

---

**UNDERSTAND phase output** (these feed directly into GENERATE):

For each module detected:
- `name` — directory name or logical grouping
- `path` — relative path from repo root
- `responsibility` — one sentence DERIVED FROM READING THE CODE (not from filename)
- `depends_on` — FROM ACTUAL IMPORT STATEMENTS (cite file:line)
- `depended_by` — FROM GREP OF WHO IMPORTS THIS MODULE
- `entry_points` — exported/public functions that serve as API surface

For routes (if web framework detected):
- `method` — HTTP method
- `path` — URL pattern
- `handler` — file:function reference (VERIFIED by reading the code)
- `framework` — detected framework name

### Phase 3.5: ENRICH (Human Touchpoint #2)

> Ask ONLY what the code can't tell you. Max 5 questions. All optional.

**Run the helper to determine what questions to ask:**

```python
from ai_ready_helpers import generate_enrich_questions, classify_enrich_answer

questions = generate_enrich_questions(info, gotchas, graph)
# Returns: [{question, target_file, why}]
```

**Present questions to user:**

```
I've analyzed the code. A few things I can't determine from code alone:

1. [PRODUCT.md] Who are the primary users, and what problem does this solve?
   (Why: README doesn't clearly state audience)

2. [PRODUCT.md] What is explicitly OUT OF SCOPE? What should this project NEVER do?
   (Why: Non-goals prevent agents from building wrong things)

3. [PROJECT.md] What are your top 1-3 priorities right now?
   (Why: Git shows what was done, not what should be done next)

Answer any/all, or say "skip" to proceed with code-only analysis.
```

**Process answers:**
- Each answer goes into the `target_file` specified by the question
- If user provides unsolicited info, use `classify_enrich_answer(text)` to route it
- Tag each entry: `[source: user, 2026-06-01]`
- NEVER rewrite user's words — add them verbatim to the appropriate file section

**If user says "skip" or provides no answers:**
- Proceed to GENERATE. PRODUCT.md and PROJECT.md will be skeletal.
- Note in REVIEW-REPORT.md: "ENRICH skipped — PRODUCT/PROJECT are code-derived only (Level 2)"

**Progress display:**
```
## ✦ ENRICH [Human Touchpoint #2]
→ Asked: {N} questions | Answered: {M} | Skipped: {K}
  Enriched: {list of files that got new content}
  Quality boost: Level {2→3 if answered, stays 2 if skipped}
```

### Phase 4: GENERATE

Produce all output files. The structure for each file is defined inline below.

**Output directory:** Use `resolve_output_path()` to determine where to write:

```python
from ai_ready_helpers import resolve_output_path
output_path = resolve_output_path(Path(repo_path), project_name="...", target=user_target_if_specified)
# Creates: {output_path}/AGENTS.md + {output_path}/.ai-ready/
```

Priority: user-specified path > SwarmWS .artifacts/ > alongside repo.
Output is always at a predictable location the user can find.

#### 4.1: AGENTS.md (entry point)

Use `render_agents_md()` from the helper script:

```python
from ai_ready_helpers import render_agents_md

agents_content = render_agents_md({
    "project_name": "...",
    "build_command": "...",
    "test_command": "...",
    "lint_command": "...",
    "test_duration": "...",
    "modules": [...],
    "entry_points": [...],
    "critical_rules": [...],
    "gotchas": [...],
    "score": 7.5,
    "generated_date": "2026-06-01",
})
```

Write to `AGENTS.md` at output root. MUST be ≤150 lines.

#### 4.2: PRODUCT.md

Generate from README + user signals. Sections:
- **Purpose** — what problem, for whom (from README + user input)
- **Audience** — primary users/consumers
- **Non-Goals** — explicitly out of scope
- **Success Criteria** — measurable outcomes
- **Constraints** — regulatory, compliance, SLA, business rules

If no user input available for a section, write `[To be filled by product owner]`.
End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.3: TECH.md

Generate from ACTUAL CODE ANALYSIS (Phase 3 UNDERSTAND output). Sections:
- **Stack** — languages, frameworks, databases (verified from imports + config)
- **Architecture** — module map WITH verified dependency arrows (from Step 3.2)
- **Conventions** — prescriptive rules, EACH citing 2+ files where observed (Step 3.3)
- **Key Decisions** — architectural choices visible in code + git history
- **Invariants** — things that must always be true (from base classes, assertions, guards)

**Quality gate: TECH.md is REJECTED if any convention lacks file citations.**
Every "ALWAYS do X" must say "(observed in: file1.py, file2.py)".
Every "NEVER do Y" must say "(violation would break: explanation based on code reading)".

**Level 3 requirement: TECH.md MUST include function-level architecture tables**
for the top 3-5 hot-zone files. Format:

```markdown
### {filename} — {description} ({N} lines, {M} fix commits)
_Verified at commit {short_hash}. Line numbers approximate — grep function name to confirm._

| Function | ~Lines | Callers | What It Does | Gotchas |
|----------|--------|---------|-------------|---------|
| `func_name(args)` | ~100 | module.caller1, module.caller2 | One sentence | Specific trap |
```

**Line number rules:**
- Prefix with `~` to indicate approximate (lines shift on every commit)
- Always include the function SIGNATURE — this is the stable anchor, not line number
- Agent consumers should `grep -n "def func_name"` to confirm current line
- Include `Verified at commit: {hash}` so staleness is detectable

Plus a **data flow diagram** showing the main E2E path through the codebase (e.g.,
CLI → mine → process_file → add_drawer → ChromaDB). This is what makes the output
useful for bug-fixing — agent can trace the path without reading source.

Plus an **Extension Points** section documenting where new cross-cutting features
plug in. If no hook/event/plugin system exists, state: "No hook system — new
features added inline at [specific location in the call chain]."

End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.4: IMPROVEMENT.md

Generate from git history + gotchas. Sections:
- **What Failed** — WHEN [trigger] → RISK [what breaks] → BECAUSE [evidence]
  (Use `gotchas` from helper script — every entry has commit hash evidence)
- **What Works** — patterns that appear stable (unchanged files with high usage)
- **Known Issues** — from open issues, TODO comments, or user signals

CRITICAL: Every entry in "What Failed" MUST have commit hash evidence.
Never generate gotchas without evidence — absence of evidence = absence of entry.

End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.5: PROJECT.md

Generate from recent git activity + user signals. Sections:
- **Current Priorities** — what's actively being worked on (from recent commits)
- **Recent Decisions** — major changes in last 30 days
- **Blocked By** — known blockers (from user signals or stale PRs)
- **Open Questions** — things agent should NOT decide unilaterally

End with: `<!-- user: Your additions below — refresh preserves this section -->`

#### 4.6: code-intel.json

Build the v2 document from UNDERSTAND phase analysis:

```python
from ai_ready_helpers import validate_code_intel_json, build_packages_partition

doc = {
    "$schema": "https://ai-ready-repo.dev/schemas/code-intel.v2.json",
    "version": "2.0",
    "generated_at": "...",
    "repo": { "name": "...", "languages": {...}, "total_symbols": N, "total_edges": M },
    "modules": [...],
    "edges": [...],
    "entry_points": [...],
    "routes": [...],
    "hot_zones": [...],
    "risk_areas": [...],
    "dead_code": [],
    "dependencies": {...},
    # packages[] — monorepo boundary partition (additive; parity with the core
    # reindex producer json_exporter, so BOTH producers of code-intel.json carry it).
    # Single-package repo → [{name, root: "."}]; monorepo → one entry per package.
    "packages": build_packages_partition(repo_root),
}

# MANDATORY: validate before writing
errors = validate_code_intel_json(doc)
if errors:
    # Fix errors before writing — never write invalid JSON
    raise ValueError(f"code-intel.json validation failed: {errors}")
```

Write as formatted JSON (indent=2).

#### 4.6.5: code-intel v3 domain layer (OPTIONAL — business-flow spec understanding)

**When to run:** the user wants human-signable business-flow specs (the "legacy
code nobody dares touch" use case), not just the machine graph. Skip for a plain
AI-ready pass — v2 is complete without it. This produces `domains[]/flows[]/steps[]`
that Run-3 recall + spec-details generation consume.

**The anti-hallucination contract (§1.1/§1.5): the LLM classifies REAL entry
points into business flows — it never invents an entry point.** The deterministic
scaffold produces a constrained anchor menu; the LLM may only reference those ids.

```python
from ai_ready_helpers import (
    backfill_route_ids, extract_entry_anchors, finalize_v3)

# 1. Backfill stable §1.4 join keys onto v2 routes/entry_points (idempotent).
doc = backfill_route_ids(doc)

# 2. Project the ANCHOR MENU — the ONLY entry ids a flow may reference.
anchors = extract_entry_anchors(doc)
# anchors = [{id, method, path, file_path, line_number, kind}, ...]
```

**3. LLM classification (this is the agent's judgment step — NOT a helper):**
Read the anchor menu + the UNDERSTAND-phase module/hot-zone analysis.

> **🚨 COVERAGE IS MANDATORY — account for EVERY anchor, do NOT cherry-pick a subset.**
> This is the difference between "we understand this codebase" and "we understand
> the 5% we felt like classifying". On a bank/enterprise legacy codebase, reporting
> "done" while silently leaving 95% of entry points unclassified is a **fatal
> delivery**. Every anchor in the menu MUST be ACCOUNTED for — one of two ways:
> 1. **Classified** — referenced by a `flow.entry_ref` (it belongs to a business flow), OR
> 2. **Explicitly unclassified** — listed in a top-level `unclassified: [{id, reason}]`
>    array, where `reason` is a **substantive** explanation of why it has no business
>    flow (e.g. "unauthenticated health probe, no business semantics" / "static asset
>    route" / "dead code, 0 callers"). A blank or junk reason (`"."`, `"n/a"`, `"todo"`)
>    is REJECTED — it must genuinely explain the absence.
>
> `finalize_v3` is **fail-closed on accounting** (`check_anchor_accounting`): if ANY
> anchor is neither classified nor reasoned-unclassified, it RAISES with the list of
> unaccounted ids. Silent omission is structurally impossible to ship. The honest
> `classified_ratio` (real business-flow coverage) is REPORTED, never gated — so you
> are never rewarded for padding trivial routes into fake flows just to hit a number.

Group the REAL anchors into business domains and flows. For each:
- `domain`: id (`domain:<kebab>`), name, summary, entities, complexity, optional
  `business_rules`/`issues`/`gaps`/`diagram` (mermaid).
- `flow`: id (`flow:<kebab>`), `domain_id` (a real domain id), `entry_ref`
  (**MUST be an id from the anchor menu** — a hallucinated ref is rejected in step 4),
  `entry_type`, optional `diagram`.
- `step`: id, `flow_id` (a real flow id), `order`, `name`, `file_path`, `line_range`,
  optional `io`/`contract`/`rules`/`preconditions`/`exceptions`.
- **§1.5 assertion rule (fail-closed):** every rule/precondition/exception is a
  dict with an explicit bool `verified`. `verified:true` REQUIRES a non-blank
  `anchor` (code file:line). `verified:false` REQUIRES `absence_evidence` (a
  `grep`-returned-0 proof) — a "rule doesn't exist" claim is unreliable unless
  proven absent. A bare-string rule or a missing `verified` is rejected.

```python
# 4. Assemble + FAIL-CLOSED validate. Raises ValueError if ANY flow.entry_ref is
#    dangling, any verified:true lacks an anchor (spurious), any verified:false
#    lacks absence_evidence, OR ANY anchor is UNACCOUNTED (not in a flow and not in
#    a reasoned unclassified bucket — the coverage guarantee). A rejected layer is
#    NEVER written — fix and re-run.
#    Put every not-a-business-flow anchor in doc["unclassified"] with a real reason:
doc["unclassified"] = [
    {"id": "route:get-health-...", "reason": "unauthenticated liveness probe, no business semantics"},
    # ... every anchor not classified into a flow MUST appear here with a substantive reason
]
doc = finalize_v3(doc, domains, flows, steps)  # doc["version"] now "3.0"

errors = validate_code_intel_json(doc)  # redundant belt-and-suspenders; must be []
assert not errors

# Coverage report (honest signal — NOT a gate): how much is real business flow?
acc = compute_anchor_accounting(doc)
print(f"accounted {acc['accounted_ratio']:.0%} (MUST be 100% — fail-closed), "
      f"of which real flows {acc['classified_ratio']:.0%}, "
      f"{acc['unclassified_count']} explicitly-unclassified")
```

Then re-write `code-intel.json` with the v3 doc. Downstream: Run-3 recall surfaces
these domains on any recall query; each domain also gets a
`spec-details/<domain>.spec.md` for human enrichment.

**⚠️ Writing a `.spec.md` — ALWAYS route through the preserving path (irreversible
data-loss risk):** §5 of a spec holds human-authored `[human]` business rules that
a banking reviewer has signed off on. Regenerating a spec MUST NOT destroy them.

```python
from ai_ready_helpers import regenerate_spec_preserving_human
# domain['id'] is "domain:<name>"; the spec filename is the bare <name>.
spec_name = domain["id"].split(":", 1)[-1]
spec_path = spec_dir / f"{spec_name}.spec.md"
existing = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
spec_md = regenerate_spec_preserving_human(existing, domain, flows, steps)  # splices §5 [human] blocks back in; plain skeleton on first gen
spec_path.write_text(spec_md, encoding="utf-8")
```

`regenerate_spec_preserving_human` auto-detects `[human]` blocks in `existing` and
re-injects them verbatim (idempotent; a first generation where `existing==""` yields
a plain skeleton). **NEVER call `project_domain_skeleton(...)` directly to WRITE a
spec file that may already exist** — it renders only the raw skeleton with an empty
§5 stub, so writing it over an enriched spec DESTROYS the human rules. Use
`project_domain_skeleton` only for an in-memory preview of a brand-new domain.

#### 4.7: ai-ready.json (metadata)

```python
from ai_ready_helpers import build_ai_ready_meta
meta = build_ai_ready_meta(score=calculated_score, project_name="...")
```

#### 4.8: REVIEW-REPORT.md

Generate a human-readable report covering:
- Overall AI-Ready Score (9 dimensions, 0-10 each)
- Per-file confidence (High/Medium/Low) and source attribution
- Review assignments (who should verify which file)
- Improvement recommendations (prioritized)
- Known gaps (what the engine couldn't determine)

#### 4.8.5: BLIND-SPOTS.md (reverse-coverage — code→doc direction)

Write a **per-package** `{output_path}/.ai-ready/BLIND-SPOTS.md` — the Spec Studio-style
reverse-coverage check: risky code spans (high fan-in / flagged risk) that the DDD domain
layer does NOT document. This is the human-facing consumer for `blind_spot_scan`; it runs
off the SAME `code-intel.json` doc built in §4.6/§4.6.5 (needs `risk_areas`/`hot_zones` +
`steps`/`business_rules`).

```python
import json
from pathlib import Path
from ai_ready_helpers import blind_spot_scan, render_blind_spots_md

doc = json.loads((Path(output_path) / ".ai-ready" / "code-intel.json").read_text())
scan = blind_spot_scan(doc)                       # {total_risky, documented, blind, clean, blind_spots}
# TITLE ARG = the name of THIS unit. Single-repo path → project_name. In the §4.9
# monorepo fan-out → package.name (NOT project_name — else every package's doc is
# titled with the repo name; Gate-2 MED, run_d7b78923). The FILE is already per-package
# because output_path is the per-package dir inside the fan-out loop.
md = render_blind_spots_md(scan, unit_name)        # unit_name = project_name | package.name
(Path(output_path) / ".ai-ready" / "BLIND-SPOTS.md").write_text(md)
# carry scan["blind"] into the Phase-6 DELIVER summary line for THIS package
```

Rules (load-bearing):
- **PER-PACKAGE, never shared.** Blind spots are that repo's own — one `BLIND-SPOTS.md`
  per package `.ai-ready/` dir. In the §4.9 monorepo fan-out, each package writes its own
  (inside its per-package dir); there is NO global/merged BLIND-SPOTS.md.
- **REPORT-ONLY, never a gate.** `blind_spot_scan` is deterministic (keys off real
  risk_areas/hot_zones, not an LLM negative assertion) and explicitly NOT fail-closed
  (the gate version was deferred as C042). Do NOT BLOCK generation on blind spots — they
  are SME-documentation candidates, surfaced honestly.
- **Zero blind spots is a valid, STATED outcome** — `render_blind_spots_md` emits an
  explicit "no reverse-coverage blind spots" doc, never an empty file.

#### 4.9: MONOREPO FAN-OUT (only when Step 1.1b detected ≥2 packages)

Skip this section entirely for a single-package repo (the flow above already
produced its artifacts). For a monorepo, GENERATE runs **per package** + one
cross-package synthesis:

```python
from ai_ready_helpers import run_multi_package
# Deterministic per-package material + cross-package synthesis (auto-detects
# boundaries; do NOT hand it a package list — it calls detect_package_roots).
mp = run_multi_package(repo_root, output_base=Path(output_path) / ".ai-ready" / "packages")
# mp["packages"]     → [{name, root, path, language_mix, detected_by, stats}]
# mp["partition"]    → the packages[] navigation partition
# mp["cross_package"]→ {shared_deps, dep_order}
```

**Fan-out loop — for EACH package in `mp["packages"]`:**
1. Run UNDERSTAND (Phase 3) scoped to `package.path` (its subtree only).
2. GENERATE §4.1–§4.6 **+ §4.8.5 (per-package BLIND-SPOTS.md — pass `unit_name=package.name`, NOT project_name, or every package's doc is mistitled with the repo name)** into a **per-package dir keyed on a UNIQUE segment** — use
   `package.name` (already disambiguated by `run_multi_package`: a root/member name
   collision is path-suffixed, e.g. `sub/core` → dir `sub__core`) OR, safest, key the
   dir on `package.root` (always unique). Do NOT key on a raw repo-dir name — two
   packages can share it (a repo dir `x` + a nested member `x`), and keying on the raw
   name would make the second clobber the first, silently violating coverage below.
   Each package's `code-intel.json` carries its own `packages: [{name, root:"."}]`
   (it is a self-contained unit from its own root).
3. **Coverage is mandatory** — account for EVERY detected package, never a subset
   (same discipline as §4.6.5 entry-point coverage). Verify N distinct output dirs
   for N packages (a collision that dropped one = coverage violated). Silently
   skipping packages = "we understand the repo" being false.

**Cross-package synthesis — write `{output_path}/.ai-ready/CROSS-PACKAGE.md`:**
- The package inventory (`mp["partition"]` — name, root, language_mix, detected_by).
- Shared dependencies (`mp["cross_package"]["shared_deps"]`) — libs used by ≥2 packages.
- Dependency order (`mp["cross_package"]["dep_order"]`) — which package imports which
  (the build/change-blast-radius order).
- Root-level `code-intel.json` keeps the full `packages[]` partition so a top-level
  agent sees all boundaries at once.

The single-repo GENERATE path (§4.1–§4.8) is unchanged; this section is purely additive.

### Phase 5: VERIFY (sub-agent quality gate)

> "Can an agent actually USE this output?" — proved mechanically, not assumed.

**Step 5.1: Select 3 verification tasks from git log**

```python
from ai_ready_helpers import select_verification_tasks
tasks = select_verification_tasks(Path(repo_path))
# Returns: [{type, description, correct_file, commit}]
```

If fewer than 2 tasks found (repo has < 3 meaningful commits), skip VERIFY
and note in REVIEW-REPORT.md: "VERIFY skipped — insufficient commit history."

**Step 5.2: Build verification prompt**

```python
from ai_ready_helpers import build_verification_prompt

# Collect the generated DDD content
ddd_content = {
    "AGENTS.md": Path(output_path / "AGENTS.md").read_text(),
    "TECH.md": Path(output_path / ".ai-ready/TECH.md").read_text(),
    "IMPROVEMENT.md": Path(output_path / ".ai-ready/IMPROVEMENT.md").read_text(),
    "code-intel.json": Path(output_path / ".ai-ready/code-intel.json").read_text(),
}

prompt = build_verification_prompt(ddd_content, tasks)
```

**Step 5.3: Spawn verification sub-agent**

Use the Agent tool to spawn a fresh sub-agent with ISOLATED context:

```
Agent({
  description: "Verify AI-Ready output usability",
  prompt: <the prompt from build_verification_prompt>,
  model: "sonnet"  // cheaper model is fine for verification
})
```

The sub-agent has ONLY the DDD text — no Read tools, no source code access.
It must answer using solely the provided artifacts.

**Step 5.4: Evaluate response**

```python
from ai_ready_helpers import evaluate_verification_response
result = evaluate_verification_response(response, tasks)
# Returns: {passed: bool, score: "2/3", results: [...], feedback: [...]}
```

**Step 5.5: Pass/Fail decision**

- **PASS (score >= 2/3):** Proceed to DELIVER. Note score in REVIEW-REPORT.md.
- **FAIL (score < 2/3):** Read the `feedback` list. Each entry tells you what's
  missing from the output. Go back to GENERATE and add the missing information
  (e.g., "dedup.py function list not in TECH.md" → add dedup.py to function tables).
  Then re-run VERIFY (max 2 iterations).
- **After 2 failed iterations:** Proceed to DELIVER anyway, but mark in
  REVIEW-REPORT.md: "VERIFY FAILED — output has known gaps: {feedback}"

**Progress display:**
```
## ✦ VERIFY [Sub-Agent Quality Test]
→ Tasks: {N} from git log | Score: {X}/{N} | {PASS/FAIL}
  {per-task result summary}
  {feedback if any}
```

### Phase 6: DELIVER (post-verification)

Present to user:
```
✅ AI-Ready artifacts generated for {project_name}

Output: {output_path}/
├── AGENTS.md (XX lines, score: X.X/10)
├── .ai-ready/
│   ├── PRODUCT.md
│   ├── TECH.md
│   ├── IMPROVEMENT.md
│   ├── PROJECT.md
│   ├── code-intel.json (N modules, M routes)
│   ├── ai-ready.json
│   ├── REVIEW-REPORT.md
│   └── BLIND-SPOTS.md ({blind} reverse-coverage blind spots)

Next steps:
1. Review REVIEW-REPORT.md for confidence levels and gaps
2. Review BLIND-SPOTS.md — {blind} risky code span(s) the docs don't cover (SME queue)
3. Have PM review PRODUCT.md, engineer review TECH.md
4. Install to your IDE: copy to project root
```

> The `{blind}` count comes from §4.8.5's `scan["blind"]`. For a monorepo, name each
> package's own BLIND-SPOTS.md + its count (per-package, not merged).

## Scoring (10 Dimensions)

| Dimension | How to Score |
|-----------|-------------|
| Navigation | AGENTS.md quality + module map completeness |
| Build/Test | Build commands found + verified working |
| Architecture | Module boundaries clear + deps mapped |
| Conventions | Rules detected + prescriptive (not descriptive) |
| Tribal Knowledge | Gotchas with evidence (count × quality) |
| Code Graph | Modules + edges + entry points populated |
| Route Coverage | Routes detected / total endpoint references |
| Test Safety | Test files found + CI config present |
| Ops Context | Deploy info + monitoring + runbook references |
| Business-Rules Extraction | domain_rules coverage × traceability pass-rate — **N/A (excluded from overall)** if no domain_rules layer (non-legacy/non-SQL repo). Compute MECHANICALLY via `compute_business_rules_dimension(doc, specs=...)` in `ai_ready_helpers.py`; never eyeball it. |

Score each 0-10. **Overall = average of the APPLICABLE dimensions only** — a dimension
returning N/A (score=None, e.g. Business-Rules Extraction on a repo with no
domain_rules layer) is EXCLUDED from the average, never counted as 0 (a repo is not
penalized for lacking a capability it was never meant to have). Minimum for
"AI-Ready": 6.0.

## Quality Gate (BLOCKING — before writing ANY output)

Before entering GENERATE, self-check:

| Check | Pass Condition | If FAIL |
|-------|---------------|---------|
| Files read | ≥8 source files actually Read (not just listed) | Go back to UNDERSTAND |
| Import graph | `depends_on` derived from grep/Read of imports | Go back to Step 3.2 |
| Conventions cited | Every convention cites 2+ files | Go back to Step 3.3 |
| Entry points verified | Each entry point Read and confirmed | Go back to Step 3.5 |
| Function tables | Top 3 hot-zone files have function tables (name, ~lines, callers, gotchas) | Go back to Step 3.1 and read full files |
| Extension points | TECH.md states where new features plug in (or says "no hook system") | Add section to TECH.md |
| Coverage declared | REVIEW-REPORT.md states honest coverage % and per-scenario confidence | Add coverage section |

**If you didn't Read the code, you have NOTHING to generate.**
README + git log alone is NEVER sufficient for TECH.md or code-intel.json.

## Boundaries

### Always
- AGENTS.md ≤ 150 lines
- code-intel.json passes validate_code_intel_json() before writing
- IMPROVEMENT.md gotchas have commit evidence (WHEN/RISK/BECAUSE grammar)
- All DDD files end with `<!-- user -->` preservation marker
- Works on ANY repo — no SwarmAI-specific paths
- **TECH.md conventions cite source files (2+ per convention)**
- **code-intel.json edges come from verified import statements**
- **Read ≥8 source files before generating output**

### Never
- Never generate gotchas without commit hash evidence
- Never write invalid code-intel.json (validate first)
- Never exceed 150 lines in AGENTS.md
- Never overwrite existing AGENTS.md without asking
- Never assume specific IDE or framework
- **Never write TECH.md conventions without file citations**
- **Never guess dependency edges — verify from imports**
- **Never skip UNDERSTAND and go straight to GENERATE**
