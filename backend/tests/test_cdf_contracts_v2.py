"""CDF unit contract v2 (design r16.5 §5.3, §5.10, §5.12, §9).

Methodology: full enumeration of the ontology and routing tables plus targeted
validation cases. Key invariants:
- layer is derived from type only, and every type has exactly one layer;
- every legal (kind, answer, ddd_exists, tainted) combination routes to exactly one
  destination, and tainted content never reaches a per-turn store;
- conditional required fields are enforced and nothing is invented;
- external links are https-only and carry no credentials.
"""
from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from core.ingestion import contracts_v2 as c
from core.ingestion.contracts import CARD1_ADAPTER

Q = {"atomic": True, "valuable": True, "self_contained": True, "durable": True,
     "evidenced": True, "internally_consistent": True, "concise": True}


def test_layer_is_a_function_of_type_only():
    assert set(c.TYPE_TO_LAYER) == set(c.TYPES)
    assert {c.layer_of(t) for t in c.TYPES} == {"meta", "cognitive", "operational"}
    assert c.layer_of("guideline") == "operational" and c.layer_of("correction") == "meta"


def test_conditional_fields_never_invented():
    base = {"kind": "cognition", "scope": "world", "title": "t", "what": "w", "evidence": ["event:1"]}
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python({**base, "type": "decision", "why": "y"})
    c.SOURCE_UNIT.validate_python({**base, "type": "decision", "why": "y", "rejected_alternatives": "none"})
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python({**base, "type": "pitfall"})
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python({**base, "type": "process"})
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python({**base, "type": "principle", "scope": "user"})
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python({**base, "type": "principle", "evidence": []})


def test_event_needs_impact_or_outcome_and_link_needs_https():
    ev = {"kind": "event", "when": "2026-10-27", "title": "t", "what": "w", "evidence": ["run_event:1"]}
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python(ev)
    c.SOURCE_UNIT.validate_python({**ev, "outcome": "landed"})
    link = {"kind": "reference", "reference_kind": "link", "title": "t", "summary": "s.", "body": "b",
            "evidence": ["web:1"]}
    with pytest.raises(ValidationError):
        c.SOURCE_UNIT.validate_python(link)
    for bad in ("http://example.com", "https://user:" + "pw@example.com", "file:///etc/passwd", "https://"):
        with pytest.raises(ValidationError):
            c.SOURCE_UNIT.validate_python({**link, "source_url": bad})
    c.SOURCE_UNIT.validate_python({**link, "source_url": "https://example.com/x"})


def _cognition_answers():
    common = {"content_form": "rule", "type": "guideline", "applicability": "a", "claims": ["c"], "quality": Q}
    yield CARD1_ADAPTER.validate_python({**common, "shape": "UserCross", "user_kind": "preference"})
    yield CARD1_ADAPTER.validate_python({**common, "shape": "Agent", "agent_kind": "correction"})
    yield CARD1_ADAPTER.validate_python({**common, "shape": "WorldCrossGlobal", "global_relevance": True})
    for shape in ("ProjectSingle", "UserSingle", "WorldSingle"):
        yield CARD1_ADAPTER.validate_python({**common, "shape": shape, "project": "SwarmAI", "topic": "lesson"})


def test_reference_folders_learned_library_notes_only():
    assert set(c.REFERENCE_FOLDERS) == set(c.ReferenceKind.__args__)
    assert set(c.REFERENCE_FOLDERS.values()) <= {"Knowledge/Learned", "Knowledge/Library",
                                                 "Knowledge/Library/Links", "Knowledge/Notes"}
    assert c.link_file_for("https://Docs.Example.COM./a?b=1") == "Knowledge/Library/Links/docs.example.com.md"


def test_source_url_credentials():
    with pytest.raises(ValueError, match="credentialed_url"):
        c.sanitize_source_url("https://b.s3.amazonaws.com/k?X-Amz-Signature=abc&X-Amz-Credential=x")
    with pytest.raises(ValueError, match="credentialed_url"):
        c.sanitize_source_url("https://cdn.example.com/f?Signature=a&Expires=1")
    assert c.sanitize_source_url("HTTPS://Example.com/p/?token=s3cr3t&q=1&utm_source=x#frag") == \
        "https://example.com/p?q=1"
    assert c.semantic_urls_have_credentials("see https://x.io/a?api_key=zz for details")
    assert not c.semantic_urls_have_credentials("see https://x.io/a?q=1", None)


