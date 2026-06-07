
### 2026-06-07: Streaming P0 Cluster — 5 Bugs, 1 Root Cause Pattern

**What Worked:**
- **Structural reconciliation > string matching** — `_confirmed` marker + replace-not-dedup eliminated text duplication by construction. No amount of `endsWith`/`startsWith` heuristics can cover all edge cases (whitespace, prefix, re-emission). Making the wrong thing structurally impossible is the only robust fix.
- **Adversarial review caught 2 HIGH bugs** the author missed — `appendTextDelta` corrupting confirmed blocks, and same-turn re-emission dedup needing tool-boundary guard. Fresh context beats self-review every time.
- **Backend liveness events (thinking_progress)** — simple 15-line change to `sse_with_heartbeat` that eliminates both the stall warning AND gives users elapsed time feedback.

**What Failed:**
- **Streaming is highest-risk, lowest-coverage surface** — 700+ backend tests, 0 integration tests that simulate real SDK event sequences (20 text_delta + 5 tool_use + 3 assistant events in realistic order and timing). All streaming tests are unit-level against `updateMessages` in isolation.
- **Dev environment ≠ production regime** — Dev uses short conversations (<100K context). Production uses 1.4M context + 40+ tools/turn + 12min extended thinking. Timing, batching, content redaction all behave differently at scale. Every streaming P0 was invisible in dev testing.
- **"Clever" over "Structural" design** — `endsWith` dedup was elegant (5 lines) but assumed byte-identical text. One trailing `\n` difference → P0. Pattern: any logic that relies on "detecting" or "matching" instead of "preventing" is a time bomb.
- **Stall detection had no concept of thinking phase** — 60s threshold designed for text generation, applied universally. Extended thinking (redacted content, 12min silent) is a fundamentally different regime that needs its own UX treatment.
- **No "large context" QA gate** — Changes to streaming code were validated only with short conversations. Need a mandatory smoke test on 1M+ context session after any streaming change.

**Known Limitation (deliberate tradeoff):**
- **Cross-turn identical text dedup false positive** — If two separate turns produce identical text (≥20 chars), same-turn dedup (`startsWith` check) collapses them into 1 block. This is extremely rare in practice (agent almost never produces byte-identical ≥20 char text across turns). Tradeoff accepted: P0 fix (content explosion → spinner hang) > edge case (identical cross-turn text merged). **Structural fix direction:** backend should tag each assistant event with a monotonic turn index; frontend replaces "block from same turn index" instead of matching by string content. This eliminates ALL heuristic matching — true "impossible by construction."

**Lessons (systemic, not per-bug):**
1. **Streaming needs integration test harness** — Replay real SDK event sequences from production session logs. Not mock, not unit — real timing, real event order, real content shapes.
2. **"Make wrong impossible" > "Detect wrong after"** — `_confirmed` marker eliminated an entire class of bugs. Any future "match and filter" logic in the hot path should be replaced with structural markers. NOTE: same-turn dedup still uses `startsWith` heuristic — this is a known deviation from the principle, accepted as P0 tradeoff until turn-index tagging is implemented.
3. **5 P0s in 1 cluster = architecture gap, not 5 bugs** — Every fix touched `useChatStreamingLifecycle.ts`. The file is 2600+ lines with 19 exported functions and no isolation between streaming phases. Extraction into focused modules (reconciliation, stall detection, tab routing) would make each testable in isolation.
4. **Production testing for production regimes** — After any streaming change, verify on a real 1M+ context session with extended thinking. Can't simulate 12-min thinking silence in unit tests.

### 2026-05-16: Strangler Fig + Pipeline Self-Improvement Session

**What Worked:**
- **Thin delegates for zero-test migration** — 382 lines moved from hook → orchestrator, 0 test files changed. Delegate stubs (3 lines each) maintain backward compat. Commit f7824b12.
- **RP compound effect** — RP33 (multi-shape) caught today's CI bug class. RP34 (shell scope) caught F3 from PE review. Each RP prevents an entire class, not one instance.
- **Adversarial tier override (mechanical)** — diff > 100 lines = full tier regardless of profile. Would have caught today's F3 if it existed before the run.
- **Auto-discover > opt-in for gates** — Pollinate validator changed from `data.get("content_dir")` (opt-in, never fires) to auto-discover from `Knowledge/Pollinate/` (opt-out, always fires). Default path must trigger the gate.
- **Specialist ↔ REVIEW_PATTERNS sync** — Each specialist now references domain-specific RPs. Prevents the "two systems, different coverage" drift (LL12 recurrence).

**What Failed:**
- **First pollinate validator commit had 3 bugs** — sys.path pollution (F1), opt-in gate never fires (F2), indentation error (if outside try). All caught by same-session PE review. Root cause: "quick fix" mindset — wrote it fast, didn't trace the execution path mentally before committing.

**Anti-Patterns Encountered:**
- **Opt-in mechanical gate** — If the gate requires the actor to opt-in (add a field, pass a flag), it's not a gate — it's a suggestion. Real gates fire on the default path.
- **Specialist prompt drift** — Adding RP patterns to REVIEW_PATTERNS.md without syncing to specialist prompts = new knowledge that adversarial review can't use. Same root cause as LL12.

### 2026-05-20: Slack Human Experience — Pipeline Gap Analysis

