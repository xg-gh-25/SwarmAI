"""Event-sourced finding tests (v13 §A3.3-§A3.5, §D4).

Every finding, R2 status, proof and human acceptance is a validated event in one hash
chain. Tests assert canonical occurrence identity, kind-specific producer evidence,
R2 adequacy, severity push effects, clustering and addressed-expert routing.
"""
from __future__ import annotations

import pytest

from core.run_protocol import auth, events as ev
from core.run_protocol import findings as fd

AT = "2026-10-22T00:00:00+00:00"


class Stream:
    def __init__(self):
        self.rows, self.ordinal = [], 0
        self.authority = ev.EventAuthority(b"f" * 32)
        self.oracle = auth.AuthorizationOracle()

    def add(self, kind, payload, *, producer=None, stage=None, round=None, attempt=None):
        self.ordinal += 1
        spec = ev.EVENT_SPECS[kind]
        if producer is None:
            producer = spec.producer if isinstance(spec.producer, str) else spec.producer[0]
        row, _ = ev.make_event(
            writer=self.authority.writer(producer),
            run_id="run_x", kind=kind, at=AT, payload=payload,
            stage=stage, round=round, attempt=attempt,
            ordinal=self.ordinal if "ordinal" in spec.occurrence else None,
            prev_hash=ev.event_digest(self.rows[-1]) if self.rows else None,
        )
        self.rows.append(row)
        return row

    def answer(self, expert="correctness", round=1, *, state="parsed", tool_use_id=None):
        """An answer bound to its handout, as the v1 contract requires (one answer per
        handout: the answer's ckey is the handout id). Reuses this expert's unanswered
        handout of the round (an R2 handout _status emitted), else emits a new attempt."""
        answered = {row["payload"]["handout_event_id"] for row in self.rows if row["kind"] == "answer"}
        free = [row for row in self.rows if row["kind"] == "handout" and row.get("round") == round
                and row["payload"]["expert_id"] == expert and row["event_id"] not in answered]
        if free:
            handout = free[-1]
        else:
            payload = {"expert_id": expert, "prompt_sha": "0" * 64}
            hround = round
            if round == 2:
                # an R2 handout lists this expert's canonical findings; an expert with
                # none (a wrong-expert answer under test) is bound to an R1 attempt
                ids = ["CF-" + row["event_id"][:8] for row in self.rows
                       if row["kind"] == "finding_occ" and row["payload"]["expert"] == expert]
                if ids:
                    payload["finding_ids"] = ids
                else:
                    hround = 1
            attempt = 1 + sum(1 for row in self.rows if row["kind"] == "handout"
                              and row.get("round") == hround and row["payload"]["expert_id"] == expert)
            handout = self.add("handout", payload, stage="review", round=hround, attempt=attempt)
        return self.add("answer", {
            "handout_event_id": handout["event_id"],
            "prompt_sha": handout["payload"].get("prompt_sha") or "0" * 64,
            "tool_use_id": tool_use_id or f"tool-{self.ordinal}", "expert_id": expert,
            "stage": "review", "round": round, "state": state,
        })

    def finding(self, *, expert="correctness", severity="HIGH", kind="behavior",
                file="backend/x.py", line=10, claim="crashes", answer=None):
        answer = answer or self.answer(expert, 1)
        return self.add("finding_occ", {
            "answer_event_id": answer["event_id"], "expert": expert,
            "severity": severity, "finding_kind": kind, "file": file,
            "line": line, "claim": claim,
        }, round=1)

    def projected(self):
        return fd.fold(self.rows, self.authority)

    def proof(self, canonical_id, evidence_type, result, **extra):
        payload = {"canonical_id": canonical_id, "pass": 1,
                   "evidence_type": evidence_type, "code_tree": "1" * 40,
                   "result": result, **extra}
        if evidence_type == "test":
            old_tree, target = "0" * 40, "tests/test_break.py::test_finding"
            calls = [self.add("test_call", {"seq": self.ordinal + 1,
                "call_type": "test", "target": target, "snapshot": snapshot,
                "code_tree": old_tree if snapshot == "old" else "1" * 40,
                "result": result_value}) for snapshot, result_value in (
                    ("old", "assert_fail"), ("old", "assert_fail"), ("new", "pass"))]
            payload.update({"test_call_event_ids": [call["event_id"] for call in calls],
                            "test_node": target, "old_code_tree": old_tree})
            payload.setdefault("old_runs", ["assert_fail", "assert_fail"])
            payload.setdefault("new_run", "pass")
        elif evidence_type == "probe":
            old_tree, target = "0" * 40, "git grep -e old -- backend"
            calls = [self.add("test_call", {"seq": self.ordinal + 1,
                "call_type": "probe", "target": target, "snapshot": snapshot,
                "code_tree": old_tree if snapshot == "old" else "1" * 40,
                "result": result_value}) for snapshot, result_value in (
                    ("old", "not_satisfied"), ("new", "satisfied"))]
            payload.update({"test_call_event_ids": [call["event_id"] for call in calls],
                            "probe_target": target, "old_code_tree": old_tree})
            payload.setdefault("probe_old", "not_satisfied")
            payload.setdefault("probe_new", "satisfied")
        return self.add("proof", payload, producer="runner", attempt=1)


