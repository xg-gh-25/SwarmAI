"""work.py registers a video as soon as it is started, not only once its film exists.

- a folder with only brief.md (or only storyboard.json) is registered: work.json kind
  video, no versions, title and size from the storyboard when there is one (else null, for
  the overlay to read from the storyboard later), and the chat session running adopt
  (SWARM_APP_SESSION_ID) as ``session``;
- a folder with none of brief.md, storyboard.json or out/*.mp4 is refused (exit 2) and
  nothing is written: no work.json, no registry entry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import env, meta, run, work  # noqa: E402

__all__ = ["env"]


def _registry(env) -> list:
    p = env["tmp"] / "ws" / ".pollinate" / "works.json"
    return json.loads(p.read_text(encoding="utf-8"))["works"] if p.exists() else []


def test_adopt_video_with_brief_only_registers(env, capsys, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-video")
    d = env["tmp"] / "film"
    d.mkdir()
    (d / "brief.md").write_text("# Brief\n- Audience: field\n", encoding="utf-8")
    code, out = run(["adopt", "--dir", str(d), "--kind", "video"], capsys)
    assert code == 0, out
    m = meta(d)
    assert m["kind"] == "video" and m["versions"] == [] and m["current"] is None
    assert m["title"] is None and m["size"] is None and m["session"] == "sess-video"
    assert out["title"] == "film"
    assert [(w["kind"], w["path"]) for w in _registry(env)] == [("video", str(d.resolve()))]
    assert out["film"] is False


def test_adopt_video_with_storyboard_only_registers(env, capsys, monkeypatch):
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    d = env["tmp"] / "film"
    d.mkdir()
    (d / "storyboard.json").write_text(json.dumps({"title": "Edge", "width": 1920, "height": 1080,
                                                   "scenes": [{"id": "S01"}]}), encoding="utf-8")
    code, out = run(["adopt", "--dir", str(d), "--kind", "video"], capsys)
    assert code == 0, out
    m = meta(d)
    assert m["title"] == "Edge" and m["size"] == {"w": 1920, "h": 1080} and m["session"] is None


def test_adopt_video_empty_folder_refused(env, capsys):
    d = env["tmp"] / "film"
    (d / "out").mkdir(parents=True)
    (d / "notes.txt").write_text("not a brief", encoding="utf-8")
    capsys.readouterr()
    assert work.main(["adopt", "--dir", str(d), "--kind", "video"]) == 2
    assert "brief.md" in capsys.readouterr().err
    assert not (d / "work.json").exists()
    assert _registry(env) == []
