"""The built-in dangerous command patterns are a floor (design
docs/design/2026-11-04-kirocrew-borrow.md §3 A1).

Questions: does a file that lists no patterns (or a malformed one) still load every
built-in pattern, without raising; does the one-time migration keep only the user's
own patterns in the file, back the original up byte for byte first, and so let a
pattern the code later removes stop loading; does a backup that cannot be written
leave the file as it was.
"""
from __future__ import annotations

import json
import shutil

import pytest

import core.security_hooks as sh


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "get_app_data_dir", lambda: tmp_path)
    return tmp_path


def _file(app_dir):
    return app_dir / "dangerous_commands.json"


def test_empty_file_keeps_defaults(app_dir):
    f = _file(app_dir)
    f.write_text(json.dumps({"patterns": []}))
    assert sh.load_dangerous_patterns() == sh.DEFAULT_DANGEROUS_PATTERNS
    # the user's own patterns come after the built-in ones, once each
    f.write_text(json.dumps({"patterns": ["terraform destroy*", "terraform destroy*"]}))
    assert sh.load_dangerous_patterns() == [*sh.DEFAULT_DANGEROUS_PATTERNS, "terraform destroy*"]
    # a missing file is written holding no patterns, and every built-in one applies
    f.unlink()
    assert sh.load_dangerous_patterns() == sh.DEFAULT_DANGEROUS_PATTERNS
    assert json.loads(f.read_text()) == {"patterns": []}


DEEP = '{"patterns": ' + "[" * 100_000 + "]" * 100_000 + "}"   # too deep for json.loads


@pytest.mark.parametrize("raw", [json.dumps({"patterns": v}) for v in (None, "*", 3, ["sudo *", 7], {"a": "b"})]
                         + ["", "{not json", '["sudo *"]', DEEP],
                         ids=["null", "string", "number", "non-string", "mapping", "empty", "bad-json",
                              "list", "deep"])
def test_malformed_patterns_fall_back(app_dir, raw, caplog):
    f = _file(app_dir)
    f.write_text(raw)
    with caplog.at_level("WARNING", logger=sh.logger.name):
        assert sh.load_dangerous_patterns() == sh.DEFAULT_DANGEROUS_PATTERNS
    assert any("dangerous_commands.json" in r.getMessage() for r in caplog.records)
    assert f.read_text() == raw                                   # a bad file is not rewritten
    assert "*" not in sh.load_dangerous_patterns()                # "*" never became a pattern


def test_migration_keeps_extras_and_backup(app_dir, monkeypatch):
    f = _file(app_dir)
    seeded = {"patterns": [*sh.DEFAULT_DANGEROUS_PATTERNS, "rm -rf *", "terraform destroy*"]}
    raw = json.dumps(seeded, indent=4, sort_keys=True)            # not how we would write it
    f.write_text(raw)
    loaded = sh.load_dangerous_patterns()
    assert loaded == [*sh.DEFAULT_DANGEROUS_PATTERNS, "terraform destroy*"]
    assert json.loads(f.read_text()) == {"patterns": ["terraform destroy*"]}
    backups = sorted(app_dir.glob("dangerous_commands.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_text() == raw    # byte for byte
    # a second load changes nothing and takes no second backup
    assert sh.load_dangerous_patterns() == loaded
    assert len(list(app_dir.glob("dangerous_commands.json.bak-*"))) == 1
    # a built-in pattern the code drops later no longer loads
    dropped = sh.DEFAULT_DANGEROUS_PATTERNS[0]
    monkeypatch.setattr(sh, "DEFAULT_DANGEROUS_PATTERNS", sh.DEFAULT_DANGEROUS_PATTERNS[1:])
    assert dropped not in sh.load_dangerous_patterns()


def test_migration_writes_through_a_symlink(app_dir):
    target = app_dir / "dotfiles" / "dc.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"patterns": ["sudo *", "terraform destroy*"]}))
    _file(app_dir).symlink_to(target)
    assert sh.load_dangerous_patterns() == [*sh.DEFAULT_DANGEROUS_PATTERNS, "terraform destroy*"]
    assert _file(app_dir).is_symlink()
    assert json.loads(target.read_text()) == {"patterns": ["terraform destroy*"]}


def test_backup_failure_leaves_file(app_dir, monkeypatch):
    f = _file(app_dir)
    raw = json.dumps({"patterns": ["sudo *", "terraform destroy*"]})
    f.write_text(raw)

    def _fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", _fail)
    assert sh.load_dangerous_patterns() == [*sh.DEFAULT_DANGEROUS_PATTERNS, "terraform destroy*"]
    assert f.read_text() == raw
    assert list(app_dir.glob("dangerous_commands.json.bak-*")) == []
    # the copy is written but the rewrite fails: the file stays, and no copy piles up
    monkeypatch.undo()
    monkeypatch.setattr(sh, "get_app_data_dir", lambda: app_dir)
    monkeypatch.setattr(sh, "_write_patterns_file", _fail)
    for _ in range(2):
        assert sh.load_dangerous_patterns() == [*sh.DEFAULT_DANGEROUS_PATTERNS, "terraform destroy*"]
    assert f.read_text() == raw
    assert list(app_dir.glob("dangerous_commands.json.bak-*")) == []
