"""Immutable event primitives for Pipeline v1 (v13 design §A3.1).

Public symbols:
- ``make_event`` — build an envelope and content-address large strings.
- ``event_digest`` / ``encode_event`` — canonical hash and bounded JSONL form.
- ``add_event`` — append, deduplicate, or record an integrity conflict.
- ``verify_chain`` — verify event identities, uniqueness and ``prev_hash`` links.
- ``occurrence_key`` — derive the kind-specific physical-occurrence identity.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping

from core.run_protocol.identifiers import IdentifierError, validate_run_id

MAX_INLINE_STRING_BYTES = 1_024
MAX_EVENT_LINE_BYTES = 4_096
PRODUCERS = frozenset({"hook", "cli", "runner"})

# Which writers THIS process may hold (fixing spec C3). The hook writer exists only in
# the daemon (main.py calls allow_hook_writer at import; the test process does too, as
# it plays the daemon); the CLI and the runner narrow themselves to their own writer
# at entry. Narrowing is one-way. The signing key itself is a separate design (§9).
_hook_writer_allowed = False
_process_producers: frozenset[str] = frozenset({"cli", "runner"})


def allow_hook_writer() -> None:
    """The daemon process: it alone records hook facts (answers, approvals, owner)."""
    global _hook_writer_allowed, _process_producers
    if _process_producers != frozenset({"cli", "runner"}) and "hook" not in _process_producers:
        raise EventError("a process narrowed to its own writer cannot become the daemon")
    _hook_writer_allowed = True
    _process_producers = _process_producers | {"hook"}


def restrict_process(*producers: str) -> None:
    """A CLI / runner process: from now on only *producers* may be obtained."""
    global _hook_writer_allowed, _process_producers
    wanted = frozenset(producers)
    if not wanted or not wanted <= PRODUCERS:
        raise EventError("restrict_process needs known producers")
    _process_producers = _process_producers & wanted
    _hook_writer_allowed = _hook_writer_allowed and "hook" in _process_producers
FORCED_BLOB_KEYS = frozenset({"raw", "prompt", "test_details", "decision_json", "hunk"})

# Field paths use envelope keys directly and ``payload.<key>`` for payload keys.
OCCURRENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "handout": ("stage", "round", "payload.expert_id", "attempt"),
    "answer": ("payload.tool_use_id",),
    "finding_occ": ("payload.answer_event_id", "ordinal"),
    "finding_status": ("payload.answer_event_id", "ordinal"),
    "reclassify": ("payload.canonical_id", "payload.pass", "attempt"),
    "address": ("payload.canonical_id", "payload.pass", "attempt"),
    "proof": ("payload.canonical_id", "payload.pass", "attempt"),
    "test_call": ("payload.seq",),
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
    "restore": ("ordinal",),
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
                     # prompt: the full text, always moved into an audit blob (F-4)
                     {"finding_ids": list, "prompt_sha": str, "prompt": (str, dict)}),
    # one answer per handout: the event id derives from the handout, so a second
    # spawn of the same handout can never add a second answer (a retry needs a new
    # handout attempt)
    # one answer per tool call (design §A3.1); one per handout is the bridge's rule,
    # checked under the run lock (F-2)
    "answer": _spec("hook", ("payload.tool_use_id",), "payload.expert_id",
                    {"handout_event_id": str, "prompt_sha": str, "tool_use_id": str,
                     "expert_id": str, "stage": str, "round": int, "state": str}, {"raw": str, "verdict": (str, type(None)),
                     "n_findings": int, "n_status": int}),
    "finding_occ": _spec("hook", ("payload.answer_event_id", "ordinal"),
                         "event_id:canonical", {"answer_event_id": str,
                         "expert": str, "severity": str,
                         "finding_kind": str, "file": str, "line": int, "claim": str},
                         {"fix": (str, type(None))}),
    "finding_status": _spec("hook", ("payload.answer_event_id", "ordinal"),
                            "payload.canonical_id", {"answer_event_id": str,
                            "canonical_id": str, "status": str},
                            {"kind_confirm": (str, type(None))}),
    "reclassify": _spec("cli", ("payload.canonical_id", "payload.pass", "attempt"),
                        "payload.canonical_id", {"canonical_id": str, "from": str,
                        "to": str, "reason": str, "pass": int}),
    # what the fixer gave for this finding (K4, F21): the hunk (always an audit blob),
    # its cause and proof note — FIXING_SPEC and R2 read them from here only
    "address": _spec("cli", ("payload.canonical_id", "payload.pass", "attempt"),
                     "payload.canonical_id", {"canonical_id": str, "pass": int, "hunk": str},
                     {"cause": str, "proof": str, "solution": str, "evidence_plan": str}),
    "proof": _spec(("runner", "cli"), ("payload.canonical_id", "payload.pass", "attempt"),
                   "payload.canonical_id", {"canonical_id": str, "pass": int,
                   "evidence_type": str, "code_tree": str, "result": str},
                   {"old_runs": list, "new_run": str, "test_call_event_id": str,
                    "test_call_event_ids": list, "old_code_tree": str,
                    "test_node": str, "probe_target": str,
                    "probe_old": str, "probe_new": str,
                    "touches_finding": bool, "touches_executable": bool,
                    "refs": list, "repo": str}),
    "test_call": _spec("runner", ("payload.seq",), "payload.call_type",
                       {"seq": int, "call_type": str, "code_tree": str, "result": str},
                       {"details": list, "target": str, "snapshot": str}),
    "stage_attempt": _spec("cli", ("stage", "attempt"), "stage",
                           {"check_ids": list}),
    "check": _spec("cli", ("payload.stage_attempt_event_id", "payload.check_id"),
                   "payload.check_id", {"stage_attempt_event_id": str, "check_id": str,
                   "passed": bool, "detail": str}),
    # the producer of the triggering event signs its transition (the lifecycle decides
    # what it must say; a transition that differs from the expected one never folds)
    "transition": _spec(("cli", "runner"), ("payload.from", "payload.to", "payload.trigger_event_id"),
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
                            "run_id": str, "action": str, "binding_digest": str,
                            "oracle_receipt": str},
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
                   # signed map; progress files are crash recovery only
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
    "restore": _spec("cli", ("ordinal",), None, {"snapshot_exists": bool}),
    # hook: the daemon's required_approval count (D4)
    "metric": _spec(("cli", "runner", "hook"), ("stage", "payload.name", "attempt"), "payload.name",
                    {"name": str, "value": (int, float)},
                    # metric(cap_exceeded.<cap>): a count cap of the run was hit (F18)
                    {"cap": str}),
    "turn": _spec("cli", ("payload.message_id",), None, {"message_id": str}),
    "integrity": _spec(("cli", "hook", "runner"), ("payload.conflict_id", "ordinal"),
                       "payload.conflict_id", {"conflict_id": str,
                       "conflict_type": str, "first_hash": str, "new_hash": str}),
    "session_lost": _spec("cli", ("ordinal",), None, {"reason": str}),
    "user_stop": _spec("cli", ("ordinal",), None, {"reason": str}),
    # Ownership moves only by this daemon-written event (run_owner_gate takeover):
    # every human authorization binds to the CURRENT owner, not the creator.
    "owner_transfer": _spec("hook", ("ordinal",), None,
                            {"from_session": str, "to_session": str, "reason": str}),
    "stage_complete": _spec("cli", ("stage", "attempt"), "stage",
                            {"stage_attempt_event_id": str},
                            {"final_verdict": str, "degraded": bool}),
    "gate0_exhausted": _spec("cli", ("ordinal",), None, {"reason": str}),
    "resume": _spec("cli", ("ordinal",), None, {"worktree_ready": bool}),
    # the run's infrastructure failed (conformance F11): the CLI or the runner that met
    # the failure records it
    "infrastructure_failed": _spec(("cli", "runner"), ("ordinal",), None, {"reason": str}),
    "worktree_unavailable": _spec("cli", ("ordinal",), None, {"reason": str}),
    # the daemon's background REFLECT ends REFLECT, never the agent's CLI (D1)
    "reflect_complete": _spec("hook", ("ordinal",), None, {}),
    "reflect_exhausted": _spec("hook", ("ordinal",), None, {"reason": str}),
}
class EventError(ValueError):
    """The event violates the v1 envelope, identity or size contract."""



class EventAuthority:
    """Trust root for one run's event log; cycle 2 stores it outside agent write scope."""

    def __init__(self, secret: bytes):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise EventError("event authority secret must be at least 32 bytes")
        self.__secret = bytes(secret)
        self.authority_id = hashlib.sha256(self.__secret).hexdigest()

    def writer(self, producer: str) -> "WriterCapability":
        if producer not in PRODUCERS:
            raise EventError(f"unknown producer {producer!r}")
        return WriterCapability(self, producer)   # the process check lives there

    def _sign(self, event: Mapping[str, Any]) -> str:
        body = {key: value for key, value in event.items() if key != "signature"}
        return hmac.new(self.__secret, _canonical(body), hashlib.sha256).hexdigest()


    def _seal_consumption(self, value: Any) -> dict[str, Any]:
        """Convert a registered oracle Consumption and bind it to this authority."""
        from core.run_protocol.auth import Consumption
        if not isinstance(value, Consumption):
            raise EventError("auth_consumed payload requires oracle Consumption")
        payload = value._event_payload()
        receipt = hmac.new(
            self.__secret, b"oracle-consumption\0" + _canonical(payload), hashlib.sha256
        ).hexdigest()
        return {**payload, "oracle_receipt": receipt}

    def _verify_consumption(self, payload: Mapping[str, Any]) -> bool:
        receipt = payload.get("oracle_receipt")
        body = {key: value for key, value in payload.items() if key != "oracle_receipt"}
        expected = hmac.new(
            self.__secret, b"oracle-consumption\0" + _canonical(body), hashlib.sha256
        ).hexdigest()
        return isinstance(receipt, str) and hmac.compare_digest(receipt, expected)
    def verify(self, event: Mapping[str, Any]) -> bool:
        return event.get("authority_id") == self.authority_id and isinstance(
            event.get("signature"), str
        ) and hmac.compare_digest(event["signature"], self._sign(event))


