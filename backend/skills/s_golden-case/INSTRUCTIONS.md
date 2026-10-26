# Golden Case — Instructions

Design: `docs/design/2026-10-26-eval-as-product.md` (§3.1–3.3, §3.8). Mechanism:
`core/golden_set_store.py` (the one loader, `load_merged`, and the size limits),
`scripts/golden_case_validator.py` (the gates) and `scripts/eval_runner.py`.

## The two sets

| Set | File | Who changes it | Runs on |
|---|---|---|---|
| Product | `backend/templates/eval/golden_set.yaml` (ships with the backend) | a code change in the SwarmAI repo | a temporary workspace, data folder and HOME (never your data) |
| Yours | `<workspace>/Eval/golden_set.private.yaml` | you, through the dashboard or this skill | your own workspace |

Both load together. A case of yours with the same id as a product case is skipped
and listed in Suggested follow-up. The product set holds at most `PRODUCT_CAP`
cases, both together at most `TOTAL_CAP` (`core/golden_set_store.py`); adding past
the limit is refused, so retire a case first.

Each case belongs to one line, decided by `eval_method`:
- `programmatic` → **quick check** (BVT): fixed input, deterministic, no model;
- `behavior` (and `llm` in your own set) → **offline test**: a real agent answers a
  fixed task, judged against `decision_rubric`.
Online scoring has no cases: it checks your real sessions (`jobs/handlers/session_quality.py`).

## Operations

### ADD — a case of your own
1. Write the case: `id, category, dimension, eval_method, affected_by, evaluators`,
   the `verification` (quick check) or `scenario` + `expected_trajectory` +
   `decision_rubric` (offline test), and
   `purpose: {en: "...", zh: "..."}`: what it protects and what breaks for you if it
   fails (at least one language, ≤200 characters each, no run ids or module names).
   `dimension` is one of `cognition`, `quality`, `responsibility`, `cost`,
   `performance`. `gating` decides the case's power over a release: absent or `watch`
   (the default: shown, never blocks), `gate` (must pass to release; only a quick check
   no model judges can gate), or the existing `redline: true` (any failure vetoes).
   Start a new case as watch and raise it to gate once it has proven itself.
2. A quick-check case needs `verification.negative_command` and
   `negative_expected_contains`: a command that breaks the thing on purpose and must
   print its FAIL token. A case that cannot fail is refused.
3. Validate, then add it with **+ Add Case** on the dashboard (it runs the same
   gates and stamps it), or append it to the private file and re-stamp:
   ```bash
   echo '<case-json>' > /tmp/case.json
   "$SWARM_BACKEND_BIN" py -m scripts.golden_case_validator --case-file /tmp/case.json
   ```

### PROPOSE — a product case (a code change)
A product case must work on any install: no reference to your projects, your
`.context/`, your paths or your machine (`tests/test_eval_product_set.py` checks).
- **Quick check:** a probe in `backend/scripts/probe_<area>.py` built on
  `scripts/_probe_kit.py`: one function decides whether the property holds, the
  normal mode runs it on the real code and prints `<NAME>_OK`, `negative` runs it
  with the property broken (a patched function, a bad input) and must print
  `<NAME>_TEETH`. Commands are `"$SWARM_BACKEND_BIN" py -m scripts.probe_<area> <sub>`
  (plus `negative`); the probe creates what it needs in temporary folders.
- **Offline test:** `scenario.system: swarmai`, read-only tools, an
  `expected_trajectory` (a step may name alternatives: `Grep|Read|Glob`) and a
  `decision_rubric`. It runs in a fresh copy of
  `backend/templates/eval/fixtures/workspace/` (a made-up Demo project, memory and
  archive). Put the facts it needs there and note them in `fixtures/README.md`. Run
  it once for real before proposing it.
- Both: `purpose` in both languages; re-stamp (`validated_by_4gate =
  golden_case_validator.compute_case_stamp(case)`); run
  `tests/test_eval_product_set.py` and `tests/test_eval_fresh_install.py`.

### RETIRE
Archive the case (dashboard archive button): it stays on disk, out of every run, and
can be restored. Product cases are retired by a code change. One invariant, one case:
when a product case covers what one of yours checks, retire yours.

### VALIDATE / AUDIT
Re-run the validator over a case or the whole set to catch drift. Suggested
follow-up already lists: cases failing in each of the last full runs (the case may
itself be broken), cases timing out twice in a row, hidden duplicates and the size
limit.

## The gates (golden_case_validator.py)
| Gate | Checks | Kills |
|------|--------|-------|
| schema | required fields, valid types | malformed cases |
| purpose | `purpose` present (both languages for a product case), bounded | cases nobody can read |
| duplicate | same verification target as existing | corpus bloat |
| non_vacuous | grep≠match-anything, command≠echo-its-own-literal | a case that passes without the knowledge under test |
| teeth (gate-eligible) | declares `verification.negative_command` | probes with no proof they go RED |
| refs | dotted refs (MEMORY./AGENT./…) resolve non-empty | silent ref-drift |
| redline | if `redline` present → bool; if true → a RUNNABLE evaluator | unenforceable red-line markers |

At run time the negative command is executed: it must print
`negative_expected_contains` and must not print the positive marker
(`eval_runner._verify_canary_teeth`). A case edited without re-stamping drops out of
the quick check (`compute_bvt`).

## The `redline` field — zero-tolerance cases (safety / governance ONLY)

`redline: true` marks a case as **zero-tolerance**: if it FAILS, ERRORS or TIMES
OUT, the whole eval is **NO-GO** — `ci_eval_gate` exits 1 with a distinct RED-LINE
message, independent of the aggregate % and of `eval_method`
(`eval_runner.compute_redline`). Use it only for invariants where one failure means
"do not ship". It is part of the stamp, so adding or removing it forces
re-validation. A red-line case that is SKIPPED (not part of the run) does not flip
the gate; `gate_redline` refuses a red-line case with no runnable evaluator.
