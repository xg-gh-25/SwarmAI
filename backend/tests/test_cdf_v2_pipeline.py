"""Focused tests for CDF v2 routing (design 2026-10-30-cdf-agent-decides: the destination
is the extractor's declaration; no model call; the every-turn files take what the user's
words or a measurement show)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ingestion.pipeline_v2 import run_unit_v2


def row(unit: dict, *, tainted: bool = False, project_hint: str | None = None,
        source: str = "S2") -> dict:
    return {
        "id": "candidate", "unit_id": "candidate.unit", "unit_kind": unit["kind"],
        "source_v2": source, "payload": json.dumps(unit, ensure_ascii=False),
        "evidence_root_ids": json.dumps(["root-1"]),
        "envelope": json.dumps({"root_ids": ["root-1"], "lineage_root_ids": ["root-1"],
                                "strength": "inferred" if tainted else "user_explicit",
                                "read_external": tainted, "cited_external": tainted,
                                "user_authored_text": unit.get("what", "")}),
        "project_candidates": json.dumps([project_hint] if project_hint else []),
        "project_hint": project_hint,
    }


def cognition(**overrides) -> dict:
    value = {
        "kind": "cognition", "type": "guideline", "scope": "world",
        "title": "Verify current state", "what": "Verify current state before editing.",
        "applicability": "When changing a repository.", "evidence": ["event:one"],
        "user_quote": "Verify current state before editing",
    }
    value.update(overrides)
    return value


@pytest.fixture
def words(monkeypatch):
    monkeypatch.setattr("core.ingestion.pipeline_v2._user_texts",
                        lambda unit, row: ["Verify current state before editing, always."])


def _widget(tmp_path: Path) -> None:
    und = tmp_path / "Projects/Widget/2-understanding"
    und.mkdir(parents=True)
    (und / "IMPROVEMENT.md").write_text("# I\n\n## What to Watch For\n", encoding="utf-8")


def test_the_users_words_reach_memory_and_a_unit_without_them_the_general_brain(tmp_path, words):
    context = tmp_path / ".context"
    context.mkdir()
    (context / "MEMORY.md").write_text("# Memory\n\n## Guidelines\n", encoding="utf-8")
    trusted = run_unit_v2(row(cognition()), tmp_path)
    assert trusted.decision == "write" and trusted.destination.store == "MEMORY"
    unsaid = run_unit_v2(row(cognition(user_quote=None)), tmp_path)
    assert (unsaid.destination.store, unsaid.destination.rerouted_from) == ("COMMON_DDD", "MEMORY")
    external = run_unit_v2(row(cognition(), tainted=True), tmp_path)
    assert external.destination.store == "COMMON_DDD" and external.version_vector["basis"] == "external"


def test_a_project_without_a_ddd_goes_to_the_general_brain(tmp_path, words):
    decision = run_unit_v2(row(cognition(scope="project:Widget"), project_hint="Widget"), tmp_path)
    assert (decision.destination.store, decision.destination.relative_path) == (
        "COMMON_DDD", "Knowledge/Library/widget.md")


@pytest.mark.parametrize(
    "unit,expected",
    [
        ({"kind": "event", "when": "2026-10-27", "title": "Landed CDF",
          "what": "The CDF pipeline landed.", "impact": "Knowledge now compounds.",
          "event_kind": "event", "evidence": ["event:one"]},
         ("TIMELINE", ".context/TIMELINE.md")),
        ({"kind": "gap", "gap_kind": "risk", "scope": "project:Widget",
          "title": "Missing fallback", "what": "The fallback is absent.",
          "proposed_next": "Add and validate the fallback.", "evidence": ["event:one"]},
         ("DDD", "Projects/Widget/2-understanding/IMPROVEMENT.md")),
        ({"kind": "reference", "reference_kind": "note", "title": "CDF notes",
          "summary": "These notes explain the complete CDF contract.",
          "body": "The reference remains useful outside the source conversation.",
          "evidence": ["event:one"]},
         ("KNOWLEDGE", "Knowledge/Notes/")),
    ],
)
def test_every_non_cognition_kind_reaches_its_store(tmp_path, words, unit, expected) -> None:
    _widget(tmp_path)
    decision = run_unit_v2(row(unit, project_hint="Widget"), tmp_path)
    assert decision.decision == "write", decision.reason
    assert (decision.destination.store, decision.destination.relative_path) == expected


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
    result = run_unit_v2(row(unit), tmp_path)
    assert (result.decision, result.reason) == ("reject", "already_exists")


def test_a_gap_takes_the_project_its_source_declared_in_the_snapshot(tmp_path: Path, words) -> None:
    """S3, S4 and S6 declare the project as ``project_snapshot.project``."""
    _widget(tmp_path)
    gap = {"kind": "gap", "gap_kind": "risk", "scope": "agent",
           "title": "Missing fallback", "what": "The fallback is absent.",
           "proposed_next": "Add and validate the fallback.", "evidence": ["event:one"]}
    queued = row(gap, source="S3")
    queued["project_snapshot"] = json.dumps({"project": "Widget", "repos": {}})
    decision = run_unit_v2(queued, tmp_path)
    assert decision.decision == "write", decision.reason
    assert decision.destination.relative_path == "Projects/Widget/2-understanding/IMPROVEMENT.md"
