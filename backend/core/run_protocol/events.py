"""Immutable event primitives for Pipeline v1 (v13 design §A3.1).

Public symbols:
- ``make_event`` — build an envelope and content-address large strings.
- ``event_digest`` / ``encode_event`` — canonical hash and bounded JSONL form.
- ``add_event`` — append, deduplicate, or record an integrity conflict.
- ``verify_chain`` — verify event identities, checksums, uniqueness and ``prev_hash`` links.
- ``EventAuthority`` / ``WriterCapability`` — the producer label an event is written
  with. Since S6-3 (design usable-by-anyone §3.3.5) nothing is logged: each event
  carries a sha256 ``checksum`` that finds a torn or damaged record, not a forged one
  (the pipeline does not defend against its own agent, P5).
- ``occurrence_key`` — derive the kind-specific physical-occurrence identity.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from core.run_protocol.identifiers import IdentifierError, validate_run_id

MAX_INLINE_STRING_BYTES = 1_024
MAX_EVENT_LINE_BYTES = 4_096
PRODUCERS = frozenset({"hook", "cli"})

FORCED_BLOB_KEYS = frozenset({"raw", "prompt", "test_details", "decision_json", "hunk",
                              "report_json"})

# Field paths use envelope keys directly and ``payload.<key>`` for payload keys.
OCCURRENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "handout": ("stage", "round", "payload.expert_id", "attempt"),
    "answer": ("payload.tool_use_id",),
    "spawn": ("payload.tool_use_id",),
    "spawn_rejected": ("payload.handout_event_id", "payload.tool_use_id", "payload.reason"),
    "answer_rejected": ("payload.handout_event_id", "payload.tool_use_id", "payload.reason"),
    "finding_occ": ("payload.answer_event_id", "ordinal"),
    "finding_status": ("payload.answer_event_id", "ordinal"),
    "reclassify": ("payload.canonical_id", "payload.pass", "attempt"),
    "address": ("payload.canonical_id", "payload.pass", "attempt"),
    "proof": ("payload.canonical_id", "payload.pass", "attempt"),
    "validation_report": ("ordinal",),
    "stage_attempt": ("stage", "attempt"),
    "check": ("payload.stage_attempt_event_id", "payload.check_id"),
    "transition": ("payload.from", "payload.to", "payload.trigger_event_id"),
    "render": ("stage", "attempt", "payload.document"),
    "render_failed": ("payload.document", "payload.source_tail_event_id"),
    "land_intent": ("payload.stage_attempt_event_id|request_id",),
    "land_request": ("payload.request_id",),
    "auth": ("payload.request_id",),
    "auth_consumed": ("payload.auth_event_id",),
    "decision": ("payload.request_id",),
    "human_answer": ("payload.request_id",),
    "land": ("ordinal",),
    "partial_land": ("ordinal",),
    "abandon": ("ordinal",),
    "landed_as": ("ordinal",),
    "restore": ("ordinal",),
    "knowledge_recalled": ("payload.purpose",),
    "metric": ("stage", "payload.name", "attempt"),
    "turn": ("payload.message_id",),
    "integrity": ("payload.conflict_id", "ordinal"),
}



@dataclass(frozen=True)
class EventSpec:
    """One event kind's trusted producer, occurrence, correlation and payload schema."""

    producer: str | tuple[str, ...]
    occurrence: tuple[str, ...]
    ckey_path: str | None
    required: Mapping[str, type | tuple[type, ...]]
    optional: Mapping[str, type | tuple[type, ...]]


def _spec(producer: str | tuple[str, ...], occurrence: tuple[str, ...], ckey: str | None,
          required: Mapping[str, type | tuple[type, ...]],
          optional: Mapping[str, type | tuple[type, ...]] | None = None) -> EventSpec:
    return EventSpec(producer, occurrence, ckey, required, optional or {})



