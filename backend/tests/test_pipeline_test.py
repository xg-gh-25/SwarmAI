"""Tests for ``scripts/pipeline_test.py`` — the single pipeline test entry point.

Methodology: each test builds a tiny throwaway git repo under ``tmp_path`` that
mirrors the real layout (``backend/core/mod.py``, ``backend/tests/test_*.py``,
``backend/pyproject.toml``) and points the runner at it through
SWARM_TEST_REPO_ROOT, SWARM_TEST_PYTHON (= ``sys.executable``), SWARM_TEST_CACHE_DIR
and SWARM_WORKSPACE (set with monkeypatch; conftest strips inherited SWARM_* vars).
The runner launches REAL nested pytest shards in that repo, so selection,
sharding, event/junit parsing, the deadline kill, the HEAD-baseline worktree,
probe convergence and mutation isolation run end to end. The tmp repos have no
``backend/.venv``, so the conftest venv guard never applies. Git commits in the
tmp repos pass ``-c core.hooksPath=/dev/null`` so machine-wide hooks don't run.

Invariants covered:
- selection: a changed module selects its direct importer and never an unrelated
  test; overflow past the cap lands in ``skipped_for_budget`` (never silent)
- cache: identical inputs return ``cached: true`` without launching anything
- deadline: a hung test is reported as ``timeout`` with its stack and the call returns
- B2 classification: new / flaky / pre-existing / regression via a reusable HEAD worktree
- G2 probe rules 1–3 (converge on 0 new; cached when nothing changed; exactly one extra
  round after a probe change, then refuse; a target change resets convergence)
- mutation: killed vs survived, main tree untouched, temp worktree removed
- evidence files match the C2 / C3 shapes; tdd > 5 files rejected; repeat warning after 8
- H1: a change two import hops away (and any changed file) invalidates the cache;
  verify selects transitive importers, ordered changed → recent failures → distance (L1)
- H2: a selected file / nodeid that collects zero tests exits 1 and is never cached
  (a module-level skip is not an error)
- H3: tests that never reached a call report get no HEAD baseline (unknown, exit 1);
  any timeout fails the exit code whatever its classification
- H4: an order-dependent regression (fails again when its whole file re-runs) is a
  regression, not flaky
- H5: ``..`` / outside paths are rejected; symlinks escaping the repo are not copied
- M1: SIGTERM aborts the child process group and removes the mutation worktree;
  worktrees are added without running repo hooks; deadline clamp + warning
- M2: evidence carries ``head_sha`` and ``tree_digest`` (current tree, also on a hit)
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pipeline_test as pt

PROJECT = "P1"
RUN_ID = "run_t1"

BASE_FILES = {
    ".gitignore": "__pycache__/\n",
    "backend/pyproject.toml": "[tool.pytest.ini_options]\n",
    "backend/core/__init__.py": "",
    "backend/tests/__init__.py": "",
    "backend/core/mod.py": "def add(a, b):\n    return a + b\n",
    "backend/tests/test_x.py": (
        "from core.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"),
    "backend/tests/test_unrelated.py": "def test_other():\n    assert True\n",
}


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, timeout=60, check=True)
    return p.stdout


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Factory: tmp git repo (BASE_FILES + extras committed) + runner env + run dir."""
    def make(extra: dict[str, str] | None = None, run_state: dict | None = None) -> Path:
        repo = tmp_path / "repo"
        _write(repo, {**BASE_FILES, **(extra or {})})
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        ws = tmp_path / "ws"
        run_dir = ws / "Projects" / PROJECT / ".artifacts" / "runs" / RUN_ID
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps(run_state or {}), encoding="utf-8")
        monkeypatch.setenv("SWARM_TEST_REPO_ROOT", str(repo))
        monkeypatch.setenv("SWARM_TEST_PYTHON", sys.executable)
        monkeypatch.setenv("SWARM_TEST_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
        return repo
    return make


def _run(capsys, *args: str) -> tuple[dict, int]:
    code = pt.main(["--project", PROJECT, *args])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1, lines  # exactly ONE JSON object on stdout
    assert len(lines[0]) <= pt.OUTPUT_BUDGET + 500
    return json.loads(lines[0]), code


def _evidence(tmp_path: Path, rel: str) -> dict:
    run_dir = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts" / "runs" / RUN_ID
    return json.loads((run_dir / "evidence" / rel).read_text(encoding="utf-8"))


def _assert_c2(ev: dict, mode: str) -> None:
    assert ev["schema"] == 1 and ev["mode"] == mode and ev["run_id"] == RUN_ID
    for k in ("started_at", "finished_at", "duration_s", "cached", "selected_files",
              "skipped_for_budget", "counts", "results", "failures", "layers", "tdd_repeats"):
        assert k in ev, k
    assert set(ev["counts"]) == {"passed", "failed", "errors", "timeouts", "skipped"}
    for r in ev["results"]:
        assert set(r) == {"nodeid", "outcome", "file"}
        assert r["outcome"] in ("passed", "failed", "error", "timeout", "skipped")
    for f in ev["failures"]:
        assert f["classification"] in ("regression", "pre-existing", "new", "flaky", "unknown")
        assert isinstance(f["summary"], str)
    assert set(ev["layers"]) == {"ac_driven", "dependency_scoped", "import_smoke"}
    assert set(ev["layers"]["ac_driven"]) == {"run", "pass", "fail"}
    assert set(ev["layers"]["dependency_scoped"]) == {"run", "tests", "pass"}
    assert set(ev["layers"]["import_smoke"]) == {"run", "modules", "pass"}
    assert ev["finished_at"].endswith("Z")


# ── selection ───────────────────────────────────────────────────────────────
def test_selection_picks_direct_importers_not_unrelated(rig, tmp_path):
    repo = rig({"backend/tests/test_y.py": (
        "import core.mod\n\n\ndef test_y():\n    assert core.mod.add(0, 0) == 0\n")})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    changed = pt.changed_files(repo, {})
    assert changed == ["backend/core/mod.py"]
    graph = pt.build_import_graph(repo, tmp_path / "cache")
    sel, skipped, mods = pt.select_backend(repo, changed, graph, 40)
    assert set(sel) == {"backend/tests/test_x.py", "backend/tests/test_y.py"}
    assert "backend/tests/test_unrelated.py" not in sel and skipped == [] and mods == ["core.mod"]
    # files_touched from run.json joins the change set (abs or repo-relative)
    assert "backend/tests/test_unrelated.py" in pt.changed_files(
        repo, {"files_touched": [str(repo / "backend/tests/test_unrelated.py")]})
    # cap: overflow is reported, never dropped
    sel1, skipped1, _ = pt.select_backend(repo, changed, graph, 1)
    assert len(sel1) == 1 and len(skipped1) == 1 and set(sel1 + skipped1) == set(sel)


def test_verify_runs_scope_reports_budget_overflow_and_writes_c2(rig, capsys, monkeypatch, tmp_path):
    repo = rig({"backend/tests/test_y.py": (
        "import core.mod\n\n\ndef test_y():\n    assert core.mod.add(0, 0) == 0\n")})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    monkeypatch.setattr(pt, "CAP_FILES", 1)
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--json")
    assert code == 0, out
    assert out["counts"]["passed"] == 1 and out["skipped_for_budget_total"] == 1
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    _assert_c2(ev, "verify")
    assert len(ev["selected_files"]) == 1 and len(ev["skipped_for_budget"]) == 1
    assert ev["layers"]["dependency_scoped"] == {"run": True, "tests": 1, "pass": 1}
    assert Path(out["evidence"]).is_file() and Path(out["evidence"]).name.startswith("verify-")


# ── tdd: cache, repeat counter, file limit ──────────────────────────────────
def test_tdd_cache_hit_skips_execution_and_warns_after_eight_runs(rig, capsys, monkeypatch, tmp_path):
    rig()
    args = ("--run-id", RUN_ID, "--mode", "tdd", "--files", "backend/tests/test_x.py")
    out, code = _run(capsys, *args)
    assert code == 0 and out["cached"] is False and out["counts"]["passed"] == 1

    def boom(*a, **k):
        raise AssertionError("cache hit must not launch any process")
    monkeypatch.setattr(pt, "_run_jobs", boom)
    for i in range(2, 10):
        out, code = _run(capsys, *args)
        assert code == 0 and out["cached"] is True and out["duration_s"] == 0
        assert out["counts"]["passed"] == 1
        warned = any("batch your changes" in w for w in out["warnings"])
        assert warned is (i > pt.TDD_REPEAT_WARN), (i, out["warnings"])
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    _assert_c2(ev, "tdd")
    assert ev["cached"] is True and ev["tdd_repeats"] == {"backend/tests/test_x.py": 9}


def test_tdd_rejects_more_than_five_files(rig, capsys):
    rig()
    out, code = _run(capsys, "--mode", "tdd", "--files", *["backend/tests/test_x.py"] * 6)
    assert code == 2 and "batch" in out["error"]


# ── deadline ────────────────────────────────────────────────────────────────
def test_deadline_kills_hung_test_and_reports_timeout_with_stack(rig, capsys, tmp_path):
    repo = rig()
    # untracked → classified "new" without re-runs, so the call ends at the deadline
    _write(repo, {"backend/tests/test_hang.py": (
        "import time\n\n\ndef test_hang():\n    time.sleep(60)\n")})
    t0 = time.monotonic()
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--deadline", "6",
                     "--files", "backend/tests/test_hang.py")
    assert time.monotonic() - t0 < 15
    assert code == 1 and out["counts"]["timeouts"] == 1, out
    f = out["failures"][0]
    assert f["nodeid"] == "backend/tests/test_hang.py::test_hang"
    assert f["classification"] == "new"
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    assert "test_hang" in ev["failures"][0]["summary"].split("stack:", 1)[1]  # the stack
    assert len(ev["failures"][0]["summary"].splitlines()) <= pt.STACK_LINES + 1
    assert ev["cached"] is False
    assert not (tmp_path / "cache" / "results").exists() or not any(
        json.loads(p.read_text()).get("counts", {}).get("timeouts")
        for p in (tmp_path / "cache" / "results").glob("*.json"))  # timeouts never cached


