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
    """Accumulates pass/fail results for each check."""

    def __init__(self, verbose: bool = False):
        self.results: list[tuple[str, bool, str]] = []
        self.verbose = verbose

    def record(self, name: str, passed: bool, detail: str = ""):
        self.results.append((name, passed, detail))
        icon = "\033[32m✓\033[0m" if passed else "\033[31m✗\033[0m"
        if self.verbose or not passed:
            print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
        elif passed:
            print(f"  {icon} {name}")

    @property
    def all_passed(self) -> bool:
        return all(passed for _, passed, _ in self.results)

    @property
    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for _, p, _ in self.results if p)
        return f"{passed}/{total} checks passed"


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
                our_streaming = False
                if session_id in sessions_in_state:
                    our_streaming = sessions_in_state[session_id].get(
                        "streaming", False
                    )
                result.record(
                    "state_clean",
                    not our_streaming,
                    "session idle" if not our_streaming else "still streaming!",
                )
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
