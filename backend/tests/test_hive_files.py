"""Files a Hive run writes come back into the run's Brain (core/hive_files; design
docs/design/2026-10-30-hive-run-files-back.md).

Questions: (1) does a turn that lists a file seal it as a ``file`` entry with its path and
bytes, and nothing for a link, a path under ``Hive/``, a secret path, a file outside the
workspace or over the cap; (2) does the host take it, check it and write it under the
run's Brain (General when the row names none), and refuse a bad path or a linked
destination with nothing written outside; (3) does the General Brain's sync leave
``deliverables/`` out; (4) does the result give each returned file its local path.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tests.test_hive_records import DID, ROW, SID, _turn_rows, host, hive_ws  # noqa: F401 — fixtures


def _rows_listing(*abs_paths: str, i: str = "a") -> list[dict]:
    rows = _turn_rows("done", i=i)
    rows[-1]["content"].insert(0, {"type": "turn_deliverables", "files": [
        {"path": p, "absolutePath": p, "kind": "content", "operation": "written"} for p in abs_paths]})
    return rows


def _write(p, text="report body"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)


def _files(ws):
    from core import hive_records as hr
    return [e for e in hr.entries(ws, SID)["entries"] if e["kind"] == "file"]


def test_a_listed_report_is_sealed_with_its_path_and_bytes(hive_ws):
    from core import hive_records as hr
    rep = _write(hive_ws / "Knowledge" / "Reports" / "r.md", "# the report")
    hr.seal_turn(hive_ws, SID, _rows_listing(rep))
    (e,) = _files(hive_ws)
    assert e["path"] == "Knowledge/Reports/r.md" and e["size"] == len("# the report")
    fd, size = hr.open_entry(hive_ws, SID, e["seq"])
    try:
        assert os.read(fd, size) == b"# the report"
    finally:
        os.close(fd)


def test_a_pipeline_runs_report_comes_back_when_the_run_ends(hive_ws):
    """dispatch-center §3: the run engine writes REPORT.md, so no turn lists it; the
    turn whose rows end the run (a pipeline_summary) seals it, once, never another run's."""
    from core import hive_records as hr
    rid = "run_0123abcd"
    _write(hive_ws / "Projects" / "Lib" / ".artifacts" / "runs" / rid / "REPORT.md", "# run report")
    _write(hive_ws / "Projects" / "Lib" / ".artifacts" / "runs" / "run_ffffffff" / "REPORT.md", "# other")
    listed = _write(hive_ws / "Outputs" / "so_aaaaaaaaaaaa" / "notes.md", "notes")
    rows = _rows_listing(listed)
    rows[-1]["content"].append({"type": "pipeline_summary", "runId": rid, "status": "completed"})
    rows[-1]["content"].append({"type": "pipeline_stage", "runId": "run_ffffffff", "stage": "evaluate"})  # not ended
    hr.seal_turn(hive_ws, SID, rows)
    assert sorted(e["path"] for e in _files(hive_ws)) == [
        f"Outputs/so_aaaaaaaaaaaa/notes.md", f"Projects/Lib/.artifacts/runs/{rid}/REPORT.md"]
    hr.seal_turn(hive_ws, SID, _rows_listing(i="b"))                          # a later turn: not again
    assert len(_files(hive_ws)) == 2


def test_what_is_never_sealed(hive_ws, tmp_path, monkeypatch):
    from core import hive_records as hr
    outside = _write(tmp_path / "outside" / "secret.md", "not yours")
    link = hive_ws / "Knowledge" / "Reports" / "link.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    (hive_ws / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)
    paths = [str(link), outside, str(hive_ws / "linked" / "secret.md"),
             _write(hive_ws / "Hive" / "repos" / "x.md"), _write(hive_ws / ".env", "KEY=1"),
             _write(hive_ws / "big.md", "x" * 50)]
    _write(hive_ws / "secret.md", "inside")   # same name, a different file: never taken for the outside one
    monkeypatch.setitem(hr.MAX_BYTES, "file", 10)
    hr.seal_turn(hive_ws, SID, _rows_listing(*paths))
    assert _files(hive_ws) == []
    with pytest.raises(ValueError):
        hr.append(hive_ws, SID, "file", data=b"x")                           # a file entry needs its path
    with pytest.raises(ValueError):
        hr.append(hive_ws, SID, "file", data=b"x", path="../escape.md")


