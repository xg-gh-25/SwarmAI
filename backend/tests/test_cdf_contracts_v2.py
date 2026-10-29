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


def _cases():
    cases = [("cognition", a) for a in _cognition_answers()]
    cases += [("event", c.EventAnswer(shape="Event", significance=["milestone"], self_contained=True, evidenced=True))]
    cases += [("gap", c.GapAnswer(shape="Gap", actionable=True, evidenced=True, self_contained=True, project=p))
              for p in ("SwarmAI", None)]
    cases += [("reference", c.ReferenceAnswerV2(shape="ReferenceV2", reference_kind=k, quality=Q))
              for k in ("learned", "analysis", "design", "spec", "note", "link")]
    return cases


def test_route_is_total_unique_and_tainted_never_reaches_per_turn():
    """Full enumeration: every legal route is unique or parks on unknown_domain;
    missing_section is the second parked dependency discovered after routing."""
    seen = 0
    for (kind, answer), ddd_exists, tainted, hint, domain in itertools.product(
            _cases(), (True, False), (True, False), ("SwarmAI", None), ("writing", "nope", None)):
        kwargs = dict(ddd_exists=ddd_exists, project_hint=hint, tainted=tainted, domain=domain,
                      source_url="https://Docs.Example.com/x")
        try:
            dest = c.route_for_v2(kind, answer, **kwargs)
        except ValueError as exc:
            assert str(exc) == "unknown_domain", (kind, answer, exc)
            continue
        assert dest == c.route_for_v2(kind, answer, **kwargs)  # deterministic
        seen += 1
        if tainted and dest.store in c.PER_TURN_STORES:
            assert dest.store == "TIMELINE" and dest.injectable is False
        if dest.store == "COMMON_DDD":
            assert dest.injectable is False
            assert dest.relative_path.startswith(("Knowledge/Learned/domains/", "Knowledge/Learned/repos/",
                                                  "Knowledge/Learned/_common/"))
        if dest.rerouted_from:
            assert tainted and dest.relative_path == c.REROUTE_FILE[dest.rerouted_from]
            assert c.card3_corpus_excludes_common(c.DestinationV2(store=dest.rerouted_from, relative_path="x"))
    assert seen > 100


def test_tainted_per_turn_rules_reroute_to_fixed_common_files():
    by_shape = {a.shape: a for a in _cognition_answers()}
    for shape, path in (("UserCross", "Knowledge/Learned/_common/user.md"),
                        ("Agent", "Knowledge/Learned/_common/agent.md"),
                        ("WorldCrossGlobal", "Knowledge/Learned/_common/world.md")):
        clean = c.route_for_v2("cognition", by_shape[shape], ddd_exists=True, project_hint=None, tainted=False)
        dirty = c.route_for_v2("cognition", by_shape[shape], ddd_exists=True, project_hint=None, tainted=True)
        assert clean.store in c.PER_TURN_STORES and clean.injectable
        assert dirty.relative_path == path and dirty.section == clean.section
        assert dirty.rerouted_from == clean.store and not dirty.injectable


def test_no_ddd_cognition_keeps_its_type_in_repos_file():
    answer = CARD1_ADAPTER.validate_python({"content_form": "rule", "type": "pitfall", "applicability": "a",
                                            "claims": ["c"], "quality": Q, "shape": "ProjectSingle",
                                            "project": "Foo Repo", "topic": "architecture"})
    dest = c.route_for_v2("cognition", answer, ddd_exists=False, project_hint=None, tainted=False)
    assert dest.store == "COMMON_DDD" and dest.section == "Runtime Traps"
    assert dest.relative_path == "Knowledge/Learned/repos/foo-repo.md"


def test_world_not_global_goes_to_domain_file_and_unknown_domain_parks():
    answer = CARD1_ADAPTER.validate_python({"content_form": "rule", "type": "guideline", "applicability": "a",
                                            "claims": ["c"], "quality": Q, "shape": "WorldCrossGlobal",
                                            "global_relevance": False})
    dest = c.route_for_v2("cognition", answer, ddd_exists=True, project_hint=None, tainted=False,
                          domain="Writing ")
    assert dest.relative_path == "Knowledge/Learned/domains/writing.md"
    dest = c.route_for_v2("cognition", answer, ddd_exists=True, project_hint=None, tainted=False,
                          domain="Rust", existing_domains=("rust",))
    assert dest.relative_path == "Knowledge/Learned/domains/rust.md"
    with pytest.raises(ValueError, match="unknown_domain"):
        c.route_for_v2("cognition", answer, ddd_exists=True, project_hint=None, tainted=False, domain="rust")
    # Learned/ root cards and reserved '_' names are never domains
    assert "2026-04-25-agent-harnesses-are-dead" not in c.known_domains(())
    assert "_common" not in c.known_domains(("_common",))
    assert c.PARKED_DEFER_REASONS == frozenset({"unknown_domain", "missing_section"})


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
    with pytest.raises(ValidationError):
        c.EntailmentVerdict(entailed=True, reason="")


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
        for kind, answer in _cases():
            dest = c.route_for_v2(kind, answer, ddd_exists=True, project_hint="SwarmAI",
                                  tainted=False, domain="writing",
                                  source_url="https://docs.example.com/x")
            routed.append(dest)
        assert any(d.store == "DDD" and d.relative_path.startswith("Projects/SwarmAI/") for d in routed)
        assert os.listdir(cwd) == []
    finally:
        cwd.chmod(0o755)