def one(**kwargs):
    stream = Stream()
    event = stream.finding(**kwargs)
    return stream, event, stream.projected()[0]


def test_canonical_id_is_the_finding_occ_event_id_not_text():
    stream = Stream()
    first = stream.finding(claim="same")
    second = stream.finding(claim="same")
    rows = {row["occurrence_event_id"]: row for row in stream.projected()}
    assert rows[first["event_id"]]["canonical_id"] == "CF-" + first["event_id"][:8]
    assert rows[second["event_id"]]["canonical_id"] == "CF-" + second["event_id"][:8]
    assert rows[first["event_id"]]["canonical_id"] != rows[second["event_id"]]["canonical_id"]


def test_finding_occ_wrong_producer_or_forged_ckey_is_rejected_by_event_contract():
    stream = Stream()
    answer = stream.answer("correctness", 1, tool_use_id="t")
    payload = {"answer_event_id": answer["event_id"], "expert": "correctness",
               "severity": "HIGH", "finding_kind": "behavior", "file": "x.py",
               "line": 1, "claim": "x"}
    with pytest.raises(ev.EventError, match="producer"):
        stream.add("finding_occ", payload, producer="cli", round=1)
    event = stream.add("finding_occ", payload, round=1)
    event["ckey"] = "CF-deadbeef"
    with pytest.raises(fd.FindingError, match="invalid event chain"):
        stream.projected()


@pytest.mark.parametrize("payload", [
    {"severity": "MAYBE"}, {"finding_kind": "other"}, {"line": 0},
])
def test_malformed_finding_payload_is_rejected(payload):
    stream = Stream()
    event = stream.finding()
    event["payload"].update(payload)
    # Rebuild a valid hash chain cannot turn invalid domain values into a finding.
    event["event_id"] = ev.expected_event_id(event)
    with pytest.raises(fd.FindingError):
        stream.projected()


def _r2_answer(stream, expert="correctness"):
    return stream.answer(expert, 2, tool_use_id=f"r2-{stream.ordinal}")


def _status(stream, finding, status, *, kind_confirm=None, answer=None):
    if not any(row["kind"] == "handout" and row.get("round") == 2
               and finding["canonical_id"] in row["payload"].get("finding_ids", [])
               for row in stream.rows):
        stream.add("handout", {"expert_id": finding["expert"],
                               "finding_ids": [finding["canonical_id"]]},
                   stage="review", round=2, attempt=1)
    answer = answer or _r2_answer(stream, finding["expert"])
    payload = {"answer_event_id": answer["event_id"],
               "canonical_id": finding["canonical_id"], "status": status}
    if kind_confirm is not None:
        payload["kind_confirm"] = kind_confirm
    return stream.add("finding_status", payload, round=2)


def test_missing_r2_line_is_open_for_unclosed_finding():
    stream, _, finding = one()
    stream.add("handout", {"expert_id": "correctness",
                           "finding_ids": [finding["canonical_id"]]},
               stage="review", round=2, attempt=1)
    row = stream.projected()[0]
    assert row["status"] == "open" and row["r2_status"] == "OPEN"


