"""Tests for scripts/consumers_gate.py — the function-level, diff-bound readers gate.

Each test builds a throwaway git repo: a base commit, then a working-tree change, and
asks the gate whether every function that matches a declared pattern was either changed
by the diff or declared unaffected with a reason. The core fixture is the defect class
the gate exists for: two readers of the same contract in ONE touched file, only one of
them migrated.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import consumers_gate as cg  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


READERS = '''\
import json


def read_index(path):
    return json.loads(open(path).read())


def count_runs(path):
    data = read_index(path)
    return len(data)


def breaker(path):
    data = read_index(path)
    return bool(data)
'''


@pytest.fixture()
def repo(tmp_path: Path) -> "tuple[Path, str]":
    r = tmp_path / "repo"
    (r / "backend").mkdir(parents=True)
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "backend" / "readers.py").write_text(READERS)
    (r / "README.md").write_text("call read_index(path) to load it\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    base = _git(r, "rev-parse", "HEAD").strip()
    return r, base


def _migrate_one(r: Path) -> None:
    """Change count_runs only; breaker in the same file still reads the old way."""
    src = (r / "backend" / "readers.py").read_text()
    src = src.replace("    data = read_index(path)\n    return len(data)",
                      "    data = read_index(path) or []\n    return len(data)")
    (r / "backend" / "readers.py").write_text(src)


def _consumer(**over) -> dict:
    c = {"id": "K1", "contract": "read_index result may be unusable",
         "pattern": r"read_index\(", "paths": ["backend"], "readers": []}
    c.update(over)
    return c


def test_unmigrated_reader_in_a_touched_file_is_named(repo):
    r, base = repo
    _migrate_one(r)
    errors, _w = cg.check([_consumer()], [], repo=r, base=base, new_files=[])
    joined = "\n".join(errors)
    assert "backend/readers.py::breaker" in joined
    assert "count_runs" not in joined  # changed by the diff: covered


def test_declared_unaffected_with_reason_passes(repo):
    r, base = repo
    _migrate_one(r)
    readers = [{"at": "backend/readers.py::breaker", "status": "unaffected",
                "reason": "only tests truthiness, an empty list is falsy too"},
               {"at": "backend/readers.py::read_index", "status": "unaffected",
                "reason": "the definition itself, not a reader of the result"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert errors == []


def test_unaffected_without_reason_is_refused(repo):
    r, base = repo
    _migrate_one(r)
    readers = [{"at": "backend/readers.py::breaker", "status": "unaffected", "reason": ""},
               {"at": "backend/readers.py::read_index", "status": "unaffected",
                "reason": "the definition itself, not a reader"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert any("breaker" in e for e in errors)


def test_migrated_but_unchanged_reader_is_refused(repo):
    """I2: a declaration cannot stand in for the edit."""
    r, base = repo
    _migrate_one(r)
    readers = [{"at": "backend/readers.py::breaker", "status": "migrated"},
               {"at": "backend/readers.py::read_index", "status": "unaffected",
                "reason": "the definition itself, not a reader"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert any("breaker" in e and "migrated" in e for e in errors)


def test_extra_readers_from_run_update_count(repo):
    """F4: a reader declared after PLAN (run-update --add-consumer-reader) is seen."""
    r, base = repo
    _migrate_one(r)
    extra = [{"id": "K1", "at": "backend/readers.py::breaker", "status": "unaffected",
              "reason": "only tests truthiness, empty list is falsy"},
             {"id": "K1", "at": "backend/readers.py::read_index", "status": "unaffected",
              "reason": "the definition itself, not a reader"}]
    errors, _w = cg.check([_consumer()], extra, repo=r, base=base, new_files=[])
    assert errors == []


def test_absolute_reader_paths_are_normalized(repo):
    """I8 / F8: an absolute path of the repo (or of another root) matches the git path."""
    r, base = repo
    _migrate_one(r)
    other_root = Path("/somewhere/main-checkout")
    readers = [{"at": f"{r}/backend/readers.py::breaker", "status": "unaffected",
                "reason": "only tests truthiness, empty list is falsy"},
               {"at": f"{other_root}/backend/readers.py::read_index", "status": "unaffected",
                "reason": "the definition itself, not a reader"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[],
                          roots=[other_root])
    assert errors == []


def test_reader_path_outside_the_repo_is_refused(repo):
    r, base = repo
    _migrate_one(r)
    readers = [{"at": "/etc/passwd::x", "status": "unaffected", "reason": "not ours at all here"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert any("/etc/passwd" in e and "outside" in e for e in errors)


def test_non_python_hit_is_a_file_unit(repo):
    r, base = repo
    _migrate_one(r)
    c = _consumer(paths=["."], readers=[
        {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "truthiness only, fine"},
        {"at": "backend/readers.py::read_index", "status": "unaffected", "reason": "the definition itself"}])
    errors, _w = cg.check([c], [], repo=r, base=base, new_files=[])
    assert any("README.md" in e for e in errors)
    c["readers"].append({"at": "README.md", "status": "unaffected", "reason": "prose, no code reads it"})
    errors, _w = cg.check([c], [], repo=r, base=base, new_files=[])
    assert errors == []


def test_invalid_pattern_is_refused_by_id(repo):
    """F1: a pattern git grep rejects is a refusal naming the consumer."""
    r, base = repo
    errors, _w = cg.check([_consumer(pattern="([")], [], repo=r, base=base, new_files=[])
    assert errors and "K1" in errors[0] and "pattern" in errors[0]


def test_option_like_pattern_is_data_not_an_option(repo):
    """F6 / I3: a pattern starting with '-' is passed after -e, never parsed as a flag."""
    r, base = repo
    (r / "backend" / "flags.py").write_text("def f():\n    return '--output=/tmp/x'\n")
    _git(r, "add", "-A")
    errors, _w = cg.check([_consumer(pattern="--output=/tmp/x")], [], repo=r, base=base,
                          new_files=[])
    # flags.py::f is new (changed) so it is covered: the pattern was searched as text,
    # git did not take it as its --output option
    assert errors == []


def test_pattern_with_no_hit_now_or_at_base_is_refused(repo):
    """A typo'd pattern checks nothing: fail closed."""
    r, base = repo
    errors, _w = cg.check([_consumer(pattern=r"reed_indx\(")], [], repo=r, base=base, new_files=[])
    assert any("matches nothing" in e for e in errors)


