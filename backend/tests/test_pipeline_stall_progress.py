"""Stall rule for test continuations (design pipeline-test-speed.md §10).

- _stalled_calls: a call made progress when the unfinished test count fell by >= 10%
  OR one more file finished while the count fell; unchanged rules without file counts
- the run index records ``unfinished_files``; _continuation_file_series stays aligned
- end to end: 30 equal slow files, two finished per call (29 -> 27 -> 25 ...), is
  steady progress: no per-file split, no breaker, and the scope finishes
"""
import json

import pytest

from scripts import pipeline_test as pt
from tests.test_pipeline_test import RUN_ID, _evidence, _run, _write, rig  # noqa: F401


@pytest.mark.parametrize("tests, files, stalled", [
    ([29, 27, 25, 23], [29, 27, 25, 23], 0),     # large scope, two files per call: progress
    ([29, 27, 25, 23], None, 3),                 # no file counts: the share rule alone
    ([60, 58, 57], [3, 3, 3], 2),                # same files, a few tests: a real stall
    ([60, 58, 57], [3, 3, 2], 0),                # the last call finished a file
    ([50, 52], [5, 4], 1),                       # the count grew: never progress
    ([10, 0], [2, 0], 0),
])
def test_progress_is_a_finished_file_or_a_10pct_cut(tests, files, stalled):
    assert pt._stalled_calls(tests, files) == stalled


def test_misaligned_file_counts_fall_back_to_the_share_rule():
    assert pt._stalled_calls([100, 97, 95], [9, 8]) == 2


def test_the_run_index_records_unfinished_files(tmp_path):
    tdir = tmp_path / "tests"
    tdir.mkdir()
    rows = [{"kind": "run", "mode": "verify", "unfinished": 9, "unfinished_files": 3},
            {"kind": "run", "mode": "recheck", "auto_recheck_of": "verify", "unfinished": 7},
            {"kind": "overlay", "mode": "verify", "unfinished": 7},             # old row
            {"kind": "overlay", "mode": "verify", "unfinished": 5, "unfinished_files": 2}]
    (tdir / pt.RUN_INDEX).write_text(json.dumps(rows))
    assert pt._continuation_series(tmp_path, "verify") == [9, 7, 5]
    assert pt._continuation_file_series(tmp_path, "verify") == [3, None, 2]


def test_a_large_scope_that_keeps_finishing_files_is_not_split(rig, tmp_path, capsys):
    """30 equal files, about two finished per call: each cut is under 10% (29 -> 27 ...),
    so the share rule alone split per file from the 4th call and tripped the breaker
    later. Finished files count as progress: five calls, no split, no breaker."""
    slow = "import time\nfrom core.mod import add\n\n\ndef test_s():\n    time.sleep(1.0)\n    assert add(1, 1) == 2\n"
    repo = rig({f"backend/tests/test_many_{i:02d}.py": slow for i in range(30)},
               run_state={"files_touched": ["backend/core/mod.py"]})
    _write(repo, {"backend/core/mod.py": "def add(a, b):\n    return b + a\n"})
    outs = [_run(capsys, "--run-id", RUN_ID, "--mode", "verify", "--deadline", "4")[0] for _ in range(5)]
    idx = json.loads((tmp_path / "ws" / "Projects" / "P1" / ".artifacts" / "runs" / RUN_ID
                      / "evidence" / "tests" / pt.RUN_INDEX).read_text())
    series = pt._continuation_series(tmp_path / "ws" / "Projects" / "P1" / ".artifacts" / "runs" / RUN_ID / "evidence", "verify")
    assert len(series) >= 4 and series[-1] < series[0], series
    assert any(isinstance(e.get("unfinished_files"), int) for e in idx)
    assert all(o.get("continue") for o in outs), outs[-1]       # still going, never stopped
    assert not any("circuit_breaker" in o for o in outs), outs[-1]
    tdir = tmp_path / "ws" / "Projects" / "P1" / ".artifacts" / "runs" / RUN_ID / "evidence" / "tests"
    split = [p.name for p in tdir.glob("*.json") if "split per file" in p.read_text()]
    assert not split, (series, split)