def test_proven_finding_with_missing_r2_status_is_open_not_assumed_adequate():
    stream, _, finding = one()
    stream.proof(finding["canonical_id"], "test", "proven")
    stream.add("handout", {"expert_id": "correctness",
                           "finding_ids": [finding["canonical_id"]]},
               stage="review", round=2, attempt=1)
    row = stream.projected()[0]
    assert row["status"] == "open" and row["r2_status"] == "OPEN"
    assert fd.push_effect(row)


def test_finding_occ_requires_existing_matching_expert_answer():
    stream = Stream()
    answer = stream.answer("security", 1, tool_use_id="t")
    stream.add("finding_occ", {"answer_event_id": answer["event_id"],
               "expert": "correctness", "severity": "HIGH", "finding_kind": "behavior",
               "file": "x.py", "line": 1, "claim": "x"}, round=1)
    with pytest.raises(fd.FindingError, match="parsed expert answer"):
        stream.projected()


def test_finding_status_requires_this_experts_real_r2_answer():
    stream, _, finding = one(expert="correctness")
    stream.add("handout", {"expert_id": "correctness",
                           "finding_ids": [finding["canonical_id"]]},
               stage="review", round=2, attempt=1)
    wrong = stream.answer("security", 2, tool_use_id="r2")
    stream.add("finding_status", {"answer_event_id": wrong["event_id"],
               "canonical_id": finding["canonical_id"], "status": "OPEN"}, round=2)
    with pytest.raises(fd.FindingError, match="parsed R2"):
        stream.projected()


def test_r2_test_adequate_keeps_closed_proven_inadequate_invalidates():
    for status, expected in (("ADEQUATE", "closed_proven"),
                             ("TEST_INADEQUATE", "invalid_test")):
        stream, _, finding = one()
        stream.proof(finding["canonical_id"], "test", "proven")
        _status(stream, finding, status)
        assert stream.projected()[0]["status"] == expected


def test_reclassification_changes_kind_only_after_matching_r2_confirmation():
    stream, _, finding = one()
    stream.add("reclassify", {"canonical_id": finding["canonical_id"],
               "from": "behavior", "to": "docs", "reason": "comment only", "pass": 1},
               attempt=1)
    assert stream.projected()[0]["kind"] == "behavior"
    _status(stream, finding, "RESOLVED", kind_confirm="docs")
    row = stream.projected()[0]
    assert row["kind"] == "docs" and row["reclassification"]["confirmed"]


def test_unknown_status_finding_is_rejected():
    stream, _, _ = one()
    answer = _r2_answer(stream)
    stream.add("finding_status", {"answer_event_id": answer["event_id"],
               "canonical_id": "CF-deadbeef", "status": "OPEN"}, round=2)
    with pytest.raises(fd.FindingError, match="unknown finding"):
        stream.projected()


def test_runner_test_and_probe_proofs_close_only_supported_kinds():
    for kind, evidence in (("behavior", "test"), ("contract", "test"),
                           ("absence", "probe"), ("contract", "probe")):
        stream, _, finding = one(kind=kind)
        stream.proof(finding["canonical_id"], evidence, "proven")
        assert stream.projected()[0]["status"] == "closed_proven"


def test_fake_or_wrong_producer_proof_cannot_close_high_finding():
    stream, _, finding = one()
    stream.add("proof", {"canonical_id": finding["canonical_id"], "pass": 1,
               "evidence_type": "test", "code_tree": "1" * 40, "result": "proven"},
               producer="cli", attempt=1)
    with pytest.raises(fd.FindingError, match="runner"):
        stream.projected()


def test_diff_assertion_flags_are_exact_booleans_and_docs_cannot_touch_code():
    stream, _, finding = one(kind="docs")
    _status(stream, finding, "RESOLVED")
    with pytest.raises(ev.EventError, match="wrong type"):
        stream.add("proof", {"canonical_id": finding["canonical_id"], "pass": 1,
                   "evidence_type": "diff_assertion", "code_tree": "1" * 40,
                   "result": "asserted", "touches_finding": "false",
                   "touches_executable": False}, producer="cli", attempt=1)
    stream.proof(finding["canonical_id"], "diff_assertion", "asserted",
                 touches_finding=True, touches_executable=True)
    # Stream.proof defaults runner; diff assertions must come from cli first.
    with pytest.raises(fd.FindingError):
        stream.projected()