# ── B2 classification ───────────────────────────────────────────────────────
CLS_TEST = '''from pathlib import Path

from core.mod import add


def test_pre():
    assert False, "broken at HEAD too"


def test_reg():
    assert add(2, 2) == 4


def test_flaky():
    marker = Path(__file__).with_name(".flaky_marker")
    if not marker.exists():
        marker.write_text("1")
        assert False, "fails on the first run only"
'''
# At HEAD test_flaky passes outright, so flaky = "re-run passes AND passed at HEAD".
CLS_HEAD = CLS_TEST.split("def test_flaky():")[0] + "def test_flaky():\n    pass\n"


def test_classification_new_flaky_preexisting_regression(rig, capsys, tmp_path):
    repo = rig({"backend/tests/test_cls.py": CLS_HEAD})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a - b\n",
                  "backend/tests/test_cls.py": CLS_TEST + "\n\ndef test_new():\n    assert False\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    _assert_c2(ev, "verify")
    got = {f["nodeid"].split("::")[-1]: f["classification"] for f in ev["failures"]}
    assert got == {"test_pre": "pre-existing", "test_reg": "regression", "test_flaky": "flaky",
                   "test_new": "new", "test_add": "regression"}, got
    sha = _git(repo, "rev-parse", "HEAD").strip()
    cache = tmp_path / "cache"
    assert (cache / "test-baseline" / sha / "backend" / "core" / "mod.py").read_text() \
        == BASE_FILES["backend/core/mod.py"]  # HEAD content, not the working tree
    base = json.loads(pt._baseline_path(cache, sha).read_text())
    assert base["backend/tests/test_cls.py::test_pre"] == "failed"
    assert base["backend/tests/test_cls.py::test_reg"] == "passed"
    assert base["backend/tests/test_cls.py::test_flaky"] == "passed"


