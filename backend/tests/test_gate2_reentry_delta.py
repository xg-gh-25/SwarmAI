"""DELIVER re-entry re-reviews only the delta (XG 2026-10-20).

What is tested (``artifact_cli._reentry_delta``, with the snapshot, marker store and
diff stubbed):
- nobody reviewed yet, no usable snapshot, a late snapshot → None (full prompts);
- an expert that reviewed, no change since → no spawn (prompt_path dropped);
- a change of <= 20 lines and no new file → no spawn, recorded;
- a larger change → ``<id>-delta.md`` with the round-1 prompt minus its change section
  plus the delta; an expert that did not review keeps its full prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))
import artifact_cli as cli  # noqa: E402


@pytest.fixture
def rig(tmp_path, monkeypatch):
    prompts = tmp_path / "evidence" / "prompts"
    prompts.mkdir(parents=True)
    experts = []
    for eid in ("correctness", "security"):
        p = prompts / f"{eid}.md"
        p.write_text(f"EXPERT-ID: {eid}\nRules.\n\n## The change\nWHOLE CHANGE\n")
        experts.append({"id": eid, "prompt_path": str(p)})
    state = {"man": {"taken_at": "2026-10-20T01:00:00+00:00", "late": False},
             "done": {"by_expert": {"correctness": 1}}, "diff": ("", [], [])}
    monkeypatch.setattr(cli, "_round1_manifest", lambda *a: (state["man"], None))
    monkeypatch.setattr(cli, "_completed_spawns", lambda *a: state["done"])
    monkeypatch.setattr(cli, "_gate2_fix_diff", lambda *a: state["diff"])
    return tmp_path, experts, state


def _diff(n: int) -> str:
    return "--- a/x.py\n+++ b/x.py\n@@\n" + "".join(f"+line {i}\n" for i in range(n))


def test_no_snapshot_late_or_nobody_reviewed_keeps_full_prompts(rig):
    run_dir, experts, state = rig
    state["done"] = {"by_expert": {}}
    assert cli._reentry_delta("r", run_dir, {}, {}, experts) is None
    state["done"] = {"by_expert": {"correctness": 1}}
    state["man"] = {**state["man"], "late": True}
    assert cli._reentry_delta("r", run_dir, {}, {}, experts) is None
    state["man"] = None
    assert cli._reentry_delta("r", run_dir, {}, {}, experts) is None


@pytest.mark.parametrize("n", [0, 5, cli.SMALL_REENTRY_DELTA_LINES])
def test_no_or_small_change_does_not_respawn_the_reviewer(rig, n):
    run_dir, experts, state = rig
    state["diff"] = (_diff(n) if n else "", ["x.py"] if n else [], [])
    out = cli._reentry_delta("r", run_dir, {}, {}, experts)
    assert out["respawn"] == [] and out["experts"] == ["correctness"]
    assert "prompt_path" not in experts[0] and experts[1]["prompt_path"].endswith("security.md")


def test_a_new_file_in_a_small_delta_is_reviewed(rig):
    run_dir, experts, state = rig
    state["diff"] = (_diff(3), ["x.py"], ["new.py"])
    out = cli._reentry_delta("r", run_dir, {}, {}, experts)
    assert out["respawn"] == ["correctness"]


def test_a_larger_change_gets_a_delta_prompt(rig):
    run_dir, experts, state = rig
    state["diff"] = (_diff(40), ["x.py"], [])
    out = cli._reentry_delta("r", run_dir, {}, {}, experts)
    assert out["respawn"] == ["correctness"] and out["delta_lines"] == 40
    p = Path(experts[0]["prompt_path"])
    assert p.name == "correctness-delta.md"
    text = p.read_text()
    assert text.startswith("EXPERT-ID: correctness") and "WHOLE CHANGE" not in text
    assert "reentry.diff" in text and "DELTA" in text
    assert experts[1]["prompt_path"].endswith("security.md")   # did not review: full prompt
    assert any(str(x).endswith("reentry.diff") for x in out["paths"])
