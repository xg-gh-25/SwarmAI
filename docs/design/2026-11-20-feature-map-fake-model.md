---
title: Feature Map — a scripted model for walking the chat path
date: 2026-11-20
created: 2026-11-20
updated: 2026-11-20
project: SwarmAI
status: r2 — after one premise review; not built. Owner direction 2026-11-20: "设计沙箱里的假模型端点".
related: docs/design/2026-11-18-feature-map.md; core/feature_map; core/claude_environment; core/bedrock_thinking_gateway; core/session_unit (pre-flight); .swarmai/features
---

# Feature Map: a scripted model for walking the chat path

Extends `2026-11-18-feature-map.md` (v3). Premise review: §9.

## 1. Why

SwarmAI's first Feature Map (`.swarmai/features/`, 10 features) covers navigation, panels,
tabs, the first-run wizard and the account banner. It cannot walk the path users spend
their time on and where regressions recur: send a message, watch it stream, see a tool
card, stop a reply, keep two tabs apart while both stream. Those need a model, and the
sandbox has none (no account, and a walk must never spend money or depend on a live
service).

Goal: a stand-in for the model that the replayed app reaches through its own, unmodified
code path, answering with replies a walk scripts. Non-goals: judging model quality,
testing prompts, the Tauri shell, real Bedrock behaviour (quotas, latency).

## 2. What the code already allows (read 2026-11-20)

- The bundled claude CLI always runs on Bedrock (`core/claude_environment.py:206`) and
  reads `ANTHROPIC_BEDROCK_BASE_URL` (the thinking gateway already redirects it:
  `core/bedrock_thinking_gateway.py:80-103`).
- In Bedrock API-key mode the CLI authenticates with `AWS_BEARER_TOKEN_BEDROCK`
  (`core/claude_environment.py:242-249`), so no SigV4 and no real credentials are needed.
  The key is set with `POST /api/system/bedrock-api-key` and `POST /api/system/auth-method`
  (`routers/system.py:403-450`); neither calls the network.
- In that mode the spawn pre-flight skips its STS check (`core/session_unit.py:3296-3303`),
  the thinking gateway steps aside (`bedrock_thinking_gateway.py:97`), and health reports
  the credentials valid without a network call (`core/readiness_sampler.py:98`), so the
  no-account banner is not shown.
- The SDK starts the CLI with the backend's whole environment plus the session's own
  (`claude_agent_sdk/_internal/transport/subprocess_cli.py:819-824`): a variable set on the
  backend service reaches every CLI it spawns.
- The backend's own direct model calls go through one `converse` call site pair
  (`jobs/bedrock.py:462,478`, inside `converse_with_retry`), reached through `structured()` /
  `text()` from about 15 callers (summaries, session scoring, CDF, signal digests); plus
  `invoke_model` in the Settings verify (`routers/system.py:540`). With botocore 1.42.59 a
  bearer-token client honours `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` (checked: a local server
  received `/model/<id>/converse` with `Authorization: Bearer`). Pointing it at the
  stand-in also keeps those background calls from reaching real AWS with the fake key.
- Start states share one sandbox and one set of services: `replay._web` runs them in name
  order after `default` (`replay.py:182`) and `Sandbox.setup` only runs the state's setup
  (`sandbox.py:337`). A state that changes the app's settings changes them for every state
  after it (§4.2a).

## 3. Evidence (spike, 2026-11-20)

A 90-line stdlib server answering Bedrock `invoke-with-response-stream` (AWS event-stream
framing: prelude, headers, CRC32s; payload `{"bytes": base64(anthropic stream event)}`), and
the bundled CLI started with a temp HOME, `AWS_BEARER_TOKEN_BEDROCK=fm-fake-token` and
`ANTHROPIC_BEDROCK_BASE_URL` pointing at it:

