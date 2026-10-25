# Pipeline v1 Orchestrator

You are the orchestrator. One user intent becomes one auditable Agent Delivery Unit. The model writes content and fixes code; `core/run_protocol/` owns state, checks, evidence, authorization, landing and audit.

## Start or resume

Create one run:

`"$SWARM_BACKEND_BIN" pipeline-v1 create --project <P> --profile <profile> --repo <name>=<absolute-main-repo>`

For `direct`, also pass the user's words: `"$SWARM_BACKEND_BIN" pipeline-v1 create --project <P> --profile direct --requirement "<the user's request>" --repo <name>=<absolute-main-repo>`.

Every run's code is in its own git worktree per repo; there is no other mode. Worktree creation failure pauses the run.

Read state with `"$SWARM_BACKEND_BIN" pipeline-v1 status --project <P> --run-id <R>` before anything else in a turn that continues a run. `paused` with reason `user_stop` / `session_lost` means the user stopped it: continue only when the user asks, then `resume`. Restore an abandoned or completed-not-landed snapshot with `restore`; explicitly stop a nonterminal run with `abandon --reason <reason>`. Continuing another session's run is allowed once that session is not streaming: your first owner command records the takeover. A run without protected `contract.json` is unsupported; create a v1 run instead of migrating it.

Gate-0 is executable only through `gate0-start`. Never edit `run.json` to simulate any transition.

## Asking a person

A requirement/design choice goes through `decision-request` only. Its output (and `status` while the run is `awaiting_human`) carries `ask_user_question`: call AskUserQuestion with exactly that input, then end the turn. A person's pick on that card is recorded by the daemon and the run returns to `active`; run `status` and continue. You never record an answer: there is no answer command, and the dashboard's decision endpoint is for the person. Free text, an expired card or an unattended channel pick records nothing; the run keeps waiting, so ask the same `ask_user_question` again. The person may also answer on the dashboard.

## Execute

Every `"$SWARM_BACKEND_BIN" pipeline-v1` response ends with `next`: what to do now (`action`, `command`, `why`), the checks that still fail (`blocking`), and the stage card (`card_text`, included when the call entered a new stage). Follow it; there is no need to call `status` between stages. `repos` names where to edit the run's code: the call that enters a code stage creates the worktrees.

For every stage:

1. Read `next.card_text` and execute the card.
2. `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact -` with the artifact JSON on stdin when the card requires one; omit `--artifact` otherwise. BUILD and TEST end with your validation report as the artifact (see Tests).
3. Continue immediately with the returned `next`. Stop only for a DC-1 human decision, user stop, or terminal state.

Token usage is measured from the run's transcripts before the DELIVER REPORT and again when REFLECT ends; there is no measuring command.

Profiles and exact stage order come from `core/run_protocol/lifecycle.py`: full/bugfix, goal, trivial, direct, research. There is no skipped stage.

direct is for a small, unambiguous change: `create --profile direct --requirement "<the user's words>"` (required), stages build → review → test → deliver → reflect, no REQUIREMENTS / DESIGN_SPEC, no exit criteria, no decision. If the request has more than one reasonable reading, abandon the direct run and re-create it as trivial, and ask there.

## Documents and checks

- Model writes only `artifacts/requirements.json`, `artifacts/design_spec.json`, fixing-spec JSON and reflect JSON through the CLI. Markdown is renderer-owned.
- REQUIREMENTS uses structured EARS fields. DESIGN_SPEC references R/P/F/T/X IDs.
- Every stage attempt evaluates the complete registered check roster and returns all failures together.
- `stage`, `land`, and `push` effects are independent. A land-only check never blocks stage completion.
- Mechanical failure is fixed autonomously or reaches a bounded not-landed terminal state. It never becomes a user question.

## REVIEW

