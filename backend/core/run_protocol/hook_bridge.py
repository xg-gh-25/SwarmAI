"""Bind review experts' answers to Pipeline v1 handouts, from any host's hooks.

Every handout's prompt carries ``PIPELINE-HANDOUT: <run_id> <nonce>`` (second line;
``review.ReviewOrchestrator.handout`` writes the nonce into the handout event). A host
hook hands the facts it saw to these functions; nothing is kept in a session's memory,
so a hook in another process (Claude Code, Codex, Kiro, a SwarmAI session) binds the
same way (design 2026-10-25-pipeline-standalone §4.3):

- ``bind_spawn`` — a PreToolUse saw an expert spawned with exactly a handout's prompt:
  a ``spawn`` event records the tool call.
- ``record_answer`` — the expert's final text, with the prompt the host gave it. The
  prompt may be only the head of the expert's transcript (a long prompt is read
  bounded), so it needs a ``spawn`` for the handout; with ``exact=True`` (a host
  that hands the full spawn prompt and the result together, Kiro's PostToolUse) the
  prompt must equal the handout's. One answer per handout, under the run lock.
- ``record_usage`` — an expert's own token usage, measured from its transcript.
- ``parse_binding`` / ``handout_line`` / ``store_for`` — the line, and the run it names.
- ``on_spawn`` / ``on_subagent_stop`` — what every host's hook calls (a SwarmAI
  session's in-process hooks and ``python-backend hook``): resolve the run, then bind
  or record.

Public symbols: ``handout_line``, ``parse_binding``, ``prompt_sha``, ``store_for``,
``bind_spawn``, ``record_answer``, ``record_usage``, ``on_spawn``, ``on_subagent_stop``.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from core.run_protocol.answers import parse_answer
from core.run_protocol.store import AuditStore, StoreError

_LINE = re.compile(r"^PIPELINE-HANDOUT: (run_[A-Za-z0-9_-]{1,64}) ([0-9a-f]{32})\s*$",
                   re.MULTILINE)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def handout_line(run_id: str, nonce: str) -> str:
    return f"PIPELINE-HANDOUT: {run_id} {nonce}"


def parse_binding(text: str) -> "tuple[str, str] | None":
    """(run_id, nonce) of the one PIPELINE-HANDOUT line in *text*, else None."""
    found = _LINE.findall(text or "") if isinstance(text, str) else []
    return found[0] if len(found) == 1 else None


def prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def store_for(text: str) -> "tuple[AuditStore, str] | None":
    """The run named by *text*'s binding line, as (its audit store, its owner session),
    from this backend's data dir and workspace. None when it names no readable run."""
    binding = parse_binding(text)
    if binding is None:
        return None
    from config import get_app_data_dir
    from core.run_protocol.contract import read_contract
    from core.run_protocol.engine import RunEngine
    data = Path(get_app_data_dir())
    contract = read_contract(data, binding[0])
    if not isinstance(contract, dict) or not contract.get("project"):
        return None
    import os
    workspace = Path(os.environ.get("SWARM_WORKSPACE") or data / "SwarmWS")
    engine = RunEngine.load(data_dir=data, workspace=workspace,
                            project=str(contract["project"]), run_id=binding[0])
    return engine.store, engine.owner_session()


def _handout(store: AuditStore, text: str) -> "dict[str, Any] | None":
    binding = parse_binding(text)
    if binding is None or binding[0] != store.paths.run_id:
        return None
    rows = [row for row in store.read_events() if row.get("kind") == "handout"
            and (row.get("payload") or {}).get("nonce") == binding[1]]
    return rows[0] if len(rows) == 1 else None


def _spawned(store: AuditStore, handout_id: str) -> "str | None":
    """The tool call a host hook bound to *handout_id* (its first ``spawn``), else None."""
    rows = [row for row in store.read_events() if row.get("kind") == "spawn"
            and (row.get("payload") or {}).get("handout_event_id") == handout_id]
    return rows[0]["payload"]["tool_use_id"] if rows else None


def bind_spawn(store: AuditStore, prompt: str, tool_use_id: str) -> "dict[str, Any] | None":
    """Record that *tool_use_id* spawns an expert with exactly a handout's prompt.
    Anything else (another prompt, a changed one, no tool call id) binds nothing."""
    if not isinstance(prompt, str) or not tool_use_id:
        return None
    handout = _handout(store, prompt)
    if handout is None or handout["payload"].get("prompt_sha") != prompt_sha(prompt):
        return None
    try:
        event, _ = store.append_new(
            writer=store.authority.writer("hook"), kind="spawn", at=_now(),
            stage=handout["stage"], round=handout["round"], attempt=handout["attempt"],
            payload={"handout_event_id": handout["event_id"], "tool_use_id": str(tool_use_id)})
    except StoreError as exc:
        if "already" in str(exc) or "duplicate" in str(exc):
            return None          # the same tool call seen twice
        raise
    return event


