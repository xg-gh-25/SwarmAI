"""Bind review experts' answers to Pipeline v1 handouts, from any host's hooks.

Every handout's prompt carries ``PIPELINE-HANDOUT: <run_id> <nonce>`` (second line;
``review.ReviewOrchestrator.handout`` writes the nonce into the handout event). A host
hook hands the facts it saw to these functions; nothing is kept in a session's memory,
so a hook in another process (Claude Code, Codex, Kiro, a SwarmAI session) binds the
same way (design 2026-10-25-pipeline-standalone §4.3):

- ``bind_spawn`` — a PreToolUse saw an expert spawned with exactly a handout's spawn
  prompt (the four lines naming PROMPT-FILE; the full prompt for a handout written
  before the file form; one missing final newline tolerated): a ``spawn`` event
  records the tool call. A prompt naming a pending handout but differing leaves a
  ``spawn_rejected`` with its reason.
- ``record_answer`` — the expert's final text, with the prompt the host gave it. The
  prompt may be only the head of the expert's transcript (a long prompt is read
  bounded), so it needs a ``spawn`` for the handout; with ``exact=True`` (a host
  that hands the full prompt and the result together, Kiro's PostToolUse) the
  prompt must equal the handout's full prompt. One answer per handout, under the run
  lock. It does not judge the transcript: ``on_subagent_stop`` does, before calling it.
- ``record_relayed`` — the same answer handed over by the agent where no host hook
  reads it (standalone T6 ``review-answer``): ``source: relayed``, producer ``cli``.
- ``record_usage`` — an expert's own token usage, measured from its transcript.
- ``parse_binding`` / ``handout_line`` / ``store_for`` — the line, and the run it names.
- ``on_spawn`` / ``on_subagent_stop`` — what every host's hook calls (a SwarmAI
  session's in-process hooks and ``python-backend hook``): resolve the run, then bind
  or record. ``on_subagent_stop`` records an answer only from a sub-agent whose first
  message is the bound spawn text, that read PROMPT-FILE and was told nothing else
  after it was spawned; otherwise an ``answer_rejected`` says why (handout-by-file
  design §3.3). The transcript is the host's file: a sub-agent that rewrites its own
  transcript is not caught here (known gap, see the design). An R2 status line the
  finding ledger cannot apply to its finding (ADEQUATE on an open finding) is left out
  as an ``invalid_r2_status`` integrity note and the rest of the answer is recorded; an
  answer whose write still fails leaves ``answer_rejected`` with ``record_failed``.

Public symbols: ``handout_line``, ``parse_binding``, ``prompt_sha``, ``store_for``,
``bind_spawn``, ``record_answer``, ``record_relayed``, ``record_usage``, ``on_spawn``, ``on_subagent_stop``.
"""
from __future__ import annotations

import hashlib
import os
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
    from config import get_swarmws
    workspace = get_swarmws()
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
    """The tool call a host hook bound to *handout_id* (its latest ``spawn``: after a
    refused answer the handout is dispatched again), else None."""
    rows = [row for row in store.read_events() if row.get("kind") == "spawn"
            and (row.get("payload") or {}).get("handout_event_id") == handout_id]
    return rows[-1]["payload"]["tool_use_id"] if rows else None


_REJECT_CAP = 3   # rejection events kept per handout and kind (diagnostics, not a log flood)


def _expected_sha(payload: Mapping[str, Any]) -> "str | None":
    """The sha a spawn of this handout must have: its four-line spawn prompt, or the full
    prompt for a handout written before the file form."""
    return payload.get("spawn_sha") or payload.get("prompt_sha")


def _matches(payload: Mapping[str, Any], prompt: str) -> bool:
    """*prompt* is the handout's spawn text, allowing only ONE missing final newline
    (a host or a copy can drop it; nothing else is tolerated)."""
    expected = _expected_sha(payload)
    return expected in (prompt_sha(prompt), prompt_sha(prompt + "\n"))