EVENT_SPECS: dict[str, EventSpec] = {
    "run_create": _spec("cli", ("payload.run_id",), None,
                        {"run_id": str, "profile": str, "decision_cap": int}),
    "handout": _spec("cli", ("stage", "round", "payload.expert_id", "attempt"),
                     "payload.expert_id", {"expert_id": str},
                     # prompt: the full text, always moved into an audit blob (F-4);
                     # code_trees: the trees whose diff the expert was given (S6-2);
                     # incremental_from: an incremental review's previous reviewed trees
                     # nonce: the spawn identity in the prompt's PIPELINE-HANDOUT line
                     # spawn_prompt / spawn_sha: the four lines the expert is spawned
                     # with (PROMPT-FILE = the prompt blob); absent on older handouts
                     # knowledge_items: ids of the DDD entries in the prompt (run-knowledge §3.4)
                     # knowledge_project: the project whose DDD units (knowledge find / get)
                     # a finding may cite besides knowledge_items (knowledge addressing §2.5)
                     # allowed_status: R2 only, {finding id: the words its state takes};
                     # redispatch_of: R2 only, the answer whose wrong words this re-asks
                     {"finding_ids": list, "prompt_sha": str, "prompt": (str, dict),
                      "allowed_status": dict, "redispatch_of": str,
                      "code_trees": dict, "incremental_from": dict, "nonce": str,
                      # bases: each repo's run base under code_trees, so a later
                      # review compares the run's own patch after main moved (design
                      # 2026-10-31 §3 change 3)
                      "bases": dict,
                      # reask_of: the handout whose unreadable answer this re-asks
                      "reask_of": str,
                      "spawn_prompt": str, "spawn_sha": str, "knowledge_items": list, "knowledge_project": str}),
    # a host hook saw an expert spawned with exactly a handout's spawn prompt
    # (standalone §4.3, handout-by-file design §3.2)
    "spawn": _spec("hook", ("payload.tool_use_id",), None,
                   {"handout_event_id": str, "tool_use_id": str}),
    # a spawn that named a handout but was not its spawn prompt, and an expert's answer
    # that did not qualify (first message not the spawn prompt, PROMPT-FILE not read or
    # changed, transcript unreadable): diagnostics only, no check reads them
    "spawn_rejected": _spec("hook", ("payload.handout_event_id", "payload.tool_use_id",
                                     "payload.reason"), None,
                            {"handout_event_id": str, "tool_use_id": str, "reason": str},
                            {"got_sha": str, "got_len": int}),
    "answer_rejected": _spec("hook", ("payload.handout_event_id", "payload.tool_use_id",
                                      "payload.reason"), None,
                             {"handout_event_id": str, "tool_use_id": str, "reason": str},
                             {"detail": str}),   # record_failed: the write's error
    # one answer per handout: the event id derives from the handout, so a second
    # spawn of the same handout can never add a second answer (a retry needs a new
    # handout attempt)
    # one answer per tool call (design §A3.1); one per handout is the bridge's rule,
    # checked under the run lock (F-2)
    # cli: review-answer, where no host hook reads the expert (standalone T6), always
    # with source relayed (the bridge writes both; a run takes relayed answers only when
    # its contract says so)
    "answer": _spec(("hook", "cli"), ("payload.tool_use_id",), "payload.expert_id",
                    {"handout_event_id": str, "prompt_sha": str, "tool_use_id": str,
                     "expert_id": str, "stage": str, "round": int, "state": str}, {"raw": str, "verdict": (str, type(None)),
                     "n_findings": int, "n_status": int,
                     # host: a host hook read it from the expert; relayed: the agent gave it
                     "source": str}),
    "finding_occ": _spec(("hook", "cli"), ("payload.answer_event_id", "ordinal"),
                         "event_id:canonical", {"answer_event_id": str,
                         "expert": str, "severity": str,
                         "finding_kind": str, "file": str, "line": int, "claim": str},
                         # knowledge_ref: a DDD entry the handout carried (§3.4)
                         {"fix": (str, type(None)), "knowledge_ref": (str, type(None))}),
    "finding_status": _spec(("hook", "cli"), ("payload.answer_event_id", "ordinal"),
                            "payload.canonical_id", {"answer_event_id": str,
                            "canonical_id": str, "status": str},
                            # said: for an UNANSWERED line, the word the expert used
                            {"kind_confirm": (str, type(None)), "said": str}),
    "reclassify": _spec("cli", ("payload.canonical_id", "payload.pass", "attempt"),
                        "payload.canonical_id", {"canonical_id": str, "from": str,
                        "to": str, "reason": str, "pass": int}),
    # what the fixer gave for this finding (K4, F21): the hunk (always an audit blob),
    # its cause and proof note — FIXING_SPEC and R2 read them from here only
    "address": _spec("cli", ("payload.canonical_id", "payload.pass", "attempt"),
                     "payload.canonical_id", {"canonical_id": str, "pass": int, "hunk": str},
                     # fix_files: where the fix landed when not (only) in the finding's
                     # own file (design 2026-10-29-pipeline-retro-fixes F3b)
                     {"cause": str, "proof": str, "solution": str, "evidence_plan": str,
                      "fix_files": list}),
    "proof": _spec("cli", ("payload.canonical_id", "payload.pass", "attempt"),
                   "payload.canonical_id", {"canonical_id": str, "pass": int,
                   "evidence_type": str, "code_tree": str, "result": str},
                   {"touches_finding": bool, "touches_executable": bool,
                    "refs": list, "repo": str,
                    # reported_test (S6-2): the report event and the passing command;
                    # n_tests_changed: that report's tests_changed count (an absence
                    # finding needs it above zero; the fold does not read report blobs)
                    "report_event_id": str, "command": str, "n_tests_changed": int,
                    # basis: "file" when a diff_assertion was judged on the file (the R1
                    # and R2 handout trees, design 2026-10-26-pipeline-review-closure §2.3
                    # point 4); absent: the reported line was touched (older runs)
                    "basis": str,
                    # fix_files: a diff_assertion judged on other files than the finding's
                    # own (design 2026-10-29-pipeline-retro-fixes F3b): [{path, blob}]
                    "fix_files": list}),
    # the agent's own test report for one repo (S6-1, design usable-by-anyone §3.3.3):
    # code_tree is computed by the engine at submission, the report itself is a blob
    "validation_report": _spec("cli", ("ordinal",), "payload.repo",
                               {"stage": str, "repo": str, "code_tree": str,
                                "n_pass": int, "n_fail": int, "n_not_run": int,
                                "report_json": (str, dict)}),
    "stage_attempt": _spec("cli", ("stage", "attempt"), "stage",
                           {"check_ids": list}),
    "check": _spec("cli", ("payload.stage_attempt_event_id", "payload.check_id"),
                   "payload.check_id", {"stage_attempt_event_id": str, "check_id": str,
                   "passed": bool, "detail": str},
                   # cause: why a failed check failed, when the fold classifies by it
                   # (lifecycle.CHECK_CAUSES); the detail is text a long one of which is
                   # moved into a blob, so the fold never reads a cause out of it
                   {"cause": str}),
    # the producer of the triggering event signs its transition (the lifecycle decides
    # what it must say; a transition that differs from the expected one never folds)
    "transition": _spec("cli", ("payload.from", "payload.to", "payload.trigger_event_id"),
                        None, {"from": (str, type(None)), "to": str,
                        "trigger_event_id": str}),
    "render": _spec("cli", ("stage", "attempt", "payload.document"),
                    "payload.document", {"document": str, "blob_sha": str}),
    # a Markdown projection could not be written (K4, F23): recorded, never decisive
    "render_failed": _spec("cli", ("payload.document", "payload.source_tail_event_id"),
                           "payload.document",
                           {"document": str, "error": str, "source_tail_event_id": str}),
    "land_intent": _spec("cli", ("payload.stage_attempt_event_id|request_id",), None,
                         {}, {"stage_attempt_event_id": str, "request_id": str}),
    "land_request": _spec("cli", ("payload.request_id",), "payload.request_id",
                          {"request_id": str, "action": str, "binding_digest": str,
                           "request": dict},
                          # landing: what the approval card shows and execute reads (C1)
                          {"guard_attempt_event_id": str, "guard_check_ids": list,
                           "report_sha": str, "bases": dict, "push_reasons": int}),
    "auth": _spec("hook", ("payload.request_id",), "payload.request_id",
                  {"request_id": str, "session_id": str, "run_id": str,
                   "action": str, "binding_digest": str, "expires_at": (int, float)}),
    "auth_consumed": _spec("cli", ("payload.auth_event_id",), "payload.request_id",
                           {"auth_event_id": str, "request_id": str, "session_id": str,
                            "run_id": str, "action": str, "binding_digest": str},
                           {"canonical_id": str, "state_hash": str}),
    "decision": _spec("cli", ("payload.request_id",), "payload.request_id",
                      {"request_id": str, "decision_kind": str, "document": str},
                      # decision_json: the validated decision itself, always a blob:
                      # what the person is asked is what DC-1 validated (conformance
                      # K1). Optional only so a log written before it still folds; such
                      # a decision has no content to ask and DC-1 fails it.
                      {"decision_json": str,
                       # after the decision cap: the agent's evidence-based choice
                       # (DC-2, conformance F12); the run is not handed to a person
                       "decided_by": str, "option_id": str, "evidence_refs": list}),
    "human_answer": _spec("hook", ("payload.request_id",), "payload.request_id",
                          {"request_id": str, "option_id": str}),
    "land": _spec("cli", ("ordinal",), None,
                  {"landing": str, "commit": (str, type(None))},
                  {"reason": str, "stage_attempt_event_id": str,
                   "auth_consumed_event_id": str,
                   # repo -> commit of every repo this landing moved (review r3): the
                   # logged map; progress files are crash recovery only
                   "commits": dict,
                   # shared checkouts whose branch moved but whose index / REPORT
                   # could not be finished (K5 review r2)
                   "unsettled": list}),
    # a land whose fast-forwards raced another session after every repo passed its
    # pre-check (§A3.7, conformance K2): the repos that did move, and the one that failed
    "partial_land": _spec("cli", ("ordinal",), None,
                          {"landed": dict, "failed": str, "detail": str},
                          # exactly one: the normal land attempt, or the person's
                          # consumed authorization, the landing belonged to
                          {"stage_attempt_event_id": str, "auth_consumed_event_id": str,
                           # landed shared checkouts left unfinished (K5 review r3)
                           "unsettled": list}),
    "abandon": _spec("cli", ("ordinal",), None, {"reason": str}),
    # an abandoned run's change is on main all the same, as these commits (repo ->
    # sha), checked by the engine against the run's own change (design 2026-10-31 §3
    # change 4): the run stays abandoned, nobody needs to act on it
    "landed_as": _spec("cli", ("ordinal",), None, {"commits": dict}),
    "restore": _spec("cli", ("ordinal",), None, {"snapshot_exists": bool}),
    # hook: the daemon's required_approval count (D4)
    "metric": _spec(("cli", "hook"), ("stage", "payload.name", "attempt"), "payload.name",
                    {"name": str, "value": (int, float)},
                    # metric(cap_exceeded.<cap>): a count cap of the run was hit (F18);
                    # metric(usage_not_available): why the run's usage was not measured
                    # metric(delivery_untracked): files_blob = blob of {repo: paths} of
                    # the untracked, non-ignored worktree files a landing's code tree took
                    # in (a blob: a path list can pass the event line limit); landing =
                    # that landing's request_id or stage_attempt_event_id
                    {"cap": str, "reason": str, "files_blob": str, "landing": str}),
    "turn": _spec("cli", ("payload.message_id",), None, {"message_id": str}),
    "integrity": _spec(("cli", "hook"), ("payload.conflict_id", "ordinal"),
                       "payload.conflict_id", {"conflict_id": str,
                       "conflict_type": str, "first_hash": str, "new_hash": str}),
    "session_lost": _spec("cli", ("ordinal",), None, {"reason": str}),
    "user_stop": _spec("cli", ("ordinal",), None, {"reason": str}),
    # Ownership moves only by this daemon-written event (run_owner_gate takeover):
    # every human authorization binds to the CURRENT owner, not the creator.
    "owner_transfer": _spec("hook", ("ordinal",), None,
                            {"from_session": str, "to_session": str, "reason": str}),
    # REVIEW's completion records the code it signed off on (S6-2): LG-1 lands only a
    # change whose patch one of these recorded
    "stage_complete": _spec("cli", ("stage", "attempt"), "stage",
                            {"stage_attempt_event_id": str},
                            {"degraded": bool, "code_trees": dict, "patch_ids": dict,
                             "bases": dict}),
    # TEST changed the code: the run goes back to REVIEW for an incremental review
    # (S6-2, design usable-by-anyone §3.3.3); the engine appends it, never the agent
    # doc_paths: repo -> the doc-class paths (``.md/.txt/.rst``) changed since the last
    # reviewed tree when ONLY those changed: the engine completed that REVIEW itself
    # (design 2026-10-26-pipeline-review-closure §5.2 point 1)
    "review_reopen": _spec("cli", ("ordinal",), None, {"reason": str},
                           {"doc_paths": dict, "from": str}),
    "gate0_exhausted": _spec("cli", ("ordinal",), None, {"reason": str}),
    # what THINK / PLAN were given when EVALUATE completed (run-knowledge §3.6.3): the
    # ids knowledge_refs are checked against, and the DDD entries REFLECT dedups with;
    # one per purpose, so a re-completed EVALUATE cannot replace it
    "knowledge_recalled": _spec("cli", ("payload.purpose",), None,
                                {"purpose": str, "status": str, "items": list}, {"ddd": list}),
    "resume": _spec("cli", ("ordinal",), None, {"worktree_ready": bool}),
    # the run's infrastructure failed (conformance F11): the CLI that met it records it
    "infrastructure_failed": _spec("cli", ("ordinal",), None, {"reason": str}),
    "worktree_unavailable": _spec("cli", ("ordinal",), None, {"reason": str}),
    # the daemon's background REFLECT ends REFLECT, never the agent's CLI (D1)
    "reflect_complete": _spec("hook", ("ordinal",), None, {}),
    "reflect_exhausted": _spec("hook", ("ordinal",), None, {"reason": str}),
}
class EventError(ValueError):
    """The event violates the v1 envelope, identity or size contract."""



