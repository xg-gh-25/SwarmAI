"""The dispatch text and the gates say the same thing about reserved actions (design
docs/design/2026-11-04-kirocrew-borrow.md §3 A3).

Question: the project section a dispatched session reads says each reserved action is
refused without asking anyone; on a session pinned with those same actions, does each
one get refused by its gate, with nobody asked?
"""
from __future__ import annotations

import asyncio

from core import project_agent_session as pas
from core import task_spec as ts
from core.project_agent import RESERVED_ACTIONS
from core.security_hooks import create_dangerous_command_gate, create_external_approval_gate

#: one example of each reserved action, and the gate it reaches
EXAMPLES = {
    "push": ("Bash", {"command": "git push origin main"}),
    "open_pr": ("Bash", {"command": "gh pr create --fill"}),
    "publish_release": ("Bash", {"command": "gh release create v1"}),
    "send_external_message": ("mcp__slack__post_message", {"channel": "c", "text": "hi"}),
}


class _PM:
    """Approves anything it is asked: a refusal can only come from the gate itself."""

    def __init__(self):
        self.asked = []

    def is_command_approved(self, session_key, command, *, cwd):
        return True

    def is_resolved(self, session_id, tool_call_id):
        return False

    def store_pending_request(self, req):
        pass

    async def request_decision(self, session_id, req, timeout=None):
        self.asked.append(req)
        return "approve"

    def remove_pending_request(self, request_id):
        pass

    def pop_decided_by(self, request_id):
        return "person"

    def approve_command(self, session_key, command, *, cwd):
        pass


def _spec(reserved):
    return {"brain": "Lib", "mode": "code-change", "summary": "", "execution": {"how": "native"},
            "profile": None, "setup": [], "validations": [], "watches": [], "skills": [],
            "returns": "commits", "reserved": reserved, "gates": [], "acceptance": "tests pass"}


def test_segment_matches_gates(tmp_path, monkeypatch):
    import core.security_hooks as sh
    import core.tool_risk as tr
    monkeypatch.setattr(sh, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(tr, "user_overrides", lambda name: None)
    reserved = sorted(RESERVED_ACTIONS)
    text = ts.segment(_spec(reserved))
    line = next(ln for ln in text.splitlines() if ", ".join(reserved) in ln)
    assert "refused" in line.lower() and "without asking anyone" in line, line
    assert "Asked of the person" not in text
    assert "another way" in text and "why they could not be done" in text

    ctx = {"sdk_session_id": "s-pa"}
    pas.set_pin(ctx, {"brain": "Lib", "mode": "code-change", "revision": "r", "profile": None,
                      "reserved": reserved, "gates": [], "gates_digest": "d"})
    pm = _PM()
    bash = create_dangerous_command_gate(ctx, "s-pa", pm, True)
    external = create_external_approval_gate(ctx, "s-pa", pm, True)
    for action in reserved:
        tool, tool_input = EXAMPLES[action]       # every reserved action has a gate example
        gate = bash if tool == "Bash" else external
        out = asyncio.run(gate({"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)},
                               f"t-{action}", None))
        assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", (action, out)
    assert pm.asked == []
