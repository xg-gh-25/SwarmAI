# 四条都同意，起 pipeline 做规则写入（设计 docs/design/2026-11-09-project-agent-contract.md r3 第… · Audit through DELIVER

Delivered: 四条都同意，起 pipeline 做规则写入（设计 docs/design/2026-11-09-project-agent-contract.md r3 第 1 步的规则写入和核实部分：rules 字段、project-agent rule add/remove 命令、写入时核实一次并把核实记录放在受 gate 保护的目录、只读出核实过的规则、\_merge 保留 rules、CDF 抽取提示和. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 45m 0s wall · 45m 0s active · 29.5% budget |
| Quality | WARN | 1/1 verification · 18/21 findings closed (7 test-closed, confirmed by review) · 1 HIGH finding open · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 818k comparable · 610k input · 208k output · 62.5M cache reads, not counted · 63.3M billed · host-session responses in the run window · 20.1% budget |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · What is the command called? → \`"$SWARM\_BACKEND\_BIN" py -m scripts.project\_agent\_rule add\|remove\|list --brain &lt;B&gt; \[--said &lt;quote&gt;\]\` instead of the design's \`project-agent rule add\`. Why: The run owner gate hands the session id to scripts it recognizes by module name; this keeps the gate change to one constant (DESIGN\_SPEC, agent)
- D2 · Where are the verification records kept? → state/pipeline\_agent\_audit/project\_agent\_rules/&lt;brain&gt;.jsonl, one JSON line per verification: key (sha256 of the CDF-normalized quote), event id, time; removing a rule does not delete its record (audit), the YAML row is what is gone. Why: Inside the directory the audit write gate already protects (design r3 says 'a gate-protected state directory'); no gate change (DESIGN\_SPEC, agent)
- D3 · Which normalization makes two quotes 'the same'? → CDF's own \`\_quote\_norm\` (NFKC, case-folded, without whitespace and quotation marks) for the verbatim check, the 6-character minimum, the record key and the STEERING de-duplication. Why: One normalization for every door (P8): CDF's user\_quote check and the rule check agree on what the person said (DESIGN\_SPEC, agent)
- D4 · Does the packager need a change for R7? → No code change: the packager ships only deliverables, skills, gates and understanding files and never project-agent.yaml (ddd\_packager.py:1293-1300, no reference to the file); a regression test packages a Brain with rules and asserts no packaged file contains the rule. Why: The property already holds; the test keeps it (DESIGN\_SPEC, agent)
- D5 · How does the agent learn to use the command during a session? → This run: the s\_project-manager skill's Project Agent section and the extractor's \_WHERE line; the contract block of the second run will carry the instruction to every session of the Brain. Why: Delivery to the three entries is the second run's scope (DESIGN\_SPEC, agent)
- D6 · Does project\_agent\_standard.SHARED\_ORDER need 'rules' (PLAN task T5)? → No: the change was removed. Why: ordered() keeps a key the standard does not name after the named ones, so rules already stays last after watches; with or without the entry every test is green (mutation check), so the entry was a layer that did nothing (BUILD report · swarmai, agent)
- D7 · Does writing a rule rewrite the definition in the standard order? → No: add/remove keep the file's key order and header comments and only append or drop the rules key. Why: R1.1 promises every other key unchanged; reordering or dropping retired keys is the draft's job, not the rule command's (BUILD report · swarmai, agent)
- D8 · Is a rule still a STEERING duplicate after it was removed from the Brain? → No: is\_brain\_rule\_quote needs both the record and the rule still in that Brain's definition. Why: The record is an append-only audit and outlives a removal; only a rule in force is a home for the words (BUILD report · swarmai, agent)
- D9 · Do the STEERING de-duplication and the extractor line (R6.1, R6.2) land in this run? → No: they move to the delivery run. Why: Review CF-4fbade85: until rules are delivered, a recorded rule reaches no session, and STEERING is the path that does; de-duplicating now would make a rule the person gave stop working until delivery lands (TEST report · swarmai, agent)
- D10 · What may removing a rule rest on? → A turn the person opened in the desktop chat whose message quotes the rule (6 normalized characters in a row, or all of a shorter rule). Why: Review CF-b7ba613d: a page the agent reads in the person's turn must not take a rule away; asking the person to name the rule in its words is a small cost (TEST report · swarmai, agent)
- D11 · When is a clause enough? → When the rest of its sentence holds no negation; otherwise only the whole sentence is accepted. Why: Review CF-358f5362: a clause after a comma can drop the sentence's negation and say the opposite (TEST report · swarmai, agent)
- D12 · How far do the checks go against an agent that tries to get around them? → Option A (XG 2026-11-09): whole sentences, removal needs the whole rule, the gate catches the obvious session overrides; a command built to hide its session, or a span chosen across a list heading, is the product-wide limit, listed as residual risk. Why: Two rounds of string checks against an injected agent kept finding new bypasses: the checks run in the agent's own process, so only a person's confirmation would close them, and XG chose not to add one (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A new state directory with its own gate (or a generalized audit gate over several directories). Not taken: The audit gate's path matching is long and finely tuned (redirects, interpreter code, xargs, cd, realpath; security\_hooks.py:5225-5400); widening it to a second root is a risky gate change for no gain, while a subdirectory of the protected root gets every rule for free.
- A3 · A \`project-agent\` subcommand of the backend binary (desktop\_main.main), like \`ingest\`. Not taken: The run owner gate already hands the session id to scripts named by module (scripts.swarm\_out, scripts.session\_worktree); a subcommand would need a new recognizer in the gate. The design doc's command name changes to \`py -m scripts.project\_agent\_rule\`; recorded as D1.
- A4 · Re-verify each rule against the user message whenever the definition is read. Not taken: Messages expire after 90 days and event ids are local to a machine, so rules would silently stop being delivered (design r3 §4.3).

Requirements analysis:

- missing\_edge (R1.1, R3.1): The user message the quote was checked against is deleted after 90 days and its event id is local to this machine; the check therefore happens once, at add, and readers look up the verification record instead of the message. → rewritten
- missing\_edge (R3.1): A Hive has the synced definition but not the host's verification records, so verified\_rules there returns none; delivering rules to a Hive through the dispatch is the second run's work (design §3.3). → confirmed
- missing\_edge (R1.2): A channel turn (Slack and the like) has no user message the command can read; the rule must be stated in the desktop chat. The command says so. → confirmed
- vague (R4.1): 'An agent cannot write' means the same guard level as the pipeline audit directory: the agent's tool calls are refused; another process of the same user is not stopped (the existing gate's documented limit). → rewritten
- missing\_edge (R1.1): A quote pasted by the person from somewhere else is in their message and counts as their words; the guard is against text the agent read, not text the person sent. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/26 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 18fec099dc09: 6 pass, 0 fail, 0 not run
- test · swarmai @ caaf9b3fbc1e: 5 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 280; exec @ARGV' /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest -q -p no:cacheprovider --maxfail=1000 tests/test\_project\_agent\_rules.py tests/test\_pro → pass
- perl -e 'alarm 150; exec @ARGV' npx vitest run src/components/layout/ProjectAgentView.test.tsx → pass (run on this change's desktop files, which did not change since)
- perl -e 'alarm 280; exec @ARGV' npx tsc --noEmit → pass (run on this change's desktop files, which did not change since)
- perl -e 'alarm 100; exec @ARGV' /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_skills.py → pass
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m scripts.mutation\_check /tmp/as\_research/mut11.json --timeout 120 -- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.v → pass (3 mutations for the last fixes (CJK and quote sentence ends, abbreviations in any case, the .pyc form of the rule command): 3 caught)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/project\_agent\_rules.py
- swarmai: backend/scripts/project\_agent\_rule.py
- swarmai: backend/tests/test\_project\_agent\_rules.py
- swarmai: desktop/src/components/layout/ProjectAgentView.test.tsx
