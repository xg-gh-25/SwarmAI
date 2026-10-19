# REVIEW ref — Fan-out dispatch, merge and finding format
Read when: `review_experts` lists more than spec-compliance, you are merging sub-agent
reports, or writing findings. Card steps 3 and 5 carry the commands.

## Who is dispatched (computed by stage-start, not read off a page)
`artifact_cli.py stage-start --stage review` returns `review_experts` (each `{id, domain,
reason}`), `changed_files` (each with `status` A/M/D/R, `added`, `removed`, `is_test`),
`diff_insertions`/`diff_deletions` (all files, display only), `source_insertions`/
`source_deletions` (NON-TEST source only), `full_tier_override` (source insertions > 100)
and `spawn_mode: "foreground"`. It also records `dispatch_plan.review` in run.json for
the profiler. Spawn exactly the ids listed, no more. Test files never count toward any
threshold below.

| Id | Listed when | Prompt file / lens |
|----|-------------|--------------------|
| spec-compliance | always (separate axis, runs before fan-out) | `backend/experts/code/spec-compliance.md` — AC coverage matrix, MISSING/EXTRA/MISUNDERSTOOD |
| code-quality | fan-out applies | `backend/experts/code/code-quality.md` — TECH.md conformance, integration trace, replace/move parity, runtime patterns RP1-RP91, depth & seam |
| ux-test | fan-out applies AND a non-test frontend file changed | `backend/experts/code/ux-test.md` — UX1-UX5, test coverage gaps, E2E trace |
| security | fan-out applies AND Gate-2 will NOT dispatch security for this scope | `backend/experts/code/security.md` — confidence-gated scan, wire test, blast radius |

**Fan-out applies** when ANY holds: >3 non-test source files, >100 non-test source lines
(insertions + deletions), or a non-test auth / payments / data-access / infra / config
path. Otherwise single-pass: `review_experts` holds only spec-compliance, and the main
context runs Tier 1 + applicable Tier 2 checks sequentially (`checks-core.md`).

**One security owner per run.** Gate-2 owns the security lens when it dispatches it —
every strict-profile Gate-2 batch does (Gate-2 dispatches nothing under 50 changed source
lines unless required). Otherwise REVIEW owns it, so a small auth fix under 50 source
lines still gets its security review here. If the diff later drifts so that REVIEW left
security to Gate-2 but the final diff no longer makes Gate-2 dispatch it, DELIVER's
stage-start adds security back — when the final diff still meets the fan-out rule. A
final diff below both bars (under 50 source lines and no fan-out trigger) gets no
security sub-agent; the inline Security Scan covers it. The inline Security Scan
(check 2) runs every time regardless.
(Gate-2's size tiers read the same non-test source counts: < 50 lines → no specialist
unless required, > 100 insertions → full tier, > 200 lines → red-team. They are DELIVER's
to apply; REVIEW uses them only through the security-owner decision above.)

## Spawning (Phase A — one message, several foreground Agent calls)
- Issue ALL Agent tool calls in a single assistant message so they run in parallel, every
  call with `run_in_background: false`: a background spawn ends the visible chat stream,
  so the user sees nothing running.
- First prompt line `EXPERT-ID: <id>` (the same anchor Gate-2 prompts carry; the run
  profiler reads it to show which lens ran in which stage).
- stage-start writes one prompt FILE per REVIEW sub-agent, `evidence/prompts/review-<id>.md`
  (each `review_experts[]` entry's `prompt_path`): `EXPERT-ID: <id>`, the task statement,
  the expert's `backend/experts/code/<id>.md` checklist, the PLAN acceptance criteria as
  fenced DATA, the diff to read, then the project-knowledge block. Spawn each with the
  entry's `spawn` text (`EXPERT-ID: <id>` + "… read <prompt_path> first; follow it."), adding nothing
  (a prompt naming its file is how the SubagentStop marker credits the right run). Only
  when an entry has no `prompt_path` compose it by hand from the same parts, as below.
- **Project knowledge for every fan-out agent (and spec-compliance)** — emit once, paste
  the same block into each prompt under a `## Project Knowledge` heading AFTER the task
  statement (never before it — the adversarial classifier reads the prompt head).
  `stage-start --stage review` writes the block to `review_knowledge_path` and the change
  to `review_diff_path` (one Read for the reviewer). That diff is a snapshot at REVIEW
  entry: after a fix inside REVIEW, re-diff the fixed files (`git diff HEAD -- <files>`)
  and never pass the snapshot to `--patterns-for --diff-file`. No diff path → give the
  reviewer `git diff HEAD -- <changed files>`. Only without the knowledge path emit the
  block by hand, using the output ONLY on exit 0 (exit 3 = `--project` missing), from the repo root:
  `python backend/skills/s_autonomous-pipeline/scripts/spawn_prompt.py --knowledge-only --project <P> --files <changed file 1> <changed file 2> ...`
  It carries the DDD entries recalled for the changed files with doc + section
  provenance, an explicit "none recalled" or "UNAVAILABLE" state, and the command an
  agent can run to query further.
- Each sub-agent returns a JSON report; they do NOT communicate with each other.

## Phase B — Merge (main agent)
Once all reports are back:
1. **Deduplicate** — the same finding from 2 agents → keep the more specific one.
2. **Cross-reference** — a security finding + a code-quality finding on the same function
   = same root cause?
3. **Apply the Anti-Rationalization Gate** (check 14, `exit.md`) — reject shortcuts.
4. **Produce the Exit Evidence Checklist** (check 15, `exit.md`) — every check has evidence.
5. **Single verdict** — GO (advance to TEST) or BLOCK (fix findings first).

**Two axes, never merged.** Dedup/cross-reference apply ONLY within the fan-out quality
findings (code-quality / security / ux). The spec_compliance verdict is a SEPARATE axis
("does the code do what the spec asked?" vs "is it well-built?"). A change can pass one
and fail the other; merging lets one mask the other. This governs REPORTING, not gate
topology: a spec BLOCK already returned to BUILD before quality agents spawned, so at
merge the spec verdict is PASS or WARNING (WARNING injects EXTRA findings for
visibility). Record it alongside the merged quality verdict as two un-cross-ranked axes;
never fold spec into the quality dedup. A quality-axis BLOCK still blocks the stage.

## Base methodology
Follow `backend/skills/s_code-review/INSTRUCTIONS.md` for structured review findings.

## Durable findings format (P3)
All findings MUST be durable — no file paths, no line numbers; they are permanent
artifact records that must survive refactors.
| BAD (stale after refactor) | GOOD (survives refactor) |
|----------------------------|-------------------------|
| "Line 42 in `signal_fetch.py` has a race condition" | "The signal fetch handler races when two feeds write the state buffer concurrently" |
| "`github_trending.py:87` missing error handling" | "The GitHub Trending adapter doesn't handle HTTP 429 (rate limit)" |
| "Fix `session_unit.py` method `_spawn`" | "The session spawn module doesn't release the slot lock on timeout" |
- Describe **behaviors and contracts**, not code locations.
- Use **module names** (TECH.md / domain language), not file paths.
- Reference **acceptance criteria** by number, not test function names.
- A finding must still make sense after a major refactor.
Finding entry shape in `findings[]` (same keys the delivery findings use):
`{"severity": "MEDIUM", "confidence": 8, "resolved": true, "finding": "<durable text>. Fixed: how."}`.
`findings_count` = `len(findings)`, explicit even when 0 (required field).
