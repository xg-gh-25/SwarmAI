# 起 pipeline：规则送到三个入口（设计 docs/design/2026-11-09-project-agent-contract.md r3 §3、§… · Audit through DELIVER

Delivered: 起 pipeline：规则送到三个入口（设计 docs/design/2026-11-09-project-agent-contract.md r3 §3、§4.3：本机聊天进入这个 Brain 时送 Project Agent 约定块（含已核实的规则、watches、各 mode 的 when/检查/skills，不列 setup，检查写成按改动挑；pin 和 worker 不送；压缩后重送；绑. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 31m 0s wall · 31m 0s active · 20.3% budget |
| Quality | WARN | 1/1 verification · 4/5 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 583k comparable · 478k input · 105k output · 33.9M cache reads, not counted · 34.5M billed · host-session responses in the run window · 14.3% budget |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 0 · searched the DDD 0×

## Decisions

- D1 · Where does the chat block go relative to the DDD block? → Delivered by \_maybe\_inject\_ddd right after the DDD leg, through the same \_deliver\_block, as its own block. Why: Every branch of \_inject\_ddd\_for\_active\_project (hits, pointer, bound repo without hits) then gets it without editing each branch (DESIGN\_SPEC, agent)
- D2 · How is the block re-delivered after compaction? → The block is kept in the hook session context (\_project\_agent\_block) and create\_post\_compact\_injection appends it to its continuity note. Why: That hook already fires once on the first prompt after a compaction; no new hook (DESIGN\_SPEC, agent)
- D3 · What does the contract record for experts? → contract\['project\_agent'\] = {'rules': \[...\], 'watches': \[{'area', 'why'}\]}, only when there is at least one; rules are the verified rules locally, the pin's rules on a Hive. Why: Protected and fixed at create; an older run without the key gets no section (DESIGN\_SPEC, agent)
- D4 · How are the pin's rules bounded? → make\_pin keeps rules only when they are a list of at most 50 non-empty strings of at most 2000 characters each; anything else is dropped. Why: The value comes over the network from the host; an invalid value must never block the task (DESIGN\_SPEC, agent)
- D5 · How does the agent learn to add a rule in-session? → The chat block ends with one line naming the scripts.project\_agent\_rule add command and that only the person's own whole sentences are accepted. Why: Every session of the Brain then knows the command, not only one that loaded the s\_project-manager skill (DESIGN\_SPEC, agent)
- D6 · Is a Hive pin's rules list the only route for rules into a Hive run? → Yes: create\_pinned\_run passes pin\['rules'\] (or \[\]), so a Hive never reads its synced YAML's rules. Why: A Hive has no verification records; brain\_rules=None would fall back to its own (empty) verified rules, \[\] says the same explicitly (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Read the rules live wherever they are needed (the expert handout reads the Brain's definition at review time; a Hive reads its synced YAML). Not taken: A Hive has no verification records, so its rules would always be empty; and a run reviewed against rules that changed under it is not reviewed against the contract it was created with (the same reason review.md is read at the run's base).
- A3 · Deliver the chat block through repo\_guidance on first touch of a bound repo. Not taken: repo\_guidance caps a block at 7,500 characters and skips an extra that does not fit; the DDD leg already retries every message and resolves the bound repo (design r3 §3.1).

Requirements analysis:

- missing\_edge (R1.1): The DDD leg runs once per session after a project is found; a subprocess recycle resets it, so the block is delivered again then, which is wanted (the new process has no system prompt of the old one). → confirmed
- missing\_edge (R2.1, R3.2): A Hive has the synced definition but no verification records, so its own verified\_rules is empty; rules reach a Hive run only through the host's dispatch (pin), never from the synced YAML. → confirmed
- missing\_edge (R3.2): An older Hive ignores the extra key in the pin request; a newer Hive with an older host gets no rules. Both degrade to today's behaviour. → confirmed
- vague (R2.2): Recorded at create, not read at review time: the rules a run is reviewed against are the ones it was created under, like its review guidance read at the run's base. → rewritten
- missing\_edge (R1.1): Out of this run (design step 2): pipeline --agent-mode, the contract section in knowledge.md and TEST listing the mode's validations. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/26 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ f65c5e8fd73d: 3 pass, 0 fail, 0 not run
- test · swarmai @ b8033327ab32: 2 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 580; exec @ARGV' /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest -q -p no:cacheprovider --maxfail=1000 tests/test\_project\_agent\_delivery.py tests/test\_ → pass (778 passed: the change's tests and the tests of every changed function and its direct callers)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m scripts.mutation\_check /tmp/as\_research/mut16.json --timeout 150 -- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.v → pass (1 of 1 caught: refusing the 51st rule before its record is written (review finding CF-ea3bc9d9, fixed in TEST))

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_project\_agent\_delivery.py