class EventAuthority:
    """The checksum of one run's event log (S6-3: no key). Every run uses the same
    one; ``authority_id`` names the format, so a log written another way never folds."""

    authority_id = "sha256-checksum-v1"

    def writer(self, producer: str) -> "WriterCapability":
        if producer not in PRODUCERS:
            raise EventError(f"unknown producer {producer!r}")
        return WriterCapability(self, producer)

    def _sign(self, event: Mapping[str, Any]) -> str:
        body = {key: value for key, value in event.items() if key != "checksum"}
        return hashlib.sha256(_canonical(body)).hexdigest()

    def verify(self, event: Mapping[str, Any]) -> bool:
        return event.get("authority_id") == self.authority_id \
            and event.get("checksum") == self._sign(event)


class WriterCapability:
    """The producer label (``cli`` for commands, ``hook`` for what the daemon's hooks
    observed: answers, a person's decisions, owner changes)."""

    __slots__ = ("_authority", "producer")

    def __init__(self, authority: EventAuthority, producer: str):
        if not isinstance(authority, EventAuthority) or producer not in PRODUCERS:
            raise EventError("invalid writer capability")
        self._authority = authority
        self.producer = producer


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EventError(f"value is not canonical JSON: {exc}") from exc




def _blob_ref(value: Any) -> bool:
    return isinstance(value, Mapping) and set(value) == {"blob"} \
        and isinstance(value.get("blob"), str) and len(value["blob"]) == 64