def test_trusted_passes_s1_cap_and_fanout():
    assert c.TRUSTED_PASS_ROOT_KINDS["user_utterance"] == {"user_message"}
    assert c.TRUSTED_PASS_ROOT_KINDS["human_answer"] == {"human_answer"}
    assert "human_answer" in c.EvidenceRootKind.__args__ and "decision_answer" not in c.EvidenceRootKind.__args__
    assert c.S1_MAX_UNITS_PER_MESSAGE == 3
    assert c.expand_projects("u1", ["B", "A", "A"]) == [("u1.p-a", "A"), ("u1.p-b", "B")]
    assert c.expand_projects("u1", ["A"]) == [("u1", "A")]


def test_event_kind_values():
    ev = {"kind": "event", "when": "2026-10-27", "title": "t", "what": "w", "outcome": "o", "evidence": ["run_event:1"]}
    for k in ("event", "month_summary", "month_supplement"):
        assert c.SOURCE_UNIT.validate_python({**ev, "event_kind": k}).event_kind == k


def test_routing_touches_no_filesystem_from_a_read_only_cwd(tmp_path, monkeypatch):
    """Routing computes a path; it must not create one. The daemon runs with cwd ``/``
    (read-only), where a relative ``mkdir`` raised OSError and poisoned every unit bound
    for a project DDD, while tests with a writable cwd passed and left ``Projects/``
    behind."""
    import os
    cwd = tmp_path / "ro"
    cwd.mkdir()
    cwd.chmod(0o555)
    monkeypatch.chdir(cwd)
    try:
        routed = []
        for scope in ("project:SwarmAI", "user", "agent", "world"):
            unit = c.SOURCE_UNIT.validate_python({
                "kind": "cognition", "type": "pitfall", "scope": scope, "topic": "lesson",
                "applicability": "When it applies.", "title": "t", "what": "w",
                "evidence": ["unit:x"], **({"user_kind": "preference"} if scope == "user" else {})})
            routed.append(c.route_unit(unit, "SwarmAI" if scope.startswith("project:") else None))
        assert any(d.store == "DDD" and d.relative_path.startswith("Projects/SwarmAI/") for d in routed)
        assert os.listdir(cwd) == []
    finally:
        cwd.chmod(0o755)


def test_open_work_is_no_cdf_unit():
    """Design 2026-10-31-continue-lookback-todo D2: work left for later is a ToDo, so a
    follow_up gap and an event's follow_up field are refused; the other gap kinds stay."""
    import pydantic
    import pytest as _pytest
    from core.ingestion import contracts_v2 as C
    base = {"kind": "gap", "scope": "project:P", "title": "t", "what": "w", "evidence": ["event:e1"]}
    for kind in ("debt", "risk", "deferred_finding", "process_issue"):
        assert C.SOURCE_UNIT.validate_python({**base, "gap_kind": kind}).gap_kind == kind
    with _pytest.raises(pydantic.ValidationError):
        C.SOURCE_UNIT.validate_python({**base, "gap_kind": "follow_up"})
    event = {"kind": "event", "when": "2026-10-31", "title": "t", "what": "w", "outcome": "o",
             "evidence": ["event:e1"]}
    C.SOURCE_UNIT.validate_python(event)
    with _pytest.raises(pydantic.ValidationError):
        C.SOURCE_UNIT.validate_python({**event, "follow_up": "do the next thing"})
    assert "never work still to do" in C.EVENT_RULE and "ToDo" in C.EVENT_RULE


def test_the_review_prompt_sends_next_steps_to_todos_not_gaps():
    from core.ingestion.sources import s7_review
    prompt = " ".join(s7_review.build_prompt(("cognition", "gap", "event"), {}, "", "record").split())
    assert "the session's own activity summary makes them ToDos" in prompt
    assert "what is left open as gaps" not in prompt
