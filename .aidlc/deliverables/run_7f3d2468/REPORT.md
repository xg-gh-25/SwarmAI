# XG: 起Pipeline 把整个做完吧 (Project Agent contract, design docs/design/2026-11-09-pro… · Audit through DELIVER

Delivered: XG: 起Pipeline 把整个做完吧 (Project Agent contract, design docs/design/2026-11-09-project-agent-contract.md step 2, narrowed as agreed 2026-11-10). (1) pipeline-v1 create --agent-mode &lt;mode&gt;, default the Br. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 43m 5s wall · 43m 5s active · 28.2% budget |
| Quality | WARN | 1/1 verification · 3/3 findings closed (2 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 440k comparable · 345k input · 95k output · 29.1M cache reads, not counted · 29.6M billed · host-session responses in the run window · 10.8% budget |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 1 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · Where does the mode live in the contract? → contract\['project\_agent'\] gains 'mode' and 'checks': \[{'id','command'}\] (no setup); the rules and watches keys stay as they are. Why: One record of what the run was created under; an older run has none and every reader treats a missing key as no mode (DESIGN\_SPEC, agent)
- D2 · What does create do with --agent-mode? → Given: the definition must exist and have that mode, else create is refused before the run exists, naming the modes. Not given: the definition's default\_mode when there is a readable definition, else nothing. Why: A wrong mode is a typo the agent fixes at once; a Brain without a definition still runs pipelines as today (DESIGN\_SPEC, agent)
- D3 · How does a report say which check a command runs? → An optional 'validation' field on a command row (an id of at most 40 characters); the event payload gets 'checks': {id: result} for ids of the run's mode; an unknown id is not recorded and stage-complete's response lists it under 'warnings'. Why: Never refuses a report for it (the checks are reported, not enforced); the event store's payload schema lists 'checks' as optional (DESIGN\_SPEC, agent)
- D4 · What does the REPORT show? → Under the Tests lines: 'Brain checks (&lt;mode&gt;):' then one line per check, 'id: result' from the latest TEST report of any repo that named it, else 'not run'; only when the profile has a TEST stage and checks were recorded. Why: Shown in the existing section, not a new one; a research run never had a TEST to run them in (DESIGN\_SPEC, agent)
- D5 · What does a Hive's pinned run record? → create\_pinned\_run passes the pin's mode as agent\_mode. Why: The pin's revision equals the Hive's definition (make\_pin compares them), so its checks are the dispatched ones (DESIGN\_SPEC, agent)
- D6 · Does every pipeline run of a Brain with a definition now record a mode? → Yes: without --agent-mode the definition's default mode is recorded, so its checks are listed in TEST and in the REPORT. Why: The requirement's default; a run that does other work names its mode, and listing never blocks (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Match a reported command to a check by equal command text, as the design first said. Not taken: Commands are reported wrapped in a wall-clock cap and with the venv's interpreter (AGENT § Tests), so equal text almost never happens and every check would read not run; which row is which check is an identity, decided by an id its producer assigns, not by content (AGENT R3 (8)).
- A3 · Read the mode's checks live from the definition at TEST and REPORT time. Not taken: A definition changed during the run would change what the run is reported against, and a Hive's REPORT would read a file that may have been re-synced; the rules and watches are already fixed at create for the same reason.

Requirements analysis:

- vague (R2.2, R3.1): The design matched a reported command to a check by equal command text. Commands are run wrapped in a wall-clock cap (perl alarm) and with the venv's interpreter, so equal text almost never happens and every check would read not run. Which check a command runs is now said by the agent with a validation id on the row (an id the producer assigns, not a content match). → rewritten
- missing\_edge (R1.1): The checks are recorded at create, not read at TEST or REPORT time: a definition changed during the run does not change what the run is reported against, like its rules and watches. → confirmed
- missing\_edge (R3.1): A research run has no TEST stage, so it lists no checks (none could have run); its recorded mode is still in the contract. → rewritten
- missing\_edge (R2.1): SwarmAI's code-change mode has 11 checks; the listing says to run the ones the change touches, matching the chat contract's wording; not running the rest is shown, never refused. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 13/26 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r1: read 100% of its prompt · 1/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 602286a39cdb: 5 pass, 0 fail, 0 not run
- test · swarmai @ 45366c3ab966: 3 pass, 0 fail, 0 not run

TEST · swarmai:
- perl -e 'alarm 590; exec @ARGV' /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest -q -p no:cacheprovider --maxfail=1000 tests/test\_pipeline\_agent\_mode.py tests/test\_pro → pass (314 passed)
- perl -e 'alarm 590; exec @ARGV' /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest -q -p no:cacheprovider --maxfail=1000 tests/test\_pipeline\_v1\_not\_run\_report.py tests/t → pass (24 passed)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m scripts.mutation\_check /tmp/as\_research/mut20.json --timeout 200 -- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.v → pass (1 of 1 caught: the engine passing the run's mode to the warning (review finding CF-19d66c17, test added in TEST))

Tests were not run again after main moved: tests carried over a main move: swarmai (main 75c4954a858b-&gt;8ba89d3ddf36): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_pipeline\_agent\_mode.py
