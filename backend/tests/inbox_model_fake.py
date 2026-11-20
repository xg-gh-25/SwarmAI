"""A fake Bedrock Converse call for the Inbox triage, drafting and trend tests.

It replaces only ``jobs.bedrock.converse_with_retry`` (and the model-id lookup), so the
real ``structured()`` / ``text()`` build the request and parse the reply: a test sees the
exact tool configuration, system prompt and user turn the Inbox code sends.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Union

from jobs import bedrock

MODEL = "claude-sonnet-4-6"

Reply = Union[dict, Callable[[dict], dict]]


def tool_reply(answer: dict, *, name: str = "answer", extra_blocks: tuple = ()) -> dict:
    """A Converse response whose assistant message calls tool *name* with *answer*."""
    content = [{"toolUse": {"toolUseId": "t1", "name": name, "input": answer}}, *extra_blocks]
    return {"stopReason": "tool_use", "usage": {},
            "output": {"message": {"role": "assistant", "content": content}}}


def text_reply(text: str) -> dict:
    return {"stopReason": "end_turn", "usage": {},
            "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


class FakeBedrock:
    def __init__(self, monkeypatch: Any, reply: Reply) -> None:
        self.reply = reply
        self.calls: list[dict] = []
        self.threads: list[threading.Thread] = []
        monkeypatch.setattr(bedrock, "converse_with_retry", self._converse)
        monkeypatch.setattr(bedrock, "get_model_id", lambda *a, **k: MODEL)

    def _converse(self, **kw: Any) -> dict:
        self.calls.append(kw)
        self.threads.append(threading.current_thread())
        return self.reply(kw) if callable(self.reply) else self.reply

    # what the last call sent
    @property
    def user_turn(self) -> str:
        return "".join(part["text"] for part in self.calls[-1]["messages"][0]["content"])

    @property
    def system(self) -> str:
        return "".join(part["text"] for part in self.calls[-1]["system"])

    @property
    def tool_config(self) -> Any:
        return self.calls[-1]["tool_config"]