# ── G2 probe ────────────────────────────────────────────────────────────────
PROBE_SRC = '''import json
from pathlib import Path

print(json.dumps({"findings": json.loads(Path(__file__).with_name("findings.json").read_text())}))
'''


def test_probe_convergence_rules(rig, capsys, tmp_path):
    repo = rig()
    probe, findings = tmp_path / "probe.py", tmp_path / "findings.json"
    probe.write_text(PROBE_SRC)
    findings.write_text(json.dumps([{"id": "A", "cmd": "x"}]))
    args = ("--run-id", RUN_ID, "--mode", "probe", "--name", "fuzz",
            "--files", "backend/core/mod.py", "--", sys.executable, str(probe))

    out, code = _run(capsys, *args)  # round 1: A is new
    assert code == 1 and out["new_findings"] == 1 and out["converged"] is False
    out, code = _run(capsys, *args)  # rule 1: 0 new → converged
    assert code == 0 and out["new_findings"] == 0 and out["converged"] is True
    findings.write_text(json.dumps([{"id": "B"}]))  # would be new IF it ran
    out, code = _run(capsys, *args)  # rule 2: nothing changed → cached, not executed
    assert code == 0 and out["cached"] is True and out["new_findings"] == 0
    findings.write_text(json.dumps([{"id": "A"}]))
    probe.write_text(PROBE_SRC + "# extended alphabet\n")
    out, code = _run(capsys, *args)  # rule 3: probe changed → exactly one more round
    assert code == 0 and out["cached"] is False and out["converged"] is True
    probe.write_text(PROBE_SRC + "# extended again\n")
    out, code = _run(capsys, *args)  # ... and a further change is refused
    assert code == 2 and out.get("refused") is True and "refused" in out["error"]
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a + b + 0\n"})
    out, code = _run(capsys, *args)  # target change resets convergence → runs again
    assert out["cached"] is False and "refused" not in out and out["converged"] is True

    ev = _evidence(tmp_path, "probes/fuzz.json")
    assert ev["schema"] == 1 and ev["name"] == "fuzz" and isinstance(ev["converged"], bool)
    assert [f["id"] for f in ev["findings"]] == ["A"]
    assert len(ev["rounds"]) == 5  # the refused call records no round
    for r in ev["rounds"]:
        assert {"at", "target_digest", "probe_digest", "new_findings", "findings_total",
                "duration_s", "cached"} <= set(r)
    assert [r["cached"] for r in ev["rounds"]] == [False, False, True, False, False]


