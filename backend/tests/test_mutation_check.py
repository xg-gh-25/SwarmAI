"""scripts/mutation_check: caught / missed / anchor, the file always comes back,
a red baseline mutates nothing, a leftover backup blocks until --restore, and a
SIGTERM mid-run restores the file (the recovery path runs, AGENT R28)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import mutation_check as mc

FIX = "def add(a, b):\n    return a + b\n"
# the "test": exit 1 unless add(2, 3) == 5
TEST = [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import calc; sys.exit(0 if calc.add(2, 3) == 5 else 1)"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "calc.py").write_text(FIX)
    return tmp_path


def _spec(tmp: Path, muts: list[dict]) -> Path:
    p = tmp / "spec.json"
    p.write_text(json.dumps(muts))
    return p


def test_a_mutation_the_test_sees_is_caught_and_one_it_does_not_is_missed(repo):
    out = mc.check([
        {"file": "calc.py", "name": "drop the fix", "old": "return a + b", "new": "return a - b"},
        {"file": "calc.py", "name": "harmless", "old": "def add(a, b):", "new": "def add(a, b):  # noop"},
    ], TEST, repo, timeout=60, baseline=True)
    assert [m["status"] for m in out["mutations"]] == ["caught", "missed"]
    assert out["ok"] is False and out["caught"] == 1 and out["missed"] == 1
    assert (repo / "calc.py").read_text() == FIX
    assert not (repo / "calc.py.mutation-backup").exists()
    assert Path(out["mutations"][0]["log"]).is_file()


def test_an_anchor_that_is_not_exactly_once_changes_nothing(repo):
    out = mc.check([{"file": "calc.py", "old": "a", "new": "z"}], TEST, repo, timeout=60, baseline=False)
    assert out["mutations"][0]["status"] == "anchor" and out["mutations"][0]["anchor_count"] > 1
    assert out["ok"] is False
    assert (repo / "calc.py").read_text() == FIX


def test_a_red_baseline_mutates_nothing(repo):
    (repo / "calc.py").write_text(FIX.replace("a + b", "a * b"))
    out = mc.check([{"file": "calc.py", "old": "a * b", "new": "a - b"}], TEST, repo, timeout=60, baseline=True)
    assert out["ok"] is False and "baseline" in out["error"]
    assert out["mutations"] == []


def test_a_leftover_backup_blocks_the_file_until_restore(repo):
    (repo / "calc.py.mutation-backup").write_text(FIX)
    (repo / "calc.py").write_text("broken by a killed run\n")
    out = mc.check([{"file": "calc.py", "old": "x", "new": "y"}], TEST, repo, timeout=60, baseline=False)
    assert out["ok"] is False and out["files"] == [str((repo / "calc.py").resolve())]
    assert (repo / "calc.py").read_text() == "broken by a killed run\n"
    assert mc.main(["--restore", "calc.py", "--cwd", str(repo)]) == 0
    assert (repo / "calc.py").read_text() == FIX
    assert not (repo / "calc.py.mutation-backup").exists()


def test_a_run_at_the_cap_is_killed_caught_and_the_file_restored(repo):
    # the hanging command records its pid: after the cap it must be gone, not orphaned
    hang = [sys.executable, "-c",
            "import os, time; open('hang.pid', 'w').write(str(os.getpid())); time.sleep(30)"]
    out = mc.check([{"file": "calc.py", "old": "return a + b", "new": "return a - b"}],
                   hang, repo, timeout=1, baseline=False)
    m = out["mutations"][0]
    assert m["status"] == "caught" and m["timeout"] is True and m["seconds"] < 15
    assert (repo / "calc.py").read_text() == FIX
    pid = int((repo / "hang.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGALRM])
def test_a_stop_signal_mid_run_restores_the_file(repo, tmp_path, signum):
    spec = _spec(tmp_path, [{"file": "calc.py", "old": "return a + b", "new": "return a - b"}])
    backend = Path(mc.__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.mutation_check", str(spec), "--cwd", str(repo), "--no-baseline",
         "--", sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=backend, stdout=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    while "a - b" not in (repo / "calc.py").read_text():
        assert time.monotonic() < deadline, "the mutation was never written"
        time.sleep(0.05)
    proc.send_signal(signum)
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 1 and "restored" in json.loads(out)["error"]
    assert (repo / "calc.py").read_text() == FIX
    assert not (repo / "calc.py.mutation-backup").exists()


def test_bad_use_exits_2(tmp_path):
    assert mc.main([str(_spec(tmp_path, []))]) == 2
    assert mc.main([str(_spec(tmp_path, [{"file": "f", "old": "a", "new": "a"}])), "--", "true"]) == 2
