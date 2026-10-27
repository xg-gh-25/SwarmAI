---
name: project-manager
description: "Create, list, edit, rename and delete DDD projects with the canonical six-section structure. TRIGGER: \"add project\", \"new project\", \"create project\", \"rename project\", \"create DDD\". NOT FOR: todos (radar-todo)."
tier: always
python-deps: [yaml]
---
# Project Manager

Manage projects in the SwarmWS workspace. Each project uses **DDD (Domain-Driven
Design)** structure with 4 knowledge documents that give Swarm deep understanding
of the project's domain.

> These 4 docs are **section ② KNOWLEDGE** of the canonical **six-section** DDD
> structure (DDD_SPEC_VERSION 1.0) — see "The Canonical DDD Structure" below. CREATE
> scaffolds the ①②③④⑥ SKELETON (dirs + manifests + purpose-READMEs); their content
> accretes as the project grows; only ⑤ `bindings.yaml` waits for BIND.

## DDD Structure

Every project gets these files (templates with instructions for the user):

| File | Domain | Purpose |
|------|--------|---------|
| **PRODUCT.md** | Strategic alignment | Vision, priorities, success criteria, non-goals |
| **TECH.md** | Technical context | Architecture, stack, codebase location, dev commands, conventions |
| **IMPROVEMENT.md** | Historical patterns | What worked, what failed, known issues, security history |
| **PROJECT.md** | Current context | Current focus, open items, recent decisions, blockers |
| **.artifacts/** | Pipeline outputs | Research, design docs, changesets, reviews, test reports |

The **SwarmAI** project is the default project that ships with every installation.
It cannot be deleted but can be freely edited. It serves as a working example of
the DDD structure.

---

## The Canonical DDD Structure — six sections (DDD-agent-brain spec §3.6)

> **`DDD_SPEC_VERSION: 1.0`** — this skill provisions DDDs conforming to spec version
> 1.0 (the six-section canonical structure). Because a DDD can carry `s_ddd-manager`
> to create MORE DDDs (self-propagation), the structure is a VERSIONED spec: declaring
> the version here is what keeps propagated DDDs from drifting. Source of truth:
> `Projects/AIDLC/Knowledge/Designs/2026-07-11-ddd-agent-brain-paradigm-design.md` §3.6.

A DDD is a product's **domain BRAIN = its control plane**. It **OWNs** cognitive assets
(holds them, is their source of truth) and **GOVERNs** physical assets (points at +
controls them via data/pointers — never contains code, never runs a pipeline: 指+治,
不含+不跑). The **4 knowledge documents above are section ② KNOWLEDGE** — ONE of the six
sections, not a competing structure. The full canonical shape:

> **SKELETON vs CONTENT — the distinction that governs provisioning (design decision
> 2026-07-12, option A):** CREATE materializes the **skeleton** of ①③④⑥ so the
> standard is CONCRETE in SwarmWS — SwarmAI-maintenance follows it, and AIM export
> is low-variance. Two kinds of ④ content differ: the **3 default DDD-native skills
> are COPIED IN at create** (official, we maintain them — see below); **project-specific**
> gates/skills/agent-specs still **ACCRETE** as the project grows. "Provisioned by
> CREATE?" below distinguishes the two.

| # | Section | OWN/GOVERN | Concrete members | Provisioned by CREATE? |
|---|---------|-----------|------------------|:---:|
| ① | **IDENTITY & MANIFEST** | OWN | project dir + `.project.json`, `aim.json` (declares the 3 native skills), `AGENTS.md` (the ONE unified README), `.crux_template.md` | ✅ dir + `.project.json` + the 3 manifests |
| ② | **KNOWLEDGE** | OWN | **the 4 docs (PRODUCT/TECH/IMPROVEMENT/PROJECT.md) + `Knowledge/`** — 冷启动 + judgment BORN here as prose | ✅ the 4 docs |
| ③ | **GATES** (the moat) | OWN | `gates/<gate>.py|sh` + tests + `gates/context/includes/*denied*.json` — executable terminus of matured judgment | ✅ `gates/` + `context/includes/` empty dir (`.gitkeep`); **content accretes** as judgment matures (养成 ladder) |
| ④ | **CAPABILITIES** | OWN | `skills/` — the **3 default DDD-native skills COPIED IN** + project-specific skills that accrete | ✅ `skills/` with the **3 native skills copied from `backend/templates/ddd-skills/`**; project-specific capabilities accrete |
| ⑤ | **DELIVERY CONTRACT** | GOVERN | **`bindings.yaml`** — full delivery 全貌 per repo: build_system · version_set · branch · deploy_pipeline (ref) · review_path · refresh_policy · auto_send (all DATA) | ⬜ by **BIND**, not CREATE (repo shape is unknown at create) |
| ⑥ | **CODE-INTEL REFRESHER** | GOVERN | a self-contained mechanism that REGENERATES the code-intel projection from code (ship the refresher, not the projection) | ✅ `REFRESHER.md` marker (shape-neutral: states it activates on BIND, no-op for a no-repo project); **live refresher accretes** |

### The 3 default DDD-native skills (copied into every DDD's `skills/` at CREATE)

These are the self-養成 / self-propagation set. They are **DDD-native rewrites** of
SwarmAI's own skills — learned from the originals but re-designed for a DDD (file-based
`.artifacts/` state, no SwarmAI backend) so that **after `aim` export, anyone can run
them directly in Kiro / Claude Code**. We (SwarmAI, the official maintainer) keep the
canonical source at **`backend/templates/ddd-skills/s_ddd-*/`** and copy it into each
DDD at provision time (the same mechanism that copies the 4 DDD docs).

| DDD-native skill | Learned from (SwarmAI-native) | Role in the DDD |
|------------------|-------------------------------|-----------------|
| `s_ddd-manager` | `s_project-manager` | provision new spec-compliant DDDs (self-propagation seed) |
| `s_ddd-persist` | `s_persist` | sediment/refresh THIS DDD's docs (additive, honors human edits) |

> **Two distinct namespaces — do not conflate.** SwarmAI-native skills (`s_project-manager`,
> `s_persist`, `s_autonomous-pipeline`, `s_pollinate`, `s_repo-to-ddd`, `s_internal-*`)
> live in `backend/skills/` and are how SwarmAI operates — they are NEVER modified for the
> DDD work. The DDD-native `s_ddd-*` set is a SEPARATE, official, maintained template that
> ships INSIDE each DDD. An **internal DDD** (bound to a Brazil/CRUX repo, e.g. AIDLC) also
> gets `s_internal-brazil` / `s_internal-crux-cr` / `s_internal-crux-review` + a
> `gates/no_git_push.py` copied in.

**NOT a DDD member (derived/physical zone):** the code graph `code_intel.db` (rebuilt by ⑥,
never PR-flows-back; its analysis is computed on demand by
`"$SWARM_BACKEND_BIN" py -m core.code_intel.analysis <project>`, never stored),
the product source repos (GOVERNed via ⑤, never contained), the deploy pipeline (runs on
Amazon infra, referenced in ⑤, never executed). **AIM-export-form members** (`agents/*.agent-spec.json`,
`agent-sops/`, `context/`) are generated at export — NOT part of the SwarmWS-native skeleton.
Sanctioned non-section dirs at project root: `assets/`, `templates/`, `.artifacts/`.

**What CREATE provisions:** ① dir + `.project.json` + 3 manifests; ② the 4 docs + `Knowledge/`;
③ `gates/` + `gates/context/includes/` empty dirs (`.gitkeep`); ④ `skills/` **with the 3
DDD-native skills copied in**; ⑥ `REFRESHER.md` marker. **Only ⑤ waits** — `bindings.yaml`
appears when a repo is BOUND. NOT scaffolded: `agents/`, `agent-sops/` (AIM-export-form).
Project-specific content then accretes: gates as judgment matures, project skills as bound.
A **no-repo project** keeps ①-④+⑥ but ⑥ stays a no-op marker and never gets ⑤. A **GitHub
project** gets ⑤ on BIND with `remote_kind: github-pr`.

**Backfilling an existing project:** a project created before this scaffold (or lacking
`.project.json`) is migrated idempotently via
`swarm_workspace_manager.migrate_project_to_six_section(name)` — it writes `.project.json`
with a deliberate stable id (never `create_project`) then fills the skeleton only-if-absent,
preserving all hand-authored ②/⑤ content. Safe to re-run; done per-project on demand.

---

## The DDD Setup Flow — 6 phases (+ P3b), each with an exit-gate

> **Why this exists (a real failure):** a DDD was once hand-built by reading a
> source spec straight into the four docs + skills — and shipped with an EMPTY ⑤
> delivery contract and a no-op ⑥, yet "looked done." The omission was invisible
> because there was no ordered flow and no completeness gate. This section is the
> canonical order for standing up ANY DDD; **P6 is a code-enforced gate**
> (`scripts/verify_ddd_complete.py`), not an honor-system checklist.
>
> **The load-bearing principle: the flow FORKS on the governed-asset set.** ⑤ and
> ⑥ are ASSET-DERIVED (spec §3.6). So P3/P4 are **CONDITIONAL** — a data-agent or
> pure-knowledge brain does NOT map a repo and does NOT build code-intel, and must
> never be treated as incomplete for lacking them. Decide the assets FIRST (P0),
> and everything downstream follows from that decision.

| Phase | Do | Exit-gate (must pass to proceed) |
|-------|----|----------------------------------|
| **P0 — DEFINE (资产定形)** ⭐ | Before touching content, write the **governed-asset inventory**: `0..N` assets, each with a `kind` (`code-repo` / `data-source` / `skill-set` / `document-corpus` / `external-service` / `process` / …). This one decision determines the shape of ⑤ and whether ⑥ does anything. | An explicit asset list exists (a 0-asset pure-knowledge brain is a valid, complete answer — write "0 assets"). |
| **P1 — CREATE** | `create_project` scaffolds the six-section skeleton + copies the 3 native skills. | Six-section skeleton present (verified by P6's ① / ③ / ④ checks). |
| **P2 — KNOWLEDGE (the moat)** | Fill PRODUCT / TECH / IMPROVEMENT / PROJECT.md from the source (spec, code, conversation). This is where domain judgment is born. | All 4 docs substantive — **no placeholders** (P6 ② check FAILs on a stub). |
| **P3 — BIND** *(CONDITIONAL)* | **Only if P0 listed assets.** Declare each asset in `bindings.yaml`: a `code-repo` → a `bindings:` entry + `delivery_contract`; a `data-source` / `skill-set` → a `governed_assets:` entry. A **0-asset brain SKIPS this** (no `bindings.yaml`). | Every P0 asset appears in `bindings.yaml` (P6 ⑤ check). 0-asset → ⑤ is N/A, which passes. |
| **P3b — PROJECT AGENT** ⭐ | **Every brain.** Generate `project-agent.yaml` (next to `bindings.yaml`, section ⑤) from what this brain now knows — see "Generating the Project Agent" below. Show it to the person as a short table and ask them to confirm or change it. They review and revise; they never fill it in. Set `status: reviewed` and `reviewed_at` only after they confirm. | P6 ⑤ Project Agent check: present with only supported profiles and placements → PASS; missing → PENDING. |
| **P4 — REFRESHER (code graph)** *(CONDITIONAL)* | **Only for a `kind: code-repo` asset.** `artifact_cli bind <project>` (P3's bind) clones the repo and builds its code graph; the reindex job keeps it current on each commit. Nothing else to write. **`data-source` / `skill-set` / `document-corpus` / 0-asset → NO-OP.** | P6 ⑥ check: code-repo asset + code graph present → PASS; code-repo asset not yet bound → **PENDING (not a failure)**; no code-repo asset → **N/A**. |
| **P5 — CAPABILITIES** | Add/port the DDD's domain skills into `skills/s_<name>/` and register them in `aim.json` `domain_skills` (served `source_tier=ddd`). | Every `aim.json` domain skill has a dir + `SKILL.md` on disk (P6 ④ check). |
| **P6 — VERIFY** ⭐ | Run the completeness gate. It is **asset-aware**: it never fails a data-agent / pure-knowledge brain for a missing code-intel. | `"$SWARM_BACKEND_BIN" py {SKILL_DIR}/scripts/verify_ddd_complete.py --project <NAME> --record` exits **0** (no `FAIL`; `PENDING`/`N/A` are fine). |

**Progress while it is being born.** After each phase from P2 on, run the same P6
command with `--record`. It keeps the Brain `setting-up` until everything passes and
records how many checks pass, which Brain Hub shows on the card ("Being born · 4 of 7
checks"). A FAIL at that point is expected and is not a reason to stop.

### Generating the Project Agent (P3b)

The Project Agent is the delivery agent that works with the person on this brain. Its
definition is derived, not asked for. Read PRODUCT / TECH / IMPROVEMENT / PROJECT.md,
`bindings.yaml` and, for a bound repo, its `validations.json` or CI config, then write:

```yaml
schema: 1
status: draft                 # reviewed once the person confirms
reviewed_at: null
summary: <one line: what changes here usually look like>
default_profile: bugfix       # one of: full, bugfix, goal, trivial, direct, research
profile_rules:                # when to use another profile; each `when` names a kind of change
  - when: config-only or docs-only change
    profile: direct
review_focus:                 # what a reviewer must look at here, each with WHY and WHERE it came from
  - area: concurrency
    why: <the fact from the DDD that makes it matter>
    source: TECH.md
validations:                  # commands a Run must pass; copy them, never invent them
  - id: unit
    command: pytest -q
    source: validations.json  # or the CI file the command came from
placements: [chat-tab]        # chat-tab today; local-worker / hive when they exist
landing: {approval: human, via: <delivery_contract.remote_kind, or "none">}
sources: [TECH.md, bindings.yaml]
```

Rules: only the six pipeline profiles above (anything else FAILs P6). Every
`review_focus` and `validations` row names its `source`; if no source supports a row,
leave it out. A 0-asset brain gets `default_profile: research` and no validations.
Landing approval is always `human`.

**After P6 passes** (run with `--record`, so Brain Hub stops showing it as being born),
open this Brain's page for the person with the `show-brain-hub` UI action and
`brain: <NAME>`, and end with: the
brain is ready, and the next step is either to look at its map or to describe a first
change for a Run.

### Continue an unfinished setup

A Brain whose `.artifacts/setup.json` says `setting-up` shows **Continue setup** in
Brain Hub. Run P6 first (without `--record`) to see what is missing, then do only the
missing phases in order. Never redo or overwrite a phase that already passes. Finish
with P6 `--record`.

### Add knowledge to an existing Brain

Brain Hub's **Add knowledge** sends material sorted GOVERN / DISTILL / SHELF. Route
each item to the skill that owns that write, with that skill's rules:

| Role | Do | Owner |
|------|----|-------|
| DISTILL (file, doc) | Read it; sediment only what passes the admission gate into PRODUCT / TECH / IMPROVEMENT / PROJECT, additively, each entry with a source pointer | this Brain's `s_ddd-persist` |
| DISTILL (link) | Make a knowledge card first, then distill from it | `s_learn-content` → `s_ddd-persist` |
| SHELF (folder) | Mount it in the Library for recall; do not copy it into the docs | `s_library` |
| GOVERN (repo, data source) | Add it as an asset: P3 bind + P4 code-intel; if it changes how the Brain delivers, revise `project-agent.yaml` and ask the person to confirm | this skill, P3 / P3b / P4 |

A change to ③ Gates or to the Delivery rules is a proposal for the person, never a
direct write. Report what was added, what was skipped and why, and what could not be
reached. Finish with P6 `--record`.

**P6 gate — the exit门禁 for the whole flow:**

```bash
"$SWARM_BACKEND_BIN" py {SKILL_DIR}/scripts/verify_ddd_complete.py --project <NAME> --record   # --record: ready on PASS, so Brain Hub stops showing "being born"
# or, on a foreign host / arbitrary path:
"$SWARM_BACKEND_BIN" py .../scripts/verify_ddd_complete.py --project-dir /abs/path/to/DDD  [--json]
```

It checks the six sections and classifies each `PASS` / `FAIL` / `PENDING` / `N/A`.
**Only a `FAIL` is fatal** (exit 1); `PENDING` (a code-repo whose projection isn't
built yet) and `N/A` (a section that doesn't apply to this brain's asset shape) both
pass. Asset-awareness is the whole point — see the script's module docstring.

> **Relationship to the passive health scan:** `context_health_hook._check_ddd_completeness`
> is a *daily background* scan that only flags half-created projects (1–3 of 4 docs) in
> the session briefing. This P6 gate is the *on-demand* superset (full six-section +
> asset-aware ⑤/⑥) you run as the setup flow's exit criterion. Different cadence + scope
> — they do not compete.

---

## Commands

### Create a New Project

User says something like:
- "Create project MyApp at ~/code/my-app"
- "New project ClientPortal"
- "Add project DataPipeline from /path/to/repo"

#### Step 1: Extract project name and optional codebase path

From the user's message, determine:
- **Name**: Short project name (PascalCase preferred, no spaces). If not given, derive from folder name.
- **Path** (optional): Absolute path to existing codebase. This goes into TECH.md, NOT as a symlink.

If the user doesn't provide a name, ask:
> "What would you like to name this project?"

**Name normalization:** If the provided name is not PascalCase (e.g., `ai_ready_repo`, `my-app`), note it informatively but accept their choice:
> "Note: Convention suggests `AiReadyRepo`. Using `ai_ready_repo` as specified."

#### Step 2: Validate

```bash
# Check no duplicate name in Projects/
test -e "Projects/ProjectName" && echo "EXISTS" || echo "AVAILABLE"
```

**If name already exists:** Tell the user and suggest an alternative.
**If name is "SwarmAI":** Tell the user this name is reserved for the default project.

#### Step 3: Gather context (BEFORE writing DDD docs)

**CRITICAL: Do NOT write blank templates. Gather available context first, then write populated docs.**

Gather from these sources in priority order:

**3a. Session conversation (highest priority):**
Scan the current conversation for project-related information:
- Decisions made about architecture, tech stack, audience → extract for DDD
- Non-goals discussed → PRODUCT.md
- Past failures mentioned → IMPROVEMENT.md
- Current work items mentioned → PROJECT.md

**3b. Codebase files (if path provided):**

Read these files (first 80 lines each, skip if not found):
```bash
# Package managers → Stack
cat "{codebase_path}/package.json" 2>/dev/null | head -50
cat "{codebase_path}/pyproject.toml" 2>/dev/null | head -50
cat "{codebase_path}/Cargo.toml" 2>/dev/null | head -30
cat "{codebase_path}/go.mod" 2>/dev/null | head -20

# README → Vision, description
cat "{codebase_path}/README.md" 2>/dev/null | head -80

# Docs → Architecture, design decisions
ls "{codebase_path}/docs/"*.md 2>/dev/null | head -5
cat "{codebase_path}/docs/design.md" 2>/dev/null | head -100
cat "{codebase_path}/docs/architecture.md" 2>/dev/null | head -100

# Existing AI context → Conventions (merge, don't overwrite)
cat "{codebase_path}/CLAUDE.md" 2>/dev/null | head -50
cat "{codebase_path}/.cursorrules" 2>/dev/null | head -30
ls "{codebase_path}/.kiro/steering/"*.md 2>/dev/null | head -5
cat "{codebase_path}/.ai-context/TECH.md" 2>/dev/null | head -80

# Build/CI → Dev commands
cat "{codebase_path}/Makefile" 2>/dev/null | head -30
cat "{codebase_path}/justfile" 2>/dev/null | head -30
ls "{codebase_path}/.github/workflows/"*.yml 2>/dev/null | head -5

# License
cat "{codebase_path}/LICENSE" 2>/dev/null | head -3

# Git log → Current activity (only if git repo)
test -d "{codebase_path}/.git" && git -C "{codebase_path}" log --oneline -10 2>/dev/null
```

**3c. Parse what you found:**

| Source | Extract → | Target Doc |
|--------|-----------|-----------|
| README.md first paragraph | Project description | PRODUCT.md Vision |
| README.md features/usage | Key capabilities | PRODUCT.md Priorities |
| package.json `dependencies` | Frameworks (react, express, etc.) | TECH.md Stack |
| package.json `scripts` | dev, test, build commands | TECH.md Dev Commands |
| pyproject.toml `[project.dependencies]` | Libraries | TECH.md Stack |
| pyproject.toml `[tool.pytest]` | Test framework | TECH.md Stack + Dev Commands |
| docs/design.md | Architecture overview | TECH.md Architecture |
| CLAUDE.md / .cursorrules | Conventions | TECH.md Conventions |
| .github/workflows/ | CI test/build commands | TECH.md Dev Commands |
| Makefile targets | Available commands | TECH.md Dev Commands |
| LICENSE | License type | PRODUCT.md (note) |
| git log -10 | Recent activity | PROJECT.md Current Focus |
| Session conversation | Decisions, goals, blockers | All 4 docs |

#### Step 4: Create + REGISTER the project (never a raw mkdir), then write POPULATED DDD files

⚠️ **Register through `create_project` — do NOT `mkdir` a bare project dir.** A raw
`mkdir Projects/<name>/` produces a "pseudo-DDD": a directory with NO `.project.json`, which
Brain Hub (`list_brains` → `_list_project_dirs`, lists only dirs carrying `.project.json`)
**cannot see**. This is exactly how Agent-DLC/SimStock became invisible. `create_project`
writes `.project.json` (the registry file Brain Hub keys on) AND provisions the six-section
skeleton — so it is the ONLY sanctioned creation path (matches this skill's P1 phase + the ①
manifest row above).

Create + register the project via the daemon's projects API (it delegates to
`swarm_workspace_manager.create_project`, which writes `.project.json` AND provisions the
six-section skeleton — no path/CWD guessing, no manual `mkdir`):

```bash
# Register + scaffold in one call. 201 = created (it starts as setting-up in Brain Hub).
# 409 = a Brain with that name ALREADY EXISTS: stop and ask the person for another name.
# Never write into the existing one — that is another Brain's knowledge.
# 400 = the name breaks the naming rule; show the message and ask for another name.
curl -sS -X POST "http://127.0.0.1:${SWARM_BACKEND_PORT:-18321}/api/projects" \
  -H 'Content-Type: application/json' \
  -d '{"name": "ProjectName"}'
```

- **Importing an EXISTING code dir that lacks `.project.json`?** Do NOT use the create API
  (it 409s on the existing dir). Backfill idempotently instead — see the § "importing existing"
  note above (`migrate_project_to_six_section`, content-preserving).
- **Never** substitute a raw `mkdir Projects/<name>/` for this call — that reintroduces the
  pseudo-DDD (a dir with no `.project.json`, invisible to Brain Hub).

Now write DDD documents **pre-filled with extracted content** into `2-understanding/` (the
six-section layout the create step just scaffolded). For any field where NO context was found,
use the placeholder template. For fields where context WAS found, write the actual extracted content.

**PRODUCT.md template (fill extracted fields, keep placeholders for unknowns):**
```markdown
# {ProjectName} -- Product Context

## Vision

{extracted_from_README_or_session OR "_What is this project and why does it exist?_"}

## Strategic Priorities

{extracted_priorities OR:
1. _Priority 1_
2. _Priority 2_
3. _Priority 3_}

## Success Criteria

{extracted_criteria OR "- _How do you know this project is succeeding?_"}

## Non-Goals

{extracted_non_goals OR "- _What are you explicitly NOT doing?_"}
```

**TECH.md template:**
```markdown
# {ProjectName} -- Technical Context

## Architecture

{extracted_from_docs_or_session OR "_System overview, key components, data flow._"}

## Stack

{extracted_from_package_json_or_pyproject:
- **Language:** Python 3.12
- **Framework:** FastAPI 0.104
- **Database:** SQLite
- **Testing:** pytest + vitest
OR the placeholder version}

## Codebase Location

{codebase_path}

## Dev Commands

{extracted_from_scripts_or_makefile:
- **Start:** npm run dev
- **Test:** pytest --timeout=60
- **Build:** npm run build
OR the placeholder version}

## Conventions

{extracted_from_CLAUDE_md_or_cursorrules OR "_Naming, file structure, commit message format._"}

## Key Files

| Domain | Files |
|--------|-------|
| {extracted key entry points OR "_..._" | "_..._"} |
```

**IMPROVEMENT.md template:**
```markdown
# {ProjectName} -- Lessons & Patterns

## What Worked

{extracted_from_session OR "_Patterns that succeeded. Will grow through usage._"}

## What Failed

{extracted_from_session OR "_Patterns that failed, root causes, what to do instead. Will grow through usage._"}

## Known Issues

{extracted_from_session OR "_Recurring problems to watch for._"}
```

**PROJECT.md template:**
```markdown
# {ProjectName} -- Current Context

## Current Focus

{extracted_from_git_log_or_session OR "_What are you working on right now?_"}

## Open Items

{extracted_from_session OR "- [ ] _Active work item_"}

## Recent Decisions

{extracted_from_session OR "- _YYYY-MM-DD: Decision and rationale_"}

## Blocked By

{extracted_from_session OR "_Nothing currently blocking._"}
```

**.artifacts/manifest.json:**
```json
{
  "project": "{ProjectName}",
  "pipeline_state": "think",
  "updated_at": "{ISO_TIMESTAMP}",
  "artifacts": []
}
```

**Quality check:** After writing, verify that at least ONE doc has non-placeholder content (if a codebase path was provided). If all 4 docs are still 100% placeholder despite having a codebase path, something went wrong in Step 3 — re-read the key files.

#### Step 5: (No index refresh needed)

The project is discoverable the moment its `Projects/<name>/` directory exists —
recall (FTS5/BM25) and Glob scan the folder directly. The in-prompt PROJECTS.md
index was removed 2026-08-14, so there is nothing to regenerate; the new project
is live for the current session and sibling tabs immediately.

#### Step 6: Optional — create .ai-context/ in target repo

If the user said `--with-ai-context` or the conversation indicates they want the repo itself to be AI-ready (e.g., the project IS about making repos AI-ready), offer:

> "Want me to also create `.ai-context/` in the target repo? This makes the repo itself AI-ready for Claude Code / Kiro. (The DDD in SwarmWS tracks our internal development; `.ai-context/` in the repo is what other tools read.)"

If yes: copy the same populated content into `{codebase_path}/.ai-context/` with the same 4 files (minus .artifacts/ — that's SwarmWS-only).

If the user didn't mention it: skip this step silently (don't ask every time).

#### Step 7: Confirm

> "Created project **{ProjectName}** with DDD structure.
> {populated_summary: e.g., "Pre-filled from README.md (vision), package.json (TypeScript + React + FastAPI), and 3 session decisions."}
> Remaining TODOs: {list any sections still placeholder}
> Discoverable immediately in all tabs (via recall + Glob over Projects/)."

---

### Bind a Repo to a Project (DDD-as-Agent-Brain — BIND lifecycle)

User says something like:
- "Bind GCRAIDLCPreset to AIDLC"
- "Add repo github.com/awslabs/aidlc-workflows to the AIDLC project"
- "Pull the bound repos for AIDLC and build code intel"

**What BIND is (and is NOT):** BIND turns a DDD from "4 markdown docs" into a DDD that
governs real repos. It is NOT a symlink (the No-symlinks rule still holds) — it is a
**clone + code-intel index + a governance-as-DATA delivery contract**, all declared in
a per-project `Projects/<name>/bindings.yaml`. A DDD may bind MANY repos.

The full lifecycle is **CREATE → BIND → PULL → codeIntel → DEVELOP → SYNC-BACK**
(CREATE is the flow above; DEVELOP + SYNC-BACK are Steps 4-5 below). A bound repo is
not a dead-end index — it's a two-way channel: SwarmWS governs the repo (DEVELOP:
deliver a change through the binding's declared **delivery contract**), and engineer
edits to the repo's own DDD docs flow back into SwarmWS for review (SYNC-BACK:
`core.ddd_bindings.sync_back`).

**Three project shapes — the lifecycle adapts; it is NOT internal/Brazil-only.**
DEVELOP + SYNC-BACK are driven by the binding's `delivery_contract` (data, not a
hardcoded path), so the SAME lifecycle serves all three:

| Project shape | `bindings.yaml`? | BIND/PULL | DEVELOP path | SYNC-BACK |
|---|---|---|---|---|
| **Internal repo** (code.amazon.com / Brazil) | yes — `kind: internal` | PULL deferred (Midway/Brazil auth) | `s_internal-brazil` (build/release) → `s_internal-crux-cr` (CR) → `s_internal-crux-review` | yes (opt-in map) |
| **External repo** (GitHub) | yes — `kind: external` | `bind_repo` clones + indexes | normal git branch + PR + `s_expert-review` (NO CRUX, NO brazil) | yes (opt-in map) |
| **No-repo project** (pure DDD docs) | **no** — never bound | N/A — stops at CREATE | N/A — DDD docs ARE the deliverable; edit them directly | N/A |

The discriminator is the delivery contract's `remote_kind` (`code-amazon-cr` vs
`github-pr`) × `build_system` (`brazil` vs `none`) — both already in the schema below.
A no-repo project simply has no `bindings.yaml`; BIND/PULL/DEVELOP/SYNC-BACK don't
apply and the CREATE flow (4 DDD docs) is the whole story.

#### Step 1: Declare the binding in `Projects/<name>/bindings.yaml`

The schema is section ⑤ DELIVERY CONTRACT (spec §3.6, `DDD_SPEC_VERSION: 1.0`).
`bindings` is an ARRAY; each entry:

```yaml
bindings:
  - repo: adlc-workflows           # short repo name
    kind: external                 # external (github) | internal (code.amazon.com/Brazil)
    clone: "https://github.com/awslabs/aidlc-workflows.git"   # git URL/path, OR a brazil command for internal
    worktree: null                 # null → ~/.swarm-ai/bindings/<repo> (outside git-tracked SwarmWS)
    # NOTE: no `code_intel:` field — the code-intel projection is a DERIVED artifact
    # (derived-projection rule §3.6), regenerated locally by the ⑥ refresher, gitignored,
    # never a binding member. bind_repo derives the local db path from the worktree.
    delivery_contract:             # ⑤ governance-as-DATA (decision 7) — NEVER put this in STEERING
      remote_kind: github-pr       # github-pr | code-amazon-cr
      build_system: none           # none | brazil  (ORTHOGONAL to remote_kind)
      branch: main
      version_set: null            # brazil version-set (internal only); null for github
      deploy_pipeline: null        # ⑤ ref/id of the physical deploy pipeline — GOVERNed, never run here
      refresh_policy: null         # ⑤ policy: when the ⑥ refresher regenerates code-intel (e.g. on-develop)
      review_path: s_internal-crux-review
      auto_send: on-clean-review   # pipeline reaches CR-READY → reviewer → auto-send if clean
```

**Delivery policy lives in the binding, not STEERING.** Opening a bound external repo
for development does NOT mean piling repo-specific rules into global STEERING — each
repo declares its own delivery contract here (decision 7).

#### Step 2: PULL + build codeIntel (one command — never hand-roll)

Run the `bind` subcommand — it loads the project's `bindings.yaml`, loops EVERY
binding, clones each git-clonable repo OUTSIDE the git-tracked workspace, and builds
its codeIntel graph. Do NOT hand-assemble a `"$SWARM_BACKEND_BIN" py -c` loop — this is the invokable
entrypoint(`bind_repo` used to be reachable only via a prose recipe):

```bash
cd "$SWARM_REPO_ROOT" && source backend/.venv/bin/activate   # or cd to your checkout
"$SWARM_BACKEND_BIN" py -m scripts.artifact_cli bind --project AIDLC
```

Output is one JSON outcome per binding + a `counts` rollup, e.g.:

```json
{"project":"AIDLC","bindings":[
  {"repo":"adlc-workflows","kind":"external","status":"bound","worktree":"~/.swarm-ai/bindings/adlc-workflows","node_count":1037},
  {"repo":"GCRAIDLCPreset","kind":"internal","status":"deferred","error":"...Brazil+Midway..."}
], "counts":{"bound":1,"deferred":1,"failed":0}}
```

- **Per-binding isolation:** every binding yields `bound` / `deferred` / `failed`; one
  unreachable repo never aborts the others (multi-repo safe). A DDD may bind MANY repos.
- **`bound`** — git-clonable target cloned + indexed (reuses the existing parser; idempotent).
- **`deferred`** — `kind: internal` / `build_system: brazil` waits for the pre-Run-2
  Midway spike (§7.2.2).
- **`failed`** — bad bindings field or clone/index error, surfaced (not swallowed) with
  its message; the run continues. Exit 1 ONLY if a malformed `bindings.yaml` blocks the
  whole load, or every binding failed.
- Underlying core: `core.ddd_bindings.bind_project()` → `bind_repo()` per binding.
- **Internal / Brazil bindings** (e.g. GCRAIDLCPreset via `brazil ws create`) raise
  `NotImplementedError` — their PULL needs Brazil + Midway headless auth and is deferred
  to the pre-Run-2 spike. Declaring them in `bindings.yaml` is fine; PULLing them is not
  yet wired.

#### Step 3: Confirm

> "Bound **{repo}** to **{project}** — cloned to {worktree}, code-intel graph has {N} nodes.
> Delivery contract: {remote_kind} via {review_path}, auto-send {auto_send}.
> {deferred repos, if any}: declared, PULL deferred to the Brazil/Midway spike."

#### Step 4: DEVELOP — deliver a change via the binding's delivery contract (delegate, don't hand-roll)

Developing a change against a bound repo is **not this skill's job** — it routes to a
delivery skill chosen by the binding's `delivery_contract`. **Read `remote_kind` +
`build_system` and branch — do NOT assume CRUX/Brazil:**

**A) `remote_kind: code-amazon-cr` (internal — code.amazon.com):**
- If `build_system: brazil` → **`s_internal-brazil`** first (HITL build/release/workspace-sync:
  the agent detects the build system, commits locally, and diagnoses failures; the human
  runs the multi-minute `brazil-build release` + Midway/ssh-agent-walled `brazil ws sync`).
- Then **`s_internal-crux-cr`** to create the CR (agent commits locally + assembles the
  `cr` command; human runs it — `git.amazon.com` needs the ssh-agent cert). Review an
  existing CR with **`s_internal-crux-review`**.
- The agent **never** `git push`es (CRUX auto-merge owns the remote), never runs
  `cr --auto-merge`/approve, never calls `bind_repo` on the live worktree (it `rmtree`s).

**B) `remote_kind: github-pr` (external — GitHub):**
- Normal git flow: branch, commit, `git push` to a fork/branch, open a PR. This is the
  ordinary public-git path — **no CRUX, no Brazil, no `s_internal-*` skills.**
- Review the change with **`s_expert-review`** before opening the PR, not `s_internal-crux-review`.

**C) No-repo project (no `bindings.yaml`):** DEVELOP does not apply — there is no repo to
deliver to. The 4 DDD docs ARE the deliverable; edit them directly (or via `s_persist`).

**Why the internal split is HITL:** for A, the agent does the local/no-auth half; the
human runs the steps crossing the **build-duration + ssh-agent auth walls**. B crosses
no such wall (public git), so the agent can run the whole PR flow.

#### Step 5: SYNC-BACK — reflow engineer edits from the repo's DDD back into SwarmWS

An engineer may edit the bound repo's **own** DDD docs (e.g. `AGENTS.md`). SYNC-BACK
surfaces those edits back into SwarmWS for review — the reverse of BIND. Opt-in per
binding via a `sync_back` map `{repo-relative-doc → SwarmWS-relative-target}`; absent =
no-op. Call the helper — never diff by hand:

```bash
cd "$SWARM_REPO_ROOT" && source backend/.venv/bin/activate   # or cd to your checkout
"$SWARM_BACKEND_BIN" py -c "
from pathlib import Path
from core.ddd_bindings import load_bindings, sync_back
from jobs.paths import PROJECTS_DIR
doc = load_bindings(PROJECTS_DIR / 'AIDLC' / 'bindings.yaml')
for b in doc.bindings:
    # worktree_root = the ALREADY-PULLED worktree (pull it first — see below); ws_root = SwarmWS root.
    # Use Path.home(), NOT a literal '~' string — Path('~/..').resolve() does NOT expand the tilde.
    out = sync_back(b, worktree_root=Path.home()/'.swarm-ai'/'bindings'/b.repo, ws_root=str(PROJECTS_DIR.parent))
    for d in out['deltas']:
        print(f\"{d['repo_doc']} -> {d['ws_target']}: {d['status']}\")
    if out['report_path']:
        print('Review delta:', out['report_path'])
    print('Pull first (HITL):', out['pull_command'])
"
```

- **Non-destructive:** `sync_back` **only READS** the SwarmWS targets (to diff) and WRITES a
  reviewable delta report to `Projects/<repo>/.artifacts/sync-back/<ts>.md`. It **NEVER**
  mutates the live DDD docs — they carry cultivation edits a blind overwrite would destroy.
- **`git pull` is HITL:** `sync_back` performs **no** network/git call. It returns a
  `pull_command` string for the human to run, then `sync_back` diffs the pulled worktree.
  (For an internal `code-amazon-cr` binding the pull crosses the ssh-agent/Midway wall —
  human-only; for an external `github-pr` binding it's ordinary public git — the human
  runs it either way since `sync_back` never touches the network.) SYNC-BACK itself is
  delivery-contract-agnostic: it only diffs + surfaces, regardless of repo kind.
- **Delta statuses:** `changed` (with a unified diff), `unchanged`, `new-in-repo`,
  `missing-in-repo`, `binary`, `too-large` (>1 MB, diff skipped). Review the report, then
  apply anything worth keeping into the live DDD by hand (or via `s_persist`).

---

### List Projects

User says: "List my projects", "What projects do I have?", "Show projects"

```bash
ls -d Projects/*/  2>/dev/null | while read dir; do
  name=$(basename "$dir")
  if [ -f "$dir/PRODUCT.md" ]; then ddd="DDD"; else ddd="no DDD"; fi
  if [ -f "$dir/.artifacts/manifest.json" ]; then
    state=$("$SWARM_BACKEND_BIN" py -c "import json; print(json.load(open('$dir/.artifacts/manifest.json'))['pipeline_state'])" 2>/dev/null || echo "unknown")
  else
    state="-"
  fi
  echo "$name | $ddd | $state"
