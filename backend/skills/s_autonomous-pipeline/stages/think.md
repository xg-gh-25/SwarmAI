# THINK — execution card

Purpose: research the requirement, stress-test 2-3 constraint-driven designs, recommend one with its reasoning.

**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage think`
(returns the EVALUATE artifact, ACs, and valid `artifact`/`record` examples).

## Steps

1. **Research** (`backend/skills/s_deep-research/SKILL.md`): read the code, not descriptions;
   align with PRODUCT.md, avoid IMPROVEMENT.md failures. → `key_findings[]` (required), `sources[]`.
2. **Alternatives.** Pick the 3 most relevant constraints from SPEED / QUALITY / SIMPLICITY /
   FLEXIBILITY / DELETION: high feasibility → SPEED; high strategic → QUALITY; low feasibility →
   SIMPLICITY; uncertain scope → DELETION. Each approach states **Constraint, What** (1-2
   sentences), **Effort** (S/M/L + sessions), **Risk, Tradeoff**. → `alternatives[]` (≥ 2; ≥ 1
   `targets_root: true` = fixes EVALUATE's root, not the symptom).
3. **Design Risk Probe** (after research, before presenting alternatives). Per approach, name the
   3 riskiest assumptions (max 9 probes) and verify or falsify each from code or
   TECH.md/IMPROVEMENT.md. "Unresolved" only if it needs user intent. Drop non-viable approaches.
   → `risk_probe[]` (`approach, assumption, verification, status`).
4. **Recommend** one approach → `recommendation`; unresolved probes → `ref/think/risk-probe.md`.
   **Systemic or patch?** `[GATE·validator]` `solution_class` = {verdict systemic | patch,
   `addresses` W<n> (the deepest, or `why_not_deepest`), `why_root`, `covers` every sibling C<n>
   ({id, how} or {id, excluded}), `structural_change` none/refactor/redesign + reason}. A root
   at design level needs refactor/redesign, not a patch. `patch` = declared P0 only, with
   `p0_reason` + `follow_up` root fix. No whack-a-mole. `ref/plan/design-spec.md` §0.
5. **Ambiguity re-scan** (ONE round, ask yourself): hedge terms (`_AMBIGUITY_TERMS`, in the
   stage-start example) in `risk_probe[].verification` + `recommendation`; self-answer from
   code/DDD, escalate only real user-intent ambiguity → `ambiguity_scan`.
6. **Depth telemetry:**
   `python backend/scripts/artifact_cli.py run-observe --project <P> --run-id <R> --event think_depth --alternatives <N> --probes <N> --resolved <N> --escalated <N>`
7. **Write** `/tmp/<R>/think.json` = `{"artifact": {...}, "record": {"decisions": [...]}}`
   (shape: stage-start examples; no `--check` first).

THINK runs no tests. If a probe needs code run, only via
`perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode tdd --files <test>`.

## Gates

- **Validator-enforced (`_check_ambiguity_scan`) `[GATE·validator]`:** strict profiles REQUIRE the `ambiguity_scan` block; trivial/research exempt; every hit needs a non-empty resolution (≥12 chars) or it BLOCKS. `hits: []` is valid (scanned, clean). <!-- R-think-001 -->
- **Minimum requirements (BLOCKING):** `[MUST]` (1) at least **2 distinct approaches** with explicit tradeoffs (not 1 approach + "don't do it"); (2) at least **3 risk probes** attempted (verified, falsified, or unresolved); (3) each alternative states its **cost** (effort, risk, tradeoff), not just benefits. "This is obvious" is no excuse: trivial work belongs in the `trivial` profile (skips THINK); if you are here, go deep or EVALUATE mis-classified. <!-- R-think-002 -->
- `[GATE·validator]` `solution_class` + ≥ 2 `alternatives` (≥ 1 `targets_root`) when EVALUATE recorded a `root_cause`; the 3-probe count is discipline. <!-- R-think-003 -->

## Read only if

- `stages/ref/think/alternatives.md` — the constraints don't fit (pure research, docs-only →
  Minimal/Ideal/Creative fallback), or the constraint table / enum note.
- `stages/ref/think/risk-probe.md` — any probe is "unresolved", you think the probe can be
  skipped, or >50% of probes are unresolved on a high-stakes choice (full grill escalation);
  also holds the `risk_probe` JSON example.
- `stages/ref/think/ambiguity-scan.md` — the re-scan finds a hit, or you need the full term list
  and `ambiguity_scan` field shape.

## Exit

`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage think --data @/tmp/<R>/think.json`

It validates, publishes, records and advances. On `ok: false`, fix `errors[].path` (see `expected`).

> **⏭️ Continuation — advance is not the end of your turn.** Read `next_card` from the
> stage-complete output and execute that stage (normally PLAN) **in this same turn**. Legal
> hand-backs only: an L2 or unclear-requirement question (ask, or checkpoint), a user stop, or a terminal
> verdict (`ref/instructions/escalation.md`). `next_action` is
> AUTHORITATIVE: if it withholds the continue instruction (checkpoint due, budget unmeasured),
> follow the payload, not this card.
