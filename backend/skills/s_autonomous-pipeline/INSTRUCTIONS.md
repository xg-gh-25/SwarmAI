# Pipeline Orchestrator — orchestration card

You ARE the orchestrator: run every stage inline in this session, never via a sibling skill or slash command. A run delivers an accountability unit of work: a defensible commit plus a short, honest REPORT.md (omit fine-grained detail and anything uncertain). `ref/` paths below are under `backend/skills/s_autonomous-pipeline/stages/`.

## Core Loop
1. **INIT**: `run-create --project <P> --requirement "<text>"` (`--profile direct` for a small, fully specified change: `ref/instructions/init-and-profiles.md`), emit the briefing; resume a paused run instead if one exists (`ref/instructions/init-and-profiles.md`).
2. **PROFILE**: EVALUATE's scope picks the profile (table below); state it with a one-line why.
3. **STAGE**, for each profile stage: `stage-start` → SIGNAL/CHECK/FAIL line → read the card → execute → classify decisions → `stage-complete` → landmark → continue. ★ Gate 0 fires inside EVALUATE, ★ Gate 1 after PLAN (④), ★ Gate 2 inside DELIVER (⑧).
4. **DELIVER**: Taste Decision Gate → deliver card → Quality Convergence Loop (6-layer gate × max 3) → push-ready or escalate (`ref/instructions/deliver-convergence.md`).
5. **REFLECT**: `stages/reflect.md`. 6. **COMPLETE**: Step 6 below.

## Profiles
Authoritative stage lists: `backend/core/pipeline_profiles.py`. trivial/research run relaxed; every other profile is strict.

| Profile | Stages | When |
|---|---|---|
| full | evaluate think plan build review test deliver reflect | standard/complex; default |
| bugfix | same as full | a bug |
| trivial | evaluate think build review test deliver reflect | tiny change |
| research | evaluate think reflect | research only |
| goal | evaluate think plan goal_cycle deliver reflect | must iterate: metric, sweep, convergence |
| direct | plan build deliver | small, fully specified change; PLAN holds the exit contract |

Prefer the lightest profile that fits; goal only when you must iterate, not because the result is checkable. ADVERSARIAL (stage 7 of 9) runs inside DELIVER, so no profile lists it. Max retries: build/test 3, deliver/reflect 1, others 2.