# ── G2 mutation ─────────────────────────────────────────────────────────────
def test_mutation_killed_vs_survived_and_main_tree_untouched(rig, capsys, tmp_path):
    repo = rig()
    # an uncommitted session change must be what gets mutated (copied into the worktree)
    current = "def add(a, b):\n    # session edit\n    return a + b\n"
    _write(repo, {"backend/core/mod.py": current})
    spec = tmp_path / "muts.json"
    spec.write_text(json.dumps({"tests": ["backend/tests/test_x.py"], "mutations": [
        {"id": "M1", "file": "backend/core/mod.py", "find": "a + b", "replace": "a - b"},
        {"id": "M2", "file": "backend/core/mod.py", "find": "# session edit", "replace": "# noop"},
        {"id": "M3", "file": "backend/core/mod.py", "find": "not there", "replace": "x"},
    ]}))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "mutation", "--mutations", str(spec))
    assert code == 1, out
    ev = _evidence(tmp_path, "mutations/latest.json")
    by_id = {m["id"]: m for m in ev["mutations"]}
    assert ev["schema"] == 1 and ev["all_killed"] is False
    assert by_id["M1"]["killed"] is True
    assert by_id["M1"]["red_tests"] == ["backend/tests/test_x.py::test_add"]
    assert by_id["M2"]["killed"] is False and by_id["M2"]["red_tests"] == []
    assert by_id["M3"]["killed"] is False and "exactly once" in by_id["M3"]["error"]
    for m in ev["mutations"]:
        assert {"id", "file", "killed", "red_tests"} <= set(m)
    assert ev["baseline"] == {"passed": 1, "failed": 0}
    assert (repo / "backend/core/mod.py").read_text() == current  # main tree untouched
    assert _git(repo, "worktree", "list").strip().count("\n") == 0  # temp worktree removed
    assert not any((tmp_path / "cache" / "mutation").iterdir())


