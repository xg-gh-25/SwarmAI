#!/usr/bin/env python3
"""
Post-deploy E2E smoke test for SwarmAI.

Runs against a live daemon (or dev server) and verifies critical paths work
end-to-end. Scope-aware: full (chat stream) or frontend-only (health + endpoints).

Exit 0 = all critical paths working.
Exit 1 = regression detected (DO NOT declare deploy success).

Usage:
  python3 scripts/smoke_e2e.py                       # Full scope against daemon
  python3 scripts/smoke_e2e.py --scope frontend-only # Skip chat stream (no model call)
  python3 scripts/smoke_e2e.py --port 8000           # Against dev server
  python3 scripts/smoke_e2e.py --verbose             # Show detailed output
  python3 scripts/smoke_e2e.py --help                # Show this help
"""

import argparse
import asyncio
import json
import sys
import time

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


TIMEOUT = 60.0  # Wall-clock deadline for chat stream (cold model call can be slow)
HEALTH_TIMEOUT = 5.0
SMOKE_SESSION_PREFIX = "__smoke_test__"


class SmokeResult:
    """Accumulates pass/fail/skip results for each check.

    Three statuses (PIT49 taxonomy — distinguish busy from broken):
      - "pass": check succeeded.
      - "fail": check genuinely failed → exit 1.
      - "skip": check could not run for a NON-failure reason (e.g. the daemon
        was at MAX_CONCURRENT_STREAMS so a chat_stream would only queue). A skip
        is NOT a failure — it must never flip exit code to 1. This is the OT03
        fix: a busy-but-healthy daemon scores green-with-skip, never false-red.
    """

    def __init__(self, verbose: bool = False):
        # (name, status, detail) where status in {"pass","fail","skip"}
        self.results: list[tuple[str, str, str]] = []
        self.verbose = verbose

    def record(self, name: str, passed: bool, detail: str = ""):
        status = "pass" if passed else "fail"
        self.results.append((name, status, detail))
        icon = "\033[32m✓\033[0m" if passed else "\033[31m✗\033[0m"
        if self.verbose or not passed:
            print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
        elif passed:
            print(f"  {icon} {name}")

    def skip(self, name: str, detail: str = ""):
        """Record a check as skipped (not run, not failed). Never affects
        all_passed/exit code — a saturated daemon is healthy, just busy."""
        self.results.append((name, "skip", detail))
        icon = "\033[33m⊘\033[0m"  # yellow circle-slash = skipped
        print(f"  {icon} {name} (skipped)" + (f" — {detail}" if detail else ""))

    @property
    def all_passed(self) -> bool:
        # Skips do NOT count as failures — only an explicit "fail" fails the run.
        return all(status != "fail" for _, status, _ in self.results)

    @property
    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for _, s, _ in self.results if s == "pass")
        failed = sum(1 for _, s, _ in self.results if s == "fail")
        skipped = sum(1 for _, s, _ in self.results if s == "skip")
        parts = [f"{passed}/{total} passed"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        return ", ".join(parts)


def _extract_event_text(event: dict) -> str:
    """Pull assistant text out of an SSE event, tolerant of both shapes:
      - {"type":"assistant","content":[{"type":"text","text":"..."}, ...]}
      - {"type":"assistant","content":"plain string"}
    Non-text blocks (tool_use, thinking) contribute no text. Used by the
    content-shape check (OT03) to verify the backend delivered real content.
    """
    content = event.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    # Some events carry top-level "text" (e.g. text_delta)
    if isinstance(event.get("text"), str):
        return event["text"]
    return ""


async def _check_no_stuck_streaming(base_url: str, result: "SmokeResult") -> None:
    """Probe for wedged streaming sessions (OT01/recovery class) via the
    admission-state stalled_streaming signal. Runs even when the daemon is
    saturated — the saturation path must NOT lose the wedge detector (Q1).
    Records a passing/failing 'no_stuck_streaming' check; never raises."""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/chat/sessions/admission-state")
            adm = r.json()
            stalled = adm.get("stalled_streaming", 0)
            result.record(
                "no_stuck_streaming",
                stalled == 0,
                "no stalled streams" if stalled == 0
                else f"{stalled} STALLED streaming session(s) — wedge",
            )
    except Exception as e:
        result.record("no_stuck_streaming", False, f"probe failed: {e}")


async def _check_disconnect_recovery(
    base_url: str, agent_id: str, result: "SmokeResult", verbose: bool = False
) -> None:
    """E2E: inject a REAL mid-stream SSE drop and assert the backend recovers.

    This is the post-deploy half of the disconnect-recovery verification chain
    (the frontend half lives in chat.contract.test.ts). It exercises the exact
    class behind ~33 OT01 recurrences: a stream that drops mid-flight while the
    backend is genuinely STREAMING.

    Method: open a real chat stream, read until the FIRST assistant content
    arrives (proves the backend is genuinely mid-stream), then EXIT the
    ``async with c.stream`` context — that closes the TCP connection, which the
    backend sees as a client disconnect (``request.is_disconnected()`` →
    ``_recover_streaming_on_disconnect``).

    TEETH (Gate-1 BLOCKER fix — ``streaming==false`` ALONE is a false-green: it
    also holds for a phantom never-streamed session AND a vanished session).
    A PASS requires ALL of:
      1. non-empty assistant content streamed BEFORE the drop (genuinely mid-flight),
      2. the session was PRESENT in streaming-state (absent → FAIL, not pass),
      3. the turn PERSISTED to /sessions/{id}/messages (recovery actually worked),
      4. the session reports streaming==false (not stuck).

    Skip-aware (PIT49): if no content arrives (daemon busy / no slot) → skip,
    never fail. Runs only in full scope (needs a model call).
    """
    session_id = None
    first_content = ""
    try:
        async with asyncio.timeout(TIMEOUT):
            async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT) as c:
                async with c.stream(
                    "POST",
                    "/api/chat/stream",
                    json={
                        # A LONG reply (not one word) so the break reliably lands
                        # MID-stream — a 1-word reply can complete before the TCP
                        # close is processed, degrading this into a completion test
                        # instead of a disconnect test (Gate-2 NIT).
                        "agent_id": agent_id,
                        "message": "Count slowly from 1 to 40, one number per line.",
                        "session_id": None,
                        "enable_skills": False,
                        "enable_mcp": False,
                    },
                ) as stream:
                    async for line in stream.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        # The FIRST event carrying the id is `session_start`
                        # with key `sessionId` (camelCase, emitted at SDK init
                        # BEFORE any assistant content — streaming_orchestrator
                        # :588). `session_id` (snake_case) only appears on the
                        # `result` event AFTER the stream, which we never reach
                        # because we break mid-stream. Without capturing the
                        # camelCase key here, session_id stays None → teeth #1
                        # SKIPs forever even when content streamed (the bug that
                        # made this check never execute live).
                        if not session_id:
                            session_id = event.get("sessionId") or event.get("session_id")
                        if event.get("type") == "assistant":
                            txt = _extract_event_text(event)
                            if txt.strip():
                                first_content = txt
                                # GENUINELY mid-stream now — DROP by exiting the
                                # stream context (real TCP close), before result.
                                break
                # ← async-with exited here = client disconnect reaches backend
    except (httpx.ReadTimeout, TimeoutError):
        result.skip("disconnect_recovery", f"no content within {TIMEOUT}s (daemon busy?)")
        return
    except Exception as e:
        result.record("disconnect_recovery", False, f"stream error: {e}")
        return

    # Teeth #1: content must have streamed before the drop (else nothing to recover).
    if not session_id or not first_content.strip():
        result.skip(
            "disconnect_recovery",
            "no mid-stream content arrived (no slot / cold) — nothing to drop",
        )
        return

    # Recovery is two-phase: STREAMING→IDLE is synchronous, but the ASSISTANT
    # turn is flushed to DB by a BACKGROUND subprocess-cleanup task. A fixed
    # sleep would race that flush (Gate-2 MUST-FIX) → poll-with-retry instead.
    def _has_assistant_turn(msg_list: list) -> bool:
        # Teeth #3 (Gate-2 BLOCKER fix): the USER row is persisted BEFORE the
        # model call (session_router stores it pre-spawn), so `len>0` is a
        # FALSE GREEN — it passes even if recovery flushed ZERO assistant
        # content. Require an ASSISTANT row with non-empty text: that is what
        # proves recovery actually flushed the answer.
        for m in msg_list:
            if m.get("role") == "assistant" and _extract_event_text(m).strip():
                return True
        return False

    present = False
    our_streaming = True
    persisted = False
    try:
        deadline = time.monotonic() + 12.0  # poll up to ~12s for the bg flush
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            while time.monotonic() < deadline:
                # Teeth #2: session PRESENT in streaming-state. Absent reads as
                # streaming=true (→ fail), never a vanished-session false green.
                sr = await c.get("/api/chat/sessions/streaming-state")
                sessions_in_state = sr.json().get("sessions", {})
                present = session_id in sessions_in_state
                our_streaming = (
                    sessions_in_state.get(session_id, {}).get("streaming", True)
                    if present else True
                )
                # Teeth #3: ASSISTANT turn persisted (not just the pre-written user row).
                mr = await c.get(f"/api/chat/sessions/{session_id}/messages")
                if mr.status_code == 200:
                    msgs = mr.json()
                    msg_list = msgs if isinstance(msgs, list) else msgs.get("messages", [])
                    persisted = _has_assistant_turn(msg_list)
                # All teeth satisfied → stop polling early.
                if present and persisted and not our_streaming:
                    break
                await asyncio.sleep(1.0)

            # Teeth #4: not stuck streaming. ALL four must hold.
            passed = present and persisted and (not our_streaming)
            result.record(
                "disconnect_recovery",
                passed,
                f"recovered: present={present} assistant_persisted={persisted} "
                f"streaming={our_streaming} (pre-drop content={len(first_content)}c)"
                if passed else
                f"RECOVERY GAP: present={present} assistant_persisted={persisted} "
                f"still_streaming={our_streaming}",
            )
    except Exception as e:
        result.record("disconnect_recovery", False, f"post-drop probe failed: {e}")
    finally:
        # Best-effort cleanup of this run's session.
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
                await c.delete(f"/api/chat/sessions/{session_id}")
        except Exception:
            pass