- `claude -p "say hi"` printed the scripted text and ended `result: success`.
- Before the main request the CLI sends three small non-streaming `/invoke` probes (one
  each for the sonnet, opus and haiku ids); the main request is
  `/model/us.anthropic.claude-opus-5-5/invoke-with-response-stream` with `tools`,
  `thinking` and a final message whose role is `system`.
- A tool round trip worked with no server state: the first reply was a `tool_use`
  (Bash `echo feature-map-marker`), the CLI ran it and sent the `tool_result` back, the
  second reply quoted it, and the session ended `success`.

## 4. Design

### 4.1 Where it lives

The model stand-in is a fixture of the SwarmAI repo, not a product capability:

```
.swarmai/features/
  fixtures/model/server.py       # stdlib only: the event-stream encoder, the routes, the script reader
  fixtures/model/scripts/*.yaml  # one file per scripted conversation
  targets/app.yaml               # gains the `model` service and the `chat` start state
```

| Option | What | Effort | Risk | Why not / why |
|---|---|---|---|---|
| **A. Repo fixture (chosen)** | the server above, a service of the `app` target | ~1 day | Bedrock's wire format or the CLI's request shape changes: the walks go red, visibly | Only SwarmAI needs it today; P7 puts a need only this repo has in this repo. Promote to B when a second repo asks |
| B. Feature Map ships a `model` stub kind | the same server inside `core/feature_map`, declared by any target | ~2 days | one vendor's protocol becomes product surface we maintain for everyone | No second consumer yet; Feature Map's rule is to rent execution, not grow it |
| C. A third-party mock (AWS Emulator, LLM mock servers) | point the endpoint at it | unknown | Bedrock streaming support unverified; the emulator needs a Finch VM per replay; none scripts replies per walk | Not evaluated further |

### 4.2 Wiring (target `app`)

- New service `model`: `[uv, run, --directory, "{checkout}/backend", --frozen, --no-sync,
  python, "{checkout}/.swarmai/features/fixtures/model/server.py", --port, "{port.model}",
  --scripts, "{checkout}/.swarmai/features/fixtures/model/scripts", --log,
  "{sandbox}/logs/model.jsonl"]`, ready on `GET /ping`. Paths go through `{checkout}`
  because a service runs in the target root (`desktop/`); the interpreter is the backend's
  own (a bare `python3` may be the Xcode stub on macOS).
