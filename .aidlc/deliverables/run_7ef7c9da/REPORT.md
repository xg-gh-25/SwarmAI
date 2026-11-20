# Extract the stdio JSON-RPC client from SlackMcpBridge into core/mcp\_stdio\_clien… · Audit through DELIVER

Delivered: Extract the stdio JSON-RPC client from SlackMcpBridge into core/mcp\_stdio\_client.py, switch the bridge onto it, and implement the Slack (mcp, slack-web), mail (mcp, imap/smtp), GitHub (gh) and feed so. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 1h 45m wall · 1h 45m active · 109.2% budget |
| Quality | WARN | 1/1 verification · 5 not run · 1 red at base · 12/12 findings closed (16 reported; within 10 lines of one file they count once) (8 test-closed, confirmed by review) · review coverage: correctness r1 read 23% of its prompt; security r2 read 84% of its prompt · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | WARN | 1.3M comparable · 990k input · 360k output · 55.3M cache reads, not counted · 56.7M billed · host-session responses in the run window · 101.3% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Where do the Slack user token and IMAP/SMTP password come from? → core/inbox/sources/credentials.py: macOS keychain via the security CLI (as backup\_manager does), libsecret secret-tool on Linux when present, else the app data dir's inbox-secrets.json written 0600 and refused when group/other can read it; secrets are written through stdin, never argv. Why: the app has no generic secret store: AppConfigManager.set\_secret accepts only its fixed SECRET\_KEYS and is not owned by this run; this follows the existing backup-token pattern minus the plain env var the task forbids (DESIGN\_SPEC, agent)
- D2 · How is the mail MCP's can\_send decided? → true only when tools/list advertises email\_reply AND the server's resolved env (its configured env over the daemon env) sets OUTLOOK\_MCP\_ENABLE\_WRITES to a true value. Why: the design names OUTLOOK\_MCP\_ENABLE\_WRITES as the switch; the tools/list check also covers a server that hides write tools when writes are off; neither can be verified here against the real server, so both must hold (fail closed) (DESIGN\_SPEC, agent)
- D3 · How does the slack-mcp mapping learn who the person is, with no auth.test tool? → search for the person's own latest message (query from:me, sorted by timestamp) and read its author id; if nothing comes back, whoami raises a SourceError asking for a first message, never guesses. Why: identity must come from the source (P7); no listed tool returns the authenticated user directly (DESIGN\_SPEC, agent)
- D4 · How does the runner find the concrete sources? → a new module core/inbox/sources/builtin.py imports the four modules; each module registers its factories at import. Why: sources/\_\_init\_\_.py is shared with the other runs; importing builtin is one line for run A's runner (DESIGN\_SPEC, agent)
- D5 · What happens to the bot adapter's fallback discovery (substring 'slack' in mcp-dev.json)? → unchanged in this run; only its transport moves onto McpStdioClient. Why: the task limits slack.py to switching the bridge onto the client; changing which server the bot uses is a behaviour change for another owner (DESIGN\_SPEC, agent)
- D6 · The task asks for two commits (extract, then use); the run's worktree refuses hand commits and the landing makes one commit. → one landing commit whose message describes both steps; the extraction-only diff and its green Slack test log are saved in Outputs/so\_7ef7c9dabde8/. Why: the worktree gate is structural; the extraction was verified on its own before any source code existed (BUILD report · swarmai, agent)
- D7 · Shared helpers and secret storage need a home the task's file list does not name. → new files core/inbox/sources/{common,credentials,builtin}.py and tests/inbox\_source\_fakes.py. Why: new files under this run's own sources/ directory cannot conflict with the other three runs; nothing outside the list was edited except two test-infra lines (BUILD report · swarmai, agent)
- D8 · Moving the bridge's transport out of channels/adapters/slack.py lowers its silent-except count 12 -&gt; 9; the baseline must match the scan. → update that one entry of tests/silent\_except\_baseline.json; every new broad except logs instead of adding baseline entries. Why: keeps the baseline honest without regenerating entries other changes own (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep SlackMcpBridge as is and write a second, Inbox-only MCP client. Not taken: two transports for one protocol drift apart; the design (section 2) asks for the extraction, and the old bridge's bugs (late response read as the next answer, 64 KiB line limit, undrained stderr) would stay in the bot path
- A3 · Use the official mcp Python SDK client session. Not taken: not a dependency of the backend (checked pyproject); adding one for a 200-line stdio loop widens the supply chain and the bundle for every user
- A4 · Classify pull calls with core.tool\_risk instead of a fixed READ\_TOOLS list. Not taken: tool\_risk defaults unknown names to READ, so mark\_read or email\_move would pass (design section 4)

Requirements analysis:

- missing\_edge (R1.2): the old bridge kept the process after a timeout, so the late response would be read as the answer of the next call → rewritten
- missing\_edge (R1.1): asyncio's default 64 KiB line limit breaks on large enriched Slack/Outlook results → rewritten
- missing\_edge (R2.2): read tools with side effects (email\_read markAs, IMAP SELECT/FETCH without PEEK) would mark mail read during pull → rewritten
- missing\_edge (R2.13): the app has no generic secret store: AppConfigManager.set\_secret only accepts a fixed key list and is not owned by this run; the backup token uses keychain on macOS and a 0600 file elsewhere → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 8/28 tool calls
- correctness · review r1: read 23% of its prompt · 18/28 tool calls
- security · review r2: read 84% of its prompt · 12/27 tool calls
- correctness · review r2: read 100% of its prompt · 16/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ f475d60c421c: 8 pass, 0 fail, 0 not run
- test · swarmai @ bec139a67942: 8 pass, 0 fail, 5 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_inbox\_sources\_common.py tests/test\_inbox\_source\_slack.py tests/test\_inbox\_source\_mail.py tests/test\_inbox\_source\_github.py tests/test\_inbox\_source\_feeds.py tests/ → pass (93 passed on the reviewed tree (also inside the BVT run))
- .venv/bin/python -m pytest tests/test\_slack\_mcp\_fallback.py tests/test\_slack\_adapter.py -q --timeout=60 → pass (existing Slack channel adapter tests unchanged and green (run inside the BVT run below))
- python-backend py -m scripts.mutation\_check m6\_final.json + m6b.json + m5\_review2.json -- the six inbox/MCP test files → pass (every invariant mutation caught on the final tree (22 + 1 + 5))
- python scripts/smoke\_import.py → pass (all modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (PASS)
- python scripts/lint\_skills.py → pass (clean)
- python scripts/lint\_experts.py → pass (clean)
- bash scripts/run\_bvt.sh (every manifest file, --maxfail=1000) → pre\_existing (1069 tests incl. the Slack adapter suites; the only failures are test\_cdf\_materializer\_static (core/feature\_map writers), test\_silent\_except\_baseline (core/feature\_map/files.py, core/run\_protocol/engi)
- npx tsc --noEmit → not\_run (no desktop file changed; run through task\_checks on the landed tree)
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh (every manifest file, --maxfail=1000): 1069 tests incl. the Slack adapter suites; the only failures are test\_cdf\_materializer\_static (core/feature\_map writers), test\_silent\_except\_baseline (core/feature\_map/files.py, core/run\_protocol/engine.py only) and test\_precommit\_uv\_lock, which fail the same way on the run's base tree

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks on the landed tree
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks on the landed tree
- swarmai · npm run build: no desktop file changed; run through task\_checks on the landed tree
- swarmai · bash scripts/sync-version.sh check: no version file changed; run through task\_checks on the landed tree
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks on the landed tree
