"""Feature Map in a pipeline run: FM-1 decides from recorded facts (R7.2-R7.4, R7.6), the
TEST note names the command only with a map (R7.1, R9.1), and the REPORT and run view
carry the evidence and changed expectations (R7.5)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.feature_map import ledger, pipeline
from core.run_protocol import checks, next_step
from core.run_protocol.engine import RunEngine
from tests.feature_map_fixtures import app_data, git, make_repo  # noqa: F401 — fixture

TREE = "a" * 40
SPEC = """\
import { test, expect } from '@playwright/test';
test('title', async ({ page }) => { await page.goto('/'); await expect(page.getByTestId('title')).toHaveText('Hello'); });
"""
FEATURES = """\
schema: 1
features:
  - {id: title-a1, title: Title shows, target: web, spec: specs/title.spec.ts, expect: The title reads Hello}
"""


def _event(repo, *, tree=TREE, n_fail=0, blocking=True):
    return {"kind": "feature_checks", "payload": {"repo": repo, "code_tree": tree,
                                                   "blocking": blocking, "n_fail": n_fail,
                                                   "n_rows": 2, "report_json": "{}"}}


def _ctx(events, tree=TREE):
    return SimpleNamespace(run={"code_trees": {"app": tree}}, events=tuple(events))


def test_fm1_decides_from_recorded_facts(tmp_path, app_data):  # noqa: F811
    assert "FM-1" in checks.CHECKS and "stage" in checks.CHECKS["FM-1"].effects
    assert "test" in checks.CHECKS["FM-1"].stages
    # a failed reached walk at the current tree keeps TEST red — whatever the agent's row says
    agent_says_pass = {"kind": "validation_report", "payload": {"repo": "app", "stage": "test",
                                                                "code_tree": TREE, "n_fail": 0}}
    ok, detail = checks._feature_check(_ctx([agent_says_pass, _event("app", n_fail=1)]))
    assert not ok and "1 reached feature walk(s) failed" in detail
    # facts for an older tree are not this tree's: nothing to refuse (TS-1 refuses that report)
    assert checks._feature_check(_ctx([_event("app", tree="b" * 40, n_fail=1)]))[0]
    # the switch: listed only
    ok, detail = checks._feature_check(_ctx([_event("app", n_fail=1, blocking=False)]))
    assert ok and "listed only" in detail
    # no event (no map, nothing reached): nothing to refuse
    assert checks._feature_check(_ctx([]))[0]

    # what n_fail counts: failed at head, not red at base too, not quarantined
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)
    base = git(root, "rev-parse", "HEAD")
    (root / "site" / "index.html").write_text("<h1 data-testid='title'>Bye</h1>")
    key_r = ledger.walk_key("title-a1", "title", "regular")
    key_c = ledger.walk_key("title-a1", "title", "compact")
    from core.feature_map import files
    fmap = files.load_folder(root / ".swarmai/features")
    h = files.content_hash(fmap, fmap.features[0])

    def line(key, project, result, trigger="replay", tree=TREE, run="run_x"):
        return {"feature": "title-a1", "walk": "title", "project": project, "key": key, "hash": h,
                "commit": base, "clean": False, "code_tree": tree, "checkout": str(root),
                "trigger": trigger, "run": run, "result": result, "reason": "", "duration_s": 1,
                "evidence": "", "evidence_files": []}
    ledger.append("B", "app", [line(key_r, "regular", "fail"), line(key_c, "compact", "fail"),
                               line(key_c, "compact", "fail", trigger="base", tree=None),
                               line(key_r, "regular", "pass", tree="c" * 40),     # older tree
                               line(key_r, "regular", "pass", run="run_other")])  # other run
    facts = pipeline.facts(project_dir=None, project="B", repo="app", root=root, base=base,
                           code_tree=TREE, run_id="run_x", changed=["site/index.html"],
                           blocking=True)
    rows = {r["project"]: r for r in facts["rows"]}
    assert rows["regular"]["result"] == "fail" and not rows["regular"]["pre_existing"]
    assert rows["compact"]["pre_existing"] is True
    assert [r["project"] for r in pipeline.failures(facts)] == ["regular"]
    # a reached feature with no line at this tree is listed as missing, never failed
    none_here = pipeline.facts(project_dir=None, project="B", repo="app", root=root, base=base,
                               code_tree="d" * 40, run_id="run_x", changed=["site/index.html"],
                               blocking=True)
    assert none_here["rows"] == [] and none_here["missing"][0]["feature"] == "title-a1"
    assert pipeline.failures(none_here) == []


def test_next_names_command_only_with_a_map(tmp_path, app_data):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)
    bare = tmp_path / "bare"
    bare.mkdir()
    git(bare, "init", "-q")
    with_map = SimpleNamespace(run_id="run_x", _project_dir=lambda: None,
                               effective_repos=lambda: {"app": str(root), "lib": str(bare)})
    repos = RunEngine.feature_map_repos(with_map)
    assert repos == {"app": str(root)}
    out = {"why": "run the final verification"}
    next_step._note_feature_map(SimpleNamespace(run_id="run_x", feature_map_repos=lambda: repos), out)
    assert "scripts.feature_map replay --affected --run run_x" in out["why"]
    assert out["feature_map"][0]["cwd"] == str(root)
    # R9.1: a run whose repos have no feature folder gets nothing
    out = {"why": "run the final verification"}
    next_step._note_feature_map(SimpleNamespace(run_id="run_x", feature_map_repos=lambda: {}), out)
    assert out == {"why": "run the final verification"}
    none = SimpleNamespace(run_id="run_x", _project_dir=lambda: None,
                           effective_repos=lambda: {"lib": str(bare)})
    assert RunEngine.feature_map_repos(none) == {}


def test_report_and_view_feature_checks(tmp_path, app_data):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)
    base = git(root, "rev-parse", "HEAD")
    # the run changes an expectation of the walk and the knowledge of the feature
    spec = root / ".swarmai/features/specs/title.spec.ts"
    spec.write_text(spec.read_text().replace("toHaveText('Hello')", "toHaveText('Hi')"))
    fy = root / ".swarmai/features/features.yaml"
    fy.write_text(fy.read_text().replace("The title reads Hello", "The title reads Hi"))
    git(root, "add", "-A")
    tree = git(root, "write-tree")
    # the CLI copies evidence into the ledger's evidence folder; only that is copied on
    shot = ledger.evidence_dir("B", "app", "line-1") / "shot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG")
    stray = tmp_path / "secret.txt"
    stray.write_text("not evidence")
    from core.feature_map import files
    fmap = files.load_folder(root / ".swarmai/features")
    h = files.content_hash(fmap, fmap.features[0])
    ledger.append("B", "app", [{"feature": "title-a1", "walk": "title", "project": "regular",
                                "key": ledger.walk_key("title-a1", "title", "regular"), "hash": h,
                                "commit": base, "clean": False, "code_tree": tree,
                                "checkout": str(root), "trigger": "replay", "run": "run_x",
                                "result": "pass", "reason": "", "duration_s": 1, "evidence": "",
                                "evidence_files": [str(shot), str(stray)]}])
    facts = pipeline.facts(project_dir=None, project="B", repo="app", root=root, base=base,
                           code_tree=tree, run_id="run_x",
                           changed=[".swarmai/features/specs/title.spec.ts",
                                    ".swarmai/features/features.yaml"], blocking=True)
    changes = {(c["change"], c["line"]) for c in facts["changed_expectations"]}
    assert any(c == "-" and "toHaveText('Hello')" in line for c, line in changes)
    assert any(c == "+" and "toHaveText('Hi')" in line for c, line in changes)
    assert any(c == "+" and "The title reads Hi" in line for c, line in changes)
    # the evidence is copied under the run dir, so it outlives app-data pruning
    run_dir = tmp_path / "run"
    copied = RunEngine._copy_feature_evidence(
        SimpleNamespace(run_dir=run_dir, contract={"project": "B"}), "app", facts)
    assert len(copied["rows"][0]["evidence"]) == 1, "a file outside the ledger is never copied"
    path = Path(copied["rows"][0]["evidence"][0])
    assert path.is_file() and path.is_relative_to(run_dir)
    shot.unlink()
    assert path.is_file()
    lines = "\n".join(pipeline.report_lines([copied]))
    from core.run_protocol.audit import _text
    assert "## Feature checks" in lines and "✓ title-a1 · title [regular] pass" in lines
    assert "expected results changed by this run" in lines and _text(str(path), 400) in lines
    # the run view reads the record the engine writes, and drops paths outside the run dir
    from routers.pipelines import _feature_checks
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    rows = pipeline.evidence_rows(copied)
    rows.append({"feature": "x", "walk": "y", "project": "regular", "result": "fail",
                 "pre_existing": False, "files": ["/etc/passwd"]})
    (run_dir / "artifacts" / "feature-checks.json").write_text(json.dumps({"app": {
        "rows": rows, "changed_expectations": copied["changed_expectations"], "missing": [],
        "blocking": True, "error": ""}}))
    view = _feature_checks(run_dir)
    assert view[0]["repo"] == "app" and view[0]["rows"][0]["files"] == [str(path)]
    assert view[0]["rows"][1]["files"] == [], "a path outside the run dir is dropped"
    assert view[0]["changedExpectations"]


def _payload(**kw):
    return {"kind": "feature_checks", "payload": {"repo": "app", "code_tree": TREE,
                                                   "blocking": True, "n_fail": 0, "n_rows": 1,
                                                   "report_json": "{}", **kw}}


def test_fm1_refuses_unreplayed_and_undecided():
    # a reached feature not replayed on this tree: the agent's step was skipped
    ok, detail = checks._feature_check(_ctx([_payload(n_unreplayed=2)]))
    assert not ok and "2 reached feature(s) not replayed" in detail
    # facts not decided (the run made the folder invalid, or they could not be computed)
    ok, detail = checks._feature_check(_ctx([_payload(error_blocks=True, error="bad key x")]))
    assert not ok and "could not be decided" in detail and "bad key x" in detail
    # the switch lists both
    ok, detail = checks._feature_check(_ctx([_payload(n_unreplayed=1, error_blocks=True,
                                                      blocking=False)]))
    assert ok and "listed only" in detail
    # a record from before these fields: nothing new to refuse
    assert checks._feature_check(_ctx([_payload()]))[0]


def test_facts_folder_error_blocks_only_when_the_run_changed_it(tmp_path, app_data):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)
    base = git(root, "rev-parse", "HEAD")
    fy = root / ".swarmai/features/features.yaml"
    fy.write_text(fy.read_text() + "unknown_key: 1\n")
    common = dict(project_dir=None, project="B", repo="app", root=root, base=base,
                  code_tree=TREE, run_id="run_x", blocking=True)
    mine = pipeline.facts(changed=[".swarmai/features/features.yaml"], **common)
    assert mine["error"] and mine["error_blocks"] is True
    theirs = pipeline.facts(changed=["site/index.html"], **common)
    assert theirs["error"] and theirs["error_blocks"] is False, "invalid before the run: listed"


def test_unreplayed_leaves_out_quarantined():
    payload = {"missing": [{"feature": "a", "quarantined": False},
                           {"feature": "b", "quarantined": True}]}
    assert [m["feature"] for m in pipeline.unreplayed(payload)] == ["a"]


def test_brain_folder_changes_reach_and_are_listed(tmp_path, app_data):  # noqa: F811
    import os
    import time
    from core.feature_map import files
    root = make_repo(tmp_path / "r", specs={}, features="schema: 1\nfeatures: []\n")
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(root / ".swarmai/features")
    project_dir = tmp_path / "Projects" / "B"
    folder = files.brain_folder(project_dir, "app")
    (folder / "specs").mkdir(parents=True)
    (folder / "targets").mkdir()
    (folder / "targets" / "web.yaml").write_text("id: web\nkind: web\nroot: site/\n")
    (folder / "specs" / "title.spec.ts").write_text(SPEC)
    (folder / "features.yaml").write_text(FEATURES)
    old = time.time() - 3600
    for path in folder.rglob("*"):
        os.utime(path, (old, old))
    since = time.time() - 60
    (folder / "specs" / "title.spec.ts").write_text(SPEC.replace("Hello", "Hi"))   # edited in the run
    base = git(root, "rev-parse", "HEAD")
    assert pipeline.brain_folder_changes(folder, since) == ["specs/title.spec.ts"]
    facts = pipeline.facts(project_dir=project_dir, project="B", repo="app", root=root,
                           base=base, code_tree=TREE, run_id="run_x", changed=[],
                           blocking=True, since=since)
    assert facts["mode"] == "brain" and facts["reached"] == ["title-a1"]
    assert facts["changed_expectations"][0]["file"] == "specs/title.spec.ts"
    assert pipeline.unreplayed(facts), "an edited walk not replayed keeps TEST red"
    # no creation time known: nothing is invented
    assert pipeline.brain_folder_changes(folder, None) == []


def test_report_lines_escape_untrusted_words():
    evil = "[Approve](https://evil.example) ![](https://evil.example/px) <img src=x onerror=1>"
    facts = {"repo": "## app", "blocking": True, "error": "",
             "rows": [{"feature": "f-1", "walk": evil, "project": "regular", "result": "fail",
                       "reason": "", "error": "## Heading\n" + evil, "evidence": [evil]}],
             "missing": [{"feature": evil, "quarantined": False}], "unplaced": [evil],
             "changed_expectations": [{"file": evil, "change": "+", "line": f"expect(x).toBe('{evil}')"},
                                      {"file": "## Approved by security.yaml", "change": "~", "line": "x"}]}
    facts["rows"][0]["evidence"].append("## Heading.png")
    text = "\n".join(pipeline.report_lines([facts]))
    import re
    assert "<img" not in text
    assert not re.search(r"(?<!\\)\]\(", text), "no unescaped link"
    assert not re.search(r"(?<!\\)!\[", text), "no unescaped image"
    body = [line for line in text.splitlines() if line and line != "## Feature checks"]
    assert not any(line.lstrip().startswith("#") for line in body), "no heading it did not write"
    assert not any(re.match(r"^\s*[-*]\s+#", line) for line in body), "no heading in a list item"


def test_engine_records_uncomputable_facts_as_not_decided(tmp_path, app_data, monkeypatch):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)

    def boom(**_kw):
        raise OSError("disk gone")
    monkeypatch.setattr(pipeline, "facts", boom)
    events = []
    engine = SimpleNamespace(
        run_id="run_x", run_dir=tmp_path / "run", contract={"project": "B", "created_at": "x"},
        _project_dir=lambda: None, authority=SimpleNamespace(writer=lambda _w: "cli"),
        store=SimpleNamespace(append_new=lambda **kw: events.append(kw)))
    engine._copy_feature_evidence = lambda repo, facts: facts
    (tmp_path / "run" / "artifacts").mkdir(parents=True)
    RunEngine._record_feature_checks(engine, {"app": str(root)}, {"app": TREE},
                                     {"app": "b" * 40}, {"app": ["site/index.html"]})
    assert len(events) == 1 and events[0]["kind"] == "feature_checks"
    payload = events[0]["payload"]
    assert payload["error_blocks"] is True and "disk gone" in payload["error"]
    ok, _detail = checks._feature_check(SimpleNamespace(run={"code_trees": {"app": TREE}},
                                                        events=({"kind": "feature_checks",
                                                                 "payload": payload},)))
    assert not ok, "facts that could not be computed never pass in silence"


def test_engine_records_undecided_when_even_the_folder_is_unknown(tmp_path, app_data, monkeypatch):  # noqa: F811
    from core.feature_map import files
    calls = []

    def boom(*_a, **_kw):
        calls.append(1)
        raise OSError("bindings unreadable")
    monkeypatch.setattr(pipeline, "facts", boom)
    monkeypatch.setattr(files, "location", boom)
    events = []
    engine = SimpleNamespace(
        run_id="run_x", run_dir=tmp_path / "run", contract={"project": "B", "created_at": "x"},
        _project_dir=lambda: None, authority=SimpleNamespace(writer=lambda _w: "cli"),
        store=SimpleNamespace(append_new=lambda **kw: events.append(kw)))
    engine._copy_feature_evidence = lambda repo, facts: facts
    (tmp_path / "run" / "artifacts").mkdir(parents=True)
    RunEngine._record_feature_checks(engine, {"app": str(tmp_path)}, {"app": TREE},
                                     {"app": "b" * 40}, {"app": []})
    assert calls and events and events[0]["payload"]["error_blocks"] is True


def test_engine_reads_the_unbound_ledger_for_a_run_without_project(tmp_path, app_data, monkeypatch):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": SPEC}, features=FEATURES)
    asked = []

    def spy(**kw):
        asked.append(kw["project"])
        return None
    monkeypatch.setattr(pipeline, "facts", spy)
    engine = SimpleNamespace(run_id="run_x", run_dir=tmp_path / "run",
                             contract={"project": "", "created_at": "x"}, _project_dir=lambda: None)
    RunEngine._record_feature_checks(engine, {"app": str(root)}, {"app": TREE},
                                     {"app": "b" * 40}, {"app": []})
    assert asked == [ledger.UNBOUND], "the ledger the CLI writes for a run without a project"
