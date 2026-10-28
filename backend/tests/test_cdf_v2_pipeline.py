"""Focused tests for CDF v2 card/routing behavior."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ingestion.contracts import (
    Agent, ProjectSingle, QualityAnswers, UserCross, WorldCrossGlobal,
)
from core.ingestion.contracts_v2 import EventAnswer, GapAnswer, ReferenceAnswerV2
from core.ingestion.pipeline_v2 import run_unit_v2


QUALITY = QualityAnswers(
    atomic=True, valuable=True, self_contained=True, durable=True,
    evidenced=True, internally_consistent=True, concise=True,
)


class Judge:
    def __init__(self, answer):
        self.answer = answer

    def card1(self, *_args):
        return self.answer

    def card1_v2(self, *_args):
        return self.answer

    def entailment(self, *_args):
        return {"entailed": True, "reason": "explicit"}


def row(unit: dict, *, tainted: bool = False, project_hint: str | None = None,
        source: str = "S2") -> dict:
    return {
        "id": "candidate", "unit_id": "candidate.unit", "unit_kind": unit["kind"],
        "source_v2": source, "payload": json.dumps(unit, ensure_ascii=False),
        "evidence_root_ids": json.dumps(["root-1"]),
        "envelope": json.dumps({"root_ids": ["root-1"], "lineage_root_ids": ["root-1"],
                                "strength": "inferred" if tainted else "user_explicit",
                                "tainted": tainted, "user_authored_text": unit.get("what", "")}),
        "project_candidates": json.dumps([project_hint] if project_hint else []),
        "project_hint": project_hint,
    }


def cognition(**overrides) -> dict:
    value = {
        "kind": "cognition", "type": "guideline", "scope": "world",
        "title": "Verify current state", "what": "Verify current state before editing.",
        "applicability": "When changing a repository.", "evidence": ["event:one"],
    }
    value.update(overrides)
    return value


def test_cognition_trusted_per_turn_and_tainted_reroute(tmp_path: Path) -> None:
    context = tmp_path / ".context"
    context.mkdir()
    (context / "MEMORY.md").write_text("# Memory\n\n## Guidelines\n", encoding="utf-8")
    answer = WorldCrossGlobal(
        shape="WorldCrossGlobal", type="guideline", applicability="When editing.",
        claims=["Verify current state."], quality=QUALITY, global_relevance=True,
    )
    trusted = run_unit_v2(row(cognition()), tmp_path, Judge(answer))
    assert trusted.decision == "write"
    assert trusted.destination.store == "MEMORY"

    tainted = run_unit_v2(row(cognition(), tainted=True), tmp_path, Judge(answer))
    assert tainted.decision == "write"
    assert tainted.destination.store == "COMMON_DDD"
    assert tainted.destination.relative_path == "Knowledge/Learned/_common/world.md"
    assert tainted.destination.rerouted_from == "MEMORY"


def test_no_ddd_keeps_cognition_type_and_routes_to_repo(tmp_path: Path) -> None:
    unit = cognition(scope="project:Widget")
    answer = ProjectSingle(
        shape="ProjectSingle", type="guideline", applicability="When editing Widget.",
        claims=["Use one source of truth."], quality=QUALITY,
        project="Widget", topic="architecture",
    )
    decision = run_unit_v2(row(unit, project_hint="Widget"), tmp_path, Judge(answer))
    assert decision.decision == "write"
    assert decision.destination.store == "COMMON_DDD"
    assert decision.destination.relative_path == "Knowledge/Learned/repos/widget.md"
    assert decision.destination.section == "Conventions"


@pytest.mark.parametrize(
    "unit,answer,expected",
    [
        ({"kind": "event", "when": "2026-10-27", "title": "Landed CDF",
          "what": "The CDF pipeline landed.", "impact": "Knowledge now compounds.",
          "event_kind": "event", "evidence": ["event:one"]},
         EventAnswer(shape="Event", significance=["milestone"], self_contained=True, evidenced=True),
         ("TIMELINE", ".context/TIMELINE.md")),
        ({"kind": "gap", "gap_kind": "risk", "scope": "project:Widget",
          "title": "Missing fallback", "what": "The fallback is absent.",
          "proposed_next": "Add and validate the fallback.", "evidence": ["event:one"]},
         GapAnswer(shape="Gap", actionable=True, evidenced=True, self_contained=True,
                   project="Widget"),
         ("COMMON_DDD", "Knowledge/Learned/repos/widget.md")),
        ({"kind": "reference", "reference_kind": "note", "title": "CDF notes",
          "summary": "These notes explain the complete CDF contract.",
          "body": "The reference remains useful outside the source conversation.",
          "evidence": ["event:one"]},
         ReferenceAnswerV2(shape="ReferenceV2", reference_kind="note", quality=QUALITY),
         ("KNOWLEDGE", "Knowledge/Notes/")),
    ],
)
def test_every_non_cognition_kind_reaches_its_store(tmp_path: Path, unit, answer, expected) -> None:
    decision = run_unit_v2(row(unit, project_hint="Widget"), tmp_path, Judge(answer))
    assert decision.decision == "write"
    assert (decision.destination.store, decision.destination.relative_path) == expected


def test_kind_specific_quality_rejections(tmp_path: Path) -> None:
    event = {"kind": "event", "when": "2026-10-27", "title": "Routine",
             "what": "Ran a routine command.", "outcome": "It completed.",
             "evidence": ["event:one"]}
    decision = run_unit_v2(
        row(event), tmp_path,
        Judge(EventAnswer(shape="Event", significance=[], self_contained=True, evidenced=True)),
    )
    assert (decision.decision, decision.reason) == ("reject", "not_significant")

    reference = {"kind": "reference", "reference_kind": "note", "title": "Weak",
                 "summary": "This note is complete.", "body": "It has no later value.",
                 "evidence": ["event:one"]}
    weak = QUALITY.model_copy(update={"valuable": False})
    decision = run_unit_v2(row(reference), tmp_path,
                           Judge(ReferenceAnswerV2(shape="ReferenceV2", reference_kind="note",
                                                   quality=weak)))
    assert (decision.decision, decision.reason) == ("reject", "no_reference_value")


def test_unknown_domain_is_parked_and_entailment_failure_defers(tmp_path: Path) -> None:
    world = cognition()
    answer = WorldCrossGlobal(
        shape="WorldCrossGlobal", type="guideline", applicability="When editing.",
        claims=["Keep it scoped."], quality=QUALITY, global_relevance=False,
    )
    decision = run_unit_v2(row(world), tmp_path, Judge(answer))
    assert (decision.decision, decision.reason) == ("defer", "unknown_domain")

    class Broken(Judge):
        def entailment(self, *_args):
            raise RuntimeError("offline")

    s1_answer = UserCross(
        shape="UserCross", type="guideline", applicability="When responding.",
        claims=["Use Chinese."], quality=QUALITY, user_kind="preference",
    )
    decision = run_unit_v2(row(cognition(scope="user", user_kind="preference"), source="S1"),
                           tmp_path, Broken(s1_answer))
    assert (decision.decision, decision.reason) == ("defer", "entailment_unavailable")


def test_normalize_preserves_semantic_fields_except_outer_whitespace() -> None:
    from core.ingestion.contracts import Candidate, Envelope, RememberPayload
    from core.ingestion.normalize import normalize_candidate
    text = "  Keep session_id=abc, /scratch/runtime-x/path, abcdef1234567.\nSecond line.  "
    candidate = Candidate(
        id="c", source="remember", payload=RememberPayload(text=text),
        envelope=Envelope(source_event_ids=["e"], evidence="user_explicit", tainted=False),
        cluster_id="session",
    )
    [group] = normalize_candidate(candidate)
    assert group.text == text.strip()


def test_per_turn_card3_corpus_excludes_common_reroutes(tmp_path: Path) -> None:
    from core.ingestion.card3 import corpus_paths
    from core.ingestion.contracts_v2 import DestinationV2
    common = tmp_path / "Knowledge/Learned/_common/world.md"
    common.parent.mkdir(parents=True)
    common.write_text("# Common\n", encoding="utf-8")
    memory = tmp_path / ".context/MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# Memory\n", encoding="utf-8")
    paths = corpus_paths(tmp_path, DestinationV2(
        store="MEMORY", relative_path=".context/MEMORY.md", section="Guidelines"))
    assert memory in paths
    assert common not in paths


def test_owned_semantic_paths_have_no_constant_truncation_slice() -> None:
    import ast
    import inspect
    from core.ingestion import normalize, pipeline_v2, writer

    for module in (normalize, pipeline_v2, writer):
        tree = ast.parse(inspect.getsource(module))
        semantic_names = {"title", "text", "body", "summary", "what", "how", "why",
                          "applicability", "rejected_alternatives", "impact", "outcome",
                          "follow_up", "proposed_next"}
        constant_slices = [node for node in ast.walk(tree)
                           if isinstance(node, ast.Subscript)
                           and isinstance(node.slice, ast.Slice)
                           and isinstance(node.slice.upper, ast.Constant)
                           and isinstance(node.slice.upper.value, int)
                           and any(name in ast.unparse(node.value) for name in semantic_names)
                           and "sha256" not in ast.unparse(node.value)]
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and getattr(node.func, "attr", "") in {"shorten", "truncate"}]
        assert constant_slices == []
        assert calls == []


def test_event_key_duplicate_is_rejected_without_reinforcement(tmp_path: Path) -> None:
    unit = {"kind": "event", "when": "2026-10-27", "title": "Landed CDF",
            "what": "The CDF pipeline landed.", "impact": "Knowledge now compounds.",
            "event_kind": "event", "evidence": ["event:one"]}
    timeline = tmp_path / ".context/TIMELINE.md"
    timeline.parent.mkdir(parents=True)
    import hashlib
    material = json.dumps([["root-1"], "2026-10-27", "landed cdf"],
                          ensure_ascii=False, separators=(",", ":"))
    key = hashlib.sha256(material.encode()).hexdigest()
    timeline.write_text(
        "# Timeline\n\n## 2026-10\n\n### 2026-10-27 Landed CDF\n"
        "- what: The CDF pipeline landed.\n"
        f"<!-- cand:prior | event_key:{key} | source:S3 | event_kind:event -->\n",
        encoding="utf-8",
    )
    answer = EventAnswer(shape="Event", significance=["milestone"],
                         self_contained=True, evidenced=True)
    result = run_unit_v2(row(unit), tmp_path, Judge(answer))
    assert (result.decision, result.reason) == ("reject", "already_exists")
