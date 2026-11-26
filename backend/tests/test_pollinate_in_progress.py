"""core/pollinate_works: which works are in progress, and how far a video has got.

A deck, poster, page or copy is in progress with an open revision or with no passed version
yet; a video is in progress until its film (out/*.mp4) exists, and its stage is read from
its files: brief.md → storyboard.json → scene frames → the film. A video's updated time is
the newest of its registry stamp and those files' times. Each test writes a small workspace
by hand and checks the rows (and the HTTP response model keeps the new fields).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core import pollinate_works as pw
from schemas.pollinate import PollinateWorksResponse


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def _doc(root: Path, rel: str, *, current, open_=None, versions=()) -> Path:
    d = root / rel
    _write(d / "work.json", {"schema": 1, "kind": "deck", "title": rel, "src": "src/deck.html", "slides": [],
                             "current": current, "open": open_, "versions": list(versions)})
    return d


def _video(root: Path, rel: str, *, session=None) -> Path:
    d = root / rel
    _write(d / "work.json", {"schema": 1, "kind": "video", "title": rel, "src": None, "slides": [],
                             "current": None, "open": None, "versions": [], "session": session})
    return d


def _registry(ws: Path, dirs, updated=None) -> None:
    _write(ws / ".pollinate" / "works.json",
           {"schema": 1, "works": [{"path": str(d), **({"updated": updated} if updated else {})} for d in dirs]})


def _rows(ws: Path) -> dict:
    return {r["title"]: r for r in pw.read_works(ws)["works"]}


@pytest.fixture
def ws(tmp_path):
    return tmp_path.resolve()


def test_documents_in_progress_follow_open_and_current(ws):
    v1 = {"id": "v001", "by": "user", "status": "ok", "at": "2026-11-25T10:00:00Z", "scope": []}
    opened = _doc(ws, "a-open", current="v001", versions=[v1],
                  open_={"scope": ["s-1"], "remove": [], "add": 0, "at": "2026-11-26T08:00:00Z"})
    fresh = _doc(ws, "b-fresh", current=None)
    done = _doc(ws, "c-done", current="v001", versions=[v1])
    _registry(ws, [opened, fresh, done])
    rows = _rows(ws)
    assert rows["a-open"]["in_progress"] is True
    assert rows["b-fresh"]["in_progress"] is True
    assert rows["c-done"]["in_progress"] is False
    assert all(r["progress"] is None for r in rows.values())


def test_video_stages_follow_files(ws):
    empty = _video(ws, "v0-none")
    brief = _video(ws, "v1-brief")
    (brief / "brief.md").write_text("# Brief", encoding="utf-8")
    board = _video(ws, "v2-board")
    _write(board / "storyboard.json", {"scenes": [{"id": "S01"}, {"id": "S02"}, {"id": "S03"}]})
    scenes = _video(ws, "v3-scenes", session="sess-v")
    _write(scenes / "storyboard.json", {"scenes": [{"id": "S01"}, {"id": "S02"}, {"id": "S03"}]})
    (scenes / "review").mkdir()
    (scenes / "review" / "S01-a.jpg").write_bytes(b"jpg")
    (scenes / "review" / "S01-b.jpg").write_bytes(b"jpg")
    (scenes / "review" / "S03-a.jpg").write_bytes(b"jpg")
    film = _video(ws, "v4-film")
    _write(film / "storyboard.json", {"scenes": [{"id": "S01"}]})
    (film / "out").mkdir()
    (film / "out" / "f.mp4").write_bytes(b"mp4")
    _registry(ws, [empty, brief, board, scenes, film])
    rows = _rows(ws)
    assert rows["v0-none"]["progress"] == {"stage": None, "scenes": 0, "framed": 0}
    assert rows["v1-brief"]["progress"] == {"stage": "brief", "scenes": 0, "framed": 0}
    assert rows["v2-board"]["progress"] == {"stage": "storyboard", "scenes": 3, "framed": 0}
    assert rows["v3-scenes"]["progress"] == {"stage": "scenes", "scenes": 3, "framed": 2}
    assert rows["v4-film"]["progress"]["stage"] == "film"
    for name in ("v0-none", "v1-brief", "v2-board", "v3-scenes"):
        assert rows[name]["in_progress"] is True, name
    assert rows["v4-film"]["in_progress"] is False
    assert rows["v3-scenes"]["session"] == "sess-v"
    view = pw.work_view(ws, "v3-scenes")
    assert view["session"] == "sess-v"
    assert [a["key"] for a in view["actions"]] == ["open_session"]
    # the response model keeps the new fields (a missing schema field is dropped silently)
    model = PollinateWorksResponse(**pw.read_works(ws)).model_dump()
    by = {w["title"]: w for w in model["works"]}
    assert by["v3-scenes"]["in_progress"] is True and by["v3-scenes"]["progress"]["framed"] == 2
    assert by["v3-scenes"]["session"] == "sess-v"


def test_numeric_string_durations(ws):
    film = _video(ws, "film")
    _write(film / "storyboard.json", {"scenes": [{"id": "S01", "duration": "4.5"}, {"id": "S02", "duration": 2},
                                                 {"id": "S03", "duration": "soon"}, {"id": "S04", "duration": True},
                                                 {"id": "S05", "duration": "-1"}]})
    _registry(ws, [film])
    row = _rows(ws)["film"]
    assert row["duration_s"] == 6.5 and row["scene_count"] == 5
    scenes = pw.work_view(ws, "film")["scenes"]
    assert [s["duration_s"] for s in scenes] == [4.5, 2.0, None, None, None]
    assert [s["start_s"] for s in scenes] == [0, 4.5, 6.5, 6.5, 6.5]


def test_video_updated_is_newest_file_time(ws):
    film = _video(ws, "film")
    (film / "brief.md").write_text("# Brief", encoding="utf-8")
    _write(film / "storyboard.json", {"scenes": []})
    old, new = 1_700_000_000, 1_800_000_000          # 2023-11 and 2027-01
    os.utime(film / "brief.md", (old, old))
    os.utime(film / "storyboard.json", (new, new))
    _registry(ws, [film], updated="2026-11-26T03:00:00+00:00")
    assert _rows(ws)["film"]["updated"].startswith("2027-01-15")
    os.utime(film / "storyboard.json", (old, old))
    assert _rows(ws)["film"]["updated"] == "2026-11-26T03:00:00+00:00"


def test_video_registered_at_brief_takes_title_and_size_from_its_storyboard(ws):
    film = ws / "film"
    _write(film / "work.json", {"schema": 1, "kind": "video", "title": None, "style": None, "size": None,
                                "src": None, "slides": [], "current": None, "open": None, "versions": []})
    (film / "brief.md").write_text("# Brief", encoding="utf-8")
    _registry(ws, [film])
    row = pw.read_works(ws)["works"][0]
    assert (row["title"], row["width"], row["height"], row["style"]) == ("film", None, None, None)
    _write(film / "storyboard.json", {"title": "Beyond AI", "width": 1920, "height": 1080, "style": "night",
                                      "scenes": [{"id": "S01"}]})
    row = pw.read_works(ws)["works"][0]
    assert (row["title"], row["width"], row["height"], row["style"]) == ("Beyond AI", 1920, 1080, "night")
    # what work.json says wins (adopt --title / --size, or a storyboard read at adopt)
    meta = json.loads((film / "work.json").read_text(encoding="utf-8"))
    meta.update({"title": "Given", "size": {"w": 1080, "h": 1920}, "style": "plain"})
    _write(film / "work.json", meta)
    row = pw.read_works(ws)["works"][0]
    assert (row["title"], row["width"], row["height"], row["style"]) == ("Given", 1080, 1920, "plain")


def test_unknown_kind_is_never_in_progress(ws):
    odd = ws / "odd"
    _write(odd / "work.json", {"schema": 1, "kind": "hologram", "title": "odd", "slides": [], "current": None,
                               "open": {"scope": [], "at": "2026-11-26T08:00:00Z"}, "versions": []})
    nokind = ws / "nokind"
    _write(nokind / "work.json", {"schema": 1, "title": "nokind", "slides": [], "current": None, "versions": []})
    _registry(ws, [odd, nokind])
    rows = _rows(ws)
    assert rows["odd"]["in_progress"] is False and rows["nokind"]["in_progress"] is False
