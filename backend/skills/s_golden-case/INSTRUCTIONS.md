# Golden Case — Instructions

The sanctioned path to add/validate/promote SwarmAI eval golden cases. Mechanism:
`backend/scripts/golden_case_validator.py` (4 gates) + the public/private split
(`golden_set.yaml` tracked, `golden_set.private.yaml` gitignored).

## Operations

### ADD — author a new case (lands PRIVATE by default)
1. Draft the case dict (id, category, dimension, eval_method, affected_by, evaluators, verification/scenario).
2. Validate (ADD does NOT run the privacy gate — instance cases are allowed in private):
   ```bash
   echo '<case-json>' > /tmp/case.json
   python backend/scripts/golden_case_validator.py --case-file /tmp/case.json
   ```
3. PASS → append to `Eval/golden_set.private.yaml` (default, fail-closed —
   never auto-publish). FAIL → fix the reported gate and re-validate.

### PROMOTE — move a private case to public (runs the PRIVACY gate)
1. The case must be code-only (no DDD/instance refs) and deterministic to ship.
2. Validate with the privacy gate:
   ```bash
   python backend/scripts/golden_case_validator.py --case-file /tmp/case.json --for-public
   ```
3. PASS → move the case from `golden_set.private.yaml` to `golden_set.yaml`.
   FAIL (privacy) → it references instance structure; it stays private. This is the
   ship boundary — a human (XG) reviews promotions; the gate is the mechanical backstop.

### VALIDATE — re-check an existing case or the whole set
Run the validator over a case (or loop the corpus) to catch drift: a case edited
after authoring may have gone vacuous or picked up a privacy leak.

### AUDIT — scan the corpus for rot
- public cases with a privacy hit (should never happen — pre-commit backstop)
- duplicate verification targets
- vacuous assertions
- cases that only ever ERROR (no pass/fail signal → unknown validity)

## The gates (golden_case_validator.py)
| Gate | Checks | Kills |
|------|--------|-------|
| schema | required fields, valid types | malformed cases |
| duplicate | same verification target as existing | corpus bloat |
| non_vacuous | grep≠match-anything, command≠echo-its-own-literal | GUI21 vacuous-pass |
| teeth (new gate-eligible only) | declares verification.negative_command | probes with no proof they go RED |
| refs (non-grandfathered) | dotted refs (MEMORY./AGENT./…) resolve non-empty | C044 silent ref-drift |
| redline | if `redline` present → must be bool; if true → must have a RUNNABLE evaluator | mis-typed / unenforceable red-line markers |
| privacy (PROMOTE only) | no sensitive word / instance-path / DDD ref | shipping instance data (MOD01) |

## The `redline` field — zero-tolerance cases (safety / governance ONLY)

`redline: true` marks a case as **zero-tolerance**: if it FAILS or ERRORS, the whole
eval is **NO-GO** — `ci_eval_gate` exits 1 with a distinct RED-LINE message,
**independent of the aggregate % AND independent of `eval_method`.** This closes the
structural hole where the only hard gate (`bvt.green`) skips every `eval_method:llm`
case (`eval_runner.compute_bvt`), so a semantic red-line (must-refuse, political-
sensitivity, tone, a privacy leak) could only touch the flat percentage where one
failure is averaged away (SOUL P6). Mechanism: `eval_runner.compute_redline`.

**Use it ONLY for genuine zero-tolerance invariants** — the kind where a single
failure means "do not ship", not "quality dipped". A red-line is not a way to make an
ordinary case a hard-blocker; over-marking turns the veto into noise. Rules:
- It is a **first-class, stamp-bound** field (part of `compute_case_stamp`, unlike
  `tags`) — adding/removing it changes the case body → the case must be **re-validated**
  (re-run the validator → new `validated_by_4gate` stamp). This is deliberate: a
  security marker must force re-validation, never be silently toggled.
- A red-line case that is **SKIPPED** at runtime (e.g. an `llm` red-line in a
  programmatic-only canary run) is reported separately and does **NOT** flip the gate
  red — the veto fires on FAIL/ERROR, never on not-run. To prevent an
  always-skip-⇒-always-pass evasion, `gate_redline` refuses a red-line case that has
  no runnable evaluator.
- Zero red-line cases in the corpus = the veto is vacuous (never fires) — fully
  backward compatible.

## Rules
- ADD → private by default. Public is EARNED via PROMOTE + privacy gate + (human review).
- Never hand-edit golden_set.yaml to add a public case — route through PROMOTE so the
  privacy gate runs. Raw edits bypass the ship-boundary backstop.
- The runner merges public+private at load; private absent (clone) → public only.
