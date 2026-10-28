"""CDF pure pipeline and offline CLI integration tests.

Methodology: synthetic corpus snapshots and recorded judges avoid network calls.
Key invariants: record replay is byte-deterministic and corpus bytes never change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from core.ingestion.contracts import Candidate
from core.ingestion.pipeline import run_unit
from core.ingestion.prompts import UntypedOutput, parse_card1, parse_card3, parse_card3b
from scripts.ingestion_holdout_size import render_table
from scripts.ingestion_replay import RecordJudge, replay

QUALITY = {"atomic": True, "valuable": True, "self_contained": True, "durable": True,
           "evidenced": True, "internally_consistent": True, "concise": True}
CARD1_USER = {"shape": "UserCross", "content_form": "rule", "type": "guideline",
              "user_kind": "preference", "applicability": "when writing", "claims": ["use Chinese"],
              "quality": QUALITY, "reroute_to": None}
VECTOR = {"model_id": "fixture-model", "provider": "fixture", "provider_version": "1",
          "temperature": 0.0, "top_p": 1.0, "seed": 7}


def make_corpus(root: Path) -> None:
    (root / ".context").mkdir(parents=True)
    for name in ("MEMORY.md", "EVOLUTION.md", "STEERING.md", "USER.md", "TOOLS.md", "SOUL.md", "AGENT.md"):
        (root / ".context" / name).write_text("", encoding="utf-8")
    for folder in ("Notes", "Library", "Learned"):
        (root / "Knowledge" / folder).mkdir(parents=True)


def make_candidate() -> Candidate:
    return Candidate.model_validate({
        "id": "c1", "source": "remember",
        "payload": {"kind": "remember", "text": "Use Chinese when writing user-facing demos."},
        "envelope": {"source_event_ids": ["e1"], "evidence": "user_explicit", "tainted": False},
        "project_snapshot": {}, "cluster_id": "cluster-1", "origin": None,
    })


def test_strict_card1_parser_rejects_wrappers_and_unknown_fields():
    with pytest.raises(UntypedOutput):
        parse_card1("```json\n{}\n```")
    bad = dict(CARD1_USER, extra="no")
    with pytest.raises(UntypedOutput):
        parse_card1(json.dumps(bad))


def test_full_pipeline_carries_all_cards_and_version_vector(tmp_path: Path):
    make_corpus(tmp_path)
    decision = run_unit(make_candidate(), tmp_path, RecordJudge({"card1": CARD1_USER, "version_vector": VECTOR}))
    assert (decision.decision, decision.action, decision.reason) == ("write", "add", "new")
    assert decision.card0 and decision.card0.passed
    assert decision.card1 and decision.card1.shape == "UserCross"
    assert decision.card2 and decision.card2.store == "STEERING"
    assert [item.rule for item in decision.rules] == ["C1", "C2", "C3"]
    assert decision.card3 and decision.card3.status == "OK_EMPTY"
    assert decision.version_vector["model_id"] == "fixture-model"
    assert decision.version_vector["corpus_digest"]


def test_pipeline_rejects_tainted_rule_before_card2(tmp_path: Path):
    make_corpus(tmp_path)
    raw = make_candidate().model_dump(mode="json", by_alias=True)
    raw["envelope"]["tainted"] = True
    decision = run_unit(Candidate.model_validate(raw), tmp_path, RecordJudge({"card1": CARD1_USER}))
    assert decision.reason == "tainted_rule"
    assert decision.card2 is None


def test_pipeline_defer_on_ambiguous_project(tmp_path: Path):
    make_corpus(tmp_path)
    project = dict(CARD1_USER)
    project.update(shape="ProjectSingle", project="P", topic="architecture")
    project.pop("user_kind")
    decision = run_unit(make_candidate(), tmp_path, RecordJudge({"card1": project}))
    assert (decision.decision, decision.reason) == ("defer", "ambiguous_project")


def _fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    make_corpus(corpus)
    candidate = make_candidate().model_dump(mode="json", by_alias=True)
    candidate["recorded"] = {"card1": CARD1_USER, "version_vector": VECTOR}
    source = tmp_path / "candidates.jsonl"
    source.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    return source, corpus


def _args(source: Path, corpus: Path) -> argparse.Namespace:
    return argparse.Namespace(candidates=source, corpus=corpus, mode="record", runs=3,
                              exclude_origin=False, model_id=None, output=None, recordings_out=None)


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_record_replay_is_deterministic_and_never_writes_corpus(tmp_path: Path):
    source, corpus = _fixture_files(tmp_path)
    before = snapshot(corpus)
    first = replay(_args(source, corpus))
    second = replay(_args(source, corpus))
    assert first == second
    assert snapshot(corpus) == before
    assert first[0]["stable"] is True
    assert set(first[0]["card_consistency"].values()) == {1.0}
    assert first[0]["version_vector"]["contract_version"]


def test_replay_refuses_outputs_inside_corpus(tmp_path: Path):
    source, corpus = _fixture_files(tmp_path)
    args = _args(source, corpus)
    args.output = corpus / "result.jsonl"
    with pytest.raises(SystemExit, match="outside"):
        replay(args)
    assert not args.output.exists()


def test_holdout_output_is_pinned():
    assert render_table() == """| metric | n | k | assumed true rate | threshold | label error | lower 95% | power | minimal n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| write precision | 220 | 202 | 0.97 | 0.88 | 0.042 | 0.8811 | 0.8061 | 218 |