def test_every_reader_removed_passes(repo):
    """F7: hits at the base, none now — every reader was removed."""
    r, base = repo
    (r / "backend" / "readers.py").write_text("def other():\n    return 1\n")
    errors, _w = cg.check([_consumer()], [], repo=r, base=base, new_files=[])
    assert errors == []


def test_deep_path_is_refused(repo):
    """Anti-circularity (check_migration_class R-A): search a tree root, not the author's files."""
    r, base = repo
    errors, _w = cg.check([_consumer(paths=["backend/readers.py"])], [], repo=r, base=base,
                          new_files=[])
    assert any("tree root" in e for e in errors)


def test_dotdot_path_is_refused(repo):
    r, base = repo
    errors, _w = cg.check([_consumer(paths=[".."])], [], repo=r, base=base, new_files=[])
    assert any("tree root" in e or "outside" in e for e in errors)


def test_module_level_and_nested_units(repo):
    """F10: module-level code maps to <module>; nested defs map to Outer.inner."""
    r, base = repo
    (r / "backend" / "more.py").write_text(
        "from readers import read_index\nX = read_index('a')\n\n"
        "class Box:\n    def get(self):\n        def inner():\n            return read_index('b')\n"
        "        return inner()\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "more")
    base2 = _git(r, "rev-parse", "HEAD").strip()
    _migrate_one(r)
    errors, _w = cg.check([_consumer()], [], repo=r, base=base2, new_files=[])
    joined = "\n".join(errors)
    assert "backend/more.py::<module>" in joined
    assert "backend/more.py::Box.get.inner" in joined


def test_unparsable_python_is_one_file_unit(repo):
    """F3: a .py that does not parse maps to the file."""
    r, base = repo
    (r / "backend" / "broken.py").write_text("def x(:\n    read_index(1)\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "broken")
    base2 = _git(r, "rev-parse", "HEAD").strip()
    _migrate_one(r)
    errors, _w = cg.check([_consumer()], [], repo=r, base=base2, new_files=[])
    assert any("backend/broken.py::<file>" in e for e in errors)


def test_untracked_new_file_counts_as_changed(repo):
    r, base = repo
    (r / "backend" / "fresh.py").write_text("def g():\n    return read_index('z')\n")
    readers = [{"at": "backend/readers.py::count_runs", "status": "unaffected", "reason": "fine as is now"},
               {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "fine as is now"},
               {"at": "backend/readers.py::read_index", "status": "unaffected", "reason": "the definition"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base,
                          new_files=["backend/fresh.py"])
    assert errors == []


def test_unresolvable_base_is_a_warning_not_a_pass(repo):
    """F2: the gate cannot see the diff — it says so; it never reports a silent pass."""
    r, _base = repo
    errors, warnings = cg.check([_consumer()], [], repo=r, base="0" * 40, new_files=[])
    assert errors == [] and any("not verified" in w.lower() for w in warnings)


def test_no_consumers_is_a_noop(repo):
    r, base = repo
    assert cg.check([], [], repo=r, base=base, new_files=[]) == ([], [])


# ── CLI wiring (artifact_cli): BUILD stage-complete, Gate-2 fix paths, run-update ──────

from scripts import artifact_cli as cli  # noqa: E402

NEW = "2099-01-01T00:00:00+00:00"
OLD = "2026-10-20T06:00:00+00:00"


def _plan_with(consumer: dict) -> dict:
    return {"implementation_constraints": {"consumers": [consumer]}}


def _wire(monkeypatch, r: Path, base: str, consumer: dict) -> None:
    monkeypatch.setattr(cli, "_stage_artifact",
                        lambda project, rs, stage: _plan_with(consumer) if stage == "plan" else None)
    monkeypatch.setattr(cli, "_consumers_repo",
                        lambda run_id, rs: {"repo": r, "base": base, "roots": [], "new_files": []})


def test_the_diff_is_measured_from_the_given_base(repo):
    """F9: the run's changed units are the diff from ITS base. A commit after the base
    is part of the run's change; with the base moved past it, it is not."""
    r, base = repo
    src = (r / "backend" / "readers.py").read_text()
    (r / "backend" / "readers.py").write_text(src.replace("return bool(data)", "return data != []"))
    _git(r, "commit", "-qam", "edit breaker")
    head = _git(r, "rev-parse", "HEAD").strip()
    _migrate_one(r)
    readers = [{"at": "backend/readers.py::read_index", "status": "unaffected", "reason": "the definition"}]
    errs_from_base, _ = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    errs_from_head, _ = cg.check([_consumer(readers=readers)], [], repo=r, base=head, new_files=[])
    assert errs_from_base == []
    assert any("breaker" in e for e in errs_from_head)


# ── REVIEW fix round: one spec, these are its break tests ────────────────────────

def test_a_migrated_reader_in_a_deleted_file_counts_as_changed(repo):
    r, base = repo
    (r / "backend" / "old.py").write_text("from readers import read_index\n\ndef legacy():\n    return read_index('x')\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "old reader")
    base2 = _git(r, "rev-parse", "HEAD").strip()
    _migrate_one(r)
    (r / "backend" / "old.py").unlink()
    readers = [{"at": "backend/old.py::legacy", "status": "migrated"},
               {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "truthiness only, fine"},
               {"at": "backend/readers.py::read_index", "status": "unaffected", "reason": "the definition"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base2, new_files=[])
    assert errors == []


def test_root_path_is_one_rule_for_plan_and_gate():
    import design_spec as ds
    assert cg.root_path("./backend") == "backend" and cg.root_path("backend/x") is None
    plan = {"implementation_constraints": {"consumers": [_consumer(paths=["./backend"],
            readers=[{"at": "backend/a.py::f", "status": "migrated"}])]}}
    assert ds.constraints_errors(plan, "full") == []


# ── run_53674c75: one declaration rule, exact-grain credit, literal paths ─────────────

def _fixed_readers() -> list:
    return [{"at": "backend/readers.py::read_index", "status": "unaffected",
             "reason": "the definition itself, not a reader"}]


def test_a_later_declaration_replaces_an_earlier_one(repo):
    """I1 / AC1: a PLAN 'migrated' corrected after PLAN by 'unaffected' with a reason passes."""
    r, base = repo
    _migrate_one(r)
    c = _consumer(readers=_fixed_readers() + [{"at": "backend/readers.py::breaker", "status": "migrated"}])
    errors, _w = cg.check([c], [], repo=r, base=base, new_files=[])
    assert any("breaker" in e and "declared migrated" in e for e in errors)
    extra = [{"id": "K1", "at": "backend/readers.py::breaker", "status": "unaffected",
              "reason": "only tests truthiness, empty list is falsy"}]
    errors, _w = cg.check([c], extra, repo=r, base=base, new_files=[])
    assert errors == []


def test_a_later_unaffected_without_a_reason_does_not_silence_a_plan_reader(repo):
    """The fail-open direction of I1: an override still needs its reason."""
    r, base = repo
    _migrate_one(r)
    c = _consumer(readers=_fixed_readers() + [{"at": "backend/readers.py::breaker", "status": "migrated"}])
    extra = [{"id": "K1", "at": "backend/readers.py::breaker", "status": "unaffected", "reason": "short"}]
    errors, _w = cg.check([c], extra, repo=r, base=base, new_files=[])
    assert any("breaker" in e and "without a reason" in e for e in errors)


def test_migrated_function_in_a_touched_file_must_itself_change(repo):
    """I2 / AC5: a change elsewhere in the file credits no other unit of it."""
    r, base = repo
    _migrate_one(r)
    readers = _fixed_readers() + [
        {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "truthiness only, fine"},
        {"at": "backend/readers.py::<module>", "status": "migrated"},
        {"at": "backend/readers.py::Missing.method", "status": "migrated"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert any("readers.py::<module> is declared migrated" in e for e in errors)
    assert any("readers.py::Missing.method is declared migrated" in e for e in errors)
    bare = readers[:2] + [{"at": "backend/readers.py", "status": "migrated"}]
    errors, _w = cg.check([_consumer(readers=bare)], [], repo=r, base=base, new_files=[])
    assert any("name the function or backend/readers.py::<module>" in e for e in errors)
    readers[-2:] = [{"at": "backend/readers.py::count_runs", "status": "migrated"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert errors == []


def test_a_bare_python_path_covers_only_the_file_unit(repo):
    """AC5: a bare .py path is path::<file>; module-level code must be named ::<module>."""
    r, _base = repo
    (r / "backend" / "conf.py").write_text("DEFAULT = read_index('x')\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "module-level reader")
    base2 = _git(r, "rev-parse", "HEAD").strip()
    _migrate_one(r)
    readers = _fixed_readers() + [
        {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "truthiness only, fine"},
        {"at": "backend/conf.py", "status": "unaffected", "reason": "loads it at import, untouched"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base2, new_files=[])
    assert any("backend/conf.py::<module> matches" in e and "name it backend/conf.py::<module>" in e
               for e in errors)
    readers[-1]["at"] = "backend/conf.py::<module>"
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base2, new_files=[])
    assert errors == []


def test_a_touched_non_python_file_still_credits_its_file_unit(repo):
    """Gate-1 SSA: removing file-level credit for .py units keeps <file> for other files."""
    r, base = repo
    _migrate_one(r)
    (r / "README.md").write_text("call read_index(path) or [] to load it\n")
    readers = _fixed_readers() + [
        {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "truthiness only, fine"},
        {"at": "README.md", "status": "migrated"}]
    errors, _w = cg.check([_consumer(paths=["."], readers=readers)], [], repo=r, base=base, new_files=[])
    assert errors == []


def test_pathspec_magic_cannot_exclude_files(repo):
    """I3 / AC6: a declared path is a literal tree root, never a pathspec."""
    for bad in (":!x", ":(exclude)*", ":(icase)BACKEND", "*", "[ab]", "back?end", "!x", "a\\b"):
        assert cg.root_path(bad) is None, bad
    assert cg.root_path(".") == "." and cg.root_path("backend/") == "backend"
    r, base = repo
    _migrate_one(r)
    c = _consumer(paths=["backend", ":!*readers.py"])
    errors, _w = cg.check([c], [], repo=r, base=base, new_files=[])
    assert any("paths must be tree roots" in e for e in errors)
    # the second layer: git reads every path literally, so '*' names a file called '*'
    assert cg._hits(r, r"read_index\(", ["*"]) == {}
    assert cg._hits(r, r"read_index\(", ["backend"])


def test_a_new_file_gets_the_same_credit_committed_or_not(repo):
    """REVIEW r2: a new file is every unit of it, whether untracked, staged or committed."""
    r, base = repo
    _migrate_one(r)
    (r / "backend" / "fresh.py").write_text("def new_reader(p):\n    return read_index(p)\n")
    readers = _fixed_readers() + [
        {"at": "backend/readers.py::breaker", "status": "unaffected", "reason": "truthiness only, fine"},
        {"at": "backend/fresh.py", "status": "migrated"}]
    untracked, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base,
                             new_files=["backend/fresh.py"])
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "cycle commit")
    committed, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert untracked == committed == []


def _drop_breaker(r: Path) -> None:
    """Remove breaker (the last function) from readers.py; the file stays."""
    src = (r / "backend" / "readers.py").read_text()
    src = src.replace("\n\ndef breaker(path):\n    data = read_index(path)\n    return bool(data)\n", "\n")
    (r / "backend" / "readers.py").write_text(src)


def test_a_reader_removed_from_a_kept_file_counts_as_changed(repo):
    """Gate-2 (I2): deleting a reader is a migration; its own base lines credit it."""
    r, base = repo
    _migrate_one(r)
    _drop_breaker(r)
    readers = _fixed_readers() + [
        {"at": "backend/readers.py::count_runs", "status": "migrated"},
        {"at": "backend/readers.py::breaker", "status": "migrated"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert errors == []


def test_removing_a_function_does_not_credit_its_neighbour(repo):
    """Gate-2 (I2): a pure deletion credits the removed unit only, never the line before it."""
    r, base = repo
    _drop_breaker(r)   # count_runs is untouched
    readers = _fixed_readers() + [
        {"at": "backend/readers.py::count_runs", "status": "migrated"},
        {"at": "backend/readers.py::breaker", "status": "migrated"}]
    errors, _w = cg.check([_consumer(readers=readers)], [], repo=r, base=base, new_files=[])
    assert any("readers.py::count_runs is declared migrated" in e for e in errors), errors
    assert not any("readers.py::breaker" in e for e in errors), errors