def _host_ws(tmp_path, monkeypatch):
    import core.project_registry as pr
    ws = tmp_path / "hostws"
    (ws / "Projects" / "P").mkdir(parents=True)
    (ws / "Knowledge" / "Library").mkdir(parents=True)
    monkeypatch.setattr(pr, "get_swarmws", lambda: ws)
    return ws


def test_the_host_puts_the_file_in_the_runs_brain(host, hive_ws, tmp_path, monkeypatch):
    from core import hive_files, hive_records as hr
    ws = _host_ws(tmp_path, monkeypatch)
    hr.seal_turn(hive_ws, SID, _rows_listing(_write(hive_ws / "Knowledge" / "Reports" / "r.md", "v1")))
    row = {**ROW, "sent_at": "2026-10-30T03:00:00Z"}
    got = asyncio.run(hr.pull(row, {"id": "h"}))
    assert got["error"] is None
    here = f"Projects/P/deliverables/hive/2026-10-30-{DID}/Knowledge/Reports/r.md"
    assert (ws / here).read_text() == "v1"
    (p,) = hive_files.placed(DID)
    assert (p["path"], p["local"], p["error"]) == ("Knowledge/Reports/r.md", here, None)
    assert p["listed"] == str(hive_ws / "Knowledge" / "Reports" / "r.md")
    assert not [x for x in hr.local_dir(DID).iterdir() if x.name.endswith("-file.bin")]   # kept once, in the Brain
    # a later turn rewrites the same file: the copy here follows
    hr.seal_turn(hive_ws, SID, _rows_listing(_write(hive_ws / "Knowledge" / "Reports" / "r.md", "v2"), i="b"))
    asyncio.run(hr.pull(row, {"id": "h"}))
    assert (ws / here).read_text() == "v2"


def test_a_run_under_no_brain_goes_to_the_general_brain(host, hive_ws, tmp_path, monkeypatch):
    from core import hive_records as hr
    ws = _host_ws(tmp_path, monkeypatch)
    hr.seal_turn(hive_ws, SID, _rows_listing(_write(hive_ws / "notes.md")))
    row = {k: v for k, v in ROW.items() if k != "project_agent"}
    asyncio.run(hr.pull(row, {"id": "h"}))
    assert (ws / "Knowledge" / "Library" / "deliverables" / "hive" / f"undated-{DID}" / "notes.md").is_file()


def test_a_bad_path_or_a_linked_destination_writes_nothing(host, hive_ws, tmp_path, monkeypatch):
    from core import hive_files, hive_records as hr
    for bad in ("../x.md", "/etc/x", "Hive/records/x", "a//b", ".env", "a/./b"):
        assert hive_files.safe_rel(bad) is False
        assert hr._valid_entry({"seq": 1, "kind": "file", "size": 1, "sha256": "a" * 64, "path": bad}, 1) == "bad_entry"
    assert hr._valid_entry({"seq": 1, "kind": "file", "size": 1, "sha256": "a" * 64}, 1) == "bad_entry"
    ws = _host_ws(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / "Projects" / "P" / "deliverables").symlink_to(elsewhere, target_is_directory=True)
    hr.seal_turn(hive_ws, SID, _rows_listing(_write(hive_ws / "r.md")))
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["error"] is None and got["seq"] >= 2           # kept; only the placing is refused
    assert list(elsewhere.iterdir()) == []
    (p,) = hive_files.placed(DID)
    assert p["local"] is None and p["error"]                  # said, not silent
    # the link is gone: the next pull places it
    (ws / "Projects" / "P" / "deliverables").unlink()
    asyncio.run(hr.pull(ROW, {"id": "h"}))
    (p,) = hive_files.placed(DID)
    assert p["local"] and (ws / p["local"]).is_file()
    with pytest.raises(ValueError):
        hive_files._place_one(ws, ROW, tmp_path / "x", "../escape.md")


def test_the_general_brains_sync_leaves_returned_files_out():
    from core import brain_sync
    assert brain_sync.whitelisted("notes.md", True)
    assert not brain_sync.whitelisted(f"deliverables/hive/2026-10-30-{DID}/notes.md", True)


