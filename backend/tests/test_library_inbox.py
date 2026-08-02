"""Cycle 6 — Inbox landing zone (single-file drops into Knowledge/Inbox/).

The Inbox is a regularized staging area: a SINGLE external file MAY be copied in
(small, explicit, no drift risk — the design's one exception to never-copy; an
external DIRECTORY is always MOUNTED, never copied). Re-filing happens later, by
the user or chat. The Inbox appears as its own native category with a count.

Tested at the library_inbox module level (copy logic + path safety), not the HTTP
layer — the endpoint is a thin wrapper.

Properties:
  1. ensure_inbox creates Knowledge/Inbox/ (idempotent)
  2. copy_to_inbox copies a single file in, returns the landed path
  3. NEGATIVE: a duplicate name gets a non-clobbering unique suffix
  4. NEGATIVE: a directory source is rejected (dirs are mounted, not copied)
  5. NEGATIVE: a missing source is rejected
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.library_inbox import ensure_inbox, copy_to_inbox


def test_ensure_inbox_creates_dir(tmp_path: Path) -> None:
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    inbox = ensure_inbox(kdir)
    assert inbox.is_dir()
    assert inbox.name == "Inbox"
    # idempotent
    assert ensure_inbox(kdir) == inbox


def test_copy_single_file_in(tmp_path: Path) -> None:
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    src = tmp_path / "report.md"; src.write_text("hello")
    landed = copy_to_inbox(kdir, src)
    assert landed.exists()
    assert landed.parent.name == "Inbox"
    assert landed.read_text() == "hello"


def test_duplicate_name_gets_unique_suffix(tmp_path: Path) -> None:
    """NEGATIVE: a second file of the same name does NOT clobber the first."""
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    a = tmp_path / "a" ; a.mkdir(); (a / "note.md").write_text("first")
    b = tmp_path / "b" ; b.mkdir(); (b / "note.md").write_text("second")
    l1 = copy_to_inbox(kdir, a / "note.md")
    l2 = copy_to_inbox(kdir, b / "note.md")
    assert l1 != l2
    assert l1.read_text() == "first"
    assert l2.read_text() == "second"  # not clobbered


def test_directory_source_rejected(tmp_path: Path) -> None:
    """NEGATIVE: a directory is MOUNTED, never copied into Inbox."""
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    d = tmp_path / "adir"; d.mkdir()
    with pytest.raises(ValueError):
        copy_to_inbox(kdir, d)


def test_missing_source_rejected(tmp_path: Path) -> None:
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    with pytest.raises((ValueError, FileNotFoundError)):
        copy_to_inbox(kdir, tmp_path / "nope.md")


def test_system_path_source_refused(tmp_path: Path) -> None:
    """SECURITY (adversarial): a host system file outside the user home must NOT be
    copyable into the workspace (system-path exfiltration guard). Forces the escape:
    /etc/hosts exists on every unix host and is outside ~."""
    kdir = tmp_path / "Knowledge"; kdir.mkdir()
    system_file = Path("/etc/hosts")
    if not system_file.exists():  # pragma: no cover — non-unix
        pytest.skip("/etc/hosts not present")
    with pytest.raises(ValueError):
        copy_to_inbox(kdir, system_file)
