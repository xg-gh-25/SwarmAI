# 按方案开工：pipeline 做改动1+3。改动1（P0）：定时 agent job 按『没人看着』处理——jobs/executor.py 把 allowe… · Audit through DELIVER

Delivered: 按方案开工：pipeline 做改动1+3。改动1（P0）：定时 agent job 按『没人看着』处理——jobs/executor.py 把 allowed\_tools 换成真正限制工具集的 --tools；每个 agent\_task 进程跑在 CLI 自带沙箱里，沙箱配置由 job 已声明的 allow\_write / allow\_network / allow\_send 生成（只能写 Sw. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 21m wall · 1h 21m active · 37.5% budget |
| Quality | WARN | 1/1 verification · 7 not run · 4/12 findings closed (17 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · 4 HIGH findings open · +3 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.1M comparable · 788k input · 292k output · 66.9M cache reads, not counted · 68.0M billed · $25.53 book cost · host-session responses in the run window · 82.7% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 3 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · What may a job's Bash write? → The workspace, the app data directory and the system temp dir for every job, minus credential stores, paths that run later and the protected list; allow\_write and allow\_send are removed and warn when present. Why: allow\_write defaults to false, so enforcing it would break every job that declares Write; nothing ever read either field (grep); the app data dir must stay writable for the backend's own CLIs (IMPROVEMENT 2fbc4768) (DESIGN\_SPEC, agent)
- D2 · Is the job sandbox a boundary for secrets? → No, and the code says so: it hides credential files and variables, but measured on CLI 2.1.301 a sandboxed Bash still ran \`security find-generic-password\` and \`gh auth token\` successfully. The decisive control for a job that reads outside text is not declaring Bash. Why: Same finding as the channel-session decision (PROJECT e63ad07e); claiming more would mislead the next reader (DESIGN\_SPEC, agent)
- D3 · Network for a job's Bash → allow\_network false: sandbox network with an empty allowedDomains and strictAllowlist true. allow\_network true: open, plus enableWeakerNetworkIsolation on macOS. Why: Measured: inside the default sandbox \`curl https://example.com\` failed certificate verification (trustd blocked); the weaker isolation opens only trustd, and the network is open for such a job anyway (DESIGN\_SPEC, agent)
- D4 · Where does the protected list live and how is it applied? → core/protected\_paths.py holds credential stores, control and startup files, and paths that run later, plus rule\_path (moved from prompt\_builder.\_rule\_path) and deny\_rules(); every consumer imports it. Why: One list, one path syntax (a bare /abs rule denies nothing, IMPROVEMENT 80a68ab7) (DESIGN\_SPEC, agent)
- D5 · Owner-session sandbox default → Product default stays false; this machine's config turns it on for the trial after landing. Why: The person asked for a one-week trial before a default change; keychain-backed gh and git push are the likely breakages to observe (DESIGN\_SPEC, agent)
- D6 · A job on a platform or CLI that cannot sandbox → Pass --disallowedTools Bash and prefix the result summary with why Bash was withheld. Cannot sandbox = Windows, Linux without bwrap on PATH, or a CLI older than 2.1.301 (the version the behaviour was measured on; unknown settings keys are ignored by an older CLI). Why: Unprotected Bash in a job nobody watches is the hole being closed; a visible note keeps the failure explainable (DESIGN\_SPEC, agent)
- D7 · The Slack DM notification spawn (executor \~L1416) → Also passes --tools "" (no built-in tools; its two MCP tools stay). Why: Same class: it allows only MCP tools by --allowedTools, which under bypassPermissions leaves Bash available, and its message text carries job output (DESIGN\_SPEC, agent)
- D8 · Network host key for owner sessions → Write network.allowedDomains only when the configured hosts are not the wildcard; omit the network list for '\*'. Why: allowedHosts is not a settings key the CLI reads (it is the runtime's internal name), so the current value has never applied; omitting keeps today's open network (DESIGN\_SPEC, agent)
- D9 · The CLI ignores an invalid --settings value entirely and runs unprotected (measured: sandbox and deny rules all off, the probe model wrote into the jobs dir). How is that prevented? → Before each job run, \`claude --settings &lt;same&gt; doctor\` (no model call, about 0.6 s); any 'Invalid settings' line, or a check that cannot run, fails the job with cli\_settings\_rejected. Why: Not in the plan; without it every protection in this change can vanish on a CLI schema change with nothing visible (BUILD report · swarmai, agent)
- D10 · Network shape for a sandboxed command → allow\_network true -&gt; allowedDomains \['\*'\]; false -&gt; empty list with strictAllowlist. enableWeakerNetworkIsolation dropped (replaces plan D3). Why: Measured: with no list the sandbox proxy answers CONNECT with 403; weaker isolation did not fix curl; opening trustd without a measured benefit is not kept (BUILD report · swarmai, agent)
- D11 · R2.6 / R4.3 ask that curl https works under the sandbox → Not met, recorded as a known limit in code comments: on macOS with CLI 2.1.302 the sandbox proxy terminates TLS for listed hosts; git trusts it, curl fails certificate verification. No current job needs it; the owner-session trial is therefore not switched on. Why: A platform limit of the CLI sandbox found by measurement; working around it (custom CA wiring) is a separate decision (BUILD report · swarmai, agent)
- D12 · Owner settings: Edit denies on \~/.ssh? → Only \~/.ssh/authorized\_keys, config, rc and environment are protected for every session; the whole \~/.ssh stays a credential store for jobs. Why: ssh writes known\_hosts on a first connection; denying all of \~/.ssh would break it in owner sessions (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Run agent jobs as SwarmAI SDK sessions so security\_hooks.py applies to them. Not taken: The hooks match command text, which is the weakness being addressed; the job would still see the keychain and the daemon's variables; it moves jobs onto the session machinery the jobs design keeps them out of (process-isolated, zero chat impact).
- A3 · Never give a job Bash (the channel-session model). Not taken: docs-freshness-audit and user jobs legitimately run local commands; the declared tool list, now real, already removes Bash from every job that does not declare it, which is the case for the job that reads outside text.
- A4 · Harden the text gates for owner sessions (AST parsing, taint tracking, a model judging commands). Not taken: Still a judgment over text that a rephrased command escapes; the person asked for no over-design; a model verdict cannot gate (SOUL P5).
- A5 · Write sandbox.filesystem lists separately in each consumer. Not taken: Measured CLI schema: denyWrite/denyRead are merged with Edit(...)/Read(...) deny rules, so one rule set covers Bash and the file tools; two lists would drift.

Requirements analysis:

- missing\_edge (R2.6, R4.3): Measured 2026-11-26 on CLI 2.1.301: inside the sandbox \`curl https://example.com\` failed with 'unable to get local issuer certificate'; macOS tools verify TLS through system services the sandbox blocks by default, and gh/git keep the GitHub token in the Keychain. Turning the sandbox on as-is would break every https call. → rewritten
- missing\_edge (R1.1): Measured: \`--allowedTools Read\` with bypassPermissions still let the model run Bash (a file was written); \`--tools Read\` left it without Bash. allowedTools only pre-approves, it never restricted a job. → rewritten
- missing\_edge (R2.3, R5.2): A job that may write the workspace could rewrite a script or job definition that a later run executes with more rights (found while moving the GitHub job's posting to a script job), so paths that run later are denied even inside the workspace. → rewritten
- missing\_edge (R3.1): allow\_write defaults to false; enforcing it as written would break every existing job that declares Write, so the write scope is the workspace for every job and the two unread fields are removed instead. → rewritten
- missing\_edge (R2.7): Without a sandbox (Windows, Linux without its runtime) the CLI refuses to run sandboxed commands; a job then runs without Bash rather than with unprotected Bash. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r1: read 100% of its prompt · 19/26 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 95ec19b9b070: 10 pass, 0 fail, 0 not run
- test · swarmai @ b66739875494: 8 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5223d29d/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-10-25-swarmai-app-platform-v1.md
- changed paths the targets ignore: backend/tests/bvt\_manifest.txt, backend/tests/cdf\_materializer\_allowlist.txt, backend/tests/test\_job\_cli\_result.py, backend/tests/test\_job\_sandbox.py, backend/tests/test\_protected\_paths.py, backend/tests/test\_restricted\_session\_policy.py, backend/tests/test\_sandbox\_defaults.py, backend/tests/test\_usage\_accounting.py


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_protected\_paths.py tests/test\_job\_sandbox.py tests/test\_sandbox\_defaults.py tests/test\_restricted\_session\_policy.py tests/test\_job\_cli\_result.py tests/test\_usage\_ → pass (the design's invariant tests plus every existing test of the touched functions: 438 passed (run before TEST's two registry-file edits, which only feed test\_cdf\_materializer\_static and the BVT list; th)
- bash scripts/run\_bvt.sh → pass (first run red on test\_cdf\_materializer\_static (the governed-write inventory still named \_handle\_agent\_task and lacked \_cli\_settings\_problem's temp settings file); inventory updated, the two new test f)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_5223d29d → pass (31 walks of the features this change reaches: all pass)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → not\_run (no skill changed)
- python scripts/lint\_experts.py → not\_run (no expert prompt changed)
- npx tsc --noEmit → not\_run (no frontend change)
- npm run lint:contract → not\_run (no frontend change)
- 5 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main 9ae19d7db9e1-&gt;d89853512ae4): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert prompt changed
- swarmai · npx tsc --noEmit: no frontend change
- swarmai · npm run lint:contract: no frontend change
- swarmai · npm run build: no frontend change
- swarmai · bash scripts/sync-version.sh check: no version change
- swarmai · npx vitest run --reporter=verbose: no frontend change