# ── regression layers + frontend ────────────────────────────────────────────
FAKE_NPX = '''import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args[0] == "vitest":
    out = next(a.split("=", 1)[1] for a in args if a.startswith("--outputFile="))
    files = [a for a in args[2:] if not a.startswith("--")]
    Path(out).write_text(json.dumps({"testResults": [
        {"name": str(Path.cwd() / f), "assertionResults": [
            {"fullName": "works", "status": "passed", "failureMessages": []}]} for f in files]}))
sys.exit(0)
'''


def test_regression_fills_three_layers_and_frontend(rig, capsys, monkeypatch, tmp_path):
    state = {"stages": [{"stage": "build", "artifact_id": "art_b1"}]}
    repo = rig(run_state=state)
    arts = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts"
    (arts / "build.json").write_text(json.dumps({"ac_coverage": [
        {"ac": "AC1: adds", "impl": "core/mod.py::add", "test": "tests/test_x.py::test_add",
         "verified": True}]}))
    (arts / "manifest.json").write_text(json.dumps(
        {"artifacts": [{"id": "art_b1", "file": "build.json"}]}))
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a + b  # same\n",
                  "desktop/src/a.test.ts": "test('works', () => {})\n"})
    npx = tmp_path / "npx.py"
    npx.write_text(FAKE_NPX)
    monkeypatch.setenv("SWARM_TEST_NPX", str(npx))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert code == 0, out
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    _assert_c2(ev, "regression")
    assert ev["layers"]["ac_driven"] == {"run": True, "pass": 1, "fail": 0}
    assert ev["layers"]["dependency_scoped"] == {"run": True, "tests": 1, "pass": 1}
    assert ev["layers"]["import_smoke"] == {"run": True, "modules": 1, "pass": 1}
    assert ev["frontend"] == {"vitest": {"passed": 1, "failed": 0},
                              "tsc": {"ok": True, "errors_head": []}}
    assert "desktop/src/a.test.ts" in ev["selected_files"]
    # recheck after a fix: no previous failures, nothing changed since → empty, green
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 0 and out["counts"]["failed"] == 0 and out["selected"] == 0, out


