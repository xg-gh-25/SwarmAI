---
title: "Completion Contracts & Author≠Judge Separation — An Adoption Verdict"
date: 2026-07-06
project: SwarmAI
type: design
status: verdict
benchmark: hermes-agent v0.18.0 ("The Judgment Release")
run: run_1c22d874
verdict: GO-MINIMAL (1 of 5 surfaces) · ALREADY-COVERED (3) · SKIP (1)
---

# Completion Contracts & Author≠Judge Separation — An Adoption Verdict

## TL;DR

hermes-agent v0.18.0 productized the same proposition SwarmAI holds as SOUL **P2**
("Done = tried to break it and failed"): an agent should decide it's done from
**evidence, not vibes**. Their mechanism is a `/goal` **completion contract**
(`outcome / verification / constraints / boundaries / stop_when`) judged by a
separate `goal_judge` aux model that marks done only when verification is met by
concrete evidence.

**The honest verdict: mostly redundant, one real gap.**

SwarmAI already separates author from judge on the places it matters most, and does
it the *right* way — by giving the judge **independent evidence**, not just a second
opinion. Two surfaces are fully covered (code-correctness, AC→test), one is mostly
covered but opt-in (finding-resolution). The one genuine increment is narrow: the
**goal-mode done-decision** still judges the author's *narrative* (a rubric the
builder reads and self-scores). That — and only that — is worth changing, and the
change is a **structural evidence-citation requirement**, not a new judge model.

| # | Surface | Who judges today | Verdict |
|---|---------|------------------|---------|
| 1 | `rubric`-type DoD | builder self-evaluates (narrative) | **DEFER → prefer command-DoD** |
| 2 | done/stop decision (goal mode) | builder declares DoD met (narrative) | **✅ GO-MINIMAL — the one real gap** |
| 3 | finding-resolution | author marks `resolved:true`; **opt-in** disk-check backstop | **MOSTLY-COVERED (opt-in, WARN-default)** |
| 4 | cross-cycle regression re-judge | same builder vs its ledger | **SKIP (low leverage)** |
| 5 | AC→test correctness | same-builder AC verification (evidence-independent) | **ALREADY-COVERED** |

---

## 1. What hermes v0.18.0 actually ships (mechanism, not marketing)

