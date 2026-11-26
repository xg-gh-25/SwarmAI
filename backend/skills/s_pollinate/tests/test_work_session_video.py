"""work.py records the chat session on a revision, and lists a video as a work.

- open and cut write SWARM_APP_SESSION_ID (when set) into the open record and the cut's
  version entry, so the overlay can take the person back to that chat;
- adopt --kind video registers a video (here a finished one, an .mp4 in out/) with no
  versions, and open, cut and render refuse it (exit 2, work.json unchanged); registering
  one before its film is in test_work_video_early.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    adopt_small, env, meta, need_browser, replace_in_slide, run, version, work,
)

__all__ = ["env"]


def test_open_and_cut_record_session(env, capsys, monkeypatch):
    need_browser()
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-1234")
    assert run(["open", "--dir", str(d), "--scope", ids[1]], capsys)[0] == 0
    assert meta(d)["open"]["session"] == "sess-1234"
    replace_in_slide(d, ids[1], "Bravo", "Bravo edited")
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert code == 0, out
    assert version(d, "v002")["session"] == "sess-1234"


def test_no_session_writes_no_field(env, capsys, monkeypatch):
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    assert run(["open", "--dir", str(d), "--scope", ids[1]], capsys)[0] == 0
    assert "session" not in meta(d)["open"]
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "  ")
    assert run(["open", "--dir", str(d), "--scope", ids[2]], capsys)[0] == 0  # already open: scope updated
    assert "session" not in meta(d)["open"]


def _video_folder(root: Path, board: dict | None = None) -> Path:
    d = root / "film"
    (d / "out").mkdir(parents=True)
    (d / "out" / "film.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    if board is not None:
        (d / "storyboard.json").write_text(json.dumps(board), encoding="utf-8")
    return d


def test_video_adopt_and_refusals(env, capsys):
    d = _video_folder(env["tmp"], {"title": "Edge AI", "width": 1920, "height": 1080, "style": "anatomy",
                                   "scenes": [{"id": "S01"}, {"id": "S02"}]})
    code, out = run(["adopt", "--dir", str(d), "--kind", "video"], capsys)
    assert code == 0, out
    m = meta(d)
    assert m["kind"] == "video" and m["title"] == "Edge AI" and m["size"] == {"w": 1920, "h": 1080}
    assert m["style"] == "anatomy" and m["versions"] == [] and m["current"] is None
    reg = json.loads((env["tmp"] / "ws" / ".pollinate" / "works.json").read_text(encoding="utf-8"))
    assert [(w["kind"], w["title"]) for w in reg["works"]] == [("video", "Edge AI")]
    before = (d / "work.json").read_bytes()
    for argv in (["open", "--dir", str(d), "--scope", ""], ["cut", "--dir", str(d)], ["render", "--dir", str(d)]):
        capsys.readouterr()
        assert work.main(argv) == 2
        assert "video" in capsys.readouterr().err
    assert (d / "work.json").read_bytes() == before


def test_video_adopt_refuses_an_empty_folder(env, capsys):
    d = env["tmp"] / "empty"
    (d / "out").mkdir(parents=True)
    capsys.readouterr()
    assert work.main(["adopt", "--dir", str(d), "--kind", "video"]) == 2
    assert not (d / "work.json").exists()


def test_video_without_storyboard_uses_folder_name(env, capsys):
    d = _video_folder(env["tmp"])
    assert run(["adopt", "--dir", str(d), "--kind", "video"], capsys)[0] == 0
    m = meta(d)
    # nothing to read them from yet: the overlay falls back to the storyboard, then the folder
    assert m["title"] is None and m["size"] is None and m["style"] is None
    reg = json.loads((env["tmp"] / "ws" / ".pollinate" / "works.json").read_text(encoding="utf-8"))
    assert reg["works"][0]["title"] == "film"
