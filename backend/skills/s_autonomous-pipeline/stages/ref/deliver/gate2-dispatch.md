# DELIVER — Gate-2 dispatch detail

Read when the card points here: you need the dispatch rules behind `stage-start`'s
`experts` list, a spawn failed or was rejected, or the Red Team decision. The card
(`stages/deliver.md`) holds the gate lines; this file holds the rules they rest on.
Why each rule exists: `stages/history/deliver.md`.

Gate-2 is the third gate of the pipeline's gate family: Gate 0 (diagnose-before-build,
inside EVALUATE) guards the framing, Gate 1 (Skeptic + SSA, after PLAN) guards the plan,
Gate 2 (Adversarial Review, here) guards the code. Independent sub-agents with isolated
context and one domain each break builder bias: one generalist checking security,
performance, correctness and API contract at once dilutes attention; parallel
specialists find what a generalist misses.

## Profile-aware tiering

| Profile | What runs |
|---------|-----------|
| full, standard (and every strict profile, incl. `direct`) | All scope-gated specialists + Red Team (conditional) |
| bugfix | Correctness + Security (tier default) |
| trivial | Skip entirely |
| research, docs | Skip entirely (no code changes) |

**Exception — `understanding.work_type == refactor`:** never apply the bugfix
narrowing, even when the profile resolved to bugfix. A refactor's risk is structural:
dispatch **Correctness + API-Contract + Integration + Red-Team** (Red Team
unconditionally, so a patch-shaped or wrong-layer delivery is attacked head-on).

## Dispatch rules (computed by `stage-start --stage deliver`)

`stage-start --stage deliver` computes the whole dispatch list — read its `experts`
(and `source_insertions` / `full_tier_override`); do not recount by hand. Every size
threshold here (50 / 100 / 200) counts NON-TEST source lines only: a bugfix that adds
300 lines of tests is not a 300-line change, and a test path such as
`test_security_*.py` does not fire a file trigger. The totals including tests are still
returned (`diff_insertions`) for the report.

| Specialist | Dispatch when | Checklist |
|-----------|---------------|-----------|
| **Correctness** | Always (if changeset > 50 lines) | `backend/experts/code/correctness.md` |
| **Security** | Always on a strict-profile batch; otherwise files touch: routers/, handlers, auth, database queries, user input, file paths. Also added when REVIEW left it to Gate-2 and the final diff still meets REVIEW's fan-out rule but would no longer dispatch it | `backend/experts/code/security.md` |
| **Performance** | Files touch: endpoints, database, loops over collections, hooks, background tasks | `backend/experts/code/performance.md` |
| **API Contract** | Files touch: routers/, schemas/, models, response types, frontend services | `backend/experts/code/api-contract.md` |
| **Integration** | Changeset creates new public functions, classes, hooks, or handlers | `backend/experts/code/integration.md` |
| **Operational** | Files touch: hooks/, jobs/, schedulers, daemon code, background tasks | `backend/experts/code/operational.md` |
| **Red Team** | CONDITIONAL: source changeset > 200 lines OR any specialist found HIGH severity | `backend/experts/code/red-team.md` |

**Always the UNION with the PLAN's declared `required_experts`:**
`actual_dispatch_set = union(profile_tier_default_set, run.json["required_experts"])`.
`required_experts` is append-only (BUILD may have added one), so the union is taken
against what is present NOW; `stage-start` does this. The D7 commit gate
(`_d7_missing_required_experts`) BLOCKS `run-commit` unless EVERY declared expert
actually reviewed, so dispatching exactly the set the gate verifies, in ONE batch,
avoids a wasteful second serial batch. Never constrain `required_experts` down to the
tier instead: that reopens the D7 skip channel.

**Mechanical override: source diff > 100 lines = full tier, regardless of profile**
(`full_tier_override`). Dispatch ALL applicable specialists, not the profile's subset:
a large "bugfix" that migrates code across modules carries concurrency, import-order
and dead-code risks that only full coverage catches.

