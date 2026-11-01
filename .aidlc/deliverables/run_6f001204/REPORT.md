# # Fix: refs printed by \`cognition find\` cannot be read back with \`cognition get\` · Audit through DELIVER

Delivered: # Fix: refs printed by \`cognition find\` cannot be read back with \`cognition get\` The \`cognition\` CLI (backend/core/knowledge\_cli.py, backend/core/cognition.py, backend/core/addressing.py; design docs/. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 32m 57s wall · 32m 57s active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 0/2 findings closed |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 483k comparable · 382k input · 101k output · 17.9M cache reads, not counted · 18.4M billed · host-session responses in the run window · no baseline yet (1/5 runs) |

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+2 found on demand) · searched the DDD 3×

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 13/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ c8c5bc9ffcb1: 13 pass, 1 fail, 0 not run
- test · swarmai @ c8c5bc9ffcb1: 3 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' python -m pytest tests/test\_cognition\_refs.py tests/test\_cognition.py tests/test\_knowledge\_cli.py tests/test\_addressing.py -q --timeout=60 --maxfail=1000 → pass
- python scripts/smoke\_import.py → pass
- python scripts/security\_scan.py → pass

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_cognition\_refs.py