- Target env (services have no env of their own, `files.py:195`; target env reaches every
  service and prepare step, which is harmless here), added:
  `ANTHROPIC_BEDROCK_BASE_URL`, `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` and `AWS_ENDPOINT_URL_BEDROCK`
  all `http://127.0.0.1:{port.model}` (the last turns any CLI control-plane call, such as
  `ListInferenceProfiles`, into a logged 404 instead of a real AWS call);
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`;
  `NO_PROXY` / `no_proxy` = `127.0.0.1,localhost` (the sandbox passes a person's proxy
  variables through, and loopback model traffic must not go to it).
- New start state `chat`: onboarding complete, then
  `POST /api/system/bedrock-api-key {"bearer_token": "fm-fake-token"}` and
  `POST /api/system/auth-method {"method": "bedrock_api_key"}` (neither calls the network:
  `rederive` returns at once for this method, `aws_account.py:935,951-953`). Health samples
  every 10 s (`readiness_sampler.py:37`), so a walk waits for the banner to go rather than
  asserting it absent at once.

Nothing in the app changes. It reaches the stand-in through the same variables a real
user's API-key setup uses; the only difference is the host.

### 4.2a Start states are isolated (Feature Map engine change)

Today a start state's setup changes the app for every state after it: `chat` (API-key mode)
sorts before `home`, so the no-account banner walk would see a connected account and go
red. The design v3 meant each walk to start from its state, not from whatever ran before.

Change, in `core/feature_map/replay.py` and `sandbox.py`: before each start state after the
first, the engine stops the target's services, starts them again, and gives the state a
fresh folder through a new placeholder `{state_dir}` (`<sandbox>/state/<state>`), then runs
the state's setup. A target that keeps its data under `{state_dir}` (SwarmAI:
`SWARM_DATA_DIR={state_dir}/data`) gets a clean app per state; prepare runs once. Cost: one
service restart per extra state (SwarmAI's backend: about 10 s).

| Option | Why not / why |
|---|---|
| **Engine restarts per state with `{state_dir}` (chosen)** | Every user's states would otherwise leak into each other; the fix belongs where the leak is |
| A second target `chat` with its own backend and data folder | Works today without an engine change, but needs a second UI build pointed at the second backend, and leaves the leak for every other repo |
| A reset step in each state's setup | Depends on the app having a full reset; SwarmAI's `auth-method ada` runs `rederive` (possibly ada) and keeps the stored secret |

### 4.3 The server

Routes (any other path: 404, logged):

- `POST /model/{id}/invoke-with-response-stream`: the scripted reply as an event stream.
- `POST /model/{id}/invoke`: a short fixed text reply (the CLI's probes, the Settings verify).
- `POST /model/{id}/converse`: a short fixed text reply in Converse shape (the backend's own
  calls). Most of their callers use `structured()`, which forces a tool and raises on plain
  text (`core/summarization.py:782`); those background features log a warning per call, as
  they do today without a model. No walk depends on them; answering arbitrary schemas is
  out of scope.
- `GET /ping`.

Every reply carries a fresh `message.id` and `tool_use` id. The authorization header is
not checked: the server binds 127.0.0.1 only, never forwards
anywhere, and the token is fake. Every request is appended to `{sandbox}/logs/model.jsonl`
(path, model id, script chosen, step, byte count; never the body), so a failed walk shows
what the app sent.

### 4.4 Scripts

A walk picks its conversation with a marker in the message it types, `[fm:<name>]`. For a
request that carries `tools` (the CLI's main loop), the server:

1. looks at user messages that carry no `tool_result` (a tool's output may print a marker:
   an `echo`, a `cat` of a spec), and takes the last one with a marker; the CLI's final
   message is a `system` one, so "the last message" is never the right one;
2. takes the **last** marker in it: SwarmAI prepends session, recall and UI-state context to
   the typed text, and recall can bring back an earlier walk's message with its marker, but
   the typed text always comes last;
3. loads `scripts/<name>.yaml` and answers with the step whose index is the number of
   assistant turns after that message;
4. answers the default text with `end_turn` when the index is past the last step, so a
   script can never loop.

Requests without `tools` (the CLI's probes, a compaction or summary that resends history)
and requests with no marker get the default text. No state is kept on the server, so
retries, two viewports and two tabs streaming at once cannot confuse it.

```yaml
# scripts/tool-bash.yaml
steps:
  - tool_use: {name: Bash, input: {command: "echo feature-map-marker", description: "Print a marker"}}
  - text: "The command printed: {last_tool_result}"