def test_runner_label_without_matching_test_call_cannot_prove_a_finding():
    stream, _, finding = one()
    stream.add("proof", {"canonical_id": finding["canonical_id"], "pass": 1,
               "evidence_type": "test", "code_tree": "1" * 40, "result": "proven",
               "test_call_event_id": "a" * 64,
               "old_runs": ["assert_fail", "assert_fail"], "new_run": "pass"},
               producer="runner", attempt=1)
    with pytest.raises(fd.FindingError, match="correlated old-old-new"):
        stream.projected()


def test_cli_diff_assertion_closes_docs_but_design_remains_unproven():
    for kind, expected in (("docs", "closed_asserted"),
                           ("design", "closed_unproven"),
                           ("security_policy", "closed_unproven")):
        stream, _, finding = one(kind=kind)
        _status(stream, finding, "RESOLVED")
        stream.add("proof", {"canonical_id": finding["canonical_id"], "pass": 1,
                   "evidence_type": "diff_assertion", "code_tree": "1" * 40,
                   "result": "asserted", "touches_finding": True,
                   "touches_executable": False}, producer="cli", attempt=1)
        assert stream.projected()[0]["status"] == expected


@pytest.mark.parametrize("severity,status,effect", [
    ("HIGH", "open", True), ("CRITICAL", "invalid_test", True),
    ("HIGH", "closed_unproven", True), ("MEDIUM", "open", False),
    ("LOW", "invalid_test", False), ("HIGH", "closed_proven", False),
    ("HIGH", "closed_asserted", False), ("HIGH", "refuted", False),
    ("HIGH", "accepted", False),
])
def test_push_effect_table(severity, status, effect):
    _, _, row = one(severity=severity)
    row["status"] = status
    assert fd.push_effect(row) is effect


def _accept(stream, finding, bound_state_hash=None):
    bound_state_hash = bound_state_hash or fd.state_hash(finding)
    bindings = {"canonical_id": finding["canonical_id"],
                "state_hash": bound_state_hash}
    request = auth.make_request(request_id="q", session_id="s", run_id="run_x",
                                action="accept_finding", bindings=bindings)
    stream.add("land_request", {"request_id": "q", "action": "accept_finding",
                                "binding_digest": request["binding_digest"]})
    grant = stream.add("auth", {"request_id": "q", "session_id": "s",
        "run_id": "run_x", "action": "accept_finding",
        "binding_digest": request["binding_digest"], "expires_at": 1800.0})
    stream.oracle.issue(request, auth_event_id=grant["event_id"],
                        decided_at=0.0, expires_at=1800.0)
    result = stream.oracle.consume(request_id="q", session_id="s", run_id="run_x",
        action="accept_finding", bindings=bindings, now=1.0)
    assert result.ok and result.consumption is not None
    stream.add("auth_consumed", result.consumption)


def test_human_accept_requires_complete_consumed_authorization_chain():
    stream, _, finding = one(kind="security_policy")
    _accept(stream, finding)
    row = stream.projected()[0]
    assert row["status"] == "accepted" and not fd.push_effect(row)
    forged, _, finding2 = one(kind="security_policy")
    with pytest.raises(ev.EventError, match="oracle Consumption"):
        forged.add("auth_consumed", {"auth_event_id": "a" * 64,
            "request_id": "q", "session_id": "s", "run_id": "run_x",
            "action": "accept_finding", "binding_digest": "d" * 64,
            "canonical_id": finding2["canonical_id"], "state_hash": "a" * 64})


def test_truthy_authorized_string_is_not_an_event_field():
    stream, _, finding = one()
    with pytest.raises(ev.EventError, match="oracle Consumption"):
        stream.add("auth_consumed", {"auth_event_id": "a" * 64,
            "request_id": "q", "session_id": "s", "run_id": "run_x",
            "action": "accept_finding", "binding_digest": "d" * 64,
            "canonical_id": finding["canonical_id"], "state_hash": "a" * 64,
            "authorized": "false"})