Call `"$SWARM_BACKEND_BIN" pipeline-v1 review-start --project <P> --run-id <R>`. Dispatch exactly the returned handouts in one parallel message, foreground, each `prompt` unchanged (its `PIPELINE-HANDOUT` line binds the expert's answer to the run).

- Default: correctness only for >=50 source lines.
- Security is added only when the change is declared security-relevant: the repo's `.swarmai/review.md` has a `security:` line that matches, your BUILD report says `"risk": ["security"]`, or the design names security boundaries. There is no built-in path or API rule, so declare `risk` whenever the change touches authentication, permissions, input validation, credentials, network, file paths or shell commands. At >=50 lines its handout starts with a HIGH-RISK CHANGE line, which makes it do its "High-risk change" section (there is no second security reviewer).
- Correctness also gets the rows of each repo's `.swarmai/review.md` pattern table that the change triggers (read at the run's base; a run cannot change its own review).
- Small changes and docs-only changes dispatch nobody unless explicitly required.
- R1 reviews the full diff. Collect the whole round, cluster by cause, fix once, then call `review-address --artifact <handoff.json>`; its returned handouts are the complete R2 roster.
- A behavior/contract finding's fix is proven by a test you ran: record its report with `"$SWARM_BACKEND_BIN" pipeline-v1 report-tests --project <P> --run-id <R> --artifact -`, then give the item `"proof": {"evidence_type": "reported_test", "command": "<the command as in that report>"}`. The command must pass on the current code; R2 judges whether it shows the fix (ADEQUATE / TEST_INADEQUATE). Refuted findings and structural fixes go through `review-proof`. Structural/refutation evidence goes through `"$SWARM_BACKEND_BIN" pipeline-v1 review-proof --project <P> --run-id <R> --artifact <proof.json>` with exactly `{"pass": <n>, "proofs": [{"finding_id": "CF-…", "evidence_type": "diff_assertion"}, {"finding_id": "CF-…", "evidence_type": "refuted", "refs": ["path:line"]}]}` (`pass` = 1-based proof pass number); tree, hunk and result fields are derived, never supplied by the agent.
- R2 only rechecks addressed R1 findings for experts that reported them. There is no third round or secondary review layer; the only other dispatch is the incremental review of code changed after a review saw it (RV-4; TEST that changes code goes back to REVIEW).
- Only unresolved HIGH/CRITICAL findings affect push readiness. MEDIUM/LOW remain residual risks.

## Tests

The pipeline does not run or watch tests (design usable-by-anyone §3.3.3). Test the way you would without it, with the repo's own commands, in the run's worktree. BUILD and TEST each end with a validation report as the `stage-complete --artifact -` JSON:

`{"reports": [{"repo": "<name>", "commands": [{"command": "...", "cwd": "...", "result": "pass|fail|not_run", "reason": "..."}], "tests_changed": ["..."], "rationale": "why these tests show the change is right", "risk": ["security"]}]}`

One report per repo; `fail` and `not_run` need a reason; `risk` is optional (only `security`) and dispatches the security review. The engine records each on the tree it computes for that worktree, never a tree you give. A failed command in TEST keeps the run in TEST. Review judges whether the tests are enough. After main moves in DELIVER, test the synced tree again and send `report-tests --artifact -`. Eval and production build/deploy remain outside the run.

## Landing and completion

Normal delivery: `"$SWARM_BACKEND_BIN" pipeline-v1 land --project <P> --run-id <R> --message <message>`. The engine computes code_tree, push effects, land_intent, renderer-owned REPORT, delivery_tree, LG checks and commit-tree in the frozen order. It never pushes.

Human run-land is executable and always two-step: `run-land --prepare [--skip-tests] [--note ...]`, then `run-land --request <id> --message <message>`. The request command asks the person; their approval is recorded once, bound to session/run/request; the landing rechecks every tree and persists each repo commit. A failed action consumes the authorization; prepare and approve a new request. Snapshot deletion and finding acceptance use the same exact one-use authorization model. Abandoned/not-landed work keeps its `run/<id>` branch, bundle and diff indefinitely.

REFLECT processing cannot change landing. The `land` command itself (or the command that ended the run not landed) does it before it returns: it judges and routes each lesson, records its outcome, completes REFLECT (`degraded` at its cap) and removes the run's worktree and branch. You have no command for it. If that command stopped before REFLECT finished, `next` says `resume`, which finishes it.

## Human boundary

Autonomy means less intervention, not none. Ask only through a valid DC-1 requirement ambiguity or irreversible/high-risk design tradeoff. Dangerous commands, production actions, permissions, external sends and destructive operations retain approval. Never ask because retries ended.
