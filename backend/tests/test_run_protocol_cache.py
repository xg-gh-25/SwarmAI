"""Tests Pipeline v1 immutable content-addressed caches.

Methodology: pure key inputs plus concurrent filesystem creates and poison reads.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from core.run_protocol.cache import (CacheError, cache_key, conflict_hashes,
                                     put_once, read, value_hash)


def _inputs(**extra):
    return {"kind": "test", "repo": "repo", "tree": "1" * 40,
            "pipeline_code_hash": "2" * 64, "protocol_version": 1,
            "rules_version": "r1", **extra}


def test_every_common_identity_changes_the_key():
    base = _inputs()
    key = cache_key(base)
    for field in base:
        changed = dict(base); changed[field] = str(changed[field]) + "x"
        assert cache_key(changed) != key


def test_create_replay_and_conflict_poisoning(tmp_path):
    key = cache_key(_inputs(nodeid="a"))
    assert put_once(tmp_path, "test", key, {"result": "pass"}) == "created"
    assert put_once(tmp_path, "test", key, {"result": "pass"}) == "replayed"
    assert put_once(tmp_path, "test", key, {"result": "fail"}) == "conflict"
    assert conflict_hashes(tmp_path, "test", key) == (
        value_hash({"result": "pass"}), value_hash({"result": "fail"}))
    with pytest.raises(CacheError, match="poisoned"):
        read(tmp_path, "test", key)


def test_concurrent_same_value_has_one_create(tmp_path):
    key = cache_key(_inputs(nodeid="a"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: put_once(tmp_path, "test", key, {"ok": True}), range(20)))
    assert results.count("created") == 1 and set(results) <= {"created", "replayed"}
    assert read(tmp_path, "test", key) == {"ok": True}


def test_concurrent_different_values_poison_instead_of_last_writer_wins(tmp_path):
    key = cache_key(_inputs(nodeid="race"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda value: put_once(tmp_path, "test", key, value),
                                ({"result": "pass"}, {"result": "fail"})))
    assert "created" in results and "conflict" in results
    with pytest.raises(CacheError, match="poisoned"):
        read(tmp_path, "test", key)


def test_missing_identity_or_unsafe_kind_fails_closed(tmp_path):
    with pytest.raises(CacheError, match="lacks"):
        cache_key({"kind": "test"})
    with pytest.raises(CacheError, match="unsafe"):
        put_once(tmp_path, "../test", "a" * 64, {})
    with pytest.raises(CacheError, match="unsafe"):
        read(tmp_path, "test", "short")


# ── B7: the code identity covers the whole protocol and both CLIs ───────────

def test_pipeline_code_hash_moves_with_any_protocol_or_cli_file(tmp_path):
    import shutil
    from pathlib import Path
    from core.run_protocol.cache import _pipeline_code_hash, pipeline_code_hash
    real = Path(__file__).resolve().parents[1]
    root = tmp_path / "backend"
    shutil.copytree(real / "core" / "run_protocol", root / "core" / "run_protocol",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "scripts").mkdir()
    for name in ("pipeline_test.py", "pipeline_v1.py"):
        shutil.copy(real / "scripts" / name, root / "scripts" / name)
    first = pipeline_code_hash(root)
    for path in (root / "scripts" / "pipeline_v1.py", root / "core" / "run_protocol" / "landing.py",
                 root / "scripts" / "pipeline_test.py"):
        path.write_text(path.read_text() + "\n# changed\n")
        _pipeline_code_hash.cache_clear()          # one process = one code identity
        assert pipeline_code_hash(root) != first, path
        first = pipeline_code_hash(root)


def test_a_conflicting_baseline_outcome_is_never_trusted(tmp_path):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    import pipeline_test as pt
    from core.run_protocol.cache import put_once
    key = pt.baseline_key("repo", "a" * 40, "py", "t.py::t")
    assert put_once(tmp_path / "v1", "baseline", key, "passed") == "created"
    assert pt.baseline_lookup(tmp_path, "repo", "a" * 40, "py", "t.py::t") == "passed"
    assert put_once(tmp_path / "v1", "baseline", key, "failed") == "conflict"
    assert pt.baseline_lookup(tmp_path, "repo", "a" * 40, "py", "t.py::t") is None


def test_a_baseline_conflict_is_a_signed_integrity_event(tmp_path):
    """Conformance F10: a baseline cache conflict reaches the run's event log like a
    test-result conflict does, so E1's cross-run contamination count can see it."""
    import sys
    from types import SimpleNamespace
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    import pipeline_test as pt
    recorded = []
    runner = SimpleNamespace(record_cache_conflict=lambda **kw: recorded.append(kw))
    ctx = SimpleNamespace(cache=tmp_path, v1=runner, warnings=[], python="py",
                          repo=tmp_path / "repo")
    pt._WORKTREE.clear()
    sha = "b" * 40
    assert pt._baseline_put(ctx, sha, {"t.py::t": "passed"}) == []
    assert pt._baseline_put(ctx, sha, {"t.py::t": "failed"}) == ["t.py::t"]
    assert len(recorded) == 1 and recorded[0]["kind"] == "baseline", recorded
    assert any("baseline cache key" in w for w in ctx.warnings)