If the source change is < 50 lines, skip all specialists (unless `required_experts` or
the security-owner rule adds one). Print the dispatch summary:
`Dispatching N specialists: [names]. Skipped: [names] (scope not detected).`

## Inherit REVIEW context (before dispatch)

`python backend/scripts/artifact_cli.py discover --project <P> --types review --full`.
If REVIEW's litmus verdict was BORDERLINE, inject its `weak_areas` into EVERY specialist
prompt: `"REVIEW litmus flagged weak areas: {weak_areas}. Prioritize these in your
analysis."`

## Spawning

- "ALL selected" = the full `experts` list (the union above) in ONE message of
  parallel Agent tool calls — never a tier-default batch now and a required-experts
  batch later (the second batch and a second meta-review serialize for no reason).
- Prompt: each entry's `prompt_path` file, written by `stage-start` from
  `data/gate-prompts/gate2-adversarial.md` through `spawn_prompt.py` (numeric-cap
  check, project knowledge recalled for the changed files, `<EXPERT_ID>`, `<DOMAIN>`,
  requirement and checklist filled). By hand, if a prompt file is missing:
  `python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --gate gate2-adversarial --project <P> --files <file 1> <file 2> ...`
  — use stdout ONLY on exit 0 (exit 2 = no numeric cap, exit 3 = `--project` missing;
  stdout is empty on both). In zsh pass the files as separate words.
- `EXPERT-ID: <EXPERT_ID>` (the D7 key) takes the specialist's lowercase registry id
  — the `<id>` of `backend/experts/code/<id>.md` (`correctness`, `api-contract`,
  `red-team`), never the Title-case `<DOMAIN>`. A wrong value fails the
  `^EXPERT-ID: [a-z…]$` anchor read by `runtime_hooks._extract_expert_id`; the marker
  then records no expert and D7 cannot credit it.
- Every spawn (specialists, Red Team, meta-review) uses `run_in_background: false`
  (`spawn_mode: "foreground"`): all specialists must finish before the merge, and a
  background spawn ends the visible chat stream. Do not pin a model.
- A specialist that fails or times out: log it and continue with the others' results.

<!-- R-deliver-007 -->
**Spawn-rejection discriminator (fail-closed — BLOCKING):** "Partial results > no
results" applies ONLY when **≥1 specialist actually spawned**. It does NOT cover spawn
REJECTION by the harness ("user doesn't want to proceed with this tool use" / "does not
want to take this action"):

- **≥1 specialist spawned**, others failed/timed out → partial results OK (note the
  uncovered domains in the gate outcome).
- **ZERO specialists spawned (all rejected)** → this is NOT "partial results", it is a
  BLOCKED gate. Follow INSTRUCTIONS.md Rule 23: retry the spawn batch EXACTLY ONCE →
  still all-rejected → **CHECKPOINT, reason="gate_spawn_blocked"**. Resume re-enters on
  a fresh subprocess (the poisoning clears across the process boundary).

**NEVER** treat an all-rejected spawn as license to self-review — that is the CLASS A
bypass this gate exists to prevent. There is no self-review branch.

## Red Team (conditional, sequential)

Runs AFTER the specialists complete, because it takes their findings as input (adds
roughly 30s when triggered). Dispatch ONLY IF the source changeset is > 200 lines OR any
specialist produced a HIGH finding; otherwise skip it and say why in the gate outcome.
(`stage-start` already lists `red-team` when the size rule fires; the HIGH rule is
yours to apply after the merge.)

The Red Team sub-agent receives: the checklist `backend/experts/code/red-team.md`, the
merged specialist findings (so it knows what was already caught), and the changed-file
list. Its prompt comes from the same gate2 emitter with `<EXPERT_ID>` = `red-team`. Its
findings merge into the unified list under the same dedup and confidence rules
(`ref/deliver/gate2-findings.md`).