class WriterCapability:
    """Producer authority; only EventAuthority creates a capability used by make_event."""

    __slots__ = ("_authority", "producer")

    def __init__(self, authority: EventAuthority, producer: str):
        if not isinstance(authority, EventAuthority) or producer not in PRODUCERS:
            raise EventError("invalid writer capability")
        if producer not in _process_producers or (producer == "hook" and not _hook_writer_allowed):
            raise EventError(f"this process may not write as {producer!r}: hook facts are "
                             "recorded only by the SwarmAI daemon")
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
    if kind == "test_call":
        if payload["call_type"] not in {"test", "probe", "lint", "typecheck", "build",
                                             "security_scan", "mutation"}:
            raise EventError("test_call call_type is invalid")
        if payload["result"] not in {"pass", "fail", "error", "not_run", "assert_fail",
                                     "satisfied", "not_satisfied"}:
            raise EventError("test_call result is invalid")
        tree = payload["code_tree"]
        if not isinstance(tree, str) or len(tree) != 40 \
                or any(char not in "0123456789abcdef" for char in tree):
            raise EventError("test_call code_tree must be a git tree id")
        if "snapshot" in payload and payload["snapshot"] not in {"old", "new", "current"}:
            raise EventError("test_call snapshot is invalid")
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
    """Reject an unsigned, incomplete, forged or oversized event before persistence."""
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
    if event.get("kind") == "auth_consumed" \
            and not authority._verify_consumption(event["payload"]):
        raise EventError("auth_consumed oracle receipt mismatch")
    if event.get("event_id") != expected_event_id(event):
        raise EventError("event_id mismatch")
    if not authority.verify(event):
        raise EventError("event signature/authority mismatch")
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
        payload = authority._seal_consumption(payload)
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
    event["signature"] = authority._sign(event)
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
