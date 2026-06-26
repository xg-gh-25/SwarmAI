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

## The 4 gates (golden_case_validator.py)
| Gate | Checks | Kills |
|------|--------|-------|
| schema | required fields, valid types | malformed cases |
| duplicate | same verification target as existing | corpus bloat |
| non_vacuous | grep≠match-anything, command≠echo-its-own-literal | GUI21 vacuous-pass |
| privacy (PROMOTE only) | no sensitive word / instance-path / DDD ref | shipping instance data (MOD01) |

## Rules
- ADD → private by default. Public is EARNED via PROMOTE + privacy gate + (human review).
- Never hand-edit golden_set.yaml to add a public case — route through PROMOTE so the
  privacy gate runs. Raw edits bypass the ship-boundary backstop.
- The runner merges public+private at load; private absent (clone) → public only.
