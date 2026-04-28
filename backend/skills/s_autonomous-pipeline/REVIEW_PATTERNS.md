# Runtime Pattern Checklist (RP1-RP19)

This file contains the canonical list of recurring production bug patterns that code review and unit tests consistently miss. The REVIEW stage references this file as a BLOCKING check -- every applicable pattern must be explicitly verified or marked N/A.

## Patterns

| # | Pattern | Trigger (when to check) | What to verify | Example bug |
|---|---------|------------------------|----------------|-------------|
| RP1 | **subprocess timeout orphan** | `create_subprocess_exec` or `subprocess.Popen` | `asyncio.wait_for` timeout path has `proc.kill()` + `await proc.wait()` BEFORE re-raise | ffmpeg runs forever after 30s timeout (run_c2881d2f) |
| RP2 | **subprocess missing binary** | `create_subprocess_exec("some_binary", ...)` | `FileNotFoundError` caught -- user-friendly error message | ffmpeg not installed -- raw 500 instead of "install ffmpeg" (run_c2881d2f) |
| RP3 | **React hook cleanup** | new `use*` hook with refs or subscriptions | `useEffect` return releases all resources (streams, timers, listeners) | Mic never released on tab close (run_c2881d2f) |
| RP4 | **stale closure** | callback passed to hook/async uses component state | use `useRef` to hold latest value, read `.current` in callback | transcribed text overwrites user's typing (run_c2881d2f) |
| RP5 | **FormData Content-Type** | `new FormData()` sent via axios/fetch | NO explicit `Content-Type` header -- browser must add boundary | Multipart broken, backend gets 400 (run_c2881d2f) |
| RP6 | **setTimeout leak** | `setTimeout` in component/hook | timer ID stored in ref, cleared in cleanup effect | setState on unmounted component (run_c2881d2f) |
| RP7 | **error msg / constant mismatch** | error messages containing numeric values | message matches the actual threshold/constant in code | "need 0.5s" but constant is 1.0s (run_c2881d2f) |
| RP8 | **hardcoded env assumption** | region, URL, port, path as string literal | configurable via env var or parameter with sensible default | us-east-1 hardcoded, user in us-west-2 (run_c2881d2f) |
| RP9 | **API boundary naming** | new endpoint returns JSON consumed by frontend | backend snake_case fields have camelCase frontend interface + conversion function | `duration_ms` passed raw to TS instead of `durationMs` (Kiro review) |
| RP10 | **barrel export** | new hook, component, or service file created | exported from the directory's `index.ts` | `useVoiceRecorder` missing from `hooks/index.ts` (Kiro review) |
| RP11 | **SDK handler reassignment** | `self._handler = new_handler` or similar | old handler `.close()` called BEFORE reassignment; reset to `None` before restart after crash | Old SocketModeHandler leaked on reconnect (Kiro review) |
| RP12 | **unstable callback refs** | inline arrow functions passed as props to hooks | wrap in `useCallback` with correct deps; use ref for values that change every render | `onTranscript`/`onError` recreated every render (Kiro review) |
| RP13 | **state machine completeness** | type/enum declares states + design doc shows transitions | every declared state has >=1 code path that enters it AND exits it; every transition has a trigger | Voice `interrupted` state declared but no code ever sets it; VAD never calls `stopRecording()` -- `listening->processing` transition unreachable (2026-04-25 E2E review) |
| RP14 | **cross-service parameter mismatch** | function A calls service B with parameters mapped from A's inputs | parameter names/semantics match B's API contract; override values don't conflict with defaults | `voice_id="Zhiyu"` + `language="en-US"` -- Polly rejects because Zhiyu requires `zh-CN` (2026-04-25 E2E review) |
| RP15 | **setTimeout for state propagation** | `setTimeout(() => fn(), 0)` to sequence React state + side effects | verify the ref/state the deferred fn reads is set BEFORE the timeout, not by a concurrent React render | Voice send `setTimeout(0)` reads `inputValueRef` which could be overwritten by React re-render before timeout fires (2026-04-25 E2E review) |
| RP16 | **concurrent async without ordering** | N async calls fired in a loop with `.then()` enqueue | responses may resolve out of order; use sequential `await` or `Promise.all` + indexed insert | 3 concurrent Polly calls -- sentence 2 (short) resolves before sentence 1 (long) -- audio plays out of order (2026-04-25 E2E review) |
| RP17 | **unsanitized string in structured format** | user/dynamic text embedded in HTML, JSON template, SQL, or shell | escape for target format: `html.escape()`, `json.dumps()[1:-1]`, parameterized query, `shlex.quote()` | `<pre>{message}</pre>` with raw message -- XSS in email; `payload_template.replace("{title}", title)` -- JSON parse failure on quotes (2026-04-25 E2E review) |
| RP18 | **timezone-sensitive date boundary** | SQL uses `date('now')`, `datetime('now')`, or `strftime(...,'now')` for filtering (e.g. "today's records") | write and query use the same timezone; desktop apps should use local time (`datetime.now()`), not SQLite UTC `date('now')`. Tests always pass because test runner and DB share a timezone -- the bug only appears when user is in a different TZ from UTC. | `WHERE date(timestamp) = date('now')` but timestamps written in UTC -- "today" boundary is 8h off for UTC+8 user, tokens after midnight local show as yesterday (run_6823b0d4 E2E review) |
| RP19 | **deprecated stdlib API** | `asyncio.get_event_loop()`, `datetime.utcnow()`, `logging.warn()`, `imp.reload()` | use Python 3.12+ recommended replacements: `get_running_loop()` / `get_event_loop_policy().get_event_loop()`, `datetime.now(UTC)`, `logging.warning()`. Deprecated APIs emit warnings and may raise `RuntimeError` in future Python versions. | `asyncio.get_event_loop().create_task()` in async generator -- DeprecationWarning in 3.12, potential RuntimeError in 3.13+ (run_6823b0d4 E2E review) |
| RP20 | **nested clickable event propagation** | `<button>` or `onClick` handler inside another element with `onClick` or `<li onClick>` wrapping action buttons | inner handler calls `e.stopPropagation()` -- or event WILL bubble to outer handler, firing both actions on one click | Todo ✅ button inside `<li onClick={onItemClick}>` -- clicking "mark done" also populated ChatInput with the todo (Briefing Hub v2 review) |
| RP21 | **popover toggle + click-outside race** | popover with `mousedown` click-outside handler + a toggle button that opens/closes it | toggle uses `onMouseDown` + `e.stopPropagation()` (fires before click-outside), or click-outside handler excludes the toggle button via ref. Using `onClick` for toggle + `mousedown` for click-outside = race: close fires first, toggle reopens. | History search popover flickers on re-click -- click-outside closes it, then toggle button `onClick` reopens it in the same event cycle (Briefing Hub v2 review) |
| RP22 | **React ref/state desync on programmatic input** | code calls `setState(value)` for a value that is ALSO read via `someRef.current` in a synchronous path (e.g., send handler reads `inputValueRef.current`) | ref is synced alongside state: `ref.current = value; setState(value)`. If only state is set, any synchronous consumer within the same microtask reads the stale ref. React state updates are async; refs are immediate. | `handleItemClick` called `setInputValue(text)` but not `inputValueRef.current = text` -- fast ⌘Enter within the same render frame sent the previous message (Briefing Hub v2 PE review) |
| RP23 | **conditional layout with empty children** | CSS grid/flex container with N columns/rows where children are conditionally rendered (`{hasX && <Card/>}`) | when one side renders 0 children, the container still allocates space (empty column in `grid-cols-2`, empty flex child). Either: collapse to single-column when one side is empty, or use `display: contents` wrapper. | 2-col Welcome Screen -- only Signals on right, left column empty -- 50% of screen is blank (Briefing Hub v2 review) |
| RP24 | **cross-language serialization format assumption** | Language A produces JSON/text consumed by Language B (e.g., Python FastAPI → Rust parser, Node → Python, Go → TS) | verify the **exact byte-level format** each side produces/expects. Python `json.dumps()` uses `": "` (space after colon); compact JSON has `":"`. Rust/Go manual string matching, regex, or `split(":")` will fail on the spacing variant. Test with the **actual serializer output**, not a hand-crafted string. | Rust `get_daemon_version()` searched for `"version":"` (no space), FastAPI returned `"version": "1.8.4"` (with space) — version always `None` — daemon restarted every launch (run_91a6fb7e E2E review) |

## Output Format

For each applicable pattern, one line:
```
RP1: pass -- proc.kill() in timeout handler + finally (voice_transcribe.py:92,112)
RP3: pass -- useEffect cleanup releases stream + recorder (useVoiceRecorder.ts:58-68)
RP5: N/A -- no FormData in this changeset
```

## Maintenance

The REFLECT stage is responsible for maintaining this file. When post-pipeline review (E2E, external, or user feedback) finds bugs the pipeline missed:

1. If the bug fits an existing RP pattern -- investigate why the checklist missed it and tighten the verification criteria above.
2. If the bug is a new pattern -- **append a new row** (RP24, RP25, etc.) to the table above. Include: trigger condition, what to verify, and the real bug as the example.
3. Update the pattern count in `backend/skills/s_autonomous-pipeline/stages/review.md` check #6 if the total changes.

This ensures the pipeline learns from every review cycle. Without this step, lessons live in IMPROVEMENT.md but never reach the checklist that would prevent recurrence.
