# DELIVER — report projection, local commit, pipeline boundary, Canvas

Read when the card points here (lessons → report → commit), when `run-commit` refuses or
BLOCKS, or for the full COMPLETE-stage Canvas behavior.

<!-- R-deliver-014 -->
> **[MUST] REPORT.md is a deterministic PROJECTION of the ledger, authored by ONE
> renderer (`cmd_run_report`). You do NOT hand-write it.** The run ledger (run.json +
> each stage's published artifact) is the single source of truth; `cmd_run_report`
> aggregates it into a human-readable REPORT.md (TL;DR, §4.6 需求溯源审计 Intent→
> Requirement provenance, Evaluation, TDD results, quality gates, adversarial findings,
> files changed, §9 REFLECT lessons, Methodology Impact). Two renderers for one report =
> guaranteed drift — this stage used to ALSO hand-write a §1-§11 markdown template,
> which produced a §1 that was a prose paraphrase and a §9 frozen at
> `_(populated in REFLECT)_`. That competing template is DELETED. There is exactly one
> renderer; fill the ledger (stage artifacts + the lesson step below), then project it.
> A section that renders thin is fixed by enriching the STAGE ARTIFACT it projects from
> (Rule 22 — run.json is the audit source), never by editing REPORT.md.

## Ordering that makes the git-committed report correct

`run-commit` copies REPORT.md verbatim into git as the audit copy
(`.aidlc/deliverables/<RUN_ID>/REPORT.md`, "B"). A report generated before the lessons
exist freezes B without them while the ledger copy is later regenerated (permanent
drift). So the §9 lessons must be in the ledger BEFORE `run-commit`:

1. Seed this run's lessons into the **reflect** stage record (not deliver — the
   completion gate's reflect-lessons check and the report's §9 both read
   `reflect.lessons`):
   `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --stage-json '{"stage":"reflect","status":"recorded","lessons":["[pitfall] ...","[decision] ..."]}'`
   Each lesson > 20 chars, `[tag]`-prefixed, cross-transferable (R30#4 AUDIENCE test: a
   durable principle/pitfall/decision, not a construction log of this run). `recorded`,
   not `completed`: REFLECT still runs as a full stage, REUSES these lessons (never
   re-extracts different ones — that would be a second §9 source) and does the
   cultivation / IMPROVEMENT.md writes. No lesson → one honest
   `[note] no cross-transferable lesson — <why>` (≥ 20 chars); never a placeholder.
2. `stage-complete --stage deliver` generates REPORT.md when it is missing
   (`report_generated: true`) and never overwrites an existing one. If a report already
   existed from before the lessons, refresh it:
   `python backend/scripts/artifact_cli.py run-report --project <P> --run-id <R> --force`.
3. THEN `run-commit` copies the final report as B — one commit, B correct in one shot.

**Safety net (code-enforced).** If `run-commit` finds no terminal REPORT.md, it
auto-generates it from the ledger and warns (non-blocking) that it did — your signal the
ordering slipped and was recovered. If that generation FAILS (corrupt/unreadable
ledger), the commit is BLOCKED (exit 2, "COMMIT BLOCKED"); nothing was staged, so no data
is lost. On a transient fault, diagnose and re-run `run-report` + `run-commit`; there is
no auto-retry by design. §9 content quality is still gated: never-seeded lessons give a
degenerate §9 and B is SKIPPED-with-warning.

Report location: `Projects/<P>/.artifacts/runs/<R>/REPORT.md`. Sections (rendered, not
typed): TL;DR · §1 Requirement · §4.6 需求溯源审计 · §2 Evaluation · §3 Methodology · §4
Execution · §5 TDD · §6 Decision Log · §7 Quality Gates · §7.5 Adversarial · §7.6
Meta-Review · §7.7 Completion Audit · §7.8 Doc Sync · §8 Files Changed · §9 Lessons · §10
Known Gaps · §11 Methodology Impact (every row cites a specific moment of THIS run; an
honest "N/A — bounded feature" beats a manufactured insight).

## `run-commit` — the only commit channel

