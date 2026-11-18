# 两条合成一个 run 做。(1) Hive 还是本地的派发判断：现在 s\_swarm-out 只在用户说『派到 Hive』时触发，AGENT R1 只管 na… · Audit through DELIVER

Delivered: 两条合成一个 run 做。(1) Hive 还是本地的派发判断：现在 s\_swarm-out 只在用户说『派到 Hive』时触发，AGENT R1 只管 native 还是 pipeline，没有任何规则决定一个任务在本地跑还是派 Hive。要让 Host 在接任务时判断：预计耗时长（例如超过约 30 分钟的 full/bugfix pipeline 或长调研）、用户想离开或本机已忙、多件互不依赖. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 34m 52s wall · 34m 52s active · 22.8% budget |
| Quality | WARN | 1/1 verification · 1 not run · 1 red at base · 2/2 findings closed (1 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 258k comparable · 193k input · 65k output · 14.0M cache reads, not counted · 14.3M billed · $5.64 book cost · host-session responses in the run window · 6.3% budget |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 3 · searched the DDD 3×

## Decisions

- Q1 · Are the two parts one run or two? → One run: the person asked for it ('两条合成一个 run 做'), even though the parts share no file (REQUIREMENTS, agent)
- D1 · Does the agent dispatch to a Hive on its own when the rule says Hive? → No: it proposes in one line with the reason and dispatches only after the person accepts. Why: Dispatch sends the code and the task off this machine; SOUL P10 keeps off-machine acts for the person (DESIGN\_SPEC, agent)
- D2 · Is split required in requirements.json? → Optional; the card asks for it when the requirement has more than one part. Why: A required field changes every run's shape and adds friction to every EVALUATE; existing runs must keep validating (DESIGN\_SPEC, agent)
- D3 · Does the engine act on split.decision separate? → No: the next step states it (parts, create one run per part or go on, Hive candidates); the run continues as the orchestrator decides. Why: The orchestrator owns the decision; the engine only says it at the cheapest point, as split\_hint already does (DESIGN\_SPEC, agent)
- D4 · On which stage is the split advice stated? → On the first stage after EVALUATE (THINK for full and bugfix, PLAN for trivial). Why: That is the earliest point after the decision is recorded, before any design work is spent (DESIGN\_SPEC, agent)
- D5 · s\_swarm-out's description went over the 250-character lint cap with the new trigger → Dropped the 'run it in the cloud' trigger phrase and tightened the first sentence. Why: The lint cap is fail-closed; 'send this to a Hive', 'Swarm Out' and '派到 Hive' still trigger it, and the new proposal trigger matters more (BUILD report · swarmai, agent)
- D6 · What if next\_step cannot read requirements while giving the advice? → Log a warning and give no advice. Why: A hint must never break the next step; an unreadable document is EVALUATE's to report (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Only reword the EVALUATE card and AGENT R1, no new field. Not taken: Leaves no record of the one-or-several decision and nothing reads it; the person asked for EVALUATE to record the dependency between parts explicitly.
- A3 · Let the engine infer at EVALUATE whether requirements are independent. Not taken: Independence of requirements is semantic; no deterministic signal exists at EVALUATE, and the file-count threshold of split\_hint is exactly the proxy being moved away from.
- A4 · A hook that detects long tasks at intake and proposes or forces a Hive. Not taken: SOUL P7: a gate only for safety, off-machine acts or session integrity; there is no deterministic duration signal at intake, and dispatch is the person's choice, so the judgment belongs in the agent's rule text.

Requirements analysis:

- missing\_edge (R1.2): Dispatching to a Hive sends code and the task off this machine, so the agent may not decide it alone (SOUL P10 keeps off-machine acts for the person); the agent decides the judgment and the person's one acceptance is the only step. → rewritten
- missing\_edge (R2.3): Making split required would change the shape every run must write and add friction to every EVALUATE; it is optional and the card asks for it when there is more than one part. → rewritten
- vague (R1.1): 'Long' is not a fixed minute count; it is named by kind of work (a full or bugfix pipeline run, a long investigation), which the agent knows at intake, rather than an estimated duration it cannot measure. → rewritten
- missing\_edge (R2.4): The engine does not abandon or block the run on split.decision separate: the decision is the orchestrator's, the next step only states it at the cheapest point (before THINK), consistent with split\_hint being a hint, not a check. → rewritten

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ ff9147c834a6: 6 pass, 0 fail, 0 not run
- test · swarmai @ 7d8e4f5194c5: 4 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pre_existing
  - skills-lint: pass
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest tests/test\_pipeline\_v1\_split\_at\_evaluate.py tests/test\_where\_work\_runs\_rule.py tests/test\_run\_protocol\_schemas.py test → pass (176 tests)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_skills.py → pass
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pre\_existing (211 'high entropy string' hits, all in backend/skills/s\_frontend-design/data/modern-web-guidance/ (PROVENANCE.json hashes, SRI values in a guide), a file set this run does not touch; the same 211 hits)
- bash scripts/run\_bvt.sh → not\_run (the change's tests and the suites of every touched module ran above; the BVT allowlist is CI's broad coverage)

Tests were not run again after main moved: tests carried over a main move: swarmai (main 739735668ce1-&gt;6880e8f8bee1): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Red at the run's base too (the agent's report, not checked)

- swarmai · /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py: 211 'high entropy string' hits, all in backend/skills/s\_frontend-design/data/modern-web-guidance/ (PROVENANCE.json hashes, SRI values in a guide), a file set this run does not touch; the same 211 hits on the run's base tree (/var/folders/nv/h2twmmm90dn\_3wcrpz8zwl980000gq/T/swarmai-base/run\_a4997369/

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the change's tests and the suites of every touched module ran above; the BVT allowlist is CI's broad coverage

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_pipeline\_v1\_split\_at\_evaluate.py
- swarmai: backend/tests/test\_where\_work\_runs\_rule.py