def _reject(store: AuditStore, kind: str, handout: Mapping[str, Any], tool_use_id: str,
            reason: str, **extra: Any) -> None:
    """Leave a reason for a refused spawn / answer (handout-by-file design §3.2-3.3):
    at most _REJECT_CAP per handout and kind, none once the handout is answered."""
    from core.run_protocol.events import EventError
    from core.run_protocol.oplock import operation
    hid = handout["event_id"]
    try:
        # count and append under the run lock, so parallel hooks keep the cap
        with operation(store.paths.root, store.paths.run_id):
            mine = [e for e in store.read_events()
                    if (e.get("payload") or {}).get("handout_event_id") == hid]
            if any(e.get("kind") == "answer" for e in mine) or \
                    sum(e.get("kind") == kind for e in mine) >= _REJECT_CAP:
                return
            store.append_new(writer=store.authority.writer("hook"), kind=kind, at=_now(),
                             stage=handout["stage"], round=handout["round"],
                             attempt=handout["attempt"],
                             payload={"handout_event_id": hid,
                                      "tool_use_id": str(tool_use_id)[:128],
                                      "reason": reason, **extra})
    except (StoreError, EventError):
        pass        # the same rejection already recorded, or a malformed host id


def bind_spawn(store: AuditStore, prompt: str, tool_use_id: str) -> "dict[str, Any] | None":
    """Record that *tool_use_id* spawns an expert with exactly a handout's spawn prompt
    (only a missing final newline is tolerated). A prompt that names a pending handout
    but differs leaves a ``spawn_rejected``; anything else binds nothing."""
    if not isinstance(prompt, str) or not tool_use_id:
        return None
    handout = _handout(store, prompt)
    if handout is None:
        return None
    if not _matches(handout["payload"], prompt):
        _reject(store, "spawn_rejected", handout, tool_use_id, "prompt_differs",
                got_sha=prompt_sha(prompt), got_len=len(prompt))
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
        # the host hands the full prompt it spawned with (main's rule, unchanged)
        if payload.get("prompt_sha") != prompt_sha(prompt_text):
            return None
        call = str(tool_use_id or f"nonce:{payload['nonce']}")
    else:
        call = _spawned(store, handout["event_id"])
        if call is None:
            return None          # no host saw this spawn with the exact prompt
    return _write(store, handout, final_text, call=call, source="host", producer="hook",
                  completed_at=completed_at, session_id=session_id)


def record_relayed(store: AuditStore, handout_event_id: str, final_text: str, *,
                   completed_at: str) -> "dict[str, Any] | None":
    """The agent hands over an expert's reply where no host hook reads it (standalone
    T6, ``review-answer``): the same answer and findings a hook writes, with
    ``source: relayed`` and producer ``cli``. The caller holds the run's operation lock
    and has checked the run takes relayed answers. None: no such handout in this run,
    or it is already answered."""
    handout = next((e for e in store.read_events() if e.get("kind") == "handout"
                    and e.get("event_id") == handout_event_id), None)
    if handout is None:
        return None
    return _write(store, handout, final_text, call=f"relayed:{handout['payload']['nonce']}",
                  source="relayed", producer="cli", completed_at=completed_at,
                  session_id=None)