def test_cluster_by_file_ten_lines_and_r2_only_addressed_experts():
    stream = Stream()
    stream.finding(expert="correctness", severity="LOW", line=10)
    stream.finding(expert="security", severity="HIGH", line=20)
    stream.finding(expert="correctness", severity="MEDIUM", line=31)
    stream.finding(expert="red-team", severity="CRITICAL", file="backend/y.py", line=10)
    rows = stream.projected()
    clusters = fd.cluster_findings(rows)
    assert [len(c["findings"]) for c in clusters] == [2, 1, 1]
    assert clusters[0]["severity"] == "HIGH"
    assert fd.r2_experts(rows, {rows[1]["canonical_id"]}) == [rows[1]["expert"]]


def test_accept_finding_authorization_is_bound_to_current_state_hash():
    stream, _, finding = one(kind="security_policy")
    stale = fd.state_hash(finding)
    _status(stream, finding, "OPEN")
    _accept(stream, finding, bound_state_hash=stale)
    with pytest.raises(fd.FindingError, match="stale"):
        stream.projected()


def test_accept_finding_digest_cannot_be_rebound_to_another_finding():
    stream = Stream()
    stream.finding(expert="correctness", line=10)
    stream.finding(expert="security", line=20)
    first, second = stream.projected()
    bindings_a = {"canonical_id": first["canonical_id"],
                  "state_hash": fd.state_hash(first)}
    request = auth.make_request(request_id="q", session_id="s", run_id="run_x",
                                action="accept_finding", bindings=bindings_a)
    stream.add("land_request", {"request_id": "q", "action": "accept_finding",
                                "binding_digest": request["binding_digest"]})
    grant = stream.add("auth", {"request_id": "q", "session_id": "s",
        "run_id": "run_x", "action": "accept_finding",
        "binding_digest": request["binding_digest"], "expires_at": 1800.0})
    stream.oracle.issue(request, auth_event_id=grant["event_id"],
                        decided_at=0.0, expires_at=1800.0)
    result = stream.oracle.consume(request_id="q", session_id="s", run_id="run_x",
        action="accept_finding", bindings=bindings_a, now=1.0)
    original = result.consumption
    assert original is not None
    rebound = auth.Consumption(original.auth_event_id, original.request_id,
        original.session_id, original.run_id, original.action, original.binding_digest,
        original.capability, second["canonical_id"], fd.state_hash(second),
        original.bindings, stream.oracle)
    with pytest.raises(auth.AuthorizationError, match="not issued"):
        stream.add("auth_consumed", rebound)


def test_lint_call_cannot_prove_a_behavioral_break_test():
    stream, _, finding = one()
    lint = stream.add("test_call", {"seq": stream.ordinal + 1,
        "call_type": "lint", "target": "tests/test_break.py::test_finding",
        "snapshot": "new", "code_tree": "1" * 40, "result": "pass"})
    stream.add("proof", {"canonical_id": finding["canonical_id"], "pass": 1,
        "evidence_type": "test", "code_tree": "1" * 40, "old_code_tree": "0" * 40,
        "result": "proven", "test_node": "tests/test_break.py::test_finding",
        "test_call_event_ids": [lint["event_id"]] * 3,
        "old_runs": ["assert_fail", "assert_fail"], "new_run": "pass"},
        producer="runner", attempt=1)
    with pytest.raises(fd.FindingError, match="correlated old-old-new"):
        stream.projected()


def test_new_r2_finding_can_close_by_proof_without_missing_status_reopening_it():
    stream = Stream()
    old = stream.finding(expert="correctness")
    stream.add("handout", {"expert_id": "correctness",
                           "finding_ids": [old["ckey"]]},
               stage="review", round=2, attempt=1)
    answer = _r2_answer(stream)
    new = stream.add("finding_occ", {
        "answer_event_id": answer["event_id"], "expert": "correctness",
        "severity": "HIGH", "finding_kind": "behavior", "file": "backend/new.py",
        "line": 7, "claim": "new R2 issue"}, round=2)
    stream.proof(new["ckey"], "test", "proven")
    rows = {row["canonical_id"]: row for row in stream.projected()}
    assert rows[old["ckey"]]["status"] == "open"
    assert rows[new["ckey"]]["status"] == "closed_proven"
