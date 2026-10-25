"""Test helper: the old ``ReviewHookBridge`` call shape over ``hook_bridge``'s
stateless functions, for tests of the review flow itself (not of the binding, which
``test_run_protocol_hook_bridge`` covers). ``track_tool_use`` records the spawn a host
PreToolUse would and returns the prompt the expert gets (unchanged since T2)."""
from __future__ import annotations

from core.run_protocol import hook_bridge as hb


class ReviewHookBridge:
    prompt_sha = staticmethod(hb.prompt_sha)

    def __init__(self, store, session_id: str):
        self.store, self.session_id = store, session_id

    def track_tool_use(self, tool_use_id: str, prompt: str):
        return prompt if hb.bind_spawn(self.store, prompt, tool_use_id) else None

    def record_answer(self, prompt: str, final_text: str, *, completed_at: str):
        return hb.record_answer(self.store, prompt, final_text, completed_at=completed_at,
                                session_id=self.session_id)

    def record_usage(self, usage, *, completed_at: str) -> None:
        hb.record_usage(self.store, usage, completed_at=completed_at)
