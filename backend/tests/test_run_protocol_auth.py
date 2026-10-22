"""Pipeline v1 authorization tests (v13 §A4.1-§A4.3, §D4).

Methodology: in-memory oracle with explicit timestamps and concurrent consumers.
Invariants: grants bind session/run/action/exact trees or finding state, expire, survive
no daemon restart, and at most one concurrent consumer succeeds.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from core.run_protocol import auth

CODE = "1" * 40
DELIVERY = "2" * 40
REPORT = "a" * 64
STATE = "b" * 64


def land_bindings(**patch):
    row = {"code_tree": CODE, "delivery_tree": DELIVERY, "report_sha": REPORT}
    row.update(patch)
    return {"swarmai": row}


def request(action="land", bindings=None):
    if bindings is None:
        bindings = land_bindings() if action.startswith("land") else (
            {"snapshot_id": "snap-1", "repo_name": "repo"} if action == "discard_snapshot" else
            {"canonical_id": "CF-deadbeef", "state_hash": STATE}
        )
    return auth.make_request(request_id="q1", session_id="s1", run_id="run_x",
                             action=action, bindings=bindings, note="approved")


def test_land_request_binds_every_repo_tree_and_report():
    req = request()
    assert req["bindings"] == {"repos": land_bindings()}
    assert len(req["binding_digest"]) == 64 and req["skip_tests"] is False
    assert request("land_skip_tests")["skip_tests"] is True


@pytest.mark.parametrize("action,bindings", [
    ("discard_snapshot", {"snapshot_id": "snap-1", "repo_name": "repo"}),
    ("accept_finding", {"canonical_id": "CF-deadbeef", "state_hash": STATE}),
])
def test_other_actions_bind_exact_resource_state(action, bindings):
    req = request(action, bindings)
    assert req["bindings"] == bindings


@pytest.mark.parametrize("action,bindings", [
    ("land", {}),
    ("land", {"r": {"code_tree": CODE, "delivery_tree": DELIVERY}}),
    ("discard_snapshot", {"snapshot_id": "x", "extra": True}),
    ("accept_finding", {"canonical_id": "F1", "state_hash": STATE}),
    ("other", {}),
])
def test_binding_shapes_are_exact(action, bindings):
    with pytest.raises(auth.AuthorizationError):
        request(action, bindings)


def issued(action="land", bindings=None):
    oracle = auth.AuthorizationOracle()
    req = request(action, bindings)
    oracle.issue(req, auth_event_id="a" * 64, decided_at=100.0, expires_at=1900.0)
    return oracle, req


def consume(oracle, action="land", bindings=None, **patch):
    if bindings is None:
        bindings = land_bindings() if action.startswith("land") else (
            {"snapshot_id": "snap-1", "repo_name": "repo"} if action == "discard_snapshot" else
            {"canonical_id": "CF-deadbeef", "state_hash": STATE}
        )
    args = {"request_id": "q1", "session_id": "s1", "run_id": "run_x",
            "action": action, "bindings": bindings, "now": 200.0}
    args.update(patch)
    return oracle.consume(**args)


def test_exact_grant_consumes_once():
    oracle, _ = issued()
    assert oracle.pending() == 1
    result = consume(oracle)
    assert result.ok and result.reason == "authorization consumed"
    assert isinstance(result.consumption, auth.Consumption)
    assert result.consumption.auth_event_id == "a" * 64
    assert oracle.consumption_payload(result.consumption)["request_id"] == "q1"
    assert oracle.pending() == 0
    result = consume(oracle)
    assert not result.ok and result.reason == "authorization already consumed"


@pytest.mark.parametrize("patch,part", [
    ({"session_id": "s2"}, "session/run"),
    ({"run_id": "run_y"}, "session/run"),
    ({"action": "land_skip_tests"}, "action/bindings"),
    ({"bindings": land_bindings(code_tree="3" * 40)}, "action/bindings"),
    ({"bindings": land_bindings(delivery_tree="3" * 40)}, "action/bindings"),
    ({"bindings": land_bindings(report_sha="3" * 64)}, "action/bindings"),
])
def test_neighbouring_identity_action_or_tree_does_not_consume(patch, part):
    oracle, _ = issued()
    result = consume(oracle, **patch)
    assert not result.ok and part in result.reason
    assert oracle.pending() == 1  # a mismatch never burns the valid grant


def test_expired_and_daemon_restarted_grants_fail():
    oracle, _ = issued()
    result = consume(oracle, now=1900.1)
    assert not result.ok and result.reason == "authorization expired"
    oracle, _ = issued()
    oracle.clear()
    assert consume(oracle).ok is False


def test_concurrent_consumers_have_exactly_one_winner():
    oracle, _ = issued()
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: consume(oracle).ok, range(50)))
    assert results.count(True) == 1 and results.count(False) == 49


def test_duplicate_request_id_and_bad_expiry_are_refused():
    oracle, req = issued()
    with pytest.raises(auth.AuthorizationError, match="already"):
        oracle.issue(req, auth_event_id="a" * 64, decided_at=100, expires_at=200)
    fresh = auth.AuthorizationOracle()
    with pytest.raises(auth.AuthorizationError, match="expiry"):
        fresh.issue(req, auth_event_id="a" * 64, decided_at=200, expires_at=200)


def test_grant_is_limited_to_thirty_minutes_and_times_are_finite():
    req = request()
    for decided, expires in ((100, 1900.1), (float("nan"), 200), (100, float("inf"))):
        with pytest.raises(auth.AuthorizationError, match="30 minutes"):
            auth.AuthorizationOracle().issue(req, auth_event_id="a" * 64, decided_at=decided, expires_at=expires)
    oracle, _ = issued()
    result = consume(oracle, now=float("nan"))
    assert not result.ok and result.reason == "authorization check time is invalid"


def test_request_note_must_be_text_or_null():
    with pytest.raises(auth.AuthorizationError, match="note"):
        auth.make_request(request_id="q", session_id="s", run_id="run_x", action="land",
                          bindings=land_bindings(), note=123)


def test_auth_consumed_payload_rejects_a_truthy_json_dict():
    with pytest.raises(auth.AuthorizationError, match="oracle Consumption"):
        auth.AuthorizationOracle().consumption_payload({"ok": True})


@pytest.mark.parametrize("run_id", ["..", "run_../x", "run_x\n", "other", "run_x/y"])
def test_authorization_uses_the_shared_safe_run_id(run_id):
    with pytest.raises(auth.AuthorizationError, match="run_id"):
        auth.make_request(request_id="q", session_id="s", run_id=run_id, action="land",
                          bindings=land_bindings())


def test_publicly_constructed_consumption_has_no_oracle_capability():
    forged = auth.Consumption("a" * 64, "q", "s", "run_x", "land", "d" * 64,
                              "invented", None, None, {}, None)
    with pytest.raises(auth.AuthorizationError, match="not issued"):
        auth.AuthorizationOracle().consumption_payload(forged)
