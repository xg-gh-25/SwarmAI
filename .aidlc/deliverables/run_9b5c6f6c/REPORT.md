# Delete, with every reader changed, the repository pieces design 2026-11-20-inbo… · Audit through DELIVER

Delivered: Delete, with every reader changed, the repository pieces design 2026-11-20-inbox §10 lists as replaced by the Inbox: the fallback\_script branch, the signal job chain, the old fetcher location, the Com. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 4m wall · 1h 4m active · 54.4% budget |
| Quality | WARN | 1/1 verification · 1 red at base · 3/3 findings closed (2 test-closed, confirmed by review) · review coverage: security r1 read 6% of its prompt; correctness r1 read 9% of its prompt |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 899k comparable · 710k input · 188k output · 81.3M cache reads, not counted · 82.2M billed · $25.72 book cost · host-session responses in the run window · 63.9% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which job types go with the signal chain? → signal\_digest, signal\_fetch and self\_tune (enum members, executor branches, handlers), plus the fetch-digest buffer (dedup.py, SchedulerState.raw\_signals / dedup\_cache, the maintenance trim, the status counters, execute\_job's feeds / user\_context parameters and load\_user\_context). Why: each only fed the digest; leaving them is a consumer of the deleted chain with no reader (DESIGN\_SPEC, agent)
- D2 · How is a retired config key reported? → load\_jobs logs one warning per job per load for fallback\_script / fallback\_cwd / output\_as\_signals and keeps the job. Why: the design asks for a warning, never a silent drop; load\_jobs is the single place both the scheduler and the run-now endpoint read jobs from (DESIGN\_SPEC, agent)
- D3 · Which folder readers change and how? → swarm\_workspace\_manager stops creating Knowledge/Signals; library\_health drops Signals from its raw-log categories; SWARMAI.md / KNOWLEDGE.md / needs\_human\_review / product\_registry / workspace\_surface\_watcher describe Signals and DailyBriefs only as archived history (their segment matches still classify or skip archived files, which is correct for Knowledge/Archives/Signals). Why: the archived files still exist under Archives; classification and skip rules by segment stay useful, only the 'live folder' description and the folder creation go (DESIGN\_SPEC, agent)
- D4 · Where do the recall block and the untrusted fence apply? → filter\_recall drops a hit of any domain whose doc / source path has a DailyBriefs or Signals segment; is\_external\_source is true for any Signals segment. Why: the requirement is 'never auto-inject', not 'never from the library leg'; segment matching covers nested and Archives copies (DESIGN\_SPEC, agent)
- D5 · The /api/inbox/feeds writes were exempt from X-Swarm-User-Action only 'until the Community page is deleted' (schemas/inbox.py). Keep the exemption? → No: the feed writes now require the header like every other Inbox write. Why: the contract tied the exemption to the Community page this run deletes; the desktop already sends the header on every feed write (BUILD report · swarmai, agent)
- D6 · jobs/adapters/http\_client.py is the shared SSRF client, not a fetcher. Where does it go? → core/http\_client.py; fetchers, routers/inbox.py and s\_notify import it there. Why: s\_notify falls back to an unguarded httpx client on ImportError, so a stale import would silently drop the guard (BUILD report · swarmai, agent)
- D7 · rss.py moved into core/, where the silent-except baseline applies → narrowed its two date-parse excepts to the exceptions the parsers raise instead of baselining them. Why: a broad swallow there would only hide bugs; behaviour for bad dates is unchanged (None) (BUILD report · swarmai, agent)
- D8 · Workspace provisioning still created Services/signals and wrote a self-tune user\_context block into a new config.yaml → removed both; the config.yaml header now names the Inbox. Why: nothing writes or reads them after the signal chain is gone (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Delete the signal jobs and Community but leave jobs/adapters in place and re-export from core/inbox/feeds. Not taken: a re-export keeps the old module alive as a second import path, so the 'no consumer left' check can never pass and the signal-job home stays the fetchers' owner (design §10 says the old location goes)
- A3 · Move http\_client.py together with the fetchers into core/inbox/feeds/. Not taken: it is not a fetcher: s\_notify and routers/inbox.py use it as the shared SSRF-guarded client, and making the notify skill import from the Inbox feed package couples two unrelated features
- A4 · Delete recall\_multi's EXTERNAL\_SOURCE\_PREFIXES along with the signal chain. Not taken: archived Signals files stay in the Library index and are still reachable on demand; removing the untrusted fence would let fetched third-party text read as trusted (security baseline B1), so it is kept and widened to any Signals segment instead

Requirements analysis:

- missing\_edge (R2.4): a user job that names a removed job type would fail Job validation; load\_jobs already catches a per-job error and skips it with a warning, so the other jobs keep running → rewritten
- missing\_edge (R2.5): state.json written before the change carries raw\_signals / dedup\_cache; pydantic ignores unknown fields, so dropping the fields is load-safe and the next save drops them → rewritten
- missing\_edge (R2.6): recall\_multi.py:629 is the untrusted fence for Signals text; deleting it would weaken a security control for on-demand reads of archived signals, so the change keeps the fence and widens it to any Signals segment → rewritten
- missing\_edge (R3.2): jobs/adapters/http\_client.py is not a fetcher but the shared SSRF-guarded outbound client; s\_notify falls back to a bare httpx client on ImportError, so a missed import would silently drop the guard. It moves to core/http\_client.py and every importer is changed → rewritten
- missing\_edge (R4.3): routers/inbox.py reads parse\_sources from core.community\_data; the parser (and its member helpers) moves under core/inbox/feeds before community\_data is deleted → rewritten
- missing\_edge (R2.2): the signal-fetch handler and the dedup buffer (state.raw\_signals, dedup\_cache, the maintenance trim, the jobs status counters, execute\_job's feeds and user\_context parameters, load\_user\_context) only fed the digest; they go with it → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 6% of its prompt · 27/33 tool calls
- correctness · review r1: read 9% of its prompt · 26/33 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 716395a761a8: 10 pass, 0 fail, 1 not run
- test · swarmai @ c25fdfcace7b: 12 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- – first-run-setup-q8rx · \* [\*] not\_run: needs uv
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- – pipeline-view-vmna · \* [\*] not\_run: needs uv
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- – todo-view-55s5 · \* [\*] not\_run: needs uv
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- – jobs-view-k98s · \* [\*] not\_run: needs uv
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- – settings-view-ze9a · \* [\*] not\_run: needs uv
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- – brain-hub-bvcc · \* [\*] not\_run: needs uv
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- – capabilities-view-6qa4 · \* [\*] not\_run: needs uv
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- – chat-tab-draft-nfu9 · \* [\*] not\_run: needs uv
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- – no-account-banner-6atk · \* [\*] not\_run: needs uv
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/27/0-0-app-walk-api-health-q56c.log
- – chat-send-stream-h4kw · \* [\*] not\_run: needs uv
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- – chat-tool-card-m2rp · \* [\*] not\_run: needs uv
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/32/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/33/0-0-test-finished-1.png
- – chat-tabs-apart-x7tn · \* [\*] not\_run: needs uv
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/35/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/36/0-0-test-finished-1.png
- – chat-stop-p9ds · \* [\*] not\_run: needs uv
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/38/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/39/0-0-test-finished-1.png
- – chat-model-error-b3qz · \* [\*] not\_run: needs uv
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/41/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/42/0-0-test-finished-1.png
- – chat-thinking-t5wq · \* [\*] not\_run: needs uv
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/44/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_9b5c6f6c/artifacts/feature-checks/swarmai/45/0-0-test-finished-1.png
- changed paths under no target: .secrets.baseline, bandit-baseline.json, docs/SwarmAI-Architecture-Design.md, docs/USER\_GUIDE.md


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_job\_retired\_config\_keys.py tests/test\_inbox\_deletion\_sweep.py tests/test\_recall\_no\_inject\_segments.py tests/test\_inbox\_source\_feeds.py tests/test\_inbox\_api.py tes → pass (the plan's final set (X1-X5) plus the inventories the change touched: 390 tests)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- npx tsc --noEmit → pass (desktop files unchanged since this run (the review fixes were backend-only))
- npm run lint:contract → pass (desktop files unchanged since this run)
- npm run build → pass (bundle 4,062 kB under the 4,186 kB cap)
- 3 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: only tests/test\_precommit\_uv\_lock.py (3 tests) fails: uv is not on this shell's PATH; identical on the base checkout. Everything else in the BVT passes, including the three files this run added to it

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/inbox/feeds/\_\_init\_\_.py
- swarmai: backend/core/inbox/feeds/config.py
- swarmai: backend/tests/test\_inbox\_deletion\_sweep.py
- swarmai: backend/tests/test\_job\_retired\_config\_keys.py
- swarmai: backend/tests/test\_recall\_no\_inject\_segments.py