def _write(store, handout, final_text, *, call, source, producer, completed_at, session_id):
    payload = handout["payload"]
    expert, review_round = payload["expert_id"], handout["round"]
    parsed = parse_answer(final_text, expert_id=expert, round=review_round,
                          finding_ids=payload.get("finding_ids", ()))
    answer_payload = {
        "handout_event_id": handout["event_id"], "prompt_sha": payload["prompt_sha"],
        "tool_use_id": call, "expert_id": expert, "stage": handout["stage"],
        "round": review_round, **parsed.answer_payload, "raw": final_text, "source": source,
    }
    handout_id = handout["event_id"]

    def answered(chain):
        return ("this handout is already answered" if any(
            row.get("kind") == "answer"
            and row.get("payload", {}).get("handout_event_id") == handout_id
            for row in chain) else None)
    stage, attempt = handout["stage"], handout["attempt"]
    # a status line the ledger cannot apply to its finding's current state (ADEQUATE on
    # an open finding) is dropped and noted, so the rest of the answer still counts
    # instead of the whole write being refused (ced6caa3)
    statuses, notes = _applicable_statuses(store, parsed, payload.get("allowed_status"))

    def children(answer: dict[str, Any]) -> list[dict[str, Any]]:
        if parsed.state not in ("parsed", "partial"):
            return []   # an unparsed answer is no usable output (§A3.2)
        common = {"at": completed_at, "stage": stage, "round": review_round,
                  "attempt": attempt}
        carried = set(payload.get("knowledge_items") or ())

        def _occ(finding: Mapping[str, Any]) -> dict[str, Any]:
            row = {"answer_event_id": answer["event_id"], "expert": expert,
                   "severity": finding["severity"], "finding_kind": finding["kind"],
                   "file": finding["file"], "line": finding["line"],
                   "claim": finding["claim"], "fix": finding["fix"]}
            # a citation counts only for an entry this handout carried (§3.4)
            if finding.get("knowledge_ref") in carried:
                row["knowledge_ref"] = finding["knowledge_ref"]
            return row
        rows = [{**common, "kind": "finding_occ", "ordinal": ordinal, "payload": _occ(finding)}
                for ordinal, finding in enumerate(parsed.findings, 1)]
        rows += [{**common, "kind": "finding_status", "ordinal": ordinal, "payload": {
                    "answer_event_id": answer["event_id"],
                    "canonical_id": status["finding_id"], "status": status["status"],
                    "kind_confirm": status.get("kind_confirm"),
                    **({"said": status["said"]} if status.get("said") else {})}}
                 for ordinal, status in enumerate(statuses, 1)]
        for ordinal, (note, conflict) in enumerate(notes, 1):
            # bound to this answer: a re-dispatch answered with the same wrong word
            # writes its own note, not a duplicate event
            digest_note = hashlib.sha256(
                (answer["event_id"] + "\n" + note).encode("utf-8")).hexdigest()
            rows.append({**common, "kind": "integrity", "ordinal": ordinal, "payload": {
                "conflict_id": digest_note, "conflict_type": conflict,
                "first_hash": hashlib.sha256(b"ignored").hexdigest(),
                "new_hash": digest_note}})
        return rows
    try:
        # the answer and everything it says are one write (r6 §2.2, review f227e2ed)
        answer = store.append_batch(
            writer=store.authority.writer(producer), refuse=answered,
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


def _applicable_statuses(store: AuditStore, parsed,
                         allowed_status: "Mapping[str, Any] | None" = None
                         ) -> "tuple[list, list]":
    """(*parsed*'s R2 status rows as the ledger records them, integrity notes as
    ``(text, conflict_type)``). A row naming a finding the ledger does not hold is kept:
    the ledger's own refusal of it is unchanged.

    Which words a finding takes comes from its R2 handout's ``allowed_status`` (the
    state when the handout was written), else from its current state (a handout written
    before that field existed). A word it does not take: ``OPEN`` for a test-proven
    finding is recorded as TEST_INADEQUATE (stricter, never a release); any other is
    recorded as UNANSWERED (the state stays, the finding counts as answered, ``next``
    re-dispatches once) with an integrity note (design 2026-10-26-pipeline-review-
    closure §2.3 point 3)."""
    notes = [(note, "foreign_r2_status") for note in parsed.integrity]
    if not parsed.statuses:
        return [], notes
    from core.run_protocol import findings
    try:
        # the state this answer's status lines apply to: before the "R2 silence is
        # OPEN" rule, which would count this very answer's targets as unanswered
        current = {row["canonical_id"]: row["status"]
                   for row in findings.fold(store.read_events(), store.authority,
                                            r2_silence=False)}
    except findings.FindingError:
        # a log the ledger cannot fold refuses every finding write anyway (the engine's
        # store validator); nothing to filter against, the write reports the error
        current = {}
    kept = []
    for status in parsed.statuses:
        finding_id = status["finding_id"]
        held = current.get(finding_id)
        allowed = set(findings.r2_allowed(held)) if held is not None else None
        if isinstance(allowed_status, Mapping) and isinstance(allowed_status.get(finding_id), list):
            # the handout's judgement; a state that moved on since (an accept) narrows it
            handed = {str(word).upper() for word in allowed_status[finding_id]}
            allowed = handed if allowed is None else handed & allowed
        if allowed is None:
            kept.append(status)
            continue
        word = str(status["status"]).upper()
        if word in allowed:
            kept.append(status)
        elif word == "OPEN" and "TEST_INADEQUATE" in allowed:
            notes.append((f"R2 status OPEN for the test-proven {finding_id} recorded as "
                          "TEST_INADEQUATE", "invalid_r2_status"))
            kept.append({**status, "status": "TEST_INADEQUATE"})
        else:
            notes.append((f"R2 status {word} is not allowed for {finding_id} "
                          f"(allowed: {', '.join(sorted(allowed))}); recorded as "
                          f"{findings.R2_UNANSWERED}", "invalid_r2_status"))
            kept.append({**status, "status": findings.R2_UNANSWERED, "kind_confirm": None,
                         "said": word})
    return kept, notes


def record_usage(store: AuditStore, usage: Mapping[str, Any], *, completed_at: str) -> None:
    values = {
        # cache reads apart (as usage.measure): runs compare without them
        "input_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
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


def _prompt_file(store: AuditStore, payload: Mapping[str, Any]) -> "str | None":
    """The file a file-form handout's expert must read: the prompt's own audit blob."""
    if not payload.get("spawn_sha") or not payload.get("prompt_sha"):
        return None
    return os.path.realpath(store.paths.blobs / str(payload["prompt_sha"]))


def _refusal(store: AuditStore, handout: Mapping[str, Any],
             transcript_path: "str | None") -> "str | None":
    """Why this sub-agent's answer may not be recorded for *handout*, or None
    (handout-by-file design §3.3): its first message must be the spawn text the
    handout's spawn was bound with, and a file-form expert must have read PROMPT-FILE
    from the top, the file still being the prompt the handout names."""
    from core import subagent_transcript
    payload = handout["payload"]
    prompt_file = _prompt_file(store, payload)
    evidence = (subagent_transcript.spawn_evidence(transcript_path, prompt_file)
                if transcript_path else None)
    if evidence is None:
        return "transcript_unreadable"
    if _expected_sha(payload) not in evidence["first_shas"]:
        return "spawn_prompt_differs"
    if evidence["told_after_spawn"]:
        return "instructed_after_spawn"
    if prompt_file is not None:
        if not evidence["read_ok"]:
            return "prompt_file_not_read"
        try:
            data = Path(prompt_file).read_bytes()
        except OSError:
            return "prompt_file_changed"
        if hashlib.sha256(data).hexdigest() != payload["prompt_sha"]:
            return "prompt_file_changed"
    return None


def on_subagent_stop(prompt_head: str, final_text: str, *,
                     transcript_path: str | None = None,
                     completed_at: str | None = None) -> "dict[str, Any] | None":
    """A host's SubagentStop: record the expert's answer (copied to the run owner's
    session stream), then the expert's own token usage from its transcript. An answer
    that does not qualify (``_refusal``) is not recorded; an ``answer_rejected`` says
    why, and the handout can be dispatched again."""
    found = store_for(prompt_head) if prompt_head else None
    if found is None:
        return None
    store, owner = found
    at = completed_at or _now()
    handout = _handout(store, prompt_head)
    if handout is None:
        return None
    reason = _refusal(store, handout, transcript_path)
    if reason is not None:
        who = (f"transcript:{Path(transcript_path).name}" if transcript_path
               else "transcript:none")
        _reject(store, "answer_rejected", handout, who, reason)
        return None
    from core.run_protocol.findings import FindingError
    try:
        recorded = record_answer(store, prompt_head, final_text, completed_at=at,
                                 session_id=owner)
    except (StoreError, FindingError) as exc:
        # the answer could not be written: say so on the run, never drop it silently
        import logging
        logging.getLogger(__name__).warning("v1 answer not recorded: %s", exc)
        _reject(store, "answer_rejected", handout, f"handout:{handout['event_id']}",
                "record_failed", detail=str(exc)[:300])
        return None
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
