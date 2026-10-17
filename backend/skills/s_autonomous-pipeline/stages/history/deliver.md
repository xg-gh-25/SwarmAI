# DELIVER — history and rationale (never read at runtime)

Why the DELIVER stage looks the way it does. The runtime rules live in
`stages/deliver.md` (card), `stages/ref/deliver/*.md` and the Meta-Review prompt
`data/gate-prompts/meta-review.md`. Nothing here is a rule; if something here reads like
one, it is already in the card or a ref.

## The 2026 split (design `docs/design/pipeline-speed-systematic-fix.md` §4-A, C, E, F, G)

The old `stages/deliver.md` (~73K chars) was read in full on every DELIVER entry. It
mixed the rules, shell recipes the model executed by hand, two rationalization tables,
incident stories, and a 4.7K inline Meta-Review prompt. What changed:

| Old (model did it) | Now (tool does it) |
|---|---|
| Resolve `BASE_REF`, `git diff --name-only`, `git ls-files --others`, `DIFF_LINES` from `git diff --stat` to decide the tier | `stage-start --stage deliver` returns changed files, `source_insertions`, `full_tier_override` and the computed `experts` list; thresholds count NON-TEST source lines |
| Hand-fill each specialist's prompt from the gate2 template | `stage-start` writes one prompt file per expert (`prompt_path`) through `spawn_prompt.py` |
| Retype the inline Meta-Review prompt | `spawn_prompt.py --gate meta-review` emits `data/gate-prompts/meta-review.md` (same cap + knowledge-slot contract as gate0/1/2) |
| Declare `push_ready` in the artifact | `stage-complete` computes an upper bound from the gates; the model may only lower it |
| `publish --type delivery` + `advance --state reflect` | one `stage-complete --stage deliver`, which also generates REPORT.md when missing |
| Count findings by hand | `findings_total/fixed/remaining` derived from `findings[]` |
| Hand-written delivery summary | code-rendered `_display_block` at COMPLETE (§4-E) |

Changes made in the move:
- The Meta-Review prompt gained a `SCOPE BUDGET` with numeric caps (the emitter refuses a
  cap-less prompt) and the `<PROJECT_KNOWLEDGE>` slot, so the meta-reviewer gets the
  project's recorded pitfalls like the Gate-2 experts do. Its output categories gained
  `architecture` for the No-Patch question, which had no category before.
- `meta_review.verdict` follows the artifact enum (`CLEAR` | `RISKS`); the prompt still
  answers `RISKS_IDENTIFIED`.
- `ac_verification.status` follows the artifact enum: old `verified` / `claimed` /
  `failed` map to `verified` / `partial` / `unverified`.
- The Fresh User Audit / User Path Latency examples and the Doc Sync bash block moved to
  refs unchanged in substance; volatile counts and source line numbers were dropped from
  runtime text.
- The leftover fragment of the deleted hand-written report template (§7.8-§11 markdown)
  was removed; REPORT.md is projected by `cmd_run_report`.

## Why Gate-2 is non-negotiable

The gate was skipped 5 times in 6 weeks. Each time the agent said "tests pass, code is
simple, I'm confident", and each time the feature was broken. Recorded runs showed
reviews finding critical bugs AFTER confidence was high: a voice-input feature 100%
non-functional, three CRITICAL subprocess bugs, and hallucinated false-positive
criticals. Confidence gating solves false positives; specialists solve false negatives.
The multi-specialist shape was adopted from an external "Review Army" pattern.

| What you're thinking | Why it's wrong |
|-----|------|
| "Tests pass, adversarial review is unnecessary" | 57 tests green, 10/10 confidence → feature 100% non-functional. Confidence measured PROCESS compliance, not CODE correctness |
| "Code is simple, I already reviewed it" | 3 files, 2 functions, "simple" → the user caught it. Subjective complexity estimates are unreliable |
| "Validator schema is strict, I'll force past it" | Strictness IS the gate. Forcing past it shipped incomplete work |
| "I'll do a quick self-review instead" | Self-review found 0 findings; adversarial found 5, two HIGH. Same session, same code |
| "I reviewed it many times during BUILD" | Repetition ≠ fresh perspective: you validated your own assumptions N times |
| "Meta-review is redundant after adversarial" | Adversarial reviews CODE; meta reviews PROCESS. Adversarial catches bugs in what was built; meta catches gaps in what WASN'T |
| "Convergence passed in 1 iteration — must be clean" | Fast convergence on a non-trivial change may mean lenient gates. More scrutiny, not less |

**Mechanical full-tier override (> 100 source lines):** a run used the lite tier on a
382-line cross-module migration under a bugfix profile; a later PE review caught a HIGH
(shell variable scope) that full adversarial coverage would have found.

**Union with `required_experts`:** an expert the PLAN declared but the tier default
omitted was rejected by the D7 commit gate after the fact, forcing a second serial
batch and a second meta-review. Dispatching the union in one batch removes the mismatch.

