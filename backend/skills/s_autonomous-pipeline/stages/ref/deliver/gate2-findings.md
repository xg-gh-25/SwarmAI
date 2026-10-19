# DELIVER — merging, gating and fixing Gate-2 findings

Read when the card points here: after the specialists (and Red Team) return. Covers
merge/dedup, the confidence tiers, the Shared-State Override, the fix rules, the gate
outcome and what to record.

## Merge and deduplicate

`[GATE·cli]` (runs created from 2026-10-18T20:00Z): each expert's marker keeps the findings
of its own answer. DELIVER `stage-complete` refuses unless (1) every expert in
`dispatch_plan.deliver` completed in this review cycle (meta-review exempt on
trivial/research/docs); (2) every HIGH (conf ≥ 5) and MED (conf ≥ 7) an expert reported has
its OWN record in `findings[]` (one record per reported finding; a cross-specialist merge may
list several specialists), with that `specialist`, at least as severe (a MED stays conf ≥ 7),
naming its file; (3) each answer could be read: JSON finding lines, or a final `NO FINDINGS`.
Never drop or downgrade one you think is a false positive: record it as reported, `resolved:
true`, `finding` = "Not a bug: <evidence>": its specialist's re-check confirms or refutes
that. Unprovable (no audit hook) → `gate2_findings_unverified_reason` in the record.

**Use the ledger — do not copy the answers by hand.** `python backend/scripts/artifact_cli.py
gate2-ledger --project <P> --run-id <R>` builds the list from the experts' captured answers
(every round of this review cycle; also written to `evidence/gate2-ledger.json`): one entry
per reported finding, verbatim, with a stable `id`, `specialist`, `severity`, `confidence`,
`path`, `line`, `blocking`. In the deliver artifact write
`"adversarial_review": {"findings_from": "ledger", "dispositions": {"<id>": {"disposition":
"fixed" | "refuted" | "known-issue", "evidence": "...", "test": "<nodeid>"}}, ...}` —
`stage-complete` builds `findings[]` from the ledger (so it always matches the
answers); an entry with no disposition is recorded `unaddressed`, unresolved, never dropped.
`refuted` is recorded as `finding` = "Not a bug: <evidence>" (the expert's words kept in
`reported_finding`). A BLOCKING entry left `known-issue` or `unaddressed` stays unresolved and
blocks DELIVER like any open finding — only a SOUND root-cause verdict after round 3 defers it.
Fix-by-class fields (`invariant`, `root_cause`, `class_fix`, `break_test`) go in the disposition.
Findings no expert produced (meta-review risks) still go in `findings[]` next to it.
`gate2-recheck --findings` accepts the same ledger-shaped `adversarial_review`.

- `NO FINDINGS` → skip that specialist. Otherwise parse each JSON line into one list.
  A bare `NO FINDINGS` from a reviewer that shows no evidence of reading the files is
  the self-review failure signature, not a clean result.
- Fingerprint: `{path}:{line}:{category}` (or `{path}:{category}` without a line).
- Same fingerprint from several specialists → keep the highest confidence, tag it
  "MULTI-SPECIALIST CONFIRMED (specialist1 + specialist2)", confidence +1 (cap 10).
- Specialists emit `MED`; the artifact schema says `MEDIUM`. Both gate identically.

## Confidence gating (Unified Confidence Rubric)

**ORDER IS LOAD-BEARING.** When a finding touches shared mutable state (≥2 readers,
grep-derived), the Shared-State Override below is evaluated FIRST and blocks tier-based
suppression: such a finding is never suppressed by the tiers, however low its
confidence. A finding that touches no shared symbol goes straight to the tiers. This is
why the drop is NOT code-enforced as a blind `confidence < 5` filter: shared-state
status is grep-derived, not a finding field, so a mechanical drop would re-automate the
regression the override was built for.

Tiers (for a finding cleared by the override, or not shared-state):
- Confidence 7+: show normally.
- Confidence 5-6: show with the caveat "⚠️ Medium confidence — verify".
- Confidence 3-4: suppress from the main findings — **appendix only, retained, not
  dropped**.
- Confidence 1-2: suppress entirely (dropped).

Do NOT collapse this into a binary keep/drop at 5: the 3-4 appendix tier is retained for
visibility, and the fix rules below (`confidence >= 5`) depend on the
surviving-vs-appendix distinction.

<!-- R-deliver-008 -->
**3c.1 Shared-State Override (BLOCKING — overrides suppression):** a low-confidence
finding may NOT be suppressed if it touches **shared mutable state** — a variable, flag,
field, or attribute read by **2+ code sites**. Confidence reflects the reviewer's
certainty about ONE site; it cannot account for the blast radius across all readers.
The reviewer who flagged it likely checked the same single consumer you would check to
dismiss it.

**Trigger:** the finding references a variable/flag/attribute (e.g. `self._x`, a status
field, a module-level global) that the changeset's diff **reads or writes**, AND that
symbol has ≥2 readers.

**Required action (do NOT suppress until done):**
1. `grep -n "<symbol>" <changed_file>` and across the module → list ALL readers.
2. For EACH reader: does the value change introduced by this changeset alter that
   reader's behavior? Binary yes/no, per site.