def _matches(value: Any, expected: type | tuple[type, ...]) -> bool:
    types = expected if isinstance(expected, tuple) else (expected,)
    if str in types and _blob_ref(value):
        return True
    if bool in types and isinstance(value, bool):
        return True
    non_bool = tuple(t for t in types if t is not bool)
    return bool(non_bool) and isinstance(value, non_bool) and not (
        isinstance(value, bool) and int in non_bool
    )


def _event_contract(event: Mapping[str, Any], *, allow_missing_ckey: bool) -> str | None:
    kind = event.get("kind")
    spec = EVENT_SPECS.get(kind)
    if spec is None:
        raise EventError(f"unknown event kind {kind!r}")
    allowed_producers = (spec.producer,) if isinstance(spec.producer, str) else spec.producer
    if event.get("producer") not in allowed_producers:
        raise EventError(f"{kind} producer must be {'/'.join(allowed_producers)}")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise EventError("payload must be an object")
    unknown = set(payload) - set(spec.required) - set(spec.optional)
    missing = set(spec.required) - set(payload)
    if unknown:
        raise EventError(f"{kind} payload has unknown fields: {sorted(unknown)}")
    if missing:
        raise EventError(f"{kind} payload lacks fields: {sorted(missing)}")

    for key, expected in {**spec.required, **spec.optional}.items():
        if key in payload and not _matches(payload[key], expected):
            raise EventError(f"{kind} payload.{key} has wrong type")
    if kind == "run_create" and payload["run_id"] != event.get("run_id"):
        raise EventError("run_create payload.run_id must equal envelope run_id")
    if kind == "handout" and event.get("round") == 2:
        ids = payload.get("finding_ids")
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) \
                or any(not isinstance(item, str) or not item.startswith("CF-") for item in ids):
            raise EventError("R2 handout requires unique canonical finding_ids")
    if kind == "validation_report":
        if payload["stage"] not in {"build", "review", "test"}:
            raise EventError("validation_report stage is invalid")
        tree = payload["code_tree"]
        if len(tree) != 40 or any(char not in "0123456789abcdef" for char in tree):
            raise EventError("validation_report code_tree must be a git tree id")
        if min(payload["n_pass"], payload["n_fail"], payload["n_not_run"]) < 0:
            raise EventError("validation_report counts must not be negative")
    if spec.ckey_path == "event_id:canonical":
        expected_ckey = ("CF-" + event["event_id"][:8]) if event.get("event_id") else None
    else:
        expected_ckey = _at_path(dict(event), spec.ckey_path) if spec.ckey_path else None
    if spec.ckey_path == "event_id:canonical" and not event.get("event_id") \
            and allow_missing_ckey:
        return None
    if spec.ckey_path and event.get("ckey") is None and allow_missing_ckey:
        return str(expected_ckey)
    if event.get("ckey") != (str(expected_ckey) if spec.ckey_path else None):
        raise EventError(f"{kind} ckey does not match {spec.ckey_path or 'none'}")
    return event.get("ckey")