done
```

Present as a table:

| Project | DDD Status | Pipeline | Type |
|---------|-----------|----------|------|
| SwarmAI | Full | plan | Default |
| ClientApp | Partial (TECH.md only) | build | User |

---

### Edit a Project

User says: "Update MyApp priorities", "Add a lesson to ClientApp"

1. Read the relevant DDD document
2. Apply the user's changes using the Edit tool
3. Confirm what was changed

DDD documents are plain markdown — any edit is valid.

---

### Rename a Project

User says: "Rename project BizProject to SalesIntel", "Rename project X to Y"

#### Rules:
- **SwarmAI project CANNOT be renamed.** It's the default project.
- **Always confirm before renaming:**
  > "This will rename **OldName** to **NewName** across the workspace. Proceed?"

#### Step 1: Validate new name
```bash
# Check no collision
test -e "Projects/NewName" && echo "COLLISION" || echo "AVAILABLE"
```

#### Step 2: Rename directory
```bash
mv "Projects/OldName" "Projects/NewName"
```

#### Step 3: Update DDD doc titles
Edit the `# OldName` heading in each of PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md to use the new name.

#### Step 4: Update manifest
Edit `.artifacts/manifest.json` — change `"project": "OldName"` to `"project": "NewName"`.

#### Step 5: Update shared path constant (if applicable)
If the project is referenced in `backend/skills/_shared/project_paths.py` (e.g., `ClientOrg_PROJECT`), update that constant. Also update `backend/core/project_registry.py` if it has a named constant for this project.