`python backend/scripts/artifact_cli.py run-commit --project <P> --run-id <R>`, right after
`stage-complete` records `push_ready: true`. It stages EXACTLY this run's
`files_touched` via `git add -- <path>` (never `git add -A`, so it never adds a parallel
session's in-flight edits, R29), and the `git commit` is pathspec-scoped too
(`git commit -m <msg> -- <files>`), so a file another session pre-staged stays
staged-but-uncommitted. The message is machine-rendered from the ledger. It commits in
whichever repo each file lives in (source repo and/or SwarmWS) and **NEVER pushes**
(STEERING #5 limits auto-push, not local commit). It refuses if the deliver record is
not `push_ready` or `files_touched` is empty, refuses (D7) unless every
`required_experts` entry left a review marker this run, and WARNS, listing them, when
the tree has changes this run did not track. Still good hygiene first: `git diff --cached
--name-only`, then `git restore --staged -- <path>` for anything not this run's.

## Pipeline boundary: PUSH-READY → local commit → done

```
Pipeline scope:  quality gates → PUSH-READY → auto local-commit → REFLECT → COMPLETE
User scope:      git push → CI green → PR (optional)
```

Local commit is safe and reversible; push is a deployment decision the user controls
(the remote may be public). CI needs remote infrastructure, and the pipeline must be
completable offline. At PUSH-READY the pipeline guarantees: all tests pass locally (L1),
no regressions (L3), adversarial review clean (L4), DDD conformance verified (L5), all
decisions resolved (L6). After COMPLETE, suggest (do not execute) `Next: push to remote +
verify CI`; the standing Post-Push CI Ownership rule (`gh run watch` → green or fix) takes
over from there.

## COMPLETE-stage Canvas review — full behavior

At COMPLETE the agent surfaces "what this run changed" to Canvas. The change truth is
**git**, not the process that wrote each file: `run-surface-changes` runs `git status`
on the SwarmWS tree + every bound source worktree and classifies each changed path via
`needs_human_review`, so main-agent, sub-agent, CLI and hook writes are seen ALIKE (the
SDK filters sub-agent sidechain messages, so process tracing is structurally blind to
them).

`python backend/scripts/artifact_cli.py run-surface-changes` →
`{"content":[...], "knowledge":[...], "source":[...], "process":[...]}`

1. **content + knowledge** (DDD / design docs / MEMORY / KNOWLEDGE) — the IMMEDIATE
   regime: they surfaced as rail rows the moment they were written; the command only
   confirms them. The canonical context files under `.context/` and a run's REPORT.md
   under `.artifacts/runs/<id>/` are on a NARROW allowlist that lets them escape the
   bookkeeping / dot-dir / gitignore drop, so they surface immediately too.
2. **source (code)** — the finish BATCH, never popped per file mid-run. After
   `run-commit` records the commits, call the `surface_run_outputs` tool ONCE with this
   run_id. The orchestrator observes that call (same mechanism as `ui_action`) and emits
   one `file_changed(kind=source-final)` event per committed file → N persistent OUTPUTS
   rows, each openable as that file's changes (a local-PR experience). Mid-run
   `kind=source` stays suppressed. There is no aggregated PR document (removed — it had
   no review value). REPORT.md must exist before the surface call (`stages/complete.md`).
3. **Record it** on the deliver stage: `run-update --stage-json
   '{"stage":"deliver","status":"completed","outputs_surfaced":true,...}'`. The gate
   itself is stated on the card (Gates). Honest scope: this is self-attestation — the
   CLI cannot observe the frontend dispatch; the flag proves the agent RAN the surface
   step, not that Canvas rendered. A knowledge/docs-only run, or one whose commits are
   all a sibling session's files, is not gated.

## PR creation — USER-initiated, outside the pipeline

The pipeline never pushes or opens a PR, and DELIVER never invokes `pipeline_pr.py`. A
user who has pushed and has green CI may run
`python backend/skills/s_autonomous-pipeline/scripts/pipeline_pr.py --run-dir <run_dir>`
(flags: `--run-dir`, `--dry-run` only). Its guards: profile `full` or `bugfix` (others
skip silently); `gh auth status` must succeed (else warn — push-ready stays valid); on
`main` it creates `pipeline/<run_id>`, pushes and PRs against main; on a feature branch
it pushes with `-u`. Title `feat(<scope>): <requirement condensed>` (≤ 70 chars); body
TL;DR + delivery stats + files + REPORT.md link. It always uses `gh pr create --auto`
(hardcoded). A PR failure is a WARNING, never a pipeline error; record
`{"pr_result": {"success": true, "pr_url": "..."}}` in run.json. Use `--dry-run` when the
user said they will handle the PR.

## Also at DELIVER

- Add a delivery entry to the project's PROJECT.md; check for unresolved issues from
  upstream stages.
- Legacy path (sibling skills only): `publish --type delivery --stage deliver` then
  `advance --state reflect`. Pipeline runs use `stage-complete` instead.

## Review summary (`outcome` + `attention_flags`) `[MUST]`
The deliver artifact carries `outcome` = {`problem_before`, `result_now`, `approach[]`}: in plain
words, what was wrong before this run, what is true now, and the few key moves. Every item still
open (deferred finding, unrun check, needed restart, known risk) goes in `attention_flags`. The
chat completion block and the REPORT TL;DR + §10 render both through one function
(`core.run_delivery_summary.review_summary`), together with unresolved findings, a
push_ready=false reason and any forced commit. A part with nothing recorded shows 未记录, so a
skipped `outcome` is visible to the reviewer, never filled in from the requirement text.
The forced-commit line exists only after `run-commit` records the commit, so the REPORT copy committed
into git never shows it; the ledger REPORT (regenerated at completion) does. A push_ready=false reason
appears in both copies.