Read from the release + PRs (#50501, #52285, #55413, #53552), not the blurb:

- **`/goal` Completion Contracts (PR #50501):** 5 structured fields
  (`outcome / verification / constraints / boundaries / stop_when`). A `goal_judge`
  aux model marks the goal done **only when `verification` is satisfied by concrete
  evidence** (command output / file fragment / test result). Explicitly targets two
  failure modes: **premature completion** + **infinite run on an under-defined goal**.
  Their own PR note: adapted from OpenAI Codex's `/goal` guidance.
- **`/goal draft` (same PR):** an aux model expands a one-line goal into a full
  contract. Fallback to free-form if the model is unavailable.
- **Verification Evidence Ledger (PR #52285):** profile-scoped record of
  test/lint/typecheck/build results; **passive by design** ("records evidence, not
  guarantees"); **marks evidence stale on edit** (`write_file`/`patch`); bounded
  storage (caps + 30-day expiry).
- **`pre_verify` hook + verify-on-stop OFF (PR #55413 / #53552):** a hook to nudge
  one more verification round before the final answer; **verify-on-stop was disabled**
  because it "fired too eagerly — including on doc/markdown/skill edits with nothing
  to verify." They added `_filter_verifiable_paths()` to skip `.md/.rst/.txt`.

That last item is not something to adopt — it's **external confirmation of our own
O030 + R6**: a verification gate that fires on things needing no verification becomes
ritual. They shipped it and rolled it back; we already govern against it.

---

## 2. What SwarmAI already has — the 5 author==judge surfaces

Verified by code-trace this run (R15/O011 — read, not recalled), then a
**zero-context skeptic** was spawned to refute the scoping. The skeptic **corrected
my initial framing** (I had said "only 2 gaps"; it found 5 — the correction is why
this section is trustworthy).

| # | Surface | File:line | Judge sees… |
|---|---------|-----------|-------------|
| 1 | `rubric`-type DoD | `goal_cycle.md:134-135` | the builder reads the state and self-scores PASS/FAIL — **narrative** |
| 2 | done/stop decision | `goal_cycle.md:124-125, 141-143` | builder runs DoD, declares SUCCESS — **narrative** |
| 3 | finding-resolution | `pipeline_validator.py:153` (`_verify_findings_on_disk`) | author sets `resolved:true` **and** hand-writes a `disk_check` marker the grep looks for — **opt-in evidence backstop** (missing marker → WARN, not BLOCK; `pipeline_validator.py:2955-2972`) |
| 4 | cross-cycle regression | `goal_cycle.md:237-272` | same builder re-judges its own ledger — **narrative** |
| 5 | AC→test correctness | `deliver.md:158-231` (step 2.5) | a verification step separates "builder claims evidence" from "verifier confirms" — **evidence** |

And where SwarmAI **already has real separation** (the baseline that makes most of
hermes redundant):

- **Code-correctness (Gate-2):** `deliver.md:369-371` spawns **fresh-context
  sub-agents** ("no prior review bias"), with **no self-review escape**
  (`:427-428` — "NEVER treat an all-rejected spawn as license to self-review — that
  is the CLASS A bypass this gate exists to prevent"). Code-enforced at
  `pipeline_validator.py:1150-1177` (requires `spawned=true` + non-empty evidence;
  blocks `tier=skipped/lite`).
- **command-type DoD:** `goal_cycle.md:128-132` — shell exit code. Objective; there
  is no judgment to bias.

---

## 3. The thesis — evidence-independence, not instance-independence

This is the load-bearing insight, and it's what makes the verdict *narrow* instead
of a blanket "adopt author≠judge everywhere."

> **What stops CLASS A is judging against INDEPENDENT EVIDENCE — not consulting a
> SEPARATE INSTANCE.**

A second LLM instance fed **the author's own narrative** (the `resolved:true` flag,
a self-written rubric result) **shares the blind spot**, because it shares the
*input*. Spawning it is separation theater. This is exactly pre-mortem risk #2, and
it applies to me specifically: I am the highest-frequency CLASS A offender (12
occurrences, 0 self-corrections). A judge that reads my summary of what I did will
be as wrong as I was.

The places SwarmAI's separation **holds** all share one property — the judge looks
at **independent evidence**, not the author's account:

- Gate-2 reads a **fresh diff** (the actual code, not my description of it).
- command-DoD reads an **exit code** (the program's behavior, not my claim).
- `_verify_findings_on_disk` greps the file **when the author attaches a
  `disk_check` marker** — a *partial* evidence check: independent on the content
  it verifies, but opt-in (the author still both sets `resolved:true` and writes
  the marker string), and only a WARN when absent. Evidence-backed on the covered
  path; narrative-trusting on the uncovered one.

The places it **fails** all judge the **author's narrative**:

- rubric-DoD: I read the state and write PASS. (surface #1)
- done-decision: I declare DoD satisfied. (surface #2)
- cross-cycle: I re-judge my own ledger. (surface #4)

So the design principle is not "add judges." It is: **wherever a done/pass decision
currently reads the author's narrative, make it cite independent evidence instead.**
That is a structural evidence gate — consistent with **P7 (defense outside the
agent)** — not another prose rule I'll bypass when confident.

---

## 4. Per-surface verdict

**#1 `rubric`-type DoD — DEFER (bias toward command-DoD).**
Don't wrap an LLM judge around a subjective rubric. The higher-leverage move is one
EVALUATE already prefers — `evaluate.md:466` ("`command` type: shell command, exit 0
= pass. **ALWAYS prefer this.**") and `goal_cycle.md:96-106` Rule 3 ("prefer DoD
criteria that verify **behavior** over **existence**"): **convert rubric criteria to
`command` criteria** (objective evidence) wherever possible, and where a rubric is
truly unavoidable, require it to **cite the specific state it read**. Adding a judge
over a vague rubric just moves the subjectivity, it doesn't remove it.

**#2 done/stop decision — ✅ GO-MINIMAL. This is the one real increment.**
Today the goal loop exits when the builder runs the DoD and declares success. Adopt
hermes's *structure* (not its model): a **completion contract** where the
`verification` of each done-criterion **must cite concrete evidence** — command
output, `file:line`, or test result — and is **evaluated exit-first by command**,
falling back to rubric only as a last resort. This upgrades the done-decision from
narrative-self-judge to evidence-cited. See §5.

**#3 finding-resolution — MOSTLY-COVERED (opt-in; one small hardening worth noting).**
`_verify_findings_on_disk` (`pipeline_validator.py:153`) is the right *shape* of
backstop — it exists *because* an author-marked `resolved:true` shipped falsely once
(run_b5592983, C011). But it is **opt-in and WARN-default**, not the full independent
re-judgment my §3 thesis wants: it fires only when the author hand-attaches a
`disk_check` marker (`{file, must_contain|must_not_contain}`), and a **resolved
HIGH/CRITICAL finding with no `disk_check` is only a WARN, never a BLOCK**
(`pipeline_validator.py:2955-2972`); MEDIUM/LOW get nothing. So on the covered path
it is genuine independent evidence (greps the real file); on the uncovered path it
falls back to trusting my `resolved` flag. **This is close, not done.** A cheap
hardening — make a resolved HIGH/CRITICAL finding *require* a `disk_check` (WARN →
BLOCK) — would fully close it, and it's the same evidence-citation principle as the
§5 increment. Not a hermes increment; a one-line severity bump. (Noted, not
recommended as the primary action — §8.)

> **DECISION (2026-07-06, run follow-up): NOT ADOPTED.** The "one-line severity
> bump" framing was wrong — a Gate-1 plan skeptic (verified against source)
> found the naive `warnings.append`→`errors.append` move is *harmful*, not
> trivial: (1) `resolved:true` is a bare boolean with no code-edit semantics —
> legitimate non-code resolutions (verified-not-exploitable, won't-fix, deleted
> file, `.tsx`/config/docs fixes) have no `disk_check` to attach, so BLOCK
> false-blocks correct deliveries; (2) the block at `:2953` is un-gated on
> profile/tier (unlike every sibling gate) → breaks relaxed profiles and
> *self-locks its own bugfix delivery*; (3) it's trivially gamed (a fake
> `must_contain:"def "` passes) — a hoop, not a higher bar. The safe design
> (`resolution_kind` OR `disk_check`, BLOCK only when `code_fix` + no evidence)
> is a real schema-migration feature, not a one-liner, and its ROI is below the
> bar this doc already assigned #3 ("可做可不做"). **Closed: evaluated, not
> pursued.** The opt-in WARN backstop stays as-is.

**#4 cross-cycle regression re-judge — SKIP.**
Low leverage. The ledger re-judge (`goal_cycle.md:237`) is narrative-based, but the
Final Quality Gate already runs a fresh adversarial on the **total** diff
(`goal_cycle.md:342-348`) — independent evidence covers the diff as a whole. Adding a
dedicated per-cycle judge is over-engineering (C044).

**#5 AC→test correctness — ALREADY-COVERED (evidence-independent, not instance-independent).**
The AC VERIFICATION step (`deliver.md:158-231`, step 2.5) already "separates builder
claims evidence from verifier confirms evidence" — the exact C011 failure mode, gated.
Honest caveat: this step is run by the **same builder agent** (a fresh *re-read* of
the test file + trace to implementation), not a spawned fresh-context instance like
Gate-2. It qualifies under this doc's thesis because it is **evidence-independent**
(it reads the actual test body, not my summary of it) — but it is not
*instance*-independent, so don't over-read the "INDEPENDENT" label in the stage doc.

---

## 5. Minimal increment spec (the ONLY thing to build, if anything)

An **evidence-cited completion contract** for **goal-mode DoD only**:

```json
{
  "dod_criteria": [
    {
      "type": "command",              // prefer command; rubric is last resort
      "check": "pytest tests/foo.py -q",
      "verification": "EXIT:0 from the command above",  // must be evidence-shaped
      "desc": "foo behavior verified"
    },
    {
      "type": "rubric",
      "check": "...",
      "verification": "CITE the file:line / output fragment that proves PASS",
      "desc": "..."                    // rubric MUST cite what it read
    }
  ]
}
```

The single new **structural** rule (validator-enforceable, not prose):

> Every goal-mode DoD criterion's `verification` field must be **evidence-shaped** —
> an exit-code assertion, a `file:line` reference, or a captured output fragment. A
> DoD criterion whose verification is a bare restatement of the goal ("errors are
> user-friendly") **blocks** until it cites the evidence that would prove it.

This is a small extension to the existing DoD Quality Rules
(`goal_cycle.md:42-106`), which already enforce **Rule 1** (≥1 negative test), **Rule
2** (≥1 non-default path), and — importantly — **Rule 3: Behavioral Over Existential**
(`:96-106`, "prefer behavior over existence; existential criteria pass on dead code").
Rule 3 already pushes DoD toward *executable/evidence-shaped* verification, so the
"prefer evidence over existence" spine is **partially present today**. The genuinely
**new** part is narrower than "every criterion must name its evidence": it is
specifically a **blocking rule on the `rubric` branch** — a rubric criterion whose
`verification` is a bare restatement of the goal ("errors are user-friendly") must
cite the state it read, or it blocks. The `command` branch is already evidence-shaped
by Rule 3; this closes the rubric hole Rule 3 doesn't reach. No new model, no new
subsystem, no new hook.

**Effort: S.** It's a `pipeline_validator` check on the rubric branch + a
`goal_cycle.md` DoD-rule line.

---

## 6. Non-Goals (what NOT to build — guards C042/C044)

1. **No independent LLM judge for code-correctness.** Gate-2 already spawns
   fresh-context reviewers against a real diff. Adding another is duplication.
2. **No `/goal draft` aux-model contract expansion.** An extra LLM call to write the
   contract for me is mechanism-for-its-own-sake (C042) — I write DoD in EVALUATE
   already.
3. **No cross-cycle regression re-judge agent** (surface #4). Low leverage; the
   total-diff adversarial covers it.
4. **No Verification Evidence Ledger subsystem.** SwarmAI stores verification in
   pipeline artifacts already; a bounded-storage ledger with stale-on-edit tracking
   is a hermes-shaped answer to a problem our artifact model already handles. (The
   *stale-on-edit* concept is interesting but belongs to R7 docstring/comment
   co-update, not a new store.)

---

## 7. The CLASS-A self-test (the design's own success condition)

This design's central value claim is "author≠judge fixes CLASS A." I am the primary
CLASS A offender, so the doc must survive its own thesis. Honest answer:

**An independent judge does NOT reliably stop my CLASS A if it judges my narrative.**
The 12 CLASS A occurrences were all high-confidence; a second instance reading my
confident summary would rubber-stamp it. Separation is only protective when the
judge is **forced onto independent evidence I cannot narrate away** — a diff, a grep,
an exit code. That is why the recommendation is an **evidence-citation gate**, not a
judge model. It's the same lesson as every structural gate that has actually held
(`pytest_command_guard`, `cmd_run_checkpoint`, `_verify_findings_on_disk`): the fix
is a mechanical check on reality, outside my discretion (P7) — never a smarter opinion.

---

## 8. Recommendation & next step

- **Adopt:** the evidence-cited completion-contract rule for goal-mode DoD (§5). One
  small validator check + one DoD-rule line. Highest leverage, lowest cost, directly
  on the one surface that is genuinely author-narrative-judged.
- **Do not adopt:** the goal_judge model, the `/goal draft` expander, the evidence
  ledger, the pre_verify hook. 3 of 4 duplicate existing gates; the 4th is ritual we
  already govern against.
- **Evaluated, not pursued (2026-07-06):** the #3 WARN→BLOCK hardening on
  no-`disk_check` resolved HIGH/CRITICAL findings. Gate-1 showed the naive severity
  bump false-blocks non-code resolutions, is un-gated on profile/tier, self-locks its
  own delivery, and is trivially gamed; the safe `resolution_kind` design is a real
  schema migration below this doc's ROI bar for #3. See §#3 DECISION. Opt-in WARN stays.
- **Already have (no action):** independent code-correctness judge (Gate-2),
  finding-resolution disk-verify, AC→test verification.

**Next step (ask-first):** open a `trivial`-profile implementation run for the §5
validator rule if XG wants it shipped. This doc is the verdict; it does not itself
change code.