# ── H1 / M2 / L1: transitive cache key, transitive selection, tree evidence ─
TRANS = {
    "backend/core/b.py": "def val():\n    return 1\n",
    "backend/core/a.py": "from core.b import val\n\n\ndef get():\n    return val()\n",
    "backend/tests/test_a.py": (
        "from core.a import get\n\n\ndef test_get():\n    assert get() == 1\n"),
    "backend/tests/test_bdirect.py": (
        "from core.b import val\n\n\ndef test_v():\n    assert val()\n"),
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_cache_key_follows_transitive_imports_and_evidence_records_tree(rig, capsys, tmp_path):
    repo = rig(TRANS)
    head = _git(repo, "rev-parse", "HEAD").strip()
    args = ("--run-id", RUN_ID, "--mode", "tdd", "--files", "backend/tests/test_a.py")
    out, code = _run(capsys, *args)
    assert code == 0 and out["cached"] is False, out
    out, code = _run(capsys, *args)
    assert code == 0 and out["cached"] is True
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    assert ev["head_sha"] == head
    assert ev["tree_digest"] == {"backend/tests/test_a.py": _sha256(TRANS["backend/tests/test_a.py"])}
    broken = "def val():\n    return 2\n"
    _write(repo, {"backend/core/b.py": broken})  # two import hops from the test
    out, code = _run(capsys, *args)
    assert out["cached"] is False and code == 1, out  # was a stale cached PASS
    assert out["failures"][0]["classification"] == "regression"
    fresh = _evidence(tmp_path, "tests/latest-tdd.json")
    assert fresh["tree_digest"]["backend/core/b.py"] == _sha256(broken)
    out, code = _run(capsys, *args)  # cache hit: digests are the current tree's
    hit = _evidence(tmp_path, "tests/latest-tdd.json")
    assert out["cached"] is True and code == 1
    assert hit["tree_digest"] == fresh["tree_digest"] and hit["head_sha"] == head
    _write(repo, {"README.md": "any changed file is a key input\n"})
    out, code = _run(capsys, *args)
    assert out["cached"] is False


def test_selection_reverse_closure_orders_changed_recent_then_distance(rig, tmp_path):
    repo = rig(TRANS)
    _write(repo, {"backend/core/b.py": "def val():\n    return 2\n"})
    cache = tmp_path / "cache"
    sgraph = pt.build_source_graph(repo, cache)
    graph = pt.build_import_graph(repo, cache, sgraph)
    changed = pt.changed_files(repo, {})
    assert changed == ["backend/core/b.py"]
    sel, skipped, _ = pt.select_backend(repo, changed, graph, 40, (), sgraph)
    # direct importer (distance 1) before the transitive one (core.a → core.b)
    assert sel == ["backend/tests/test_bdirect.py", "backend/tests/test_a.py"] and skipped == []
    assert pt.select_backend(repo, changed, graph, 40)[0] == ["backend/tests/test_bdirect.py"]
    # L1: recent failures come before importers, so the cap never cuts them first
    recent = ["backend/tests/test_unrelated.py"]
    sel, skipped, _ = pt.select_backend(repo, changed, graph, 1, recent, sgraph)
    assert sel == recent and skipped == ["backend/tests/test_bdirect.py", "backend/tests/test_a.py"]
    # relative imports resolve against the file's package
    assert "core.b" in pt._imports_of_source("from .b import val\n", "core")
    assert pt._imports_of_source("from .b import val\n") == []


def test_key_inputs_cover_conftest_chain_and_its_imports(rig, tmp_path):
    repo = rig({**TRANS, "backend/core/helper.py": "H = 1\n",
                "backend/tests/sub/__init__.py": "",
                "backend/tests/sub/conftest.py": "from core.helper import H  # noqa: F401\n",
                "backend/tests/sub/test_z.py": "def test_z():\n    assert True\n"})
    assert pt._conftests_for("backend/tests/sub/test_z.py") == [
        "backend/tests/sub/conftest.py", "backend/tests/conftest.py", "backend/conftest.py"]
    sgraph = pt.build_source_graph(repo, tmp_path / "cache")
    ctx = SimpleNamespace(repo=repo)
    inputs = pt._key_inputs(ctx, {"backend/tests/sub/test_z.py": None}, {}, [], sgraph)
    assert inputs["backend/core/helper.py"] == _sha256("H = 1\n")  # via the conftest
    assert inputs["backend/tests/sub/conftest.py"] != "missing"
    inputs = pt._key_inputs(ctx, {"backend/tests/test_a.py": None}, {}, [], sgraph)
    assert {"backend/core/a.py", "backend/core/b.py", "backend/core/__init__.py"} <= set(inputs)


# ── H2: zero collected is not green ─────────────────────────────────────────
def test_zero_collected_target_fails_and_is_never_cached(rig, capsys):
    rig({"backend/tests/test_empty.py": "X = 1\n",
         "backend/tests/test_modskip.py": (
             "import pytest\n\npytest.skip('other platform', allow_module_level=True)\n")})
    for files in (["backend/tests/test_empty.py"], ["backend/tests/test_x.py::test_typo"]):
        for _ in range(2):  # never served from the cache either
            out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files", *files)
            assert code == 1 and out["ok"] is False and out["cached"] is False, out
            assert out["runner_errors"] and out["counts"]["errors"] >= 1, out
            assert out["failures"][0]["classification"] == "unknown"
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd",
                     "--files", "backend/tests/test_modskip.py")
    assert code == 0 and out["counts"]["skipped"] == 1 and "runner_errors" not in out, out


