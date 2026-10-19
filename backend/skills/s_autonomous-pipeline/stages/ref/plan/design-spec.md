# Design spec — find the root, fix the class, think it through, break it yourself

Five validator gates (`backend/scripts/design_spec.py`) that close one failure: the run
reads the surface, picks a one-instance fix, starts coding before the design is thought
through, review then finds the holes one by one, and each hole gets a one-line patch that
leaves the class open — whack-a-mole, round after round.

| Gate | Stage | Field | Checked by |
|------|-------|-------|------------|
| Root cause | EVALUATE | `understanding.root_cause` | `root_cause_errors` |
| Solution class | THINK | `solution_class`, `alternatives` | `solution_errors` |
| Design spec | PLAN | `artifact.design` (+ `root_invariant`) | `plan_errors` |
| Break tests | BUILD / goal_cycle | `break_tests` | `build_errors` |
| Fix by class | REVIEW + DELIVER | each fixed blocking finding + `design_updates` | `finding_errors` |

The chain: the root's **missed invariant** (EVALUATE) → the fix that restores it for the whole
class (THINK) → the invariant `I<n>` the design enforces (PLAN `root_invariant`) → the test that
tries to break it (BUILD) → every review finding measured against the invariants (REVIEW /
DELIVER).

**Who it applies to:** full / goal (and any unknown profile — fail closed) need the whole spec.
bugfix / direct need `flow`, `states`, `invariants`, `failure_modes` (and the root cause).
trivial / docs / research are exempt. Runs created before the cutover (`CUTOVER` for §1–3,
`ROOT_CUTOVER` for §0; the ledger's date, not run.json's writable copy) get warnings, not
errors; a run with no readable date is judged.

## 0. EVALUATE — dig to the root; THINK — fix the class

Do not stop at the surface. The symptom is where you start, not what you fix.

```json
"understanding": {"...": "...", "root_cause": {
  "why_chain": [
    {"id": "W1", "because": "the symptom: X happens because Y", "evidence": "file.py:120"},
    {"id": "W2", "because": "Y happens because the module does Z for every caller", "evidence": "grep: 3 callers"},
    {"id": "W3", "because": "Z was designed without the rule that ... must hold", "evidence": "git log -S of the choice"}],
  "root_layer": "module-design",
  "missed_invariant": "the rule whose absence lets the whole class happen",
  "recurrence": {"searched": ["git log -S'<pattern>'", "IMPROVEMENT.md What Failed", "grep -rn '<shape>'"],
                 "prior_instances": ["abc123: the same symptom patched at file.py:130"],
                 "siblings": [{"id": "C1", "where": "other.py:40", "shape": "same Z, no rule"}]}}}
```

- **why_chain**: ≥ 3 levels `W<n>`, each "X because Y" with an observation. Keep asking *why*
  until the answer is a design decision or a missing rule ("nothing enforces Y"), not a line
  of code ("the code does X" is still a symptom).
- **root_layer**: `code-line` · `module-design` · `contract` · `architecture` · `process` ·
  `user-need` (for a feature: the real problem behind the request).
- **recurrence**: ≥ 2 searches actually run (git log, IMPROVEMENT "What Failed", EVOLUTION,
  grep for the same shape). `prior_instances` = the same symptom fixed before; `siblings`
  `C<n>` = the same shape elsewhere. Either one non-empty means this is a **class**.
- **Profile**: `bugfix` (a smallest-diff fix) only when `root_layer == code-line` with no prior
  instance and no sibling. Anything else is whack-a-mole as a bugfix — run `full`.
- The Gate-0 skeptic gets the root cause and attacks it: is the last level the root or another
  symptom, and is there an instance the search missed? (`WRONG-FRAME` sends you back.)

THINK then declares what kind of fix the recommendation is:

```json
"alternatives": [{"...": "..."}, {"constraint": "QUALITY", "what": "...", "targets_root": true}],
"solution_class": {"verdict": "systemic", "addresses": "W3",
  "why_root": "removes the cause every instance comes from",
  "covers": [{"id": "C1", "how": "other.py moves onto the enforced path"}],
  "structural_change": "refactor", "structural_reason": "the rule must live in one place"}
```

- ≥ 2 `alternatives`, ≥ 1 with `targets_root: true`.
- `addresses` = the deepest `W<n>`, or `why_not_deepest` (≥ 20 chars).
- `covers` every sibling `C<n>`: `{id, how}` or `{id, excluded: <why>}`.
- `structural_change` none / refactor / redesign + `structural_reason`. When the root is at
  `module-design` / `contract` / `architecture`, `none` needs a ≥ 40-char reason: if the design
  is the cause, refactor or redesign — don't patch around it.
- `verdict: "patch"` is legal only as a declared P0 stop-the-bleeding: `p0_reason` + a
  `follow_up` (the root-fix run / ToDo). An undeclared patch does not pass.
