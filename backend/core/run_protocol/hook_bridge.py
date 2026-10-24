"""Bridge SDK Agent hooks into Pipeline v1 review events.

Public symbols:
- ``ReviewHookBridge`` — bind Agent tool-use IDs to handouts and persist answers.
- ``install_bridge`` / ``bridge_for`` — session-context scoped bridge registration.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

from core.run_protocol.answers import ParsedAnswer, parse_answer
from core.run_protocol.store import AuditStore, StoreError

_CONTEXT_KEY = "_pipeline_v1_review_bridges"
_EXPERT = re.compile(r"^EXPERT-ID:\s*([a-z][a-z0-9-]*)\s*$", re.MULTILINE)
_BINDING_PREFIX = "PIPELINE-HANDOUT: "


@dataclass(frozen=True)
class _Pending:
    run_id: str
    tool_use_id: str
    handout_event_id: str
    prompt_sha: str
    nonce: str


class ReviewHookBridge:
    """One run's hook bridge; all durable state remains in ``AuditStore``."""

    def __init__(self, store: AuditStore, session_id: str):
        if not session_id:
            raise StoreError("session_id is required for review hook bridge")
        self.store = store
        self.session_id = session_id
        self._pending: dict[str, _Pending] = {}

    @staticmethod
    def prompt_sha(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_binding(pending: _Pending) -> str:
        raw = json.dumps({
            "run_id": pending.run_id,
            "handout_event_id": pending.handout_event_id,
            "tool_use_id": pending.tool_use_id,
            "prompt_sha": pending.prompt_sha,
            "nonce": pending.nonce,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_binding(prompt_head: str) -> dict[str, str] | None:
        lines = [line for line in prompt_head.splitlines()
                 if line.startswith(_BINDING_PREFIX)]
        if len(lines) != 1:
            return None
        token = lines[0][len(_BINDING_PREFIX):].strip()
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        required = {"run_id", "handout_event_id", "tool_use_id", "prompt_sha", "nonce"}
        if not isinstance(value, dict) or set(value) != required \
                or any(not isinstance(value[key], str) or not value[key] for key in required):
            return None
        return value

    def matching_handouts(self, prompt: str) -> list[dict[str, Any]]:
        """Signed handouts whose full prompt digest and expert both match *prompt*."""
        if not isinstance(prompt, str) or _BINDING_PREFIX in prompt:
            return []
        match = _EXPERT.search(prompt)
        if not match:
            return []
        digest = self.prompt_sha(prompt)
        return [event for event in self.store.read_events()
                if event.get("kind") == "handout"
                and event.get("payload", {}).get("expert_id") == match.group(1)
                and event.get("payload", {}).get("prompt_sha") == digest]

    def track_tool_use(self, tool_use_id: str, prompt: str) -> str | None:
        """Return a prompt carrying this exact spawn's unguessable completion identity."""
        if not tool_use_id:
            return None
        candidates = self.matching_handouts(prompt)
        if len(candidates) != 1:
            return None
        handout = candidates[0]
        nonce = secrets.token_hex(24)
        pending = _Pending(
            run_id=self.store.paths.run_id,
            tool_use_id=tool_use_id,
            handout_event_id=handout["event_id"],
            prompt_sha=self.prompt_sha(prompt),
            nonce=nonce,
        )
        self._pending[nonce] = pending
        lines = prompt.splitlines(keepends=True)
        if not lines:
            return None
        return lines[0] + _BINDING_PREFIX + self._encode_binding(pending) + "\n" + "".join(lines[1:])

    def record_answer(self, prompt: str, final_text: str, *, completed_at: str) -> dict[str, Any] | None:
        """Persist output only for the exact nonce-bound spawn represented by *prompt*.
        The answer and its findings are one operation on the run (r6 §2.2): a CLI
        command never decides on half of them."""
        from core.run_protocol.oplock import operation
        # Every other holder is a CLI call bounded by its Bash call (at most 600 s) and a
        # flock dies with its process, so this wait always ends with the lock; the
        # answer is never dropped for a busy run (review f227e2ed).
        with operation(self.store.paths.root, self.store.paths.run_id, timeout=900.0):
            return self._record_answer(prompt, final_text, completed_at=completed_at)

    def _record_answer(self, prompt: str, final_text: str, *,
                       completed_at: str) -> dict[str, Any] | None:
        identity = self._decode_binding(prompt)
        if identity is None or identity.get("run_id") != self.store.paths.run_id:
            return None
        pending = self._pending.get(identity["nonce"])
        if pending is None or identity != {
            "run_id": pending.run_id,
            "handout_event_id": pending.handout_event_id,
            "tool_use_id": pending.tool_use_id,
            "prompt_sha": pending.prompt_sha,
            "nonce": pending.nonce,
        }:
            return None
        events = self.store.read_events()
        # one answer per handout is checked under the run lock at the append (F-2);
        # a retry of an answered handout needs a new attempt
        handout = next((row for row in events
                        if row.get("event_id") == pending.handout_event_id), None)
        if handout is None or handout.get("kind") != "handout" \
                or handout.get("payload", {}).get("prompt_sha") != pending.prompt_sha:
            raise StoreError("tracked handout is missing or changed")
        payload = handout["payload"]
        expert = payload["expert_id"]
        review_round = handout["round"]
        parsed = parse_answer(final_text, expert_id=expert, round=review_round,
                              finding_ids=payload.get("finding_ids", ()))
        answer_payload = {
            "handout_event_id": pending.handout_event_id,
            "prompt_sha": pending.prompt_sha,
            "tool_use_id": pending.tool_use_id,
            "expert_id": expert,
            "stage": handout["stage"],
            "round": review_round,
            **parsed.answer_payload,
            "raw": final_text,
        }
        handout_id = pending.handout_event_id

        def answered(chain):
            return ("this handout is already answered" if any(
                row.get("kind") == "answer"
                and row.get("payload", {}).get("handout_event_id") == handout_id
                for row in chain) else None)
        stage, attempt = handout["stage"], handout["attempt"]

        def children(answer: dict[str, Any]) -> list[dict[str, Any]]:
            if parsed.state not in ("parsed", "partial"):
                return []   # an unparsed answer is no usable output (§A3.2)
            common = {"at": completed_at, "stage": stage, "round": review_round,
                      "attempt": attempt}
            rows = [{**common, "kind": "finding_occ", "ordinal": ordinal, "payload": {
                        "answer_event_id": answer["event_id"], "expert": expert,
                        "severity": finding["severity"], "finding_kind": finding["kind"],
                        "file": finding["file"], "line": finding["line"],
                        "claim": finding["claim"], "fix": finding["fix"]}}
                    for ordinal, finding in enumerate(parsed.findings, 1)]
            rows += [{**common, "kind": "finding_status", "ordinal": ordinal, "payload": {
                        "answer_event_id": answer["event_id"],
                        "canonical_id": status["finding_id"], "status": status["status"],
                        "kind_confirm": status.get("kind_confirm")}}
                     for ordinal, status in enumerate(parsed.statuses, 1)]
            for ordinal, note in enumerate(parsed.integrity, 1):
                digest_note = hashlib.sha256(note.encode("utf-8")).hexdigest()
                rows.append({**common, "kind": "integrity", "ordinal": ordinal, "payload": {
                    "conflict_id": digest_note, "conflict_type": "foreign_r2_status",
                    "first_hash": hashlib.sha256(b"ignored").hexdigest(),
                    "new_hash": digest_note}})
            return rows
        try:
            # the answer and everything it says are one write (r6 §2.2, review f227e2ed)
            answer = self.store.append_batch(
                writer=self.store.authority.writer("hook"), refuse=answered,
                first={"kind": "answer", "at": completed_at, "stage": stage,
                       "round": review_round, "attempt": attempt, "payload": answer_payload},
                children=children)[0]
        except StoreError as exc:
            if "already answered" in str(exc):
                self._pending.pop(pending.nonce, None)
                return None   # a concurrent second answer of the same handout
            raise
        self._pending.pop(pending.nonce, None)   # only once the answer is on disk
        self.store.copy_to_session(self.session_id, answer)
        return {"answer": answer, "parsed": parsed}

    def record_usage(self, usage: Mapping[str, Any], *, completed_at: str) -> None:
        values = {
            "input_tokens": int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_tokens") or 0)
                + int(usage.get("cache_creation_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "accounted_calls": int(usage.get("responses") or 0),
        }
        existing = self.store.read_events()
        for name, value in values.items():
            attempt = 1 + sum(event.get("kind") == "metric"
                              and event.get("payload", {}).get("name") == name
                              for event in existing)
            self.store.append_new(
                writer=self.store.authority.writer("hook"), kind="metric",
                at=completed_at, stage="review", attempt=attempt,
                payload={"name": name, "value": value})
            existing = self.store.read_events()


def install_bridge(context: MutableMapping[str, Any], run_id: str,
                   bridge: ReviewHookBridge) -> None:
    """Install one bridge in a session closure; no process-global mutable state."""
    context.setdefault(_CONTEXT_KEY, {})[run_id] = bridge


def bridge_for(context: MutableMapping[str, Any], run_id: str | None) -> ReviewHookBridge | None:
    """Return this session's exact run bridge, never another run's bridge."""
    bridges = context.get(_CONTEXT_KEY)
    return bridges.get(run_id) if isinstance(bridges, dict) and run_id else None


def bridge_for_prompt(context: MutableMapping[str, Any], prompt: str) -> ReviewHookBridge | None:
    """Resolve one installed bridge by signed full-prompt handout identity."""
    bridges = context.get(_CONTEXT_KEY)
    if not isinstance(bridges, dict):
        return None
    matches = [bridge for bridge in bridges.values()
               if isinstance(bridge, ReviewHookBridge) and bridge.matching_handouts(prompt)]
    return matches[0] if len(matches) == 1 else None


def bridge_for_completion(context: MutableMapping[str, Any], prompt_head: str) -> ReviewHookBridge | None:
    """Resolve a completion by its embedded run id; validation stays in record_answer."""
    identity = ReviewHookBridge._decode_binding(prompt_head)
    return bridge_for(context, identity.get("run_id") if identity else None)