def _at_path(envelope: dict[str, Any], path: str) -> Any:
    if path == "payload.stage_attempt_event_id|request_id":
        payload = envelope.get("payload") or {}
        value = payload.get("stage_attempt_event_id") or payload.get("request_id")
        if value is None:
            raise EventError("land_intent requires stage_attempt_event_id or request_id")
        return value
    if path.startswith("payload."):
        value = (envelope.get("payload") or {}).get(path[8:])
    else:
        value = envelope.get(path)
    if value is None or value == "":
        raise EventError(f"{envelope.get('kind', 'event')} occurrence key lacks {path}")
    return value


def occurrence_key(kind: str, envelope: dict[str, Any]) -> str:
    """Return canonical JSON for the kind's physical occurrence (design §A3.1)."""
    spec = EVENT_SPECS.get(kind)
    fields = spec.occurrence if spec else None
    if fields is None:
        raise EventError(f"unknown event kind {kind!r}")
    values = [_at_path(envelope, field) for field in fields]
    if any(isinstance(value, str) and len(value.encode("utf-8")) > MAX_INLINE_STRING_BYTES
           for value in values):
        raise EventError(f"{kind} occurrence field exceeds {MAX_INLINE_STRING_BYTES} bytes")
    return _canonical(values).decode("utf-8")