- PLAN `design.root_invariant` names the `I<n>` that encodes `missed_invariant`.

## 1. PLAN — `design`

If you cannot fill these in, you have not thought the change through. Do not write a change
spec yet: go back to the code and the user flow until you can.

```json
"design": {
  "user_workflow": ["1. today: the user ...", "2. after: the user ... then ..."],
  "architecture": "flowchart LR\n  A[component] -->|data| B[component]\n  B --> C[(store)]",
  "flow": "flowchart TD\n  S[start] --> C{check}\n  C -->|ok| D[do]\n  C -->|fail| E[refuse + message]",
  "states": [{"id": "S1", "name": "the new state",
              "created_by": "file::symbol", "removed_by": "file::symbol | never: <why safe>",
              "crosses": [{"with": "landed / killed / paused / resumed / another session",
                           "outcome": "what happens, and why that is correct"}]}],
  "invariants": [{"id": "I1", "statement": "what must always hold", "enforced_at": "file::symbol"}],
  "failure_modes": [{"id": "F1", "scenario": "killed between A and B", "expected": "next call recovers"},
                    {"id": "F2", "scenario": "two sessions at once", "expected": "one wins, the other refuses"}]
}
```

- **user_workflow** (full tier): ≥ 2 steps, before → after, as the user or the agent lives it.
- **architecture** (full tier): a mermaid diagram (`graph` / `flowchart` / `sequenceDiagram` /
  `stateDiagram`) with ≥ 2 edges: the components and the data between them.
- **flow**: a mermaid diagram of the main path with ≥ 2 edges AND a failure branch (fail,
  error, refuse, deny, timeout, kill, conflict, retry, abort, invalid). A happy-path-only flow
  is rejected: the bugs live on the branch you did not draw.
- **states**: every state the change adds or touches — a file, a marker, a DB row, a flag, a
  lock, an env var, a cache. For each: who creates it, who removes it (`never: <why safe>` is
  an answer), and what happens when it meets every existing state or event (`crosses`). The
  question to ask: "what if this outlives the thing that made it?" No state at all → an empty
  list plus `no_state_reason` (≥ 20 chars).
- **invariants**: numbered `I<n>`, each a statement (≥ 15 chars) and where it is enforced.
  These are what BUILD tests and what every review finding is measured against.
- **failure_modes**: numbered `F<n>` (≥ 2 full, ≥ 1 light): interruption / kill, concurrency,
  retry / re-entry, partial completion, stale data, a foreign repo or user. Each with the
  expected outcome.

## 2. BUILD — `break_tests`

Before any expert sees the change, write the tests that try to break your own design. Every
plan `I<n>` and `F<n>` must be targeted by at least one.

```json
"break_tests": [{"targets": ["I1", "F1"], "test": "backend/tests/test_x.py::test_kill_mid_way",
                 "attack": "kills the process between A and B, then calls again"}]
```

- `test` is `path::test_name` (`.py` / `.ts` / `.tsx` / `.js` / `.jsx`).
- `attack` (≥ 10 chars) says how it tries to violate the target — not what it asserts.
- A test that only walks the happy path is not a break test.

Experts confirm what you already tried to break. They are not your test designers: if a
reviewer finds a hole that a break test should have found, that is a missed invariant (§3).

## 3. REVIEW + DELIVER — fix by class

Every fixed blocking finding (HIGH / CRITICAL, or MED at confidence ≥ 7, with
`resolved: true`) carries:

```json
{"invariant": "I1", "root_cause": "why the DESIGN allowed it",
 "class_fix": "where the invariant is now enforced for every instance",
 "break_test": "backend/tests/test_x.py::test_the_class"}
```

- **invariant**: the `I<n>` it violated. If no invariant covers it, the design missed one:
  add it to `design_updates: [{id, statement, why_missed}]` (artifact or record) and fix the
  design, not only the line.
- **root_cause** (≥ 20 chars): why the design let it happen, not what the line did.
- **class_fix** (≥ 20 chars): every place the invariant must hold, now enforced. One finding
  is usually one instance of a class; fix the class.
- **break_test**: the `path::test` that proves the class is closed (it fails on the old code).

Checked: REVIEW `findings` (required as a list once `findings_count` > 0), and DELIVER /
goal_cycle `adversarial_review.findings`. Blocking = the shared Gate-2 rule
(`core/gate2_findings.is_blocking`).

Not judged: a finding recorded as `"finding": "Not a bug: <evidence>"` (a false positive is
recorded, never dropped — `ref/deliver/gate2-findings.md` — and is not a defect to fix), and
an `adversarial_review` with `profile_tier: "skipped"`.

## Why

The run that earned this went through three spec BLOCKs and repeated Gate-2 rounds, because
the state lifecycle (who creates a marker, who removes it, what happens after landing) was
never designed, each finding was patched one line at a time, and no test of the builder's
own tried to break the change. Each gate here makes one of those structurally required.
