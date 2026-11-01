"""Brain setup resume — the periodic job body (job type ``brain_setup_resume``).

A New Brain is set up by the agent of the chat that created it (s_project-manager
P0..P6). When that session ends first, the Brain stays ``setting-up``; nothing should
wait for the person to click "Continue setup" (design 2026-11-01-brain-to-hive-zero-touch
§1). This finds the oldest Brain whose setup files have not changed for a while
(``core.ddd_setup_state.stalled``) and starts an ordinary chat session on this machine's
daemon with the continue-setup prompt: the session runs with every hook a chat session
has (dangerous commands, off-machine calls), never as a headless job agent, since the
setup reads repository content it did not write. Each start is counted
(``record_resume``): a setup is continued at most ``MAX_RESUMES`` times, a day apart.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

#: hang guard for the daemon to name the new session (it names it within seconds)
SESSION_WAIT_S = 60


def next_resume(projects_dir: Path, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """``{"brain", "dir", "prompt"}`` for the oldest stalled setup, or None."""
    from core.ddd_setup_state import resume_prompt, stalled
    found = stalled(Path(projects_dir), now)
    if not found:
        return None
    return {"brain": found[0].name, "dir": found[0], "prompt": resume_prompt(found[0].name)}


def _base() -> str:
    port = os.environ.get("SWARMAI_PORT", "18321")
    return f"http://127.0.0.1:{port if port.isdigit() else '18321'}"


def start_session(prompt: str) -> Optional[str]:
    """Start a chat session on this machine's daemon with *prompt*; its id, or None when
    the daemon did not take it. Reads the stream only until the session is named; the
    daemon keeps running the turn."""
    import httpx
    body = {"agent_id": "default", "message": prompt}
    try:
        with httpx.Client(base_url=_base(), timeout=httpx.Timeout(10.0, read=SESSION_WAIT_S)) as c:
            with c.stream("POST", "/api/chat/stream", json=body) as r:
                if r.status_code != 200:
                    return None
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    sid = ev.get("session_id") if isinstance(ev, dict) else None
                    if isinstance(sid, str) and sid:
                        return sid
    except httpx.HTTPError:
        return None
    return None


def run(projects_dir: Path, now: Optional[float] = None) -> dict[str, Any]:
    """One tick: continue the oldest stalled setup, if any."""
    from core.ddd_setup_state import record_resume
    task = next_resume(projects_dir, now)
    if task is None:
        return {"status": "success", "summary": "no stalled Brain setup"}
    record_resume(task["dir"], now)          # counted before it starts: a failure counts too
    sid = start_session(task["prompt"])
    if sid is None:
        return {"status": "failed", "summary": f"{task['brain']}: the daemon did not start the session"}
    return {"status": "success", "summary": f"{task['brain']}: continued in session {sid}"}