def _externalize(value: Any, blobs: dict[str, bytes], key: str | None = None) -> Any:
    if isinstance(value, str):
        data = value.encode("utf-8")
        if len(data) > MAX_INLINE_STRING_BYTES or key in FORCED_BLOB_KEYS:
            digest = hashlib.sha256(data).hexdigest()
            blobs.setdefault(digest, data)
            return {"blob": digest}
        return value
    if isinstance(value, list):
        return [_externalize(item, blobs) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise EventError("payload object keys must be strings")
        return {k: _externalize(v, blobs, k) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise EventError(f"payload contains unsupported {type(value).__name__}")


def externalize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return a copied payload plus ``sha256 -> bytes`` blobs; never writes to disk."""
    if not isinstance(payload, dict):
        raise EventError("payload must be an object")
    blobs: dict[str, bytes] = {}
    return _externalize(payload, blobs), blobs


def encode_event(event: dict[str, Any]) -> bytes:
    """Canonical single-line JSON; reject events over the 4 KB protocol limit."""
    data = _canonical(event)
    if len(data) > MAX_EVENT_LINE_BYTES:
        raise EventError(
            f"event line is {len(data)} bytes; maximum is {MAX_EVENT_LINE_BYTES}"
        )
    return data + b"\n"


def event_digest(event: dict[str, Any]) -> str:
    """Hash an event's complete canonical envelope (without the JSONL newline)."""
    return hashlib.sha256(_canonical(event)).hexdigest()


def expected_event_id(event: dict[str, Any]) -> str:
    """Recompute the event id from run, kind and kind-specific occurrence key."""
    run_id = event.get("run_id")
    kind = event.get("kind")
    try:
        run_id = validate_run_id(run_id)
    except IdentifierError as exc:
        raise EventError(str(exc)) from exc
    if not isinstance(kind, str):
        raise EventError("kind is required")
    key = occurrence_key(kind, event)
    return hashlib.sha256(f"{run_id}|{kind}|{key}".encode("utf-8")).hexdigest()


def validate_event(event: dict[str, Any], authority: EventAuthority) -> None:
    """Reject an incomplete, damaged or oversized event before persistence."""
    if not isinstance(authority, EventAuthority):
        raise EventError("event authority is required")
    if not isinstance(event, dict):
        raise EventError("event must be an object")
    if event.get("producer") not in PRODUCERS:
        raise EventError(f"unknown producer {event.get('producer')!r}")
    if not isinstance(event.get("at"), str) or not event.get("at"):
        raise EventError("at is required")
    if not isinstance(event.get("payload"), dict):
        raise EventError("payload must be an object")
    _event_contract(event, allow_missing_ckey=False)
    if event.get("event_id") != expected_event_id(event):
        raise EventError("event_id mismatch")
    if not authority.verify(event):
        raise EventError("event checksum mismatch (a damaged record)")
    encode_event(event)


def make_event(
    *,
    writer: WriterCapability,
    run_id: str,
    kind: str,
    at: str,
    payload: Any,
    stage: str | None = None,
    round: int | None = None,
    attempt: int | None = None,
    ordinal: int | None = None,
    ckey: str | None = None,
    prev_hash: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build one validated envelope and return it with blobs not yet persisted."""
    try:
        run_id = validate_run_id(run_id)
    except IdentifierError as exc:
        raise EventError(str(exc)) from exc
    if not isinstance(writer, WriterCapability):
        raise EventError("writer capability is required")
    producer = writer.producer
    authority = writer._authority
    if kind == "auth_consumed":
        from core.run_protocol.auth import Consumption
        if not isinstance(payload, Consumption):
            raise EventError("auth_consumed payload requires an oracle Consumption")
        payload = payload._event_payload()
    elif not isinstance(payload, dict):
        raise EventError("payload must be an object")
    if not isinstance(at, str) or not at:
        raise EventError("at is required")
    raw = {
        "run_id": run_id, "kind": kind, "stage": stage, "round": round,
        "attempt": attempt, "ordinal": ordinal, "at": at, "producer": producer,
        "ckey": ckey, "payload": copy.deepcopy(payload), "prev_hash": prev_hash,
    }
    raw["ckey"] = _event_contract(raw, allow_missing_ckey=True)
    key = occurrence_key(kind, raw)
    event_id = hashlib.sha256(f"{run_id}|{kind}|{key}".encode("utf-8")).hexdigest()
    if EVENT_SPECS[kind].ckey_path == "event_id:canonical":
        raw["event_id"] = event_id
        raw["ckey"] = "CF-" + event_id[:8]
        _event_contract(raw, allow_missing_ckey=False)
    cooked, blobs = externalize_payload(payload)
    event = {"event_id": event_id, **raw, "payload": cooked,
             "authority_id": authority.authority_id}
    event["checksum"] = authority._sign(event)
    validate_event(event, authority)
    return event, blobs


def add_event(
    authority: EventAuthority,
    events: list[dict[str, Any]],
    event: dict[str, Any],
    *,
    integrity_ordinal: int = 1,
    detected_by: str = "cli",
) -> tuple[list[dict[str, Any]], str, dict[str, bytes]]:
    """Pure append: return ``(events, appended|duplicate|integrity, blobs)``.

    The same event id and content is a replay. Different content under an existing id
    keeps the first event and appends an integrity event; an ordinary new event must
    link to the current tail.
    """
    if events:
        errors = verify_chain(events, authority)
        if errors:
            raise EventError(f"existing event chain is invalid: {errors[0]}")
    validate_event(event, authority)
    out = copy.deepcopy(events)
    same = next((old for old in out if old.get("event_id") == event.get("event_id")), None)
    if same is not None:
        if _canonical(same) == _canonical(event):
            return out, "duplicate", {}
        integrity, blobs = make_event(
            writer=authority.writer(detected_by),
            run_id=str(event.get("run_id") or same.get("run_id") or ""),
            kind="integrity", at=str(event.get("at") or ""),
            stage=event.get("stage"), ordinal=integrity_ordinal,
            ckey=str(event.get("event_id") or ""),
            prev_hash=event_digest(out[-1]) if out else None,
            payload={
                "conflict_id": str(event.get("event_id") or ""),
                "conflict_type": "event_id_content",
                "first_hash": event_digest(same), "new_hash": event_digest(event),
            },
        )
        if any(old.get("event_id") == integrity["event_id"] for old in out):
            raise EventError(f"integrity ordinal {integrity_ordinal} already used")
        out.append(integrity)
        return out, "integrity", blobs
    expected = event_digest(out[-1]) if out else None
    if event.get("prev_hash") != expected:
        raise EventError(f"prev_hash mismatch: expected {expected!r}")
    encode_event(event)
    out.append(copy.deepcopy(event))
    return out, "appended", {}


def verify_chain(events: list[dict[str, Any]], authority: EventAuthority) -> list[str]:
    """Return every identity/size/link error; an empty list means a valid chain."""
    errors: list[str] = []
    seen: set[str] = set()
    previous: dict[str, Any] | None = None
    for index, event in enumerate(events):
        prefix = f"event[{index}]"
        try:
            validate_event(event, authority)
        except EventError as exc:
            errors.append(f"{prefix}: {exc}")
        event_id = event.get("event_id")
        if event_id in seen:
            errors.append(f"{prefix}: duplicate event_id")
        if isinstance(event_id, str):
            seen.add(event_id)
        expected_prev = event_digest(previous) if previous is not None else None
        if event.get("prev_hash") != expected_prev:
            errors.append(f"{prefix}: prev_hash mismatch")
        previous = event
    return errors
