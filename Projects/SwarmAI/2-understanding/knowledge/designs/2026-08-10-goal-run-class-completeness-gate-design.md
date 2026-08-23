---
run_id: run_9e236074 (design) → run_1d3df9e6 (implementation)
status: IMPLEMENTED (local, push-ready) — Gate-2 hardened, 352 tests green
profile: docs (design) → full (impl)
supersedes_gap: run_0d60e04e decisions-path miss (a class sibling shipped ungated)
impl_note: |
  Built in run_1d3df9e6: scripts/check_migration_class.py (+ _test), pipeline_validator
  _check_migration_class_declared (AC11 code-enforced), goal_cycle.md step 2.5, evaluate.md
  migration_class Gate-0. Gate-2 adversarial found 6 issues, ALL fixed: #1 full-path locator
  (basename collision), #2 mandatory line-number locator (wildcard absorb), #3 empty-
  enumeration→BLOCK (fail-open), #4 deep-subdir/truncation grep→reject, #5 AC11 is now a real
  validator gate (not prose), #6 teeth via to_delivery_finding→_blocked_findings.
---

# Goal-Run FINAL Class-Completeness E2E Gate

## 0. The problem (code-verified)

A **goal run** migrates a *class* of things (all callers of a chokepoint; all siblings
of a type) across MANY cycles via strangler-fig. Each cycle's Gate-2 adversarial +
the goal_cycle **Final Quality Gate** are **DIFF-SCOPED**: they review
`git diff <start_commit>..HEAD` and ask "for each path *in the delivered code*, was it
covered?" (`goal_cycle.md:344,355`).

**A class member that NO cycle ever touched is not in the diff → it is structurally
invisible to every diff-scoped reviewer.** The 7 per-cycle adversarials in run_0d60e04e
all passed; none could see that the `all_decisions → MEMORY.md` path (a sibling of the
`all_lessons` path C5 gated) was never migrated. Only a separate, manual, whole-system
E2E pass caught it. That E2E pass is exactly what this gate makes structural.

This is the **R27 / "enumerate all entrances"** failure family, mutated for goal runs:
R27 fires on a *single commit*; a goal run spreads the migration across commits, so no
single diff triggers "grep the whole class", and there is no run-level completeness
backstop.

**Discriminator (the whole point):** *diff-scoped* review answers "is what I changed
correct?"; *class-scoped* completeness answers "did I change EVERYTHING in the class,
or is a sibling still on the old path?" The second cannot be derived from the diff —
it needs the full class enumerated independently of what was touched.

## 1. Approach (THINK → A, XG-approved direction)

**EVALUATE declares an optional `migration_class`; the final gate enumerates it
mechanically and blocks on any member that is neither migrated nor explicitly
carved-out.** Deterministic; arms ONLY when a class migration is actually declared
(no false-positive noise on feature/bugfix goal runs).

Rejected: **B** (LLM infers the class at completion — non-deterministic, reproduces the
exact miss). **C** (always-on for every goal run — false-positives on non-refactor goals).

## 1b. The circularity trap and how enumeration breaks it (Gate-2 CRITICAL, run_9e236074)

**Adversarial found the deepest hole: a gate whose `enumeration_cmd` is authored by the
same agent that did the incomplete migration inherits that agent's blind spot** — the
grep omits the same sibling the migration omitted, the gate finds no unreconciled member,
and it PASSes with the sibling still invisible. A gate that only checks "did you migrate
what you *declared*?" is false comfort; it must check "did you declare everything that
*exists*?" — which a self-authored member list cannot guarantee.

**What actually breaks the circularity (verified against run_0d60e04e): enumerate by the
physical CHOKEPOINT, never by a semantic guess.** The decisions-path miss was found by
`grep '_run_locked_write('` — the low-level sink EVERY MEMORY write must call — NOT by
"list the paths I think write to memory." A chokepoint grep is **complete by
construction**: nothing reaches the store without calling the sink, so the sink's caller
set IS the full class, independent of the author's mental model. The semantic guess
("all_lessons, all_decisions, …") is exactly the blind-spot surface; the chokepoint
(`_run_locked_write` / `apply_to_ddd`) is blind-spot-proof.

