"""CDF batch evaluation (scripts/cdf_batch_eval.py, design §14.2).

What is tested, end to end on a small export with three fake labelers:
- sample: every stratum drawn (weights 1), blind items carry no system outcome, and a
  second sample into the same directory is refused.
- label: strict reply parsing; an unparsed reply is retried on resume; the budget stops
  before the next call; the CDF judge's own model is refused as a labeler.
- aggregate: majority per field, ties and spot checks listed in review.html.
- score: the user's picks override the majority and resolve ties; exact metric counts
  (precision 1/2, routing 1/1, D1 1/1, extraction loss 1/1, recall 2/3); label error
  from the spot check; an untouched tie is reported unresolved; failures.md groups each
  disagreement by the system's reason.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import cdf_batch_eval as be


def _rec(uid, stage, source, state, reason, text, destination=None):
    return {"unit_id": uid, "stage": stage, "origin": "live", "at": "2026-10-28T00:00:00",
            "source": source, "state": state, "reason": reason,
            "record": {"payload": {"what": text}, "destination": destination,
                       "project_candidates": ["SwarmAI"]}}


EXPORT = [
    _rec("w1", "judge", "S2", "written", "new", "rule A", {"store": "DDD", "project": "SwarmAI"}),
    _rec("w2", "judge", "S2", "written", "new", "progress note", {"store": "MEMORY"}),
    _rec("r1", "judge", "S2", "rejected", "not_durable", "should keep"),
    _rec("e1", "judge", "S4", "rejected", "already_exists", "known fact"),
    _rec("h1", "judge", "S4", "deferred", "busy", "pending thing"),
    {"unit_id": "x1", "stage": "extraction", "origin": "live", "at": "2026-10-28T00:00:00",
     "source": "S4", "state": "rejected_at_source", "reason": "too_large_incomplete",
     "record": {"raw": {"what": "lost lesson"}}},
]

_WHY = {"null": "not_a_decision", "true": "true", "false": "false"}
L = lambda w, s, m="null", r="理由": {  # noqa: E731
    "should_write": w, "scope": s, "missing_why": _WHY[m], "reason": r}
VOTES = {
    "A": {"rule A": L(True, "project:SwarmAI", "false"), "progress note": L(False, "agent"),
          "should keep": L(True, "agent", "true"), "known fact": L(True, "world"),
          "pending thing": L(True, "agent"), "lost lesson": L(True, "user")},
    "B": {"rule A": L(True, "project:SwarmAI", "false"), "progress note": L(False, "agent"),
          "should keep": L(True, "agent", "true"), "known fact": L(True, "user"),
          "pending thing": L(False, "agent"), "lost lesson": L(True, "user")},
    "C": {"rule A": L(True, "project:SwarmAI", "false"), "progress note": L(True, "agent"),
          "should keep": L(False, "agent", "true"), "known fact": L(True, "agent"),
          "pending thing": {"error": "missing", "raw": ""}, "lost lesson": L(True, "user")},
}


def _caller(name):
    def call(prompt: str):
        for text, reply in VOTES[name].items():
            if f'"what": "{text}"' in prompt:
                return reply, 1000, 100
        raise AssertionError(prompt)
    return call


@pytest.fixture
def eval_dir(tmp_path: Path) -> Path:
    export = tmp_path / "export.jsonl"
    export.write_text("".join(json.dumps(r) + "\n" for r in EXPORT))
    out = tmp_path / "eval"
    meta = be.sample(export, out, per_stratum=15)
    assert sum(v["sampled"] for v in meta["strata"].values()) == 6
    for name in "ABC":
        be.label(out, name, _caller(name), max_usd=5)
    return out


def test_sample_is_blind_and_single_use(eval_dir: Path, tmp_path: Path) -> None:
    item = json.loads((eval_dir / "items.jsonl").read_text().splitlines()[0])
    assert set(item) == {"id", "source", "text", "project_candidates"}
    with pytest.raises(FileExistsError):
        be.sample(tmp_path / "export.jsonl", eval_dir)


def test_unparsed_reply_is_retried_and_budget_stops(eval_dir: Path) -> None:
    rows = [json.loads(l) for l in (eval_dir / "labels" / "C.jsonl").read_text().splitlines()]
    assert [r["id"] for r in rows if "error" in r] == ["h1"]
    VOTES["C"]["pending thing"] = L(False, "agent")
    try:
        assert be.label(eval_dir, "C", _caller("C"), max_usd=5)["labeled"] == 1
    finally:
        VOTES["C"]["pending thing"] = {"error": "missing", "raw": ""}
    fresh = eval_dir / "labels"
    (fresh / "D.jsonl").unlink(missing_ok=True)
    result = be.label(eval_dir, "D", _caller("A"), max_usd=0.004)  # one call costs 0.0045
    assert result["labeled"] == 1 and result["budget_stop"] == 1
    (fresh / "D.jsonl").unlink()


def test_parse_label_is_strict() -> None:
    assert be.parse_label(L(True, "world"))["scope"] == "world"
    assert be.parse_label(L(True, "world"))["missing_why"] is None
    for bad in ({**L(True, "world"), "should_write": "yes"}, L(True, "team"),
                {**L(True, "world"), "missing_why": "maybe"}):
        with pytest.raises(ValueError):
            be.parse_label(bad)


def test_labeler_asks_through_structured(monkeypatch) -> None:
    import jobs.bedrock as jb
    seen = {}

    def fake(prompt, *, schema, model_id, usage, **kw):
        seen.update(schema=schema, model=model_id)
        usage.update(input_tokens=7, output_tokens=3)
        return L(False, "agent")

    monkeypatch.setattr(jb, "structured", fake)
    answer, tokens_in, tokens_out = be.bedrock_caller("labeler-model")("p")
    assert seen == {"schema": be.LABEL_SCHEMA, "model": "labeler-model"}
    assert (answer["scope"], tokens_in, tokens_out) == ("agent", 7, 3)

    def cut(prompt, **kw):
        raise jb.StructuredOutputError("truncated", "half", "max_tokens")

    monkeypatch.setattr(jb, "structured", cut)
    assert be.bedrock_caller("m")("p")[0] == {"error": "truncated", "raw": "half"}


def test_judge_model_is_refused(eval_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr("jobs.bedrock.get_model_id", lambda key="x": "judge-model")
    with pytest.raises(SystemExit):
        be.main(["label", "--dir", str(eval_dir), "--model", "judge-model",
                 "--labeler", "Z", "--max-usd", "1"])


def test_aggregate_score_and_failures(eval_dir: Path) -> None:
    summary = be.aggregate(eval_dir, spot_per_field=50)
    assert summary["ties"] == {"should_write": 1, "scope": 1, "missing_why": 0}
    review = (eval_dir / "review.html").read_text()
    assert "h1" in review and "e1" in review and "导出 JSON" in review
    human = eval_dir / "adjudication.json"
    human.write_text(json.dumps({"adjudicator": "user", "items": {
        "h1": {"should_write": "false"},
        "w2": {"should_write": "agree"},
        "r1": {"should_write": "false"},
    }}))
    report = be.score(eval_dir, human)
    metrics = {m["metric"]: m for m in report["metrics"]}
    assert (metrics["write precision"]["k"], metrics["write precision"]["n"]) == (1, 2)
    assert (metrics["DDD routing"]["k"], metrics["DDD routing"]["n"]) == (1, 1)
    assert (metrics["D1 missing why"]["k"], metrics["D1 missing why"]["n"]) == (1, 1)
    assert (metrics["extraction loss"]["k"], metrics["extraction loss"]["n"]) == (1, 1)
    recall = metrics["write recall (weighted)"]
    assert (recall["k"], recall["n"], recall["point"]) == (2, 3, 0.6667)
    assert report["label_error"]["should_write"] == {"checked": 2, "majority_wrong": 1, "rate": 0.5}
    assert report["unresolved"] == {"scope": 1}
    failures = (eval_dir / "failures.md").read_text()
    assert "`w2` · 写了不该写的" in failures and "`x1` · 该写没写" in failures
    assert "`r1`" not in failures
    assert "judge · written · new" in failures


def test_recall_is_weighted_by_stratum_population(tmp_path: Path) -> None:
    export = tmp_path / "export.jsonl"
    rows = [_rec(f"w{i}", "judge", "S2", "written", "new", f"kept {i}") for i in range(3)]
    rows.append(_rec("r", "judge", "S2", "rejected", "not_durable", "dropped"))
    export.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = tmp_path / "eval"
    be.sample(export, out, per_stratum=1)  # one of three written items, weight 3
    for name in "ABC":
        be.label(out, name, lambda prompt: (L(True, "agent"), 10, 1), max_usd=1)
    be.aggregate(out)
    recall = {m["metric"]: m for m in be.score(out)["metrics"]}["write recall (weighted)"]
    assert (recall["k"], recall["n"]) == (1, 2)
    assert recall["point"] == 0.75
