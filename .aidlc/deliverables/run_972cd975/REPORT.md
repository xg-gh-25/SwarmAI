# Needs You 里 Swarm 自己能处理的事改成自动去做（用户原话：「Swarm 能处理的改成自动去做」，接在「稿子可以，四页一起改代码」之后）。 · Audit through DELIVER

Delivered: Needs You 里 Swarm 自己能处理的事改成自动去做（用户原话：「Swarm 能处理的改成自动去做」，接在「稿子可以，四页一起改代码」之后）。 Needs You items that Swarm can handle itself (backend attention\_authority handler == "agent": AGENT\_KINDS = a job that fail. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 51m 57s wall · 51m 57s active · 24.0% budget |
| Quality | WARN | 1/1 verification · 4/9 findings closed (11 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 780k comparable · 594k input · 185k output · 53.5M cache reads, not counted · 54.2M billed · $19.15 book cost · host-session responses in the run window · 65.2% budget |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 2 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · How is a job digest learned without a person? → core.ingestion.sources.s6\_external.extract\_external on the digest file's text, anchored to the file, with the owning Brain as project hint; no agent session. Why: S6 is CDF's door for content outside every session: it keeps references only and never puts external text in a file read every turn, and the extraction call has no tools, so the content cannot act (DESIGN\_SPEC, agent)
- D2 · What may an unattended session run in Bash? → one simple command (no pipe, chain, redirect, substitution or other variable) whose first word is the backend program, limited to: ingest status\|requeue\|wake, cognition find\|get\|map, py -m jobs.job\_manager list\|show\|edit\|pause\|resume\|validate-cron, py -m jobs.scheduler --run-now\|--status\|--list-jobs\|--validate; everything else is denied by a PreToolUse hook that only sessions marked unattended register. Why: Bash is the one channel left that can send data off the machine (external\_approval\_gate skips Bash; dangerous\_command\_gate covers only destructive and irreversible commands); an allowlist fails closed, and no attended session is affected (DESIGN\_SPEC, agent)
- D3 · When is an automatic run finished and did it work? → it ends with a line 'RESULT: resolved' or 'RESULT: not resolved — &lt;why&gt;'; a missing line, an error event, the hang guard or a daemon restart is a failure; a run that said resolved but whose item (same id and fingerprint) is still queued 10 minutes later is a failure too. Why: the outcome comes from a parsed line and the queue itself, never from a model's opinion of its own work (DESIGN\_SPEC, agent)
- D4 · What identifies an item's state? → sha256 of the item id and its dispatch.context (kind and ids, no free text such as 'requested 16 h ago'); a record is pruned once its item leaves the queue and it is not running. Why: text that changes by the hour would start a new run every hour; pruning lets a later recurrence be handled again, once (DESIGN\_SPEC, agent)
- D5 · How many automatic runs at once? → one: a pass starts a run only when none is running. Why: each session holds a CLI process of about 1 GB; the queue is small and runs are minutes long. This is a memory choice, not a spend limit: no run is refused or cut for cost (DESIGN\_SPEC, agent)
- D6 · Hang guard → 2 hours per run; when it fires the run is cancelled and recorded failed. Why: a job fix or CDF unstick takes minutes; only a run that never finishes reaches it (DESIGN\_SPEC, agent)
- D7 · A digest stays queued for its 20 h freshness window whatever is learned from it. Does D3's 'done but still queued 10 min later = failed' apply to it? → no: the rule applies to kinds whose item leaves the queue once fixed (job and the CDF kinds); a done digest shows Done until it ages out. Why: otherwise every learned digest would come back to the person as failed after 10 minutes (BUILD report · swarmai, agent)
- D8 · D2 allowed \`jobs.job\_manager edit\`. Is that safe? → no in general: edit can change a job's prompt, type, config or tool list, and --run-now then runs it (a way to run anything). Only \`edit &lt;id&gt; --json\` changing safety.timeout\_seconds (positive int) and/or schedule is allowed. Why: the live failing job fails with 'timeout' at 480 s, so raising its limit is the fix the run needs, and a time limit or schedule runs no code (BUILD report · swarmai, agent)
- D9 · The in-process UI MCP (ui\_action) is added to every desktop session regardless of enable\_mcp. Leave it in unattended runs? → no: prompt\_builder skips it when agent\_config\['unattended'\]. Why: nobody is at the screen it would move; the session's tools stay to what the item needs (R4.1) (BUILD report · swarmai, agent)
- D10 · The hook admission rule says Bash guards fail open. This one? → fails closed. Why: it is registered only for unattended sessions, so no person is ever blocked, and failing open would be the hole it closes (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Reuse the jobs executor's headless claude --print run (\_handle\_agent\_task) for every kind. Not taken: it runs with --bare, so no SwarmAI hook or gate fires, under bypassPermissions; a digest is external content and the run would hold Bash (read-poison then exfiltrate). It also goes through the daily agent-task count cap (DDD 8e2a6143).
- A3 · Let the desktop app send each agent item's prompt to a chat tab when it sees one (an automatic Hand to Swarm). Not taken: works only while the app is open and a tab is free, writes into the person's own tab, and the same external digest would run in a full session with every tool
- A4 · One unattended session for every kind, digest included, with all tools. Not taken: the digest is external content: a session with Bash could be steered by it to send private files off the machine; CDF already has a no-tool extractor for exactly this content

Requirements analysis:

- missing\_edge (R1.2, R3.2): an item that keeps its id but changes state (a job failing again with a new error) is a new fingerprint and may be handled again once; the same fingerprint never twice → rewritten
- missing\_edge (R3.3): a run cut by a daemon restart must not be restarted forever after each restart → rewritten
- missing\_edge (R4.1): a job digest is external GitHub content read by an unattended run with tool access: the read-poison then exfiltrate chain → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 11/26 tool calls
- correctness · review r1: read 100% of its prompt · 20/26 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- security · review r2: read 100% of its prompt · 9/26 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ edcaa996e7aa: 7 pass, 0 fail, 0 not run
- test · swarmai @ 552acb7c69e5: 5 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: not run
  - backend-security-scan: not run
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: not run
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: pass
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- – first-run-setup-q8rx: feature check not run on this tree
- – pipeline-view-vmna: feature check not run on this tree
- – todo-view-55s5: feature check not run on this tree
- – jobs-view-k98s: feature check not run on this tree
- – settings-view-ze9a: feature check not run on this tree
- – brain-hub-bvcc: feature check not run on this tree
- – capabilities-view-6qa4: feature check not run on this tree
- – chat-tab-draft-nfu9: feature check not run on this tree
- – no-account-banner-6atk: feature check not run on this tree
- – api-health-q56c: feature check not run on this tree
- – chat-send-stream-h4kw: feature check not run on this tree
- – chat-tool-card-m2rp: feature check not run on this tree
- – chat-tabs-apart-x7tn: feature check not run on this tree
- – chat-stop-p9ds: feature check not run on this tree
- – chat-model-error-b3qz: feature check not run on this tree
- – chat-thinking-t5wq: feature check not run on this tree
- changed paths the targets ignore: backend/tests/test\_attention\_authority.py, backend/tests/test\_attention\_auto.py, backend/tests/test\_desktop\_prewarm.py, backend/tests/test\_unattended\_gate.py, desktop/src/components/layout/NeedYouOverlay.test.tsx, desktop/src/services/attention.test.ts


TEST · swarmai:
- python scripts/smoke\_import.py → pass (704 modules import on the synced tree)
- npx tsc --noEmit → pass (clean)
- npm run build → pass (built; bundle under its caps)
- npx vitest run src/components/layout/NeedYouOverlay.test.tsx src/services/attention.test.ts src/hooks/useAttentionQueue.test.ts src/pages/chat/components/WelcomeScreen.test.tsx → pass (33 passed on the synced tree (main's SSH-carrier commit changed the i18n files))
- .venv/bin/python -m pytest tests/test\_attention\_auto.py tests/test\_unattended\_gate.py tests/test\_attention\_authority.py tests/test\_hook\_builder.py tests/test\_prompt\_builder\_properties.py tests/test\_ui → pass (262 passed on the synced tree)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/attention\_auto.py
- swarmai: backend/core/unattended\_gate.py
- swarmai: backend/tests/test\_attention\_auto.py
- swarmai: backend/tests/test\_unattended\_gate.py
