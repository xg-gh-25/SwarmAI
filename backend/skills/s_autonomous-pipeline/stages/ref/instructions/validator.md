# INSTRUCTIONS ref — 3e. Verify Stage Output (Pipeline Validator)
Read when: a stage-complete / publish / run-update reports validator errors or warnings you need
to interpret, after EVALUATE on the legacy path (DDD checksums), or when running a standalone
DDD / staleness / full-run check. `stage-complete` runs the current stage's checks for you before
it writes anything; this is how to read and act on them.

Legacy path, after execution:
```bash
python backend/scripts/pipeline_validator.py check --project <PROJECT> --run-id <RUN_ID> --stage <STAGE>
```
| # | Check | Severity | What it catches |
|---|-------|----------|-----------------|
| 1 | Stage order | BLOCK | Skipped stages, out-of-order execution |
| 2 | Artifact exists | BLOCK | Missing artifact publish (except reflect) |
| 3 | Artifact schema | BLOCK/WARN | Required fields missing (BLOCK), recommended missing (WARN) |
| 4 | Decision logged | WARN | No decisions classified (except reflect/deliver) |
| 5 | Budget recorded | WARN | token_cost is 0 — needed for calibration |
| 6 | Profile respected | BLOCK | Stage not in the selected profile |
| 7 | DDD consistency | WARN | Non-goals vs TECH.md conflict, failed patterns not recorded, missing DDD docs, staleness since last run (EVALUATE only) |
| 8 | Semantic depth | WARN | BUILD: smoke_tests > 0 when files > 1; REVIEW: integration_trace checked > 0 |

Further numbered checks (e.g. Check 13 upstream routing, Check 8e litmus shape) are named where
they apply. Response: `{"valid": true, "stage": "evaluate", "errors": [], "warnings": [...],
"checks_passed": 8, "checks_total": 8}`.

<!-- R-instr-006 -->
> ⚠️ **`checks_passed` is NOT a quality score — read `errors`/`warnings`, not the count (F4).** The
> ADVISORY checks (4 Decision-logged, 5 Budget-recorded, and the other WARN-severity checks)
> **always credit `checks_passed` whether they pass or fail** — they only ever append a WARNING,
> never reduce the count. So `checks_passed == checks_total` is the NORMAL state even when
> advisory checks flagged issues; it means "no BLOCKING (hard) check failed", NOT "everything is
> clean". The real signals are `valid: false` OR a non-empty `errors[]` (a hard BLOCK), and the
> `warnings[]` list (advisory issues to weigh). Never gate on the count itself (P6: the metric is
> not the outcome).

**Handle the result:**
- `valid: true` → advance. Log any warnings for the delivery report.
- `valid: false` → fix before advancing: missing artifact → publish it; schema violation → update
  the artifact data; stage order → you skipped a stage, go back; profile violation → wrong stage
  for this profile, skip it.
- Fix attempts ≥ max_retries → **checkpoint** with all failure details.

**Write DDD checksums to run.json after EVALUATE** (so future staleness detection works):
```bash
CHECKSUMS=$(python backend/scripts/pipeline_validator.py ddd-check --project <PROJECT> | python -c "import sys,json; print(json.dumps(json.load(sys.stdin)['checksums']))")
python backend/scripts/artifact_cli.py run-update --project <PROJECT> --run-id <RUN_ID> --ddd-checksums "$CHECKSUMS"
```

**Standalone DDD check** (no pipeline needed):
`python backend/scripts/pipeline_validator.py ddd-check --project <PROJECT>` → non-goals, failed
patterns, doc checksums and any cross-doc warnings.

**Staleness check** (which completed runs rest on outdated DDD docs?):
`python backend/scripts/pipeline_validator.py ddd-staleness --project <PROJECT>` → `stale_runs`
(docs changed), `fresh_runs` (matching), `untracked_runs` (no checksums). Exit 1 if any stale run
— usable as a CI gate.

**Full-run validation** (at pipeline end or for debugging):
`python backend/scripts/pipeline_validator.py summary --project <PROJECT> --run-id <RUN_ID>`
