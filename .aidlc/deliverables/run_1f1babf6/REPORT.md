# dangerous\_command\_gate asks the person for commands it could prove safe because… · Audit through DELIVER

Delivered: dangerous\_command\_gate asks the person for commands it could prove safe because a removed repo reads as 'git cannot tell', same-command literal variables are not resolved, redirect fd numbers are read. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 3h 33m wall · 3h 33m active · no baseline yet (2/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 1 red at base · 5/22 findings closed (30 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) · 1 fail-to-pass · 3 HIGH findings open · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.9M comparable · 1.4M input · 548k output · 110.1M cache reads, not counted · 112.1M billed · $43.91 book cost · host-session responses in the run window · no baseline yet (2/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Where does 'no longer a work tree' get decided? → In shared\_checkout.sibling\_uncommitted, only after git failed: root not a directory, no .git, or a .git file whose gitdir is missing -&gt; \[\]; otherwise GitUnknown as before. Why: A real failure on a live tree must still raise; checking only after failure costs nothing on the normal path (DESIGN\_SPEC, agent)
- D2 · When is a variable's value known? → Exactly one binding of the name in the whole command (assignment words in any segment, binder builtins, arithmetic/compound assignment text), that binding a standalone top-level assignment segment outside any (...) or keyword block, preceded by start or ';' and not followed by a pipe or '&amp;'; no backtick, IFS, source/./eval, declare -n anywhere; the reference not single-quoted or backslash-escaped; value a word of \[A-Za-z0-9\_./@%+:,=-\] (or an unquoted \~/ path), no .. component, not starting with '-'. Why: Counting bindings over the whole command (not only before the use) closes loops that rebind after the use; requiring an unconditional assignment closes \`false &amp;&amp; W=/tmp/a; rm -rf $W/etc\` (DESIGN\_SPEC, agent)
- D3 · Are relative literal words allowed as values? → Yes, substituted textually as the shell would; each predicate then judges the resulting path as written (relative paths stay relative). Why: Representative command 11 builds its destination from $W and $n=SwarmAI (DESIGN\_SPEC, agent)
- D4 · How is a redirect's fd number dropped? → In \_gate\_shell\_segments, before shlex: an unquoted run of digits that starts a word and is immediately followed by &lt; or &gt; is removed (it is part of the operator). Why: Fixes every predicate at the segmenter layer, as the task asks; \`echo 2 &gt; f\` keeps its 2 because a space separates it (DESIGN\_SPEC, agent)
- D5 · What does a Python body count as? → ast.parse it; a discard is an argv list or tuple literal starting with a string whose shell-joined line \_discards\_work judges a discard, or a literal / f-string command passed to a process call (subprocess.\*, os.system/popen/exec\*/spawn\*, asyncio subprocess, and names imported from them). A process call with a non-literal command, dynamic execution, or a parse failure -&gt; the current signature + can-execute rule. python -c CODE is judged the same way. Why: Prose in literals is data; anything the AST cannot prove falls back to today's behaviour (fail closed) (DESIGN\_SPEC, agent)
- D6 · Is a store-named path under /tmp a store? → No, when the operand (after substitution) is an absolute path written out in full under the same OS temp prefixes the rm rule trusts. Why: A governed store is never under an OS temp dir; representative command 11 copies a DDD into /tmp, possibly over an earlier attempt's copy (DESIGN\_SPEC, agent)
- D7 · rm and store checks skipped segments starting with a shell keyword (\`do rm -rf /\`) - fix here? → Yes: \_strip\_keywords before both checks (a tightening found while reading the rm loop; same functions this change rewrites). Why: \`for x in 1; do rm -rf /; done\` and \`if true; then rm .context/MEMORY.md; fi\` passed with no prompt on the base (BUILD report · swarmai, agent)
- D8 · Substitute a reference followed by anything other than \`/\`? → No: only when the reference ends the word or \`/\` follows. Why: zsh reads $W\[1\] as W's first character and $W:h as its dirname: \`W=/tmp/a/b; rm -rf $W:h:h\` is rm -rf /tmp (BUILD report · swarmai, agent)
- D9 · The gate's predicates each re-split the same command; the resolver adds a split and an AST parse → Cache \_split\_segments, \_known\_vars and \_python\_discards per command text (lru 32/64/32); callers get fresh lists, the dicts are read-only. Why: Keeps the per-call cost at the base's level on the event loop (measured above) (BUILD report · swarmai, agent)
- D10 · Parse every Python body? → Only bodies the call-shape signature already matches; the AST verdict can only clear a match, never add one beyond it. Why: The new judgement is never looser than the old rule, and bodies without git calls are not parsed (BUILD report · swarmai, agent)
- D11 · The approval class of \`sudo -u x rm -rf .context\` changes from unknown to destructive\_data → Accept and update the test expectation. Why: No change to approval\_class itself: the store rule now sees the rm behind sudo's options, so the class is that rule's, the person's either way, exactly as for \`sudo rm -rf .context\` (TEST report · swarmai, agent)
- D12 · Residuals left by the review cap → Report, do not change code after the review closed. Why: env --argv0 (long form) not skipped; xargs-run rm not judged (pre-existing); the brace/glob program-word rule also treats \[ and \[\[ as expansions, so commands with them keep base behaviour for variables (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Remember approvals per rule per session (approve the rule once, not the command hash). Not taken: Hides the false positives instead of removing them, changes the approval card flow (excluded by the task), and would let a real discard through after one approval.
- A3 · Run the bash parser (bash -n / a real shell AST library) to resolve variables. Not taken: bash -n gives no variable values, a shell AST dependency is new for every platform, and the gate runs on the event loop's executor where an extra subprocess per command costs latency; the existing segmenter already carries the structure needed.
- A4 · Drop gone repos from repo\_guidance.\_repo\_root's cache instead of checking liveness at the consumer. Not taken: The cache feeds other readers (shared-checkout note, guidance hook) and a stale entry can also come from a path cached before removal elsewhere; checking at the one consumer that turns failure into GitUnknown is the smallest change that makes 'repo gone' distinct from 'git cannot tell'.

Requirements analysis:

- missing\_edge (R2.1): Loops, conditionals, pipelines and sourced files can rebind or skip an assignment; a single top-level unconditional assignment that is the only binding of the name in the whole command (not just before the use) is required, and source / . / eval / IFS / backticks / declare -n make every variable unknown. → rewritten
- missing\_edge (R2.1): A single-quoted or backslash-escaped $NAME is literal text, not a reference; such names are not substituted. → rewritten
- missing\_edge (R2.1, R6.3): Example 11 needs $n (a plain word, n=SwarmAI) inside the destination as well as $W; a literal relative word is allowed as a value (it substitutes textually, exactly as the shell would), and a destination under /tmp can exist from an earlier attempt, so a store-named path under an OS temp prefix is scratch, consistent with the recursive-rm rule. → confirmed
- missing\_edge (R5.1): python -c CODE bodies were not judged for discards at all today; they are judged with the same AST rule (prose is data), which adds asks only for a real git discard. → confirmed
- missing\_edge (R3.2): A trailing slash on a symlinked cache name could follow the link; an operand with a trailing slash or one whose on-disk realpath is not so named stays dangerous. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 12/26 tool calls
- correctness · review r1: read 100% of its prompt · 14/26 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- security · review r2: read 100% of its prompt · 13/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r2: read 100% of its prompt · 17/25 tool calls
- security · review r2: read 58% of its prompt · 13/27 tool calls
- security · review r2: read 100% of its prompt · 17/25 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 17/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 47% of its prompt · 21/29 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 8c5e443b93fa: 6 pass, 0 fail, 0 not run
- test · swarmai @ 96c7480be953: 12 pass, 0 fail, 5 not run
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
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_1f1babf6/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: AGENTS.md, docs/design/2026-10-26-approval-prompt-false-positives.md, docs/design/2026-11-25-approval-prompt-false-positives-r2.md
- changed paths the targets ignore: backend/tests/test\_approval\_prompt\_false\_positives\_r2.py, backend/tests/test\_command\_segments.py, backend/tests/test\_hive\_approvals\_host.py, backend/tests/test\_prompt\_board\_review.py, backend/tests/test\_shared\_checkout.py


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_approval\_prompt\_false\_positives\_r2.py -q -x --timeout=60 -p no:cacheprovider → pass (the fail-to-pass matrix incl. all review rounds' cases)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_approval\_prompt\_false\_positives\_r2.py tests/test\_shared\_checkout.py tests/test\_security\_hooks.py tests/test\_discard\_gate.py tests/ → pass (605 tests)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_agent\_lands\_its\_run.py tests/test\_dispatched\_reserved\_text.py tests/test\_hive\_approvals\_host.py tests/test\_main\_dirty\_scan\_repeats → pass (306 tests)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_audit\_marker\_write\_gate.py tests/test\_background\_command\_guard.py tests/test\_bash\_misuse\_report.py tests/test\_run\_session\_link.py  → pass (655 tests: the other gates that read \_unwrap\_program)
- perl -e 'alarm 100; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_command.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider → pre\_existing (test\_no\_other\_module\_matches\_pipeline\_command\_text fails on core/swarm\_run\_card.py:27 (untouched); it fails the same way on the run's base checkout)
- backend/.venv/bin/python scripts/smoke\_import.py → pass
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- backend/.venv/bin/python scripts/security\_scan.py → pass
- backend/.venv/bin/python scripts/lint\_skills.py → pass
- backend/.venv/bin/python scripts/lint\_experts.py → pass
- 8 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · perl -e 'alarm 100; exec @ARGV' .venv/bin/python -m pytest tests/test\_pipeline\_command.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider: test\_no\_other\_module\_matches\_pipeline\_command\_text fails on core/swarm\_run\_card.py:27 (untouched); it fails the same way on the run's base checkout

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks after landing
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks after landing
- swarmai · npm run build: no desktop file changed; run through task\_checks after landing
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks after landing
- swarmai · bash scripts/sync-version.sh check: no version file changed; run through task\_checks after landing

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_approval\_prompt\_false\_positives\_r2.py
- swarmai: docs/design/2026-11-25-approval-prompt-false-positives-r2.md
