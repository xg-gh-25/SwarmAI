"""Human authorization bindings and single-consume oracle (v13 §A4.1-§A4.3).

Public symbols:
- ``make_request`` — validate and freeze a land/discard/accept request.
- ``AuthorizationOracle`` — in-memory, atomic, one-use grant registry.
- ``binding_digest`` — canonical identity used by requests and grants.

Files may record auth events, but only this oracle's exact-match consume result grants an
action. Cycle 3 wires it to the daemon-owned socket; this module performs no I/O.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from core.run_protocol.identifiers import IdentifierError, validate_run_id

ACTIONS = frozenset({"land", "land_skip_tests", "discard_snapshot", "accept_finding"})
_HEX40 = set("0123456789abcdef")
_HEX64 = set("0123456789abcdef")


class AuthorizationError(ValueError):
    """An authorization request or consume attempt violates the protocol."""


def _sha(value: Any, length: int, field: str) -> str:
    if not isinstance(value, str) or len(value) != length or any(
        char not in ("0123456789abcdef") for char in value
    ):
        raise AuthorizationError(f"{field} must be a {length}-hex digest")
    return value


def _land_bindings(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or not value:
        raise AuthorizationError("land bindings must name at least one repo")
    out = {}
    for repo, binding in sorted(value.items()):
        if not isinstance(repo, str) or not repo or not isinstance(binding, Mapping):
            raise AuthorizationError("land binding is malformed")
        if set(binding) != {"code_tree", "delivery_tree", "report_sha"}:
            raise AuthorizationError("land binding fields must be exact")
        out[repo] = {
            "code_tree": _sha(binding["code_tree"], 40, "code_tree"),
            "delivery_tree": _sha(binding["delivery_tree"], 40, "delivery_tree"),
            "report_sha": _sha(binding["report_sha"], 64, "report_sha"),
        }
    return out


def normalize_bindings(action: str, value: Any) -> dict[str, Any]:
    """Validate the action-specific immutable binding."""
    if action in ("land", "land_skip_tests"):
        return {"repos": _land_bindings(value)}
    if action == "discard_snapshot":
        # the repo is logged too: the ledger path is derived from (run, repo), never
        # read from the run's display JSON (C1 review)
        if not isinstance(value, Mapping) or set(value) != {"snapshot_id", "repo_name"} \
                or not isinstance(value.get("snapshot_id"), str) or not value["snapshot_id"] \
                or not isinstance(value.get("repo_name"), str) or not value["repo_name"]:
            raise AuthorizationError("discard_snapshot binds exactly snapshot_id and repo_name")
        return {"snapshot_id": value["snapshot_id"], "repo_name": value["repo_name"]}
    if action == "accept_finding":
        if not isinstance(value, Mapping) or set(value) != {"canonical_id", "state_hash"}:
            raise AuthorizationError("accept_finding binding fields must be exact")
        cid = value.get("canonical_id")
        if not isinstance(cid, str) or not cid.startswith("CF-"):
            raise AuthorizationError("accept_finding needs canonical_id")
        return {"canonical_id": cid, "state_hash": _sha(value.get("state_hash"), 64, "state_hash")}
    raise AuthorizationError(f"unknown action {action!r}")


def binding_digest(action: str, bindings: Mapping[str, Any]) -> str:
    """Hash normalized bindings; callers cannot authorize a neighbouring tree/state."""
    normalized = normalize_bindings(action, bindings)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


NOTE_MAX = 600


def make_request(*, request_id: str, session_id: str, run_id: str, action: str,
                 bindings: Mapping[str, Any], note: str | None = None) -> dict[str, Any]:
    """Create the land_request payload shown by the approval card."""
    for name, value in (("request_id", request_id), ("session_id", session_id)):
        if not isinstance(value, str) or not value:
            raise AuthorizationError(f"{name} is required")
    try:
        run_id = validate_run_id(run_id)
    except IdentifierError as exc:
        raise AuthorizationError(str(exc)) from exc
    if action not in ACTIONS:
        raise AuthorizationError(f"unknown action {action!r}")
    if note is not None and not isinstance(note, str):
        raise AuthorizationError("note must be text or null")
    if note is not None and len(note) > NOTE_MAX:
        # the logged request lives in one event line; a longer note would be moved to a
        # blob and the request could no longer be rebuilt from the event
        raise AuthorizationError(f"note is longer than {NOTE_MAX} characters")
    normalized = normalize_bindings(action, bindings)
    return {
        "request_id": request_id, "session_id": session_id, "run_id": run_id,
        "action": action, "bindings": normalized,
        "binding_digest": hashlib.sha256(json.dumps(
            normalized, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
        "skip_tests": action == "land_skip_tests", "note": note,
    }


@dataclass
class _Grant:
    request: dict[str, Any]
    auth_event_id: str
    decided_at: float
    expires_at: float
    consumed: bool = False


@dataclass(frozen=True)
class Consumption:
    """A successful daemon-oracle consume; CLI converts this to auth_consumed payload."""

    auth_event_id: str
    request_id: str
    session_id: str
    run_id: str
    action: str
    binding_digest: str
    capability: str
    canonical_id: str | None
    state_hash: str | None
    bindings: Mapping[str, Any]
    _issuer: Any

    def _event_payload(self) -> dict[str, str]:
        if not isinstance(self._issuer, AuthorizationOracle):
            raise AuthorizationError("Consumption has no issuing oracle")
        return self._issuer.consumption_payload(self)


@dataclass(frozen=True)
class ConsumeResult:
    """One consume outcome with an optional typed success value."""

    ok: bool
    reason: str
    consumption: Consumption | None = None


class AuthorizationOracle:
    """Daemon-memory authorization registry; ``consume`` is atomic and one-use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._grants: dict[str, _Grant] = {}
        self._consumptions: dict[str, dict[str, str]] = {}

    def issue(self, request: Mapping[str, Any], *, auth_event_id: str,
              decided_at: float, expires_at: float) -> None:
        """Register a human-approved request; duplicate request IDs are refused."""
        if not isinstance(decided_at, (int, float)) or not isinstance(expires_at, (int, float)) \
                or isinstance(decided_at, bool) or isinstance(expires_at, bool) \
                or not math.isfinite(decided_at) or not math.isfinite(expires_at) \
                or expires_at <= decided_at or expires_at - decided_at > 1800:
            raise AuthorizationError("authorization expiry must be after its decision and within 30 minutes")
        required = {"request_id", "session_id", "run_id", "action", "bindings",
                    "binding_digest", "skip_tests", "note"}
        if not isinstance(request, Mapping) or set(request) != required:
            raise AuthorizationError("request shape is invalid")
        # Rebuild to prove a hand-written dictionary carries exact normalized bindings.
        rebuilt = make_request(
            request_id=request["request_id"], session_id=request["session_id"],
            run_id=request["run_id"], action=request["action"],
            bindings=(request["bindings"].get("repos") if request["action"] in
                      ("land", "land_skip_tests") else request["bindings"]),
            note=request.get("note"),
        )
        if rebuilt != dict(request):
            raise AuthorizationError("request fields do not match its bindings")
        _sha(auth_event_id, 64, "auth_event_id")
        with self._lock:
            if request["request_id"] in self._grants:
                raise AuthorizationError("request_id already registered")
            self._grants[request["request_id"]] = _Grant(
                copy.deepcopy(dict(request)), auth_event_id,
                float(decided_at), float(expires_at),
            )


    def consume(self, *, request_id: str, session_id: str, run_id: str, action: str,
                bindings: Mapping[str, Any], now: float) -> ConsumeResult:
        """Atomically consume only an unexpired exact match."""
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            return ConsumeResult(False, "authorization check time is invalid")
        try:
            normalized = normalize_bindings(action, bindings)
            digest = hashlib.sha256(json.dumps(
                normalized, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
        except AuthorizationError as exc:
            return ConsumeResult(False, str(exc))
        with self._lock:
            grant = self._grants.get(request_id)
            if grant is None:
                return ConsumeResult(False, "authorization not found (daemon may have restarted)")
            if grant.consumed:
                return ConsumeResult(False, "authorization already consumed")
            request = grant.request
            if float(now) > grant.expires_at:
                return ConsumeResult(False, "authorization expired")
            if request["session_id"] != session_id or request["run_id"] != run_id:
                return ConsumeResult(False, "authorization session/run mismatch")
            if request["action"] != action or request["binding_digest"] != digest:
                return ConsumeResult(False, "authorization action/bindings mismatch")
            grant.consumed = True
            capability = secrets.token_hex(32)
            canonical_id = normalized.get("canonical_id")
            state = normalized.get("state_hash")
            value = Consumption(grant.auth_event_id, request_id, session_id, run_id,
                                action, digest, capability, canonical_id, state,
                                copy.deepcopy(normalized), self)
            payload = {
                "auth_event_id": grant.auth_event_id, "request_id": request_id,
                "session_id": session_id, "run_id": run_id, "action": action,
                "binding_digest": digest,
            }
            if canonical_id is not None:
                payload.update({"canonical_id": canonical_id, "state_hash": state})
            self._consumptions[capability] = payload
            return ConsumeResult(True, "authorization consumed", value)

    def consumption_payload(self, value: Consumption) -> dict[str, str]:
        """Convert only a Consumption registered by this oracle."""
        if not isinstance(value, Consumption):
            raise AuthorizationError("auth_consumed payload requires oracle Consumption")
        with self._lock:
            payload = self._consumptions.get(value.capability)
            expected = {
                "auth_event_id": value.auth_event_id, "request_id": value.request_id,
                "session_id": value.session_id, "run_id": value.run_id,
                "action": value.action, "binding_digest": value.binding_digest,
            }
            if value.canonical_id is not None:
                expected.update({"canonical_id": value.canonical_id,
                                 "state_hash": value.state_hash})
            if payload != expected:
                raise AuthorizationError("Consumption was not issued by this oracle")
            return copy.deepcopy(payload)

    def clear(self) -> None:
        """Daemon restart semantics: every unconsumed grant becomes invalid."""
        with self._lock:
            self._grants.clear()
            self._consumptions.clear()

    def pending(self) -> int:
        """Number of registered, not-yet-consumed grants (diagnostics only)."""
        with self._lock:
            return sum(not grant.consumed for grant in self._grants.values())