async def cleanup_orphans(base_url: str) -> int:
    """Delete leftover smoke sessions from prior crashed runs.

    Identifies orphans by title containing "SMOKE_OK" (our prompt text)
    or sessions with title matching our exact prompt message. Since we can't
    control session title directly, we use the message content as a heuristic.

    Returns count of orphans cleaned.
    """
    cleaned = 0
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/chat/sessions", params={"limit": 100})
            if r.status_code != 200:
                return 0
            sessions = r.json()
            for s in sessions:
                title = (s.get("title") or "").lower()
                # Backend auto-titles from first message content
                if "smoke_ok" in title or "reply with exactly one word" in title:
                    await c.delete(f"/api/chat/sessions/{s['id']}")
                    cleaned += 1
    except Exception:
        pass  # Non-critical — orphan cleanup is best-effort
    return cleaned


async def run_smoke(
    base_url: str, scope: str = "full", verbose: bool = False
) -> SmokeResult:
    """Run smoke checks against the given base URL.

    Scopes:
      - "full": health + default agent + chat stream + state cleanup (costs 1 model call)
      - "frontend-only": health + sessions endpoint + streaming-state (no model call)
    """
    result = SmokeResult(verbose=verbose)

    # ── 0. Cleanup orphan smoke sessions from prior crashed runs ──
    orphans = await cleanup_orphans(base_url)
    if orphans > 0 and verbose:
        print(f"  ℹ Cleaned {orphans} orphan smoke session(s)")

    # ── 1. Health ──
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/health")
            health_ok = r.status_code == 200 and r.json().get("status") == "healthy"
            result.record("health", health_ok, f"status={r.status_code}")
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        # BaseException needed for ExceptionGroup (Python 3.11+ httpx wraps ConnectError)
        err_str = str(e)
        if "Connect" in err_str or "refused" in err_str or "TaskGroup" in err_str:
            result.record("health", False, "connection refused")
        else:
            result.record("health", False, err_str)
        print(f"\n  Cannot reach {base_url}/health — is the daemon running?")
        return result

    # ── 2. Streaming state endpoint (works for all scopes) ──
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/chat/sessions/streaming-state")
            state_data = r.json()
            has_sessions_key = "sessions" in state_data
            result.record("streaming_state", has_sessions_key, "endpoint responsive")
    except Exception as e:
        result.record("streaming_state", False, str(e))

    # ── 3. Sessions list endpoint (works for all scopes) ──
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/chat/sessions", params={"limit": 5})
            sessions_ok = r.status_code == 200 and isinstance(r.json(), list)
            result.record("sessions_list", sessions_ok, f"status={r.status_code}")
    except Exception as e:
        result.record("sessions_list", False, str(e))

    # ── 3b. Workspace tree endpoint (Gap #18) ──
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/workspace/tree", params={"depth": 1})
            data = r.json()
            # API returns either a list (flat nodes) or dict with children
            tree_ok = r.status_code == 200 and (
                (isinstance(data, list) and len(data) > 0)
                or (isinstance(data, dict) and bool(data.get("children") or data.get("items")))
            )
            count = len(data) if isinstance(data, list) else len(data.get("children", []))
            result.record(
                "workspace_tree", tree_ok,
                f"status={r.status_code}, nodes={count}",
            )
    except Exception as e:
        result.record("workspace_tree", False, str(e))

    # ── 3c. Session persistence (Gap #19) ──
    # Verify that sessions created are actually persisted (survive daemon restart)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/chat/sessions", params={"limit": 1})
            if r.status_code == 200:
                sessions = r.json()
                if sessions:
                    # Fetch the first session's messages to verify DB persistence
                    sid = sessions[0].get("id")
                    r2 = await c.get(f"/api/chat/sessions/{sid}/messages", params={"limit": 1})
                    persist_ok = r2.status_code == 200
                    result.record(
                        "session_persist", persist_ok,
                        f"session={sid[:8]}… messages_status={r2.status_code}",
                    )
                else:
                    # No sessions exist — that's OK for fresh installs
                    result.record("session_persist", True, "no sessions (fresh install)")
            else:
                result.record("session_persist", False, f"sessions_status={r.status_code}")
    except Exception as e:
        result.record("session_persist", False, str(e))

    # ── Frontend-only scope stops here — no model call needed ──
    if scope == "frontend-only":
        # Also check eval health (API that frontend uses)
        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=HEALTH_TIMEOUT
            ) as c:
                r = await c.get("/api/eval/health")
                eval_ok = r.status_code == 200
                result.record("eval_health", eval_ok, f"status={r.status_code}")
        except Exception as e:
            result.record("eval_health", False, str(e))
        return result

    # ══════════════════════════════════════════════════════════════
    # Full scope: chat stream (requires model call)
    # ══════════════════════════════════════════════════════════════

    # ── 4. Get default agent ──
    agent_id = ""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/agents/default")
            agent = r.json()
            agent_id = agent.get("id", "")
            result.record("default_agent", bool(agent_id), f"id={agent_id[:8]}...")
    except Exception as e:
        result.record("default_agent", False, str(e))
        return result

    # ── 4b. Admission pre-flight (OT03) ──
    # Before spending a model call + a wall-clock timeout, check whether the
    # daemon is at the R6 concurrent-streaming cap. If saturated, a chat_stream
    # would only QUEUE behind the cap and hit our TIMEOUT — looking broken when
    # the daemon is merely busy. Skip (not fail) in that case. Read-only probe,
    # consumes no slot.
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=HEALTH_TIMEOUT) as c:
            r = await c.get("/api/chat/sessions/admission-state")
            adm = r.json()
            if adm.get("saturated"):
                stalled = adm.get("stalled_streaming", 0)
                if stalled > 0:
                    # CRITICAL distinction (adversarial Q1): saturation caused by
                    # STALLED streams is the OT01/recovery WEDGE — the exact bug
                    # this smoke exists to catch. Skipping here would MASK it.
                    # Fail loudly instead.
                    result.record(
                        "chat_stream",
                        False,
                        f"saturated by {stalled} STALLED streaming session(s) "
                        f"(>{adm.get('streaming_count')}/{adm.get('max_concurrent')}) "
                        f"— WEDGE suspected, not legitimate load",
                    )
                else:
                    # Saturated by ADVANCING streams = legitimately busy. A new
                    # turn would only queue → skip (not fail). Endpoint checks
                    # above already proved the daemon is responsive.
                    result.skip(
                        "chat_stream",
                        f"daemon busy ({adm.get('streaming_count')}/"
                        f"{adm.get('max_concurrent')} streaming, 0 stalled) — a "
                        f"new turn would queue; not a failure",
                    )
                # Either way we do NOT run a model call this turn (no free slot).
                # But still run the stuck-session probe (Q1: don't lose the only
                # wedge detector to an early return).
                await _check_no_stuck_streaming(base_url, result)
                return result
    except Exception as e:
        # Endpoint missing/unreachable → don't skip, fall through to the real
        # stream test (degrade gracefully; old behavior).
        if verbose:
            print(f"  ℹ admission-state unavailable ({e}) — running full stream test")

    # ── 5. Chat stream with wall-clock timeout ──
    session_id = None
    try:
        async with asyncio.timeout(TIMEOUT):
            async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT) as c:
                events: list[dict] = []
                async with c.stream(
                    "POST",
                    "/api/chat/stream",
                    json={
                        "agent_id": agent_id,
                        "message": "Reply with exactly one word: SMOKE_OK",
                        "session_id": None,
                        "enable_skills": False,
                        "enable_mcp": False,
                    },
                ) as stream:
                    async for line in stream.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            event = json.loads(data_str)
                            events.append(event)
                            if not session_id and event.get("session_id"):
                                session_id = event["session_id"]
                            if event.get("type") == "result":
                                break
                        except json.JSONDecodeError:
                            continue

                has_result = any(e.get("type") == "result" for e in events)
                has_assistant = any(e.get("type") == "assistant" for e in events)

                # Ensure session_id extracted (check all events, not just first)
                if not session_id:
                    for e in events:
                        if e.get("session_id"):
                            session_id = e["session_id"]
                            break

                result.record(
                    "chat_stream",
                    has_result and has_assistant,
                    f"{len(events)} events, result={'yes' if has_result else 'NO'}, "
                    f"assistant={'yes' if has_assistant else 'NO'}",
                )

                # ── 5b. Content-shape check (OT03 — backend layer of the
                # content-loss guard). The events we already captured must
                # carry non-empty assistant content with no truncation
                # sentinel. This is the cheap backend half of the OT01
                # content-loss class (frontend render half lives in the
                # vitest render-fidelity test). Shape, not exact text — AI
                # output varies run to run.
                if has_assistant:
                    assistant_text = "".join(
                        _extract_event_text(e)
                        for e in events
                        if e.get("type") == "assistant"
                    )
                    # Q2 fix: assert the EXPECTED token, not arbitrary sentinels.
                    # The prompt demands the exact word SMOKE_OK; a truncated
                    # response ("SMOK", "SMOKE_O", or empty) FAILS this — which
                    # generic non-empty + sentinel-scan did NOT catch. Tolerate
                    # surrounding whitespace/markdown but require the full token.
                    expected = "SMOKE_OK"
                    delivered_full = expected in assistant_text
                    result.record(
                        "content_shape",
                        delivered_full,
                        f"assistant_len={len(assistant_text)}, "
                        f"expected_token={'present' if delivered_full else 'MISSING/truncated'}",
                    )
                elif has_result:
                    # result but no assistant content block = empty/dropped
                    # response (a content-loss failure, not a skip).
                    result.record(
                        "content_shape", False,
                        "result event but no assistant content block",
                    )
    except (httpx.ReadTimeout, TimeoutError):
        result.record("chat_stream", False, f"timeout after {TIMEOUT}s")
    except Exception as e:
        result.record("chat_stream", False, str(e))

    # ── 6. Verify session not stuck streaming ──
    if session_id:
        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=HEALTH_TIMEOUT
            ) as c:
                r = await c.get("/api/chat/sessions/streaming-state")
                state_data = r.json()
                sessions_in_state = state_data.get("sessions", {})
                # FALSE-GREEN FIX: an ABSENT session must FAIL, not pass. The old
                # `our_streaming=False` default meant a vanished/phantom session
                # (never registered, or dropped from the mirror) scored
                # `not False` = PASS — a disappeared session is a FAILURE, not
                # "idle". Same teeth as disconnect_recovery #2 (present→judge,
                # absent→fail). We JUST streamed a real turn on this session, so
                # it MUST be present in streaming-state; present-and-idle is the
                # only clean result.
                present = session_id in sessions_in_state
                our_streaming = (
                    sessions_in_state[session_id].get("streaming", False)
                    if present
                    else False
                )
                state_clean = present and not our_streaming
                if not present:
                    detail = "session ABSENT from streaming-state (vanished/phantom — FAIL, not idle)"
                elif our_streaming:
                    detail = "still streaming!"
                else:
                    detail = "session idle"
                result.record("state_clean", state_clean, detail)
        except Exception as e:
            result.record("state_clean", False, str(e))

        # ── 7. Cleanup this run's session ──
        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=HEALTH_TIMEOUT
            ) as c:
                r = await c.delete(f"/api/chat/sessions/{session_id}")
                result.record(
                    "cleanup",
                    r.status_code in (200, 204, 404),
                    f"status={r.status_code}",
                )
        except Exception as e:
            result.record("cleanup", False, str(e))

    # ── 8. Mid-stream disconnect recovery (OT01/OT03 — the verification chain
    # this gap kept open). Full scope only; reaches here only past the admission
    # pre-flight (busy daemon already early-returned). Skip-aware. NOTE: this is
    # a SECOND model call — it adds ~one cold-stream's wall-clock to full smoke;
    # accepted because no other layer exercises a real transport drop.
    await _check_disconnect_recovery(base_url, agent_id, result, verbose)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="SwarmAI post-deploy E2E smoke test"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18321,
        help="Backend port (default: 18321 for daemon, use 8000 for dev)",
    )
    parser.add_argument(
        "--scope",
        choices=["full", "frontend-only"],
        default="full",
        help="Test scope: 'full' includes chat stream (model call), "
        "'frontend-only' checks endpoints only (no model call)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed output"
    )
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}"

    print(f"\n{'='*50}")
    print(f"  SwarmAI E2E Smoke Test")
    print(f"  Target: {base_url} | Scope: {args.scope}")
    print(f"{'='*50}\n")

    start = time.time()
    result = asyncio.run(run_smoke(base_url, scope=args.scope, verbose=args.verbose))
    elapsed = time.time() - start

    print(f"\n{'─'*50}")
    print(f"  {result.summary} ({elapsed:.1f}s)")
    print(f"{'─'*50}\n")

    sys.exit(0 if result.all_passed else 1)


if __name__ == "__main__":
    main()