def record_answer(store: AuditStore, prompt_text: str, final_text: str, *,
                  completed_at: str, exact: bool = False, tool_use_id: str | None = None,
                  session_id: str | None = None) -> "dict[str, Any] | None":
    """Persist an expert's answer for the handout *prompt_text* names (module docstring).
    The answer and its findings are one operation on the run (r6 §2.2)."""
    from core.run_protocol.oplock import operation
    # every other holder is a CLI call bounded by its Bash call, and a flock dies with
    # its process: this wait ends with the lock; the answer is never dropped for a busy
    # run (review f227e2ed)
    with operation(store.paths.root, store.paths.run_id, timeout=900.0):
        return _record(store, prompt_text, final_text, completed_at=completed_at,
                       exact=exact, tool_use_id=tool_use_id, session_id=session_id)


def _record(store, prompt_text, final_text, *, completed_at, exact, tool_use_id, session_id):
    handout = _handout(store, prompt_text)
    if handout is None:
        return None
    payload = handout["payload"]
    if exact:
        if payload.get("prompt_sha") != prompt_sha(prompt_text):
            return None
        call = str(tool_use_id or f"nonce:{payload['nonce']}")
    else:
        call = _spawned(store, handout["event_id"])
        if call is None:
            return None          # no host saw this spawn with the exact prompt
    expert, review_round = payload["expert_id"], handout["round"]
    parsed = parse_answer(final_text, expert_id=expert, round=review_round,
                          finding_ids=payload.get("finding_ids", ()))
    answer_payload = {
        "handout_event_id": handout["event_id"], "prompt_sha": payload["prompt_sha"],
        "tool_use_id": call, "expert_id": expert, "stage": handout["stage"],
        "round": review_round, **parsed.answer_payload, "raw": final_text, "source": "host",
    }
    handout_id = handout["event_id"]

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
        answer = store.append_batch(
            writer=store.authority.writer("hook"), refuse=answered,
            first={"kind": "answer", "at": completed_at, "stage": stage,
                   "round": review_round, "attempt": attempt, "payload": answer_payload},
            children=children)[0]
    except StoreError as exc:
        if "already answered" in str(exc) or "already recorded" in str(exc):
            return None   # a second answer of the same handout
        raise
    if session_id:
        store.copy_to_session(session_id, answer)
    return {"answer": answer, "parsed": parsed}


def record_usage(store: AuditStore, usage: Mapping[str, Any], *, completed_at: str) -> None:
    values = {
        "input_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_tokens") or 0)
            + int(usage.get("cache_creation_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "accounted_calls": int(usage.get("responses") or 0),
    }
    existing = store.read_events()
    for name, value in values.items():
        attempt = 1 + sum(event.get("kind") == "metric"
                          and event.get("payload", {}).get("name") == name
                          for event in existing)
        store.append_new(
            writer=store.authority.writer("hook"), kind="metric",
            at=completed_at, stage="review", attempt=attempt,
            payload={"name": name, "value": value})
        existing = store.read_events()


def on_spawn(prompt: str, tool_use_id: str) -> bool:
    """A host's PreToolUse on an agent spawn: bind it when its prompt is a handout's."""
    if not isinstance(prompt, str) or "PIPELINE-HANDOUT: " not in prompt:
        return False
    found = store_for(prompt)
    return found is not None and bind_spawn(found[0], prompt, tool_use_id) is not None


def on_subagent_stop(prompt_head: str, final_text: str, *,
                     transcript_path: str | None = None,
                     completed_at: str | None = None) -> "dict[str, Any] | None":
    """A host's SubagentStop: record the expert's answer (copied to the run owner's
    session stream), then the expert's own token usage from its transcript."""
    found = store_for(prompt_head) if prompt_head else None
    if found is None:
        return None
    store, owner = found
    at = completed_at or _now()
    recorded = record_answer(store, prompt_head, final_text, completed_at=at,
                             session_id=owner)
    if recorded is not None and transcript_path:
        try:
            from core import transcript_usage
            measured = transcript_usage.usage_for_subagent(transcript_path)
            if measured is not None:
                record_usage(store, measured, completed_at=at)
        except Exception:  # noqa: BLE001 — usage is optional accounting
            import logging
            logging.getLogger(__name__).warning("v1 subagent usage recording failed",
                                                exc_info=True)
    return recorded
