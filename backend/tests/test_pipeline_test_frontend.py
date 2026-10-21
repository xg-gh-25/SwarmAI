"""Frontend path of ``scripts/pipeline_test.py``: vitest related + tsc, run for real.

Methodology: the ``rig`` tmp repo from test_pipeline_test gets a minimal ``desktop/``
project (package.json, a tiny tsconfig.json, ``node_modules`` symlinked to the real
SwarmAI desktop/node_modules) so the REAL vitest and tsc run, offline, in a few
seconds each. These are marked ``slow`` and skip when node_modules / npx are absent.
Unit tests feed synthetic tool output to the parsers (no processes).

Properties:
- tdd with several vitest files: per-test nodeids, compact passed entries with a
  duration; a failing test → exit 1, ``file::name`` nodeid, assertion message + the
  test-file frame (node_modules frames dropped), classification ``new`` for a file
  absent at HEAD
- verify with a TS type error AND a backend change in one call: tsc reports ONE
  ``error`` result listing ``desktop/<file>:<line>:<col>: error TS…``; the backend
  pytest results arrive in the same evidence
- a hung vitest test: the call returns by the deadline, the hung file is a timeout,
  files that finished are reported, no vitest process outlives the call
- a vitest exit != 0 without any failing result (a hard-killed worker shows up as a
  skipped test) is an error, never green
- the frontend result cache key covers every changed desktop file and HEAD
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pipeline_test as pt
from tests.test_pipeline_test import (  # noqa: F401 — rig is a fixture
    RUN_ID, _evidence, _run, _write, rig)

_NODE_MODULES = (Path(pt.__file__).resolve().parents[2] / "desktop" / "node_modules").resolve()
_REAL = (_NODE_MODULES / ".bin" / "vitest").exists() and (_NODE_MODULES / ".bin" / "tsc").exists() \
    and shutil.which("npx") is not None
real = pytest.mark.skipif(not _REAL, reason="needs desktop/node_modules (vitest, tsc) and npx")

TSCONFIG = ('{"compilerOptions": {"strict": true, "noEmit": true, "target": "ES2020", '
            '"module": "ESNext", "moduleResolution": "bundler", "skipLibCheck": true, '
            '"types": []}, "include": ["src"], "exclude": ["src/**/*.test.ts"]}\n')
ALPHA = "import { it, expect } from 'vitest'\n\nit('adds', () => {\n  expect(1 + 1).toBe(2)\n})\n"
GAMMA = ("import { describe, it, expect } from 'vitest'\n\ndescribe('gamma', () => {\n"
         "  it('passes', () => {\n    expect(true).toBe(true)\n  })\n"
         "  it('fails on purpose', () => {\n    expect(2 + 2).toBe(5)\n  })\n})\n")
HANG = ("import { it } from 'vitest'\n\nit('hangs', async () => {\n"
        "  await new Promise((r) => setTimeout(r, 600_000))\n}, 700_000)\n")


def _desktop(repo: Path, files: dict[str, str]) -> None:
    """Minimal real desktop project in the tmp repo (uncommitted → new at HEAD)."""
    _write(repo, {"desktop/package.json": '{"name": "t", "private": true, "type": "module"}\n',
                  "desktop/tsconfig.json": TSCONFIG, **files})
    (repo / "desktop" / "node_modules").symlink_to(_NODE_MODULES, target_is_directory=True)


def _live_vitest(repo: Path) -> list[str]:
    """Command lines of vitest processes still running for *repo*. ``ps`` first;
    psutil when ``ps`` cannot run (a sandboxed agent shell denies spawning it)."""
    try:
        ps = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True,
                            timeout=10)
        lines = ps.stdout.splitlines()
    except OSError:
        import psutil
        lines = []
        for p in psutil.process_iter(attrs=["pid", "cmdline"]):
            try:
                lines.append(f"{p.info['pid']} {' '.join(p.info['cmdline'] or [])}")
            except psutil.Error:
                continue
    return [ln for ln in lines if "vitest" in ln and str(repo) in ln]


@real
@pytest.mark.slow
def test_real_vitest_several_files_and_a_failing_test(rig, capsys, tmp_path):
    repo = rig()
    _desktop(repo, {"desktop/src/alpha.test.ts": ALPHA, "desktop/src/gamma.test.ts": GAMMA})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files",
                     "desktop/src/alpha.test.ts", "desktop/src/gamma.test.ts")
    assert code == 1, out
    assert out["counts"]["passed"] == 2 and out["counts"]["failed"] == 1, out
    (f,) = out["failures"]
    assert f["nodeid"] == "desktop/src/gamma.test.ts::gamma fails on purpose"
    assert f["classification"] == "new"
    assert "expected 4 to be 5" in f["summary"][0]
    assert any("gamma.test.ts:8" in ln for ln in f["summary"])
    assert not any("node_modules" in ln for ln in f["summary"])
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    passed = [r for r in ev["results"] if r["outcome"] == "passed"]
    assert {r["nodeid"] for r in passed} == {"desktop/src/alpha.test.ts::adds",
                                              "desktop/src/gamma.test.ts::gamma passes"}
    assert all(set(r) == {"nodeid", "outcome", "duration"} for r in passed), passed
    assert ev["frontend"]["vitest"] == {"passed": 2, "failed": 1}


@real
@pytest.mark.slow
def test_real_verify_mixed_backend_and_desktop_reports_tsc_file_line(rig, capsys, tmp_path):
    repo = rig()
    _desktop(repo, {"desktop/src/alpha.test.ts": ALPHA,
                    "desktop/src/bad.ts": "export const total: number = 'not a number'\n"})
    _write(repo, {"backend/tests/test_zz.py": (
        "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert 1 == 2\n")})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1, out
    ev = _evidence(tmp_path, "tests/latest-verify.json")
    by_id = {r["nodeid"]: r["outcome"] for r in ev["results"]}
    assert by_id["backend/tests/test_zz.py::test_ok"] == "passed"
    assert by_id["backend/tests/test_zz.py::test_bad"] == "failed"
    assert by_id["desktop/src/alpha.test.ts::adds"] == "passed"
    assert by_id["desktop::tsc"] == "error"
    tsc = next(f for f in ev["failures"] if f["nodeid"] == "desktop::tsc")
    assert "desktop/src/bad.ts:1:14: error TS2322" in tsc["summary"], tsc
    assert ev["frontend"]["tsc"]["ok"] is False
    assert {f["nodeid"]: f["classification"] for f in ev["failures"]} == {
        "backend/tests/test_zz.py::test_bad": "new", "desktop::tsc": "unknown"}


@real
@pytest.mark.slow
def test_real_vitest_hang_is_killed_at_the_deadline(rig, capsys, tmp_path):
    repo = rig()
    _desktop(repo, {"desktop/src/alpha.test.ts": ALPHA, "desktop/src/hang.test.ts": HANG})
    t0 = time.monotonic()
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--deadline", "20", "--files",
                     "desktop/src/hang.test.ts", "desktop/src/alpha.test.ts")
    assert time.monotonic() - t0 < 25, "the call must return by its deadline"
    assert code == 1 and out["counts"]["timeouts"] == 1, out
    ev = _evidence(tmp_path, "tests/latest-tdd.json")
    by_id = {r["nodeid"]: r["outcome"] for r in ev["results"]}
    assert by_id == {"desktop/src/hang.test.ts": "timeout", "desktop/src/alpha.test.ts": "passed"}
    time.sleep(0.5)
    assert _live_vitest(repo) == []


CRASH = "import { it } from 'vitest'\n\nit('dies', () => {\n  process.kill(process.pid, 'SIGKILL')\n})\n"


@real
@pytest.mark.slow
def test_real_hard_killed_vitest_worker_is_never_green(rig, capsys):
    repo = rig()
    _desktop(repo, {"desktop/src/alpha.test.ts": ALPHA, "desktop/src/crash.test.ts": CRASH})
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--deadline", "40", "--files",
                     "desktop/src/crash.test.ts", "desktop/src/alpha.test.ts")
    assert code == 1, out  # vitest marks the dead test "skipped" and exits 1
    assert out["counts"]["errors"] >= 1, out


# ── parsers (no processes) ──────────────────────────────────────────────────
def _job(tmp_path: Path, kind: str, rc: int, out: str = "", err: str = "", **meta) -> pt._Job:
    j = pt._Job(kind, ["x"], tmp_path, {}, tmp_path, kind, **meta)
    j.out.write_text(out, encoding="utf-8")
    j.err.write_text(err, encoding="utf-8")
    j.started, j.rc = True, rc
    return j


def test_killed_vitest_keeps_finished_files_and_times_out_the_rest(tmp_path):
    files = ["desktop/src/a.test.ts", "desktop/src/b.test.tsx", "desktop/src/c.test.ts",
             "desktop/src/util.ts"]
    j = _job(tmp_path, "vitest", -9, out=(
        " RUN  v4\nBrowserslist: browsers data is old\n ✓ src/a.test.ts (1 test) 2ms\n"
        " ❯ src/b.test.tsx (2 tests | 1 failed) 4ms\n     × x 3ms\n ❯ src/b.test.tsx:8:19\n"),
        files=files, json=tmp_path / "none.json")
    j.killed = True
    got = {r["nodeid"]: r["outcome"] for r in pt._parse_frontend(j)}
    assert got == {"desktop/src/a.test.ts": "passed", "desktop/src/b.test.tsx": "failed",
                   "desktop/src/c.test.ts": "timeout"}
    j.meta["files"] = ["desktop/src/a.test.ts"]  # everything requested finished
    res = pt._parse_frontend(j)
    tmo = [r for r in res if r["outcome"] == "timeout"]
    assert [r["nodeid"] for r in tmo] == ["desktop::vitest"]
    assert not any("Browserslist" in ln for ln in tmo[0]["summary"])


def test_tsc_errors_are_one_error_result_with_repo_relative_file_line(tmp_path):
    lines = [f"src/f{i}.ts({i},2): error TS2322: bad {i}" for i in range(1, 13)]
    j = _job(tmp_path, "tsc", 2, out="\n".join(["src/x.ts:3:4 - error TS1005: ';' expected."]
                                                + lines + ["", "Found 13 errors."]))
    (r,) = pt._parse_frontend(j)
    assert r["nodeid"] == "desktop::tsc" and r["outcome"] == "error" and r["layer"] == "tsc"
    assert r["summary"][0] == "tsc: 13 type error(s), rc=2"
    assert r["summary"][1] == "desktop/src/x.ts:3:4: error TS1005: ';' expected."
    assert r["summary"][2] == "desktop/src/f1.ts:1:2: error TS2322: bad 1"
    assert r["summary"][-1] == "+3 more type error(s)"
    ok = pt._parse_frontend(_job(tmp_path, "tsc", 0))
    assert [(x["nodeid"], x["outcome"]) for x in ok] == [("desktop::tsc", "passed")]


def test_nonzero_vitest_exit_without_a_failure_is_an_error(tmp_path):
    js, root = tmp_path / "v.json", tmp_path.resolve()
    js.write_text(json.dumps({"testResults": [
        {"name": str(root / "src/a.test.ts"), "assertionResults": [
            {"fullName": "ok", "status": "passed", "duration": 2.5}]},
        {"name": str(root / "src/k.test.ts"), "assertionResults": [
            {"fullName": "dies", "status": "skipped"}]}]}))
    files = ["desktop/src/a.test.ts", "desktop/src/k.test.ts", "desktop/src/missing.test.ts"]
    res = pt._parse_frontend(_job(tmp_path, "vitest", 1, out="[vitest-pool]: Timeout terminating "
                                  "forks worker", json=js, files=files))
    got = {r["nodeid"]: r["outcome"] for r in res}
    assert got["desktop/src/a.test.ts::ok"] == "passed"
    assert got["desktop/src/missing.test.ts"] == "error"  # requested, no result: never green
    assert next(r for r in res if r["nodeid"] == "desktop/src/a.test.ts::ok")["duration"] == 0.003
    res = pt._parse_frontend(_job(tmp_path, "vitest", 1, json=js, files=files[:2]))
    assert [r["nodeid"] for r in res if r["outcome"] == "error"] == ["desktop::vitest"]
    green = pt._parse_frontend(_job(tmp_path, "vitest", 0, json=js, files=files[:2]))
    assert green and not any(r["outcome"] == "error" for r in green)


def test_frontend_cache_key_covers_changed_desktop_files(rig):
    repo = rig()
    _write(repo, {"desktop/src/a.test.ts": ALPHA, "desktop/src/util.ts": "export const u = 1\n"})
    ctx = SimpleNamespace(repo=repo)
    changed = ["desktop/src/util.ts", "backend/core/mod.py"]
    k1 = pt._frontend_key(ctx, "tdd", ["desktop/src/a.test.ts"], changed)
    _write(repo, {"backend/core/mod.py": "x = 2\n"})
    assert pt._frontend_key(ctx, "tdd", ["desktop/src/a.test.ts"], changed) == k1  # not desktop
    _write(repo, {"desktop/src/util.ts": "export const u = 2\n"})  # an import of the test
    assert pt._frontend_key(ctx, "tdd", ["desktop/src/a.test.ts"], changed) != k1