def test_each_returned_file_gets_its_local_path_by_identity():
    from core.swarm_out_results import attach_local
    res = {"work": {"files": [{"path": "Knowledge/Reports/r.md", "abs": "/ws/Knowledge/Reports/r.md"},
                              {"path": "Hive/repo/r.md", "abs": "/ws/Hive/repo/r.md"},
                              {"path": "x.md", "abs": "/ws/x.md"}]}}
    attach_local(res, [{"path": "Knowledge/Reports/r.md", "listed": "/ws/Knowledge/Reports/r.md",
                        "local": "Projects/P/deliverables/hive/x/Knowledge/Reports/r.md", "error": None},
                       {"path": "x.md", "listed": "/ws/x.md", "local": None, "error": "SyncError"}])
    f = res["work"]["files"]
    assert f[0]["local"].endswith("Knowledge/Reports/r.md")
    assert "local" not in f[1] and "not_placed" not in f[1]          # same ending, a different file
    assert f[2].get("not_placed") is True and "local" not in f[2]


def test_a_brain_no_longer_here_is_never_made_the_file_goes_to_general(host, hive_ws, tmp_path, monkeypatch):
    from core import hive_records as hr
    ws = _host_ws(tmp_path, monkeypatch)
    hr.seal_turn(hive_ws, SID, _rows_listing(_write(hive_ws / "r.md")))
    asyncio.run(hr.pull({**ROW, "project_agent": {"brain": "Gone"}}, {"id": "h"}))
    assert not (ws / "Projects" / "Gone").exists()
    assert (ws / "Knowledge" / "Library" / "deliverables" / "hive" / f"undated-{DID}" / "r.md").is_file()


def test_a_file_over_the_kept_budget_never_stops_the_pull(host, hive_ws, tmp_path, monkeypatch):
    from core import hive_records as hr
    _host_ws(tmp_path, monkeypatch)
    hr.seal_turn(hive_ws, SID, _rows_listing(_write(hive_ws / "big.md", "x" * 5000)))
    hr.seal_end(hive_ws, SID, "done")
    monkeypatch.setattr(hr, "MAX_KEPT_BYTES", 3000)
    got = asyncio.run(hr.pull(ROW, {"id": "h"}))
    assert got["error"] is None and got["closed"]                   # the close after it is kept
    (f,) = [e for e in hr.local_entries(DID) if e["kind"] == "file"]
    assert f["skipped"] == "record_too_large"


def test_returned_files_are_not_knowledge(tmp_path):
    import sqlite3
    from core import hive_files
    from core.ingestion import card3
    from core.ingestion.contracts import Destination
    from core.knowledge_store import KnowledgeStore, sync_knowledge_index
    assert hive_files.is_returned(("Library", "deliverables", "hive", "x", "r.md"))
    assert not hive_files.is_returned(("Library", "deliverables", "r.md"))
    kdir = tmp_path / "Knowledge"
    returned = _write(kdir / "Library" / "deliverables" / "hive" / "2026-10-30-x" / "r.md", "# r\n\n## Constraints\n\nalways run X; some body text here that is long enough to be a chunk of knowledge worth indexing.\n")
    _write(kdir / "Library" / "note.md", "# n\n\n## A note\n\nsome body text here that is long enough to be a chunk of knowledge worth indexing.\n")
    store = KnowledgeStore(sqlite3.connect(str(tmp_path / "k.db")))
    store.ensure_tables()
    sync_knowledge_index(store, kdir)
    indexed = set(store.get_indexed_files())
    assert "Library/note.md" in indexed and not any("deliverables" in k for k in indexed)
    corpus = card3.corpus_paths(tmp_path, Destination(store="KNOWLEDGE", relative_path="Knowledge/Library"))
    assert kdir / "Library" / "note.md" in corpus and Path(returned) not in corpus


def test_a_fifo_or_a_non_file_path_is_never_taken(hive_ws):
    from core import hive_records as hr
    fifo = hive_ws / "pipe.md"
    os.mkfifo(fifo)
    hr.seal_turn(hive_ws, SID, _rows_listing(str(fifo)))            # returns: never blocks on the pipe
    assert _files(hive_ws) == []
    assert hr._valid_entry({"seq": 1, "kind": "turn", "size": 1, "sha256": "a" * 64, "path": "x"}, 1) == "bad_entry"
    assert hr._valid_entry({"seq": 1, "kind": "turn", "size": 1, "sha256": "a" * 64, "listed": "x"}, 1) == "bad_entry"