# ── H3: no baseline for tests that never ran; timeouts always fail ──────────
SHARD_DIES = '''import os

from core.m import flag


def test_a_dies():
    os._exit(3)


def test_b():
    assert flag() == 1
'''


def test_not_run_tests_get_no_head_baseline_and_fail(rig, capsys, tmp_path):
    repo = rig({"backend/core/m.py": "def flag():\n    return 1\n",
                "backend/tests/test_die.py": SHARD_DIES})
    _write(repo, {"backend/core/m.py": "def flag():\n    return 2\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1 and out["ok"] is False, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    got = {f["nodeid"].split("::")[-1]: f["classification"] for f in ev["failures"]}
    assert got == {"test_a_dies": "unknown", "test_b": "unknown"}, got
    sha = _git(repo, "rev-parse", "HEAD").strip()
    bpath = pt._baseline_path(tmp_path / "cache", sha)
    base = json.loads(bpath.read_text()) if bpath.exists() else {}
    assert not any("test_die" in k for k in base), base  # never "failed at HEAD"
    assert pt._head_outcome("backend/t.py::t", [
        {"nodeid": "backend/t.py::t", "file": "backend/t.py", "outcome": "timeout",
         "notrun": True}]) is None
    assert pt._exit_code({"failures": [{"classification": "pre-existing", "outcome": "timeout"}]}) == 1
    assert pt._exit_code({"failures": [{"classification": "flaky", "outcome": "timeout"}]}) == 1
    assert pt._exit_code({"failures": [{"classification": "flaky", "outcome": "failed"}]}) == 0


# ── H4: order-dependent regression is not flaky ─────────────────────────────
ORDER_TEST = ("from core import s\n\n\ndef test_1():\n    assert s.setup() == 1\n\n\n"
              "def test_2():\n    assert s.STATE['n'] == 0\n")


def test_order_dependent_regression_is_not_flaky(rig, capsys, tmp_path):
    repo = rig({"backend/core/s.py": "STATE = {'n': 0}\n\n\ndef setup():\n    return 1\n",
                "backend/tests/test_s.py": ORDER_TEST})
    _write(repo, {"backend/core/s.py": (
        "STATE = {'n': 0}\n\n\ndef setup():\n    STATE['n'] += 1\n    return 1\n")})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1, out
    got = {f["nodeid"].split("::")[-1]: f["classification"] for f in out["failures"]}
    assert got == {"test_2": "regression"}, got


# ── H5: path traversal ──────────────────────────────────────────────────────
def test_traversal_and_escaping_paths_are_rejected(rig, capsys, tmp_path):
    repo = rig()
    for bad in ("backend/../../x", "../x", "backend/core/../../../etc/passwd", "/etc/passwd",
                str(tmp_path / "outside.py"), str(repo / "backend" / ".." / ".." / "x")):
        assert pt._norm_path(repo, bad) is None, bad
    assert pt._norm_path(repo, "./backend/core/mod.py") == "backend/core/mod.py"
    assert pt._norm_path(repo, str(repo / "backend/core/mod.py")) == "backend/core/mod.py"
    assert pt.changed_files(repo, {"files_touched": ["backend/../../x"]}) == []
    assert pt._contained(repo, "backend/core/mod.py") == (repo / "backend/core/mod.py").resolve()
    assert pt._contained(repo, "backend/../../x") is None
    victim = tmp_path / "victim.py"
    victim.write_text("SECRET = 1\n")
    (repo / "backend" / "evil.py").symlink_to(victim)  # untracked → in the change set
    assert pt._contained(repo, "backend/evil.py") is None
    spec = tmp_path / "muts.json"
    spec.write_text(json.dumps({"tests": ["backend/tests/test_x.py"], "mutations": [
        {"id": "M1", "file": "backend/../../victim.py", "find": "SECRET", "replace": "X"}]}))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "mutation", "--mutations", str(spec))
    assert code == 2 and "bad mutation entry" in out["error"], out
    spec.write_text(json.dumps({"tests": ["backend/tests/test_x.py"], "mutations": [
        {"id": "M1", "file": "backend/core/mod.py", "find": "a + b", "replace": "a - b"}]}))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "mutation", "--mutations", str(spec))
    assert code == 0 and out["all_killed"] is True, out
    assert any("outside the repo" in w for w in out["warnings"]), out["warnings"]
    assert victim.read_text() == "SECRET = 1\n"