| write recall | 180 | 145 | 0.83 | 0.75 | 0.000 | 0.7506 | 0.8350 | 167 |
| DDD routing | 150 | 129 | 0.88 | 0.80 | 0.000 | 0.8047 | 0.8126 | 135 |
| D1 missing why | 130 | 123 | 0.96 | 0.90 | 0.000 | 0.9012 | 0.8491 | 116 |
| duplicate unchanged | 90 | 86 | 0.97 | 0.90 | 0.000 | 0.9012 | 0.8659 | 76 |"""


def test_strict_card3_and_card3b_parsers():
    hit = [{"anchor": "a", "score": 1.0, "overlap": "same", "same_topic": True,
            "claim_verdicts": [{"verdict": "agrees", "existing_sentence": "x"}],
            "scope_relation": "equal", "candidate_evidence": "measured",
            "existing_evidence": "review", "candidate_observed_at": None,
            "existing_observed_at": None}]
    assert parse_card3(json.dumps(hit))[0].anchor == "a"
    with pytest.raises(UntypedOutput):
        parse_card3("not-json")
    effective = {"claims": [{"text": "old", "sentence": "old", "disposition": "preserve",
                              "candidate_claim": None, "output_sentence": "old"}],
                 "effective_unit": "old plus new", "card1": CARD1_USER}
    assert parse_card3b(json.dumps(effective)).effective_unit == "old plus new"
    with pytest.raises(UntypedOutput):
        parse_card3b(json.dumps({"claims": [], "effective_unit": "missing card"}))


def test_replay_aborts_if_corpus_changes_during_run(tmp_path: Path, monkeypatch):
    """The before/after digest guard must catch any write into the corpus."""
    import scripts.ingestion_replay as replay_mod
    source, corpus = _fixture_files(tmp_path)
    original = replay_mod.normalize_batch

    def tamper(candidates):
        (corpus / "intruder.md").write_text("x", encoding="utf-8")
        return original(candidates)

    monkeypatch.setattr(replay_mod, "normalize_batch", tamper)
    with pytest.raises(RuntimeError, match="read-only corpus changed"):
        replay(_args(source, corpus))


def test_judge_sends_temperature_and_no_top_p(monkeypatch):
    """Claude 4.5+ on Bedrock rejects temperature + top_p together (ValidationException).
    The judge now asks through jobs.bedrock.text(), which takes no top_p at all."""
    import inspect
    import core.ingestion.llm as llm
    import jobs.bedrock as jb

    sent = {}

    def fake_text(prompt, **kwargs):
        sent.update(kwargs)
        return "x"

    monkeypatch.setattr(llm, "text", fake_text)
    result = llm.BedrockJudge(model_id="m")._invoke("prompt", lambda raw: raw)
    assert sent["temperature"] == 0.0 and "top_p" not in sent
    assert "top_p" not in inspect.signature(jb.text).parameters
    assert result.vector["top_p"] is None and result.vector["temperature"] == 0.0


def test_replay_temp_inbox_is_not_beside_the_corpus(tmp_path: Path, monkeypatch):
    import scripts.ingestion_replay as replay_mod
    source, corpus = _fixture_files(tmp_path)
    monkeypatch.delenv("KIROCREW_SCRATCH", raising=False)
    created = []
    real = replay_mod.tempfile.TemporaryDirectory

    def spy(*a, **k):
        d = real(*a, **k)
        created.append(Path(d.name))
        return d

    monkeypatch.setattr(replay_mod.tempfile, "TemporaryDirectory", spy)
    replay(_args(source, corpus))
    assert created and all(corpus.parent not in p.parents and corpus not in p.parents for p in created)


# --- r15.1-v2 prompt contract (online probe: every reply was REJECT untyped) ---

def test_card1_prompt_carries_the_full_output_contract():
    from core.ingestion.prompts import card1_prompt
    prompt = card1_prompt("some lesson", ("SwarmAI",))
    for token in ("applicability", "claims", "quality", "self_contained", "internally_consistent",
                  "reference_kind", "user_kind", "agent_kind", "global_relevance", "topic",
                  "Reference", "UserCross", "UserSingle", "Agent", "ProjectSingle", "WorldSingle",
                  "WorldCrossGlobal", '"SwarmAI"', '"$defs"'):
        assert token in prompt, token


def test_card3_and_card3b_prompts_carry_their_contracts():
    from core.ingestion.prompts import card3_prompt, card3b_prompt
    p3 = card3_prompt("c", [{"anchor": "a#s#1", "text": "t", "evidence": "review"}])
    for token in ("overlap", "same_topic", "claim_verdicts", "scope_relation", "existing_sentence", "a#s#1"):
        assert token in p3, token
    assert '"review"' not in p3  # pipeline-owned facts are not shown to the judge
    p3b = card3b_prompt("old", "new")
    for token in ("disposition", "effective_unit", "output_sentence", "card1", '"$defs"'):
        assert token in p3b, token


def test_prompt_examples_validate_against_the_parsers():
    import re as _re
    from core.ingestion import prompts
    example = _re.search(r"Example \(ProjectSingle\):\n(\{.*\})\n", prompts._CARD1_GUIDE).group(1)
    assert prompts.parse_card1(example).shape == "ProjectSingle"
    example3 = _re.search(r"Example: (\[.*\])\n", prompts._CARD3_GUIDE).group(1)
    anchor = json.loads(example3)[0]["anchor"]
    assert prompts.parse_card3(example3, [{"anchor": anchor, "score": 1.0}])[0].anchor == anchor


@pytest.mark.parametrize("wrap,ok", [
    ("{body}", True), ("```json\n{body}\n```", True), ("```\n{body}\n```\n", True),
    ("Here it is:\n```json\n{body}\n```", False), ("```json\n```json\n{body}\n```\n```", False),
    ("{body} trailing", False),
])
def test_one_surrounding_fence_is_allowed_and_nothing_else(wrap, ok):
    from core.ingestion.prompts import extract_json
    text = wrap.format(body='{"a": 1}')
    if ok:
        assert extract_json(text) == {"a": 1}
    else:
        with pytest.raises(UntypedOutput):
            extract_json(text)


def test_card3_pipeline_facts_override_the_model():
    from core.ingestion.prompts import parse_card3
    reply = json.dumps([{"anchor": "a", "overlap": "related", "same_topic": True,
                         "claim_verdicts": [{"verdict": "refutes"}], "scope_relation": "equal",
                         "candidate_evidence": "user_explicit", "existing_evidence": "inferred", "score": 99}])
    hits = [{"anchor": "a", "score": 0.5, "candidate_evidence": "inferred", "evidence": "review"}]
    out = parse_card3(reply, hits)[0]
    assert (out.candidate_evidence, out.existing_evidence, out.score) == ("inferred", "review", 0.5)
    with pytest.raises(UntypedOutput):
        parse_card3(reply.replace('"a"', '"invented"'), hits)


def test_capturing_judge_records_raw_text_of_untyped_replies():
    from scripts.ingestion_replay import CapturingJudge

    class Inner:
        def card1(self, text, projects, context=""):
            raise UntypedOutput("untyped", raw="```json\n{\"atomic_claims\": []}\n```")

    judge = CapturingJudge(Inner())
    with pytest.raises(UntypedOutput):
        judge.card1("t", ())
    assert judge.recorded["card1"] == ["```json\n{\"atomic_claims\": []}\n```"]


def test_card1_prompt_carries_source_context():
    """Smoke: 4/5 dev units were 'speculative' because the judge never saw where the text came from."""
    from core.ingestion.prompts import card1_prompt, source_context
    ctx = source_context("distillation", "inferred")
    prompt = card1_prompt("lesson", ("SwarmAI",), ctx)
    assert "SOURCE_CONTEXT: origin: a lesson distilled at session end from a real working session" in prompt
    assert "envelope evidence strength: inferred" in prompt
    assert "SOURCE_CONTEXT: unknown" in card1_prompt("lesson", ())
