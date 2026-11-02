"""The one-time metadata migration rewrites only entry metadata lines, loses nothing a
reader uses, never touches an archive, and is a no-op the second time."""
from __future__ import annotations

from pathlib import Path

from scripts import meta_shape_migrate as M

DOC = ("# TECH\n\n## Runtime Traps\n\n"
       "- [pitfall] **First** One. <!-- cand:cand-a.unit -->\n"
       "  <!-- ref:3 | last:2026-10-30 | decay:dormant | source:S7 | taint:external -->\n"
       "- [pitfall] **Second** Two.\n"
       "  <!-- ref:0 | last:none | decay:active | source:manual | valid_until:2026-10-20 | superseded_by:First -->\n"
       "- [pitfall] **Third** Three.\n"
       "  <!-- ref:0 | last:none | decay:active -->\n\n"
       "```\n  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n```\n")


def test_rewrite_keeps_every_field_a_reader_uses():
    after, n = M.rewrite(DOC)
    assert n == 2
    assert "  <!-- source:S7 | taint:external -->" in after
    assert "  <!-- source:manual | valid_until:2026-10-20 | superseded_by:First -->" in after
    assert "**Third** Three.\n  <!-- ref:0 | last:none | decay:active -->" in after  # only dead: kept
    assert "```\n  <!-- ref:0 | last:2026-01-01" in after    # not an entry's line: untouched
    assert M._entries(after) == M._entries(DOC)
    assert M.rewrite(after) == (after, 0)


def test_apply_writes_main_files_and_skips_archives(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    tech = ws / "Projects" / "P" / "2-understanding" / "TECH.md"
    arch = ws / "Projects" / "P" / "2-understanding" / "TECH-archive-2026-10.md"
    for p in (tech, arch):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DOC, encoding="utf-8")
    calls = []
    from core.ingestion import governed_edit, materializer
    monkeypatch.setattr(materializer, "is_governed", lambda p: True)
    monkeypatch.setattr(governed_edit, "revision_of", lambda p: "r")
    monkeypatch.setattr(governed_edit, "apply_user_edit",
                        lambda p, b, **k: (calls.append((p, k["actor"])), Path(p).write_bytes(b)))
    rc = M.main(["--workspace", str(ws), "--apply", "--backup-dir", str(tmp_path / "bk")])
    assert rc == 0 and [c[0] for c in calls] == [tech]
    assert "ref:3" not in tech.read_text() and arch.read_text() == DOC
    assert (tmp_path / "bk" / "Projects/P/2-understanding/TECH.md").read_text() == DOC


def test_a_fenced_example_and_an_archive_directory_are_left_alone(tmp_path):
    fenced = "```\n- [guideline] **Ex** e\n  <!-- ref:0 | last:none | decay:active | source:auto -->\n```\n"
    assert M.rewrite(fenced) == (fenced, 0)
    ws = tmp_path / "ws"
    snap = ws / "Projects" / "P" / "2-understanding" / "knowledge" / "Archived-notes" / "TECH.md"
    snap2 = ws / "Projects" / "P" / "2-understanding" / "archived-improvement-snapshot.md"
    for p in (snap, snap2):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DOC, encoding="utf-8")
    assert M.candidates(ws) == []


def test_a_file_outside_the_governed_corpus_is_replaced_directly(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ref = ws / "Projects" / "P" / "2-understanding" / "knowledge" / "notes.md"
    ref.parent.mkdir(parents=True)
    ref.write_text(DOC, encoding="utf-8")
    from core.ingestion import governed_edit, materializer
    monkeypatch.setattr(materializer, "is_governed", lambda p: False)
    monkeypatch.setattr(governed_edit, "apply_user_edit",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("not governed")))
    assert M.main(["--workspace", str(ws), "--apply", "--backup-dir", str(tmp_path / "bk")]) == 0
    assert "  <!-- source:S7 | taint:external -->" in ref.read_text()
    assert list(ref.parent.iterdir()) == [ref]                      # no temp file left


def test_a_rewrite_that_would_lose_an_entry_is_refused(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    tech = ws / "Projects" / "P" / "2-understanding" / "TECH.md"
    tech.parent.mkdir(parents=True)
    tech.write_text(DOC, encoding="utf-8")
    monkeypatch.setattr(M, "rewrite", lambda text, **k: (text.replace("- [pitfall] **Second** Two.\n", ""), 1))
    called = []
    from core.ingestion import governed_edit
    monkeypatch.setattr(governed_edit, "apply_user_edit", lambda *a, **k: called.append(a))
    assert M.main(["--workspace", str(ws), "--apply", "--backup-dir", str(tmp_path / "bk")]) == 0
    assert called == [] and tech.read_text() == DOC
