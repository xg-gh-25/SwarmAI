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
  verify selects direct importers (tests naming a changed symbol first), ordered
  changed → recent failures → importers (L1)
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
- per-file reuse: verify → regression on an unchanged tree re-runs no all-pass L2 file;
  an edit to ANY changed file (even one loaded only by computed name) re-runs it; only
  whole, all-pass, non-AC files with the same key are reused; overlays drop rechecked keys
- a failure that also fails at HEAD is pre-existing with no re-run; HEAD outcomes from
  another interpreter are discarded
- selection: a test naming a changed backend/scripts/*.py file (whole name) is selected
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

# These tests run the runner end to end: each spawns nested pytest processes (and for
# baseline / mutation, git worktrees). Measured 3-19 s each alone and over 30 s under a
# loaded regression, so the runner's 30 s per-test timeout killed their shard and took
# the rest of the file down with it. 120 s still catches a real hang.
pytestmark = pytest.mark.timeout(120)

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
        # Nested runners started by these tests use ONE worker: once this file is split
        # into concurrent chunks, each nested runner sizing itself to 4 workers
        # oversubscribed the machine (run_1f81be36: 350 s summed at load 21 vs 70 s).
        monkeypatch.setenv("SWARM_TEST_MAX_WORKERS", "1")
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
        if r["outcome"] == "passed":  # compact: nodeid + outcome (+ duration, + file if differs)
            assert {"nodeid", "outcome"} <= set(r) <= {"nodeid", "outcome", "duration", "file"}
        else:
            assert set(r) == {"nodeid", "outcome", "file"}
        assert r["outcome"] in ("passed", "failed", "error", "timeout", "skipped")
    for f in ev["failures"]:
        assert f["classification"] in ("regression", "pre-existing", "new", "flaky",
                                       "timeout", "unknown")
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
    # no file cap: the reached set is the scope (time grows, scope does not shrink)
    sel1, skipped1, _ = pt.select_backend(repo, changed, graph, 1)
    assert set(sel1) == set(sel) and skipped1 == []


def test_verify_runs_scope_reports_budget_overflow_and_writes_c2(rig, capsys, monkeypatch, tmp_path):
    repo = rig({"backend/tests/test_y.py": (
        "import core.mod\n\n\ndef test_y():\n    assert core.mod.add(0, 0) == 0\n")})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--json")
    assert code == 0, out
    assert out["counts"]["passed"] == 2 and out["skipped_for_budget_total"] == 0
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    _assert_c2(ev, "verify")
    assert len(ev["selected_files"]) == 2 and ev["skipped_for_budget"] == []
    assert ev["layers"]["dependency_scoped"] == {"run": True, "tests": 2, "pass": 2}
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
def test_global_deadline_timeout_is_its_own_unverified_class(rig, capsys, tmp_path):
    # A GLOBAL wall-clock deadline kill is NOT a code failure (its own "timeout" class,
    # never "new", so the run is not sent back to BUILD) but it is NOT verified either:
    # exit 1, never cached, hang stack kept, and the warning says re-run, don't edit code.
    # (As "new" it drove a build-fix loop whenever a heavy shard ran out of wall time.)
    repo = rig()
    _write(repo, {"backend/tests/test_hang.py": (
        "import time\n\n\ndef test_hang():\n    time.sleep(60)\n")})
    t0 = time.monotonic()
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--deadline", "6",
                     "--files", "backend/tests/test_hang.py")
    assert time.monotonic() - t0 < 15
    assert code == 1 and out["counts"]["timeouts"] == 1, out
    f = out["failures"][0]
    assert f["nodeid"] == "backend/tests/test_hang.py::test_hang"
    assert f["classification"] == "timeout"
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    assert "test_hang" in ev["failures"][0]["summary"].split("stack:", 1)[1]  # the stack
    assert len(ev["failures"][0]["summary"].splitlines()) <= pt.STACK_LINES + 1
    assert ev["cached"] is False
    assert any("global deadline" in w.lower() and "do not edit code" in w.lower()
               for w in ev.get("warnings") or [])  # routed to re-run, not to BUILD
    assert not (tmp_path / "cache" / "results").exists() or not any(
        json.loads(p.read_text()).get("counts", {}).get("timeouts")
        for p in (tmp_path / "cache" / "results").glob("*.json"))  # timeouts never cached


def test_per_test_hang_still_blocks(rig, capsys, tmp_path, monkeypatch):
    # A per-test hang (pytest-timeout kills it, NOT the global deadline) is a real defect
    # and must still block (exit 1) — it is NOT the non-blocking infra "timeout" class.
    monkeypatch.setattr(pt, "PER_TEST_TIMEOUT_S", 2)
    repo = rig()
    _write(repo, {"backend/tests/test_slow.py": (
        "import time\n\n\ndef test_slow():\n    time.sleep(20)\n")})
    t0 = time.monotonic()
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--deadline", "60",
                     "--files", "backend/tests/test_slow.py")
    assert time.monotonic() - t0 < 40
    assert code == 1 and out["counts"]["timeouts"] == 1, out
    f = out["failures"][0]
    assert f["nodeid"] == "backend/tests/test_slow.py::test_slow"
    assert f["classification"] != "timeout"  # a per-test hang never becomes infra-timeout


# ── circuit breaker ─────────────────────────────────────────────────────────
def _write_run_index(tmp_path, entries):
    d = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts" / "runs" / RUN_ID / "evidence" / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / "run_index.json").write_text(json.dumps(entries), encoding="utf-8")


def test_circuit_breaker_trips_on_stalled_identical_rounds(rig, capsys, tmp_path):
    # N consecutive verify rounds with the SAME blocking failure set = no progress -> abort.
    rig()
    sig = ["backend/tests/test_x.py::test_add"]
    _write_run_index(tmp_path, [{"kind": "run", "mode": "verify", "blocking_sig": sig}
                                for _ in range(pt.CIRCUIT_STALL_ROUNDS)])
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 2 and out["circuit_breaker"]["tripped"] is True, out
    assert "not making progress" in out["circuit_breaker"]["reason"]


def test_circuit_breaker_hard_ceiling_trips_even_without_a_stall(rig, capsys, tmp_path):
    # Distinct blocking sets each round (no stall) still hit the absolute ceiling.
    rig()
    _write_run_index(tmp_path, [{"kind": "run", "mode": "verify", "blocking_sig": [f"a::{i}"]}
                                for i in range(pt.CIRCUIT_MAX_ROUNDS)])
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 2 and out["circuit_breaker"]["tripped"] is True, out
    assert "hard limit" in out["circuit_breaker"]["reason"]


def test_circuit_breaker_allows_progress(rig, capsys, tmp_path):
    # Blocking set shrinks each round -> progress -> never trips (runs normally).
    rig()
    _write_run_index(tmp_path, [
        {"kind": "run", "mode": "verify", "blocking_sig": ["a::1", "a::2", "a::3"]},
        {"kind": "run", "mode": "verify", "blocking_sig": ["a::1", "a::2"]},
        {"kind": "run", "mode": "verify", "blocking_sig": ["a::1"]},
        {"kind": "run", "mode": "verify", "blocking_sig": []},
    ])
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert out.get("circuit_breaker") is None, out


def test_circuit_breaker_force_override(rig, capsys, tmp_path, monkeypatch):
    # Operator override bypasses the breaker even past the ceiling.
    rig()
    monkeypatch.setenv("SWARM_CIRCUIT_BREAKER_FORCE", "1")
    sig = ["backend/tests/test_x.py::test_add"]
    _write_run_index(tmp_path, [{"kind": "run", "mode": "verify", "blocking_sig": sig}
                                for _ in range(pt.CIRCUIT_MAX_ROUNDS + 2)])
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert out.get("circuit_breaker") is None, out


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


# ── L1 ac_driven: desktop vitest refs and probe refs ────────────────────────
FAKE_NPX_AC = """import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args[0] == "vitest":
    out = next(a.split("=", 1)[1] for a in args if a.startswith("--outputFile"))
    files = [a for a in args[2:] if not a.startswith("--")]
    flag = Path.cwd() / "src" / "flag.txt"
    broken = flag.is_file() and flag.read_text().strip() == "fail"
    flaky = os.environ.get("FAKE_FLAKY_COUNTER")  # fail on the first call only
    if flaky:
        n = int(Path(flaky).read_text()) if Path(flaky).is_file() else 0
        Path(flaky).write_text(str(n + 1))
        broken = broken or n == 0
    Path(out).write_text(json.dumps({"testResults": [
        {"name": str(Path.cwd() / f), "assertionResults": [
            {"fullName": "suite works",
             "status": "failed" if ("fails" in f or broken) else "passed",
             "failureMessages": []}]} for f in files]}))
sys.exit(0)
"""


def _ac_build(tmp_path: Path, tests: list[str]) -> dict:
    """Write a BUILD artifact whose ac_coverage[i].test is tests[i]; return run_state."""
    arts = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts"
    arts.mkdir(parents=True, exist_ok=True)
    (arts / "build.json").write_text(json.dumps({"ac_coverage": [
        {"ac": f"AC{i + 1}", "impl": "x", "test": t, "verified": True}
        for i, t in enumerate(tests)]}))
    (arts / "manifest.json").write_text(json.dumps(
        {"artifacts": [{"id": "art_b1", "file": "build.json"}]}))
    return {"stages": [{"stage": "build", "artifact_id": "art_b1"}]}


DESKTOP_FILES = {
    "desktop/src/pages/A.test.tsx": "test('works', () => {})\n",
    "desktop/src/B.fails.test.ts": "test('works', () => {})\n",
    "desktop/src/one/C.test.ts": "test('works', () => {})\n",
    "desktop/src/two/C.test.ts": "test('works', () => {})\n",
}


def test_regression_ac_frontend_refs(rig, capsys, monkeypatch, tmp_path):
    state = _ac_build(tmp_path, [
        "desktop/src/pages/A.test.tsx",                    # repo-relative -> pass
        "src/pages/A.test.tsx, A.test.tsx",                # desktop-relative + unique bare -> pass
        "B.fails.test.ts",                                 # a failing vitest file -> fail
        "C.test.ts",                                       # ambiguous bare name -> fail
        "desktop/src/pages/A.test.tsx::suite works",       # ::name rejected (file-level only)
        "tests/test_x.py::test_add",                       # backend ref, unchanged -> pass
    ])
    rig(DESKTOP_FILES, run_state=state)
    npx = tmp_path / "npx.py"
    npx.write_text(FAKE_NPX_AC)
    monkeypatch.setenv("SWARM_TEST_NPX", str(npx))
    out, _ = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"] == {"run": True, "pass": 3, "fail": 3}, ev["layers"]
    assert "desktop/src/pages/A.test.tsx" in ev["selected_files"]
    assert "desktop/src/B.fails.test.ts" in ev["selected_files"]
    # desktop refs never become pytest targets
    # (a compact pass omits ``file`` when it is the nodeid's own path)
    assert not any(r.get("file", r["nodeid"].split("::")[0]).startswith("desktop/")
                   and not r["nodeid"].startswith("desktop/") for r in ev["results"])
    warns = " ".join(out["warnings"])
    assert "ambiguous (2 files): C.test.ts" in warns
    assert "file-level (no ::name)" in warns


def test_regression_ac_probe_refs(rig, capsys, monkeypatch, tmp_path):
    state = _ac_build(tmp_path, ["probe:geo", "probe:../geo", "probe:absent"])
    repo = rig(run_state=state)
    probe, findings = tmp_path / "probe.py", tmp_path / "findings.json"
    probe.write_text(PROBE_SRC)
    findings.write_text("[]")
    pargs = ("--run-id", RUN_ID, "--mode", "probe", "--name", "geo", "--",
             sys.executable, str(probe))
    out, code = _run(capsys, *pargs)  # no --files: converges on the run's changed files
    assert code == 0 and out["converged"] is True
    assert _evidence(tmp_path, "probes/geo.json")["rounds"][-1]["current_findings"] == 0

    out, _ = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"] == {"run": True, "pass": 1, "fail": 2}
    assert not any("ref not found: probe:" in w for w in out["warnings"])
    assert any("probe:../geo" in w and "invalid probe name" in w for w in out["warnings"])
    assert any("probe:absent" in w and "--mode probe --name absent" in w for w in out["warnings"])
    by = {r["nodeid"]: r["outcome"] for r in ev["results"]}
    assert by["probe:geo"] == "passed" and by["probe:../geo"] == "failed"
    assert by["probe:absent"] == "failed"

    # the probe finds something after a cached-able regression -> the verdict is
    # re-evaluated, never served from the whole-result cache
    findings.write_text(json.dumps([{"id": "A"}]))
    probe.write_text(PROBE_SRC + "# widened\n")
    out, code = _run(capsys, *pargs)
    assert out["converged"] is False
    out, _ = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert out["cached"] is False
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"]["pass"] == 0

    # converged again, then the run's changed files move on -> stale, fails closed
    findings.write_text("[]")
    out, code = _run(capsys, *pargs)
    assert out["converged"] is True
    out, _ = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert _evidence(tmp_path, "tests/latest-regression.json")["layers"]["ac_driven"]["pass"] == 1
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a + b  # edited\n"})
    out, _ = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"]["pass"] == 0
    stale = next(r for r in ev["failures"] if r["nodeid"] == "probe:geo")
    assert "stale" in stale["summary"]


def test_cached_regression_is_reevaluated_when_the_probe_changes(rig, capsys, tmp_path):
    state = _ac_build(tmp_path, ["probe:geo"])
    rig(run_state=state)
    probe, findings = tmp_path / "probe.py", tmp_path / "findings.json"
    probe.write_text(PROBE_SRC)
    findings.write_text("[]")
    pargs = ("--run-id", RUN_ID, "--mode", "probe", "--name", "geo", "--",
             sys.executable, str(probe))
    assert _run(capsys, *pargs)[0]["converged"] is True
    reg = ("--run-id", RUN_ID, "--mode", "regression")
    _run(capsys, *reg)
    out, code = _run(capsys, *reg)
    assert code == 0 and out["cached"] is True  # an all-green probe verdict is cacheable
    findings.write_text(json.dumps([{"id": "A"}]))
    probe.write_text(PROBE_SRC + "# widened\n")
    assert _run(capsys, *pargs)[0]["converged"] is False
    out, code = _run(capsys, *reg)
    assert out["cached"] is False and code != 0
    assert _evidence(tmp_path, "tests/latest-regression.json")["layers"]["ac_driven"] == {
        "run": True, "pass": 0, "fail": 1}


def test_recheck_reevaluates_a_probe_that_went_stale(rig, capsys, tmp_path):
    state = _ac_build(tmp_path, ["probe:geo"])
    repo = rig(run_state=state)
    probe, findings = tmp_path / "probe.py", tmp_path / "findings.json"
    probe.write_text(PROBE_SRC)
    findings.write_text("[]")
    assert _run(capsys, "--run-id", RUN_ID, "--mode", "probe", "--name", "geo", "--",
                sys.executable, str(probe))[0]["converged"] is True
    _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert _evidence(tmp_path, "tests/latest-regression.json")["layers"]["ac_driven"]["pass"] == 1
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a + b  # edited\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"] == {"run": True, "pass": 0, "fail": 1}, ev["layers"]
    assert {r["nodeid"]: r["outcome"] for r in ev["results"]}["probe:geo"] == "failed"


def test_recheck_of_a_probe_only_failure_overlays_the_reconverged_probe(rig, capsys, tmp_path):
    state = _ac_build(tmp_path, ["probe:geo"])
    rig(run_state=state)
    probe, findings = tmp_path / "probe.py", tmp_path / "findings.json"
    probe.write_text(PROBE_SRC)
    findings.write_text(json.dumps([{"id": "A"}]))
    pargs = ("--run-id", RUN_ID, "--mode", "probe", "--name", "geo", "--",
             sys.executable, str(probe))
    assert _run(capsys, *pargs)[0]["converged"] is False
    _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert _evidence(tmp_path, "tests/latest-regression.json")["layers"]["ac_driven"]["pass"] == 0
    findings.write_text("[]")
    assert _run(capsys, *pargs)[0]["converged"] is True
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 0, out
    assert _evidence(tmp_path, "tests/latest-regression.json")["layers"]["ac_driven"] == {
        "run": True, "pass": 1, "fail": 0}


def test_a_crashed_probe_never_satisfies_an_ac(rig, capsys, tmp_path):
    state = _ac_build(tmp_path, ["probe:geo"])
    rig(run_state=state)
    probe, findings = tmp_path / "probe.py", tmp_path / "findings.json"
    probe.write_text(PROBE_SRC + "raise SystemExit(1)\n")
    findings.write_text("[]")
    _run(capsys, "--run-id", RUN_ID, "--mode", "probe", "--name", "geo", "--",
         sys.executable, str(probe))
    _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"] == {"run": True, "pass": 0, "fail": 1}
    assert "rc=1" in next(r for r in ev["failures"] if r["nodeid"] == "probe:geo")["summary"]


def test_desktop_ref_outside_src_and_glob_names_do_not_resolve(rig, capsys, monkeypatch, tmp_path):
    state = _ac_build(tmp_path, ["desktop/e2e/X.test.ts", "*.test.tsx"])
    rig({**DESKTOP_FILES, "desktop/e2e/X.test.ts": "test('e', () => {})\n"}, run_state=state)
    npx = tmp_path / "npx.py"
    npx.write_text(FAKE_NPX_AC)
    monkeypatch.setenv("SWARM_TEST_NPX", str(npx))
    out, _ = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ac_driven"]["pass"] == 0
    assert ev["layers"]["ac_driven"]["fail"] == 2


def test_mutation_spec_still_rejects_a_desktop_test(rig, capsys, tmp_path):
    rig(DESKTOP_FILES)
    spec = tmp_path / "muts.json"
    spec.write_text(json.dumps({"tests": ["desktop/src/pages/A.test.tsx"], "mutations": [
        {"id": "M1", "file": "backend/core/mod.py", "find": "a + b", "replace": "a - b"}]}))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "mutation", "--mutations", str(spec))
    assert code == 2 and "mutation test not found" in out["error"]


def test_frontend_cache_key_follows_the_source_under_test(rig, capsys, monkeypatch, tmp_path):
    repo = rig({"desktop/src/pages/A.test.tsx": "test('works', () => {})\n",
                "desktop/src/flag.txt": "pass\n"})
    npx = tmp_path / "npx.py"
    npx.write_text(FAKE_NPX_AC)
    monkeypatch.setenv("SWARM_TEST_NPX", str(npx))
    args = ("--run-id", RUN_ID, "--mode", "tdd", "--files", "desktop/src/pages/A.test.tsx")
    out, code = _run(capsys, *args)
    assert code == 0 and out["counts"]["passed"] == 1
    # the source the test runs against breaks; the test file itself is unchanged
    _write(repo, {"desktop/src/flag.txt": "fail\n"})
    out, code = _run(capsys, *args)
    assert out["counts"]["failed"] == 1, out  # a stale cached pass would report passed



def test_a_failing_frontend_result_is_not_served_from_cache(rig, capsys, monkeypatch, tmp_path):
    rig({"desktop/src/pages/A.test.tsx": "test('works', () => {})\n"})
    npx = tmp_path / "npx.py"
    npx.write_text(FAKE_NPX_AC)
    monkeypatch.setenv("SWARM_TEST_NPX", str(npx))
    monkeypatch.setenv("FAKE_FLAKY_COUNTER", str(tmp_path / "count"))
    args = ("--run-id", RUN_ID, "--mode", "tdd", "--files", "desktop/src/pages/A.test.tsx")
    out, _ = _run(capsys, *args)
    assert out["counts"]["failed"] == 1  # first vitest call fails
    out, _ = _run(capsys, *args)  # same inputs: must re-run, not replay the failure
    assert out["counts"]["passed"] == 1 and out["counts"]["failed"] == 0, out
    assert (tmp_path / "count").read_text() == "2"


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
    out = next(a.split("=", 1)[1] for a in args if a.startswith("--outputFile"))
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
    # default depth 1: direct importers only (depth 2 selected 103-463 files for 21)
    sel, skipped, _ = pt.select_backend(repo, changed, graph, 40, (), sgraph)
    assert sel == ["backend/tests/test_bdirect.py"] and skipped == []
    # depth 2: direct importer (distance 1) before the transitive one (core.a → core.b)
    sel, skipped, _ = pt.select_backend(repo, changed, graph, 40, (), sgraph, depth=2)
    assert sel == ["backend/tests/test_bdirect.py", "backend/tests/test_a.py"] and skipped == []
    assert pt.select_backend(repo, changed, graph, 40)[0] == ["backend/tests/test_bdirect.py"]
    # L1: recent failures come before importers, so the cap never cuts them first
    recent = ["backend/tests/test_unrelated.py"]
    sel, skipped, _ = pt.select_backend(repo, changed, graph, 1, recent, sgraph)
    assert sel == recent + ["backend/tests/test_bdirect.py"] and skipped == []
    # relative imports resolve against the file's package
    assert "core.b" in pt._imports_of_source("from .b import val\n", "core")
    assert pt._imports_of_source("from .b import val\n") == []


def test_recent_failures_ignore_deadline_timeouts_and_pre_existing(tmp_path):
    ev_root = tmp_path / "ev"
    (ev_root / "tests").mkdir(parents=True)
    (ev_root / "tests" / "latest-verify.json").write_text(json.dumps({"failures": [
        {"nodeid": "backend/tests/test_t.py::a", "outcome": "timeout", "classification": "timeout"},
        {"nodeid": "backend/tests/test_p.py::a", "outcome": "failed", "classification": "pre-existing"},
        {"nodeid": "backend/tests/test_r.py::a", "outcome": "failed", "classification": "regression"}]}))
    assert pt._recent_failure_files(ev_root) == ["backend/tests/test_r.py"]


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


def test_not_run_tests_get_no_head_baseline_and_fail(rig, capsys, tmp_path, monkeypatch):
    """When a dead shard's remaining tests CANNOT be requeued (no budget left), they stay
    not-run: no HEAD baseline, classification unknown, never green."""
    monkeypatch.setattr(pt, "REQUEUE_MIN_S", 10_000.0)  # no requeue possible
    repo = rig({"backend/core/m.py": "def flag():\n    return 1\n",
                "backend/tests/test_die.py": SHARD_DIES})
    _write(repo, {"backend/core/m.py": "def flag():\n    return 2\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1 and out["ok"] is False, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    got = {f["nodeid"].split("::")[-1]: f["classification"] for f in ev["failures"]}
    assert got == {"test_a_dies": "unknown", "test_b": "unknown"}, got
    # test_b never started: unfinished work for the next call, not a code failure
    assert out.get("continue") is True and out["unfinished"] == 1, out
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
    assert pt.DEFAULT_DEADLINE <= 100 < pt.WRAPPER_ALARM_S <= 110
    assert pt._clamp_deadline(pt.DEFAULT_DEADLINE) == (pt.DEFAULT_DEADLINE, [])
    d, warn = pt._clamp_deadline(300)
    assert d == 300 and "alarm" in warn[0] and "310" in warn[0]
    d, warn = pt._clamp_deadline("auto")  # retired: back to the default, warned
    assert d == pt.DEFAULT_DEADLINE and "retired" in warn[0]
    d, warn = pt._clamp_deadline(99999)
    assert d == pt.MAX_DEADLINE and len(warn) == 2
    assert pt._clamp_deadline("nan")[0] is None


def test_shards_keep_priority_order_serial_suites_do_not_jump_ahead():
    """_run_jobs starts shards in list order and the deadline drops the tail, so the
    run's own tests (inserted first by the planner) must lead. Alphabetical order
    with serial suites first once left them unstarted behind a 66 s serial suite."""
    targets = {  # planner order: changed tests, then importers by distance
        "backend/tests/test_stage_boundary_commands.py": None,
        "backend/tests/test_pipeline_run_profile.py": None,
        "backend/tests/test_alpha_importer.py": None,
        "backend/tests/test_context_health_hook.py": None,  # serial suite, far importer
        "backend/tests/test_beta_importer.py": {"backend/tests/test_beta_importer.py::t"},
    }
    shards = pt._shards(targets, workers=1)
    flat = [a for sh in shards for a in sh]
    assert flat[0] == "tests/test_stage_boundary_commands.py"
    assert flat.index("tests/test_pipeline_run_profile.py") < flat.index("tests/test_context_health_hook.py")
    assert ["tests/test_context_health_hook.py"] in shards  # still isolated
    assert shards[0] != ["tests/test_context_health_hook.py"]
    assert sorted(flat) == sorted(["tests/test_stage_boundary_commands.py", "tests/test_pipeline_run_profile.py",
                                   "tests/test_alpha_importer.py", "tests/test_context_health_hook.py",
                                   "tests/test_beta_importer.py::t"])


def test_changed_files_scopes_to_files_touched_and_warns(rig):
    """A run's change set is its files_touched — the set run-commit commits — not
    every uncommitted file in a tree shared with other sessions. The others are
    named in a warning with a command that runs as written."""
    repo = rig()
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n",
                  "backend/core/other_session.py": "x = 1\n"})
    warnings: list = []
    got = pt.changed_files(repo, {"files_touched": [str(repo / "backend/core/mod.py")]},
                           warnings, ("P1", "run_t1"))
    assert got == ["backend/core/mod.py"]
    assert len(warnings) == 1
    assert "backend/core/other_session.py" in warnings[0]
    assert "--project P1 --run-id run_t1 --files-touched" in warnings[0]
    assert warnings[0].startswith("1 changed file(s)")
    # nothing outside files_touched → no warning
    warnings.clear()
    pt.changed_files(repo, {"files_touched": ["backend/core/mod.py", "backend/core/other_session.py"]},
                     warnings, ("P1", "run_t1"))
    assert warnings == []
    # more than 8 → count is exact, list is capped with the remainder
    _write(repo, {f"backend/core/extra_{i}.py": "y = 1\n" for i in range(9)})
    warnings.clear()
    pt.changed_files(repo, {"files_touched": ["backend/core/mod.py"]}, warnings, ("P1", "run_t1"))
    assert warnings[0].startswith("10 changed file(s)") and "(+2 more)" in warnings[0]


def test_changed_files_falls_back_without_in_repo_files_touched(rig):
    """No files_touched, or entries that are not files of this repo (a workspace
    path like Projects/... normalizes like a repo path but does not exist here),
    keep the old union so the run is still tested."""
    repo = rig()
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    for state in ({}, {"files_touched": []},
                  {"files_touched": ["Projects/SwarmAI/2-understanding/TECH.md"]}):
        warnings: list = []
        got = pt.changed_files(repo, state, warnings, ("P1", "run_t1"))
        assert "backend/core/mod.py" in got, state
        assert warnings == [], state
    # a deleted tracked file in files_touched is still the run's own
    (repo / "backend/tests/test_unrelated.py").unlink()
    got = pt.changed_files(repo, {"files_touched": ["backend/tests/test_unrelated.py"]}, [], None)
    assert got == ["backend/tests/test_unrelated.py"]


def test_verify_without_files_touched_refuses_in_a_tree_shared_with_a_live_run(rig, tmp_path, capsys):
    """run_a2f2c171: no files_touched + a sibling run editing the same checkout → the
    fallback tested everyone's changes and 5 verify / recheck calls hit the deadline.
    The runner now asks for files_touched first; nothing runs, no evidence is written."""
    repo = rig()
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    runs = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts" / "runs"
    sib = runs / "run_sib1"
    sib.mkdir()
    (sib / "run.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code != 0 and "run_sib1" in out["error"] and "--files-touched" in out["error"], out
    assert f"--project {PROJECT} --run-id {RUN_ID}" in out["error"]
    assert not (runs / RUN_ID / "evidence" / "tests" / "latest-verify.json").exists()
    # a sibling that is not running, or whose run.json is stale, does not count
    (sib / "run.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    assert pt.shared_tree_scope_error(repo, {}, RUN_ID, PROJECT, runs) is None
    (sib / "run.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    old = time.time() - pt.SIBLING_ACTIVE_S - 60
    os.utime(sib / "run.json", (old, old))
    assert pt.shared_tree_scope_error(repo, {}, RUN_ID, PROJECT, runs) is None
    # with a live sibling, registering this run's own file lifts the refusal
    os.utime(sib / "run.json", None)
    assert pt.shared_tree_scope_error(repo, {}, RUN_ID, PROJECT, runs)
    assert pt.shared_tree_scope_error(
        repo, {"files_touched": ["backend/core/mod.py"]}, RUN_ID, PROJECT, runs) is None


def _seed_evidence(tmp_path: Path, mode: str, failures: list, finished_at: str) -> None:
    d = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts" / "runs" / RUN_ID / "evidence" / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"latest-{mode}.json").write_text(json.dumps(
        {"schema": 1, "mode": mode, "finished_at": finished_at, "failures": failures,
         "changed_files": {}}), encoding="utf-8")


def test_recheck_is_never_green_when_previous_failures_cannot_be_retargeted(rig, capsys, tmp_path):
    """A regression whose failures were only desktop:: / import:: used to recheck zero
    tests and exit 0. Now: nothing re-targetable while failures exist → an error."""
    rig()
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "desktop::vitest", "classification": "unknown", "outcome": "timeout"},
        {"nodeid": "import::core.gone", "classification": "unknown", "outcome": "timeout"},
    ], "2026-10-18T00:00:00")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code != 0, out
    assert "could not re-target" in out.get("error", ""), out


def test_recheck_reruns_import_smoke_failures(rig, capsys, tmp_path):
    rig()
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "import::core.mod", "classification": "unknown", "outcome": "timeout"},
    ], "2026-10-18T00:00:00")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 0, out
    ev = _evidence(tmp_path, "tests/latest-recheck.json")
    assert any(r["nodeid"] == "import::core.mod" and r["outcome"] == "passed" for r in ev["results"]), ev
    assert ev["rechecked_failures"] == 1


def test_recheck_builds_on_regression_not_a_later_tdd(rig, capsys, tmp_path):
    """A tdd run after the regression covers only its --files; building the recheck on
    it would drop the regression's failures."""
    rig()
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "backend/tests/test_x.py::test_add", "classification": "unknown", "outcome": "timeout"},
    ], "2026-10-18T00:00:00")
    _seed_evidence(tmp_path, "tdd", [], "2026-10-18T00:05:00")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 0, out
    ev = _evidence(tmp_path, "tests/latest-recheck.json")
    assert ev["rechecked_failures"] == 1
    assert any(r["nodeid"] == "backend/tests/test_x.py::test_add" and r["outcome"] == "passed"
               for r in ev["results"]), ev


def test_shards_put_ac_nodeids_first_and_run_them_once():
    """AC nodeids lead as their own shard; a whole-file target deselects them and a
    nodeid-set target drops them, so they are not run twice."""
    targets = {
        "backend/tests/test_slow.py": None,                       # whole file, holds an AC test
        "backend/tests/test_mixed.py": {"backend/tests/test_mixed.py::t1",
                                        "backend/tests/test_mixed.py::t_ac"},
        "backend/tests/test_context_health_hook.py": None,        # serial suite with an AC test
    }
    first = {"backend/tests/test_slow.py": {"backend/tests/test_slow.py::test_ac"},
             "backend/tests/test_mixed.py": {"backend/tests/test_mixed.py::t_ac"},
             "backend/tests/test_context_health_hook.py": {"backend/tests/test_context_health_hook.py::t_ac"}}
    shards = pt._shards(targets, workers=2, first=first)
    assert shards[0] == ["tests/test_context_health_hook.py::t_ac"]  # serial AC isolated, leads
    assert shards[1] == ["tests/test_slow.py::test_ac", "tests/test_mixed.py::t_ac"]  # priority order
    flat = [a for sh in shards[2:] for a in sh]
    assert "--deselect=tests/test_slow.py::test_ac" in flat
    assert "--deselect=tests/test_context_health_hook.py::t_ac" in flat
    assert "tests/test_mixed.py::t_ac" not in flat and "tests/test_mixed.py::t1" in flat


def test_per_test_timeout_requeues_the_rest_of_the_shard_once(rig, capsys, tmp_path, monkeypatch):
    """One test over the per-test timeout ends the shard; the tests after it used to be
    reported not-run. Now they get one more shard and real results."""
    monkeypatch.setattr(pt, "PER_TEST_TIMEOUT_S", 2)
    rig({"backend/tests/test_hangmid.py": (
        "import time\n\n\ndef test_a_hangs():\n    time.sleep(20)\n\n\n"
        "def test_b_after():\n    assert True\n\n\ndef test_c_after():\n    assert True\n")})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd",
                     "--files", "backend/tests/test_hangmid.py")
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    by = {r["nodeid"].split("::")[-1]: r for r in ev["results"]}
    assert by["test_a_hangs"]["outcome"] == "timeout"
    for t in ("test_b_after", "test_c_after"):
        assert by[t]["outcome"] == "passed" and by[t].get("requeued") is True, by[t]
    assert sum(1 for r in ev["results"] if r["nodeid"].endswith("test_b_after")) == 1
    assert code != 0  # the hang itself is never green


def test_overlay_evidence_replaces_rechecked_results_by_nodeid(tmp_path):
    base = {"mode": "regression", "finished_at": "t0",
            "results": [{"nodeid": "backend/tests/test_x.py::a", "outcome": "timeout", "file": "backend/tests/test_x.py"},
                        {"nodeid": "backend/tests/test_x.py::b", "outcome": "passed", "file": "backend/tests/test_x.py"},
                        {"nodeid": "import::core.mod", "outcome": "timeout", "file": "backend/core/mod.py"}],
            "failures": [{"nodeid": "backend/tests/test_x.py::a", "classification": "unknown", "outcome": "timeout"},
                         {"nodeid": "import::core.mod", "classification": "unknown", "outcome": "timeout"}],
            "runner_errors": [], "layers": {"dependency_scoped": {"run": True, "tests": 2, "pass": 1},
                                             "import_smoke": {"run": True, "modules": 1, "pass": 0}}}
    re_ev = {"results": [{"nodeid": "backend/tests/test_x.py::a", "outcome": "passed", "file": "backend/tests/test_x.py"},
                         {"nodeid": "import::core.mod", "outcome": "passed", "file": "backend/core/mod.py"}],
             "failures": [], "runner_errors": [], "changed_files": {"backend/core/mod.py": "d2"},
             "head_sha": "h2", "tree_digest": "t2"}
    base["changed_files"], base["head_sha"], base["tree_digest"] = {"backend/core/mod.py": "d1"}, "h1", "t1"
    ev = pt._overlay_evidence(tmp_path, base, re_ev, [])
    assert ev["counts"]["passed"] == 3 and ev["counts"]["timeouts"] == 0
    assert ev["failures"] == [] and ev["mode"] == "regression"
    assert ev["layers"]["dependency_scoped"]["pass"] == 2 and ev["layers"]["import_smoke"]["pass"] == 1
    assert ev["rechecked_from"] == {"finished_at": "t0", "rechecked": 2, "auto": False}
    # staleness checks compare against the tree the recheck saw, not the base's
    assert (ev["changed_files"], ev["head_sha"], ev["tree_digest"]) == ({"backend/core/mod.py": "d2"}, "h2", "t2")


def test_identical_verify_after_a_timeout_runs_a_recheck_and_overlays(rig, capsys, tmp_path, monkeypatch):
    """The second verify on unchanged inputs re-runs only what failed and writes the
    result back into latest-verify.json, instead of re-running everything."""
    monkeypatch.setattr(pt, "PER_TEST_TIMEOUT_S", 2)
    repo = rig()
    marker = tmp_path / "hung_once"
    _write(repo, {"backend/tests/test_once.py": (
        "import pathlib, time\n\n\ndef test_hangs_first_time():\n"
        f"    m = pathlib.Path({str(marker)!r})\n"
        "    if not m.exists():\n        m.write_text('x')\n        time.sleep(20)\n\n\n"
        "def test_fine():\n    assert True\n")})
    _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    first = _evidence(tmp_path, "tests/latest-verify.json")
    assert first["counts"]["timeouts"] >= 1
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    assert "rechecked_from" in ev, ev.keys()
    assert any("ran a recheck" in w for w in ev["warnings"])
    outcome = {r["nodeid"].split("::")[-1]: r["outcome"] for r in ev["results"]}
    assert outcome["test_hangs_first_time"] == "passed" and outcome["test_fine"] == "passed"
    assert code == 0, out


def test_dead_shard_rest_is_requeued_and_gets_a_real_verdict(rig, capsys, tmp_path):
    """With budget left, the test after the one that killed its shard runs in a requeue
    and gets a real result: here it fails on the change. (Whether it is then classified
    "regression" depends on the HEAD baseline finishing in time, which load can prevent,
    so the assertion is on the result, not the classification.)"""
    repo = rig({"backend/core/m.py": "def flag():\n    return 1\n",
                "backend/tests/test_die.py": SHARD_DIES})
    _write(repo, {"backend/core/m.py": "def flag():\n    return 2\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    res = {r["nodeid"].split("::")[-1]: r for r in ev["results"]}
    assert res["test_b"]["outcome"] == "failed" and res["test_b"].get("requeued") is True, res


def test_manual_recheck_overlays_onto_the_regression_evidence(rig, capsys, tmp_path):
    """The TEST stage reads latest-regression.json, so a recheck of a regression must
    update that file, not only latest-recheck.json."""
    rig()
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "backend/tests/test_x.py::test_add", "classification": "unknown", "outcome": "timeout"},
    ], "2026-10-18T00:00:00")
    d = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts" / "runs" / RUN_ID / "evidence" / "tests"
    reg = json.loads((d / "latest-regression.json").read_text())
    reg.update(results=[{"nodeid": "backend/tests/test_x.py::test_add", "outcome": "timeout",
                         "file": "backend/tests/test_x.py"}],
               counts={"passed": 0, "failed": 0, "errors": 0, "timeouts": 1, "skipped": 0},
               layers={"dependency_scoped": {"run": True, "tests": 1, "pass": 0}})
    (d / "latest-regression.json").write_text(json.dumps(reg))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 0 and out.get("overlaid_onto") == "regression", out
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["counts"]["timeouts"] == 0 and ev["counts"]["passed"] == 1
    assert ev["failures"] == [] and ev["rechecked_from"]["rechecked"] == 1


# ── Gate-2 fixes ─────────────────────────────────────────────────────────────

def test_plugin_deselects_exact_nodeids_not_prefixes(tmp_path):
    """pytest's own --deselect matches by PREFIX: deselecting test_parse also dropped
    test_parse_empty with no trace. The runner's plugin matches exactly."""
    import subprocess
    (tmp_path / "test_t.py").write_text(
        "import pytest\n\n\ndef test_parse():\n    pass\n\n\ndef test_parse_empty():\n    pass\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_p(n):\n    pass\n\n\n"
        "class TestK:\n    def test_m(self):\n        pass\n")
    plug = pt._plugin_dir(tmp_path / "cache")
    env = {**os.environ, "PYTHONPATH": str(plug), "PIPELINE_TEST_DESELECT": json.dumps(["test_t.py::test_parse", "test_t.py::test_p",
                                                                    "test_t.py::TestK"]),
           "PIPELINE_TEST_EVENTS": str(tmp_path / "ev.jsonl")}
    env.pop("PYTEST_ADDOPTS", None)
    r = subprocess.run([sys.executable, "-m", "pytest", "test_t.py", "-q", "-p", pt._PLUGIN_NAME,
                        "-p", "no:cacheprovider", "-o", "addopts="],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert "1 passed, 4 deselected" in r.stdout, r.stdout + r.stderr  # parametrized + class too
    collected = [json.loads(ln) for ln in (tmp_path / "ev.jsonl").read_text().splitlines()]
    ids = next(e["nodeids"] for e in collected if e["ev"] == "collected")
    assert ids == ["test_t.py::test_parse_empty"]


def test_pytest_job_routes_deselect_tokens_to_the_plugin_not_argv(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def t_ac():\n    pass\n")  # targets must exist
    ctx = SimpleNamespace(workdir=tmp_path, python=sys.executable, plugin_dir=tmp_path)
    job = pt._pytest_job(ctx, "j1", ["tests/test_a.py", "--deselect=tests/test_a.py::t_ac"],
                         tmp_path, False)
    assert not any(a.startswith("--deselect") for a in job.argv)
    assert json.loads(job.env["PIPELINE_TEST_DESELECT"]) == ["tests/test_a.py::t_ac"]
    assert job.meta["args"] == ["tests/test_a.py"]


def test_auto_recheck_falls_back_to_a_full_run_when_it_cannot_overlay(monkeypatch, tmp_path):
    """A recheck that errors (e.g. a shard:: failure it cannot re-target) or lands on
    another mode's evidence must not be returned as this mode's result."""
    ctx = SimpleNamespace(warnings=[])
    ns = SimpleNamespace(mode="regression", project="P1", run_id="r", auto_recheck_of=None)
    monkeypatch.setattr(pt, "_run_test_mode", lambda *a, **k: ({"error": "auto-recheck cannot re-target: shard::x"}, 2))
    assert pt._auto_recheck(ctx, ns, tmp_path, {}, 95.0, {}) is None
    monkeypatch.setattr(pt, "_run_test_mode", lambda *a, **k: ({"overlaid_onto": "verify"}, 0))
    assert pt._auto_recheck(ctx, ns, tmp_path, {}, 95.0, {}) is None
    assert any("running the full regression" in w for w in ctx.warnings)


def test_recheck_retargets_a_failed_vitest_test(rig, capsys, tmp_path, monkeypatch):
    """A failed vitest test is reported as desktop/src/<file>::<name>; recheck reruns the
    frontend job for it instead of dropping it."""
    repo = rig()
    _write(repo, {"desktop/src/a.test.ts": "test('works', () => {})\n"})
    npx = tmp_path / "npx.py"
    npx.write_text(FAKE_NPX)
    monkeypatch.setenv("SWARM_TEST_NPX", str(npx))
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "desktop/src/a.test.ts::works", "classification": "unknown", "outcome": "failed"},
    ], "2026-10-18T00:00:00")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 0, out
    assert out["rechecked_failures"] == 1 and "frontend" in out, out


def test_recheck_that_skipped_tests_for_budget_does_not_overlay(rig, capsys, tmp_path, monkeypatch):
    """Overlaying a recheck that could not run every selected test would keep stale
    passes from the old tree; it must leave the regression evidence alone."""
    real = pt.select_backend
    monkeypatch.setattr(pt, "select_backend",  # every selected file overflows
                        lambda *a, **k: (lambda r: ([], r[0] + r[1], r[2]))(real(*a, **k)))
    repo = rig()
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "backend/tests/test_x.py::test_add", "classification": "unknown", "outcome": "timeout"},
    ], "2026-10-18T00:00:00")
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert not out.get("overlaid_onto"), out
    assert any("NOT laid" in w for w in out["warnings"]), out["warnings"]
    assert "rechecked_from" not in _evidence(tmp_path, "tests/latest-regression.json")


def test_auto_recheck_builds_on_the_mode_being_rerun(rig, capsys, tmp_path, monkeypatch):
    """regression times out → a newer green verify → regression again on the same tree:
    the automatic recheck must re-run the REGRESSION's failures and update its evidence,
    not inherit the verify's green."""
    monkeypatch.setattr(pt, "PER_TEST_TIMEOUT_S", 2)
    repo = rig()
    marker = tmp_path / "hung_once"
    _write(repo, {"backend/tests/test_once.py": (
        "import pathlib, time\n\n\ndef test_hangs_first_time():\n"
        f"    m = pathlib.Path({str(marker)!r})\n"
        "    if not m.exists():\n        m.write_text('x')\n        time.sleep(20)\n")})
    _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert _evidence(tmp_path, "tests/latest-regression.json")["counts"]["timeouts"] >= 1
    _run(capsys, "--run-id", RUN_ID, "--mode", "verify")             # newer, green
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev.get("rechecked_from", {}).get("auto") is True, ev.keys()
    assert {r["nodeid"].split("::")[-1]: r["outcome"] for r in ev["results"]}["test_hangs_first_time"] == "passed"
    assert code == 0, out


def test_recheck_after_a_recheck_still_overlays_onto_the_regression(rig, capsys, tmp_path):
    """A second recheck must build on the (overlaid) regression, not on the first
    recheck — otherwise its results never reach latest-regression.json."""
    rig()
    _seed_evidence(tmp_path, "regression", [
        {"nodeid": "backend/tests/test_x.py::test_add", "classification": "unknown", "outcome": "timeout"},
    ], "2026-10-18T00:00:00")
    _seed_evidence(tmp_path, "recheck", [], "2026-10-18T00:00:00")   # same second, newer file
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert out.get("overlaid_onto") == "regression", out
    assert _evidence(tmp_path, "tests/latest-regression.json")["failures"] == []


def test_heavy_file_with_history_splits_and_runs_each_test_once(rig, capsys, tmp_path, monkeypatch):
    """run_1f81be36 AC3/AC5 end to end: recorded history makes one file the long pole,
    so it is split across shards; every test (including one missing from history)
    runs exactly once and the main run records the new durations."""
    body = "".join(f"def test_t{i}():\n    assert True\n\n\n" for i in range(5)) + \
        "def test_new():\n    assert True\n"
    rig({"backend/tests/test_big.py": body})
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    now = time.time()
    (cache / "durations.json").write_text(json.dumps(
        {f"backend/tests/test_big.py::test_t{i}": [100.0, now] for i in range(5)}))
    seen: list[list[str]] = []
    real = pt._pytest_job
    monkeypatch.setattr(pt, "_pytest_job", lambda ctx, name, args, *a: seen.append(args) or real(ctx, name, args, *a))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files", "backend/tests/test_big.py")
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    ids = [r["nodeid"] for r in ev["results"]]
    want = [f"backend/tests/test_big.py::test_t{i}" for i in range(5)] + ["backend/tests/test_big.py::test_new"]
    assert sorted(ids) == sorted(want), ev["results"]
    assert all(r["outcome"] == "passed" for r in ev["results"]) and code == 0, out
    assert sum(1 for a in seen if "tests/test_big.py" in a) >= 2, seen  # split across shards
    hist = json.loads((cache / "durations.json").read_text())
    assert "backend/tests/test_big.py::test_new" in hist
    assert hist["backend/tests/test_big.py::test_t0"][0] < 100.0  # newest measurement wins


def test_rig_caps_nested_runner_workers(rig):
    rig()
    assert pt._worker_count() == 1


# ── per-file reuse between verify / regression calls of one run ─────────────
def _logged_test(log: Path, name: str, imports: str) -> str:
    return (f"from pathlib import Path\n{imports}\n\n\n"
            f"def test_{name}():\n"
            f"    with Path({str(log)!r}).open('a') as fh:\n"
            f"        fh.write({name!r} + '\\n')\n")


def test_regression_reuses_passing_files_until_a_changed_file_is_edited(rig, capsys, tmp_path):
    """verify → regression on an unchanged tree re-runs no L2 file (the whole-run cache
    misses: another mode, other targets); its evidence still counts them. An edit to
    ANY changed file since — even one no test imports — re-runs every file."""
    log = tmp_path / "ran.log"
    repo = rig({
        "backend/core/alpha_mod.py": "def one():\n    return 1\n",
        "backend/tests/test_alpha.py": _logged_test(log, "alpha", "from core.alpha_mod import one"),
        "backend/tests/test_beta.py": _logged_test(log, "beta", "import core.alpha_mod"),
    })
    _write(repo, {"backend/core/alpha_mod.py": "def one():\n    return 1  # v2\n",
                  "docs/notes.md": "v1\n"})

    def ran() -> list[str]:
        return sorted(log.read_text().split()) if log.exists() else []

    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 0 and ran() == ["alpha", "beta"], out
    v = _evidence(tmp_path, "tests/latest-verify.json")
    assert sorted(v["l2_file_keys"]) == ["backend/tests/test_alpha.py", "backend/tests/test_beta.py"]
    assert "reused_from" not in v

    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert code == 0 and out["cached"] is False, out
    assert ran() == ["alpha", "beta"]  # neither re-ran
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    _assert_c2(ev, "regression")
    assert ev["reused_from"]["files"] == ["backend/tests/test_alpha.py", "backend/tests/test_beta.py"]
    assert ev["reused_from"]["tests"] == 2 and ev["reused_from"]["sources"][0]["mode"] == "verify"
    assert {"backend/tests/test_alpha.py::test_alpha", "backend/tests/test_beta.py::test_beta"} \
        <= {r["nodeid"] for r in ev["results"] if r["outcome"] == "passed"}
    assert ev["layers"]["dependency_scoped"] == {"run": True, "tests": 2, "pass": 2}
    assert ev["layers"]["import_smoke"]["run"] is True  # L3 always runs
    assert {"backend/tests/test_alpha.py", "backend/tests/test_beta.py"} <= set(ev["selected_files"])
    assert any(w.startswith("reused 2 test file") for w in ev["warnings"]), ev["warnings"]
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    hit = _evidence(tmp_path, "tests/latest-regression.json")
    assert out["cached"] is True and "reused_from" not in hit, out  # names the writer's sources
    assert hit["l2_file_keys"] == ev["l2_file_keys"]

    _write(repo, {"docs/notes.md": "v2: no test imports it, a module may still read it\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert code == 0 and "reused_from" not in _evidence(tmp_path, "tests/latest-regression.json"), out
    assert ran() == ["alpha", "alpha", "beta", "beta"]


def test_file_key_covers_imports_conftests_every_changed_file_and_head(rig, tmp_path):
    repo = rig({"backend/tests/test_y.py": "def test_y():\n    assert True\n"})
    _write(repo, {"docs/a.md": "doc\n"})
    ctx = SimpleNamespace(repo=repo, python=sys.executable)
    sgraph = pt.build_source_graph(repo, tmp_path / "cache")
    files = ["backend/tests/test_x.py", "backend/tests/test_y.py"]

    def keys(changed=("docs/a.md",), head="h1"):
        return pt._l2_file_keys(ctx, files, list(changed), sgraph, head)

    k0 = keys()
    assert keys() == k0 and k0["backend/tests/test_x.py"] != k0["backend/tests/test_y.py"]
    _write(repo, {"docs/a.md": "doc v2\n"})  # imported by nobody: still an input of all
    k1 = keys()
    assert all(k1[f] != k0[f] for f in files)
    assert keys(head="h2") != k1
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a + b + 0\n"})
    k2 = keys()  # an import of test_x only (not in the changed list passed here)
    assert k2["backend/tests/test_x.py"] != k1["backend/tests/test_x.py"]
    assert k2["backend/tests/test_y.py"] == k1["backend/tests/test_y.py"]
    _write(repo, {"backend/tests/conftest.py": "\n"})
    assert all(keys()[f] != k2[f] for f in files)


def test_reusable_takes_only_whole_all_pass_l2_files_with_the_same_key(tmp_path):
    t = "backend/tests/test_{}.py".format
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "latest-verify.json").write_text(json.dumps({
        "schema": 1, "mode": "verify", "run_id": RUN_ID, "finished_at": "t1",
        "l2_file_keys": {t(n): ("old" if n == "d" else "k") for n in "abcdefg"},
        "runner_errors": [t("c") + "::x"],
        "results": [{"nodeid": t(n) + "::x", "outcome": "passed"} for n in "acdef"]
        + [{"nodeid": t("b") + "::x", "outcome": "passed"},
           {"nodeid": t("b") + "::y", "outcome": "failed", "file": t("b")},
           {"nodeid": t("a") + "::s", "outcome": "skipped", "file": t("a")}]}))
    (tdir / "latest-regression.json").write_text(json.dumps({  # another run's evidence
        "schema": 1, "mode": "regression", "run_id": "run_other", "finished_at": "t9",
        "l2_file_keys": {t("g"): "k"}, "results": [{"nodeid": t("g") + "::x", "outcome": "passed"}]}))
    plan = {"targets": {**{t(n): None for n in "abcdeg"}, t("f"): {t("f") + "::x"}},
            "ac_files": [t("e")]}
    reused, sources = pt._reusable(tmp_path, RUN_ID, plan, {t(n): "k" for n in "abcdefg"})
    # b failed, c runner error, d key changed, e AC (L1), f nodeid-scoped, g no own-run result
    assert sorted(reused) == [t("a")] and len(reused[t("a")]) == 2
    assert sources == [{"mode": "verify", "finished_at": "t1", "files": 1}]


def test_overlay_drops_the_file_keys_of_rechecked_files(tmp_path):
    x, y = "backend/tests/test_x.py", "backend/tests/test_y.py"
    base = {"mode": "verify", "finished_at": "t0", "l2_file_keys": {x: "k1", y: "k2"},
            "results": [{"nodeid": x + "::a", "outcome": "failed", "file": x},
                        {"nodeid": y + "::b", "outcome": "passed"}],
            "failures": [{"nodeid": x + "::a", "classification": "new", "outcome": "failed"}],
            "runner_errors": [], "layers": {}}
    re_ev = {"results": [{"nodeid": x + "::a", "outcome": "passed", "file": x}],
             "failures": [], "runner_errors": []}
    ev = pt._overlay_evidence(tmp_path, base, re_ev, [])
    assert ev["l2_file_keys"] == {y: "k2"}  # x now passes under a later tree: never reused
    # a rechecked file's fresh results are no longer reported as reused
    base["reused_from"] = {"files": [x, y], "tests": 2, "sources": []}
    ev = pt._overlay_evidence(tmp_path, base, re_ev, [])
    assert ev["reused_from"]["files"] == [y] and ev["reused_from"]["tests"] == 1
    base["reused_from"] = {"files": [x], "tests": 1, "sources": []}
    assert "reused_from" not in pt._overlay_evidence(tmp_path, base, re_ev, [])


def test_known_pre_existing_failure_skips_the_reruns(rig, capsys, tmp_path, monkeypatch):
    """A failure that also fails at HEAD is pre-existing whatever a re-run shows, so only
    the HEAD lookup runs (no whole-file / prefix re-run); HEAD outcomes stored with
    another interpreter id are not trusted."""
    repo = rig({"backend/tests/test_pre.py": (
        "from core.mod import add\n\n\ndef test_pre():\n    assert add(1, 1) == 3\n")})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return a + b  # v2\n"})
    sha = _git(repo, "rev-parse", "HEAD").strip()
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    nid = "backend/tests/test_pre.py::test_pre"
    pt._baseline_path(cache, sha).write_text(json.dumps({nid: "passed", "_python": "other-venv"}))
    tags: list[str] = []
    real = pt.run_pytest_targets
    monkeypatch.setattr(pt, "run_pytest_targets",
                        lambda ctx, t, d, tag, *a, **k: tags.append(tag) or real(ctx, t, d, tag, *a, **k))
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 0, out
    assert {f["nodeid"]: f["classification"] for f in out["failures"]} == {nid: "pre-existing"}
    assert tags == ["base"], tags  # the HEAD lookup only
    stored = json.loads(pt._baseline_path(cache, sha).read_text())
    assert stored[nid] == "failed" and stored["_python"] == pt._python_id(sys.executable)


def test_selection_adds_tests_that_drive_a_changed_script_by_subprocess(rig, tmp_path):
    """No import edge reaches a test that runs ``scripts/tool.py`` as a subprocess; naming
    the script's file ranks it as a direct importer (ahead of a 2-hop importer)."""
    repo = rig({
        **TRANS,
        "backend/scripts/tool.py": "from core.b import val\nprint(val())\n",
        "backend/tests/test_tool_cli.py": (
            "import subprocess, sys\nfrom pathlib import Path\n\n\ndef test_cli():\n"
            "    p = Path(__file__).parents[1] / 'scripts' / 'tool.py'\n"
            "    assert subprocess.run([sys.executable, str(p)]).returncode == 0\n"),
        "backend/tests/test_names_mod.py": "def test_m():\n    assert 'mod.py'\n",
        "backend/tests/test_names_mytool.py": "def test_m():\n    assert 'scripts/mytool.py'\n",
    })
    _write(repo, {"backend/scripts/tool.py": "from core.b import val\nprint(val(), 1)\n",
                  "backend/core/b.py": "def val():\n    return 1  # v2\n"})
    cache = tmp_path / "cache"
    sgraph = pt.build_source_graph(repo, cache)
    graph = pt.build_import_graph(repo, cache, sgraph)
    changed = pt.changed_files(repo, {})
    sel, _, _ = pt.select_backend(repo, changed, graph, 40, (), sgraph)
    assert "backend/tests/test_tool_cli.py" in sel
    assert "backend/tests/test_a.py" not in sel  # 2 hops: outside the default depth
    sel_d2, _, _ = pt.select_backend(repo, changed, graph, 40, (), sgraph, depth=2)
    assert "backend/tests/test_tool_cli.py" in sel_d2
    assert "backend/tests/test_names_mod.py" not in sel  # only scripts/*.py names count
    assert "backend/tests/test_names_mytool.py" not in sel  # a whole file name, not a suffix
    # without a script change the subprocess test is not selected
    _write(repo, {"backend/scripts/tool.py": "from core.b import val\nprint(val())\n"})
    sel2, _, _ = pt.select_backend(repo, pt.changed_files(repo, {}), graph, 40, (), sgraph)
    assert "backend/tests/test_tool_cli.py" not in sel2


def test_a_module_loaded_by_computed_name_is_never_served_stale(rig, capsys, tmp_path):
    """red-team 2026-10-18: no import edge reaches core/plugin_a.py (importlib with a built
    name), so a per-file key of static imports alone reused a stale pass after it broke."""
    reg = "from core.registry import load\n\n\ndef test_load():\n    assert load() == 'ok'\n"
    repo = rig({
        "backend/core/registry.py": ("import importlib\n\n\ndef load():\n"
                                     "    return importlib.import_module('core.' + 'plugin_' + 'a').VALUE\n"),
        "backend/core/plugin_a.py": "VALUE = 'ok'\n",
        "backend/tests/test_reg.py": reg})
    _write(repo, {"backend/core/plugin_a.py": "VALUE = 'ok'  # v2\n",
                  "backend/tests/test_reg.py": reg + "\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 0, out
    _write(repo, {"backend/core/plugin_a.py": "VALUE = 'BROKEN'\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    assert code == 1, out
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert "reused_from" not in ev
    assert [f["nodeid"] for f in ev["failures"]] == ["backend/tests/test_reg.py::test_load"]


def test_every_direct_importer_runs_and_transitive_ones_are_named(rig, tmp_path, capsys):
    """Expert review 2026-10-19: name matching dropped tests that a rename, a rebound
    import, a multi-line import or getattr dispatch would break. Every direct importer
    runs now (no narrowing); tests reaching the change only through another module are
    not run but listed in the evidence (``transitive_unrun``) for REPORT §10."""
    repo = rig({"backend/core/leaf.py": "def old_name():\n    return 1\n",
                "backend/core/facade.py": "from core.leaf import old_name\n\n\ndef api():\n    return old_name()\n",
                "backend/tests/test_l_multi.py": ("from core.leaf import (\n    old_name,\n)\n\n\n"
                                                  "def test_m():\n    assert old_name() == 1\n"),
                "backend/tests/test_l_getattr.py": ("import core.leaf as lf\n\n\n"
                                                    "def test_g():\n    assert getattr(lf, 'old' + '_name')() == 1\n"),
                "backend/tests/test_l_facade.py": "from core.facade import api\n\n\ndef test_f():\n    assert api() == 1\n"},
               run_state={"files_touched": ["backend/core/leaf.py"]})
    _write(repo, {"backend/core/leaf.py": "def new_name():\n    return 1\n"})  # a rename
    cache = tmp_path / "cache"
    sgraph = pt.build_source_graph(repo, cache)
    graph = pt.build_import_graph(repo, cache, sgraph)
    sel, skipped, _ = pt.select_backend(repo, ["backend/core/leaf.py"], graph, 1, (), sgraph)
    assert {"backend/tests/test_l_multi.py", "backend/tests/test_l_getattr.py"} <= set(sel)
    assert skipped == [] and "backend/tests/test_l_facade.py" not in sel
    assert pt.transitive_unselected(repo, sgraph, graph, ["backend/core/leaf.py"], sel) == [
        "backend/tests/test_l_facade.py"]
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1 and out["counts"]["failed"] + out["counts"]["errors"] >= 2, out  # rename breaks both
    assert out["transitive_unrun_total"] == 1
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    assert ev["transitive_unrun"] == ["backend/tests/test_l_facade.py"]


def test_files_touched_counts_only_where_git_sees_a_change(rig):
    """An existing but unchanged files_touched entry must not replace the real change set."""
    repo = rig()
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    got = pt.changed_files(repo, {"files_touched": ["backend/tests/test_unrelated.py"]})
    assert "backend/core/mod.py" in got
    assert pt.changed_files(repo, {"files_touched": ["backend/core/mod.py"]}) == ["backend/core/mod.py"]


def test_a_deleted_modules_importers_still_run(rig, tmp_path):
    repo = rig({"backend/core/gone.py": "def f():\n    return 1\n",
                "backend/tests/test_gone.py": "from core.gone import f\n\n\ndef test_f():\n    assert f()\n"})
    (repo / "backend/core/gone.py").unlink()
    cache = tmp_path / "cache"
    sgraph = pt.build_source_graph(repo, cache)
    graph = pt.build_import_graph(repo, cache, sgraph)
    sel, _, _ = pt.select_backend(repo, ["backend/core/gone.py"], graph, 40, (), sgraph)
    assert "backend/tests/test_gone.py" in sel


def test_a_scope_that_does_not_fit_one_call_continues_across_calls(rig, tmp_path, capsys):
    """Every call stays inside alarm 110; nothing selected is cut or refused. A call cut
    by its deadline says continue: true, and the SAME command finishes only the rest."""
    slow = "import time\nfrom core.mod import add\n\n\ndef test_s():\n    time.sleep(1.5)\n    assert add(1, 1) == 2\n"
    repo = rig({f"backend/tests/test_slow_{i}.py": slow for i in range(4)},
               run_state={"files_touched": ["backend/core/mod.py"]})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    calls, out = 0, {"continue": True}
    while out.get("continue") and calls < 8:
        out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--deadline", "4")
        calls += 1
    assert calls >= 2, out                       # one 4 s call cannot hold 4 × 1.5 s serial
    assert "continue" not in out and code == 0, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    passed = {r["nodeid"] for r in ev["results"] if r["outcome"] == "passed"}
    assert {f"backend/tests/test_slow_{i}.py::test_s" for i in range(4)} <= passed
    assert ev["counts"]["timeouts"] == 0


def test_a_renamed_scripts_old_name_still_selects_its_tests(rig, tmp_path):
    repo = rig({"backend/scripts/oldtool.py": "print(1)\n",
                "backend/tests/test_oldtool_cli.py": "def test_c():\n    assert 'oldtool.py'\n"})
    (repo / "backend/scripts/oldtool.py").rename(repo / "backend/scripts/newtool.py")
    cache = tmp_path / "cache"
    sgraph = pt.build_source_graph(repo, cache)
    graph = pt.build_import_graph(repo, cache, sgraph)
    changed = ["backend/scripts/oldtool.py", "backend/scripts/newtool.py"]
    sel, _, _ = pt.select_backend(repo, changed, graph, 40, (), sgraph)
    assert "backend/tests/test_oldtool_cli.py" in sel


def test_a_deleted_modules_transitive_tests_are_listed(rig, tmp_path):
    repo = rig({"backend/core/gone2.py": "def g():\n    return 1\n",
                "backend/core/wrap2.py": "from core.gone2 import g\n\n\ndef w():\n    return g()\n",
                "backend/tests/test_wrap2.py": "from core.wrap2 import w\n\n\ndef test_w():\n    assert w()\n"})
    (repo / "backend/core/gone2.py").unlink()
    cache = tmp_path / "cache"
    sgraph = pt.build_source_graph(repo, cache)
    graph = pt.build_import_graph(repo, cache, sgraph)
    sel, _, _ = pt.select_backend(repo, ["backend/core/gone2.py"], graph, 40, (), sgraph)
    assert "backend/tests/test_wrap2.py" in pt.transitive_unselected(
        repo, sgraph, graph, ["backend/core/gone2.py"], sel)


def test_test_modes_refuse_a_deadline_that_outlives_alarm_110(rig, capsys):
    rig()
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--deadline", "300")
    assert code == 2 and "continue" in out["error"], out
    d, _ = pt._clamp_deadline(5000)
    assert d == pt.MAX_DEADLINE == 575  # probe / mutation ceiling


def test_continuations_are_one_round_for_the_circuit_breaker(tmp_path):
    tdir = tmp_path / "tests"
    tdir.mkdir()
    rows = [{"kind": "run", "mode": "regression", "blocking_sig": [f"t{i}"]}
            for i in range(3)]
    rows += [{"kind": "run", "mode": "regression", "auto_recheck_of": "regression",
              "blocking_sig": [f"c{i}"]} for i in range(pt.CIRCUIT_MAX_ROUNDS)]
    (tdir / pt.RUN_INDEX).write_text(json.dumps(rows))
    assert pt._circuit_breaker_check(tmp_path, "regression") is None
    # the same unfinished set four calls running is a stall, regression included
    stall = rows + [{"kind": "run", "mode": "regression", "auto_recheck_of": "regression",
                     "blocking_sig": ["x"]}] * pt.CIRCUIT_STALL_ROUNDS
    (tdir / pt.RUN_INDEX).write_text(json.dumps(stall))
    assert pt._circuit_breaker_check(tmp_path, "regression")["circuit_breaker"]["tripped"]


def test_a_continuation_replays_the_earlier_tests_of_the_file(rig, tmp_path, capsys):
    """test_b only passes after test_a set the module state; the first call is cut at
    test_b. The continuation must run test_a again before it (alone, test_b fails)."""
    flag = tmp_path / "slow-once"
    body = ("import os, time\nfrom core import mod\nSTATE = {}\n\n\n"
            "def test_a():\n    STATE['x'] = mod.add(1, 1)\n\n\n"
            f"def test_b():\n    if not os.path.exists({str(flag)!r}):\n"
            f"        open({str(flag)!r}, 'w').close()\n        time.sleep(30)\n"
            "    assert STATE.get('x') == 2\n")
    repo = rig({"backend/tests/test_ctx.py": body},
               run_state={"files_touched": ["backend/core/mod.py"]})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--deadline", "8")
    assert out.get("continue") is True, out
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--deadline", "8")
    assert code == 0 and "continue" not in out, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    assert "backend/tests/test_x.py" not in {r["nodeid"] for r in ev["results"]}  # placeholder answered


def test_bvt_manifest_files_join_the_regression_scope(rig, tmp_path, capsys):
    repo = rig({"backend/tests/test_bvt_one.py": "def test_ok():\n    assert True\n",
                "backend/tests/bvt_manifest.txt": "# CI BVT\ntest_bvt_one.py  # pure\n\nmissing.py\n"},
               run_state={"files_touched": ["backend/core/mod.py"]})
    warns: list = []
    assert pt._bvt_files(repo, warns) == ["backend/tests/test_bvt_one.py"]
    assert "missing.py" in warns[0]
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "regression")
    ev = _evidence(tmp_path, "tests/latest-regression.json")
    assert ev["layers"]["ci_bvt"]["files"] == 1 and ev["layers"]["ci_bvt"]["pass"] == 1, ev["layers"]
    assert "backend/tests/test_bvt_one.py::test_ok" in {r["nodeid"] for r in ev["results"]}


def test_a_heavy_continuation_file_spreads_over_workers():
    ids = [f"backend/tests/test_h.py::test_{i}" for i in range(8)]
    dur = {n: 10.0 for n in ids}
    items = [(0, "backend/tests/test_h.py", set(ids), set(), [pt._backend_rel(n) for n in ids])]
    bins = pt._cost_bins(items, 4, dur)
    assert len(bins) >= 4
    got = [a for _, args in bins for a in args]
    assert sorted(got) == sorted(pt._backend_rel(n) for n in ids)  # each test exactly once
