# # Fix: a Hive run can land CI and agent-configuration files without review · Audit through DELIVER

Delivered: # Fix: a Hive run can land CI and agent-configuration files without review \*\*Evidence (2026-11-15).\*\* When a Hive run's commits are returned and landed, \`backend/core/hive\_return.py\` protects only pat. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 26m 43s wall · 26m 43s active · no baseline yet (2/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 12/13 findings closed (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 598k comparable · 484k input · 114k output · 17.4M cache reads, not counted · 18.0M billed · host-session responses in the run window · no baseline yet (2/5 runs) |

Knowledge: requirement recalled · 6 · design recalled · 6 · cited 2 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · May a repository's \[hive\_return\] narrow the default set? → No: it only widens what is marked; there is no key that removes a default path. Why: an agent may land .swarmai/pipeline.toml, so a narrowing key would let one Hive landing remove the hold for the next run; the git-behaviour set cannot be narrowed either (DESIGN\_SPEC, agent)
- D2 · Do the repository's own \[hive\_return\] globs become person-only? → No: they stay marked and agent-landable after review, as the funnel decision says. Why: the request is about the default CI and agent-configuration set; A3 explains why (DESIGN\_SPEC, agent)
- D3 · Which paths exactly? → The request's list, the agent directories and AGENTS.md/CLAUDE.md/.mcp.json/Jenkinsfile at any depth, plus .github/actions/\*\* (local actions a workflow runs) and .devcontainer.json (the root alternative of .devcontainer/), both azure-pipelines.yml/.yaml. Why: each is read by CI or a coding agent the same way as a listed path; matching stays case-insensitive as \_is\_protected already does (DESIGN\_SPEC, agent)
- D4 · Does this override the earlier decision that the landing funnel is not where security is enforced? → Yes for these paths, at the person's request of 2026-11-15; docstrings and the skill are updated to say so. Why: the person asked for exactly this refusal; push stays the person's as before (DESIGN\_SPEC, agent)
- D5 · Check the held set over every changed file or only over the marked ones? → every file plus the marked ones, in land() and child merge. Why: a prepared review saved by the previous version marked only GIT\_PROTECTED; the state survives an upgrade (BUILD report · swarmai, agent)
- D6 · Stale frontend comments saying protected paths need their own tick → corrected in ReturnPanel.tsx and RunOverlay.tsx (comments only). Why: they describe the protected-path review this change touches; the tick was removed earlier (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Put the CI and agent-configuration globs into each repository's \[hive\_return\] protected through a seeded default. Not taken: repository globs are agent-landable by design (the funnel decision), so seeding them holds nothing for the person; a repository without the file would still have no protection; and an agent may land .swarmai/pipeline.toml itself.
- A3 · Make every \[hive\_return\] protected glob person-only as well. Not taken: reverses the recorded funnel decision that an agent lands repository-protected paths it reviewed (runs stalled in waiting-for-you); the request asks only for CI and agent-configuration files to be held.

Requirements analysis:

- missing\_edge (R1.1): a nested .claude/, .cursor/, .kiro/ or AGENTS.md/CLAUDE.md in a monorepo subdirectory is read by agent tools too; the set matches them at any depth → rewritten
- missing\_edge (R1.1): .devcontainer.json at the root is the alternative location of the devcontainer config and .github/actions/\*\* holds local actions that workflows run; both are added to the default set as the same class → rewritten
- missing\_edge (R2.2): if \[hive\_return\] could narrow the default set, one Hive landing of .swarmai/pipeline.toml (an agent may land it) could remove the hold for the next run; so narrowing is not offered → rewritten
- conflict (R1.1): an earlier recorded decision rejected forbidding agents to land CLAUDE.md and context files; this request (2026-11-15, the person's) supersedes it for CI and agent-configuration files, while repository-declared globs stay agent-landable per the funnel decision → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 3/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0f89460f909b: 4 pass, 0 fail, 0 not run
- test · swarmai @ 3fc3c96f5bdd: 10 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_73a3fc17bc1b/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_return.py tests/test\_swarm\_run\_cli.py tests/tes → pass (116 passed on the final tree)
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (exit 0)
- python scripts/lint\_skills.py → pass (exit 0)
- python scripts/lint\_experts.py → pass (exit 0)
- bash scripts/sync-version.sh check → pass (exit 0)
- npx tsc --noEmit → pass (exit 0)
- npm run lint:contract → pass (exit 0)
- npx vitest run src/components/swarm-out/ReturnPanel.test.tsx src/components/swarm-out/funnel.test.tsx → pass (15 passed; the desktop change is comments only)
- 2 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: its 53 files include none of the changed modules' tests; run through the task's recorded checks on the landed branch
- swarmai · npm run build: desktop change is comments only; run through the task's recorded checks on the landed branch