3. If ANY reader's behavior changes in a way no test covers → the finding is REAL
   regardless of the reviewer's original confidence. Fix it and add a test.
4. If every reader is verified safe → record the per-site verification in the delivery
   artifact (`shared_state_audit`), THEN suppression is allowed.

## Fix rules (findings that survived gating, confidence >= 5)

- HIGH: fix immediately.
- MED: fix if confidence >= 7, otherwise note it with a recommendation.
- LOW: note in the report only.

Re-run affected tests after any fix through the runner only:
`perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode recheck`.
Record every finding in `adversarial_review.findings[]`; `stage-complete` derives
`findings_total` / `findings_fixed` / `findings_remaining` from that list. For a resolved
HIGH/CRITICAL finding add a `disk_check` (`{"file": "<ABSOLUTE path>", "must_contain":
"..."}` or `must_not_contain`): the validator greps it, because a `resolved: true` flag
records intent, not what is on disk.
## Re-check round (round 2, the fix only)
After the fixes are on disk, write the merged findings (the same `findings[]` you record,
with `specialist`, `severity`, `confidence`, `finding`, `resolved`) to a file and run
`python backend/scripts/artifact_cli.py gate2-recheck --project <P> --run-id <R> --findings @/tmp/<R>/findings.json`.
It diffs every changed file against the copy DELIVER stage-start kept for round 1
(`evidence/prompts/round1/`) into `fix_diff`, and returns `recheck`: one
`<id>-recheck.md` prompt per specialist with a fixed HIGH or conf ≥ 7 MED finding,
holding only that diff and its own items (findings marked `resolved: false` are not
re-checked). Spawn exactly the `recheck` list in ONE message, each with its `spawn` text unchanged.
`[GATE·cli]` (runs created from 2026-10-19T04:00Z): every prompt file the tools hand out
(gate0/gate1, Gate-2 experts, meta-review, re-check, the round-1 manifest) is recorded by sha
in a ledger beside the audit markers; a gate round counts only when it read a ledger sha AND
its first message is exactly the printed `spawn` text. A hand-edited prompt, manifest or
round-1 copy, or a spawn with words added, does not count. NOT RESOLVED from any round sticks;
RESOLVED counts only from an exact-spawn round. Empty list → no re-check.
It exits 1 when such findings exist but no file changed since round 1 (the fixes are not
on disk), and when the snapshot was first taken on a DELIVER re-entry (fixes may already
have been in it): then re-check by re-spawning the round-1 prompts. A changed file the
text diff cannot show (binary, too large) is named on the diff's `NOT INCLUDED` line.
`not_rechecked` lists findings it could not route (no specialist / text, no round-1
prompt): route those by hand. This is the second and last round; a third needs a recorded reason.
Answers are bound too: the re-check prompt numbers the items `R1…`; the specialist answers
`R<n>: RESOLVED` / `R<n>: NOT RESOLVED`, and a finding recorded `resolved: true` whose latest
re-check answer is not RESOLVED blocks. A NOT RESOLVED sticks to that prompt: re-spawning
until a round says RESOLVED does not clear it; fix again + gate2-recheck, or record `resolved: false`.
`[GATE·cli]` (runs created from 2026-10-18T18:00Z): DELIVER `stage-complete` refuses while a
specialist with a fixed HIGH / conf ≥ 7 MED has no sub-agent of this run that read its
`<id>-recheck.md` as gate2-recheck last wrote it (re-run gate2-recheck after a further fix →
spawn again), or, when gate2-recheck refused (late snapshot), no second round of its round-1
prompt. Unprovable (no audit hook) → record `gate2_recheck_unverified_reason` in the record.

## Gate outcome (print it)

```
Adversarial Review Gate (Multi-Specialist):
  Specialists dispatched: N (correctness, security, ...)
  Red Team: dispatched / skipped (reason)
  Total findings: N (X HIGH, Y MED, Z LOW)
  After confidence gating: M shown (K suppressed)
  Fixed: F | Noted: N
  Multi-specialist confirmed: C findings
  PASS → Quality Convergence Loop | FAIL → fix → re-test (max 1 loop)
```

After it passes, the Quality Convergence Loop (card step 7) re-verifies all 6 layers,
including that the findings stay fixed, for up to 3 iterations. L4 re-spawns specialists
only when a convergence fix changes code the original review did not cover.

## What to record in the deliver artifact

`adversarial_review`: `spawned`, `profile_tier` (`full` | `lite` | `skipped`), `findings[]`
(`severity`, `confidence`, `resolved`, `specialist` = the producer's EXPERT-ID copied
from its JSON line, e.g. `correctness` / `red-team` / `meta-review`, `finding` = "path
func() line N: issue. Fixed: how.", optional `disk_check`), `red_team_dispatched`
(`true` / `false`; a specialist HIGH without it BLOCKS on strict profiles for runs created
from 2026-10-18T06:00:00Z, and the Red Team is proven by its EXPERT-ID marker, not by
this claim — `ref/deliver/gate2-dispatch.md`),
`evidence` (which EXPERT-IDs spawned in the one batch), plus optional counts for the
report: `specialists_dispatched`, `after_confidence_gate`, `suppressed`,
`multi_confirmed`. The exact accepted shape is the `artifact_example` that `stage-start`
returns.
