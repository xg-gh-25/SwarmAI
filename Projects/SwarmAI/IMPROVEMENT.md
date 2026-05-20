
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
