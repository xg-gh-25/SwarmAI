"""Tests for core/contract_readers.py — the one rule for declared readers of a contract.

The consumers gate and DESIGN_SPEC.md both read the declarations through ``resolve``:
one entry per (consumer id, canonical unit), the latest declaration (PLAN readers, then
run.json ``consumer_readers`` in order) decides, and every replaced declaration stays
visible. ``run_roots`` is the one source of the roots that make an absolute path of the
worktree or the main checkout repo-relative.
"""
from __future__ import annotations

from pathlib import Path

from core import contract_readers as cr


def test_bare_path_and_file_unit_are_one_unit():
    assert cr.unit("backend/a.md", []) == "backend/a.md::<file>"
    assert cr.unit("backend/a.md::<file>", []) == "backend/a.md::<file>"
    assert cr.unit("./backend/a.py::f ", []) == "backend/a.py::f"
    assert cr.unit("backend/a.py::<module>", []) == "backend/a.py::<module>"


def test_absolute_paths_of_any_root_are_made_relative(tmp_path):
    wt, main = tmp_path / "wt", tmp_path / "main"
    wt.mkdir()
    main.mkdir()
    assert cr.unit(f"{wt}/backend/a.py::f", [wt, main]) == "backend/a.py::f"
    assert cr.unit(f"{main}/backend/a.py::f", [str(wt), str(main)]) == "backend/a.py::f"
    assert cr.unit("/etc/passwd::x", [wt, main]) is None
    assert cr.unit("../outside.py::x", [wt]) is None
    assert cr.unit("", [wt]) is None


def test_a_later_declaration_replaces_an_earlier_one():
    """I1: the latest declaration per unit decides; the replaced one is kept."""
    consumers = [{"id": "K1", "readers": [{"at": "a.py::f", "status": "migrated"},
                                          {"at": "b.py::g", "status": "migrated"}]}]
    extra = [{"id": "K1", "at": "a.py::f", "status": "unaffected", "reason": "only logs the value"},
             {"id": "K2", "at": "a.py::f", "status": "unaffected", "reason": "another contract"}]
    by_id, rejected = cr.resolve(consumers, extra, [])
    assert rejected == []
    k1 = {e["unit"]: e for e in by_id["K1"]}
    assert k1["a.py::f"]["status"] == "unaffected" and k1["a.py::f"]["source"] == "run"
    assert k1["a.py::f"]["replaced"] == [{"status": "migrated", "reason": "", "source": "plan"}]
    assert k1["b.py::g"]["replaced"] == []
    assert "K2" not in by_id   # not a PLAN consumer: nothing to judge


def test_one_unit_by_two_spellings_is_one_entry(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    consumers = [{"id": "K1", "readers": [{"at": "README.md", "status": "migrated"}]}]
    extra = [{"id": "K1", "at": f"{wt}/README.md::<file>", "status": "unaffected",
              "reason": "prose, nothing reads it"}]
    by_id, _e = cr.resolve(consumers, extra, [wt])
    assert [(e["unit"], e["status"]) for e in by_id["K1"]] == [("README.md::<file>", "unaffected")]


def test_order_of_the_store_decides():
    consumers = [{"id": "K1", "readers": []}]
    a = {"id": "K1", "at": "a.py::f", "status": "unaffected", "reason": "first reason given"}
    b = {"id": "K1", "at": "a.py::f", "status": "migrated"}
    by_id, _e = cr.resolve(consumers, [a, b, a], [])
    (entry,) = by_id["K1"]
    assert entry["status"] == "unaffected"
    assert [r["status"] for r in entry["replaced"]] == ["unaffected", "migrated"]


def test_invalid_declarations_are_errors_and_replace_nothing():
    consumers = [{"id": "K1", "readers": [{"at": "a.py::f", "status": "migrated"}]}]
    extra = [{"id": "K1", "at": "a.py::f", "status": "done"},
             {"id": "K1", "at": "/etc/passwd::x", "status": "unaffected", "reason": "outside the repo"}]
    by_id, rejected = cr.resolve(consumers, extra, [])
    assert by_id["K1"][0]["status"] == "migrated"
    errors = [r["error"] for r in rejected]
    assert any("status 'done'" in e for e in errors)
    assert any("/etc/passwd" in e and "outside" in e for e in errors)
    assert [(r["id"], r["at"], r["status"]) for r in rejected] == [
        ("K1", "a.py::f", "done"), ("K1", "/etc/passwd::x", "unaffected")]


def test_run_roots_names_only_the_gated_repo(monkeypatch, tmp_path):
    """Gate-2 (I5): the roots are the gated repo's only, so a path in another repo of the
    run is never made relative to it."""
    from core import run_worktree as rw
    monkeypatch.setattr(rw, "wt_root", lambda: tmp_path / "wts")
    wts = tmp_path / "wts" / "run_abc"
    state = {"id": "run_abc", "worktrees": [
        {"repo_name": "swarmai", "main": "/m/swarmai", "state": "released"},
        {"repo_name": "other", "main": "/m/other", "state": "active"},
        {"repo_name": "other", "main": "/m/other2", "state": "released"},
        {"repo_name": "../bad", "main": "/m/x"}]}
    assert cr.gated_entry(state)["main"] == "/m/other"
    roots = cr.run_roots(state)
    assert roots == [str(wts / "other"), "/m/other", "/m/other2"]
    assert cr.unit("/m/swarmai/backend/a.py::f", roots) is None   # another repo: no alias
    assert cr.unit("/m/other/backend/a.py::f", roots) == "backend/a.py::f"
    # after release: the repo every entry names, else nothing
    released = {"id": "run_abc", "worktrees": [
        {"repo_name": "swarmai", "main": "/m/swarmai", "state": "released"}]}
    assert cr.gated_entry(released) is None
    assert cr.run_roots(released) == [str(wts / "swarmai"), "/m/swarmai"]
    mixed = {"id": "run_abc", "worktrees": [
        {"repo_name": "swarmai", "main": "/m/swarmai", "state": "released"},
        {"repo_name": "other", "main": "/m/other", "state": "released"}]}
    assert cr.run_roots(mixed) == []
    unsafe = {"id": "run_abc", "worktrees": [{"repo_name": "../bad", "main": "/m/x", "state": "active"}]}
    assert cr.run_roots(unsafe) == []   # an unsafe repo name is not trusted at all
    assert cr.run_roots({}) == [] and cr.run_roots({"id": "run_x", "worktrees": "x"}) == []

def test_rel_resolves_symlinked_roots(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert cr.rel(f"{real}/a.py", [Path(link)]) == "a.py"