## 3c. Execute Stage Behavior
- **Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage <S>` → card path, upstream artifacts, ACs, exit criteria, examples (review/deliver: changed files, experts, prompts). Read its stdout as is.
- **One command per call** (no `cd … &&`, `source`, `;`, pipes, redirects); copy the absolute command last printed.
- <!-- R-instr-004 --> **[GATE·cli] BLOCKING:** before executing ANY stage, read its execution card `stages/<S>.md`; read a `stages/ref/` file only when the card's "Read only if" condition holds. Skipping the card skips the stage's quality gate (`stage_doc_consumed: true`; run-update exits 1 without it). Read from `backend/skills/`, never `.claude/skills/`.
- **Tests:** only `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode tdd|verify|regression|recheck [--files …]` (`probe|mutation`: heavy checks); `continue: true` → rerun it. Direct pytest/vitest/tsc: denied in a run.
- **Exit:** `python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage <S> --data @/tmp/<R>/<S>.json` with `{"artifact": {...}, "record": {"decisions": [...]}}`. It validates first (each error names the `path` and expected value; fix from that, never from artifact_cli.py source), fills derived fields, publishes, records, advances. No `--check` preflight (a failure writes nothing); `--check` only to ask mid-stage whether the exit criteria are met. Exit criteria met → stop verifying.
- Legacy publish / run-update / advance still work: `ref/instructions/cli-reference.md`.

## Decisions
Classify every non-trivial decision in `record.decisions`: **Mechanical** (do it), **Taste** (take the default, disclose in ONE line), **L2 Judgment** (ambiguous AND hard to reverse: ask the user). Unsure between taste and L2 → taste.

## Human in the loop: less, not never
Autonomy = the user intervenes less, not never. Genuinely unclear requirement or L2 decision → ASK: `channel=direct` → AskUserQuestion now (options + recommendation), same run continues; else record the question, `run-checkpoint`. Never push on with a guess. Mechanical dead ends never wait for a human: stuck (retries exhausted, circuit breaker, unresolved HIGH) → DELIVER `push_ready: false`; a gate error at `stage-complete` → degraded at once; spawn refused twice → `<gate>_unverified_reason`. Approval prompts for dangerous/external actions always stay. Full rule: `ref/instructions/escalation.md`.

## Fixing findings `[MUST]`
Any review's findings (gates, REVIEW, Gate-2, meta-review, a red test run): collect the whole round, group by cause, ONE fixing spec, fix it in one pass, re-verify once. Never one by one: `ref/instructions/fixing-findings.md`.

## Checkpoint
For a question no attended channel can answer, or when the user asks to pause. Budget or token use is NEVER a trigger (`run-checkpoint` refuses it): `run-checkpoint --project <P> --run-id <R> --stage <next> --reason "<why>"`. Details: `ref/instructions/checkpoint-and-budget.md`.

<!-- R-instr-007 -->
> **⏭️ [MUST] Continuation contract (§3f): advance is not the end of your turn.** `stage-complete` (or legacy `advance`, which prints the next card as `next_stage_doc`) only records the transition; read the next card and execute it **in the same turn**. Legal hand-backs ONLY: a question you cannot answer yourself (`escalation.md`), a user stop, or a terminal verdict (abandon, DEFER/REJECT). "Proceeding to <STAGE>" + ending the turn is a stall. `next_action` wins when it withholds the continue.

## Sub-agent spawns (Rule 23)
<!-- R-instr-037 -->
**[MUST] Every spawn prompt is BOUNDED — investigate first, delegate the JUDGEMENT.** A sub-agent is a second pair of eyes on YOUR conclusion, not a re-run of your investigation. Single authority for hand-written spawn prompts.
- **Finite item list:** "confirm or refute these N things" (N ≤ 3-4, each with the file:line you read). Never ask it to prove a negative; that never terminates.
- **Don't delegate your own grep.** "Verify each / grep and confirm" moves your judgement cost onto the sub-agent.
- **Spawn in the FOREGROUND — every pipeline Agent tool call passes `run_in_background: false`.** A background spawn ends your visible stream; the verdict lands outside the stage. Parallelism = several Agent calls in ONE message. `stage-start` returns `spawn_mode: "foreground"`; the run profiler flags any background spawn.
- **State the scope budget in the prompt** (files to read, tool calls, answer length). Bound SCOPE, never the clock. Never let the budget authorize skipping a checklist item: the reviewer writes `N/A: <reason>` or `UNCHECKED: <item> — budget exhausted`.

Gate-0/1/2 prompts are emitted, not composed (`spawn_prompt.py --gate <id>`, exit 0 only). A rejected gate spawn: retry once, then record `<gate>_unverified_reason` (push_ready capped) and go on; never self-review (`ref/instructions/rules-23-24.md`).

## Output (Complete Stage Reference)
Landmark after each stage: `## ✦ <n> STAGE` + `→ <one-line result>`; gates use `★`. Circled numbers are mandatory:
① EVALUATE `→ GO | profile | scope` + ★ GATE 0 · ② THINK `→ N alternatives, N probes, approach` · ③ PLAN `→ N AC, N files, spec` · ④ PRE-CHECK `★ GATE 1: PASS/WARN/BLOCK | detail` · ⑤ BUILD `→ TDD: NR→NG | N tests, N commits` · ⑥ REVIEW `→ N checks, N findings (M fixed) | constraints` · ⑦ TEST `→ N passed, M failed | regression: clean` · ⑧ ADVERSARIAL `★ GATE 2: N findings → M fixed | convergence: i/3` · ⑨ DELIVER `→ 6L gate: pass | push-ready: yes` · ⑩ REFLECT `→ N lessons → IMPROVEMENT.md`.
After ①, relay EVALUATE's `_display_block` (the requirement understanding); never hand-type it.
**Suppress CLI JSON** (one-line `art_xxxx ✓`), EXEMPT `next_action` / `next_stage_doc` / `next_card`: read them, they carry the continuation. No text before tool calls, no prose between stages. Phase headers, gate blocks, token targets: `ref/instructions/progress-display.md`.

## Step 6 COMPLETE
After `stages/complete.md` Step 0: **6.1** `run-update --project <P> --run-id <R> --status completed` (BLOCKS on incomplete stages, missing lessons, REPORT.md <500 bytes, unsurfaced outputs). **6.2** Read `stages/complete.md` fresh, relay `_display_block` verbatim plus a ```next-steps block. **6.3** STOP.

## Where things live (`stages/ref/instructions/`)
| Concept | File |
|---|---|
| Step 1 INIT, briefing, resume; Step 2 PROFILE, why ≤8 stages, Profile Selection Principle, Goal Profile Orchestration | `init-and-profiles.md` |
| Stage Feedback Loop, 3a Gate Check, 3b Load Stage Context, Upstream artifacts (Output Routing), Understanding Gate, 3c dispatch table, schema-first, 3f Handle Result | `stage-execution.md` |
| 3e Verify Stage Output (validator, `checks_passed`, DDD checksums) | `validator.md` |
| 3d Classify Decisions, when to ask vs decide, Taste vs L2 | `escalation.md` |
| Unified fixing: one evaluation, one fixing spec, one fix pass | `fixing-findings.md` |
| Checkpoint Protocol (true-trigger), Budget Tracking, `token_cost` | `checkpoint-and-budget.md` |
| Step 4: 4a Taste Decision Gate, 4c Quality Convergence Loop / 6-Layer Push-Ready Gate, L4 verify-against-disk, 4d Report & CI | `deliver-convergence.md` |
| Step 5 REFLECT, Step 6 completion gate + report ordering, North Star | `completion.md` |
| Progress Display, ① requirement emit, Token Conservation Rules | `progress-display.md` |
| Rules 1–22, [GATE]/[MUST]/[GUIDE] legend | `rules.md` |
| Rule 23 (`gate_spawn_blocked`, two-field evidence), Rule 24 (required fields) | `rules-23-24.md` |
| Artifact Operations Reference, Observe / Abandon Protocols, Background Execution | `cli-reference.md` |

Rationale and incidents: `stages/history/instructions.md` (never read at runtime).