**No pinned model:** the retired directive named a model tier as "strongest reasoning".
The recorded sub-agent corpus did not support it, and model identity was confounded with
era and diff size, so the evidence licensed deleting the claim rather than naming a
replacement that would decay at the next default change. Review quality comes from
bounded scope and fresh context.

**Foreground spawns:** a background spawn ended the visible chat stream; the user saw
nothing running and no status. Every spawn is now `run_in_background: false`.

**Project knowledge emitted, not pasted:** experts are fresh-context on purpose, which
also cut them off from the project's recorded pitfalls. The knowledge block used to be
a hand-paste of named TECH.md sections and was filled for 1 of 13 expert prompts in
one recorded run; the expert reviewing a known fragile zone never saw its pitfall.

## Coverage and the classifier

Measured on one machine's markers: almost every real `_adv_` marker came from a
`general-purpose` sub-agent (the rest `Explore`), so the prompt wording alone decided
whether a marker was written. About one in six markers carried an EMPTY
`reviewed_paths` list (a reviewer spawned in an already-clean worktree), and one marker
lacked the key entirely. A new test file left unstaged through the first review pass was
both unread and uncovered; the pass that finally covered it ran dozens of mutations and
proved several tests vacuous, the worst being a data-loss guard whose only writer could
be deleted with the suite green. The gate DENIED, but only at commit time.

## Shared-State Override

An adversarial review flagged a LOW confidence-4 finding on a flag (`_content_emitted`).
It was dismissed after checking ONE of its 3 consumers, the one that confirmed the
dismissal. The second consumer (zombie detection: short stream and no content emitted →
kill + retry) had no guard, and a real kill-healthy-subprocess regression shipped to the
delivery artifact. PE review caught it afterwards. Confidence gating is calibrated for
false-positive noise, not for blast radius; shared state is where a low score is
structurally untrustworthy. That is why the override runs before the tiers and why the
drop is not a mechanical `confidence < 5` filter.

## Audits

- **Fresh User Audit (P6):** a provisioner passed 28 tests and 10/10 confidence, yet a
  fresh-user audit found 7 P0 gaps in 10 minutes: a hardcoded repo, a storage bucket
  name collision, stale IPs in an update script, a placeholder hash that looked valid,
  a package install blocking instance startup, a password in a list response, and no
  permission pre-check. Tests run in the developer's environment, so all were invisible.
- **User Path Latency Trace (P6.5):** a PE review of a chat-channel feature found 2 bugs
  that passed TDD (24 tests), adversarial review (11 findings handled) and CI: an ack
  helper ignored its text parameter and posted a hardcoded "Thinking...", and a fixed
  2-second sleep delayed every message, including instant answers. Mocks accepted any
  call; only walking the real path showed them.
- **AC verification:** the 57-green-tests feature that was 100% non-functional had
  tests that validated the implementation, not the spec. A separate read of the AC →
  test mapping separates "builder claims evidence" from "verifier confirms evidence".
- **User-Value Probe:** a documentation-engine run was declared PUSH-READY with 9 green
  tests and a clean adversarial review, but its output paraphrased the README: no code
  was read, TECH.md had no file citations, and the code-intel edges were fabricated. The
  annotation audit later found no code enforcing the probe; that drift is flagged in the
  ref and left for the user to decide.

## Meta-review origin

The PE-perspective review used to be manual (the user had to ask for it). Review and
adversarial reliably caught code bugs but missed deployment-context mismatches: an O(n)
no-op scan in a per-session hook (RP30), `sys.executable` in a daemon, a JSON space
after a colon across languages. Section 6 (cross-fix interaction) was added because each
specialist's fix was validated alone and nobody checked whether the fixes composed.

## Report, commit and Canvas

- **One renderer:** the stage once hand-wrote a §1-§11 markdown template next to
  `cmd_run_report`. Two renderers drifted: §1 became a prose paraphrase and §9 stayed
  frozen at `_(populated in REFLECT)_`. The hand template was deleted.
- **Lessons before commit:** the git audit copy of REPORT.md is taken by `run-commit`.
  Generated before REFLECT, it froze without lessons while the ledger copy was later
  regenerated. Agent-discipline ordering failed on a majority of historical runs, so
  `run-commit` now auto-generates a missing report and blocks if generation fails.
- **Pathspec-scoped commit:** `run-commit` once could sweep another session's pre-staged
  file into its commit; both its `git add` and `git commit` are now scoped to
  `files_touched`.
- **Canvas from git:** process tracing missed sub-agent writes (the SDK filters
  sidechain messages), so surfacing reads `git status` instead. The aggregated local-PR
  document was removed for having no review value; per-file OUTPUTS rows replaced it,
  and the old surfaced flag was renamed to `outputs_surfaced`.
- **Push boundary:** local commit is reversible, push is a deployment decision; the
  pipeline must also complete offline. PR creation stays a user-run tool.
- **Doc Sync non-blocking:** blocking on docs rewards low-quality docs written to
  unblock; catching the gap at delivery still helps because the author has context.
