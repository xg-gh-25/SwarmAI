"""REFLECT without a DDD: lessons go to the shared Library (design
docs/design/2026-10-25-knowledge-delivery-gaps.md §3.1, part B / G1).

A lesson whose project or DDD doc does not exist used to stay in the run only
(``kept_in_run``), read by nothing. Now it is written to
``Knowledge/Library/pipeline-lessons/<repo>.md`` (in the Recall index, and delivered by
``repo_guidance`` in part A). Tested: ``reflect_job.route`` against a temporary
workspace, and one run end to end through the CLI rig.

Invariants:
- a domain lesson goes to the 经验 section under its DDD target; an instance /
  product lesson goes to 待审 (it changes the agent's own rules: never in force there);
- the file names its main checkout; a second repo with the same name gets its own
  ``<name>-<sha8>.md``;
- a multi-repo run's lesson without ``repo`` stays ``kept_in_run``; a ``repo`` that is
  not one of the run's is refused at prepare;
- the same lesson again is a reinforcement line, not a second entry; a replay of the
  same run's lesson writes nothing;
- a symlinked directory on the way is refused, nothing written through it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from core.run_protocol import reflect, reflect_job

LESSON = {"id": "L1", "statement": "Give background work its own worker pool so a "
          "blocking call never starves the interactive path.",
          "evidence": ["e" * 40], "relation": {"kind": "new", "entry": None}}
LIB = Path("Knowledge") / "Library" / "pipeline-lessons"


def _route(ws: Path, destination: str, repos: dict, *, lesson=LESSON, run_id="run_x",
           project: str = "P") -> str:
    return reflect_job.route(lesson, destination, workspace=ws, project=project,
                             run_id=run_id, repos=repos)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _repo(tmp_path: Path, name: str = "shop", parent: str = "code") -> str:
    root = tmp_path / parent / name
    root.mkdir(parents=True)
    return str(root)


def test_a_domain_lesson_without_a_ddd_goes_to_the_library(tmp_path):
    ws = _ws(tmp_path)
    root = _repo(tmp_path)
    out = _route(ws, "domain:P:TECH:Conventions", {"shop": root})
    assert out == f"library:{LIB / 'shop.md'}"
    text = (ws / LIB / "shop.md").read_text(encoding="utf-8")
    assert root in text and "## 经验" in text
    assert "worker pool" in text and "TECH:Conventions" in text and "run_x L1" in text
    assert "## 待审" in text and text.index("worker pool") < text.index("## 待审")


def test_an_instance_lesson_goes_to_pending_review(tmp_path):
    ws = _ws(tmp_path)
    out = _route(ws, "MEMORY", {"shop": _repo(tmp_path)}, project="Nope")
    assert out.startswith("library:")
    text = (ws / LIB / "shop.md").read_text(encoding="utf-8")
    assert text.index("## 待审") < text.index("worker pool")
    assert "[MEMORY]" in text


def test_a_project_with_a_ddd_doc_still_gets_it_there(tmp_path):
    ws = _ws(tmp_path)
    doc = ws / "Projects" / "R" / "2-understanding" / "TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# TECH\n\n## Conventions\n\n- existing\n")
    out = _route(ws, "domain:R:TECH:Conventions", {"shop": _repo(tmp_path)}, project="R")
    assert out in reflect.APPLIED_RESULTS and "worker pool" in doc.read_text()
    assert not (ws / LIB).exists()


def test_two_repos_with_one_name_get_two_files(tmp_path):
    ws = _ws(tmp_path)
    first, second = _repo(tmp_path, parent="a"), _repo(tmp_path, parent="b")
    assert _route(ws, "domain:P:TECH:Conventions", {"shop": first}) == \
        f"library:{LIB / 'shop.md'}"
    sha = hashlib.sha256(second.encode("utf-8")).hexdigest()[:8]
    assert _route(ws, "domain:P:TECH:Conventions", {"shop": second}) == \
        f"library:{LIB / f'shop-{sha}.md'}"
    assert second in (ws / LIB / f"shop-{sha}.md").read_text(encoding="utf-8")
    assert second not in (ws / LIB / "shop.md").read_text(encoding="utf-8")


def test_a_multi_repo_lesson_needs_its_repo(tmp_path):
    ws = _ws(tmp_path)
    repos = {"shop": _repo(tmp_path), "api": _repo(tmp_path, "api")}
    assert _route(ws, "domain:P:TECH:Conventions", repos) == reflect_job.KEPT_IN_RUN
    named = {**LESSON, "repo": "api"}
    assert _route(ws, "domain:P:TECH:Conventions", repos, lesson=named) == \
        f"library:{LIB / 'api.md'}"


def test_the_same_lesson_again_reinforces_and_a_replay_writes_nothing(tmp_path):
    ws = _ws(tmp_path)
    repos = {"shop": _repo(tmp_path)}
    _route(ws, "domain:P:TECH:Conventions", repos)
    once = (ws / LIB / "shop.md").read_text(encoding="utf-8")
    _route(ws, "domain:P:TECH:Conventions", repos)                 # replay: same run
    assert (ws / LIB / "shop.md").read_text(encoding="utf-8") == once
    _route(ws, "domain:P:TECH:Conventions", repos, run_id="run_y")
    text = (ws / LIB / "shop.md").read_text(encoding="utf-8")
    assert text.count("worker pool") == 1 and "reinforced by run_y L1" in text


def test_a_symlinked_library_directory_is_refused(tmp_path):
    ws = _ws(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / "Knowledge" / "Library").mkdir(parents=True)
    os.symlink(elsewhere, ws / LIB)
    assert _route(ws, "domain:P:TECH:Conventions", {"shop": _repo(tmp_path)}) == \
        reflect_job.KEPT_IN_RUN
    assert not list(elsewhere.iterdir())


def test_process_reports_library_as_its_own_outcome(tmp_path):
    ws = _ws(tmp_path)
    repos = {"shop": _repo(tmp_path)}
    doc = {"schema": 1, "lessons": [{**LESSON, "destination": "domain:P:TECH:Conventions"}]}
    out = reflect.process(doc, judge=lambda _l: "keep",
                          writer=lambda lesson, dest: _route(ws, dest, repos))
    assert out["reflect"] == "ok"
    assert out["lessons"][0]["outcome"]["status"] == "library"


def test_prepare_takes_a_repo_only_when_it_is_the_runs():
    base = {"id": "L1", "type": "guideline", "scope": "domain:P", "statement":
            LESSON["statement"], "tell": "a blocking call", "evidence": ["e1"],
            "relation": {"kind": "new", "entry": None}}
    doc = reflect.prepare([{**base, "repo": "api"}], event_ids={"e1"}, candidates={},
                          repos={"shop", "api"})
    assert doc["lessons"][0]["repo"] == "api"
    with pytest.raises(reflect.ReflectError):
        reflect.prepare([{**base, "repo": "web"}], event_ids={"e1"}, candidates={},
                        repos={"shop", "api"})


def test_a_run_without_ddd_puts_its_lesson_in_the_library(tmp_path, monkeypatch):
    """End to end through the CLI rig (its project has no DDD docs)."""
    from tests.pipeline_v1_rig import SWARM, Rig
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    try:
        r.create()
        r.drive(until="deliver")
        lesson = {"id": "L1", "type": "guideline", "scope": "domain:P",
                  "statement": LESSON["statement"], "tell": "a blocking call on a shared pool",
                  "evidence": [r.events()[0]["event_id"]],
                  "relation": {"kind": "new", "entry": None}}
        out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": [lesson]}))
        assert out.get("ok") is True, out
        landed = r.v1("land", "--message", "fix: swap the operands" + SWARM)
        assert landed.get("ok") is True, landed
        engine = r.engine()
        assert r.state()["status"] == "completed"
        rows = json.loads((engine.run_dir / "artifacts" / "reflect.json").read_text())["lessons"]
        assert rows[0]["outcome"]["status"] == "library", rows
        target = r.ws / LIB / f"{Path(engine.contract['repo_roots']['repo']).name}.md"
        assert "worker pool" in target.read_text(encoding="utf-8")
        report = (engine.run_dir / "REPORT.md").read_text()
        assert "## 写进公共 Library 的经验" in report and str(target.relative_to(r.ws)) in report
    finally:
        r.close()