#### Step 6: (No index refresh needed)
The renamed `Projects/<NewName>/` directory is discoverable immediately via recall +
Glob. The in-prompt PROJECTS.md index was removed 2026-08-14 — nothing to regenerate.

#### Step 7: Confirm
> "Renamed **OldName** → **NewName**. DDD docs updated, manifest updated. The project is discoverable immediately (no index to refresh)."

**Note:** Historical references in DailyActivity, Signals, Reports, and .artifacts/runs/ are NOT updated (they record what the name was at that time).

---

### Delete a Project

User says: "Remove project ClientApp", "Delete project Foo"

#### Rules:
- **SwarmAI project CANNOT be deleted.** If the user tries, respond:
  > "The SwarmAI project is the default project and can't be deleted. You can edit its DDD documents freely."
- For all other projects, **always confirm before deleting:**
  > "This will remove **ProjectName** and its DDD documents from Projects/. Proceed?"

```bash
# After confirmation — use trash (recoverable) not rm
mv "Projects/ProjectName" ~/.Trash/ 2>/dev/null || rm -rf "Projects/ProjectName"
```

---

## Rules

- **No symlinks** — projects are real directories with DDD documents. Codebase paths go in TECH.md.
- **SwarmAI is protected** — cannot be deleted or renamed. Can be edited.
- **Pre-fill over placeholder** — ALWAYS gather available context before writing DDD docs. Blank templates are the LAST RESORT when zero context exists.
- **Each document owns its domain** — PRODUCT.md never estimates technical cost. TECH.md never judges business priority.
- **.artifacts/ is auto-managed** — don't manually create artifact files. The lifecycle pipeline handles this.
- **Project names** — PascalCase preferred, no spaces (use hyphens if needed). Non-PascalCase accepted with informational note.
- **Read, don't ls** — when codebase path is provided, READ key files (README, package.json, docs/). Don't just check if they exist.
- **No secrets in DDD** — never copy credentials, API keys, passwords, or tokens from codebase files into DDD docs. If a file appears to contain secrets (AWS_SECRET, API_KEY, PASSWORD, TOKEN patterns), skip that content.
- **No index refresh** — the in-prompt PROJECTS.md index was removed 2026-08-14; a project is discoverable the moment its `Projects/<name>/` dir exists (recall + Glob). Nothing to refresh; never tell the user to "wait for next session."
- **Don't overwrite existing .ai-context/** — if `--with-ai-context` and `.ai-context/` already exists in the target repo, warn and skip (don't overwrite).

## Verification

Before marking this task complete, show evidence for each:

- [ ] **Project directory created/updated** — `Projects/<Name>/` exists with the expected structure shown via `ls`
- [ ] **DDD docs present AND populated** — all four files exist. If codebase path provided: at least one doc has non-placeholder content extracted from repo files
- [ ] **Artifacts directory initialized** — `.artifacts/manifest.json` exists with correct project name and pipeline state
- [ ] **TECH.md populated** — if a codebase path was provided, stack auto-detection results (actual deps from package.json/pyproject.toml) are written to TECH.md
- [ ] **Project discoverable** — `Projects/<Name>/` exists, so recall + Glob surface it immediately (no PROJECTS.md index to refresh — removed 2026-08-14)
- [ ] **Confirmation shown** — user received the creation confirmation with pre-fill summary and remaining TODOs