**Therefore two HARD requirements (not optional):**
- **R-A (chokepoint enumeration):** `enumeration_cmd` MUST enumerate by a **physical sink
  function** (the last-mile write/commit call every member is forced through), NOT by a
  hand-listed set of named paths. The gate REJECTS a `migration_class` whose
  `enumeration_cmd` is a literal member list / an `echo` / a per-path `grep` of
  agent-named symbols — because that re-imports the blind spot. It must be a grep for the
  SINK across the whole relevant tree (`backend/`), not a curated file subset. (A curated
  file list is the Axis-1 failure: scope the grep to 2 files and a 3rd file's caller is
  invisible — grep the sink across the tree, let the grep find the files.)
- **R-B (mandatory for migration-shaped requirements):** `migration_class` is NOT opt-in
  for a migration. If the requirement contains migrate / unify / consolidate / "route/gate
  all" / "every … through" (the AC9 heuristic), EVALUATE **BLOCKS at Gate-0** until a
  `migration_class` is declared. Opt-in was the C036 escape-hatch (an agent with an
  incomplete mental model simply doesn't declare it → no-op → the exact miss ships). The
  no-op path (AC2) survives ONLY for goals with NO migration keyword.

These convert the gate from "comfort" to "correctness": R-A makes the enumeration
blind-spot-proof (physical sink, not mental model); R-B removes the opt-out for the runs
that need it most.

## 2. Data contract — `migration_class` in the evaluation artifact

A goal run whose work is a class-migration declares this block at EVALUATE (optional;
absent → gate is a no-op, logged as "no migration_class declared"):

```json
"migration_class": {
  "description": "every path that writes new knowledge to a cognitive store",
  "enumeration_cmd": "grep -rn '_run_locked_write(\\|apply_to_ddd(' backend/ | grep -v 'def '",
  "members": [
    {"id": "all_lessons→MEMORY",     "disposition": "migrated",   "locator": "distillation_hook.py:598", "evidence": "_admit_memory_lesson"},
    {"id": "all_decisions→MEMORY",   "disposition": "migrated",   "locator": "distillation_hook.py:558", "evidence": "_admit_memory_lesson gate at :558"},
    {"id": "writeback→apply_to_ddd", "disposition": "migrated",   "locator": "improvement_writeback_hook.py:50", "evidence": "_gate_and_apply_writeback"},
    {"id": "orchestrator_refresh×4", "disposition": "carved-out", "locator": "ddd_orchestrator.py:1550", "evidence": "value-refresh, not ingestion (design §4b)"},
    {"id": "COE_registry→MEMORY",    "disposition": "carved-out", "locator": "distillation_hook.py:1615", "evidence": "structural Open-Thread reconcile, code-enforced"}
  ]
}
```

> **`locator` vs `evidence` (build-time contract refinement, run_1d3df9e6):** a member
> needs BOTH. `locator` = the file:line of its SINK CALL — it ties the member to WHICH
> live grep line it is (the reconciliation key). `evidence` = the PROOF of its
> disposition — a new gated symbol for `migrated`, a one-line reason for `carved-out`.
> They must be separate fields: a carved-out member's evidence is a *reason string*, which
> can never match a live line, so matching-by-evidence wrongly flags every carve-out as
> MISSED. The gate reconciles by `locator`, checks `evidence` per disposition.

- **`enumeration_cmd`** — a grep/command that lists the FULL class from live source
  (the ground truth). Re-run at the final gate; its output is the member set to reconcile.
- **`members[]`** — the run's CLAIM: each member + `disposition ∈ {migrated, carved-out}`
  + `evidence` (a file:symbol the gate can verify exists). Built incrementally as cycles
  land (a cycle that migrates a member updates its row).
- **`disposition`** has only two legal terminal values. There is no "pending" at the
  final gate — an un-dispositioned member = BLOCK.

## 3. The gate — `goal-e2e-completeness` (inserted in goal_cycle Final Quality Gate)

Runs in the **Final Quality Gate** (goal_cycle.md §"EXIT with SUCCESS"), as **step 0.5**
— AFTER cross-cycle re-judgment (step 0) and the cross-path adversarial (steps 1-2),
BEFORE marking the stage complete. Sequence rationale: the adversarial may itself add a
migrated member; completeness reconciles the final set.

**Algorithm (deterministic):**
```
if evaluation.migration_class is absent:
    log "no migration_class — completeness gate is a no-op"; PASS
else:
    live_members  = run(enumeration_cmd)                    # ground truth from source
    claimed       = {m.id: m for m in migration_class.members}
    for each live member L:
        matched = the claimed row whose evidence-symbol appears on L's file:line
        if no matched row                      → MISSED(L)   # a sibling nobody declared
        elif matched.disposition == "migrated":
             verify matched.evidence symbol EXISTS in source → else EVIDENCE_STALE(L)
        elif matched.disposition == "carved-out":
             carve-out has a one-line REASON   → else UNJUSTIFIED_CARVEOUT(L)
    any MISSED / EVIDENCE_STALE / UNJUSTIFIED_CARVEOUT  → BLOCK (emit coverage table)
    all members migrated|carved-out with live evidence  → PASS
```

**Output — a coverage table (always emitted, the audit artifact):**
```
CLASS: every path that writes new knowledge to a cognitive store  (5 live members)
  ✅ all_lessons→MEMORY      migrated    _admit_memory_lesson            [verified]
  ✅ all_decisions→MEMORY    migrated    distillation_hook.py:558        [verified]
  ✅ writeback→apply_to_ddd  migrated    _gate_and_apply_writeback       [verified]
  ⚪ orchestrator_refresh×4  carved-out  value-refresh (design §4b)      [reason ok]
  ⚪ COE_registry→MEMORY     carved-out  structural reconcile            [reason ok]
  → PASS (5/5 dispositioned, evidence live)
```
A MISSED row renders `❌ <id>  UNDECLARED — a class sibling on the old path` → BLOCK.

**TEETH (the R27/step-10.5 lesson — a gate that only prints changes nothing):** a BLOCK
here carries into the DELIVER `adversarial_review.findings[]` as a
`severity=HIGH, confidence=9` finding (`class_completeness:<member>`), so the existing
`_blocked_findings` confidence gate in `pipeline_validator.py` blocks COMPLETE exactly
as a fresh adversarial finding would. The coverage table is working memory; the DELIVER
artifact is the enforcement surface. (Mirrors goal_cycle.md:264-272 verbatim.)

## 4. Acceptance Criteria

- **AC1** — `goal_cycle.md` Final Quality Gate gains step 0.5 `goal-e2e-completeness`,
  positioned after cross-path adversarial, before stage-complete.
- **AC2** — Gate is a **no-op + logged** when `migration_class` is absent **AND the
  requirement has no migration keyword** (feature/bugfix goal runs unaffected; zero
  false-positive). If a migration keyword IS present, absence is a BLOCK, not a no-op (AC11).
- **AC3** — Gate runs `enumeration_cmd` against LIVE source and reconciles every returned
  member against `members[]`. A live member with no `members[]` row = **MISSED → BLOCK**
  (this is the decisions-path catch).
- **AC4** — `migrated` disposition BLOCKS if its `evidence` symbol is not found in source
  (EVIDENCE_STALE — catches a claimed-but-reverted migration).
- **AC5** — `carved-out` disposition BLOCKS if it has no one-line reason (UNJUSTIFIED —
  forces "why is this not ingestion?" to be stated, per design §4b honesty clause).
- **AC6** — a BLOCK is carried into DELIVER `adversarial_review.findings[]`
  (`severity=HIGH`) so `_blocked_findings` enforces it; a print-only gate is rejected.
- **AC7** — the coverage table is emitted on BOTH pass and block (the audit artifact).
- **AC8 (negative test)** — a fixture goal run with a `migration_class` whose
  `enumeration_cmd` returns a member absent from `members[]` MUST BLOCK with a MISSED row
  (proves the gate catches the run_0d60e04e class of miss; without it the case is vacuous).
- **AC9** — EVALUATE stage doc (`evaluate.md`) documents `migration_class` + the
  migration-keyword heuristic ("migrate / unify / consolidate / route-all / every…through").
- **AC10 (anti-circularity — Gate-2 CRITICAL, R-A)** — the gate REJECTS a `migration_class`
  whose `enumeration_cmd` is a literal member list, an `echo`, or a curated per-symbol grep
  over a hand-picked file subset. `enumeration_cmd` MUST grep a **physical sink function**
  (the last-mile write/commit call, e.g. `_run_locked_write` / `apply_to_ddd`) across the
  relevant tree (`backend/`), so the caller set is complete by construction and cannot
  inherit the author's blind spot. A fixture with a member-list-style enumeration_cmd MUST
  be rejected (negative test).
- **AC11 (mandatory-for-migration — closes the C036 opt-in escape, R-B)** — if the goal
  requirement contains a migration keyword, EVALUATE Gate-0 BLOCKS until `migration_class`
  is declared. The no-op path exists ONLY for keyword-free requirements. A fixture
  migration-keyword requirement with no `migration_class` MUST fail Gate-0 (negative test).

## 5. Change spec (for the follow-on goal/full run — NOT this docs run)

| # | File | Change |
|---|------|--------|
| 1 | `stages/goal_cycle.md` | add step 0.5 `goal-e2e-completeness` in Final Quality Gate (algorithm §3) + BLOCK-carries-to-DELIVER teeth |
| 2 | `stages/evaluate.md` | document `migration_class`; **Gate-0 BLOCKS** on a migration-keyword requirement with no `migration_class` (AC11/R-B) |
| 3 | `scripts/pipeline_validator.py` | recognize `class_completeness:*` in `adversarial_review.findings[]` (already blocks HIGH; verify no allowlist excludes the prefix) |
| 4 | `scripts/check_migration_class.py` (new, unit-testable) | (a) VALIDATE enumeration_cmd is chokepoint-shaped, reject member-list/echo/curated-subset (AC10/R-A); (b) run it on live source; (c) reconcile → coverage table + block list. AC8 + AC10 + AC11 negative tests land here. |

## 6. Boundaries

### Always
- Gate arms ONLY on a declared `migration_class` (no-op otherwise).
- `enumeration_cmd` reads LIVE source (never a cached member list — the whole point is
  to catch a sibling the cached view omits).
- A BLOCK has teeth (DELIVER finding), never print-only.

### Never
- Do NOT infer the class with an LLM at completion (approach B — reproduces the miss).
- Do NOT run for every goal run unconditionally (approach C — false-positive noise).
- Do NOT let `carved-out` be a free pass — it requires a stated reason (AC5), else the
  carve-out becomes the new hiding place.
- Do NOT accept a member-list / echo / curated-file-subset `enumeration_cmd` — it
  re-imports the author's blind spot (AC10). Grep the physical sink across the tree.

### Honest residual (the boundary R-A cannot cross)
R-A (chokepoint enumeration) is blind-spot-proof **only when a true single physical sink
exists** for the class (MEMORY has `_run_locked_write`; DDD has `apply_to_ddd`). For a
class whose members are genuinely SCATTERED writers with no common last-mile call, no
grep is complete-by-construction, and the gate degrades toward the Axis-1 circularity
(the enumeration is only as good as the author's pattern). Mitigation: the gate should
WARN when `enumeration_cmd` matches multiple distinct sink symbols (a sign the class may
lack a single chokepoint) so the reviewer knows completeness is not guaranteed. This is a
known limit, stated — not silently assumed away. (The two classes this run cares about
BOTH have single sinks, so it holds here; a future scattered-writer class needs a
different completeness strategy, e.g. a type-system/registry that forces registration.)

## 7. Success Criteria (DoD for the follow-on implementation run)
- goal_cycle Final Quality Gate blocks a fixture migration run with an undeclared live
  member (AC8 negative test green).
- A declared, fully-dispositioned class PASSES with a coverage table.
- `migration_class` absent → no-op (existing goal runs unaffected; their tests still green).
- The BLOCK reaches `_blocked_findings` (verified: a `class_completeness` finding blocks
  COMPLETE like any HIGH finding).