**What Worked:**
- **TDD caught CJK complexity bug immediately** — first test run exposed `len(text) < 50` doesn't work for CJK (19 chars = 38 semantic weight). Fixed in 2 minutes.
- **Adversarial sub-agent found double-send (HIGH)** — human_mode path fell through to generic fallback, posting response twice. Tests couldn't catch this because mock adapter accepts any call.
- **Word boundary fix from adversarial** — "stop" matched "stopwatch" in redirect detection. Non-obvious from unit tests (test inputs never contain partial matches).

**What Failed:**
- **Protocol/Interface mismatch survived TDD + adversarial** — HeartbeatManager defined Protocol with `send_message_raw`/`update_message_raw`/`delete_message_raw`. SlackChannelAdapter has NONE of these. 24 tests pass (mocks). Adversarial said "Protocol looks fine." Would have crashed first real Slack message. Root cause: unit tests with mocks prove "if method exists, logic works" but not "method exists on the REAL object."
- **Parameter semantic mismatch survived everything** — `_post_ack(channel, text)` delegated to `send_typing_indicator()` which ignores `text` and hardcodes "Thinking...". Technically "works" (returns ts, no crash). But user sees wrong content. Root cause: function exists + signature compatible ≠ semantic contract satisfied.
- **Unnecessary latency survived everything** — `asyncio.sleep(2.0)` on every message for "merge window." Code-correct (no bug), but terrible UX for instant questions. Root cause: no user-path latency trace asked "what does user WAIT for?"

**Anti-Patterns Encountered:**
- **Protocol defined without verifying satisfier** — Protocol ≠ evidence that anyone implements it. Must grep the concrete class for each declared method.
- **Delegating to a method that "looks right" without reading its body** — `send_typing_indicator` sounds like it posts a message (it does), but it ignores the text parameter (reads its own hardcoded template). Method name is marketing, body is truth.
- **Adding sleep for "safety" on a user-facing path** — Every `asyncio.sleep(N)` on a request path is N seconds of user frustration. Justify each one against a specific data dependency, or remove it.

**Pipeline Improvements Made:**
- Step 3.6: Interface Seam Verification (build.md) — verify Protocol satisfiers exist + signatures match + semantics correct
- P6.5: User Path Latency Trace (deliver.md) — walk 2-3 user scenarios through real code, flag latency/silent-failure

### 2026-06-01: Thinking Block "不显示" — 7 层链路全绿,根因是 adaptive 统计行为

**What Worked:**
- **临时诊断日志定位空块假设** — 在 `session_unit.py` StreamEvent + ThinkingBlock 分支加 `TEMP_THINK_DIAG`,一轮对话即证明 Bedrock 上 Opus 4.8 发的是**完整明文** thinking(529 个非空 delta,12/12 块有内容 51~985 字符,0 空块),推翻了代码注释里"Bedrock returns empty thinking blocks"的旧假设。
- **7 层逐环验证 + DB/built-dist 实证** — 模型→Bedrock→`session_unit.py` 解析→`_persist_assistant_blocks` 持久化→`data.db`(45 条非空 thinking,最新 18:42)→`get_session_messages` 回传→`useChatStreamingLifecycle.ts`→`ContentBlockRenderer.tsx`。关键铁证:运行 app 内嵌的 `desktop/dist/assets/index-*.js` grep 直接命中 `appendThinkingDelta`/`>Thinking<`,证明不是旧构建、不缺代码。
- **统计分布解释感知** — 最近 500 条 assistant 消息仅 8%(43 条)带 thinking,65% 是纯工具调用。"看不到"是 `adaptive`+`effort=high` 下模型自行跳过思考的正常行为,非 bug。

**What Failed:**
- **代码注释/测试与运行实测不符** — `session_unit.py:2095` 注释 + `test_session_unit_preservation.py` 断言"Opus 4.8 over Bedrock 返回空 thinking 块"。当前版本(1.17.5,`claude-opus-4-8`)实测 Bedrock 发完整明文。注释成了误导,差点让人以为是上游 bug。Root cause:把某次特定条件的观察固化成了普适注释,没标版本/条件。

**Anti-Patterns Encountered:**
- **"compressed binary grep 0 命中"当成"代码缺失"** — Tauri brotli 压缩内嵌前端,直接 grep `.app` 二进制对所有前端字符串(连 `tool_use`/`ChatPage`)都 0 命中。差点误判。正解:grep 未压缩的 `desktop/dist/assets/*.js`(.app 的真实来源)。
- **查错 DB** — messages 表在 `~/.swarm-ai/data.db`,不在 `~/.swarm-ai/SwarmWS/data.db`。第一次查 SwarmWS 报 "no such table"。

**Known Issues / Decision:**
- **保持 `adaptive` + `effort=high`(官方最优解)**,不切 `enabled`。`enabled + budget_tokens` 在 Opus 4.6+ 已废弃(官方推荐 `adaptive + effort`,未来移除),且每轮强制思考会变慢变贵、挤占 1M context。8% 可见率是设计,不是缺陷。
- 完整诊断:`Knowledge/Notes/2026-06-01-thinking-block-7layer-diagnosis.md`
- TODO(可选):修正 `session_unit.py` 那段空块注释,标注"早期/特定条件"而非普适;或删除已不符的断言。
