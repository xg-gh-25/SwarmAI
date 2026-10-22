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