```

Step kinds, each with optional `chunk` (characters per delta, default 8) and `delay_ms`
(between deltas, default 0):

- `text`: one text block; `{last_tool_result}` is replaced by the latest tool result.
- `thinking` + `text`: a thinking block (with a placeholder signature) before the text.
- `tool_use`: one tool call; the CLI runs it in the sandbox like any real call.
- `slow_text`: a long text with a delay, long enough to click Stop while it streams.
- `error`: an event-stream exception or an HTTP status. A walk of the error card uses a
  non-retriable one (`validationException`, HTTP 400): throttling is retried by the backend
  three times with a 5 s backoff behind a "reconnecting" state (`session_utils.py:485`,
  `session_unit.py:2167-2168`) and by the CLI itself, and a stateless server throttles every
  attempt.

### 4.5 First walks (start state `chat`)

1. **Send and stream**: type `hello [fm:hello]`, send; the reply streams into the chat, the
   input is free again, the reply stays after a reload.
2. **Tool card**: `[fm:tool-bash]`; a Bash card appears with its command and output, then
   the final text that quotes it.
3. **Two tabs stream apart**: start `[fm:slow-a]` in tab 1, switch, send `[fm:slow-b]` in tab
   2; each tab shows only its own reply, in both orders of finishing (the MessageStore
   isolation invariant).
4. **Stop**: `[fm:slow]`, click Stop while it streams; streaming ends, the partial reply
   stays, a new message can be sent.
5. **Model error**: `[fm:invalid]` (a `validationException`); the chat shows the error, no
   endless spinner, and a new message can be sent.

Each asserts what a user sees (text, cards, buttons), never internal state.

## 5. What it does not cover

Real model output and its edge cases; Bedrock throttling timing; the SigV4 path (ada/SSO)
and the thinking gateway (the stand-in is reached in API-key mode, where both step aside);
anything the CLI does differently with a real model id (the spike shows it accepts the
stand-in's replies for the ids it probes).

## 6. Risks

- **The CLI's request shape changes** (new probe, new route): unknown routes 404 and are
  logged; the walks go red with the route in `model.jsonl`. That is the intended signal.
- **Thinking signatures**: a later turn sends earlier thinking blocks back; the CLI may
  check their signature. Unverified: the `thinking` step is built last and dropped if the
  CLI refuses placeholder signatures.
- **Recall bringing back markers**: handled by taking the last marker (§4.4); the table
  test covers it.
- **Tools run for real** in the sandbox. Scripts are repo content, the same trust as the
  repo's tests (`2026-11-18-feature-map.md` §6.3).

## 7. Build order

1. Engine: start states isolated (§4.2a), with its tests; the existing 10 SwarmAI features
   verified green again.
2. `server.py`: encoder, routes, script reader; unit tests (below).
3. Target wiring and the `chat` state; walk 1 verified green.
4. Walks 2-5, each verified green in both viewports.
5. `thinking` step, if the signature question allows it.

## 8. Verification plan

- **Encoder**: frames decoded by botocore's own `EventStreamBuffer` round-trip to the
  payloads; a broken CRC is refused by it. (Answers: is the wire format right without the
  CLI.)
- **Script selection**: a table test over request histories gives the expected step: no
  marker; a marker in an older message; after one and two tool results; a trailing
  `system` message; a marker inside a `tool_result`; two markers in one message (recall
  context first); a step index past the end; a request without `tools`. (Answers: is the
  stateless rule right, and can it loop.)
- **State isolation**: an engine test where state `a` writes a file under `{state_dir}` and
  changes a service's in-memory value, and a walk in state `b` sees neither; plus the
  existing banner walk staying green with the `chat` state present. (Answers: do states
  still leak.)
- **End to end**: `feature_map verify` of the five walks, green in both viewports.
- **Each walk proves something**: one mutation per walk, applied to product code and
  confirmed red, e.g. drop the tab id from a stream event's routing (walk 3), stop
  rendering the tool card (walk 2). (Answers: does the walk catch the regression it names.)
- **Review**: one correctness review of `server.py` and the walks before commit.

## 9. Review

One zero-context premise review (2026-11-20). Verdict: the premise holds; nothing in the
backend strips or overrides the three variables on the chat CLI's path in API-key mode, and
no frontend or backend check blocks a send. Its findings, all verified and folded in:
start states leaking into each other (§4.2a); throttling being retried behind a
reconnecting state (walk 5 now uses a non-retriable error); the step rule able to loop or
pick a recalled marker (§4.4); service argv and env not matching the target schema, proxy
variables, the control-plane endpoint, unique ids, the interpreter (§4.2, §4.3); the wrong
"4 sites" count (§2).

Found in passing, outside this design: the Settings verify of a Bedrock API key fails when
the key is not already in the process environment, because botocore reads the token when
the request is signed, not when the client is built (`routers/system.py:466-475,516-534`;
reproduced with botocore 1.42.59). Fixed as its own change.
