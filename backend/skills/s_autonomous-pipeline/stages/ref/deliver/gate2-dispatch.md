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
| every strict profile (full, standard, bugfix, `direct`) | Correctness; Security + Red Team only on a clear security risk |
| trivial, research | Skip (only a governed-context change or a declared expert) |

There is no refactor or bugfix tier of its own: correctness's checklist holds the
structural lenses (callers, contracts, state machines).

## Dispatch rules (computed by `stage-start --stage deliver`)

`stage-start --stage deliver` computes the whole dispatch list — read its `experts`
(and `source_insertions`); do not recount by hand. Every size
threshold here (50 / 200) counts NON-TEST source lines only: a bugfix that adds
300 lines of tests is not a 300-line change, and a test path such as
`test_security_*.py` does not fire a file trigger. The totals including tests are still
returned (`diff_insertions`) for the report.

| Specialist | Dispatch when | Checklist |
|-----------|---------------|-----------|
| **Correctness** | Source changeset >= 50 lines | `backend/experts/code/correctness.md` (also holds the performance, API-contract, integration, operational, state-machine and concurrency items) |
| **Security** | A clear security risk, at any size above the small-change waiver | `backend/experts/code/security.md` |
| **Red Team** | A clear security risk AND source changeset >= 50 lines | `backend/experts/code/red-team.md` |

**A clear security risk** (`_security_risk`, docs/design/pipeline-gate-expert-simplification.md
E1): a non-test path naming auth, login, token, secret, credential, permission, file
access, CORS, `security_hooks`, `hook_builder` or a `*_gate.py`, or `backend/context/`;
or an added line with `subprocess`, `shell=True`, `os.system`, an outbound HTTP client,
`pickle.load`, `yaml.load(`, `eval(` / `exec(`, `allow_origins`, a `host=` / `port=`
listener setting, `0.0.0.0` or a bind.
Size alone never adds Security or Red Team.

**Always the UNION with the PLAN's declared `required_experts`:**
`actual_dispatch_set = union(profile_tier_default_set, run.json["required_experts"])`; a
declared id outside correctness / security / red-team maps to correctness.
`required_experts` is append-only (BUILD may have added one), so the union is taken
against what is present NOW; `stage-start` does this. A declared expert that did not
review sets push_ready false (`_d7_missing_required_experts`); it never blocks the local
landing, which only the test evidence decides. Dispatch exactly that set, in ONE batch.
Never constrain `required_experts` down to the tier instead: that hides the gap.

**Small change (≤ 20 non-test source lines, no new code file, no sensitive path —
auth / data / infra / config, the REVIEW fan-out pattern):** no Gate-2 specialist, no
meta-review, no re-check, declared `required_experts` included (`_small_change`, XG
2026-10-20). Changed again after a review by ≤ 20 lines with no added file: the review
holds, no re-review (`pipeline_worktree._small_review_delta`; the delta is recorded in
`review-tree.json`). A larger change after review: re-check only that delta, never a fresh round 1 —
`gate2-recheck` within DELIVER; on a DELIVER re-entry (or a new cycle after BUILD
rework) stage-start itself hands each expert that already reviewed the change a
`<id>-delta.md` prompt with the diff since its review, or no spawn when that diff is
≤ 20 lines with no new file (`_reentry_delta`, reported as `gate2_reentry`).

**Risk, not size, picks the tier.** There is no line-count override to the full tier.
A change with no code file (`pipeline_validator.docs_only_paths`: prose, images; not
expert / skill / agent definitions, not dependency manifests) gets no specialist and no
meta-review, and its DELIVER needs no `adversarial_review` record.

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
  — the `<id>` of `backend/experts/code/<id>.md` (`correctness`, `security`,
  `red-team`), never the Title-case `<DOMAIN>`. A wrong value fails the
  `^EXPERT-ID: [a-z…]$` anchor read by `runtime_hooks._extract_expert_id`; the marker
  then records no expert and D7 cannot credit it.
- Every spawn (specialists, Red Team, meta-review) uses `subagent_type: "pipeline-expert"`
  (read-only tools, effort medium, a 24-call cap; `expert_type_gate` denies any other
  type for an EXPERT-ID prompt) and `run_in_background: false`
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
  still all-rejected → record `gate2_findings_unverified_reason` ("gate_spawn_blocked: …")
  and complete DELIVER: push_ready is capped false, run-commit commits locally, marked.

**NEVER** treat an all-rejected spawn as license to self-review — that is the CLASS A
bypass this gate exists to prevent. There is no self-review branch.

## Red Team (conditional, never a wave of its own)

`stage-start` lists `red-team` in `experts` when the change carries a clear security
risk and has >= 50 source lines, so it runs in round 1 in the same parallel batch.
Otherwise it does not run; say why in the gate outcome. The legacy flow's
"specialist HIGH" trigger below applies only to a run with `convergence` other than
`deterministic`.

**`[GATE·validator]` HIGH trigger is mechanical** (strict profiles; trivial/research
exempt): every finding records its producer in `specialist` — the reviewer's EXPERT-ID.
`stage-start`'s prompt files fill the output format's `"specialist"` with it; with the
`spawn_prompt.py` fallback, replace `<name>` with the EXPERT-ID yourself (`red-team` for
Red Team findings; meta-review, skeptic, probe, convergence and doc-sync findings name
their producer).
`adversarial_review.red_team_dispatched` (`true` / `false`) claims whether the Red Team
ran. A finding counts as a specialist HIGH when its severity (`severity`, else
`sev`/`level`/`priority`) is HIGH, CRITICAL, BLOCK(ER), P0/P1 or a number ≥ 8, resolved or
not, and its `specialist` names a Gate-2 registry id (`backend/experts/code/*.md` except
red-team) or is missing (fail-closed). `findings[]`, `specialists[].findings` and
`iterations[].findings` all count. Labels starting with "red-team" ("red-team (opus)",
or an unattributed "Red Team: …") are the Red Team's own and never trigger it. Then
`publish` and `stage-complete` BLOCK (`E_RED_TEAM_REQUIRED`) unless the claim is made,
and `stage-complete` also requires the red-team EXPERT-ID marker for this run (marker
store unreadable → allowed with a warning). The trigger also adds `red-team` to
`required_experts`, so `run-commit` (D7) refuses without that marker on the legacy
path too. **Cutover:** enforced for runs whose run.json `created_at` ≥
2026-10-18T06:00:00Z; older runs, and a publish with no run, get the same text as a
warning. Remedy: spawn the Red Team as below, merge its findings, set
`red_team_dispatched: true`.

The Red Team sub-agent receives: the checklist `backend/experts/code/red-team.md`, the
merged specialist findings (so it knows what was already caught), and the changed-file
list. Its prompt comes from the same gate2 emitter with `<EXPERT_ID>` = `red-team`. Its
findings merge into the unified list under the same dedup and confidence rules
(`ref/deliver/gate2-findings.md`).
