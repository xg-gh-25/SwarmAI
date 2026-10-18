"""Unit tests for pipeline_test.py's cost-based shard balancing (run_1f81be36).

The runner used to pack shards by FILE COUNT and keep every file whole, so one heavy
file (the nested-runner suite, 5+ minutes) became the long pole of every run. These
tests pin the replacement:

- AC1: report events carry durations; ``_parse_pytest`` sums them per test; a main
  run's history (``<cache>/durations.json``) records only tests that actually ran,
  keyed by the ``backend/``-prefixed nodeid, bounded, newest wins, unreadable = empty.
- AC2: with history, non-serial targets are packed by cost (LPT) and shards still
  start in priority order.
- AC3: a heavy whole-file target splits into chunks in different shards. Every chunk
  but the last runs only its own history ids (plugin keep list); the last runs the
  file minus the other chunks, so a test missing from history runs there only. A
  parametrized family stays in one chunk; AC-lead ids never enter a keep list.
- AC4: serial suites never split; an empty history gives the old plan exactly;
  baseline / mutant / flaky / requeue plans never use history.

Selection is checked by simulating the plugin's keep/deselect rule over the files'
real nodeids, plus one plugin test against a real pytest subprocess. All pure or
tmp_path based — no nested runner, so this file stays fast.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import pipeline_test as pt


def _match(nid: str, ids) -> bool:
    return any(nid == d or nid.startswith((d + "[", d + "::")) for d in ids)


def _selected(shard: list[str], files: dict[str, list[str]]) -> list[str]:
    """The nodeids one shard runs, with the plugin's keep/deselect semantics.

    ``files`` maps a backend-relative file to the nodeids pytest would collect there.
    """
    keep = [a[len("--keep="):] for a in shard if a.startswith("--keep=")]
    desel = [a[len("--deselect="):] for a in shard if a.startswith("--deselect=")]
    keep_files = {k.split("::")[0] for k in keep}
    out: list[str] = []
    for a in shard:
        if a.startswith("--"):
            continue
        f = a.split("::")[0]
        for nid in files[f]:
            if "::" in a and not _match(nid, [a]):
                continue
            if _match(nid, desel):
                continue
            if f in keep_files and not _match(nid, keep):
                continue
            out.append(nid)
    return out


def _run_all(shards, files) -> list[str]:
    return [n for sh in shards for n in _selected(sh, files)]


# ── AC1: durations ──────────────────────────────────────────────────────────
def _job(tmp_path, events: list[dict], args=("tests/test_x.py",)):
    ev = tmp_path / "j.events"
    ev.write_text("".join(json.dumps(e) + "\n" for e in events))
    return SimpleNamespace(started=True, killed=False, rc=1, kind="pytest",
                           meta={"args": list(args), "events": ev, "deselected": set()},
                           text=lambda: "")


def test_parse_pytest_sums_phase_durations_per_test(tmp_path):
    f = "tests/test_x.py"
    events = [{"ev": "collected", "nodeids": [f"{f}::a", f"{f}::hung", f"{f}::never"]}]
    for when, dur in (("setup", 0.1), ("call", 0.5), ("teardown", 0.05)):
        events.append({"ev": "report", "nodeid": f"{f}::a", "when": when, "outcome": "passed",
                       "longrepr": "", "dur": dur})
    events += [{"ev": "start", "nodeid": f"{f}::a"}, {"ev": "start", "nodeid": f"{f}::hung"}]
    res = {r["nodeid"]: r for r in pt._parse_pytest(_job(tmp_path, events))}
    assert res[f"backend/{f}::a"]["duration"] == pytest.approx(0.65)
    assert res[f"backend/{f}::hung"]["duration"] == pt.PER_TEST_TIMEOUT_S  # started, never finished
    assert "duration" not in res[f"backend/{f}::never"]                     # never ran: no cost known


def test_durations_record_only_tests_that_ran_and_newest_wins(tmp_path):
    cache = tmp_path / "cache"
    pt._save_durations(cache, [
        {"nodeid": "backend/tests/t.py::a", "outcome": "passed", "duration": 2.0},
        {"nodeid": "backend/tests/t.py::b", "outcome": "timeout", "notrun": True},
        {"nodeid": "backend/tests/t.py::c", "outcome": "timeout", "deadline": True, "duration": 9.0},
        {"nodeid": "backend/tests/t.py::d", "outcome": "error", "runner_error": True, "duration": 1.0},
        {"nodeid": "backend/tests/t.py", "outcome": "skipped", "collect_skip": True, "duration": 0.0},
        {"nodeid": "backend/tests/t.py::e", "outcome": "failed"},  # no duration recorded
    ])
    assert pt._load_durations(cache) == {"backend/tests/t.py::a": 2.0}
    pt._save_durations(cache, [{"nodeid": "backend/tests/t.py::a", "outcome": "passed", "duration": 5.0}])
    assert pt._load_durations(cache) == {"backend/tests/t.py::a": 5.0}


def test_durations_history_is_bounded_oldest_evicted(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setattr(pt, "MAX_DURATION_ENTRIES", 2)
    for i, n in enumerate(("a", "b", "c")):
        monkeypatch.setattr(pt.time, "time", lambda i=i: 1000.0 + i)
        pt._save_durations(cache, [{"nodeid": f"backend/tests/t.py::{n}", "outcome": "passed",
                                    "duration": 1.0}])
    assert set(pt._load_durations(cache)) == {"backend/tests/t.py::b", "backend/tests/t.py::c"}


def test_unreadable_history_counts_as_empty(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "durations.json").write_text("{not json")
    assert pt._load_durations(cache) == {}
    (cache / "durations.json").write_text(json.dumps({"backend/tests/t.py::a": "bad", "x": [1]}))
    assert pt._load_durations(cache) == {}
    pt._save_durations(cache, [{"nodeid": "backend/tests/t.py::a", "outcome": "passed", "duration": 1.5}])
    assert pt._load_durations(cache) == {"backend/tests/t.py::a": 1.5}


# ── AC4: no history → old plan; serial never split ──────────────────────────
FIXTURES = [
    ({"backend/tests/test_stage_boundary_commands.py": None,
      "backend/tests/test_pipeline_run_profile.py": None,
      "backend/tests/test_alpha_importer.py": None,
      "backend/tests/test_context_health_hook.py": None,
      "backend/tests/test_beta_importer.py": {"backend/tests/test_beta_importer.py::t"}}, 1, None),
    ({"backend/tests/test_slow.py": None,
      "backend/tests/test_mixed.py": {"backend/tests/test_mixed.py::t1", "backend/tests/test_mixed.py::t_ac"},
      "backend/tests/test_context_health_hook.py": None}, 2,
     {"backend/tests/test_slow.py": {"backend/tests/test_slow.py::test_ac"},
      "backend/tests/test_mixed.py": {"backend/tests/test_mixed.py::t_ac"}}),
]


@pytest.mark.parametrize("targets,workers,first", FIXTURES)
def test_empty_history_gives_the_count_based_plan(targets, workers, first):
    assert pt._shards(targets, workers, first, durations={}) == pt._shards(targets, workers, first)
    assert pt._shards(targets, workers, first, durations=None) == pt._shards(targets, workers, first)


def test_serial_suite_is_never_split():
    f = "backend/tests/test_context_health_hook.py"
    hist = {f"{f}::t{i}": 60.0 for i in range(10)}
    targets = {f: None, "backend/tests/test_a.py": None, "backend/tests/test_b.py": None}
    shards = pt._shards(targets, 2, durations=hist)
    assert ["tests/test_context_health_hook.py"] in shards
    assert not any(a.startswith("--keep=tests/test_context_health_hook.py") for sh in shards for a in sh)


# ── AC2 / AC3: cost packing + split ─────────────────────────────────────────
def _heavy_world():
    """Two heavy files (10 × 10 s) and eight light ones (1 s each)."""
    files, hist, targets = {}, {}, {}
    for name, n, cost in [("test_h1", 10, 10.0), ("test_h2", 10, 10.0)] + \
                         [(f"test_l{i}", 1, 1.0) for i in range(8)]:
        rel = f"tests/{name}.py"
        files[rel] = [f"{rel}::t{i}" for i in range(n)]
        for nid in files[rel]:
            hist["backend/" + nid] = cost
        targets["backend/" + rel] = None
    return files, hist, targets


def _cost(shard, files, hist):
    return sum(hist.get("backend/" + n, 0.0) for n in _selected(shard, files))


def test_heavy_files_split_and_bins_balance():
    files, hist, targets = _heavy_world()
    shards = pt._shards(targets, workers=2, durations=hist)
    ran = _run_all(shards, files)
    assert sorted(ran) == sorted(n for ns in files.values() for n in ns)  # every test exactly once
    costs = [_cost(sh, files, hist) for sh in shards]
    share = sum(costs) / 4  # 2 x workers bins
    assert max(costs) <= share * 1.2  # about one share, not 100 s on one shard
    h1_shards = [i for i, sh in enumerate(shards) if any(a.startswith("tests/test_h1.py") for a in sh)]
    assert len(h1_shards) >= 2  # split across shards


def test_shards_start_in_target_priority_order_with_history():
    files, hist, targets = _heavy_world()
    order = list(targets)
    shards = pt._shards(targets, workers=2, durations=hist)

    def first_rank(sh):
        return min(order.index("backend/" + a.split("::")[0]) for a in sh if not a.startswith("--"))
    ranks = [first_rank(sh) for sh in shards]
    assert ranks == sorted(ranks)


def test_split_runs_every_test_once_with_unknown_ac_and_param_family():
    rel = "tests/test_big.py"
    real = [f"{rel}::t{i}" for i in range(6)] + [f"{rel}::test_p[1]", f"{rel}::test_p[2]",
                                                 f"{rel}::test_ac", f"{rel}::test_new",
                                                 f"{rel}::TestK::test_m"]
    files = {rel: real, "tests/test_small.py": ["tests/test_small.py::s"]}
    hist = {"backend/" + n: 30.0 for n in real if not n.endswith("test_new")}  # test_new: not in history
    hist["backend/" + rel + "::test_p"] = 30.0      # stale id from before parametrization
    hist["backend/" + rel + "::gone"] = 30.0        # deleted test still in history
    hist["backend/tests/test_small.py::s"] = 1.0
    targets = {"backend/" + rel: None, "backend/tests/test_small.py": None}
    first = {"backend/" + rel: {"backend/" + rel + "::test_ac"}}
    shards = pt._shards(targets, workers=4, first=first, durations=hist)
    ran = _run_all(shards, files)
    assert sorted(ran) == sorted(n for ns in files.values() for n in ns), shards  # exactly once
    big = [i for i, sh in enumerate(shards) if rel in sh]
    assert len(big) >= 2 and len(big) == len(set(big))
    keeps = [a for sh in shards for a in sh if a.startswith("--keep=")]
    assert not any("test_ac" in k for k in keeps)  # AC id never enters a keep list
    fam = [i for i, sh in enumerate(shards) for n in _selected(sh, files) if "test_p" in n]
    assert len(set(fam)) == 1  # a parametrized family stays in one chunk


def test_keep_tokens_go_to_the_plugin_and_count_as_deselected(tmp_path):
    ctx = SimpleNamespace(workdir=tmp_path, python=sys.executable, plugin_dir=tmp_path)
    job = pt._pytest_job(ctx, "j1", ["tests/test_a.py", "--keep=tests/test_a.py::t1",
                                     "--keep=tests/test_a.py::t2", "tests/test_b.py"], tmp_path, False)
    assert not any(a.startswith("--keep") for a in job.argv)
    assert json.loads(job.env["PIPELINE_TEST_KEEP"]) == ["tests/test_a.py::t1", "tests/test_a.py::t2"]
    assert job.meta["args"] == ["tests/test_a.py", "tests/test_b.py"]
    assert "tests/test_a.py" in job.meta["deselected"]  # an emptied keep chunk is no runner error


def test_plugin_keep_list_is_per_file_and_exact(tmp_path):
    (tmp_path / "test_t.py").write_text(
        "import pytest\n\n\ndef test_a():\n    pass\n\n\ndef test_ab():\n    pass\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_p(n):\n    pass\n")
    (tmp_path / "test_u.py").write_text("def test_u():\n    pass\n")
    plug = pt._plugin_dir(tmp_path / "cache")
    env = {**os.environ, "PYTHONPATH": str(plug),
           "PIPELINE_TEST_KEEP": json.dumps(["test_t.py::test_a", "test_t.py::test_p"]),
           "PIPELINE_TEST_EVENTS": str(tmp_path / "ev.jsonl")}
    env.pop("PYTEST_ADDOPTS", None)
    r = subprocess.run([sys.executable, "-m", "pytest", "test_t.py", "test_u.py", "-q", "-p", pt._PLUGIN_NAME,
                        "-p", "no:cacheprovider", "-o", "addopts="],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    evs = [json.loads(ln) for ln in (tmp_path / "ev.jsonl").read_text().splitlines()]
    ids = next(e["nodeids"] for e in evs if e["ev"] == "collected")
    assert ids == ["test_t.py::test_a", "test_t.py::test_p[1]", "test_t.py::test_p[2]", "test_u.py::test_u"], \
        r.stdout + r.stderr
    assert all("dur" in e for e in evs if e["ev"] == "report")


def test_baseline_and_flaky_plans_never_use_history(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(pt, "_shards", lambda t, w, first=None, durations=None: seen.append(durations) or [])
    monkeypatch.setattr(pt, "_run_jobs", lambda *a, **k: None)
    ctx = SimpleNamespace(seq=0, workers=2, durations={"backend/tests/a.py::t": 9.0}, repo=tmp_path)
    pt.run_pytest_targets(ctx, {"backend/tests/a.py": None}, time.monotonic() + 60, "base", root=tmp_path)
    pt.run_pytest_targets(ctx, {"backend/tests/a.py": None}, time.monotonic() + 60, "flaky")
    assert seen == [None, None]


def test_zero_cost_families_never_leave_an_empty_chunk_that_runs_the_whole_file():
    """Spec review: sub-millisecond tests round to 0.0 s; with LPT on cost alone they all
    stacked on one chunk and the other non-last chunks were EMPTY — an empty keep chunk
    has no keep list, so it ran the whole file (the 300 s test four times)."""
    rel = "tests/test_one_slow.py"
    real = [f"{rel}::slow"] + [f"{rel}::t{i}" for i in range(5)]
    files = {rel: real, "tests/test_small.py": ["tests/test_small.py::s"]}
    hist = {"backend/" + n: 0.0 for n in real}
    hist["backend/" + rel + "::slow"] = 300.0
    hist["backend/tests/test_small.py::s"] = 1.0
    targets = {"backend/" + rel: None, "backend/tests/test_small.py": None}
    shards = pt._shards(targets, workers=4, durations=hist)
    ran = _run_all(shards, files)
    assert sorted(ran) == sorted(n for ns in files.values() for n in ns), shards
    for sh in shards:  # a split chunk of the file always says what it runs
        if rel in sh:
            assert any(a.startswith(("--keep=", "--deselect=")) for a in sh) or \
                sum(1 for s2 in shards if rel in s2) == 1, sh


def test_stale_parent_id_in_history_does_not_rerun_an_ac_lead_test():
    """Spec review: history holds ``test_p`` (recorded before parametrization), the AC
    lead holds ``test_p[1]``: a keep chunk with ``--keep=test_p`` also ran ``test_p[1]``."""
    rel = "tests/test_lead.py"
    real = [f"{rel}::test_p[1]", f"{rel}::test_p[2]"] + [f"{rel}::t{i}" for i in range(4)]
    files = {rel: real}
    hist = {"backend/" + rel + "::test_p": 50.0}
    hist.update({"backend/" + f"{rel}::t{i}": 50.0 for i in range(4)})
    targets = {"backend/" + rel: None}
    first = {"backend/" + rel: {"backend/" + rel + "::test_p[1]"}}
    shards = pt._shards(targets, workers=2, first=first, durations=hist)
    assert shards[0] == [f"{rel}::test_p[1]"]
    ran = _run_all(shards, files)
    assert sorted(ran) == sorted(real), shards


def test_requeue_plan_never_uses_history(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(pt, "_shards", lambda t, w, first=None, durations=None: seen.append(durations) or [])
    monkeypatch.setattr(pt, "_run_jobs", lambda *a, **k: None)
    nr = {"nodeid": "backend/tests/a.py::t2", "outcome": "timeout", "file": "backend/tests/a.py",
          "notrun": True, "summary": []}
    monkeypatch.setattr(pt, "_parse_job", lambda j: [nr])
    job = SimpleNamespace(kind="pytest", started=True, killed=False)
    ctx = SimpleNamespace(seq=0, workers=2, durations={"backend/tests/a.py::t2": 9.0}, repo=tmp_path)
    pt._results_with_requeue(ctx, [job], time.monotonic() + 1000, "main")
    assert seen == [None]  # the requeue shard plan was built, without history


def test_split_keeps_the_run_own_files_in_the_first_wave():
    """Gate-2 correctness: LPT spread the run's own files (top ranks) over every bin, so
    under the deadline a rank-1 own file could wait behind low-priority importers."""
    files, hist, targets = {}, {}, {}
    for i in range(10):
        rel = f"tests/test_r{i}.py"
        files[rel] = [f"{rel}::t"]
        hist["backend/" + rel + "::t"] = 2.0 if i < 2 else 10.0  # own files light, importers heavy
        targets["backend/" + rel] = None
    shards = pt._shards(targets, workers=1, durations=hist)  # 2 bins
    first = [a for a in shards[0] if not a.startswith("--")]
    assert "tests/test_r0.py" in first and "tests/test_r1.py" in first, shards


def test_collect_error_of_a_split_file_is_reported_once(monkeypatch):
    """Gate-2 red-team: every chunk imports the module, so a collection error came back
    once per chunk under the same file nodeid."""
    err = {"nodeid": "backend/tests/big.py", "outcome": "error", "file": "backend/tests/big.py",
           "summary": ["ImportError"]}
    ok = {"nodeid": "backend/tests/x.py::t", "outcome": "passed", "file": "backend/tests/x.py", "summary": []}
    out = iter([[dict(err)], [dict(err), ok], [dict(err)]])
    monkeypatch.setattr(pt, "_parse_job", lambda j: next(out))
    jobs = [SimpleNamespace(kind="pytest", started=True, killed=False) for _ in range(3)]
    res = pt._results_with_requeue(SimpleNamespace(), jobs, time.monotonic(), "main")
    assert [r["nodeid"] for r in res].count("backend/tests/big.py") == 1
    assert ok in res


def test_plugin_dir_is_versioned_by_source(tmp_path, monkeypatch):
    """Gate-2 concurrency: one shared plugin file let an older runner (another worktree)
    put back a plugin without the keep list mid-run."""
    d1 = pt._plugin_dir(tmp_path)
    monkeypatch.setattr(pt, "_PLUGIN_SRC", pt._PLUGIN_SRC + "\n# older or newer runner\n")
    d2 = pt._plugin_dir(tmp_path)
    assert d1 != d2
    assert (d1 / f"{pt._PLUGIN_NAME}.py").read_text() != (d2 / f"{pt._PLUGIN_NAME}.py").read_text()


def test_no_dur_field_means_no_duration_and_failures_are_not_recorded(tmp_path):
    f = "tests/test_x.py"
    events = [{"ev": "collected", "nodeids": [f"{f}::old"]}]
    for when in ("setup", "call", "teardown"):  # an older plugin: reports without "dur"
        events.append({"ev": "report", "nodeid": f"{f}::old", "when": when, "outcome": "passed", "longrepr": ""})
    res = {r["nodeid"]: r for r in pt._parse_pytest(_job(tmp_path, events))}
    assert "duration" not in res[f"backend/{f}::old"]
    cache = tmp_path / "cache"
    pt._save_durations(cache, [
        {"nodeid": "backend/tests/t.py::fast_fail", "outcome": "failed", "duration": 0.01},
        {"nodeid": "backend/tests/t.py::err", "outcome": "error", "duration": 0.01},
        {"nodeid": "backend/tests/t.py::hung", "outcome": "timeout", "duration": 30.0},
    ])
    assert pt._load_durations(cache) == {"backend/tests/t.py::hung": 30.0}


def test_history_ages_out_and_rejects_non_finite(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    now = time.time()
    (cache / "durations.json").write_text(json.dumps({
        "backend/tests/t.py::old": [5.0, now - pt.DURATION_TTL_S - 10],
        "backend/tests/t.py::fresh": [5.0, now],
        "backend/tests/t.py::inf": [float("inf"), now]}))
    assert pt._load_durations(cache) == {"backend/tests/t.py::fresh": 5.0}
    assert pt._save_durations(cache, [{"nodeid": "backend/tests/t.py::n", "outcome": "passed",
                                       "duration": 1.0}]) is None
    assert set(json.loads((cache / "durations.json").read_text())) == {
        "backend/tests/t.py::fresh", "backend/tests/t.py::n"}


def test_plugin_keep_never_overrides_a_deselect(tmp_path):
    (tmp_path / "test_t.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('n', [1, 2, 10])\ndef test_p(n):\n    pass\n")
    plug = pt._plugin_dir(tmp_path / "cache")
    env = {**os.environ, "PYTHONPATH": str(plug),
           "PIPELINE_TEST_KEEP": json.dumps(["test_t.py::test_p"]),
           "PIPELINE_TEST_DESELECT": json.dumps(["test_t.py::test_p[1]"]),
           "PIPELINE_TEST_EVENTS": str(tmp_path / "ev.jsonl")}
    env.pop("PYTEST_ADDOPTS", None)
    subprocess.run([sys.executable, "-m", "pytest", "test_t.py", "-q", "-p", pt._PLUGIN_NAME,
                    "-p", "no:cacheprovider", "-o", "addopts="],
                   cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    evs = [json.loads(ln) for ln in (tmp_path / "ev.jsonl").read_text().splitlines()]
    ids = next(e["nodeids"] for e in evs if e["ev"] == "collected")
    assert ids == ["test_t.py::test_p[2]", "test_t.py::test_p[10]"]  # [1] exact-deselected, [10] kept
