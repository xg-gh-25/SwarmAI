"""Library Inbox to CDF v2 S6 tests.

A selected external text file is validated and submitted in full. No Inbox directory
or governed file is created; directories and protected paths remain rejected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import library_inbox


def test_single_file_is_submitted_in_full_without_copy(tmp_path, monkeypatch):
    knowledge = tmp_path / "Knowledge"; knowledge.mkdir()
    source = tmp_path / "report.md"; source.write_text("complete external report")
    calls = []
    monkeypatch.setattr("core.ingestion.sources.s6_external.extract_external",
                        lambda *a, **k: calls.append((a, k)) or [object()])
    result = library_inbox.copy_to_inbox(knowledge, source)
    assert len(result) == 1
    assert calls[0][0][1] == "complete external report"
    assert calls[0][1]["evidence_anchor"].startswith("doc:")
    assert not (knowledge / "Inbox").exists()


def test_directory_and_missing_source_are_rejected(tmp_path):
    knowledge = tmp_path / "Knowledge"; knowledge.mkdir()
    directory = tmp_path / "directory"; directory.mkdir()
    with pytest.raises(ValueError):
        library_inbox.copy_to_inbox(knowledge, directory)
    with pytest.raises(FileNotFoundError):
        library_inbox.copy_to_inbox(knowledge, tmp_path / "missing")


def test_system_path_source_refused(tmp_path):
    system_file = Path("/etc/hosts")
    if not system_file.exists():
        pytest.skip("/etc/hosts not present")
    knowledge = tmp_path / "Knowledge"; knowledge.mkdir()
    with pytest.raises(ValueError):
        library_inbox.copy_to_inbox(knowledge, system_file)