# ── M1: signals, hooks, deadline clamp ──────────────────────────────────────
WAIT_TEST = ("import os\nimport time\nfrom pathlib import Path\n\n\ndef test_wait():\n"
             "    Path(os.environ['PT_PIDFILE']).write_text(str(os.getpid()))\n"
             "    time.sleep(60)\n")


def test_sigterm_aborts_children_and_removes_mutation_worktree(rig, tmp_path):
    repo = rig({"backend/tests/test_wait.py": WAIT_TEST})
    hooks, marker = tmp_path / "hooks", tmp_path / "hook-ran"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    (hooks / "post-checkout").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))  # tmp repo only
    spec = tmp_path / "muts.json"
    spec.write_text(json.dumps({"tests": ["backend/tests/test_wait.py"], "mutations": [
        {"id": "M1", "file": "backend/core/mod.py", "find": "a + b", "replace": "a - b"}]}))
    pidfile = tmp_path / "child.pid"
    env = dict(os.environ, PT_PIDFILE=str(pidfile))
    proc = subprocess.Popen(
        [sys.executable, str(Path(pt.__file__)), "--project", PROJECT, "--run-id", RUN_ID,
         "--mode", "mutation", "--mutations", str(spec)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        end = time.monotonic() + 30
        while not pidfile.exists() and time.monotonic() < end and proc.poll() is None:
            time.sleep(0.1)
        assert pidfile.exists(), proc.poll()
        assert _git(repo, "worktree", "list").strip().count("\n") == 1  # mutation wt exists
        proc.send_signal(signal.SIGTERM)
        stdout, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 2
    out = json.loads(stdout.strip().splitlines()[-1])
    assert "interrupted by SIGTERM" in out["error"], out
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)  # the hung test's process is gone
    assert _git(repo, "worktree", "list").strip().count("\n") == 0
    assert not any((tmp_path / "cache" / "mutation").iterdir())
    assert not marker.exists()  # worktree add ran with core.hooksPath=/dev/null


def test_deadline_default_fits_the_wrapper_and_large_values_warn():
    assert pt.DEFAULT_DEADLINE <= 100 < pt.WRAPPER_ALARM_S
    assert pt._clamp_deadline(pt.DEFAULT_DEADLINE) == (pt.DEFAULT_DEADLINE, [])
    d, warn = pt._clamp_deadline(300)
    assert d == 300 and "alarm" in warn[0] and "310" in warn[0]
    d, warn = pt._clamp_deadline(99999)
    assert d == pt.MAX_DEADLINE and len(warn) == 2
    assert pt._clamp_deadline("nan")[0] is None
